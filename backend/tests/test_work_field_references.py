"""Explicit work references survive real encrypted revisions without grants."""
import pytest
from sqlmodel import Session, select

from app.api import decisions
from app.models.activity_feed import ActivityFeedCommentRevision, ActivityFeedRevision
from app.models.channel import ChannelMember
from app.models.collaboration import DecisionRevision
from app.models.project_tasks import ProjectTaskRevision
from app.models.secure_channel import SecureChannel
from app.services import project_tasks as task_service
from app.services.field_references import normalize_field_references
from tests.test_activity_feed import feed, fields as post_fields, write as write_post
from tests.test_collaboration_features import bearer, collaboration
from tests.test_message_references import references
from tests.test_project_tasks import PROJECT, TASK, board, headers as board_headers, project
from tests.test_workflows import flows, headers as feed_headers


def reference(text, label="@peer", *, kind="person", identifier="peer"):
    index = text.index(label)
    start = len(text[:index].encode("utf-16-le")) // 2
    return {"kind": kind, "id": identifier, "start": start,
            "end": start + len(label.encode("utf-16-le")) // 2}


def test_project_and_task_fields_preserve_exact_referenced_text_and_legacy_shape(board):
    title, description = "  @peer  ", "😀 @peer is reviewing this.  "
    refs = {"title": [reference(title)], "description": [reference(description)]}
    result = board.client.post("/channels/a/projects", headers=board_headers(board),
        json={"id": "referenced-project", "fields": {**PROJECT, "title": title,
            "description": description, "field_references": refs}})
    assert result.status_code == 200, result.text
    assert result.json()["fields"] == {**PROJECT, "title": title,
        "description": description, "field_references": refs}
    project(board)
    checklist = [{"id": "check", "title": "  Ask @peer  ", "done": False}]
    task_refs = {"description": [reference(description)],
                 "checklist:check": [reference(checklist[0]["title"])]}
    content = {**TASK, "description": description, "checklist": checklist, "field_references": task_refs}
    response = board.client.post("/channels/a/projects/project-a/tasks", headers=board_headers(board),
        json={"id": "referenced-task", "fields": content})
    assert response.status_code == 200, response.text
    assert response.json()["fields"] == content
    rows = board.client.get("/channels/a/projects/project-a/tasks", headers=board_headers(board, "peer")).json()["items"]
    assert rows[0]["fields"] == content
    legacy = board.client.post("/channels/a/projects", headers=board_headers(board),
        json={"id": "legacy", "fields": {**PROJECT, "title": "  Trim me  ", "field_references": {"title": []}}})
    assert legacy.status_code == 200 and legacy.json()["fields"] == {**PROJECT, "title": "Trim me"}
    with Session(board.engine) as session:
        stored = " ".join(row.encrypted_payload or "" for row in session.exec(select(ProjectTaskRevision)).all())
        assert "field_references" not in stored and description not in stored and "Ask @peer" not in stored


def test_long_task_description_reference_keeps_utf16_offsets_after_message_limit(board):
    project(board)
    text = "x" * 10010 + " @peer"
    refs = {"description": [reference(text)]}
    response = board.client.post("/channels/a/projects/project-a/tasks", headers=board_headers(board),
        json={"id": "long-text", "fields": {**TASK, "description": text, "field_references": refs}})
    assert response.status_code == 200, response.text
    assert response.json()["fields"]["field_references"] == refs


def test_all_six_reference_types_use_current_canonical_channel_resources(references):
    text = "@peer @design @brief @step @file @channel"
    targets = [("@peer", "person", "peer"), ("@design", "group", references.group["id"]),
               ("@brief", "document", "doc-a"), ("@step", "task", "task-a"),
               ("@file", "file", "file-a"), ("@channel", "channel", "a")]
    refs = {"description": [reference(text, label, kind=kind, identifier=identifier)
                            for label, kind, identifier in targets]}
    saved = references.client.post("/channels/a/projects", headers=board_headers(references),
        json={"id": "all-kinds", "fields": {**PROJECT, "description": text, "field_references": refs}})
    assert saved.status_code == 200, saved.text
    assert saved.json()["fields"]["field_references"] == refs
    assert references.client.get("/channels/b/projects", headers=board_headers(references)).status_code == 403


@pytest.mark.parametrize("refs", [
    {"title": [{"kind": "person", "id": "peer", "start": 1, "end": 7}]},  # Inside emoji.
    {"description": [{"kind": "person", "id": "peer", "start": 3, "end": 7}]},  # Wrong field.
    {"title": [{"kind": "person", "id": "peer", "start": 3, "end": 8, "label": "spoof"}]},
    {"title": [{"kind": "person", "id": "peer", "start": True, "end": 8}]},
    {"title": [{"kind": "person", "id": "peer", "start": 3, "end": 8}] * 2},
    {"unknown": []}, {"checklist:missing": []},
])
def test_project_reference_shapes_and_ranges_reject_without_persisting(board, refs):
    response = board.client.post("/channels/a/projects", headers=board_headers(board),
        json={"id": "invalid", "fields": {**PROJECT, "title": "😀 @peer", "field_references": refs}})
    assert response.status_code == 422, response.text
    with Session(board.engine) as session:
        assert not session.exec(select(ProjectTaskRevision)).all()


@pytest.mark.parametrize("kind,identifier", [("person", "foreign"), ("person", "outsider"),
    ("person", "blocked"), ("file", "file-b"), ("channel", "b"), ("task", "absent")])
def test_work_reference_targets_do_not_grant_access_or_disclose_foreign_metadata(board, kind, identifier):
    title = "A link to @item"
    response = board.client.post("/channels/a/projects", headers=board_headers(board),
        json={"id": "invalid", "fields": {**PROJECT, "title": title,
            "field_references": {"title": [reference(title, "@item", kind=kind, identifier=identifier)]}}})
    assert response.status_code == 404, response.text
    assert "Foreign" not in response.text and "file-b" not in response.text
    with Session(board.engine) as session:
        assert not session.exec(select(ProjectTaskRevision)).all()


def test_history_keeps_reference_identity_after_target_membership_is_removed(board):
    title = "Ask @peer"
    refs = {"title": [reference(title)]}
    created = board.client.post("/channels/a/projects", headers=board_headers(board),
        json={"id": "retained", "fields": {**PROJECT, "title": title, "field_references": refs}})
    assert created.status_code == 200
    with Session(board.engine) as session:
        session.delete(session.get(ChannelMember, ("a", "peer"))); session.commit()
    history = board.client.get("/channels/a/projects/retained/revisions", headers=board_headers(board))
    assert history.status_code == 200 and history.json()[0]["fields"]["field_references"] == refs
    changed = board.client.post("/channels/a/projects", headers=board_headers(board),
        json={"id": "retained", "expected_revision": 1, "archived": True,
              "fields": {**PROJECT, "title": title, "field_references": refs}})
    assert changed.status_code == 200 and changed.json()["archived"]
    edited = "Please ask @peer"
    denied = board.client.post("/channels/a/projects", headers=board_headers(board),
        json={"id": "retained", "expected_revision": 2,
              "fields": {**PROJECT, "title": edited, "field_references": {"title": [reference(edited)]}}})
    assert denied.status_code == 404
    copied = board.client.post("/channels/a/projects", headers=board_headers(board),
        json={"id": "copied", "fields": {**PROJECT, "title": title, "field_references": refs}})
    assert copied.status_code == 404
    assert board.client.get("/channels/a/projects/retained/revisions", headers=board_headers(board, "peer")).status_code == 403


def test_task_status_and_checklist_changes_retain_only_unchanged_historical_refs(board):
    project(board)
    title = "Ask @peer"
    content = {**TASK, "title": title, "assignee_user_id": None,
               "checklist": [{"id": "ask", "title": title, "done": False}],
               "field_references": {"title": [reference(title)], "checklist:ask": [reference(title)]}}
    path = "/channels/a/projects/project-a/tasks"
    assert board.client.post(path, headers=board_headers(board), json={"id": "task", "fields": content}).status_code == 200
    with Session(board.engine) as session:
        session.delete(session.get(ChannelMember, ("a", "peer"))); session.commit()
    updated = {**content, "status": "done", "checklist": [{"id": "ask", "title": title, "done": True}]}
    saved = board.client.post(path, headers=board_headers(board),
        json={"id": "task", "expected_revision": 1, "fields": updated})
    assert saved.status_code == 200 and saved.json()["fields"] == updated
    forged = {**updated, "description": title,
              "field_references": {**updated["field_references"], "description": [reference(title)]}}
    denied = board.client.post(path, headers=board_headers(board),
        json={"id": "task", "expected_revision": 2, "fields": forged})
    assert denied.status_code == 404


def test_feed_post_comment_and_poll_reference_ids_round_trip_with_real_idempotency(feed):
    client, engine, tokens = feed
    content = post_fields(poll=True)
    content["title"] = "  @member announcement  "
    content["body"] = "😀 @member body"
    content["poll"]["question"] = "Ask @member"
    content["poll"]["options"][0]["label"] = "With @member"
    refs = {"title": [reference(content["title"], "@member", identifier="member")],
        "body": [reference(content["body"], "@member", identifier="member")],
        "poll.question": [reference(content["poll"]["question"], "@member", identifier="member")],
        "poll.option:" + "c" * 32: [reference(content["poll"]["options"][0]["label"], "@member", identifier="member")]}
    content["field_references"] = refs
    for _ in range(2):
        created = write_post(feed, content=content)
        assert created.status_code == 201, created.text
        assert created.json()["revision"] == 1 and created.json()["fields"] == content
    text = "😀 Thanks @owner"
    comment = {"body": text, "field_references": {"body": [reference(text, "@owner", identifier="owner")]}}
    for _ in range(2):
        response = client.post("/channels/channel-a/activity-feed/post/comments", headers=feed_headers(tokens, "member"),
            json={"id": "referenced-comment", "fields": comment})
        assert response.status_code == 201, response.text
        assert response.json()["revision"] == 1 and response.json()["fields"] == comment
    rows = client.get("/channels/channel-a/activity-feed/post/comments/referenced-comment/revisions", headers=feed_headers(tokens)).json()
    assert rows[0]["fields"] == comment
    with Session(engine) as session:
        posts = session.exec(select(ActivityFeedRevision)).all()
        comments = session.exec(select(ActivityFeedCommentRevision)).all()
        assert len(posts) == len(comments) == 1
        assert all("field_references" not in row.encrypted_payload and "@member" not in row.encrypted_payload for row in posts + comments)


def test_feed_phantom_poll_and_foreign_reference_fail_without_persisting(feed):
    bad = {**post_fields(), "field_references": {"poll.option:" + "c" * 32: []}}
    assert write_post(feed, content=bad).status_code == 422
    text = "Ask @outsider"
    bad = {**post_fields(), "body": text,
           "field_references": {"body": [reference(text, "@outsider", identifier="outsider")]}}
    assert write_post(feed, content=bad).status_code == 404


def test_feed_post_and_comment_archive_preserve_unchanged_refs_after_revocation(feed):
    client, engine, tokens = feed
    text = "Ask @member"
    refs = {"body": [reference(text, "@member", identifier="member")]}
    content = {**post_fields(), "body": text, "field_references": refs}
    assert write_post(feed, content=content).status_code == 201
    comment = {"body": text, "field_references": refs}
    comment_path = "/channels/channel-a/activity-feed/post/comments"
    assert client.post(comment_path, headers=feed_headers(tokens),
        json={"id": "comment", "fields": comment}).status_code == 201
    with Session(engine) as session:
        session.delete(session.get(ChannelMember, ("channel-a", "member"))); session.commit()
    saved = client.post(comment_path, headers=feed_headers(tokens),
        json={"id": "comment", "expected_revision": 1, "archived": True, "fields": comment})
    assert saved.status_code == 201 and saved.json()["fields"] == comment
    saved = write_post(feed, revision=1, archived=True, content=content)
    assert saved.status_code == 201 and saved.json()["fields"] == content
    assert write_post(feed, identifier="copy", content=content).status_code == 404
    edited = "Now ask @member"
    changed = {**content, "body": edited,
               "field_references": {"body": [reference(edited, "@member", identifier="member")]}}
    assert write_post(feed, revision=2, content=changed).status_code == 404


def test_decision_references_preserve_exact_text_in_immutable_encrypted_revisions(collaboration):
    client = collaboration.client
    text = "  😀 @member decision  "
    refs = {"title": [reference(text, "@member", identifier="member")]}
    payload = {"title": text, "content": "The actual choice.", "field_references": refs}
    created = client.post("/channels/private/decisions", headers=bearer(), json=payload)
    assert created.status_code == 200, created.text
    assert created.json()["title"] == text and created.json()["field_references"] == refs
    path = "/decisions/" + created.json()["id"]
    revised = client.post(path + "/revisions", headers=bearer(), json={**payload, "content": "Reviewed choice.", "expected_revision": 1})
    assert revised.status_code == 200 and revised.json()["field_references"] == refs
    history = client.get(path + "/revisions", headers=bearer("member")).json()
    assert [row["revision"] for row in history] == [2, 1]
    assert all(row["field_references"] == refs for row in history)
    assert client.get(path, headers=bearer("outsider")).status_code == 403
    with Session(collaboration.engine) as session:
        rows = session.exec(select(DecisionRevision)).all()
        assert len(rows) == 2 and all("field_references" not in row.encrypted_payload and text not in row.encrypted_payload for row in rows)


def test_decision_status_retains_exact_refs_but_new_text_checks_target_again(collaboration):
    client = collaboration.client
    text = "Ask @member"
    payload = {"title": text, "content": "The choice.",
               "field_references": {"title": [reference(text, "@member", identifier="member")]}}
    created = client.post("/channels/private/decisions", headers=bearer(), json=payload)
    assert created.status_code == 200, created.text
    path = "/decisions/" + created.json()["id"] + "/revisions"
    with Session(collaboration.engine) as session:
        session.delete(session.get(ChannelMember, ("private", "member"))); session.commit()
    saved = client.post(path, headers=bearer(), json={**payload, "status": "accepted", "expected_revision": 1})
    assert saved.status_code == 200 and saved.json()["field_references"] == payload["field_references"]
    edited = "Now ask @member"
    denied = client.post(path, headers=bearer(), json={**payload, "title": edited, "expected_revision": 2,
        "field_references": {"title": [reference(edited, "@member", identifier="member")]}})
    assert denied.status_code == 404


def test_secure_decisions_and_task_envelopes_reject_plain_reference_sidecars(board, monkeypatch):
    board.client.app.include_router(decisions.router)
    with Session(board.engine) as session:
        session.add(SecureChannel(channel_id="a", created_by="owner", status="preparing")); session.commit()
    monkeypatch.setattr("app.services.matrix_bridge.validate_secure_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(task_service, "validate_secure_event", lambda *args, **kwargs: {})
    opaque = board.client.post("/channels/a/decisions", headers=board_headers(board), json={"matrix_event_id": "$opaque"})
    assert opaque.status_code == 200 and opaque.json()["content_mode"] == "matrix"
    assert "field_references" not in opaque.json() and "title" not in opaque.json()
    for refs in ({}, {"title": []}):
        response = board.client.post("/channels/a/decisions", headers=board_headers(board),
            json={"matrix_event_id": "$opaque", "field_references": refs})
        assert response.status_code == 422
    response = board.client.post("/channels/a/projects", headers=board_headers(board),
        json={"id": "secure", "matrix_event_id": "$opaque", "field_references": {}})
    assert response.status_code == 422
    with Session(board.engine) as session:
        row = session.exec(select(DecisionRevision)).one()
        assert row.encrypted_payload is None and row.matrix_event_id == "$opaque"


def test_field_reference_total_bound_and_word_boundaries():
    text = "@peer " * 26
    spans = [{"kind": "person", "id": "peer", "start": index * 6, "end": index * 6 + 5}
             for index in range(26)]
    with pytest.raises(ValueError):
        normalize_field_references({"title": text, "description": text},
                                   {"title": spans, "description": spans[:25]})
    for text, start, end in [("x@peer", 1, 6), ("@peerx", 0, 5), ("@pe\ner", 0, 6),
                             ("@pe\u2028er", 0, 6)]:
        with pytest.raises(ValueError):
            normalize_field_references({"body": text}, {"body": [{"kind": "person", "id": "peer", "start": start, "end": end}]})
    assert normalize_field_references({"title": "@peer", "description": "@peer"}, {"title": [], "description": []}) == {}
