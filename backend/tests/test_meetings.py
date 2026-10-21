"""Call-only capabilities, current authorization and bounded encrypted signalling."""
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.api import auth, meetings
from app.config import settings
from app.database import get_session
from app.models.account_security import AccountSecurity
from app.models.channel import Channel, ChannelMember
from app.models.meetings import Meeting, MeetingInvitation, MeetingParticipant, MeetingSignal
from app.models.session import ActiveSession
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceChannel, WorkspaceMember
from app.models.workspace_oidc import WorkspaceOIDCProvider, WorkspaceOIDCSession
from app.services.auth import create_token, token_digest
from app.services.crypto import encrypt_message
from app.services import meetings as meeting_service


@pytest.fixture
def team(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "environment", "test")
    monkeypatch.setattr(settings, "message_encryption_keys", {"test": SecretStr(base64.b64encode(b"m" * 32).decode())})
    monkeypatch.setattr(settings, "message_encryption_active_key_id", "test")
    monkeypatch.setattr(settings, "account_security_key", SecretStr(base64.b64encode(b"a" * 32).decode()))
    monkeypatch.setattr(settings, "public_app_url", "https://collab.example")
    monkeypatch.setattr(settings, "matrix_enabled", False)
    engine = create_engine(f"sqlite:///{tmp_path / 'meetings.db'}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        for name in ["owner", "member", "outsider", "other", "p3", "p4", "p5", "p6", "p7"]:
            session.add(User(id=name, name=name.title(), email=name + "@example.com", password_hash="unused"))
        session.add_all([Workspace(id="a", name="A"), Workspace(id="b", name="B")])
        session.add_all([Channel(id="shared", name="Shared"), Channel(id="private", name="Private"), Channel(id="elsewhere", name="Elsewhere")])
        session.commit()
        for name in ["owner", "member", "other", "p3", "p4", "p5", "p6", "p7"]:
            session.add(WorkspaceMember(workspace_id="a", user_id=name, role="owner" if name == "owner" else "member"))
            session.add(ChannelMember(channel_id="shared", user_id=name, role="admin" if name == "owner" else "member"))
        session.add_all([WorkspaceChannel(channel_id="shared", workspace_id="a"), WorkspaceChannel(channel_id="private", workspace_id="a"), WorkspaceChannel(channel_id="elsewhere", workspace_id="b"),
                         ChannelMember(channel_id="private", user_id="owner", role="admin"), WorkspaceMember(workspace_id="b", user_id="owner", role="owner"), ChannelMember(channel_id="elsewhere", user_id="owner", role="admin")])
        session.add(WorkspaceOIDCProvider(workspace_id="a", name="A IdP", issuer_url="https://idp.example", client_id="private-client", created_by="owner", enabled=True, revision=1))
        session.commit()
        scoped = create_token("owner", session, auth_methods=["sso"], workspace_scope={"workspace_id": "a", "provider_revision": 1})
        session.add(WorkspaceOIDCSession(token_hash=token_digest(scoped), workspace_id="a", provider_revision=1))
        session.commit()
    app = FastAPI()
    app.include_router(meetings.router)
    app.include_router(auth.router)
    def sessions():
        with Session(engine) as session:
            yield session
    app.dependency_overrides[get_session] = sessions
    with TestClient(app) as client:
        yield client, engine, {name: create_token(name) for name in ["owner", "member", "outsider", "other", "p3", "p4", "p5", "p6", "p7"]}, scoped
    engine.dispose()


def native(token):
    return {"Authorization": "Bearer " + token}


def access(row):
    return {"X-Meeting-Token": row["meeting_token"]}


def start(team):
    client, _, tokens, _ = team
    response = client.post("/channels/shared/meetings", headers=native(tokens["owner"]), json={})
    assert response.status_code == 201
    return response.json()


def join(team, meeting, name="member"):
    response = team[0].post(f'/meetings/{meeting["meeting"]["id"]}/join', headers=native(team[2][name]), json={})
    assert response.status_code == 200
    return response.json()


def poll(client, meeting, participant):
    return client.get(f'/meeting-access/{meeting["meeting"]["id"]}/poll', headers=access(participant))


def guest(team, meeting, name="Outside guest"):
    client, _, tokens, _ = team
    identifier = meeting["meeting"]["id"]
    invite = client.post(f"/meetings/{identifier}/invites", headers=native(tokens["owner"]), json={})
    assert invite.status_code == 201
    response = client.post(f"/meeting-access/{identifier}/guest", json={"invitation_token": invite.json()["invitation_token"], "name": name})
    assert response.status_code == 201
    return response.json(), invite.json()


def test_native_members_join_and_only_host_manages(team):
    client, _, tokens, _ = team
    meeting = start(team); member = join(team, meeting)
    identifier = meeting["meeting"]["id"]
    assert len(poll(client, meeting, member).json()["participants"]) == 2
    assert client.get("/channels/shared/meetings", headers=native(tokens["outsider"])).status_code == 403
    assert client.post(f"/meetings/{identifier}/join", headers=native(tokens["outsider"]), json={}).status_code == 403
    assert client.post("/channels/shared/meetings", headers=native(tokens["member"]), json={}).status_code == 409
    for route in ["invites", "end", "participants/" + member["participant"]["id"] + "/remove"]:
        assert client.post(f"/meetings/{identifier}/{route}", headers=native(tokens["member"]), json={}).status_code == 403
    assert client.post(f"/meetings/{identifier}/end", headers=native(tokens["owner"]), json={}).status_code == 200
    assert poll(client, meeting, member).status_code == 410
    assert client.get("/channels/shared/meetings", headers=native(tokens["member"])).json() == []


def test_guests_are_one_use_waiting_and_admitted_without_workspace_access(team):
    client, engine, tokens, _ = team
    meeting = start(team); visitor, invite = guest(team, meeting)
    identifier = meeting["meeting"]["id"]
    assert "#meeting=" in invite["url"] and "meeting_invite=" in invite["url"]
    waiting = poll(client, meeting, visitor)
    assert waiting.status_code == 200 and waiting.json()["state"] == "waiting"
    assert waiting.json()["participants"] == waiting.json()["signals"] == waiting.json()["ice_servers"] == []
    assert client.post(f"/meeting-access/{identifier}/ready", headers=access(visitor), json={}).status_code == 403
    assert client.post(f"/meeting-access/{identifier}/guest", json={"invitation_token": invite["invitation_token"], "name": "Replay"}).status_code == 401
    assert client.get("/auth/me", headers=native(visitor["meeting_token"])).status_code == 401
    assert client.get("/channels/shared/meetings", headers=native(visitor["meeting_token"])).status_code == 401
    assert client.post(f'/meetings/{identifier}/participants/{visitor["participant"]["id"]}/admit', headers=native(tokens["owner"]), json={}).status_code == 200
    assert client.post(f"/meeting-access/{identifier}/ready", headers=access(visitor), json={}).status_code == 200
    assert poll(client, meeting, visitor).json()["state"] == "admitted"
    with Session(engine) as session:
        assert session.exec(select(MeetingInvitation)).one().token_hash != invite["invitation_token"]
        assert session.get(MeetingParticipant, visitor["participant"]["id"]).credential_hash != visitor["meeting_token"]
        assert session.exec(select(WorkspaceMember).where(WorkspaceMember.user_id == visitor["participant"]["id"])).first() is None


def test_signalling_is_encrypted_at_rest_private_to_recipient_and_current_sender(team):
    client, engine, tokens, _ = team
    meeting = start(team); member = join(team, meeting); other = join(team, meeting, "other")
    identifier = meeting["meeting"]["id"]
    payload = {"recipient_id": member["participant"]["id"], "description": {"type": "offer", "sdp": "v=0\r\nprivate-candidate-and-network-metadata"}}
    sent = client.post(f"/meeting-access/{identifier}/signal", headers=access(meeting), json=payload)
    assert sent.status_code == 202
    assert poll(client, meeting, member).json()["signals"][0]["payload"]["description"] == payload["description"]
    assert poll(client, meeting, other).json()["signals"] == []
    with Session(engine) as session:
        row = session.exec(select(MeetingSignal)).one()
        assert "private-candidate" not in row.encrypted_payload
        user = session.get(User, "owner"); user.is_blocked = True; session.add(user); session.commit()
    assert poll(client, meeting, member).status_code == 410


@pytest.mark.parametrize("change", ["blocked", "channel", "workspace", "auth_version", "revoked", "expired", "provider", "sidecar"])
def test_original_host_authorization_is_rechecked_for_existing_guest_capability(team, change):
    client, engine, tokens, scoped = team
    if change in {"provider", "sidecar"}:
        tokens["owner"] = scoped
    meeting = start(team); visitor, _ = guest(team, meeting)
    with Session(engine) as session:
        if change == "blocked":
            row = session.get(User, "owner"); row.is_blocked = True; session.add(row)
        elif change == "channel": session.delete(session.get(ChannelMember, ("shared", "owner")))
        elif change == "workspace": session.delete(session.get(WorkspaceMember, ("a", "owner")))
        elif change == "auth_version":
            row = session.get(AccountSecurity, "owner"); row.auth_version += 1; session.add(row)
        elif change in {"revoked", "expired"}:
            row = session.exec(select(ActiveSession).where(ActiveSession.token_hash == token_digest(tokens["owner"]))).one()
            if change == "revoked": row.revoked_at = datetime.utcnow()
            else: row.expires_at = datetime.utcnow() - timedelta(seconds=1)
            session.add(row)
        elif change == "provider":
            row = session.get(WorkspaceOIDCProvider, "a"); row.revision += 1; session.add(row)
        else: session.delete(session.get(WorkspaceOIDCSession, token_digest(scoped)))
        session.commit()
    assert poll(client, meeting, visitor).status_code == 410


def test_removed_member_cannot_use_capability_or_new_login_to_rejoin(team):
    client, _, tokens, _ = team
    meeting = start(team); member = join(team, meeting)
    identifier = meeting["meeting"]["id"]
    assert client.post(f'/meetings/{identifier}/participants/{member["participant"]["id"]}/remove', headers=native(tokens["owner"]), json={}).status_code == 200
    assert poll(client, meeting, member).status_code == 401
    assert client.post(f"/meetings/{identifier}/join", headers=native(create_token("member")), json={}).status_code == 403


def test_native_scoped_login_cannot_cross_workspace_meetings(team):
    client, _, _, scoped = team
    assert client.get("/channels/elsewhere/meetings", headers=native(scoped)).status_code == 403
    assert client.post("/channels/elsewhere/meetings", headers=native(scoped), json={}).status_code == 403
    response = client.post("/channels/shared/meetings", headers=native(scoped), json={})
    assert response.status_code == 201
    assert poll(client, response.json(), response.json()).status_code == 200


def test_capacity_and_cross_meeting_recipient_fences(team):
    client, _, tokens, _ = team
    meeting = start(team)
    for name in ["member", "other", "p3", "p4", "p5"]:
        join(team, meeting, name)
    identifier = meeting["meeting"]["id"]
    assert client.post(f"/meetings/{identifier}/join", headers=native(tokens["p6"]), json={}).status_code == 409
    other = client.post("/channels/private/meetings", headers=native(tokens["owner"]), json={}).json()
    signal = client.post(f"/meeting-access/{identifier}/signal", headers=access(meeting), json={"recipient_id": other["participant"]["id"], "candidate": {"candidate": "private"}})
    assert signal.status_code == 404


def test_payload_validation_and_persistent_signal_limit(team):
    client, engine, _, _ = team
    meeting = start(team); member = join(team, meeting); identifier = meeting["meeting"]["id"]
    endpoint = f"/meeting-access/{identifier}/signal"
    for body in [
        {"recipient_id": member["participant"]["id"]},
        {"recipient_id": member["participant"]["id"], "candidate": {"candidate": "x" * 8193}},
        {"recipient_id": member["participant"]["id"], "description": {"type": "rollback", "sdp": "x"}},
        {"recipient_id": member["participant"]["id"], "candidate": {"candidate": "x"}, "unexpected": "secret"},
    ]:
        assert client.post(endpoint, headers=access(meeting), json=body).status_code == 422
    with Session(engine) as session:
        session.add_all([MeetingSignal(meeting_id=identifier, sequence=i + 1, sender_id=meeting["participant"]["id"], recipient_id=member["participant"]["id"], encrypted_payload="opaque") for i in range(120)])
        session.commit()
    response = client.post(endpoint, headers=access(meeting), json={"recipient_id": member["participant"]["id"], "candidate": {"candidate": "x"}})
    assert response.status_code == 429 and response.headers["retry-after"] == "60"


def test_guest_invitation_is_consumed_once_under_concurrent_requests(team):
    client, _, tokens, _ = team
    meeting = start(team); identifier = meeting["meeting"]["id"]
    invitation = client.post(f"/meetings/{identifier}/invites", headers=native(tokens["owner"]), json={}).json()
    def consume(index):
        return client.post(f"/meeting-access/{identifier}/guest", json={"invitation_token": invitation["invitation_token"], "name": f"Guest {index}"}).status_code
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(consume, range(2))) == [201, 401]


def test_signal_retention_and_no_implicit_external_ice_service(team):
    client, engine, _, _ = team
    meeting = start(team); member = join(team, meeting); identifier = meeting["meeting"]["id"]
    with Session(engine) as session:
        session.add(MeetingSignal(meeting_id=identifier, sequence=1, sender_id=meeting["participant"]["id"], recipient_id=member["participant"]["id"],
            encrypted_payload=encrypt_message(json.dumps({"candidate": {"candidate": "private-old"}}), "meeting:" + identifier + ":" + member["participant"]["id"]), created_at=datetime.utcnow() - timedelta(minutes=6)))
        session.commit()
    response = poll(client, meeting, member)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json()["signals"] == response.json()["ice_servers"] == []
    with Session(engine) as session:
        assert session.exec(select(MeetingSignal)).all() == []


def test_media_or_plaintext_channel_content_is_not_a_meeting_api_payload(team):
    client, _, tokens, _ = team
    assert client.post("/channels/shared/meetings", headers=native(tokens["owner"]), json={"title": "Private encrypted channel title"}).status_code == 422
    assert client.post("/channels/shared/meetings", headers=native(tokens["owner"]), json={"media": "not-accepted"}).status_code == 422


def test_separate_host_login_revoked_during_lock_wait_cannot_issue_guest_invitation(team, monkeypatch):
    client, engine, _, _ = team
    meeting = start(team)
    with Session(engine) as session:
        second = create_token("owner", session)
    original = meeting_service.lock_meeting
    def revoke_request(session, identifier):
        result = original(session, identifier)
        active = session.exec(select(ActiveSession).where(ActiveSession.token_hash == token_digest(second))).one()
        active.revoked_at = datetime.utcnow(); session.add(active); session.commit()
        return result
    monkeypatch.setattr(meeting_service, "lock_meeting", revoke_request)
    response = client.post(f'/meetings/{meeting["meeting"]["id"]}/invites', headers=native(second), json={})
    assert response.status_code == 401
    with Session(engine) as session:
        assert session.exec(select(MeetingInvitation)).all() == []


def test_large_signal_cursor_is_rejected_before_database_conversion(team):
    client, _, _, _ = team
    meeting = start(team)
    assert client.get(f'/meeting-access/{meeting["meeting"]["id"]}/poll?after=' + '9' * 100, headers=access(meeting)).status_code == 422


@pytest.mark.parametrize("operation", ["poll", "signal", "ready", "leave"])
def test_capability_rotated_while_waiting_for_lock_cannot_operate(team, monkeypatch, operation):
    client, engine, _, _ = team
    meeting = start(team); member = join(team, meeting); identifier = meeting["meeting"]["id"]
    original = meeting_service.lock_meeting
    def rotate_before_lock(session, meeting_id):
        with Session(engine) as writer:
            row = writer.get(MeetingParticipant, member["participant"]["id"])
            row.credential_hash = token_digest("replacement-token-that-is-at-least-32-characters")
            writer.add(row); writer.commit()
        return original(session, meeting_id)
    monkeypatch.setattr(meeting_service, "lock_meeting", rotate_before_lock)
    endpoint = f"/meeting-access/{identifier}/{operation}"
    response = (client.get(endpoint, headers=access(member)) if operation == "poll" else client.post(endpoint, headers=access(member), json=(
        {"recipient_id": meeting["participant"]["id"], "candidate": {"candidate": "stale-private-candidate"}} if operation == "signal" else {})))
    assert response.status_code == 401
    with Session(engine) as session:
        assert session.get(MeetingParticipant, member["participant"]["id"]).state == "admitted"
        assert session.exec(select(MeetingSignal)).all() == []


def test_native_explicit_rejoin_gets_fresh_transport_id_and_active_join_is_rejected(team):
    client, _, tokens, _ = team
    meeting = start(team); member = join(team, meeting); identifier = meeting["meeting"]["id"]
    assert client.post(f"/meetings/{identifier}/join", headers=native(tokens["member"]), json={}).status_code == 409
    assert client.post(f"/meeting-access/{identifier}/leave", headers=access(member), json={}).status_code == 200
    rejoined = join(team, meeting)
    assert rejoined["participant"]["id"] != member["participant"]["id"]
    assert poll(client, meeting, member).status_code == 401
    assert poll(client, meeting, rejoined).json()["signals"] == []


def test_guest_rejoin_replaces_capability_and_peer_id_and_requires_readmission(team):
    client, _, tokens, _ = team
    meeting = start(team); visitor, _ = guest(team, meeting); identifier = meeting["meeting"]["id"]
    assert client.post(f'/meetings/{identifier}/participants/{visitor["participant"]["id"]}/admit', headers=native(tokens["owner"]), json={}).status_code == 200
    assert client.post(f"/meeting-access/{identifier}/leave", headers=access(visitor), json={}).status_code == 200
    response = client.post(f"/meeting-access/{identifier}/resume", headers=access(visitor), json={})
    assert response.status_code == 201
    rejoined = response.json()
    assert rejoined["participant"]["id"] != visitor["participant"]["id"]
    assert rejoined["participant"]["state"] == "waiting"
    assert client.post(f"/meeting-access/{identifier}/resume", headers=access(visitor), json={}).status_code == 401
    assert poll(client, meeting, visitor).status_code == 401
    assert poll(client, meeting, rejoined).json()["participants"] == []
    assert client.post(f"/meeting-access/{identifier}/ready", headers=access(rejoined), json={}).status_code == 403
    assert client.post(f"/meeting-access/{identifier}/resume", headers=access(meeting), json={}).status_code == 401
    assert client.post(f'/meetings/{identifier}/participants/{rejoined["participant"]["id"]}/remove', headers=native(tokens["owner"]), json={}).status_code == 200
    assert client.post(f"/meeting-access/{identifier}/resume", headers=access(rejoined), json={}).status_code == 401
