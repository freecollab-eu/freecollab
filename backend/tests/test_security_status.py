"""Private operational checks must describe real state without exposing secrets."""
import base64
from datetime import datetime, timedelta
import json
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api import security_status as api
from app.config import settings
from app.database import get_session
from app.models.account_security import AccountSecurity, MailOutbox
from app.models.channel import Channel
from app.models.remote_sync import RemoteSyncConnection
from app.models.user import User
from app.models.version_proof import VersionProof
from app.models.webhook import WebhookDelivery, WebhookEndpoint
from app.models.workforce import WorkforcePrincipal
from app.models.workspace import Workspace, WorkspaceMember
from app.models.workspace_oidc import WorkspaceOIDCProvider, WorkspaceOIDCSession
from app.services import account_security, crypto, operational_security as diagnostics
from app.services.auth import create_token, token_digest


def secret(byte):
    return SecretStr(base64.b64encode(bytes([byte]) * 32).decode())


def headers(token=None):
    return {"Authorization": "Bearer " + (token or create_token("private-operator"))}


def checks(value):
    return {item["id"]: item["status"] for item in value["checks"]}


@pytest.fixture
def operator(tmp_path, monkeypatch):
    for flag in ("workflows_enabled", "data_controls_enabled", "push_enabled"):
        monkeypatch.setattr(settings, flag, False)
    monkeypatch.setattr(settings, "environment", "test")
    monkeypatch.setattr(settings, "jwt_secret", "private-unique-operator-signing-secret-2026")
    monkeypatch.setattr(settings, "account_security_key", secret(31))
    monkeypatch.setattr(settings, "message_encryption_keys", {"current": secret(32)})
    monkeypatch.setattr(settings, "message_encryption_active_key_id", "current")
    monkeypatch.setattr(settings, "public_app_url", "https://private-app.example")
    monkeypatch.setattr(settings, "cors_origins", ["https://private-app.example"])
    monkeypatch.setattr(settings, "storage_path", str(tmp_path / "private-storage"))
    monkeypatch.setattr(settings, "enable_experimental_features", False)
    monkeypatch.setattr(settings, "enable_demo_ai", False)
    monkeypatch.setattr(settings, "mail_delivery_mode", "file")
    for name in ("webhooks_enabled", "otrust_enabled", "sync_enabled"):
        monkeypatch.setattr(settings, name, False)
    monkeypatch.setattr(diagnostics.shutil, "disk_usage", lambda path: SimpleNamespace(total=20 * 1024 ** 3, free=10 * 1024 ** 3))
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([User(id="private-operator", name="Private operator name", email="private-operator@example.com", password_hash="unused", role="admin"),
                         User(id="moderator", name="Moderator", email="moderator@example.com", password_hash="unused", role="moderator"),
                         User(id="member", name="Member", email="member@example.com", password_hash="unused")])
        session.add(Workspace(id="workspace", name="Private workspace name"))
        session.add(Channel(id="channel", name="Private channel name"))
        session.commit()
        session.add(AccountSecurity(user_id="private-operator", email_verified_at=datetime.utcnow()))
        session.commit()
    app = FastAPI()
    app.include_router(api.router)

    def sessions():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = sessions
    with TestClient(app) as client:
        yield client, engine
    engine.dispose()


def test_only_native_installation_admin_can_read_private_checks(operator, monkeypatch):
    client, engine = operator
    assert client.get("/operations/security").status_code == 401
    for actor in ("moderator", "member"):
        assert client.get("/operations/security", headers=headers(create_token(actor))).status_code == 403
    metrics = "private-metrics-credential-" + "m" * 40
    monkeypatch.setattr(settings, "operations_metrics_token", SecretStr(metrics))
    assert client.get("/operations/security", headers=headers(metrics)).status_code == 401
    with Session(engine) as session:
        session.add(WorkspaceMember(workspace_id="workspace", user_id="private-operator", role="owner"))
        session.add(WorkspaceOIDCProvider(workspace_id="workspace", name="Private identity provider",
            issuer_url="https://private-idp.example", client_id="private-client", created_by="private-operator", enabled=True))
        session.commit()
        scoped = create_token("private-operator", session, auth_methods=["sso"],
            workspace_scope={"workspace_id": "workspace", "provider_revision": 1})
        session.add(WorkspaceOIDCSession(token_hash=token_digest(scoped), workspace_id="workspace", provider_revision=1))
        session.commit()
    assert client.get("/operations/security", headers=headers(scoped)).status_code == 403
    response = client.get("/operations/security", headers=headers())
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert checks(response.json())["independent_server_keys"] == "pass"


def test_read_is_redacted_and_never_decrypts_contacts_providers_or_opens_keyfiles(operator, monkeypatch):
    client, engine = operator
    forbidden = Mock(side_effect=AssertionError("Diagnostics must not open private content"))
    monkeypatch.setattr(account_security, "unseal_private", forbidden)
    monkeypatch.setattr(crypto, "message_keyring", forbidden)
    monkeypatch.setattr("httpx.Client", forbidden)
    monkeypatch.setattr("pathlib.Path.read_bytes", forbidden)
    monkeypatch.setattr(settings, "smtp_password", SecretStr("private-provider-password"))
    monkeypatch.setattr(settings, "smtp_host", "private-smtp.example")
    with Session(engine) as session:
        session.add(MailOutbox(id="private-outbox-id", recipient="private-recipient@example.com",
            encrypted_message="private-encrypted-reset-instructions", status="failed",
            failure="private-provider-exception-with-token"))
        session.add(RemoteSyncConnection(id="private-sync-id", workspace_id="workspace", channel_id="channel",
            created_by="private-operator", grant_user_id="private-operator", name="Private cloud label",
            provider="caldav", kind="calendar", config_json='{"base_url":"https://private-cloud.example/calendar/"}',
            encrypted_credentials="private-encrypted-credentials", status="error", enabled=True))
        session.commit()
    response = client.get("/operations/security", headers=headers())
    assert response.status_code == 200, response.text
    serialized = response.text
    for value in ("private-operator", "Private operator name", "Private workspace name", "Private channel name",
                  "private-app.example", "private-storage", "private-provider-password", "private-smtp.example",
                  "private-recipient", "private-outbox-id", "private-encrypted-reset", "private-provider-exception",
                  "private-sync-id", "Private cloud label", "private-cloud.example", "private-encrypted-credentials",
                  settings.account_security_key.get_secret_value(), settings.jwt_secret):
        assert value not in serialized
    assert response.json()["queues"]["mail"]["failed"] == 1
    assert checks(response.json())["mail_leases"] == "attention"
    assert len(response.json()["checks"]) == 14
    forbidden.assert_not_called()
    with Session(engine) as session:
        assert session.get(MailOutbox, "private-outbox-id").status == "failed"
        row = session.get(RemoteSyncConnection, "private-sync-id")
        assert row.enabled and row.encrypted_credentials == "private-encrypted-credentials"


@pytest.mark.parametrize("value", [
    "http://app.example", "https://user:password@app.example", "https://app.example/path",
    "https://app.example?", "https://app.example#", "https://app.example:0", "https://app.example:99999",
    "https://*.example", "https://localhost", "https://local.localhost", "https://127.0.0.1",
    "https://169.254.169.254", "https://[::1]", "https://app.example\x00", "https://app.example\n",
    "https://%65xample.com", "https://app.example\\",
])
def test_origin_checks_reject_untrusted_or_non_origin_syntax_without_dns(value):
    assert diagnostics.https_origin(value) is None


def test_configuration_checks_are_exact_and_do_not_claim_external_verification(operator, monkeypatch):
    client, _ = operator
    monkeypatch.setattr(settings, "environment", "production")
    value = client.get("/operations/security", headers=headers()).json()
    assert checks(value)["public_https_origin"] == checks(value)["cors_https_origins"] == "pass"
    assert "certificate" not in json.dumps(value) and "backup" not in json.dumps(value)
    monkeypatch.setattr(settings, "cors_origins", ["https://private-app.example", "https://other.example/path"])
    monkeypatch.setattr(settings, "enable_experimental_features", True)
    monkeypatch.setattr(settings, "enable_demo_ai", True)
    changed = checks(client.get("/operations/security", headers=headers()).json())
    assert changed["cors_https_origins"] == "attention"
    assert changed["experimental_features"] == changed["demo_ai"] == "attention"
    monkeypatch.setattr(settings, "cors_origins", ["https://another.example"])
    assert checks(client.get("/operations/security", headers=headers()).json())["cors_https_origins"] == "attention"
    monkeypatch.setattr(settings, "public_app_url", "http://127.0.0.1:3000")
    assert checks(client.get("/operations/security", headers=headers()).json())["public_https_origin"] == "attention"
    monkeypatch.setattr(settings, "environment", "development")
    assert checks(client.get("/operations/security", headers=headers()).json())["public_https_origin"] == "unknown"


@pytest.mark.parametrize("origin", ["https://PRIVATE-APP.EXAMPLE", "https://private-app.example:443", "HTTPS://private-app.example"])
def test_cors_checks_actual_literal_browser_origin_matching(operator, monkeypatch, origin):
    client, _ = operator
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "cors_origins", [origin])
    assert checks(client.get("/operations/security", headers=headers()).json())["cors_https_origins"] == "attention"


def test_key_presence_and_independence_never_reads_development_files(operator, monkeypatch):
    client, _ = operator
    assert diagnostics.independent_key_status() == "pass"
    monkeypatch.setattr(settings, "account_security_key", secret(32))
    assert diagnostics.independent_key_status() == "attention"
    signing = settings.jwt_secret.encode()[:32].ljust(32, b"0")
    monkeypatch.setattr(settings, "account_security_key", SecretStr(base64.b64encode(signing).decode()))
    assert diagnostics.independent_key_status() == "attention"
    monkeypatch.setattr(settings, "account_security_key", SecretStr("not-valid-base64"))
    assert diagnostics.independent_key_status() == "attention"
    monkeypatch.setattr(settings, "account_security_key", None)
    monkeypatch.setattr(settings, "message_encryption_keys", {})
    assert diagnostics.independent_key_status() == "unknown"
    monkeypatch.setattr(settings, "environment", "production")
    assert diagnostics.independent_key_status() == "attention"


def test_operator_mfa_counts_only_active_personal_installation_operators(operator):
    client, engine = operator
    with Session(engine) as session:
        session.add_all([
            User(id="second-operator", name="Second", email="second@example.com", password_hash="unused", role="admin"),
            User(id="blocked-operator", name="Blocked", email="blocked@example.com", password_hash="unused", role="admin", is_blocked=True),
            User(id="managed-admin", name="Managed", email="workforce-managed-admin@identity.invalid", password_hash="unused", role="admin"),
        ])
        session.commit()
        session.add(AccountSecurity(user_id="second-operator", totp_secret="private-encrypted-authenticator"))
        session.add(WorkforcePrincipal(user_id="managed-admin", workspace_id="workspace", external_id="employee", managed_email="managed@example.com"))
        session.commit()
    value = client.get("/operations/security", headers=headers()).json()
    assert value["operators"] == {"active": 2, "mfa_enabled": 1, "without_mfa": 1, "email_verified": 1}
    assert checks(value)["operator_mfa"] == "attention"
    with Session(engine) as session:
        account = session.get(AccountSecurity, "private-operator")
        account.totp_secret = "private-encrypted-authenticator-2"
        session.add(account)
        session.commit()
    assert checks(client.get("/operations/security", headers=headers()).json())["operator_mfa"] == "pass"


@pytest.mark.parametrize("total,free,expected", [
    (20 * 1024 ** 3, 1024 ** 3, "pass"),
    (100 * 1024 ** 3, 1024 ** 3, "attention"),
    (2 * 1024 ** 3, 512 * 1024 ** 2, "attention"),
    (0, 0, "unknown"),
])
def test_storage_reports_absolute_and_relative_capacity_thresholds(operator, monkeypatch, total, free, expected):
    monkeypatch.setattr(diagnostics.shutil, "disk_usage", lambda path: SimpleNamespace(total=total, free=free))
    client, _ = operator
    value = client.get("/operations/security", headers=headers()).json()
    assert value["storage"]["status"] == checks(value)["storage_capacity"] == expected


def test_inaccessible_storage_is_unknown_and_does_not_leak_path(operator, monkeypatch):
    client, _ = operator
    monkeypatch.setattr(diagnostics.shutil, "disk_usage", Mock(side_effect=PermissionError("private-mount-path")))
    response = client.get("/operations/security", headers=headers())
    assert response.json()["storage"] == {"status": "unknown", "free_bytes": None, "total_bytes": None, "free_percent": None}
    assert "private-mount-path" not in response.text


@pytest.mark.parametrize("environment,mode,host,enabled", [
    ("test", "file", "", True), ("production", "file", "", False),
    ("test", "smtp", "", False), ("production", "smtp", "private-smtp.example", True),
])
def test_mail_queue_dispatch_readiness_matches_the_actual_worker(operator, monkeypatch, environment, mode, host, enabled):
    client, _ = operator
    monkeypatch.setattr(settings, "environment", environment)
    monkeypatch.setattr(settings, "mail_delivery_mode", mode)
    monkeypatch.setattr(settings, "smtp_host", host)
    assert client.get("/operations/security", headers=headers()).json()["queues"]["mail"]["enabled"] is enabled


def seed_delivery(session, model, identity, *, status, when, claimed, now):
    common = {"id": identity, "next_attempt_at": when, "claimed_at": claimed}
    if model is MailOutbox:
        return MailOutbox(**common, status=status, recipient="private@example.com", encrypted_message="private-message")
    if model is WebhookDelivery:
        return WebhookDelivery(**common, status=status, webhook_id="hook", event_key=identity,
            event_id=identity, event_type="message.created", payload="{}", generation=1)
    return VersionProof(**common, state=status, channel_id="channel", target_kind="file", target_id=identity,
        sha256="0" * 64, content_binding="private-binding", requested_by="private-operator", provider_origin="https://private-proof.example")


def test_due_and_expired_queue_counts_follow_actual_leases_flags_and_selected_connections(operator, monkeypatch):
    _, engine = operator
    now = datetime(2026, 10, 21, 12)
    for name in ("webhooks_enabled", "otrust_enabled", "sync_enabled"):
        monkeypatch.setattr(settings, name, True)
    with Session(engine) as session:
        session.add(WebhookEndpoint(id="hook", workspace_id="workspace", created_by="private-operator",
            granted_by="private-operator", name="Private hook", origin="https://private-hook.example",
            encrypted_url="private-url", encrypted_secret="private-secret", channel_ids_json='["channel"]', event_types_json='["message.created"]'))
        session.commit()
        for model, lease in ((MailOutbox, 600), (WebhookDelivery, 90), (VersionProof, 300)):
            prefix = model.__name__
            for suffix, status, due, claim in (
                ("due", "queued" if model is VersionProof else "pending", now - timedelta(seconds=1), None),
                ("later", "queued" if model is VersionProof else "pending", now + timedelta(seconds=1), None),
                ("fresh", "processing", now, now - timedelta(seconds=lease - 1)),
                ("boundary", "processing", now, now - timedelta(seconds=lease)),
                ("expired", "processing", now, now - timedelta(seconds=lease + 1)),
                ("invalid", "processing", now, None),
                ("failed", "failed", now, None),
            ):
                session.add(seed_delivery(session, model, prefix + suffix, status=status, when=due, claimed=claim, now=now))
        for identity, status, enabled, scheduled, lease in (
            ("due", "queued", True, now, None), ("failed", "error", True, now, None),
            ("later", "queued", True, now + timedelta(seconds=1), None),
            ("expired", "syncing", True, now, now), ("invalid", "syncing", True, now, None),
            ("fresh", "syncing", True, now, now + timedelta(seconds=1)),
            ("disabled", "error", False, now, None),
        ):
            session.add(RemoteSyncConnection(id="sync-" + identity, workspace_id="workspace", channel_id="channel",
                created_by="private-operator", grant_user_id="private-operator", name="Private cloud label",
                provider="caldav", kind="calendar", config_json="{}", status=status, enabled=enabled,
                next_run_at=scheduled, lease_expires_at=lease))
        session.commit()
        result = diagnostics.security_status(session, now=now)
        for queue in ("mail", "webhooks", "otrust"):
            assert result["queues"][queue] == {"enabled": True, "due": 2, "expired_leases": 2, "failed": 1}
        assert result["queues"]["sync"] == {"enabled": True, "due": 4, "expired_leases": 2, "failed": 1}
        assert all(checks(result)[key] == "attention" for key in ("mail_leases", "webhook_leases", "proof_leases", "sync_leases"))
        assert session.get(MailOutbox, "MailOutboxexpired").status == "processing"
        assert session.get(RemoteSyncConnection, "sync-expired").status == "syncing"
        for name in ("webhooks_enabled", "otrust_enabled", "sync_enabled"):
            monkeypatch.setattr(settings, name, False)
        monkeypatch.setattr(settings, "mail_delivery_mode", "disabled")
        disabled = diagnostics.security_status(session, now=now)
        for queue in disabled["queues"].values():
            assert queue == {"enabled": False, "due": 0, "expired_leases": 0, "failed": 0}
        assert all(checks(disabled)[key] == "disabled" for key in ("mail_leases", "webhook_leases", "proof_leases", "sync_leases"))
