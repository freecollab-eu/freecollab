"""Workspace isolation, invited guest scope, and serialized ownership changes."""
import base64
import asyncio
import hashlib
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from app import database
from app import main as application  # noqa: F401 -- register the complete released model graph
from app.api import channels, direct_messages, messages, presence, workspaces
from app.config import settings
from app.database import get_session
from app.models.channel import Channel, ChannelMember
from app.models.workspace import Workspace, WorkspaceChannel, WorkspaceInvitation, WorkspaceMember
from app.models.user import User
from app.realtime import socket
from app.services.auth import create_token
from app.services.workspaces import init_workspaces


@pytest.fixture
def workspace_team(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'workspace.db'}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(socket, "engine", engine)
    monkeypatch.setattr(settings, "storage_path", str(tmp_path / "files"))
    names = ("owner", "member", "guest", "hidden", "outsider", "newbie")
    with Session(engine) as session:
        session.add_all([User(id=name, name=name.title(), email=f"{name}@example.com", password_hash="unused") for name in names])
        session.add(Workspace(id="team", name="Team"))
        session.flush()
        session.add_all([WorkspaceMember(workspace_id="team", user_id=name, role=role) for name, role in (
            ("owner", "owner"), ("member", "member"), ("guest", "guest"), ("hidden", "member"),
        )])
        session.add_all([Channel(id="shared", name="Shared", type="group"), Channel(id="private", name="Private", type="group")])
        session.flush()
        session.add_all([WorkspaceChannel(channel_id=channel, workspace_id="team") for channel in ("shared", "private")])
        session.add_all([ChannelMember(channel_id=channel, user_id=name, role="admin" if name == "owner" else "member")
                         for channel, members in (("shared", ("owner", "member", "guest")), ("private", ("owner", "hidden")))
                         for name in members])
        session.commit()
    api = FastAPI()
    for module in (channels, workspaces, direct_messages, messages, presence):
        api.include_router(module.router)
    def sessions():
        with Session(engine) as session:
            yield session
    api.dependency_overrides[get_session] = sessions
    headers = {name: {"Authorization": "Bearer " + create_token(name)} for name in names}
    with TestClient(api) as client:
        yield client, engine, headers
    engine.dispose()


def test_custom_workspace_is_explicit_and_registration_home_does_not_grant_pilot_access(workspace_team):
    client, engine, headers = workspace_team
    listed = client.get("/workspaces", headers=headers["outsider"])
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    personal = listed.json()[0]
    assert personal["role"] == "owner" and personal["is_legacy"] is False
    assert personal["id"] != "team" and personal["member_count"] == 1
    assert client.get("/workspaces/team", headers=headers["outsider"]).status_code == 403
    created = client.post("/workspaces", headers=headers["outsider"], json={"name": "  Our studio  "})
    assert created.status_code == 201
    assert created.json()["name"] == "Our studio" and created.json()["role"] == "owner"
    assert client.get(f'/workspaces/{created.json()["id"]}', headers=headers["owner"]).status_code == 403
    assert client.post("/workspaces", headers=headers["owner"], json={"name": " ", "role": "owner"}).status_code == 422
    with Session(engine) as session:
        assert session.get(WorkspaceMember, ("legacy", "outsider")) is None
        assert session.get(WorkspaceMember, (personal["id"], "outsider")).role == "owner"
        assert session.get(User, "outsider").role == "user"


def test_guest_sees_only_assigned_channels_and_shared_people(workspace_team):
    client, _, headers = workspace_team
    people = client.get("/workspaces/team/members", headers=headers["guest"])
    assert people.status_code == 200
    assert {row["user_id"] for row in people.json()} == {"owner", "member", "guest"}
    assert all("email" not in row and "password_hash" not in row for row in people.json())
    listed = client.get("/channels?workspace_id=team", headers=headers["guest"])
    assert [row["id"] for row in listed.json()] == ["shared"]
    assert client.get("/channels/private", headers=headers["guest"]).status_code == 403
    assert client.post("/channels", headers=headers["guest"], json={"name": "Sneaky", "workspace_id": "team", "type": "public"}).status_code == 403
    assert client.post("/channels", headers=headers["owner"], json={"name": "Team", "workspace_id": "team", "member_ids": ["outsider"]}).status_code == 403
    assert client.get("/workspaces/team/members?query=HIDDEN", headers=headers["guest"]).json() == []
    assert client.get("/workspaces/team/members", headers=headers["outsider"]).status_code == 403


def test_channel_invite_grants_only_a_guest_membership_in_custom_workspace(workspace_team):
    client, engine, headers = workspace_team
    invitation = client.post("/channels/shared/invites", headers=headers["owner"], json={}).json()
    joined = client.post("/channels/invites/accept", headers=headers["outsider"], json={"token": invitation["token"]})
    assert joined.status_code == 200 and joined.json()["workspace_id"] == "team"
    with Session(engine) as session:
        assert session.get(WorkspaceMember, ("team", "outsider")).role == "guest"
    assert client.get("/channels/private", headers=headers["outsider"]).status_code == 403
    assert "hidden" not in {row["user_id"] for row in client.get("/workspaces/team/members", headers=headers["outsider"]).json()}


def test_workspace_invites_are_hashed_single_use_and_recheck_issuer_role(workspace_team):
    client, engine, headers = workspace_team
    invitation = client.post("/workspaces/team/invites", headers=headers["owner"], json={"role": "admin"})
    assert invitation.status_code == 201
    token = invitation.json()["token"]
    with Session(engine) as session:
        stored = session.get(WorkspaceInvitation, invitation.json()["id"])
        assert stored.token_hash == hashlib.sha256(token.encode()).hexdigest()
        assert token not in repr(stored)
    listed = client.get("/workspaces/team/invites", headers=headers["owner"])
    assert token not in listed.text and "token_hash" not in listed.text
    joined = client.post("/workspaces/invites/accept", headers=headers["outsider"], json={"token": token})
    assert joined.status_code == 200 and joined.json()["role"] == "admin"
    assert client.post("/workspaces/invites/accept", headers=headers["newbie"], json={"token": token}).status_code == 404
    assert client.post("/workspaces/team/invites", headers=headers["outsider"], json={"role": "admin"}).status_code == 403
    assert client.patch("/workspaces/team/members/owner", headers=headers["outsider"], json={"role": "member"}).status_code == 403
    stale = client.post("/workspaces/team/invites", headers=headers["outsider"], json={"role": "member"}).json()["token"]
    assert client.patch("/workspaces/team/members/outsider", headers=headers["owner"], json={"role": "member"}).status_code == 200
    assert client.post("/workspaces/invites/accept", headers=headers["newbie"], json={"token": stale}).status_code == 404


def test_parallel_owner_departures_keep_one_owner(workspace_team):
    client, engine, headers = workspace_team
    with Session(engine) as session:
        second = session.get(WorkspaceMember, ("team", "member"))
        second.role = "owner"
        session.add(second)
        session.commit()
    barrier = Barrier(2)
    def leave(name):
        barrier.wait(timeout=5)
        return client.post("/workspaces/team/leave", headers=headers[name]).status_code
    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(leave, ("owner", "member")))
    assert sorted(statuses) == [200, 409]
    with Session(engine) as session:
        assert len(session.exec(select(WorkspaceMember).where(WorkspaceMember.workspace_id == "team", WorkspaceMember.role == "owner")).all()) == 1


def test_workspace_removal_hands_channel_admin_to_existing_member_and_revokes_native_access(workspace_team, monkeypatch):
    client, engine, headers = workspace_team
    from app.services import matrix_bridge
    observed = []
    def revoke(session, channel_id, user_id):
        observed.append((channel_id, bool(session.get(ChannelMember, (channel_id, user_id)))))
    monkeypatch.setattr(matrix_bridge, "remove_secure_member", revoke)
    # The only channel administrator is allowed to leave a workspace once a
    # second workspace owner exists. Channel ownership transfers only to an existing participant.
    assert client.patch("/workspaces/team/members/member", headers=headers["owner"], json={"role": "owner"}).status_code == 200
    removed = client.post("/workspaces/team/leave", headers=headers["owner"])
    assert removed.status_code == 200
    assert set(observed) == {("shared", True), ("private", True)}
    with Session(engine) as session:
        assert session.get(ChannelMember, ("shared", "member")).role == "admin"
        assert session.get(ChannelMember, ("private", "hidden")).role == "admin"
        assert session.get(ChannelMember, ("shared", "owner")) is None
    assert client.get("/channels/shared", headers=headers["owner"]).status_code == 403
    assert client.get("/messages/channels/shared", headers=headers["owner"]).status_code == 403


def test_matrix_removal_failure_keeps_workspace_and_native_membership(workspace_team, monkeypatch):
    client, engine, headers = workspace_team
    from app.services import matrix_bridge
    monkeypatch.setattr(matrix_bridge, "remove_secure_member", Mock(side_effect=HTTPException(503, "Unavailable")))
    removed = client.delete("/workspaces/team/members/member", headers=headers["owner"])
    assert removed.status_code == 503
    with Session(engine) as session:
        assert session.get(WorkspaceMember, ("team", "member")) is not None
        assert session.get(ChannelMember, ("shared", "member")) is not None


def test_legacy_backfill_is_additive_idempotent_and_never_creates_channel_access(workspace_team):
    _, engine, _ = workspace_team
    with Session(engine) as session:
        session.add(Channel(id="old", name="Old", type="group"))
        session.flush()
        session.add_all([ChannelMember(channel_id="old", user_id=name) for name in ("owner", "member")])
        session.commit()
        before = {(row.channel_id, row.user_id) for row in session.exec(select(ChannelMember)).all()}
    init_workspaces(engine)
    init_workspaces(engine)
    with Session(engine) as session:
        assert session.get(WorkspaceChannel, "old").workspace_id == "legacy"
        assert {(row.channel_id, row.user_id) for row in session.exec(select(ChannelMember)).all()} == before
        assert session.get(WorkspaceMember, ("legacy", "owner")).role == "member"
        assert session.get(WorkspaceMember, ("legacy", "outsider")) is None
        assert session.get(WorkspaceChannel, "shared").workspace_id == "team"


def test_logos_are_bounded_raster_and_authorized(workspace_team):
    client, _, headers = workspace_team
    pixel = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6eRIAAAAASUVORK5CYII=")
    assert client.post("/workspaces/team/logo", headers=headers["member"], files={"file": ("logo.png", pixel, "image/png")}).status_code == 403
    assert client.post("/workspaces/team/logo", headers=headers["owner"], files={"file": ("logo.svg", b'<svg onload="alert(1)"></svg>', "image/svg+xml")}).status_code == 400
    assert client.post("/workspaces/team/logo", headers=headers["owner"], files={"file": ("large.png", pixel + b'x' * (1024 * 1024), "image/png")}).status_code == 413
    uploaded = client.post("/workspaces/team/logo", headers=headers["owner"], files={"file": ("logo.png", pixel, "image/png")})
    assert uploaded.status_code == 200
    assert uploaded.json()["logo_url"] == "/workspaces/team/logo"
    assert client.get("/workspaces/team/logo", headers=headers["outsider"]).status_code == 403
    image = client.get("/workspaces/team/logo", headers=headers["member"])
    assert image.status_code == 200 and image.content == pixel
    assert image.headers["content-type"] == "image/png"
    assert image.headers["x-content-type-options"] == "nosniff"
    assert client.delete("/workspaces/team/logo", headers=headers["owner"]).json()["logo_url"] is None
    assert client.get("/workspaces/team/logo", headers=headers["owner"]).status_code == 404


def test_presence_directory_is_directional_and_does_not_leak_other_workspaces(workspace_team, monkeypatch):
    client, engine, headers = workspace_team
    monkeypatch.setattr(presence, "get_online_users", lambda *_: ["owner", "guest", "hidden", "outsider", "missing"])
    assert client.get("/presence/online", headers=headers["guest"]).json()["online_users"] == ["owner", "guest"]
    assert client.get("/presence/users/hidden", headers=headers["guest"]).status_code == 404
    assert client.get("/presence/users/guest", headers=headers["hidden"]).status_code == 200
    assert client.get("/presence/users/outsider", headers=headers["owner"]).status_code == 404
    # Loading workspace state must not enroll an invited guest in a public fallback directory.
    assert [row["id"] for row in client.get("/workspaces", headers=headers["guest"]).json()] == ["team"]
    with Session(engine) as session:
        session.delete(session.get(WorkspaceMember, ("team", "guest")))
        session.commit()
    assert client.get("/presence/users/owner", headers=headers["guest"]).status_code == 404
    assert client.get("/presence/channel/shared", headers=headers["guest"]).status_code == 403


def test_presence_socket_uses_current_recipient_scope_and_private_rooms(workspace_team, monkeypatch):
    _, engine, headers = workspace_team
    sio = Mock()
    sio.emit = AsyncMock()
    sio.leave_room = AsyncMock()
    sio.manager.get_participants.side_effect=lambda namespace,room:[(room.removeprefix('user:'),'engine-sid')]
    sio.get_session=AsyncMock(side_effect=lambda sid:{"user_id":sid,"token":headers[sid]["Authorization"].removeprefix('Bearer ')})
    monkeypatch.setattr(socket, "sio", sio)
    monkeypatch.setattr(socket, "presence_store", {name: {"sid": name, "status": "online"} for name in ("owner", "member", "guest", "hidden", "outsider")})
    asyncio.run(socket.broadcast_presence("hidden", {"user_id": "hidden", "status": "away"}))
    assert {call.kwargs["room"] for call in sio.emit.await_args_list} == {"owner", "member", "hidden"}
    assert all(not call.kwargs.get("room", "").startswith("user:") for call in sio.emit.await_args_list)
    sio.emit.reset_mock()
    with Session(engine) as session:
        session.delete(session.get(WorkspaceMember, ("team", "member")))
        session.commit()
    asyncio.run(socket.broadcast_presence("hidden", {"user_id": "hidden", "status": "online"}))
    assert {call.kwargs["room"] for call in sio.emit.await_args_list} == {"owner", "hidden"}


def test_channel_security_binding_survives_list_get_and_direct_reopen_without_public_leak(workspace_team):
    client, engine, headers = workspace_team
    from app.models.secure_channel import SecureChannel
    direct = client.post("/direct-messages", headers=headers["owner"], json={"workspace_id": "team", "user_id": "member"}).json()
    with Session(engine) as session:
        session.add_all([
            SecureChannel(channel_id="shared", matrix_room_id="!shared:matrix.local", created_by="owner", status="active"),
            SecureChannel(channel_id="private", created_by="owner", status="preparing"),
            SecureChannel(channel_id=direct["id"], matrix_room_id="!dm:matrix.local", created_by="owner", status="active"),
        ])
        session.commit()
    fetched = client.get("/channels/shared", headers=headers["guest"])
    assert fetched.status_code == 200 and fetched.json()["secure"] is True
    assert fetched.json()["matrix_room_id"] == "!shared:matrix.local"
    listed = {row["id"]: row for row in client.get("/channels?workspace_id=team", headers=headers["owner"]).json()}
    assert listed["private"]["secure"] is True and listed["private"]["matrix_room_id"] is None
    reopened = client.post("/direct-messages", headers=headers["member"], json={"workspace_id": "team", "user_id": "owner"}).json()
    assert reopened["secure"] is True and reopened["matrix_room_id"] == "!dm:matrix.local"
    outsider = client.get("/channels/shared", headers=headers["outsider"])
    assert outsider.status_code == 403 and "matrix_room_id" not in outsider.json()


def test_parallel_legacy_backfill_maps_once(workspace_team):
    _, engine, _ = workspace_team
    with Session(engine) as session:
        session.add(Channel(id="old-parallel", type="direct"))
        session.flush()
        session.add_all([ChannelMember(channel_id="old-parallel", user_id=name) for name in ("owner", "member")])
        session.commit()
    barrier = Barrier(2)
    def migrate(_):
        barrier.wait(timeout=5)
        init_workspaces(engine)
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(migrate, (1, 2)))
    with Session(engine) as session:
        assert session.get(WorkspaceChannel, "old-parallel").workspace_id == "legacy"
        assert len(session.exec(select(WorkspaceMember).where(WorkspaceMember.workspace_id == "legacy")).all()) == 2


def test_parallel_workspace_invitation_acceptance_is_single_use(workspace_team):
    client, engine, headers = workspace_team
    invitation = client.post("/workspaces/team/invites", headers=headers["owner"], json={"role": "guest"}).json()
    barrier = Barrier(2)
    def accept(name):
        barrier.wait(timeout=5)
        return client.post("/workspaces/invites/accept", headers=headers[name], json={"token": invitation["token"]}).status_code
    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(accept, ("outsider", "newbie")))
    assert sorted(statuses) == [200, 404]
    with Session(engine) as session:
        accepted = session.get(WorkspaceInvitation, invitation["id"])
        assert accepted.accepted_by in {"outsider", "newbie"}
        assert sum(session.get(WorkspaceMember, ("team", name)) is not None for name in ("outsider", "newbie")) == 1


@pytest.mark.parametrize("kind", ["message", "upload", "post"])
@pytest.mark.parametrize("first", ["native", "secure"])
def test_native_write_and_secure_enable_share_one_history_fence(workspace_team, monkeypatch, kind, first):
    """Either acknowledged history stays native, or preparing rejects plaintext."""
    from threading import Event
    from app.api import files, posts, secure_messaging
    from app.models.file import File
    from app.models.message import Message
    from app.models.post import Post
    from app.models.secure_channel import SecureChannel
    client, engine, headers = workspace_team
    client.app.include_router(files.router)
    client.app.include_router(posts.router)
    client.app.include_router(secure_messaging.router)
    monkeypatch.setattr(secure_messaging, "require_matrix", Mock())
    def room(session, binding):
        binding.matrix_room_id = "!fenced:matrix.local"
        binding.status = "active"
        session.add(binding)
        session.flush()
    monkeypatch.setattr(secure_messaging, "create_secure_room", room)
    monkeypatch.setattr(secure_messaging, "sync_secure_membership", Mock())
    native_api = {"message": messages, "upload": files, "post": posts}[kind]
    native_lock = native_api.lock_channel_writes
    secure_lock = secure_messaging.lock_channel_writes
    locked = Event()
    contender = Event()
    release = Event()
    def hold_native(session, channel_id):
        if first == "secure":
            contender.set()
        native_lock(session, channel_id)
        if first == "native":
            locked.set()
            assert release.wait(timeout=5), "Test did not release native transaction"
    def hold_secure(session, channel_id):
        if first == "native":
            contender.set()
        secure_lock(session, channel_id)
        if first == "secure":
            locked.set()
            assert release.wait(timeout=5), "Test did not release secure transaction"
    monkeypatch.setattr(native_api, "lock_channel_writes", hold_native)
    monkeypatch.setattr(secure_messaging, "lock_channel_writes", hold_secure)
    if kind == "upload":
        # Upload publication now obtains User/Auth/Workspace fences before its
        # channel fence. Observe that real attempt before it can block on the
        # secure starter's workspace transaction; keep the native hold at the
        # actual channel fence and preserve both final history outcomes below.
        actor_lock = files.lock_upload_actor
        def observe_upload_actor(session, channel_id, actor_id):
            if first == "secure":
                contender.set()
            return actor_lock(session, channel_id, actor_id)
        monkeypatch.setattr(files, "lock_upload_actor", observe_upload_actor)
    def native_request():
        if kind == "message":
            return client.post("/messages/channels/shared", headers=headers["owner"], json={"content": "Acknowledged native message"})
        if kind == "post":
            return client.post("/posts/flows/shared", headers=headers["owner"], json={"content": "Acknowledged native post"})
        return client.post("/files/channels/shared", headers=headers["owner"], files={"file": ("notes.txt", b"Acknowledged native file", "text/plain")})
    def secure_request():
        # Independent ASGI loop makes both real requests concurrent even while
        # the first endpoint holds a synchronous database write lock.
        with TestClient(client.app) as secure_client:
            return secure_client.post("/secure/channels/shared/enable", headers=headers["owner"])
    with ThreadPoolExecutor(max_workers=2) as executor:
        starter = executor.submit(native_request if first == "native" else secure_request)
        try:
            assert locked.wait(timeout=5), "First request did not obtain its channel lock"
            follower = executor.submit(secure_request if first == "native" else native_request)
            assert contender.wait(timeout=5), "Concurrent request did not reach the lock"
        finally:
            release.set()
        first_response, second_response = starter.result(timeout=10), follower.result(timeout=10)
    assert first_response.status_code == 200, first_response.text
    assert second_response.status_code == 409, second_response.text
    with Session(engine) as session:
        assert (session.get(SecureChannel, "shared") is not None) == (first == "secure")
        if kind == "message":
            native_rows = session.exec(select(Message).where(Message.channel_id == "shared")).all()
        elif kind == "post":
            native_rows = session.exec(select(Post).where(Post.flow_id == "shared")).all()
        else:
            native_rows = session.exec(select(File).where(File.channel_id == "shared")).all()
        assert len(native_rows) == (1 if first == "native" else 0)


def test_fresh_account_provider_fetch_before_guest_invitation_never_grants_pilot_directory(workspace_team):
    client, engine, headers = workspace_team
    # Preserve the original API for existing pilot scripts while making its
    # directory unavailable to new accounts that have not joined a channel.
    with Session(engine) as session:
        session.add(Channel(id="pilot-existing", name="Pilot"))
        session.flush()
        session.add_all([ChannelMember(channel_id="pilot-existing", user_id=name) for name in ("owner", "hidden")])
        session.commit()
    init_workspaces(engine)
    legacy = client.post("/channels", headers=headers["owner"], json={"name": "Pilot follow-up", "member_ids": ["hidden"]})
    assert legacy.status_code == 200 and legacy.json()["workspace_id"] == "legacy"
    initial = client.get("/workspaces", headers=headers["outsider"])
    assert initial.status_code == 200 and len(initial.json()) == 1
    assert initial.json()[0]["is_legacy"] is False
    personal_id = initial.json()[0]["id"]
    own_directory = client.get(f"/workspaces/{personal_id}/members", headers=headers["outsider"])
    assert {person["user_id"] for person in own_directory.json()} == {"outsider"}
    assert client.get("/workspaces/legacy/members", headers=headers["outsider"]).status_code == 403
    invitation = client.post("/channels/shared/invites", headers=headers["owner"], json={}).json()
    assert client.post("/channels/invites/accept", headers=headers["outsider"], json={"token": invitation["token"]}).status_code == 200
    fetched = client.get("/workspaces", headers=headers["outsider"]).json()
    assert {row["id"] for row in fetched} == {personal_id, "team"}
    assert client.get("/presence/users/hidden", headers=headers["outsider"]).status_code == 404
    with Session(engine) as session:
        assert session.get(WorkspaceMember, ("legacy", "outsider")) is None
        assert session.get(WorkspaceMember, ("team", "outsider")).role == "guest"


def test_parallel_initial_workspace_fetch_creates_only_one_private_home(workspace_team):
    client, engine, headers = workspace_team
    barrier = Barrier(2)
    def load(_):
        barrier.wait(timeout=5)
        response = client.get("/workspaces", headers=headers["newbie"])
        assert response.status_code == 200, response.text
        return response.json()[0]["id"]
    with ThreadPoolExecutor(max_workers=2) as executor:
        identifiers = list(executor.map(load, (1, 2)))
    assert len(set(identifiers)) == 1
    with Session(engine) as session:
        memberships = session.exec(select(WorkspaceMember).where(WorkspaceMember.user_id == "newbie")).all()
        assert len(memberships) == 1 and memberships[0].role == "owner"
        assert not session.get(Workspace, memberships[0].workspace_id).is_legacy


def test_omitting_workspace_never_enrolls_fresh_caller_in_existing_pilot_directory(workspace_team):
    client, engine, headers = workspace_team
    with Session(engine) as session:
        session.add(Channel(id="pilot-original", name="Pilot"))
        session.flush()
        session.add_all([ChannelMember(channel_id="pilot-original", user_id=name) for name in ("owner", "hidden")])
        session.commit()
    init_workspaces(engine)
    response = client.post("/channels", headers=headers["outsider"], json={"name": "Private", "member_ids": ["member"]})
    assert response.status_code == 200
    personal_id = response.json()["workspace_id"]
    assert personal_id != "legacy" and personal_id != "team"
    with Session(engine) as session:
        assert session.get(WorkspaceMember, ("legacy", "outsider")) is None
        assert session.get(WorkspaceMember, (personal_id, "outsider")).role == "owner"
        assert session.get(WorkspaceMember, (personal_id, "member")).role == "guest"
    assert client.get("/workspaces/legacy/members", headers=headers["outsider"]).status_code == 403
    assert {row["user_id"] for row in client.get(f"/workspaces/{personal_id}/members", headers=headers["outsider"]).json()} == {"outsider", "member"}
    assert client.get("/presence/users/hidden", headers=headers["outsider"]).status_code == 404
    assert client.post("/channels", headers=headers["guest"], json={"name": "Implicit bypass"}).status_code == 403


def test_implicit_private_invitation_never_opens_another_private_channel(workspace_team):
    client, _, headers = workspace_team
    first = client.post("/channels", headers=headers["outsider"], json={"name": "Only me"})
    assert first.status_code == 200
    second = client.post("/channels", headers=headers["outsider"], json={"name": "Invited", "member_ids": ["newbie"]})
    assert second.status_code == 200 and second.json()["workspace_id"] == first.json()["workspace_id"]
    assert client.get(f'/channels/{first.json()["id"]}', headers=headers["newbie"]).status_code == 403
    assert client.get(f'/channels/{second.json()["id"]}', headers=headers["newbie"]).status_code == 200


def test_departed_personal_owner_can_get_new_private_home_without_rejoining_old_workspace(workspace_team):
    client, _, headers = workspace_team
    created = client.post("/channels", headers=headers["outsider"], json={"member_ids": ["newbie"]}).json()
    previous = created["workspace_id"]
    assert client.patch(f"/workspaces/{previous}/members/newbie", headers=headers["outsider"], json={"role": "owner"}).status_code == 200
    assert client.post(f"/workspaces/{previous}/leave", headers=headers["outsider"]).status_code == 200
    loaded = client.get("/workspaces", headers=headers["outsider"])
    assert loaded.status_code == 200 and len(loaded.json()) == 1
    assert loaded.json()[0]["id"] != previous and loaded.json()[0]["role"] == "owner"
    assert client.get(f"/workspaces/{previous}", headers=headers["outsider"]).status_code == 403


def test_legacy_creation_rechecks_membership_after_waiting_for_workspace_lock(workspace_team, monkeypatch):
    client, engine, headers = workspace_team
    from app.services import workspaces as workspace_service
    with Session(engine) as session:
        session.add(Channel(id="legacy-race", name="Pilot"))
        session.flush()
        session.add(ChannelMember(channel_id="legacy-race", user_id="owner"))
        session.commit()
    init_workspaces(engine)
    original = workspace_service.lock_workspace
    def remove_before_lock(session, workspace_id):
        with Session(engine) as concurrent:
            concurrent.delete(concurrent.get(WorkspaceMember, ("legacy", "owner")))
            concurrent.commit()
        original(session, workspace_id)
    monkeypatch.setattr(workspace_service, "lock_workspace", remove_before_lock)
    denied = client.post("/channels", headers=headers["owner"], json={"name": "Removed while waiting"})
    assert denied.status_code == 403
