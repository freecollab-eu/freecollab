"""Notification groups never change grants or retain stale recipients."""
import pytest
from sqlmodel import Session, select

from app.models.channel import ChannelMember
from app.models.notification import Notification
from app.models.secure_channel import SecureChannel
from app.models.workspace import WorkspaceMember
from tests.test_project_tasks import board, headers
from tests.test_message_references import BASE, reference, references, resolve, send


def create(fixture, name="Review team", member_ids=None, actor="owner", channel="a"):
    return fixture.client.post(f"/mentions/channels/{channel}/groups", headers=headers(fixture, actor),
        json={"name": name, "member_ids": ["owner", "peer"] if member_ids is None else member_ids})


def test_groups_are_durable_current_channel_metadata_with_compare_and_swap(references):
    response = create(references)
    assert response.status_code == 201, response.text
    group = response.json()
    assert group["channel_id"] == "a" and group["name"] == "Review team" and group["version"] == 1
    assert sorted(group["member_ids"]) == ["owner", "peer"]
    listed = references.client.get(BASE + "/groups", headers=headers(references, "peer"))
    assert listed.status_code == 200 and "no-store" in listed.headers.get("cache-control", "")
    assert any(item["id"] == group["id"] for item in listed.json())
    path = BASE + "/groups/" + group["id"]
    change = {"expected_version": 1, "name": "Current review team", "member_ids": ["peer"]}
    updated = references.client.patch(path, headers=headers(references), json=change)
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 2 and updated.json()["member_ids"] == ["peer"]
    assert references.client.patch(path, headers=headers(references), json=change).status_code == 409
    assert references.client.delete(path + "?expected_version=1", headers=headers(references)).status_code == 409
    assert references.client.delete(path + "?expected_version=2", headers=headers(references)).status_code == 204
    assert resolve(references, ("group", group["id"])).json()["items"][0]["available"] is False


def test_normalized_duplicate_group_names_conflict_without_replacing_members(references):
    first = create(references).json()
    duplicate = create(references, name="  review TEAM  ", member_ids=["owner"])
    assert duplicate.status_code == 409, duplicate.text
    target = BASE + "/groups/" + references.group["id"]
    for change in ({"expected_version": 1, "name": "REVIEW TEAM"},
                   {"expected_version": 1, "name": "REVIEW TEAM", "member_ids": ["owner"]}):
        response = references.client.patch(target, headers=headers(references), json=change)
        assert response.status_code == 409, response.text
    listed = references.client.get(BASE + "/groups", headers=headers(references)).json()
    by_id = {item["id"]: item for item in listed}
    assert by_id[first["id"]]["member_ids"] == ["owner", "peer"]
    assert by_id[references.group["id"]]["member_ids"] == ["peer"]
    assert by_id[references.group["id"]]["version"] == 1


@pytest.mark.parametrize("actor", ["peer", "outsider", "foreign", "blocked"])
def test_only_current_channel_administrator_can_mutate_group_directory(references, actor):
    assert create(references, actor=actor).status_code in {401, 403}
    path = BASE + "/groups/" + references.group["id"]
    assert references.client.patch(path, headers=headers(references, actor),
        json={"expected_version": 1, "name": "Forged audience"}).status_code in {401, 403}
    assert references.client.delete(path + "?expected_version=1", headers=headers(references, actor)).status_code in {401, 403}
    assert resolve(references, ("group", references.group["id"])).json()["items"][0]["user_ids"] == ["peer"]


@pytest.mark.parametrize("members", [["foreign"], ["outsider"], ["blocked"], ["missing"]])
def test_group_members_must_be_real_active_current_channel_members(references, members):
    result = create(references, member_ids=members)
    assert result.status_code == 400, result.text
    path = BASE + "/groups/" + references.group["id"]
    assert references.client.patch(path, headers=headers(references),
        json={"expected_version": 1, "member_ids": members}).status_code == 400
    assert resolve(references, ("group", references.group["id"])).json()["items"][0]["user_ids"] == ["peer"]
    with Session(references.engine) as session:
        assert session.get(ChannelMember, ("a", members[0])) is None or members[0] == "blocked"


def test_group_changes_do_not_grant_channel_or_workspace_membership(references):
    created = create(references)
    assert created.status_code == 201
    with Session(references.engine) as session:
        assert {row.user_id for row in session.exec(select(ChannelMember).where(ChannelMember.channel_id == "a")).all()} == {"owner", "peer", "blocked"}
        assert {row.user_id for row in session.exec(select(WorkspaceMember).where(WorkspaceMember.workspace_id == "wa")).all()} == {"owner", "peer", "outsider", "blocked"}


def test_removed_member_is_filtered_from_group_resolution_and_actual_notifications(references):
    group = create(references).json()
    with Session(references.engine) as session:
        session.delete(session.get(ChannelMember, ("a", "peer"))); session.commit()
    response = resolve(references, ("group", group["id"]))
    assert response.status_code == 200 and response.json()["items"][0]["user_ids"] == ["owner"]
    posted = send(references, "@Review team", [reference("group", group["id"], end=12)])
    assert posted.status_code == 200, posted.text
    assert posted.json()["mentions"] == ["owner"]
    with Session(references.engine) as session:
        assert not session.exec(select(Notification).where(Notification.user_id == "peer")).all()


def test_foreign_group_identity_is_not_resolved_or_mutated_through_another_channel(references):
    foreign = create(references, name="FOREIGN directory name", member_ids=["foreign"], actor="foreign", channel="b")
    assert foreign.status_code == 201, foreign.text
    identifier = foreign.json()["id"]
    resolved = resolve(references, ("group", identifier))
    assert resolved.json()["items"] == [{"kind": "group", "id": identifier, "available": False}]
    assert "FOREIGN" not in resolved.text
    path = BASE + "/groups/" + identifier
    assert references.client.patch(path, headers=headers(references), json={"expected_version": 1, "name": "Wrong home"}).status_code == 404
    assert references.client.delete(path + "?expected_version=1", headers=headers(references)).status_code == 404
    assert send(references, "@foreign", [reference("group", identifier, end=8)]).status_code == 404


def test_groups_in_secure_channels_are_directory_metadata_and_never_plaintext_message_fallback(references):
    with Session(references.engine) as session:
        session.add(SecureChannel(channel_id="a", matrix_room_id="!references:test", created_by="owner", status="active")); session.commit()
    result = create(references)
    assert result.status_code == 201, result.text
    assert resolve(references, ("group", result.json()["id"])).json()["items"][0]["user_ids"] == ["owner", "peer"]
    assert send(references, "@Review team", [reference("group", result.json()["id"], end=12)]).status_code == 409


def test_group_creation_rechecks_workspace_authority_after_members_are_resolved(references, monkeypatch):
    from app.services import message_references as service
    original = service.group_members
    changed_once = False
    def changed(session, channel_id, ids):
        nonlocal changed_once
        result = original(session, channel_id, ids)
        if not changed_once:
            changed_once = True
            session.delete(session.get(WorkspaceMember, ("wa", "owner"))); session.commit()
        return result
    monkeypatch.setattr(service, "group_members", changed)
    result = create(references, name="Late unauthorized group")
    assert result.status_code == 403, result.text
    with Session(references.engine) as session:
        group_table = service.MentionGroup.__table__
        rows = session.execute(select(group_table).where(group_table.c.name == "Late unauthorized group")).all()
        assert rows == []
