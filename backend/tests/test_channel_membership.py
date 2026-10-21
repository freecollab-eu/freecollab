"""Invitation lifecycle and administrator boundaries for channel membership."""
import hashlib
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine, select

from app import database
from app.database import get_session
from app.main import app
from app.models.channel import ChannelInvitation, ChannelMember
from app.models.user import User
from app.realtime import socket
from app.services.auth import create_token


@pytest.fixture
def team(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'channels.db'}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(socket, "engine", engine)
    with Session(engine) as session:
        session.add_all([
            User(id=name, name=name.title(), email=f"{name}@example.com", password_hash="unused")
            for name in ("owner", "member", "guest", "other")
        ])
        session.commit()
    def sessions():
        with Session(engine) as session:
            yield session
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_session] = sessions
    headers = {name: {"Authorization": "Bearer " + create_token(name)} for name in ("owner", "member", "guest", "other")}
    with TestClient(app) as client:
        response = client.post("/channels", headers=headers["owner"], json={"name": "Private team", "member_ids": ["member"]})
        assert response.status_code == 200, response.text
        yield client, engine, headers, response.json()["id"]
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)
    engine.dispose()


def invite(team, actor="owner", **body):
    client, _, headers, channel = team
    return client.post(f"/channels/{channel}/invites", headers=headers[actor], json=body)


def accept(team, token, actor="guest"):
    client, _, headers, _ = team
    return client.post("/channels/invites/accept", headers=headers[actor], json={"token": token})


def test_admin_can_invite_once_without_storing_bearer_token(team):
    client, engine, headers, channel = team
    assert client.get(f"/messages/channels/{channel}", headers=headers["guest"]).status_code == 403
    response = invite(team)
    assert response.status_code == 201
    assert "no-store" in {directive.strip().lower() for directive in response.headers["Cache-Control"].split(",")}
    invitation = response.json()
    assert invitation["expires_at"].endswith("Z") or invitation["expires_at"].endswith("+00:00")
    with Session(engine) as session:
        stored = session.get(ChannelInvitation, invitation["id"])
        assert stored.token_hash == hashlib.sha256(invitation["token"].encode()).hexdigest()
        assert invitation["token"] not in repr(stored)
    listed = client.get(f"/channels/{channel}/invites", headers=headers["owner"])
    assert listed.status_code == 200 and len(listed.json()) == 1
    assert invitation["token"] not in listed.text and "token_hash" not in listed.text
    joined = accept(team, invitation["token"])
    assert joined.status_code == 200 and joined.json()["role"] == "member"
    assert joined.json()["id"] == channel
    assert client.get(f"/messages/channels/{channel}", headers=headers["guest"]).status_code == 200
    assert socket._is_member("guest", channel)
    assert accept(team, invitation["token"], "other").status_code == 404
    with Session(engine) as session:
        assert session.get(ChannelInvitation, invitation["id"]).accepted_by == "guest"
        assert session.get(ChannelMember, (channel, "other")) is None


@pytest.mark.parametrize("actor", ["member", "guest"])
def test_nonadmins_cannot_manage_invites(team, actor):
    client, _, headers, channel = team
    created = invite(team).json()
    assert invite(team, actor).status_code == 403
    assert client.get(f"/channels/{channel}/invites", headers=headers[actor]).status_code == 403
    assert client.delete(f'/channels/{channel}/invites/{created["id"]}', headers=headers[actor]).status_code == 403


@pytest.mark.parametrize("state", ["expired", "revoked", "issuer_blocked", "issuer_removed", "issuer_demoted"])
def test_invalidated_invites_never_grant_membership(team, state):
    client, engine, headers, channel = team
    created = invite(team).json()
    with Session(engine) as session:
        record = session.get(ChannelInvitation, created["id"])
        if state == "expired":
            record.expires_at = datetime.utcnow() - timedelta(seconds=1)
        elif state == "revoked":
            record.revoked_at = datetime.utcnow()
        elif state == "issuer_blocked":
            issuer = session.get(User, "owner")
            issuer.is_blocked = True
            session.add(issuer)
        elif state == "issuer_removed":
            session.delete(session.get(ChannelMember, (channel, "owner")))
        else:
            member = session.get(ChannelMember, (channel, "owner"))
            member.role = "member"
            session.add(member)
        session.add(record)
        session.commit()
    assert accept(team, created["token"]).status_code == 404
    with Session(engine) as session:
        assert session.get(ChannelMember, (channel, "guest")) is None


def test_admin_can_revoke_and_revocation_is_idempotent(team):
    client, _, headers, channel = team
    created = invite(team).json()
    path = f'/channels/{channel}/invites/{created["id"]}'
    assert client.delete(path, headers=headers["owner"]).status_code == 200
    assert client.delete(path, headers=headers["owner"]).status_code == 200
    assert accept(team, created["token"]).status_code == 404


def test_blocked_recipient_cannot_accept_invite(team):
    _, engine, _, _ = team
    created = invite(team).json()
    with Session(engine) as session:
        user = session.get(User, "guest")
        user.is_blocked = True
        session.add(user)
        session.commit()
    assert accept(team, created["token"]).status_code == 403
    assert accept(team, created["token"], "other").status_code == 200


def test_already_member_does_not_consume_invite(team):
    created = invite(team).json()
    assert accept(team, created["token"], "member").status_code == 409
    assert accept(team, created["token"], "guest").status_code == 200


@pytest.mark.parametrize("hours", [0, -1, 169])
def test_invite_lifetime_is_bounded(team, hours):
    assert invite(team, expires_in_hours=hours).status_code == 422


def test_member_cannot_promote_self_or_remove_another_member(team):
    client, _, headers, channel = team
    assert client.patch(f"/channels/{channel}/members/member", headers=headers["member"], json={"role": "admin"}).status_code == 403
    assert client.delete(f"/channels/{channel}/members/owner", headers=headers["member"]).status_code == 403
    assert client.get(f"/channels/{channel}/members", headers=headers["guest"]).status_code == 403


def test_last_admin_cannot_leave_remove_or_demote_self(team):
    client, _, headers, channel = team
    assert client.post(f"/channels/{channel}/leave", headers=headers["owner"]).status_code == 409
    assert client.delete(f"/channels/{channel}/members/owner", headers=headers["owner"]).status_code == 409
    assert client.patch(f"/channels/{channel}/members/owner", headers=headers["owner"], json={"role": "member"}).status_code == 409
    assert client.get(f"/channels/{channel}", headers=headers["owner"]).json()["role"] == "admin"


def test_admin_handover_allows_original_admin_to_leave(team):
    client, engine, headers, channel = team
    promoted = client.patch(f"/channels/{channel}/members/member", headers=headers["owner"], json={"role": "admin"})
    assert promoted.status_code == 200
    assert client.post(f"/channels/{channel}/leave", headers=headers["owner"]).status_code == 200
    assert client.get(f"/channels/{channel}", headers=headers["owner"]).status_code == 403
    assert client.get(f"/channels/{channel}", headers=headers["member"]).json()["role"] == "admin"
    with Session(engine) as session:
        assert session.get(ChannelMember, (channel, "owner")) is None


def test_removal_prunes_existing_socket_subscription(team, monkeypatch):
    client, _, headers, channel = team
    sio = Mock()
    sio.manager.get_participants.side_effect = lambda namespace, room: [("member-session", None)] if room == channel else []
    sio.get_session = AsyncMock(return_value={"user_id": "member", "token": create_token("member")})
    sio.leave_room = AsyncMock()
    sio.emit = AsyncMock()
    monkeypatch.setattr(socket, "sio", sio)
    response = client.delete(f"/channels/{channel}/members/member", headers=headers["owner"])
    assert response.status_code == 200
    sio.leave_room.assert_any_await("member-session", channel)
    assert not socket._is_member("member", channel)
    assert client.get(f"/messages/channels/{channel}", headers=headers["member"]).status_code == 403


def test_member_can_leave_without_admin_and_roles_are_visible(team):
    client, _, headers, channel = team
    channels = client.get("/channels", headers=headers["member"]).json()
    assert channels[0]["role"] == "member"
    members = client.get(f"/channels/{channel}/members", headers=headers["member"]).json()
    assert {member["user_id"]: member["role"] for member in members} == {"owner": "admin", "member": "member"}
    assert client.post(f"/channels/{channel}/leave", headers=headers["member"]).status_code == 200
    assert client.get("/channels", headers=headers["member"]).json() == []


def test_unknown_or_cross_channel_invite_does_not_change_membership(team):
    client, _, headers, channel = team
    created = invite(team).json()
    other = client.post("/channels", headers=headers["other"], json={"name": "Other"}).json()["id"]
    assert client.delete(f'/channels/{other}/invites/{created["id"]}', headers=headers["other"]).status_code == 404
    assert accept(team, "x" * 43).status_code == 404
    assert accept(team, created["token"]).status_code == 200


def test_unknown_channel_never_creates_invitation(team):
    client, engine, headers, _ = team
    response = client.post("/channels/not-found/invites", headers=headers["owner"], json={})
    assert response.status_code == 404
    with Session(engine) as session:
        assert session.exec(select(ChannelInvitation)).all() == []


def test_concurrent_acceptance_consumes_invitation_only_once(team):
    _, engine, _, channel = team
    created = invite(team).json()
    barrier = Barrier(2)
    def join(actor):
        barrier.wait(timeout=10)
        return accept(team, created["token"], actor).status_code
    with ThreadPoolExecutor(max_workers=2) as workers:
        statuses = list(workers.map(join, ["guest", "other"]))
    assert sorted(statuses) == [200, 404]
    with Session(engine) as session:
        accepted = [session.get(ChannelMember, (channel, actor)) for actor in ("guest", "other")]
        assert sum(member is not None for member in accepted) == 1


def test_concurrent_admin_leaves_preserve_one_admin(team):
    client, engine, headers, channel = team
    assert client.patch(f"/channels/{channel}/members/member", headers=headers["owner"], json={"role": "admin"}).status_code == 200
    barrier = Barrier(2)
    def leave(actor):
        barrier.wait(timeout=10)
        return client.post(f"/channels/{channel}/leave", headers=headers[actor]).status_code
    with ThreadPoolExecutor(max_workers=2) as workers:
        statuses = list(workers.map(leave, ["owner", "member"]))
    assert sorted(statuses) == [200, 409]
    with Session(engine) as session:
        admins = session.exec(select(ChannelMember).where(ChannelMember.channel_id == channel, ChannelMember.role == "admin")).all()
        assert len(admins) == 1
