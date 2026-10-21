"""Tasks are collaborative, revisioned and confined to current channel grants."""
import base64
from datetime import datetime
from hashlib import sha256
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest
from sqlalchemy import event, update
from sqlmodel import Session, SQLModel, create_engine, select

from app import main as application  # Register the complete released model graph.
from app.api import project_tasks
from app.config import settings
from app.database import get_session
from app.models.account_security import AccountSecurity
from app.models.channel import Channel, ChannelMember
from app.models.collaboration import Decision
from app.models.file import File
from app.models.project_tasks import ProjectBoard, ProjectTaskRevision, TaskCard
from app.models.secure_channel import SecureChannel
from app.models.session import ActiveSession
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceChannel, WorkspaceMember
from app.models.workspace_oidc import WorkspaceOIDCProvider, WorkspaceOIDCSession
from app.services import project_tasks as service, workflow  # Register queued-event tables.
from app.services.auth import create_token, token_digest


PROJECT = {"title": "Private launch", "description": "Private project description", "color": "sage"}
TASK = {"title": "Private next step", "description": "Private task description", "status": "todo", "priority": "high",
        "assignee_user_id": "peer", "due_date": "2026-12-12", "checklist": [{"id": "first", "title": "Private checklist", "done": False}],
        "file_ids": ["file-a"], "decision_ids": ["decision-a"]}


@pytest.fixture
def board(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "environment", "test")
    monkeypatch.setattr(settings, "matrix_enabled", False)
    monkeypatch.setattr(settings, "webhooks_enabled", False)
    monkeypatch.setattr(settings, "account_security_key", SecretStr(base64.b64encode(b"k" * 32).decode()))
    engine = create_engine(f"sqlite:///{tmp_path / 'tasks.db'}", connect_args={"check_same_thread": False})
    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    SQLModel.metadata.create_all(engine)
    tokens = {}
    with Session(engine) as session:
        session.add_all([User(id=name, name=name, email=name + "@example.com", password_hash="unused", is_blocked=name == "blocked")
                         for name in ("owner", "peer", "outsider", "foreign", "blocked")])
        session.add_all([Workspace(id="wa", name="A"), Workspace(id="wb", name="B"), Channel(id="a", name="A"), Channel(id="b", name="B")])
        session.commit()
        session.add_all([WorkspaceChannel(channel_id="a", workspace_id="wa"), WorkspaceChannel(channel_id="b", workspace_id="wb")])
        session.add_all([WorkspaceMember(workspace_id="wa", user_id=name, role="owner" if name == "owner" else "admin" if name == "outsider" else "member") for name in ("owner", "peer", "outsider", "blocked")])
        session.add(WorkspaceMember(workspace_id="wb", user_id="foreign", role="owner"))
        session.add_all([ChannelMember(channel_id="a", user_id=name, role="admin" if name == "owner" else "member") for name in ("owner", "peer", "blocked")])
        session.add(ChannelMember(channel_id="b", user_id="foreign", role="admin"))
        session.add_all([File(id="file-a", channel_id="a", uploader_id="owner", filename="A.txt", content_type="text/plain", size=1, storage_path="unused-a"),
                         File(id="file-b", channel_id="b", uploader_id="foreign", filename="B.txt", content_type="text/plain", size=1, storage_path="unused-b")])
        session.add_all([Decision(id="decision-a", channel_id="a", created_by="owner"), Decision(id="decision-b", channel_id="b", created_by="foreign")])
        session.add(WorkspaceOIDCProvider(workspace_id="wa", name="Customer", issuer_url="https://idp.example.com", client_id="client", created_by="owner", enabled=True))
        session.commit()
        for name in ("owner", "peer", "outsider", "foreign", "blocked"):
            tokens[name] = create_token(name, session)
        scoped = create_token("owner", session, auth_methods=["sso"], workspace_scope={"workspace_id": "wa", "provider_revision": 1})
        session.add(WorkspaceOIDCSession(token_hash=token_digest(scoped), workspace_id="wa", provider_revision=1))
        tokens["scoped"] = scoped
        session.commit()
    app = FastAPI(); app.include_router(project_tasks.router)
    def sessions():
        with Session(engine) as session:
            yield session
    app.dependency_overrides[get_session] = sessions
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, engine=engine, tokens=tokens)
    engine.dispose()


def headers(board, who="owner"):
    return {"Authorization": "Bearer " + board.tokens[who]}


def project(board, **extra):
    response = board.client.post("/channels/a/projects", json={"id": "project-a", "fields": PROJECT, **extra}, headers=headers(board))
    assert response.status_code == 200, response.text
    return response.json()


def task(board, **extra):
    response = board.client.post("/channels/a/projects/project-a/tasks", json={"id": "task-a", "fields": TASK, **extra}, headers=headers(board))
    assert response.status_code == 200, response.text
    return response.json()


def test_native_boards_and_tasks_are_encrypted_revisioned_collaborative(board):
    project(board); original = task(board)
    changed = {**TASK, "status": "doing", "checklist": [{**TASK["checklist"][0], "done": True}]}
    response = board.client.post("/channels/a/projects/project-a/tasks", json={"id": original["id"], "expected_revision": 1, "fields": changed}, headers=headers(board, "peer"))
    assert response.status_code == 200 and response.json()["revision"] == 2
    assert response.json()["fields"]["status"] == "doing"
    history = board.client.get("/channels/a/projects/project-a/tasks/task-a/revisions", headers=headers(board)).json()
    assert [row["revision"] for row in history] == [2, 1] and history[1]["fields"] == TASK
    stale = board.client.post("/channels/a/projects/project-a/tasks", json={"id": original["id"], "expected_revision": 1, "fields": TASK}, headers=headers(board))
    assert stale.status_code == 409
    with Session(board.engine) as session:
        revisions = session.exec(select(ProjectTaskRevision)).all()
        assert len(revisions) == 3
        storage = " ".join(row.encrypted_payload or "" for row in revisions)
        for private in (PROJECT["title"], TASK["title"], TASK["description"], TASK["checklist"][0]["title"]):
            assert private not in storage
    listed = board.client.get("/channels/a/projects/project-a/tasks", headers=headers(board))
    assert listed.headers["cache-control"] == "no-store" and listed.json()["items"][0]["fields"] == changed


@pytest.mark.parametrize("who", ["outsider", "foreign", "blocked"])
def test_workspace_administration_does_not_imply_private_channel_access(board, who):
    project(board); task(board)
    for path in ("/channels/a/projects", "/channels/a/projects/project-a/tasks", "/channels/a/projects/project-a/revisions", "/channels/a/projects/project-a/tasks/task-a/revisions"):
        assert board.client.get(path, headers=headers(board, who)).status_code in {401, 403}
    assert board.client.post("/channels/a/projects", json={"id": "new", "fields": PROJECT}, headers=headers(board, who)).status_code in {401, 403}


def test_scoped_sso_cannot_cross_workspace_and_removed_members_lose_access(board):
    assert board.client.get("/channels/a/projects", headers=headers(board, "scoped")).status_code == 200
    assert board.client.get("/channels/b/projects", headers=headers(board, "scoped")).status_code == 403
    project(board)
    with Session(board.engine) as session:
        session.delete(session.get(WorkspaceMember, ("wa", "peer"))); session.commit()
    assert board.client.get("/channels/a/projects", headers=headers(board, "peer")).status_code == 403
    assert board.client.post("/channels/a/projects/project-a/tasks", json={"id": "other", "fields": {**TASK, "assignee_user_id": None}}, headers=headers(board, "peer")).status_code == 403


@pytest.mark.parametrize("fields", [
    {**TASK, "assignee_user_id": "outsider"}, {**TASK, "assignee_user_id": "blocked"},
    {**TASK, "assignee_user_id": "foreign"}, {**TASK, "file_ids": ["file-b"]}, {**TASK, "decision_ids": ["decision-b"]},
])
def test_task_references_require_current_channel_scope(board, fields):
    project(board)
    response = board.client.post("/channels/a/projects/project-a/tasks", json={"id": "task", "fields": fields}, headers=headers(board))
    assert response.status_code == 400
    with Session(board.engine) as session:
        assert not session.exec(select(TaskCard)).all()


def test_duplicate_checklist_ids_invalid_dates_and_hidden_plaintext_are_rejected(board):
    project(board)
    for fields in ({**TASK, "due_date": "2026-02-30"}, {**TASK, "checklist": [TASK["checklist"][0], TASK["checklist"][0]]}, {**TASK, "secret_extra_field": "unsupported"}):
        assert board.client.post("/channels/a/projects/project-a/tasks", json={"id": "bad", "fields": fields}, headers=headers(board)).status_code == 422
    assert board.client.post("/channels/a/projects", json={"id": "bad", "fields": PROJECT, "matrix_event_id": "$event"}, headers=headers(board)).status_code == 422


def test_archive_and_reopen_preserve_history_and_require_creator_or_channel_admin(board):
    project(board); task(board)
    path = "/channels/a/projects/project-a/tasks"
    payload = {"id": "task-a", "expected_revision": 1, "archived": True, "fields": TASK}
    assert board.client.post(path, json=payload, headers=headers(board, "peer")).status_code == 403
    assert board.client.post(path, json=payload, headers=headers(board)).json()["archived"] is True
    assert board.client.post(path, json={**payload, "expected_revision": 2}, headers=headers(board)).status_code == 409
    assert board.client.post(path, json={**payload, "expected_revision": 2, "archived": False}, headers=headers(board, "peer")).status_code == 403
    assert board.client.post(path, json={**payload, "expected_revision": 2, "archived": False}, headers=headers(board)).status_code == 200
    project(board, expected_revision=1, archived=True)
    assert board.client.post(path, json={"id": "task-a", "expected_revision": 3, "fields": TASK}, headers=headers(board)).status_code == 409
    history = board.client.get(path + "/task-a/revisions", headers=headers(board)).json()
    assert [row["archived"] for row in history] == [False, True, False]


def test_preparing_and_active_secure_channels_never_accept_native_task_content(board, monkeypatch):
    with Session(board.engine) as session:
        session.add(SecureChannel(channel_id="a", matrix_room_id="!a:test", created_by="owner", status="preparing")); session.commit()
    assert board.client.post("/channels/a/projects", json={"id": "secret", "fields": PROJECT}, headers=headers(board)).status_code == 409
    monkeypatch.setattr(service, "validate_secure_event", lambda *_: {"type": "m.room.encrypted"})
    response = board.client.post("/channels/a/projects", json={"id": "secret", "matrix_event_id": "$project"}, headers=headers(board))
    assert response.status_code == 200 and "fields" not in response.json()
    response = board.client.post("/channels/a/projects/secret/tasks", json={"id": "secret-task", "matrix_event_id": "$task"}, headers=headers(board))
    assert response.status_code == 200 and "fields" not in response.json()
    with Session(board.engine) as session:
        rows = session.exec(select(ProjectTaskRevision)).all()
        assert len(rows) == 2 and all(row.content_mode == "matrix" and row.encrypted_payload is None for row in rows)
    assert "Private" not in board.client.get("/channels/a/projects/secret/tasks", headers=headers(board)).text


def test_matrix_event_wrong_sender_or_room_does_not_create_resource(board, monkeypatch):
    with Session(board.engine) as session:
        session.add(SecureChannel(channel_id="a", matrix_room_id="!a:test", created_by="owner", status="active")); session.commit()
    def denied(*_): raise HTTPException(400, "Wrong event sender or room")
    monkeypatch.setattr(service, "validate_secure_event", denied)
    assert board.client.post("/channels/a/projects", json={"id": "secret", "matrix_event_id": "$wrong"}, headers=headers(board)).status_code == 400
    with Session(board.engine) as session:
        assert not session.exec(select(ProjectBoard)).all()


def test_logout_during_matrix_event_validation_stops_write(board, monkeypatch):
    with Session(board.engine) as session:
        session.add(SecureChannel(channel_id="a", matrix_room_id="!a:test", created_by="owner", status="active")); session.commit()
    def revoke(session, *_):
        session.exec(update(ActiveSession).where(ActiveSession.token_hash == token_digest(board.tokens["owner"])).values(revoked_at=datetime.utcnow()))
    monkeypatch.setattr(service, "validate_secure_event", revoke)
    assert board.client.post("/channels/a/projects", json={"id": "secret", "matrix_event_id": "$event"}, headers=headers(board)).status_code == 401
    with Session(board.engine) as session:
        assert not session.exec(select(ProjectBoard)).all()


def test_channel_lock_wait_rechecks_native_security_version(board, monkeypatch):
    original = project_tasks.lock_channel_writes
    def changed(session, channel_id):
        original(session, channel_id)
        session.exec(update(AccountSecurity).where(AccountSecurity.user_id == "owner").values(auth_version=1))
        session.commit()
    monkeypatch.setattr(project_tasks, "lock_channel_writes", changed)
    response = board.client.post("/channels/a/projects", json={"id": "late", "fields": PROJECT}, headers=headers(board))
    assert response.status_code == 401


def test_cross_project_ids_and_quotas_are_enforced(board):
    project(board); task(board)
    project(board, id="second")
    assert board.client.post("/channels/a/projects/second/tasks", json={"id": "task-a", "expected_revision": 1, "fields": TASK}, headers=headers(board)).status_code == 404
    with Session(board.engine) as session:
        session.add_all([ProjectBoard(id="quota" + str(index), channel_id="a", created_by="owner") for index in range(18)]); session.commit()
    assert board.client.post("/channels/a/projects", json={"id": "too-many", "fields": PROJECT}, headers=headers(board)).status_code == 409
    assert board.client.get("/channels/a/projects/project-a/tasks?limit=10000", headers=headers(board)).status_code == 422


def test_workflow_task_is_idempotent_native_scoped_and_never_overwrites_edited_task(board):
    project(board)
    with Session(board.engine) as session:
        created = service.create_workflow_task(session, "a", "owner", "project-a", "Automation title", "run-1")
        task_id = created.id; session.commit()
        again = service.create_workflow_task(session, "a", "owner", "project-a", "Automation title", "run-1")
        assert again.id == task_id
        session.commit()
    response = board.client.post("/channels/a/projects/project-a/tasks", json={"id": task_id, "expected_revision": 1, "fields": {**TASK, "title": "Edited by team"}}, headers=headers(board, "peer"))
    assert response.status_code == 200
    with Session(board.engine) as session:
        again = service.create_workflow_task(session, "a", "owner", "project-a", "Automation title", "run-1")
        assert again.revision == 2
        assert len(session.exec(select(TaskCard)).all()) == 1
        assert service.serialize(session, "task", again, service.revision_for(session, "task", again.id, 2))["fields"]["title"] == "Edited by team"
        session.rollback()
        with pytest.raises(HTTPException):
            service.create_workflow_task(session, "a", "peer", "project-a", "Denied", "run-2")
        session.rollback()
        session.add(SecureChannel(channel_id="a", matrix_room_id="!a:test", created_by="owner", status="preparing")); session.commit()
        with pytest.raises(HTTPException):
            service.create_workflow_task(session, "a", "owner", "project-a", "Never plaintext", "run-3")


def test_workflow_preexisting_identifier_cannot_fake_completed_automation(board):
    project(board)
    key = sha256(b"a\0project-a\0owner\0run-1").hexdigest()
    with Session(board.engine) as session:
        session.add(TaskCard(id="wf_" + key, project_id="project-a", channel_id="a", created_by="owner")); session.commit()
        with pytest.raises(HTTPException) as caught:
            service.create_workflow_task(session, "a", "owner", "project-a", "Expected task", "run-1")
        assert caught.value.status_code == 409
