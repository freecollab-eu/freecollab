"""Bearer revocation and credential validation with isolated persistent state."""
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from app.api import auth
from app.database import get_session
from app.models.session import RevokedToken
from app.models.user import User
from app.services.auth import active_user_for_token, create_token, decode_token, hash_password, revoke_token, token_digest
from app.services.rate_limit import AuthRateLimiter, install_auth_rate_limits


@pytest.fixture
def sessions():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(User(id="member", name="Member", email="member@example.com", password_hash=hash_password("valid-password")))
        session.commit()
    app = FastAPI()
    app.include_router(auth.router)
    install_auth_rate_limits(app)
    def override_session():
        with Session(engine) as session:
            yield session
    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as client:
        yield client, engine, app
    engine.dispose()


def bearer(token):
    return {"Authorization": "Bearer " + token}


def test_new_sessions_are_unique_and_logout_only_revokes_one(sessions):
    client, engine, _ = sessions
    first, second = create_token("member"), create_token("member")
    assert first != second
    assert client.get("/auth/me", headers=bearer(first)).status_code == 200
    response = client.post("/auth/logout", headers=bearer(first))
    assert response.status_code == 200 and response.json() == {"logged_out": True}
    assert client.get("/auth/me", headers=bearer(first)).status_code == 401
    assert client.get("/auth/me", headers=bearer(second)).status_code == 200
    with Session(engine) as session:
        row = session.get(RevokedToken, token_digest(first))
        assert row and first not in str(row.model_dump())
        assert active_user_for_token(first, session) is None
        assert active_user_for_token(second, session).id == "member"


def test_logout_requires_authenticated_active_account(sessions):
    client, engine, _ = sessions
    assert client.post("/auth/logout").status_code in (401, 403)
    assert client.post("/auth/logout", headers=bearer("invalid")).status_code == 401
    token = create_token("member")
    with Session(engine) as session:
        user = session.get(User, "member")
        user.is_blocked = True
        session.add(user)
        session.commit()
        assert active_user_for_token(token, session) is None
    assert client.post("/auth/logout", headers=bearer(token)).status_code == 403


def test_revocation_cleans_expired_digests_and_is_idempotent(sessions):
    _, engine, _ = sessions
    token = create_token("member")
    with Session(engine) as session:
        session.add(RevokedToken(token_hash="old", expires_at=datetime.utcnow() - timedelta(minutes=1)))
        session.commit()
        revoke_token(token, session)
        revoke_token(token, session)
        assert session.get(RevokedToken, "old") is None
        assert session.get(RevokedToken, token_digest(token))


def test_login_normalizes_email_and_does_not_reuse_revoked_session(sessions):
    client, _, _ = sessions
    first = client.post("/auth/login", json={"email": " MEMBER@EXAMPLE.COM ", "password": "valid-password"})
    assert first.status_code == 200
    token = first.json()["access_token"]
    assert client.post("/auth/logout", headers=bearer(token)).status_code == 200
    second = client.post("/auth/login", json={"email": "member@example.com", "password": "valid-password"})
    assert second.status_code == 200 and second.json()["access_token"] != token
    assert client.get("/auth/me", headers=bearer(second.json()["access_token"])).status_code == 200


def test_registration_rejects_case_duplicate_and_bounds_name(sessions):
    client, _, _ = sessions
    data = {"name": "Test", "email": " MEMBER@example.com ", "password": "valid-password"}
    assert client.post("/auth/register", json=data).status_code == 400
    for name in ("   ", "a" * 81):
        assert client.post("/auth/register", json={**data, "name": name, "email": "new@example.com"}).status_code == 422


@pytest.mark.parametrize("expiry", [None, [], "9999999999", float("inf"), 10**18])
def test_expiry_types_cannot_reach_revocation_storage(expiry):
    from app.services.auth import jwt
    from app.config import settings
    token = jwt.encode({"sub": "member", "exp": expiry}, settings.jwt_secret, algorithm="HS256")
    assert decode_token(token) is None


def test_pre_jti_legacy_tokens_are_revocable_without_reissuing_them(sessions):
    from app.services.auth import jwt
    from app.config import settings
    client, _, _ = sessions
    token = jwt.encode({"sub": "member", "exp": datetime.utcnow() + timedelta(minutes=5)}, settings.jwt_secret, algorithm="HS256")
    assert client.get("/auth/me", headers=bearer(token)).status_code == 200
    assert client.post("/auth/logout", headers=bearer(token)).status_code == 200
    assert client.get("/auth/me", headers=bearer(token)).status_code == 401


def test_real_login_endpoint_counts_failures_before_password_work(sessions):
    client, _, app = sessions
    app.state.auth_rate_limiter = AuthRateLimiter(account_failure_limit=2)
    data = {"email": "member@example.com", "password": "wrong-password"}
    for _ in range(2):
        assert client.post("/auth/login", json=data).status_code == 401
    response = client.post("/auth/login", json={**data, "password": "valid-password"})
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0


def test_legacy_case_aliases_fail_closed_until_operator_reconciles_accounts(sessions):
    client, engine, _ = sessions
    with Session(engine) as session:
        session.add(User(id="alias", name="Alias", email="MEMBER@example.com", password_hash=hash_password("other-password")))
        session.commit()
    for password in ("valid-password", "other-password"):
        assert client.post("/auth/login", json={"email": "member@example.com", "password": password}).status_code == 401
