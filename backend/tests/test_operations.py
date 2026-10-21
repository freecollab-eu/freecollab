"""Redacted operator diagnostics and explicit, durable mail actions."""
import base64
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api import operations
from app.config import settings
from app.database import get_session
from app.models.account_security import AccountSecurity, MailOutbox
from app.models.channel import Channel
from app.models.operations import MailOperation, WorkerHeartbeat
from app.models.user import User
from app.models.version_proof import VersionProof
from app.models.webhook import WebhookDelivery, WebhookEndpoint
from app.models.workspace import Workspace
from app.services import mail
from app.services.account_security import account_limit, seal_private, totp_at, unseal_private
from app.services.auth import create_token, hash_password
from app.services.maildiagnostics import record_worker_heartbeat


PASSWORD = "Operations-test-password-2026"


@pytest.fixture
def operator(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "environment", "test")
    monkeypatch.setattr(settings, "account_security_key", SecretStr(base64.b64encode(b"\x83" * 32).decode()))
    monkeypatch.setattr(settings, "mail_delivery_mode", "file")
    monkeypatch.setattr(settings, "mail_file_path", str(tmp_path / "mail"))
    monkeypatch.setattr(settings, "smtp_host", "")
    monkeypatch.setattr(settings, "smtp_username", "")
    monkeypatch.setattr(settings, "smtp_password", None)
    monkeypatch.setattr(settings, "smtp_from", "FreeCollab <no-reply@localhost>")
    monkeypatch.setattr(settings, "smtp_starttls", True)
    monkeypatch.setattr(settings, "smtp_implicit_tls", False)
    monkeypatch.setattr(settings, "public_app_url", "http://testserver")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        for identity, role in (("operator", "admin"), ("moderator", "moderator"), ("workspace-owner", "user")):
            session.add(User(id=identity, name=identity, email=identity + "@example.com", role=role, password_hash=hash_password(PASSWORD)))
        session.commit()
        session.add(AccountSecurity(user_id="operator", email_verified_at=datetime.utcnow()))
        session.commit()
    app = FastAPI()
    app.include_router(operations.router)
    def override_session():
        with Session(engine) as session:
            yield session
    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as client:
        yield client, engine, tmp_path
    engine.dispose()


def headers(identity="operator"):
    return {"Authorization": "Bearer " + create_token(identity)}


def seed_mail(engine, *, status="failed", attempts=5, body="Private reset token body", recipient="private-recipient@example.com", minutes_old=0):
    with Session(engine) as session:
        row = mail.enqueue_mail(session, recipient, "Private subject", body)
        row.status, row.attempts = status, attempts
        row.created_at = datetime.utcnow() - timedelta(minutes=minutes_old)
        row.failure = "Provider credential-and-token-must-never-leak" if status == "failed" else None
        session.add(row)
        session.commit()
        return row.id


@pytest.mark.parametrize("identity", ["moderator", "workspace-owner"])
def test_nonoperators_cannot_read_operational_data_or_act(operator, identity):
    client, _, _ = operator
    for path in ("/operations/status", "/operations/mail/deliveries", "/operations/metrics"):
        assert client.get(path, headers=headers(identity)).status_code == 403
        assert client.get(path).status_code == 401
    assert client.post("/operations/mail/test", headers=headers(identity), json={"password": PASSWORD}).status_code == 403
    assert client.post("/operations/mail/deliveries/missing/retry", headers=headers(identity), json={"password": PASSWORD}).status_code == 403


def test_status_and_delivery_listing_are_redacted_without_opening_content(operator, monkeypatch):
    client, engine, _ = operator
    identifier = seed_mail(engine)
    monkeypatch.setattr(settings, "mail_delivery_mode", "smtp")
    monkeypatch.setattr(settings, "smtp_host", "smtp.secret-provider.invalid")
    monkeypatch.setattr(settings, "smtp_username", "secret-smtp-user")
    monkeypatch.setattr(settings, "smtp_password", SecretStr("secret-smtp-password"))
    monkeypatch.setattr(mail, "unseal_private", lambda *args: (_ for _ in ()).throw(AssertionError("Read must not decrypt mail")))
    status = client.get("/operations/status", headers=headers())
    assert status.status_code == 200 and status.headers["cache-control"] == "no-store"
    assert status.json()["mail"]["configured"] and status.json()["mail"]["tls"] == "starttls"
    assert status.json()["queue"]["failed"] == 1
    assert status.json()["worker"]["status"] == "unknown"
    response = client.get("/operations/mail/deliveries", headers=headers())
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["id"] == identifier and item["retryable"]
    combined = status.text + response.text
    for private in ("secret-provider", "secret-smtp", "private-recipient", "Private subject", "reset token", "credential-and-token", "encrypted_message"):
        assert private not in combined
    assert "inspect server delivery configuration" in item["failure"]
    assert client.get("/operations/mail/deliveries?limit=100000", headers=headers()).status_code == 422


def test_configuration_issues_describe_missing_setup_without_secret_values(operator, monkeypatch):
    client, _, _ = operator
    monkeypatch.setattr(settings, "mail_delivery_mode", "smtp")
    monkeypatch.setattr(settings, "smtp_host", "")
    monkeypatch.setattr(settings, "smtp_username", "hidden-user")
    monkeypatch.setattr(settings, "smtp_from", "invalid sender")
    config = client.get("/operations/status", headers=headers()).json()["mail"]
    assert not config["configured"]
    assert set(config["issues"]) == {"smtp_host_missing", "smtp_credentials_incomplete", "smtp_sender_invalid"}
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "smtp_starttls", False)
    issues = client.get("/operations/status", headers=headers()).json()["mail"]["issues"]
    assert "smtp_tls_required" in issues and "public_app_https_required" in issues


def test_worker_and_metrics_report_queue_lag_without_account_labels(operator):
    client, engine, _ = operator
    seed_mail(engine, status="pending", attempts=0, minutes_old=20)
    with Session(engine) as session:
        record_worker_heartbeat(session)
    status = client.get("/operations/status", headers=headers()).json()
    assert status["worker"]["status"] == "healthy"
    assert 1199 <= status["queue"]["oldest_pending_seconds"] < 1220
    response = client.get("/operations/metrics", headers=headers())
    assert response.status_code == 200 and "version=0.0.4" in response.headers["content-type"]
    assert 'freecollab_mail_outbox_messages{status="pending"} 1' in response.text
    assert "freecollab_mail_worker_healthy 1" in response.text
    assert not any(private in response.text for private in ("@", "operator", "recipient", "token", "password"))
    with Session(engine) as session:
        row = session.get(WorkerHeartbeat, "mail")
        row.last_seen_at -= timedelta(minutes=4)
        session.add(row)
        session.commit()
    assert client.get("/operations/status", headers=headers()).json()["worker"]["status"] == "stale"


def test_test_delivery_requires_fresh_verified_operator_and_only_queues_own_address(operator):
    client, engine, tmp_path = operator
    assert client.post("/operations/mail/test", headers=headers(), json={"password": "wrong"}).status_code == 403
    assert client.post("/operations/mail/test", headers=headers(), json={"password": PASSWORD, "to": "outside@example.com"}).status_code == 422
    with Session(engine) as session:
        state = session.get(AccountSecurity, "operator")
        state.email_verified_at = None
        session.add(state)
        session.commit()
    assert client.post("/operations/mail/test", headers=headers(), json={"password": PASSWORD}).status_code == 403
    with Session(engine) as session:
        state = session.get(AccountSecurity, "operator")
        state.email_verified_at = datetime.utcnow()
        session.add(state)
        session.commit()
    result = client.post("/operations/mail/test", headers=headers(), json={"password": PASSWORD})
    assert result.status_code == 202 and result.json()["status"] == "queued"
    assert not (tmp_path / "mail").exists()
    with Session(engine) as session:
        row = session.get(MailOutbox, result.json()["id"])
        assert row.recipient == "operator@example.com" and row.status == "pending"
        assert "delivery test" not in row.encrypted_message
        payload = json.loads(unseal_private(row.encrypted_message, "outbox", row.id))
        assert "account link" in payload["body"]
        assert session.get(MailOperation, row.id).requested_by == "operator"
        assert mail.deliver_pending(session) == 1
    assert (tmp_path / "mail" / (result.json()["id"] + ".eml")).exists()


def test_operator_test_requires_current_mfa_factor(operator):
    client, engine, _ = operator
    secret = "JBSWY3DPEHPK3PXP"
    with Session(engine) as session:
        state = session.get(AccountSecurity, "operator")
        state.totp_secret = seal_private(secret, "totp", "operator")
        session.add(state)
        session.commit()
    assert client.post("/operations/mail/test", headers=headers(), json={"password": PASSWORD}).status_code == 403
    code = totp_at(secret, int(time.time() // 30))
    assert client.post("/operations/mail/test", headers=headers(), json={"password": PASSWORD, "code": code}).status_code == 202
    assert client.post("/operations/mail/test", headers=headers(), json={"password": PASSWORD, "code": "not-a-code"}).status_code == 403


def test_failed_delivery_retry_is_durable_and_does_not_duplicate_or_change_message(operator):
    client, engine, _ = operator
    identifier = seed_mail(engine)
    with Session(engine) as session:
        encrypted = session.get(MailOutbox, identifier).encrypted_message
    assert client.post(f"/operations/mail/deliveries/{identifier}/retry", headers=headers(), json={"password": "wrong"}).status_code == 403
    response = client.post(f"/operations/mail/deliveries/{identifier}/retry", headers=headers(), json={"password": PASSWORD})
    assert response.status_code == 200 and response.json()["status"] == "pending" and response.json()["attempts"] == 0
    assert client.post(f"/operations/mail/deliveries/{identifier}/retry", headers=headers(), json={"password": PASSWORD}).status_code == 409
    with Session(engine) as session:
        assert len(session.exec(select(MailOutbox)).all()) == 1
        row = session.get(MailOutbox, identifier)
        assert row.encrypted_message == encrypted and row.recipient == "private-recipient@example.com"
        assert mail.deliver_pending(session) == 1
    assert client.post(f"/operations/mail/deliveries/{identifier}/retry", headers=headers(), json={"password": PASSWORD}).status_code == 409


def test_disabled_delivery_and_rate_limit_queue_nothing(operator, monkeypatch):
    client, engine, _ = operator
    monkeypatch.setattr(settings, "mail_delivery_mode", "disabled")
    assert client.post("/operations/mail/test", headers=headers(), json={"password": PASSWORD}).status_code == 503
    assert client.get("/operations/status", headers=headers()).json()["mail"]["issues"] == ["mail_disabled"]
    with Session(engine) as session:
        for _ in range(9):
            account_limit(session, "operations-test", "operator", 10, 3600)
    assert client.post("/operations/mail/test", headers=headers(), json={"password": PASSWORD}).status_code == 429
    with Session(engine) as session:
        assert not session.exec(select(MailOutbox)).all()


def test_readonly_metrics_secret_has_no_account_or_mutation_privileges(operator, monkeypatch):
    client, _, _ = operator
    secret = "readonly-metrics-token-" + "a" * 32
    monkeypatch.setattr(settings, "operations_metrics_token", SecretStr(secret))
    scoped = {"Authorization": "Bearer " + secret}
    response = client.get("/operations/metrics", headers=scoped)
    assert response.status_code == 200 and secret not in response.text
    for path in ("/operations/status", "/operations/mail/deliveries"):
        assert client.get(path, headers=scoped).status_code == 401
    assert client.post("/operations/mail/test", headers=scoped, json={"password": PASSWORD}).status_code == 401
    assert client.post("/operations/mail/deliveries/missing/retry", headers=scoped, json={"password": PASSWORD}).status_code == 401
    assert client.get("/operations/metrics?token=" + secret).status_code == 401
    monkeypatch.setattr(settings, "operations_metrics_token", SecretStr("rotated-token-" + "b" * 32))
    assert client.get("/operations/metrics", headers=scoped).status_code == 401


def test_integration_queue_aggregate_metrics_do_not_expose_routing_or_provider_data(operator, monkeypatch):
    client, engine, _ = operator
    monkeypatch.setattr(settings, "webhooks_enabled", True)
    now = datetime.utcnow()
    with Session(engine) as session:
        session.add(Workspace(id="private-team", name="Private team name"))
        session.add(Channel(id="private-channel", name="Private channel title"))
        session.commit()
        session.add(WebhookEndpoint(id="private-hook", workspace_id="private-team", created_by="operator", granted_by="operator",
            name="Private webhook name", origin="https://private-provider.example", encrypted_url="private-url", encrypted_secret="private-secret",
            channel_ids_json='["private-channel"]', event_types_json='["message.created"]'))
        session.commit()
        session.add(WebhookDelivery(id="private-delivery", webhook_id="private-hook", event_key="private-event", event_id="private-event",
            event_type="message.created", channel_id="private-channel", payload="private-payload", generation=1, created_at=now - timedelta(minutes=7)))
        session.add(VersionProof(id="private-proof", channel_id="private-channel", target_kind="file", target_id="private-file", sha256="a" * 64,
            content_binding="native-bytes", requested_by="operator", provider_origin="https://private-otrust.example", state="pending", created_at=now - timedelta(days=2)))
        record_worker_heartbeat(session, name="integrations")
    response = client.get("/operations/status", headers=headers())
    queues = response.json()["integration_queues"]
    assert response.json()["integration_worker"]["enabled"]
    assert response.json()["integration_worker"]["status"] == "healthy"
    assert queues["webhooks"]["pending"] == 1 and queues["webhooks"]["oldest_pending_seconds"] >= 419
    assert queues["otrust"]["pending"] == 1 and queues["otrust"]["oldest_pending_seconds"] == 0
    metrics = client.get("/operations/metrics", headers=headers())
    assert 'freecollab_integration_queue_records{queue="webhooks",status="pending"} 1' in metrics.text
    assert 'freecollab_integration_queue_records{queue="otrust",status="pending"} 1' in metrics.text
    assert "freecollab_integrations_worker_healthy 1" in metrics.text
    assert "private-" not in metrics.text + response.text


def test_standalone_mail_worker_initializes_its_schema_and_records_real_poll(tmp_path):
    """Exercise the exact CLI used by Compose, independent of app import order."""
    path = tmp_path / "worker.db"
    env = dict(os.environ)
    env.update(
        ENVIRONMENT="test", DATABASE_URL="sqlite:///" + str(path),
        MAIL_DELIVERY_MODE="file", MAIL_FILE_PATH=str(tmp_path / "mail"),
        ACCOUNT_SECURITY_KEY=base64.b64encode(b"\xb7" * 32).decode(),
    )
    completed = subprocess.run([sys.executable, "-m", "app.services.mail", "--once"],
        cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    assert "Delivered 0 queued email(s)." in completed.stdout
    engine = create_engine("sqlite:///" + str(path))
    with Session(engine) as session:
        assert session.get(WorkerHeartbeat, "mail").last_seen_at >= datetime.utcnow() - timedelta(seconds=30)
    engine.dispose()


def test_mail_batch_renews_worker_heartbeat_before_each_transport_attempt(operator):
    _, engine, _ = operator
    seed_mail(engine, status="pending", attempts=0)
    with Session(engine) as session:
        session.add(WorkerHeartbeat(name="mail", last_seen_at=datetime.utcnow() - timedelta(minutes=10)))
        session.commit()
        assert mail.deliver_pending(session, heartbeat=record_worker_heartbeat) == 1
        assert session.get(WorkerHeartbeat, "mail").last_seen_at > datetime.utcnow() - timedelta(seconds=10)
