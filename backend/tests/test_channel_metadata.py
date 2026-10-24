"""Real channel label CAS, native envelopes and opaque Matrix authority boundaries."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine

from app import database
from app.database import get_session
from app.main import app
from app.models.channel import Channel, ChannelMember
from app.models.channel_metadata import ChannelMetadata
from app.models.secure_channel import SecureChannel, MatrixAccount
from app.models.user import User
from app.realtime import socket
from app.services.auth import create_token
from app.services import channel_metadata as service, matrix_bridge
from app.api import channel_metadata as routes
from app.api.secure_messaging import has_native_product_history


@pytest.fixture
def team(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'metadata.db'}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(socket, "engine", engine)
    monkeypatch.setattr(routes, "broadcast_to_channel", AsyncMock())
    with Session(engine) as session:
        session.add_all([User(id=name, name=name.title(), email=name+"@example.com", password_hash="unused") for name in ("owner", "member", "outsider")]); session.commit()
    def sessions():
        with Session(engine) as session: yield session
    previous = app.dependency_overrides.copy(); app.dependency_overrides[get_session] = sessions
    headers = {name: {"Authorization": "Bearer "+create_token(name)} for name in ("owner", "member", "outsider")}
    with TestClient(app) as client:
        response = client.post("/channels", headers=headers["owner"], json={"name": "Team", "member_ids": ["member"]})
        assert response.status_code == 200, response.text
        yield client, engine, headers, response.json()["id"]
    app.dependency_overrides.clear(); app.dependency_overrides.update(previous); engine.dispose()


def edit(team, actor="owner", **values):
    client, _, headers, channel = team
    return client.patch(f"/channels/{channel}/metadata", headers=headers[actor], json={"expected_version": 0, "name": "Nord", "description": "A private brief", **values})


def secure(team, monkeypatch, **event):
    _, engine, _, channel = team
    with Session(engine) as session:
        session.add(SecureChannel(channel_id=channel, matrix_room_id="!room:example.test", status="active", created_by="owner"))
        session.add(MatrixAccount(user_id="owner", matrix_user_id="@fc_owner:example.test", encrypted_password="unused"))
        row=session.get(Channel, channel); row.name="Encrypted channel"; session.add(row); session.commit()
    monkeypatch.setattr(matrix_bridge, "service_token", lambda *args, **kwargs: ("service", "unused"))
    monkeypatch.setattr(matrix_bridge, "matrix_request", lambda *args, **kwargs: {"room_id":"!room:example.test", "type":"m.room.encrypted", "sender":"@fc_owner:example.test", "content":{"ciphertext":"opaque"}, **event})


def pointer(team, **values):
    client, _, headers, channel = team
    return client.patch(f"/channels/{channel}/metadata", headers=headers["owner"], json={"expected_version":0, "matrix_event_id":"$committed", **values})


def test_native_admin_save_is_readable_by_members_sealed_and_idempotent(team):
    client, engine, headers, channel = team
    initial=client.get(f"/channels/{channel}/metadata", headers=headers["member"])
    assert initial.status_code==200 and initial.json()["version"]==0 and not initial.json()["can_manage"]
    assert "no-store" in initial.headers["cache-control"]
    saved=edit(team, name="  Nord  ", description="Brief\r\nnext line")
    assert saved.status_code==200, saved.text
    assert saved.json()["name"]=="Nord" and saved.json()["description"]=="Brief\nnext line" and saved.json()["version"]==1
    with Session(engine) as session:
        row=session.get(ChannelMetadata, channel)
        assert "Brief" not in row.encrypted_payload and row.matrix_event_id is None
        assert service.open_payload(session,row)=={"name":"Nord","description":"Brief\nnext line"}
        assert session.get(Channel,channel).name=="Nord"
    assert edit(team, expected_version=1, name="Nord", description="Brief\nnext line").json()["version"]==1
    peer=client.get(f"/channels/{channel}/metadata", headers=headers["member"])
    assert peer.json()["description"]=="Brief\nnext line" and not peer.json()["can_manage"]
    assert routes.broadcast_to_channel.await_count==1
    assert routes.broadcast_to_channel.await_args.args[2]=={"channel_id":channel,"version":1}


def test_members_outsiders_stale_cas_and_direct_conversations_cannot_edit(team):
    client, engine, headers, channel = team
    assert edit(team,"member").status_code==403
    assert edit(team,"outsider").status_code==403
    assert client.get(f"/channels/{channel}/metadata",headers=headers["outsider"]).status_code==403
    assert edit(team).status_code==200
    assert edit(team,name="Stale").status_code==409
    with Session(engine) as session:
        row=session.get(Channel,channel);row.type="direct";session.add(row);session.commit()
    assert edit(team,expected_version=1).status_code==409


@pytest.mark.parametrize("values", [{"name":"  "},{"name":"x\x00y"},{"name":"x"*101},{"description":"x"*1001},{"description":"bad\x01"},{"expected_version":True},{"unknown":"field"},{"matrix_event_id":"$secret"}])
def test_invalid_native_input_never_creates_metadata(team,values):
    _,engine,_,channel=team
    assert edit(team,**values).status_code==422
    with Session(engine) as session: assert session.get(ChannelMetadata,channel) is None


def test_parallel_writers_have_one_winner_without_silent_overwrite(team):
    gate=Barrier(2)
    def save(name):
        gate.wait();return edit(team,name=name)
    with ThreadPoolExecutor(max_workers=2) as pool: replies=list(pool.map(save,["First","Second"]))
    assert sorted(reply.status_code for reply in replies)==[200,409]
    winner=next(reply.json()["name"] for reply in replies if reply.status_code==200)
    client,_,headers,channel=team
    assert client.get(f"/channels/{channel}/metadata",headers=headers["owner"]).json()["name"]==winner


def test_description_envelope_binds_channel_revision_and_native_mode(team):
    client,engine,headers,channel=team
    assert edit(team).status_code==200
    with Session(engine) as session:
        row=session.get(ChannelMetadata,channel);row.version+=1;session.add(row);session.commit()
    assert client.get(f"/channels/{channel}/metadata",headers=headers["owner"]).status_code==409
    with Session(engine) as session:
        row=session.get(ChannelMetadata,channel);row.version=1
        row.encrypted_payload=service.seal_payload("other-channel",1,{"name":"Nord","description":"Foreign"});session.add(row);session.commit()
    assert client.get(f"/channels/{channel}/metadata",headers=headers["owner"]).status_code==409


def test_secure_api_registers_only_opaque_authority_and_never_returns_plaintext(team,monkeypatch):
    client,engine,headers,channel=team;secure(team,monkeypatch)
    assert edit(team).status_code==409
    assert pointer(team,name="Leaked",description="text").status_code==422
    saved=pointer(team);assert saved.status_code==200,saved.text
    assert saved.json()["name"] is None and saved.json()["description"] is None and saved.json()["matrix_event_id"]=="$committed"
    assert pointer(team,matrix_event_id="$uncommitted").status_code==409
    assert pointer(team,expected_version=1).json()["version"]==1
    with Session(engine) as session:
        row=session.get(ChannelMetadata,channel);assert row.encrypted_payload is None and row.content_mode=="matrix" and row.matrix_event_id=="$committed"
        assert not has_native_product_history(session,channel)
    assert client.get(f"/channels/{channel}/metadata",headers=headers["member"]).json()["matrix_event_id"]=="$committed"


@pytest.mark.parametrize("event", [{"room_id":"!wrong:example.test"},{"sender":"@fc_member:example.test"},{"type":"m.room.message"},{"content":{}},{"unsigned":{"redacted_because":{"type":"m.room.redaction"}}}])
def test_secure_registration_checks_wire_room_sender_encryption_and_redaction(team,monkeypatch,event):
    secure(team,monkeypatch,**event)
    assert pointer(team).status_code==400
    with Session(team[1]) as session: assert session.get(ChannelMetadata,team[3]) is None


@pytest.mark.parametrize("change", ["demote","block","revoke"])
def test_secure_registration_rechecks_authority_after_remote_verification(team,monkeypatch,change):
    secure(team,monkeypatch)
    def changed(session,channel,event,user):
        if change=="demote":
            row=session.get(ChannelMember,(channel,user));row.role="member"
        elif change=="block":
            row=session.get(User,user);row.is_blocked=True
        else:
            from app.models.session import ActiveSession
            from sqlmodel import select
            row=session.exec(select(ActiveSession).where(ActiveSession.user_id==user)).first();row.revoked_at=__import__('datetime').datetime.utcnow()
        session.add(row);session.commit()
    monkeypatch.setattr(routes,"validate_secure_event",changed)
    assert pointer(team).status_code in (401,403)
    with Session(team[1]) as session: assert session.get(ChannelMetadata,team[3]) is None


def test_native_metadata_counts_as_history_even_without_messages(team):
    assert edit(team).status_code==200
    with Session(team[1]) as session: assert has_native_product_history(session,team[3])


@pytest.mark.parametrize('kind',['stack','placement','relation','reference'])
def test_any_organization_history_conservatively_fences_encryption_conversion(team,kind):
    # Legacy SQLite installations may contain orphaned rows. Even without the
    # parent page/message, none of these native routing rows may be relabelled
    # as client-encrypted history. This also exercises placement's distinct PK.
    from app.models.document_organization import SharedDocumentStack, SharedDocumentPlacement, SharedDocumentRelation, SharedDocumentStackReference
    _,engine,_,channel=team
    with Session(engine) as session:
        assert not has_native_product_history(session,channel)
        rows={
            'stack':SharedDocumentStack(id='stack',channel_id=channel,created_by='owner',encrypted_payload='opaque'),
            'placement':SharedDocumentPlacement(document_id='page',channel_id=channel,updated_by='owner'),
            'relation':SharedDocumentRelation(id='pair',channel_id=channel,first_document_id='first',second_document_id='second',created_by='owner'),
            'reference':SharedDocumentStackReference(id='reference',channel_id=channel,stack_id='stack',message_id='message',created_by='owner'),
        }
        session.add(rows[kind]);session.commit()
        assert has_native_product_history(session,channel)
