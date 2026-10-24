"""Registered accounts create native rooms without unused legacy envelopes."""
from types import SimpleNamespace

import pytest
from sqlmodel import Session, select

from app import database
from app.config import settings
from app.models.channel import ChannelMember
from app.services.group_crypto import GroupCryptoService
from app.services.rate_limit import AuthRateLimiter
from tests.test_api import client  # noqa: F401 -- real application fixture


@pytest.fixture
def native_team(client, monkeypatch):
    monkeypatch.setattr(client.app.state, "auth_rate_limiter", AuthRateLimiter(), raising=False)
    users = []
    for name in ("owner", "peer"):
        response = client.post("/auth/register", json={
            "name": name.title(), "email": name + "@example.com", "password": "native-room-test-password",
        })
        assert response.status_code == 200, response.text
        users.append(response.json())
    assert all(user["public_key"] for user in users)
    headers = [{"Authorization": "Bearer " + user["access_token"]} for user in users]
    workspace = client.get("/workspaces", headers=headers[0]).json()[0]["id"]
    invitation = client.post("/workspaces/" + workspace + "/invites", headers=headers[0], json={})
    assert invitation.status_code == 201, invitation.text
    accepted = client.post("/workspaces/invites/accept", headers=headers[1], json={"token": invitation.json()["token"]})
    assert accepted.status_code == 200, accepted.text
    return SimpleNamespace(client=client, engine=database.engine, users=users, headers=headers, workspace=workspace)


def create_room(team, kind="group", *, direct_endpoint=False):
    if direct_endpoint:
        response = team.client.post("/direct-messages", headers=team.headers[0], json={
            "workspace_id": team.workspace, "user_id": team.users[1]["user_id"],
        })
    else:
        response = team.client.post("/channels", headers=team.headers[0], json={
            "name": "A real native room", "type": kind, "workspace_id": team.workspace,
            "member_ids": [team.users[1]["user_id"]],
        })
    assert response.status_code == 200, response.text
    return response.json()


def assert_native_material_flow(team, room):
    client = team.client
    channel_id = room["id"]
    prefix = "/channels/" + channel_id
    assert room["secure"] is False and room["matrix_room_id"] is None
    assert room["encrypted_channel_key"] is None
    with Session(team.engine) as session:
        memberships = session.exec(select(ChannelMember).where(ChannelMember.channel_id == channel_id)).all()
        assert len(memberships) == 2 and all(member.encrypted_channel_key is None for member in memberships)
    for headers in team.headers:
        listed = client.get("/channels?workspace_id=" + team.workspace, headers=headers)
        assert listed.status_code == 200, listed.text
        current = next(row for row in listed.json() if row["id"] == channel_id)
        assert current["encrypted_channel_key"] is None and current["secure"] is False
    headers = team.headers[0]
    message = client.post("/messages/channels/" + channel_id, headers=headers, json={"content": "Keep the next step in this conversation"})
    assert message.status_code == 200, message.text
    document = client.post(prefix + "/shared-documents", headers=headers, json={
        "id": "native-document", "update_id": "initial", "update_base64": "AAA=", "title": "Native brief",
    })
    assert document.status_code == 201, document.text
    project = client.post(prefix + "/projects", headers=headers, json={"id": "native-project", "fields": {"title": "Native work"}})
    assert project.status_code == 200, project.text
    task = client.post(prefix + "/projects/native-project/tasks", headers=headers, json={"id": "native-task", "fields": {"title": "Native next step"}})
    assert task.status_code == 200, task.text
    decision = client.post(prefix + "/decisions", headers=headers, json={"title": "Native agreement", "content": "Use the shared courtyard", "status": "proposed"})
    assert decision.status_code == 200, decision.text
    expected = {"document": "native-document", "task": "native-task", "decision": decision.json()["id"]}
    for kind, identifier in expected.items():
        attached = client.post(prefix + "/conversation-work", headers=headers, json={
            "source_message_id": message.json()["id"], "resource_kind": kind, "resource_id": identifier,
        })
        assert attached.status_code == 201, attached.text
    references = client.get(prefix + "/conversation-work", headers=team.headers[1])
    assert references.status_code == 200, references.text
    assert {row["resource_kind"]: row["resource_id"] for row in references.json()["items"]} == expected


@pytest.mark.parametrize("matrix_configured", [False, True])
@pytest.mark.parametrize("kind", ["group", "public"])
def test_registered_public_key_accounts_create_native_rooms_with_real_material_flows(native_team, monkeypatch, matrix_configured, kind):
    # Merely configuring Matrix never turns a new ordinary room into an E2EE room.
    monkeypatch.setattr(settings, "matrix_enabled", matrix_configured)
    assert_native_material_flow(native_team, create_room(native_team, kind))


@pytest.mark.parametrize("direct_endpoint", [False, True])
def test_both_direct_creation_apis_support_registered_accounts_native_material(native_team, direct_endpoint):
    assert_native_material_flow(native_team, create_room(native_team, "direct", direct_endpoint=direct_endpoint))


@pytest.mark.parametrize("kind", ["group", "direct"])
def test_existing_legacy_envelopes_are_preserved_and_still_deny_native_work_references(native_team, kind):
    room = create_room(native_team, kind)
    channel_key = GroupCryptoService.generate_channel_key()
    envelopes = GroupCryptoService.encrypt_key_for_members(channel_key, {
        user["user_id"]: user["public_key"] for user in native_team.users
    })
    with Session(native_team.engine) as session:
        for user in native_team.users:
            member = session.get(ChannelMember, (room["id"], user["user_id"]))
            member.encrypted_channel_key = envelopes[user["user_id"]]
            session.add(member)
        session.commit()
    if kind == "direct":
        reused = create_room(native_team, "direct", direct_endpoint=True)
        assert reused["id"] == room["id"] and reused["created"] is False
        assert reused["encrypted_channel_key"] == envelopes[native_team.users[0]["user_id"]]
        assert create_room(native_team, "direct")["id"] == room["id"]
    for index, user in enumerate(native_team.users):
        current = native_team.client.get("/channels/" + room["id"], headers=native_team.headers[index])
        assert current.status_code == 200 and current.json()["encrypted_channel_key"] == envelopes[user["user_id"]]
        response = native_team.client.get("/channels/" + room["id"] + "/conversation-work", headers=native_team.headers[index])
        assert response.status_code == 409, response.text
    with Session(native_team.engine) as session:
        assert {member.user_id: member.encrypted_channel_key for member in session.exec(select(ChannelMember).where(
            ChannelMember.channel_id == room["id"])).all()} == envelopes
