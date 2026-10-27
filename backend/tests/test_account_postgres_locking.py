"""Real PostgreSQL lock ordering, using an isolated, disposable schema.

Set FREECOLLAB_TEST_POSTGRES_URL to a test database, or use CI's PostgreSQL
DATABASE_URL. No application schema or existing data is modified.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import base64
import hashlib
import os
import time
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest
from sqlalchemy import text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.schema import CreateSchema, DropSchema
from sqlmodel import Session, SQLModel, create_engine, select

from app.api import account, admin, auth
from app.config import settings
from app.database import get_session
from app.models.account_security import AccountRecoveryCode, AccountSecurity
from app.models.session import ActiveSession
from app.models.user import User
from app.services.account_security import issue_account_token, recovery_hash, seal_private
from app.services.auth import create_token, hash_password, revoke_token, token_digest
from app.services import native_clients


@pytest.fixture
def postgres(monkeypatch):
    url = os.environ.get("FREECOLLAB_TEST_POSTGRES_URL", settings.database_url)
    if not url.startswith(("postgresql://", "postgresql+psycopg2://")):
        pytest.skip("A dedicated PostgreSQL test database is required")
    monkeypatch.setattr(settings, "environment", "test")
    monkeypatch.setattr(settings, "account_security_key", SecretStr(base64.b64encode(b"p" * 32).decode()))
    schema = "fc_auth_lock_" + uuid4().hex
    control = create_engine(url)
    with control.begin() as connection:
        connection.execute(CreateSchema(schema))
    engine = create_engine(url, connect_args={"application_name": schema,
        "options": f"-c search_path={schema} -c statement_timeout=15000 -c lock_timeout=10000"})
    try:
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            actor = User(id="operator", name="Operator", email="operator@example.com",
                         password_hash=hash_password("valid-password"), role="admin")
            session.add(actor)
            session.commit()
            token = create_token(actor.id, session)
            reset = issue_account_token(session, actor, "password-reset", 30)
            session.commit()
        app = FastAPI()
        for router in (auth.router, account.router, admin.router):
            app.include_router(router)

        def sessions():
            with Session(engine) as session:
                yield session

        app.dependency_overrides[get_session] = sessions
        yield app, engine, control, schema, token, reset
    finally:
        engine.dispose()
        with control.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        control.dispose()


def waiting_for_database_lock(control, schema, future):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with control.connect() as connection:
            waiting = connection.execute(text("SELECT count(*) FROM pg_stat_activity "
                "WHERE application_name = :name AND wait_event_type = 'Lock'"), {"name": schema}).scalar_one()
        if waiting:
            return True
        if future.done():
            return False
        Event().wait(0.02)
    return False


def create_administrator(app, token):
    with TestClient(app) as client:
        return client.post("/admin/users", headers={"Authorization": "Bearer " + token}, json={
            "name": "Replacement", "email": "replacement@example.com", "password": "valid-password", "role": "admin",
        }).status_code


def management_token(engine, parent, native):
    if not native:
        return parent
    verifier = "v" * 43
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    callback = "http://127.0.0.1:12000/freecollab-native/callback"
    with Session(engine) as session:
        grant = native_clients.issue_grant(session, "operator", parent, challenge=challenge, callback=callback, kind="desktop")
        return native_clients.exchange_grant(session, grant["code"], verifier, callback)["access_token"]


@pytest.mark.parametrize("operation", ["reset", "password-change", "password-change-mfa"])
def test_password_writes_wait_for_user_before_locking_security(postgres, monkeypatch, operation):
    app, engine, control, schema, token, reset = postgres
    code = "ABCD-EFAB-CDEF-ABCD"
    if operation == "password-change-mfa":
        with Session(engine) as session:
            state = session.get(AccountSecurity, "operator")
            state.totp_secret = seal_private("JBSWY3DPEHPK3PXP", "totp", "operator")
            session.add(state)
            session.add(AccountRecoveryCode(user_id="operator", code_hash=recovery_hash(code)))
            session.commit()
            token = create_token("operator", session, mfa_verified=True)
    hashed = Event()
    prepared_hash = hash_password("new-valid-password")

    def prepared(_):
        hashed.set()
        return prepared_hash

    monkeypatch.setattr(account, "hash_password", prepared)

    def password_write():
        with TestClient(app) as client:
            if operation == "reset":
                return client.post("/auth/reset-password", json={"token": reset, "new_password": "new-valid-password"}).status_code
            return client.post("/account/password", headers={"Authorization": "Bearer " + token}, json={
                "current_password": "valid-password", "new_password": "new-valid-password",
                **({"code": code} if operation == "password-change-mfa" else {}),
            }).status_code

    error = None
    with ThreadPoolExecutor(max_workers=1) as workers:
        with Session(engine) as management:
            management.execute(update(User).where(User.id == "operator").values(name=User.name))
            future = workers.submit(password_write)
            try:
                assert hashed.wait(5)
                assert waiting_for_database_lock(control, schema, future)
                # This is the management guard's next lock. The password write
                # must still be waiting for User, without holding this row.
                management.execute(text("SET LOCAL lock_timeout = '500ms'"))
                try:
                    management.execute(update(AccountSecurity).where(AccountSecurity.user_id == "operator")
                                       .values(auth_version=AccountSecurity.auth_version))
                except OperationalError as exc:
                    error = exc.orig.pgcode
            finally:
                management.rollback()
        assert future.result(timeout=10) == 200
    assert error is None, "Password write acquired AccountSecurity before User"
    if operation == "password-change-mfa":
        with Session(engine) as session:
            assert session.exec(select(AccountRecoveryCode)).one().used_at is not None


@pytest.mark.parametrize("native", [False, True])
def test_logout_waits_until_authorized_administrator_publication(postgres, monkeypatch, native):
    app, engine, control, schema, parent, _ = postgres
    token = management_token(engine, parent, native)
    guarded, publish = Event(), Event()
    original = admin._lock_user_management

    def pause_after_guard(*args, **kwargs):
        actor = original(*args, **kwargs)
        guarded.set()
        assert publish.wait(10)
        return actor

    monkeypatch.setattr(admin, "_lock_user_management", pause_after_guard)

    def logout():
        with TestClient(app) as client:
            return client.post("/auth/logout", headers={"Authorization": "Bearer " + parent}).status_code

    with ThreadPoolExecutor(max_workers=2) as workers:
        creation = workers.submit(create_administrator, app, token)
        try:
            assert guarded.wait(5)
            revocation = workers.submit(logout)
            assert waiting_for_database_lock(control, schema, revocation), "Logout committed before the guarded write"
        finally:
            publish.set()
        assert creation.result(timeout=10) == 200
        assert revocation.result(timeout=10) == 200
    with Session(engine) as session:
        assert session.exec(select(User).where(User.email == "replacement@example.com")).one()


@pytest.mark.parametrize("native", [False, True])
def test_administrator_waiting_for_logout_cannot_publish(postgres, native):
    app, engine, control, schema, parent, _ = postgres
    token = management_token(engine, parent, native)
    with ThreadPoolExecutor(max_workers=1) as workers:
        with Session(engine) as revocation:
            revocation.execute(update(ActiveSession).where(ActiveSession.token_hash == token_digest(parent))
                               .values(expires_at=ActiveSession.expires_at))
            future = workers.submit(create_administrator, app, token)
            try:
                waiting = waiting_for_database_lock(control, schema, future)
                revoke_token(parent, revocation)
            finally:
                revocation.rollback()
        assert future.result(timeout=10) == 401
    assert waiting, "The administrator did not wait for its current session's revocation"
    with Session(engine) as session:
        assert session.exec(select(User).where(User.email == "replacement@example.com")).first() is None


def test_password_reset_and_email_verification_follow_same_lock_order(postgres, monkeypatch):
    app, engine, control, schema, _, reset = postgres
    with Session(engine) as session:
        verification = issue_account_token(session, session.get(User, "operator"), "email-verify", 30)
        session.commit()
    reset_consumed, finish_reset = Event(), Event()
    original = account.consume_account_token

    def pause_password_reset(session, row):
        original(session, row)
        if row.purpose == "password-reset":
            reset_consumed.set()
            assert finish_reset.wait(10)

    monkeypatch.setattr(account, "consume_account_token", pause_password_reset)

    def request(path, data):
        with TestClient(app, raise_server_exceptions=False) as client:
            return client.post(path, json=data).status_code

    with ThreadPoolExecutor(max_workers=2) as workers:
        resetting = workers.submit(request, "/auth/reset-password", {"token": reset, "new_password": "new-valid-password"})
        try:
            assert reset_consumed.wait(5)
            verifying = workers.submit(request, "/account/verification/confirm", {"token": verification})
            assert waiting_for_database_lock(control, schema, verifying)
        finally:
            finish_reset.set()
        assert resetting.result(timeout=10) == 200
        assert verifying.result(timeout=10) == 400
    with Session(engine) as session:
        state = session.get(AccountSecurity, "operator")
        assert state.auth_version == 1 and state.email_verified_at is None
