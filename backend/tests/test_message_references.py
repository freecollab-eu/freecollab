"""Typed composer references stay bound to real current channel resources."""
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, select

from app.api import chat_productivity, mentions, messages, push, shared_documents, threads
from app.models.channel import Channel, ChannelMember
from app.models.chat_productivity import ScheduledMessage
from app.models.message import Message
from app.models.notification import Notification
from app.models.direct_message import UserBlock
from app.models.file import File
from app.models.secure_channel import SecureChannel
from app.models.shared_documents import SharedDocument
from app.models.workspace import WorkspaceMember
from app.models.user import User
from app.services.auth import revoke_token
from app.services.crypto import decrypt_message
from tests.test_project_tasks import board, headers, project, task, TASK


BASE = "/mentions/channels/a"


@pytest.fixture
def references(board):
    for router in (mentions.router, messages.router, threads.router, push.router,
                   shared_documents.router, chat_productivity.router):
        board.client.app.include_router(router)
    project(board)
    task(board, fields={**TASK, "file_ids": [], "decision_ids": []})
    for actor, channel, identifier, title in (
        ("owner", "a", "doc-a", "PRIVATE courtyard brief"),
        ("foreign", "b", "doc-b", "FOREIGN hidden brief"),
    ):
        result = board.client.post(f"/channels/{channel}/shared-documents", headers=headers(board, actor),
            json={"id": identifier, "update_id": "first", "update_base64": "AAA=", "title": title})
        assert result.status_code == 201, result.text
    result = board.client.post(BASE + "/groups", headers=headers(board),
        json={"name": "Design team", "member_ids": ["peer"]})
    assert result.status_code == 201, result.text
    return SimpleNamespace(**vars(board), group=result.json())


def reference(kind="person", identifier="peer", start=0, end=5):
    return {"kind": kind, "id": identifier, "start": start, "end": end}


def send(fixture, content="@peer", refs=None, actor="owner", **extra):
    return fixture.client.post("/messages/channels/a", headers=headers(fixture, actor),
        json={"content": content, "references": [reference()] if refs is None else refs, **extra})


def resolve(fixture, *targets, actor="owner", channel="a"):
    return fixture.client.post(f"/mentions/channels/{channel}/references/resolve", headers=headers(fixture, actor),
        json={"references": [{"kind": kind, "id": identifier} for kind, identifier in targets]})


def test_selected_person_is_not_name_parsed_and_utf16_span_round_trips(references):
    text = "😀 Hej @peer"
    span = reference(start=7, end=12)
    result = send(references, text, [span])
    assert result.status_code == 200, result.text
    message = result.json()
    assert message["references"] == [span] and message["mentions"] == ["peer"]
    for path in ("/messages/" + message["id"], "/messages/channels/a", "/messages/channels/a/sync?after=0"):
        response = references.client.get(path, headers=headers(references, "peer"))
        assert response.status_code == 200, response.text
        values = response.json()
        records = values["messages"] if isinstance(values, dict) and "messages" in values else values if isinstance(values, list) else [values]
        loaded = next(item for item in records if item["id"] == message["id"])
        assert loaded["content"] == text and loaded["references"] == [span]
    with Session(references.engine) as session:
        stored = session.get(Message, message["id"])
        assert stored.encrypted and text not in stored.content
        assert decrypt_message(stored.content, "a") == text
        table = SQLModel.metadata.tables["messagereference"]
        records = session.execute(select(table).where(table.c.message_id == message["id"])).mappings().all()
        assert len(records) == 1
        assert "label" not in records[0] and "content" not in records[0]
        assert "😀" not in json.dumps([dict(item) for item in records])
    unselected = send(references, "@peer without an explicit choice", [])
    assert unselected.status_code == 200 and unselected.json()["mentions"] == []


def test_suggestions_and_resolver_use_current_canonical_resources_without_foreign_titles(references):
    result = references.client.get(BASE + "/suggestions", headers=headers(references))
    assert result.status_code == 200 and "no-store" in result.headers.get("cache-control", "")
    items = result.json()
    wanted = {("person", "peer"), ("group", references.group["id"]), ("document", "doc-a"),
              ("task", "task-a"), ("file", "file-a"), ("channel", "a")}
    assert wanted <= {(item["kind"], item["id"]) for item in items}
    assert len(items) <= 50 and "FOREIGN" not in result.text and '"blocked"' not in result.text
    documents = references.client.get(BASE + "/suggestions?kinds=document&query=courtyard", headers=headers(references))
    assert [(item["kind"], item["id"]) for item in documents.json()] == [("document", "doc-a")]
    assert documents.json()[0]["label"] == "PRIVATE courtyard brief"
    response = resolve(references, *wanted)
    assert response.status_code == 200, response.text
    values = {(item["kind"], item["id"]): item for item in response.json()["items"]}
    assert all(values[target]["available"] is True for target in wanted)
    assert values[("task", "task-a")]["project_id"] == "project-a"
    assert values[("group", references.group["id"])]["user_ids"] == ["peer"]
    assert values[("document", "doc-a")]["label"] == "PRIVATE courtyard brief"


@pytest.mark.parametrize("kind,identifier", [
    ("person", "foreign"), ("person", "blocked"), ("document", "doc-b"),
    ("task", "missing-task"), ("file", "file-b"), ("channel", "b"),
    ("group", "missing-group"), ("document", "task-a"),
])
def test_missing_cross_scope_or_wrong_type_references_are_unavailable_and_cannot_be_sent(references, kind, identifier):
    resolved = resolve(references, (kind, identifier))
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["items"] == [{"kind": kind, "id": identifier, "available": False}]
    result = send(references, "@target", [reference(kind, identifier, end=7)])
    assert result.status_code == 404, result.text
    with Session(references.engine) as session:
        assert not session.exec(select(Message)).all()


@pytest.mark.parametrize("content,refs", [
    ("@peer", [reference(start=-1)]),
    ("@peer", [reference(end=0)]),
    ("@peer", [reference(end=6)]),
    ("hello", [reference()]),
    ("@😀", [reference(end=2)]),
    ("😀@peer", [reference(start=1, end=7)]),
    ("@peer", [reference(start=True)]),
    ("@peer", [reference(), reference()]),
    ("@peer", [{**reference(), "label": "forged target title"}]),
    ("@peer", [{**reference(), "kind": "arbitrary"}]),
])
def test_malformed_or_overlapping_spans_cannot_persist_routing_or_notify(references, content, refs):
    result = send(references, content, refs)
    assert result.status_code == 422, result.text
    with Session(references.engine) as session:
        assert not session.exec(select(Message)).all()
        assert not session.exec(select(Notification)).all()


def test_native_group_mentions_expand_current_members_once_and_merge_legacy_ids(references):
    text = "@Design team and @peer"
    refs = [reference("group", references.group["id"], end=12), reference(start=17, end=22)]
    result = send(references, text, refs, mentions=["peer"])
    assert result.status_code == 200, result.text
    assert result.json()["references"] == refs and result.json()["mentions"] == ["peer"]
    with Session(references.engine) as session:
        notifications = session.exec(select(Notification).where(Notification.user_id == "peer", Notification.type == "mention")).all()
        assert len(notifications) == 1 and notifications[0].entity_id == result.json()["id"]


def test_edit_without_reference_metadata_clears_old_spans_and_explicit_refs_follow_new_content(references):
    created = send(references).json()
    path = "/messages/" + created["id"]
    edited = references.client.put(path, headers=headers(references), json={"content": "The same private thought, now without a mention."})
    assert edited.status_code == 200 and edited.json()["references"] == []
    assert edited.json()["mentions"] == []
    loaded = references.client.get(path, headers=headers(references)).json()
    assert loaded["references"] == []
    revised = references.client.put(path, headers=headers(references), json={"content": "See @brief", "references": [reference("document", "doc-a", start=4, end=10)]})
    assert revised.status_code == 200, revised.text
    assert revised.json()["references"] == [reference("document", "doc-a", start=4, end=10)]
    assert references.client.delete(path, headers=headers(references)).status_code == 200
    assert references.client.get(path, headers=headers(references)).json()["references"] == []


def test_native_thread_and_offline_replay_keep_exact_typed_references(references):
    parent = send(references, "Original parent", []).json()
    data = {"content": "@peer", "references": [reference()]}
    reply = references.client.post(f'/threads/messages/{parent["id"]}/reply', headers=headers(references), json=data)
    assert reply.status_code == 200 and reply.json()["references"] == data["references"], reply.text
    thread = references.client.get(f'/threads/messages/{parent["id"]}', headers=headers(references)).json()
    assert thread["replies"][0]["references"] == data["references"]
    queued = {**data, "client_id": "typed-reference-offline-0001"}
    path = "/mobile/offline/channels/a/messages"
    first = references.client.post(path, headers=headers(references), json=queued)
    assert first.status_code == 200, first.text
    replay = references.client.post(path, headers=headers(references), json=queued)
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    assert replay.json()["id"] == first.json()["id"] and replay.json()["references"] == data["references"]
    assert references.client.post(path, headers=headers(references), json={**queued, "references": []}).status_code == 409


def test_scheduled_message_keeps_reference_metadata_until_actual_delivery(references):
    due = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
    result = references.client.post("/channels/a/scheduled-messages", headers=headers(references), json={
        "client_id": "typed-reference-schedule-0001", "content": "@peer", "references": [reference()], "due_at": due})
    assert result.status_code == 201, result.text
    identifier = result.json()["id"]
    from app.services.chat_productivity import run_pending
    with Session(references.engine) as session:
        assert session.get(Message, identifier) is None
        assert run_pending(session, now=datetime.utcnow() + timedelta(minutes=2)) == 1
        assert session.get(ScheduledMessage, identifier).status == "sent"
    response = references.client.get("/messages/" + identifier, headers=headers(references, "peer"))
    assert response.status_code == 200 and response.json()["references"] == [reference()]
    assert response.json()["mentions"] == ["peer"]


@pytest.mark.parametrize("change", ["replace", "delete"])
def test_scheduled_group_reference_uses_current_audience_or_cancels_if_removed(references, change):
    due = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
    span = reference("group", references.group["id"], end=12)
    response = references.client.post("/channels/a/scheduled-messages", headers=headers(references), json={
        "client_id": "typed-group-schedule-0001", "content": "@Design team", "references": [span], "due_at": due})
    assert response.status_code == 201, response.text
    identifier = response.json()["id"]
    group_path = BASE + "/groups/" + references.group["id"]
    if change == "replace":
        updated = references.client.patch(group_path, headers=headers(references), json={"expected_version": 1, "member_ids": ["owner"]})
        assert updated.status_code == 200, updated.text
    else:
        assert references.client.delete(group_path + "?expected_version=1", headers=headers(references)).status_code == 204
    from app.services.chat_productivity import run_pending
    with Session(references.engine) as session:
        # The queue keeps the typed group identity, not a stale copied audience.
        assert json.loads(session.get(ScheduledMessage, identifier).mentions_json) == []
        run_pending(session, now=datetime.utcnow() + timedelta(minutes=2))
        row = session.get(ScheduledMessage, identifier)
        if change == "replace":
            assert row.status == "sent"
            assert json.loads(session.get(Message, identifier).mentions) == ["owner"]
        else:
            assert row.status == "cancelled" and session.get(Message, identifier) is None
        assert not session.exec(select(Notification).where(Notification.user_id == "peer")).all()
        table = SQLModel.metadata.tables["scheduledmessagereference"]
        assert not session.execute(select(table).where(table.c.schedule_id == identifier)).all()


@pytest.mark.parametrize("actor", ["outsider", "foreign", "blocked"])
def test_reference_discovery_requires_actual_room_membership(references, actor):
    for suffix in ("/suggestions", "/groups"):
        assert references.client.get(BASE + suffix, headers=headers(references, actor)).status_code in {401, 403}
    assert resolve(references, ("document", "doc-a"), actor=actor).status_code in {401, 403}
    assert send(references, actor=actor).status_code in {401, 403}


def test_workspace_scoped_identity_cannot_query_or_resolve_another_workspace(references):
    assert references.client.get(BASE + "/suggestions", headers=headers(references, "scoped")).status_code == 200
    assert references.client.get("/mentions/channels/b/suggestions", headers=headers(references, "scoped")).status_code == 403
    assert resolve(references, ("channel", "b"), actor="scoped", channel="b").status_code == 403


def test_removed_people_and_archived_native_documents_cannot_be_rementioned(references):
    with Session(references.engine) as session:
        session.delete(session.get(ChannelMember, ("a", "peer")))
        doc = session.get(SharedDocument, "doc-a"); doc.archived = True; session.add(doc); session.commit()
    for kind, identifier in (("person", "peer"), ("group", references.group["id"]), ("document", "doc-a")):
        assert resolve(references, (kind, identifier)).json()["items"][0]["available"] is False
        assert send(references, "@target", [reference(kind, identifier, end=7)]).status_code == 404


def test_secure_channel_discovery_never_returns_server_decrypted_resource_titles(references):
    with Session(references.engine) as session:
        session.add(SecureChannel(channel_id="a", matrix_room_id="!references:test", created_by="owner", status="active")); session.commit()
    result = references.client.get(BASE + "/suggestions", headers=headers(references))
    assert result.status_code == 200, result.text
    assert {item["kind"] for item in result.json()} <= {"person", "group", "channel"}
    assert "PRIVATE courtyard brief" not in result.text and "Private next step" not in result.text
    assert resolve(references, ("document", "doc-a"), ("task", "task-a"), ("file", "file-a")).json()["items"] == [
        {"kind": kind, "id": identifier, "available": False}
        for kind, identifier in (("document", "doc-a"), ("task", "task-a"), ("file", "file-a"))]
    assert send(references).status_code == 409


def test_expanded_group_and_legacy_mentions_share_the_fifty_person_limit(references):
    identifiers = ["recipient-" + str(index) for index in range(50)]
    with Session(references.engine) as session:
        session.add_all([User(id=identifier, name=identifier, email=identifier + "@example.invalid", password_hash="unused") for identifier in identifiers])
        session.flush()
        session.add_all([WorkspaceMember(workspace_id="wa", user_id=identifier) for identifier in identifiers])
        session.add_all([ChannelMember(channel_id="a", user_id=identifier) for identifier in identifiers])
        session.commit()
    created = references.client.post(BASE + "/groups", headers=headers(references),
        json={"name": "Large audience", "member_ids": identifiers})
    assert created.status_code == 201, created.text
    result = send(references, "@Large audience", [reference("group", created.json()["id"], end=15)], mentions=["owner"])
    assert result.status_code == 422, result.text
    with Session(references.engine) as session:
        assert not session.exec(select(Message)).all()
        assert not session.exec(select(Notification)).all()


@pytest.mark.parametrize("change", ["logout", "blocked"])
def test_reference_message_rechecks_actor_after_actual_channel_write_fence(references, monkeypatch, change):
    original = messages.lock_channel_writes
    def changed(session, channel_id):
        original(session, channel_id)
        if change == "logout":
            revoke_token(references.tokens["owner"], session)
        else:
            user = session.get(User, "owner"); user.is_blocked = True; session.add(user); session.commit()
    monkeypatch.setattr(messages, "lock_channel_writes", changed)
    result = send(references)
    assert result.status_code == 401, result.text
    with Session(references.engine) as session:
        assert not session.exec(select(Message)).all()
        assert not session.execute(select(SQLModel.metadata.tables["messagereference"])).all()


@pytest.mark.parametrize("change", ["workspace", "dm_block"])
def test_authority_changed_during_resource_resolution_blocks_the_later_message_write(references, monkeypatch, change):
    from app.services import message_references as service
    if change == "dm_block":
        with Session(references.engine) as session:
            row = session.get(Channel, "a"); row.type = "direct"; session.add(row)
            session.delete(session.get(ChannelMember, ("a", "blocked"))); session.commit()
    original = service.resolve_target
    changed_once = False
    def changed(session, channel_id, actor_id, kind, identifier, **kwargs):
        nonlocal changed_once
        result = original(session, channel_id, actor_id, kind, identifier, **kwargs)
        if kind == "document" and result and not changed_once:
            changed_once = True
            if change == "workspace": session.delete(session.get(WorkspaceMember, ("wa", "owner")))
            else: session.add(UserBlock(blocker_id="owner", blocked_id="peer"))
            session.commit()
        return result
    monkeypatch.setattr(service, "resolve_target", changed)
    result = send(references, "@brief", [reference("document", "doc-a", end=6)])
    assert result.status_code == 403, result.text
    with Session(references.engine) as session:
        assert not session.exec(select(Message)).all()
        assert not session.execute(select(SQLModel.metadata.tables["messagereference"])).all()


def test_export_and_retention_plan_include_ids_only_reference_dependencies(references):
    from app.models.data_control import DataControlJob
    from app.services import data_control as service
    from app.services.account_security import seal_private
    posted = send(references, "@brief", [reference("document", "doc-a", end=6)])
    assert posted.status_code == 200, posted.text
    with Session(references.engine) as session:
        exported, _, _ = service.export_data(session, SimpleNamespace(workspace_id="wa", requested_by="owner"), {"channel_ids": ["a"]})
        refs = exported["messagereference"]
        assert refs == [{"message_id": posted.json()["id"], "start": 0, "end": 6, "kind": "document", "target_id": "doc-a"}]
        assert "PRIVATE" not in json.dumps(refs) and "encrypted_payload" not in json.dumps(refs)
        assert any(row["id"] == references.group["id"] for row in exported["mentiongroup"])
        assert {row["user_id"] for row in exported["mentiongroupmember"] if row["group_id"] == references.group["id"]} == {"peer"}
        # The board fixture's file-a is a metadata-only placeholder, not an
        # uploaded object-store identity. Keep it outside this message purge.
        file = session.get(File, "file-a"); file.created_at = datetime.utcnow() + timedelta(days=365); session.add(file); session.commit()
        plan = service.build_plan(session, "wa", "owner", ["a"], datetime.utcnow() + timedelta(days=1))
        assert plan["messagereference"] == refs
        identifier = "reference-erasure-job"
        request = {"channel_ids": ["a"]}
        job = DataControlJob(id=identifier, workspace_id="wa", requested_by="owner", token_hash="private-proof", auth_version=0,
            kind="purge", encrypted_request=seal_private(json.dumps(request), "data-job", identifier),
            expires_at=datetime.utcnow() + timedelta(days=1))
        session.add(job); session.commit()
        service.apply_plan(session, job, plan); session.commit()
        assert session.get(Message, posted.json()["id"]) is None
        assert not session.execute(select(SQLModel.metadata.tables["messagereference"])).all()
