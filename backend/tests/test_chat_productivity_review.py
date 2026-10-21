"""Independent adversarial checks for delegated daily-chat delivery authority."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
from threading import Event, current_thread

import pytest
from sqlalchemy import event
from sqlmodel import Session, select

from app.api import chat_productivity, notifications
from app.models.account_security import AccountSecurity
from app.models.chat_productivity import MessageReminder, ScheduledMessage
from app.models.message import Message
from app.models.notification import Notification
from app.models.secure_channel import MatrixAccount, SecureChannel
from app.models.session import ActiveSession
from app.models.workspace_oidc import WorkspaceOIDCProvider, WorkspaceOIDCSession
from app.models.workspace import Workspace, WorkspaceMember
from app.services import chat_productivity as service, matrix_bridge
from app.services.auth import create_token, token_digest
from .test_chat_productivity import due, prepare, schedule
from .test_workspaces import workspace_team


@pytest.mark.parametrize("change", ["provider_revision", "disabled_provider", "missing_scope_sidecar", "account_version", "session_expired"])
def test_customer_sso_scheduled_send_rechecks_each_original_authority(workspace_team, change):
    client, engine, headers = prepare(workspace_team)
    with Session(engine) as session:
        session.add(WorkspaceOIDCProvider(workspace_id="team", name="Customer SSO", issuer_url="https://customer-id.example.com", client_id="customer-client", created_by="owner", enabled=True))
        session.commit()
        token = create_token("owner", session, auth_methods=["sso"], workspace_scope={"workspace_id": "team", "provider_revision": 1})
        session.add(WorkspaceOIDCSession(token_hash=token_digest(token), workspace_id="team", provider_revision=1)); session.commit()
    scoped = {**headers, "owner": {"Authorization": "Bearer " + token}}
    response = schedule(client, scoped)
    assert response.status_code == 201, response.text
    identifier = response.json()["id"]
    with Session(engine) as session:
        if change in {"provider_revision", "disabled_provider"}:
            provider = session.get(WorkspaceOIDCProvider, "team")
            if change == "provider_revision": provider.revision += 1
            else: provider.enabled = False
            session.add(provider)
        elif change == "missing_scope_sidecar":
            session.delete(session.get(WorkspaceOIDCSession, token_digest(token)))
        elif change == "account_version":
            account = session.get(AccountSecurity, "owner"); account.auth_version += 1; session.add(account)
        else:
            active = session.exec(select(ActiveSession).where(ActiveSession.token_hash == token_digest(token))).one()
            active.expires_at = datetime.utcnow() - timedelta(seconds=1); session.add(active)
        session.commit()
        assert service.run_pending(session, now=datetime.utcnow() + timedelta(minutes=2)) == 0
        row = session.get(ScheduledMessage, identifier)
        assert row.status == "cancelled" and row.encrypted_content == "" and row.claims_json == "{}"
        assert session.get(Message, identifier) is None
        assert session.get(Notification, "schedule-" + identifier) is None


def test_two_workers_contend_while_first_holds_actual_delivery_transaction(workspace_team, monkeypatch):
    client, engine, headers = prepare(workspace_team)
    response = schedule(client, headers)
    assert response.status_code == 201
    identifier = response.json()["id"]
    first_locked, second_attempting, release = Event(), Event(), Event()
    original = service.deliver_scheduled
    def paused(session, row, now):
        first_locked.set()
        assert release.wait(5)
        return original(session, row, now)
    monkeypatch.setattr(service, "deliver_scheduled", paused)
    @event.listens_for(engine, "before_cursor_execute")
    def contention(connection, cursor, statement, parameters, context, executemany):
        if current_thread().name.endswith("_1") and statement.lstrip().upper().startswith("UPDATE SCHEDULEDMESSAGE"):
            second_attempting.set()
    def worker():
        with Session(engine) as session:
            return service.run_pending(session, now=datetime.utcnow() + timedelta(minutes=2))
    try:
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="delivery-review") as pool:
            first = pool.submit(worker); assert first_locked.wait(5)
            second = pool.submit(worker); assert second_attempting.wait(5)
            release.set()
            assert first.result(10) + second.result(10) == 1
    finally:
        release.set(); event.remove(engine, "before_cursor_execute", contention)
    with Session(engine) as session:
        assert session.get(ScheduledMessage, identifier).status == "sent"
        assert len(session.exec(select(Message).where(Message.id == identifier)).all()) == 1
        assert len(session.exec(select(Notification).where(Notification.id == "schedule-" + identifier)).all()) == 1


@pytest.mark.parametrize("wrong", ["room", "plaintext", "redacted", "empty_ciphertext"])
def test_matrix_reminder_validates_opaque_current_room_reference_without_storing_content(workspace_team, monkeypatch, wrong):
    client, engine, headers = prepare(workspace_team)
    with Session(engine) as session:
        session.add(SecureChannel(channel_id="shared", created_by="owner", status="active", matrix_room_id="!current:matrix.example"))
        session.add(MatrixAccount(user_id="member", matrix_user_id="@member:matrix.example", encrypted_password="fixture-only", provisioned=True))
        session.commit()
    monkeypatch.setattr(matrix_bridge, "service_token", lambda *_args, **_kwargs: (None, "isolated-metadata-fixture"))
    document = {"room_id": "!current:matrix.example", "type": "m.room.encrypted", "sender": "@owner:matrix.example", "content": {"ciphertext": "opaque-client-ciphertext"}}
    if wrong == "room": document["room_id"] = "!other:matrix.example"
    if wrong == "plaintext": document["type"] = "m.room.message"; document["content"] = {"body": "NEVER STORE THIS PLAINTEXT"}
    if wrong == "redacted": document["unsigned"] = {"redacted_because": {"type": "m.room.redaction"}}
    if wrong == "empty_ciphertext": document["content"] = {"ciphertext": ""}
    paths = []
    def lookup(method, path, **kwargs):
        assert method == "GET" and kwargs["token"] == "isolated-metadata-fixture"
        paths.append(path); return document
    monkeypatch.setattr(matrix_bridge, "matrix_request", lookup)
    response = client.post("/channels/shared/reminders", headers=headers["member"], json={"matrix_event_id": "$opaque-ref:matrix.example", "due_at": due()})
    assert response.status_code == 400, response.text
    assert paths and "%21current%3Amatrix.example" in paths[0]
    with Session(engine) as session: assert session.exec(select(MessageReminder)).all() == []


def test_valid_secure_reminder_queues_only_reference_and_generic_private_notice(workspace_team, monkeypatch):
    client, engine, headers = prepare(workspace_team)
    with Session(engine) as session:
        session.add(SecureChannel(channel_id="shared", created_by="owner", status="active", matrix_room_id="!current:matrix.example"))
        session.add(MatrixAccount(user_id="member", matrix_user_id="@member:matrix.example", encrypted_password="fixture-only", provisioned=True)); session.commit()
    monkeypatch.setattr(matrix_bridge, "service_token", lambda *_args, **_kwargs: (None, "isolated-metadata-fixture"))
    monkeypatch.setattr(matrix_bridge, "matrix_request", lambda *_args, **_kwargs: {"room_id": "!current:matrix.example", "type": "m.room.encrypted", "sender": "@owner:matrix.example", "content": {"ciphertext": "CLIENT CIPHERTEXT MUST NOT BE COPIED"}})
    response = client.post("/channels/shared/reminders", headers=headers["member"], json={"matrix_event_id": "$opaque-ref:matrix.example", "due_at": due()})
    assert response.status_code == 201, response.text
    identifier = response.json()["id"]
    with Session(engine) as session:
        row = session.get(MessageReminder, identifier)
        assert row.item_id == "$opaque-ref:matrix.example" and row.kind == "matrix"
        assert "CLIENT CIPHERTEXT" not in json.dumps(row.model_dump(mode="json"))
        assert service.run_pending(session, now=datetime.utcnow() + timedelta(minutes=2)) == 1
        notification = session.get(Notification, "reminder-" + identifier)
        assert notification.user_id == "member" and "CLIENT CIPHERTEXT" not in notification.body


def test_reminder_target_and_notification_inbox_recheck_owner_current_acl_and_customer_scope(workspace_team):
    client, engine, headers = prepare(workspace_team)
    client.app.include_router(notifications.router)
    message = client.post("/messages/channels/shared", headers=headers["owner"], json={"content": "PRIVATE REMINDER CONTEXT"}).json()
    created = client.post("/channels/shared/reminders", headers=headers["member"], json={"message_id": message["id"], "due_at": due()})
    assert created.status_code == 201
    identifier = created.json()["id"]
    with Session(engine) as session:
        assert service.run_pending(session, now=datetime.utcnow() + timedelta(minutes=2)) == 1
        session.add(Workspace(id="other-workspace", name="Other customer")); session.flush()
        session.add(WorkspaceMember(workspace_id="other-workspace", user_id="member"))
        session.add(WorkspaceOIDCProvider(workspace_id="other-workspace", name="Other customer SSO", issuer_url="https://other-id.example.com", client_id="other-client", created_by="member", enabled=True)); session.commit()
        token = create_token("member", session, auth_methods=["sso"], workspace_scope={"workspace_id": "other-workspace", "provider_revision": 1})
        session.add(WorkspaceOIDCSession(token_hash=token_digest(token), workspace_id="other-workspace", provider_revision=1)); session.commit()
    assert client.get("/reminders/" + identifier + "/target", headers=headers["owner"]).status_code == 404
    assert client.get("/reminders/" + identifier + "/target", headers=headers["member"]).status_code == 200
    inbox = client.get("/notifications", headers=headers["member"])
    assert inbox.status_code == 200 and any(item["id"] == "reminder-" + identifier for item in inbox.json())
    assert "PRIVATE REMINDER CONTEXT" not in inbox.text
    scoped = {"Authorization": "Bearer " + token}
    assert client.get("/reminders/" + identifier + "/target", headers=scoped).status_code == 403
    assert client.get("/notifications", headers=scoped).json() == []
    assert client.get("/notifications/unread-count", headers=scoped).json()["unread_count"] == 0
    from app.models.channel import ChannelMember
    with Session(engine) as session:
        session.delete(session.get(ChannelMember, ("shared", "member"))); session.commit()
    assert client.get("/reminders/" + identifier + "/target", headers=headers["member"]).status_code == 403
    assert client.get("/notifications", headers=headers["member"]).json() == []
