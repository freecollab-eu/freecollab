"""Native conversation links resolve real content under current product grants."""
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import io
import json
from types import SimpleNamespace
import zipfile

import pytest
from sqlmodel import Session, select

from app import main as _register_models  # noqa: F401
from app.api import conversation_work, decisions, messages, shared_documents, threads
from app.models.channel import Channel, ChannelMember
from app.models.collaboration import Decision, DecisionRevision
from app.models.connect import ConnectConnection, ConnectGrant
from app.models.conversation_work import ConversationWorkReference
from app.models.direct_message import UserBlock
from app.models.message import Message
from app.models.project_tasks import ProjectBoard, ProjectTaskRevision, TaskCard
from app.models.secure_channel import SecureChannel
from app.models.shared_documents import SharedDocument, SharedDocumentUpdate
from app.models.user import User
from app.models.workspace import WorkspaceChannel, WorkspaceMember
from app.services import conversation_work as service, data_control as data_service
from app.services.auth import create_token, revoke_token
from tests.test_project_tasks import board, headers, project, task, TASK
from tests.test_data_controls import (
    controls, enable, headers as control_headers, OLD, post, preview, queue_purge,
)


PATH = "/channels/a/conversation-work"


@pytest.fixture
def work(board):
    for router in (conversation_work.router, decisions.router, messages.router,
                   shared_documents.router, threads.router):
        board.client.app.include_router(router)
    root = board.client.post("/messages/channels/a", headers=headers(board),
                             json={"content": "PRIVATE source material for the courtyard"})
    assert root.status_code == 200, root.text
    reply = board.client.post("/threads/messages/" + root.json()["id"] + "/reply",
                              headers=headers(board, "peer"),
                              json={"content": "PRIVATE thread material"})
    assert reply.status_code == 200, reply.text
    document = board.client.post("/channels/a/shared-documents", headers=headers(board),
        json={"id": "document-a", "update_id": "first", "update_base64": "AAA=", "title": "PRIVATE shared brief"})
    assert document.status_code == 201, document.text
    project(board)
    task(board, fields={**TASK, "file_ids": [], "decision_ids": []})
    decision = board.client.post("/channels/a/decisions", headers=headers(board),
        json={"title": "PRIVATE agreement", "content": "PRIVATE agreed material", "status": "proposed"})
    assert decision.status_code == 200, decision.text
    foreign = board.client.post("/messages/channels/b", headers=headers(board, "foreign"),
                                json={"content": "FOREIGN private source"})
    assert foreign.status_code == 200, foreign.text
    foreign_document = board.client.post("/channels/b/shared-documents", headers=headers(board, "foreign"),
        json={"id": "document-b", "update_id": "first", "update_base64": "AAA=", "title": "FOREIGN private brief"})
    assert foreign_document.status_code == 201, foreign_document.text
    yield SimpleNamespace(**vars(board), source=root.json(), reply=reply.json(), foreign_source=foreign.json(),
                          targets={"document": "document-a", "task": "task-a", "decision": decision.json()["id"]})


def save(work, kind="document", *, source=None, resource=None, actor="owner", channel="a", **extra):
    return work.client.post("/channels/" + channel + "/conversation-work", headers=headers(work, actor), json={
        "source_message_id": source or work.source["id"], "resource_kind": kind,
        "resource_id": resource or work.targets[kind], **extra,
    })


def listing(work, *, actor="owner", channel="a", query=""):
    return work.client.get("/channels/" + channel + "/conversation-work" + query, headers=headers(work, actor))


def test_links_use_real_native_targets_and_expose_only_routing_metadata(work):
    saved = [save(work, kind) for kind in ("document", "task", "decision")]
    assert [response.status_code for response in saved] == [201, 201, 201]
    records = listing(work)
    assert records.status_code == 200 and records.headers["cache-control"].endswith("no-store")
    assert records.json()["next_offset"] is None
    assert {item["resource_kind"] for item in records.json()["items"]} == {"document", "task", "decision"}
    for item in records.json()["items"]:
        assert set(item) == {"id", "channel_id", "source_message_id", "resource_kind", "resource_id",
                             "project_id", "created_by", "created_at"}
        assert item["channel_id"] == "a" and item["source_message_id"] == work.source["id"]
        assert item["resource_id"] == work.targets[item["resource_kind"]]
        assert item["project_id"] == ("project-a" if item["resource_kind"] == "task" else None)
    assert "PRIVATE" not in records.text and "FOREIGN" not in records.text and "encrypted_payload" not in records.text
    with Session(work.engine) as session:
        assert len(session.exec(select(ConversationWorkReference)).all()) == 3
        assert session.get(SharedDocument, "document-a").update_count == 1
        assert session.get(TaskCard, "task-a").revision == 1
        assert len(session.exec(select(DecisionRevision).where(DecisionRevision.decision_id == work.targets["decision"])).all()) == 1


def test_duplicate_link_is_idempotent_across_members_and_preserves_original_authorship(work):
    first = save(work)
    second = save(work, actor="peer")
    assert first.status_code == 201 and second.status_code == 200
    assert second.json() == first.json() and second.json()["created_by"] == "owner"
    assert len(listing(work).json()["items"]) == 1


def test_simultaneous_retries_create_one_shared_reference(work):
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda actor: save(work, actor=actor), ["owner", "peer", "owner", "peer"]))
    assert sorted(response.status_code for response in responses) == [200, 200, 200, 201]
    assert len({response.json()["id"] for response in responses}) == 1
    assert len(listing(work).json()["items"]) == 1


def test_only_creator_or_current_channel_administrator_can_remove_a_link(work):
    first = save(work, actor="peer").json()
    path = PATH + "/" + first["id"]
    assert work.client.delete(path, headers=headers(work, "outsider")).status_code == 403
    with Session(work.engine) as session:
        session.add(User(id="another", name="Another", email="another@example.invalid", password_hash="unused"))
        session.flush()
        session.add(WorkspaceMember(workspace_id="wa", user_id="another"))
        session.add(ChannelMember(channel_id="a", user_id="another"))
        session.commit()
        work.tokens["another"] = create_token("another", session)
    assert work.client.delete(path, headers=headers(work, "another")).status_code == 403
    assert work.client.delete(path, headers=headers(work, "peer")).status_code == 204
    second = save(work, actor="peer").json()
    assert work.client.delete(PATH + "/" + second["id"], headers=headers(work)).status_code == 204
    assert listing(work).json()["items"] == []


def test_foreign_sources_targets_and_wrong_target_kinds_never_create_routing_rows(work):
    attempts = [save(work, source=work.foreign_source["id"]), save(work, resource="document-b"),
                save(work, "task", resource="document-a"), save(work, "decision", resource="document-a"),
                save(work, resource="missing"), save(work, source="missing")]
    assert all(response.status_code == 404 for response in attempts), [(r.status_code, r.text) for r in attempts]
    assert listing(work, channel="b", actor="foreign").json()["items"] == []
    with Session(work.engine) as session:
        assert not session.exec(select(ConversationWorkReference)).all()


@pytest.mark.parametrize("actor", ["outsider", "foreign", "blocked"])
def test_workspace_administration_or_another_account_does_not_grant_private_room_access(work, actor):
    assert save(work, actor=actor).status_code in {401, 403}
    assert listing(work, actor=actor).status_code in {401, 403}
    assert work.client.get(PATH).status_code == 401


@pytest.mark.parametrize("removed", ["channel", "workspace"])
def test_removed_membership_cannot_read_or_mutate_previous_links(work, removed):
    first = save(work, actor="peer").json()
    with Session(work.engine) as session:
        member = session.get(ChannelMember, ("a", "peer")) if removed == "channel" else session.get(WorkspaceMember, ("wa", "peer"))
        session.delete(member)
        session.commit()
    assert listing(work, actor="peer").status_code == 403
    assert save(work, actor="peer").status_code == 403
    assert work.client.delete(PATH + "/" + first["id"], headers=headers(work, "peer")).status_code == 403


def test_personal_direct_message_block_preserves_history_but_stops_new_outbound_changes(work):
    saved = save(work).json()
    with Session(work.engine) as session:
        room = session.get(Channel, "a"); room.type = "direct"; session.add(room)
        session.delete(session.get(ChannelMember, ("a", "blocked")))
        session.add(UserBlock(blocker_id="owner", blocked_id="peer")); session.commit()
    result = listing(work, actor="peer")
    assert result.status_code == 200 and result.json()["items"][0]["id"] == saved["id"]
    assert save(work, "task", actor="peer").status_code == 403
    assert work.client.delete(PATH + "/" + saved["id"], headers=headers(work)).status_code == 403


def test_scoped_sign_in_stays_in_its_workspace_and_guest_uses_only_assigned_channel(work):
    assert save(work, actor="scoped").status_code == 201
    assert listing(work, actor="scoped").status_code == 200
    assert listing(work, actor="scoped", channel="b").status_code == 403
    with Session(work.engine) as session:
        session.add(User(id="guest", name="Guest", email="guest@example.invalid", password_hash="unused")); session.flush()
        session.add(WorkspaceMember(workspace_id="wa", user_id="guest", role="guest"))
        session.add(ChannelMember(channel_id="a", user_id="guest")); session.commit()
        work.tokens["guest"] = create_token("guest", session)
    assert save(work, "task", actor="guest").status_code == 201
    assert listing(work, actor="guest").status_code == 200
    assert listing(work, actor="guest", channel="b").status_code == 403


@pytest.mark.parametrize("source", ["root", "reply"])
def test_deleted_source_or_deleted_reply_parent_hides_links_and_refuses_new_links(work, source):
    identifier = work.source["id"] if source == "root" else work.reply["id"]
    saved = save(work, source=identifier).json()
    deleted = work.client.delete("/messages/" + work.source["id"], headers=headers(work))
    assert deleted.status_code == 200, deleted.text
    assert listing(work).json()["items"] == []
    assert save(work, "task", source=identifier).status_code == 404
    assert work.client.delete(PATH + "/" + saved["id"], headers=headers(work)).status_code == 204


@pytest.mark.parametrize("kind", ["document", "task", "task_project"])
def test_archived_canonical_content_is_filtered_and_cannot_accept_a_new_link(work, kind):
    resource_kind = "task" if kind == "task_project" else kind
    saved = save(work, resource_kind).json()
    with Session(work.engine) as session:
        model, identifier = (ProjectBoard, "project-a") if kind == "task_project" else (
            (SharedDocument, "document-a") if kind == "document" else (TaskCard, "task-a"))
        record = session.get(model, identifier); record.archived = True; session.add(record); session.commit()
    assert listing(work).json()["items"] == []
    assert save(work, resource_kind).status_code == 409
    assert work.client.delete(PATH + "/" + saved["id"], headers=headers(work)).status_code == 204


@pytest.mark.parametrize("kind", ["document", "task", "decision"])
def test_unverified_or_matrix_only_target_content_has_no_native_reference_fallback(work, kind):
    with Session(work.engine) as session:
        if kind == "document":
            row = session.get(SharedDocument, "document-a")
            row.summary_encrypted_payload = None; row.summary_event_id = "$opaque-summary"
        elif kind == "task":
            row = session.exec(select(ProjectTaskRevision).where(ProjectTaskRevision.kind == "task", ProjectTaskRevision.item_id == "task-a")).one()
            row.content_mode = "matrix"; row.encrypted_payload = None; row.matrix_event_id = "$opaque-task"
        else:
            row = session.exec(select(DecisionRevision).where(DecisionRevision.decision_id == work.targets[kind])).one()
            row.content_mode = "matrix"; row.encrypted_payload = None; row.matrix_event_id = "$opaque-decision"
        session.add(row); session.commit()
    response = save(work, kind)
    assert response.status_code == 409, response.text
    assert listing(work).json()["items"] == []


@pytest.mark.parametrize("kind", ["document", "task", "decision"])
def test_changed_or_corrupt_native_payloads_cannot_be_misrepresented_as_available_work(work, kind):
    assert save(work, kind).status_code == 201
    with Session(work.engine) as session:
        if kind == "document":
            row = session.get(SharedDocument, "document-a"); row.summary_encrypted_payload = "corrupt-native-payload"
        elif kind == "task":
            row = session.exec(select(ProjectTaskRevision).where(ProjectTaskRevision.kind == "task", ProjectTaskRevision.item_id == "task-a")).one()
            row.encrypted_payload = "corrupt-native-payload"
        else:
            row = session.exec(select(DecisionRevision).where(DecisionRevision.decision_id == work.targets[kind])).one()
            row.encrypted_payload = "corrupt-native-payload"
        session.add(row); session.commit()
    assert listing(work).json()["items"] == []
    assert save(work, kind).status_code == 409


@pytest.mark.parametrize("kind", ["document", "task", "decision"])
def test_target_disappearing_between_scalar_lookup_and_orm_hydration_is_unavailable_not_a_server_error(work, monkeypatch, kind):
    assert save(work, kind).status_code == 201
    target_model = {"document": SharedDocument, "task": TaskCard, "decision": Decision}[kind]
    identifier = work.targets[kind]
    original = Session.get
    def disappeared(session, model, identity, *args, **kwargs):
        # A physical retention purge can commit after the scalar query found
        # the target. Keep account/grant lookups real; only that later target
        # hydration observes the concurrent disappearance.
        if model is target_model and identity == identifier:
            return None
        return original(session, model, identity, *args, **kwargs)
    monkeypatch.setattr(Session, "get", disappeared)
    result = listing(work)
    assert result.status_code == 200 and result.json()["items"] == []
    result = save(work, kind)
    assert result.status_code in {404, 409}, result.text


@pytest.mark.parametrize("kind", ["document", "task", "decision"])
def test_placeholder_rows_without_actual_revision_cannot_be_linked(work, kind):
    with Session(work.engine) as session:
        if kind == "document": row = SharedDocument(id="placeholder", channel_id="a", created_by="owner")
        elif kind == "task": row = TaskCard(id="placeholder", project_id="project-a", channel_id="a", created_by="owner")
        else: row = Decision(id="placeholder", channel_id="a", created_by="owner")
        session.add(row); session.commit()
    assert save(work, kind, resource="placeholder").status_code in {404, 409}
    assert listing(work).json()["items"] == []


@pytest.mark.parametrize("mode", ["preparing", "active", "matrix", "legacy_key", "connect"])
def test_encrypted_or_shared_connect_rooms_never_store_native_links(work, mode):
    with Session(work.engine) as session:
        if mode == "legacy_key":
            member = session.get(ChannelMember, ("a", "owner")); member.encrypted_channel_key = "private-legacy-key"; session.add(member)
        elif mode == "connect":
            connection = ConnectConnection(id="connect-a", channel_id="a", source_workspace_id="wa", target_workspace_id="wb",
                created_by="owner", accepted_by="foreign", token_hash="synthetic-connect", status="active",
                expires_at=datetime.utcnow() + timedelta(days=1), accepted_at=datetime.utcnow())
            session.add(connection); session.flush()
            session.add(ConnectGrant(connection_id=connection.id, user_id="owner", workspace_id="wa", created_by="owner"))
            session.add(SecureChannel(channel_id="a", created_by="owner", matrix_room_id="!connect:example.invalid", status="active"))
        else:
            session.add(SecureChannel(channel_id="a", created_by="owner", status=mode if mode != "matrix" else "active",
                                      matrix_room_id="!private:example.invalid" if mode == "matrix" else None))
        session.commit()
    response = save(work)
    assert response.status_code == 409, response.text
    response = listing(work)
    assert response.status_code == 409, response.text
    with Session(work.engine) as session:
        assert not session.exec(select(ConversationWorkReference)).all()


def test_filtering_precedes_pagination_and_source_filter_never_leaks_other_threads(work):
    links = [save(work, kind).json() for kind in ("document", "task", "decision")]
    reply = save(work, "decision", source=work.reply["id"]).json()
    with Session(work.engine) as session:
        document = session.get(SharedDocument, "document-a"); document.archived = True; session.add(document); session.commit()
    seen, offset = [], 0
    for _ in range(10):
        result = listing(work, query="?limit=1&offset=" + str(offset))
        assert result.status_code == 200, result.text
        page = result.json()
        assert len(page["items"]) <= 1
        seen += [item["id"] for item in page["items"]]
        if page["next_offset"] is None: break
        assert page["next_offset"] > offset
        offset = page["next_offset"]
    else: pytest.fail("Pagination never reached its bounded end")
    assert len(seen) == len(set(seen)) == 3
    assert set(seen) == {links[1]["id"], links[2]["id"], reply["id"]}
    filtered = listing(work, query="?source_message_id=" + work.reply["id"])
    assert [item["id"] for item in filtered.json()["items"]] == [reply["id"]]
    assert listing(work, query="?source_message_id=" + work.foreign_source["id"]).status_code == 404


def test_task_route_hint_is_resolved_from_current_canonical_project(work):
    saved = save(work, "task").json()
    response = work.client.post("/channels/a/projects", headers=headers(work), json={"id": "moved-project", "fields": {"title": "New home"}})
    assert response.status_code == 200, response.text
    with Session(work.engine) as session:
        from app.services import project_tasks as task_service
        record = session.get(TaskCard, "task-a")
        previous = task_service.revision_for(session, "task", record.id, record.revision)
        fields = task_service.serialize(session, "task", record, previous)["fields"]
        # The routing parent is part of the sealed canonical revision. Updating
        # only its sidecar would correctly make the task fail verification.
        record.project_id = "moved-project"
        write = task_service.SnapshotWrite(id=record.id, expected_revision=record.revision, fields=fields)
        revision = task_service.new_revision("task", record, write, fields, "owner")
        record.revision = revision.revision
        record.updated_at = datetime.utcnow()
        session.add(record); session.add(revision); session.commit()
    records = listing(work).json()["items"]
    assert records[0]["id"] == saved["id"] and records[0]["project_id"] == "moved-project"


@pytest.mark.parametrize("change", ["revoked", "blocked"])
def test_request_authority_is_rechecked_after_the_channel_write_fence(work, monkeypatch, change):
    original = service.lock_channel_writes
    def changed(session, channel_id):
        original(session, channel_id)
        if change == "revoked": revoke_token(work.tokens["owner"], session)
        else:
            actor = session.get(User, "owner"); actor.is_blocked = True; session.add(actor); session.commit()
    monkeypatch.setattr(service, "lock_channel_writes", changed)
    assert save(work).status_code == 401
    with Session(work.engine) as session:
        assert not session.exec(select(ConversationWorkReference)).all()


@pytest.mark.parametrize("existing", [False, True])
def test_late_workspace_membership_revocation_blocks_both_new_and_idempotent_reference_writes(work, monkeypatch, existing):
    original_id = None
    if existing:
        response = save(work)
        assert response.status_code == 201, response.text
        original_id = response.json()["id"]
    original = service.require_resource
    checked = []
    def revoke_workspace_grant(session, channel_id, kind, resource_id):
        result = original(session, channel_id, kind, resource_id)
        checked.append(resource_id)
        membership = session.get(WorkspaceMember, ("wa", "owner"))
        session.delete(membership)
        session.commit()
        return result
    monkeypatch.setattr(service, "require_resource", revoke_workspace_grant)
    response = save(work)
    assert response.status_code == 403, response.text
    assert checked == [work.targets["document"]]
    with Session(work.engine) as session:
        references = session.exec(select(ConversationWorkReference)).all()
        assert len(references) == (1 if existing else 0)
        assert {row.id for row in references} == ({original_id} if existing else set())


@pytest.mark.parametrize("existing", [False, True])
def test_late_direct_message_block_stops_both_new_and_idempotent_outbound_reference_writes(work, monkeypatch, existing):
    with Session(work.engine) as session:
        room = session.get(Channel, "a"); room.type = "direct"; session.add(room)
        session.delete(session.get(ChannelMember, ("a", "blocked"))); session.commit()
    original_id = None
    if existing:
        response = save(work)
        assert response.status_code == 201, response.text
        original_id = response.json()["id"]
    original = service.require_resource
    checked = []
    def block_direct_pair(session, channel_id, kind, resource_id):
        result = original(session, channel_id, kind, resource_id)
        checked.append(resource_id)
        session.add(UserBlock(blocker_id="owner", blocked_id="peer")); session.commit()
        return result
    monkeypatch.setattr(service, "require_resource", block_direct_pair)
    response = save(work)
    assert response.status_code == 403, response.text
    assert checked == [work.targets["document"]]
    with Session(work.engine) as session:
        references = session.exec(select(ConversationWorkReference)).all()
        assert len(references) == (1 if existing else 0)
        assert {row.id for row in references} == ({original_id} if existing else set())


def test_bad_input_cannot_smuggle_content_or_unsupported_resource_kinds(work):
    for extra in ({"resource_kind": "message"}, {"resource_kind": "workflow"},
                  {"resource_id": ""}, {"source_message_id": ""}, {"title": "PRIVATE forged content"},
                  {"project_id": "forged-route"}):
        assert save(work, **extra).status_code == 422
    assert listing(work, query="?limit=0").status_code == 422
    assert listing(work, query="?limit=501").status_code == 422
    assert listing(work, query="?offset=-1").status_code == 422
    assert listing(work).json()["items"] == []


def test_private_export_includes_routing_provenance_without_new_content_payloads(controls):
    client, engine, _, _ = controls
    client.app.include_router(conversation_work.router)
    linked = client.post("/channels/native/conversation-work", headers=control_headers(controls), json={
        "source_message_id": "fresh", "resource_kind": "decision", "resource_id": "decision"})
    assert linked.status_code == 201, linked.text
    queued = post(controls, "/exports", {"channel_ids": ["native"], "acknowledge_plaintext_export": True})
    assert queued.status_code == 202, queued.text
    with Session(engine) as session: assert data_service.run_pending(session) == 1
    downloaded = post(controls, "/exports/" + queued.json()["id"] + "/download")
    assert downloaded.status_code == 200, downloaded.text
    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
        exported = json.loads(archive.read("export.json"))
    record = exported["conversationworkreference"][0]
    assert record["id"] == linked.json()["id"] and record["source_message_id"] == "fresh"
    assert record["resource_kind"] == "decision" and record["decision_id"] == "decision"
    assert not {"content", "title", "encrypted_payload", "resource_payload"}.intersection(record)


@pytest.mark.parametrize("erased", ["source", "target", "reference"])
def test_retention_removes_dependent_routes_without_holding_canonical_content_or_erasing_unrelated_work(controls, erased):
    client, engine, _, _ = controls
    client.app.include_router(conversation_work.router); client.app.include_router(decisions.router)
    retained = client.post("/channels/native/decisions", headers=control_headers(controls),
        json={"title": "Fresh retained decision", "content": "Keep this separate native content", "status": "proposed"})
    assert retained.status_code == 200, retained.text
    source = "old-root" if erased == "source" else "fresh"
    target = "decision" if erased == "target" else retained.json()["id"]
    linked = client.post("/channels/native/conversation-work", headers=control_headers(controls), json={
        "source_message_id": source, "resource_kind": "decision", "resource_id": target})
    assert linked.status_code == 201, linked.text
    identifier = linked.json()["id"]
    if erased == "reference":
        with Session(engine) as session:
            reference = session.get(ConversationWorkReference, identifier); reference.created_at = OLD
            session.add(reference); session.commit()
    enable(controls)
    dry = preview(controls)
    assert dry["counts"]["conversationworkreference"] == 1
    assert queue_purge(controls, dry).status_code == 202
    with Session(engine) as session:
        assert data_service.run_pending(session) == 1
        assert session.get(ConversationWorkReference, identifier) is None
        assert session.get(Message, "fresh") is not None and session.get(Message, "hidden-message") is not None
        assert session.get(Decision, retained.json()["id"]) is not None
        if erased == "source": assert session.get(Message, "old-root") is None
        if erased == "target": assert session.get(Decision, "decision") is None
