"""Stable two-person identity, scoped discovery, and personal inbox controls."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import Mock
from fastapi import HTTPException

from sqlmodel import Session, select

from app.models.channel import ChannelMember
from app.models.direct_message import DirectMessage, DirectMessageState
from app.models.workspace import Workspace, WorkspaceMember
from .test_workspaces import workspace_team


def open_dm(team, actor="owner", other="member", workspace_id="team"):
    client, _, headers = team
    return client.post("/direct-messages", headers=headers[actor], json={"workspace_id": workspace_id, "user_id": other})


def test_opening_from_either_side_returns_same_exact_pair(workspace_team):
    first = open_dm(workspace_team)
    assert first.status_code == 200 and first.json()["created"] is True
    second = open_dm(workspace_team, "member", "owner")
    assert second.status_code == 200 and second.json()["created"] is False
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["other_user"] == {"id": "member", "name": "Member"}
    _, engine, _ = workspace_team
    with Session(engine) as session:
        assert len(session.exec(select(DirectMessage)).all()) == 1
        assert {row.user_id for row in session.exec(select(ChannelMember).where(ChannelMember.channel_id == first.json()["id"])).all()} == {"owner", "member"}


def test_parallel_opens_create_one_conversation(workspace_team):
    barrier = Barrier(2)
    def create(pair):
        barrier.wait(timeout=5)
        response = open_dm(workspace_team, *pair)
        assert response.status_code == 200, response.text
        return response.json()
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create, (("owner", "member"), ("member", "owner"))))
    assert len({result["id"] for result in results}) == 1
    assert sorted(result["created"] for result in results) == [False, True]


def test_direct_identity_is_scoped_to_workspace(workspace_team):
    _, engine, _ = workspace_team
    first = open_dm(workspace_team).json()
    with Session(engine) as session:
        session.add(Workspace(id="second", name="Second"))
        session.flush()
        session.add_all([WorkspaceMember(workspace_id="second", user_id=name) for name in ("owner", "member")])
        session.commit()
    second = open_dm(workspace_team, workspace_id="second")
    assert second.status_code == 200 and second.json()["id"] != first["id"]


def test_guests_cannot_discover_hidden_people_or_create_unauthorized_pairs(workspace_team):
    assert open_dm(workspace_team, "guest", "hidden").status_code == 404
    assert open_dm(workspace_team, "owner", "outsider").status_code == 404
    assert open_dm(workspace_team, "outsider", "owner").status_code == 403
    assert open_dm(workspace_team, "owner", "owner").status_code == 400
    assert open_dm(workspace_team, "guest", "member").status_code == 200


def test_direct_channels_reject_invites_third_members_and_native_pair_mutation(workspace_team):
    client, _, headers = workspace_team
    channel = open_dm(workspace_team).json()["id"]
    assert client.post(f"/channels/{channel}/invites", headers=headers["owner"], json={}).status_code == 409
    assert client.get(f"/channels/{channel}/invites", headers=headers["owner"]).status_code == 409
    assert client.patch(f"/channels/{channel}/members/member", headers=headers["owner"], json={"role": "member"}).status_code == 409
    assert client.delete(f"/channels/{channel}/members/member", headers=headers["owner"]).status_code == 409
    assert client.post(f"/channels/{channel}/leave", headers=headers["owner"]).status_code == 409
    bad = client.post("/channels", headers=headers["owner"], json={"workspace_id": "team", "type": "direct", "member_ids": ["member", "hidden"]})
    assert bad.status_code == 400
    old_api = client.post("/channels", headers=headers["member"], json={"workspace_id": "team", "type": "direct", "member_ids": ["owner"]})
    assert old_api.status_code == 200 and old_api.json()["id"] == channel


def test_archive_is_personal_keeps_history_and_new_message_restores_inbox(workspace_team):
    client, engine, headers = workspace_team
    channel = open_dm(workspace_team).json()["id"]
    sent = client.post(f"/messages/channels/{channel}", headers=headers["owner"], json={"content": "Retained history"})
    assert sent.status_code == 200, sent.text
    assert client.patch(f"/direct-messages/{channel}", headers=headers["owner"], json={"archived": True}).status_code == 200
    own = client.get("/direct-messages?workspace_id=team", headers=headers["owner"]).json()[0]
    other = client.get("/direct-messages?workspace_id=team", headers=headers["member"]).json()[0]
    assert own["archived"] is True and other["archived"] is False
    assert channel not in {row["id"] for row in client.get("/channels?workspace_id=team", headers=headers["owner"]).json()}
    history = client.get(f"/messages/channels/{channel}", headers=headers["owner"])
    assert history.status_code == 200 and "Retained history" in history.text
    new = client.post(f"/messages/channels/{channel}", headers=headers["member"], json={"content": "New message"})
    assert new.status_code == 200, new.text
    with Session(engine) as session:
        assert session.get(DirectMessageState, (channel, "owner")).archived is False
    assert client.patch(f"/direct-messages/{channel}", headers=headers["hidden"], json={"archived": True}).status_code == 404


def test_block_is_personal_idempotent_and_stops_both_send_directions_without_erasing_history(workspace_team):
    client, _, headers = workspace_team
    channel = open_dm(workspace_team).json()["id"]
    assert client.post(f"/messages/channels/{channel}", headers=headers["owner"], json={"content": "Before block"}).status_code == 200
    for _ in range(2):
        assert client.post("/direct-messages/blocks/member", headers=headers["owner"]).status_code == 200
    assert client.get("/direct-messages/blocks", headers=headers["owner"]).json() == [{"user_id": "member", "name": "Member"}]
    assert client.get("/direct-messages/blocks", headers=headers["member"]).json() == []
    for name in ("owner", "member"):
        assert client.post(f"/messages/channels/{channel}", headers=headers[name], json={"content": "Rejected"}).status_code == 403
        assert client.get(f"/messages/channels/{channel}", headers=headers[name]).status_code == 200
    assert open_dm(workspace_team, "member", "owner").status_code == 403
    assert client.delete("/direct-messages/blocks/owner", headers=headers["member"]).status_code == 200
    assert open_dm(workspace_team).status_code == 403
    assert client.delete("/direct-messages/blocks/member", headers=headers["owner"]).status_code == 200
    assert client.post(f"/messages/channels/{channel}", headers=headers["member"], json={"content": "After unblock"}).status_code == 200
    assert client.post("/direct-messages/blocks/outsider", headers=headers["guest"]).status_code == 404


def test_workspace_removal_retains_pair_but_revokes_access_and_further_delivery(workspace_team):
    client, engine, headers = workspace_team
    channel = open_dm(workspace_team).json()["id"]
    removed = client.delete("/workspaces/team/members/member", headers=headers["owner"])
    assert removed.status_code == 200
    with Session(engine) as session:
        assert session.get(DirectMessage, channel) is not None
        assert session.get(ChannelMember, (channel, "member")) is not None
        assert session.get(WorkspaceMember, ("team", "member")) is None
    assert client.get(f"/channels/{channel}", headers=headers["member"]).status_code == 403
    assert client.get(f"/messages/channels/{channel}", headers=headers["member"]).status_code == 403
    assert client.post(f"/messages/channels/{channel}", headers=headers["owner"], json={"content": "No delivery"}).status_code == 403
    assert client.get("/direct-messages?workspace_id=team", headers=headers["owner"]).json()[0]["blocked"] is True


def test_secure_block_revokes_both_participants_before_persisting_and_failure_is_not_success(workspace_team, monkeypatch):
    client, engine, headers = workspace_team
    from app.api import direct_messages
    from app.models.direct_message import UserBlock
    channel = open_dm(workspace_team).json()["id"]
    observed = []
    def revoke(session, channel_id, participant):
        observed.append((channel_id, participant, session.get(UserBlock, ("owner", "member")) is None))
        if participant == "member":
            raise HTTPException(503, "Matrix unavailable")
    monkeypatch.setattr(direct_messages, "remove_secure_member", revoke)
    failed = client.post("/direct-messages/blocks/member", headers=headers["owner"])
    assert failed.status_code == 503
    assert observed == [(channel, "owner", True), (channel, "member", True)]
    with Session(engine) as session:
        assert session.get(UserBlock, ("owner", "member")) is None
    monkeypatch.setattr(direct_messages, "remove_secure_member", Mock())
    assert client.post("/direct-messages/blocks/member", headers=headers["owner"]).status_code == 200
    with Session(engine) as session:
        assert session.get(UserBlock, ("owner", "member")) is not None


def test_unblock_remote_failure_reports_closed_room_pending_retry(workspace_team, monkeypatch):
    client, _, headers = workspace_team
    from app.api import direct_messages
    open_dm(workspace_team)
    assert client.post("/direct-messages/blocks/member", headers=headers["owner"]).status_code == 200
    monkeypatch.setattr(direct_messages, "sync_secure_membership", Mock(side_effect=HTTPException(503, "Matrix unavailable")))
    result = client.delete("/direct-messages/blocks/member", headers=headers["owner"])
    assert result.status_code == 200 and result.json()["secure_sync_pending"] is True
    assert client.get("/direct-messages/blocks", headers=headers["owner"]).json() == []


def test_block_cannot_be_bypassed_with_implicit_personal_group_invitation(workspace_team):
    client, _, headers = workspace_team
    # Both people have their own private home before joining a common team.
    for name in ("outsider", "newbie"):
        assert client.get("/workspaces", headers=headers[name]).status_code == 200
    invited = client.post("/channels", headers=headers["outsider"], json={"name": "Hello", "member_ids": ["newbie"]})
    assert invited.status_code == 200
    assert client.post("/direct-messages/blocks/outsider", headers=headers["newbie"]).status_code == 200
    bypass = client.post("/channels", headers=headers["outsider"], json={"name": "Unwanted follow-up", "member_ids": ["newbie"]})
    assert bypass.status_code == 403
    reciprocal = client.post("/channels", headers=headers["newbie"], json={"name": "Other direction", "member_ids": ["outsider"]})
    assert reciprocal.status_code == 403
    explicit = client.post("/channels", headers=headers["outsider"], json={"workspace_id": invited.json()["workspace_id"], "name": "Explicit bypass", "member_ids": ["newbie"]})
    assert explicit.status_code == 403


def test_retained_direct_pair_does_not_leak_removed_person_presence_to_workspace_guest(workspace_team):
    client, _, headers = workspace_team
    direct = open_dm(workspace_team, "guest", "member")
    assert direct.status_code == 200
    assert client.get("/presence/users/member", headers=headers["guest"]).status_code == 200
    assert client.delete("/workspaces/team/members/member", headers=headers["owner"]).status_code == 200
    # The direct pair is retained for history, but it is no longer a directory
    # or presence authorization after the other person's workspace removal.
    assert client.get("/presence/users/member", headers=headers["guest"]).status_code == 404
    assert "member" not in {row["user_id"] for row in client.get("/workspaces/team/members", headers=headers["guest"]).json()}
