"""Actual authenticated mutations leave bounded, payload-free operator history."""
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app import main as application  # Register the complete additive graph.
from app.api import admin, auth, scim, security_events
from app.config import settings
from app.database import get_session
from app.models.security_event import SecurityEvent
from app.models.scim import SCIMDirectory
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from app.models.workspace_oidc import WorkspaceOIDCProvider, WorkspaceOIDCSession
from app.services import security_audit
from app.services.account_security import digest
from app.services.auth import create_token, get_current_user, hash_password, token_digest
from app.services.rate_limit import install_auth_rate_limits

PASSWORD = "Audit-local-test!2026"


@pytest.fixture
def audit(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "environment", "test")
    monkeypatch.setattr(settings, "mail_delivery_mode", "disabled")
    monkeypatch.setattr(settings, "matrix_enabled", False)
    monkeypatch.setattr(settings, "security_audit_retention_days", 90)
    monkeypatch.setattr(settings, "security_audit_max_records", 100000)
    monkeypatch.setattr(settings, "account_security_key", SecretStr(base64.b64encode(b"a" * 32).decode()))
    engine = create_engine(f"sqlite:///{tmp_path / 'audit.db'}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([User(id="operator", name="Private operator", email="private-operator@example.com", role="admin", password_hash=hash_password(PASSWORD)),
                         User(id="member", name="Private member", email="private-member@example.com", password_hash="unused")])
        session.add(Workspace(id="company", name="Private company"))
        session.commit()
        session.add(WorkspaceMember(workspace_id="company", user_id="operator", role="owner"))
        session.add(WorkspaceOIDCProvider(workspace_id="company", name="Customer", issuer_url="https://idp.example.com", client_id="private-client-id", created_by="operator", enabled=True))
        directory = SCIMDirectory(workspace_id="company", token_hash=digest("private-directory-bearer"), token_issued_by="operator", created_by="operator", enabled=True)
        session.add(directory)
        session.commit()
        directory_id = directory.directory_id
        native = create_token("operator", session)
        scoped = create_token("operator", session, auth_methods=["sso"], workspace_scope={"workspace_id": "company", "provider_revision": 1})
        session.add(WorkspaceOIDCSession(token_hash=token_digest(scoped), workspace_id="company", provider_revision=1))
        session.commit()
    app = FastAPI()
    for router in (auth.router, admin.router, scim.router, security_events.router):
        app.include_router(router)
    @app.post("/messages/{channel_id}")
    def ordinary_content(user=Depends(get_current_user)):
        return {"ok": True}
    def sessions():
        with Session(engine) as session:
            yield session
    app.dependency_overrides[get_session] = sessions
    install_auth_rate_limits(app)
    app.add_middleware(security_audit.SecurityAuditMiddleware, engine=engine)
    with TestClient(app) as client:
        yield client, engine, native, scoped, directory_id
    engine.dispose()


def headers(token):
    return {"Authorization": "Bearer " + token}


def rows(engine):
    with Session(engine) as session:
        return session.exec(select(SecurityEvent).order_by(SecurityEvent.created_at)).all()


def test_real_signin_and_rejected_privilege_change_record_no_payloads(audit):
    client, engine, native, _, _ = audit
    secret = "NEVER-LOG-this-query-body-user-agent-or-token"
    response = client.post("/auth/login?password=" + secret, json={"email": "private-operator@example.com", "password": PASSWORD}, headers={"User-Agent": secret})
    assert response.status_code == 200
    member = create_token("member")
    assert client.patch("/admin/users/operator/role", json={"role": "moderator"}, headers=headers(member)).status_code == 403
    events = client.get("/operations/security-events", headers=headers(native))
    assert events.status_code == 200 and events.headers["cache-control"] == "no-store"
    assert {row["action"] for row in events.json()["items"]} == {"account.sign_in", "installation.role_change"}
    rejected = next(row for row in events.json()["items"] if row["outcome"] == "denied")
    assert rejected["actor_id"] == "member" and rejected["target_id"] == "operator" and rejected["status_code"] == 403
    persisted = json.dumps([row.model_dump(mode="json") for row in rows(engine)])
    for private in (secret, PASSWORD, "private-operator@example.com", "Private operator", response.json()["access_token"], native, "user-agent", "password"):
        assert private not in persisted + events.text


@pytest.mark.parametrize("identity", ["member", "scoped", "metrics", "missing"])
def test_only_native_installation_admin_can_read_security_history(audit, monkeypatch, identity):
    client, _, native, scoped, _ = audit
    metric = "read-only-monitor-credential-32-bytes"
    monkeypatch.setattr(settings, "operations_metrics_token", SecretStr(metric))
    token = {"member": create_token("member"), "scoped": scoped, "metrics": metric, "missing": None}[identity]
    response = client.get("/operations/security-events", headers=headers(token) if token else {})
    assert response.status_code in {401, 403}
    assert client.get("/operations/security-events", headers=headers(native)).status_code == 200


def test_anonymous_failures_and_content_routes_cannot_amplify_audit_storage(audit):
    client, engine, native, _, _ = audit
    for _ in range(3):
        assert client.post("/auth/login", json={"email": "unknown@example.com", "password": PASSWORD}, headers={"x-security-actor": "operator"}).status_code == 401
        assert client.patch("/admin/users/operator/role", json={"role": "moderator"}, headers={"Authorization": "Bearer forged", "x-security-actor": "operator"}).status_code == 401
    assert client.post("/messages/company?access_token=secret", json={"body": "private-message"}, headers=headers(native)).status_code == 200
    assert rows(engine) == []


def test_email_shaped_target_parameter_is_not_retained(audit):
    client, engine, _, _, _ = audit
    assert client.delete("/admin/users/private-person%40example.com", headers=headers(create_token("member"))).status_code == 403
    event = rows(engine)[0]
    assert event.target_id is None
    assert "private-person" not in json.dumps(event.model_dump(mode="json"))


def test_scim_service_credential_has_distinct_actor_and_no_directory_payload(audit):
    client, engine, native, _, directory = audit
    response = client.post(f"/scim/v2/{directory}/Users", headers=headers("private-directory-bearer"), json={
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"], "externalId": "private-external-id",
        "userName": "private-employee@example.com", "displayName": "Private employee", "active": True})
    assert response.status_code == 201
    event = rows(engine)[0]
    assert (event.actor_kind, event.actor_id, event.workspace_id, event.action) == ("directory", directory, "company", "directory.user_create")
    text = client.get("/operations/security-events", headers=headers(native)).text
    for private in ("private-directory-bearer", "private-external-id", "private-employee", "Private employee"):
        assert private not in text


def test_retention_cleanup_is_bounded_and_keyset_pagination_does_not_skip_ties(audit):
    client, engine, native, _, _ = audit
    now = datetime.utcnow()
    event = {"actor_kind": "account", "actor_id": "operator", "action": "account.sign_in", "outcome": "success", "status_code": 200}
    with Session(engine) as session:
        session.add_all([SecurityEvent(created_at=now - timedelta(days=91), **event) for _ in range(250)])
        session.add_all([SecurityEvent(created_at=now, **event) for _ in range(3)])
        session.commit()
    security_audit.persist_event(engine, event, now=now)
    assert len(rows(engine)) == 54
    seen, cursor = [], None
    while True:
        query = {"limit": 2, "outcome": "success", **({"cursor": cursor} if cursor else {})}
        page = client.get("/operations/security-events", params=query, headers=headers(native))
        assert page.status_code == 200 and page.json()["retention_days"] == 90
        seen.extend(row["id"] for row in page.json()["items"])
        cursor = page.json()["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == len(set(seen)) == 4
    for invalid in ["not-json", base64.urlsafe_b64encode(b'{"when":"private-secret"}').decode(), "x" * 300]:
        response = client.get("/operations/security-events", params={"cursor": invalid}, headers=headers(native))
        assert response.status_code in {400, 422}
        assert "private-secret" not in response.text


def test_audit_persistence_failure_does_not_misreport_committed_action(audit, monkeypatch, caplog):
    client, engine, native, _, _ = audit
    def unavailable(*_args, **_kwargs):
        raise RuntimeError("secret-provider-password")
    monkeypatch.setattr(security_audit, "persist_event", unavailable)
    assert client.post("/auth/logout", headers=headers(native)).status_code == 200
    assert client.get("/auth/me", headers=headers(native)).status_code == 401
    assert not rows(engine)
    assert "could not be persisted" in caplog.text and "secret-provider-password" not in caplog.text


def test_audit_keeps_newest_records_with_a_hard_size_bound(audit, monkeypatch):
    _, engine, _, _, _ = audit
    monkeypatch.setattr(settings, "security_audit_max_records", 3)
    now = datetime.utcnow()
    event = {"actor_kind": "account", "actor_id": "operator", "action": "account.sign_in", "outcome": "success", "status_code": 200}
    for index in range(8):
        security_audit.persist_event(engine, event, now=now + timedelta(seconds=index))
    assert [row.created_at for row in rows(engine)] == [now + timedelta(seconds=index) for index in range(5, 8)]


def test_concurrent_audit_writers_preserve_the_size_bound(audit, monkeypatch):
    _, engine, _, _, _ = audit
    monkeypatch.setattr(settings, "security_audit_max_records", 3)
    event = {"actor_kind": "account", "actor_id": "operator", "action": "account.sign_in", "outcome": "success", "status_code": 200}
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda _: security_audit.persist_event(engine, event), range(20)))
    assert len(rows(engine)) == 3
