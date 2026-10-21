"""Authentication regressions using a private database per test."""
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
import jwt
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api import admin, auth
from app.cli import bootstrap_admin
from app.config import settings
from app.database import get_session
from app.models.user import User
from app.services.auth import create_token, decode_token, hash_password, verify_password


@pytest.fixture
def auth_context():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        user = User(
            id="registered-user", name="Member", email="member@example.com",
            password_hash=hash_password("valid-password"),
        )
        session.add(user)
        session.commit()

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(admin.router)

    def session_override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    with TestClient(app) as client:
        yield client, engine
    engine.dispose()


def headers(user_id="registered-user"):
    return {"Authorization": "Bearer " + create_token(user_id)}


def test_blocking_revokes_existing_token_and_login(auth_context):
    client, engine = auth_context
    token_headers = headers()
    assert client.get("/auth/me", headers=token_headers).status_code == 200
    with Session(engine) as session:
        user = session.get(User, "registered-user")
        user.is_blocked = True
        session.add(user)
        session.commit()
    assert client.get("/auth/me", headers=token_headers).status_code == 403
    assert client.get("/admin/me", headers=token_headers).status_code == 403
    response = client.post("/auth/login", json={
        "email": "member@example.com", "password": "valid-password",
    })
    assert response.status_code == 403
    assert "access_token" not in response.json()


def test_deleted_account_token_is_rejected(auth_context):
    client, engine = auth_context
    with Session(engine) as session:
        session.delete(session.get(User, "registered-user"))
        session.commit()
    assert client.get("/auth/me", headers=headers()).status_code == 401


@pytest.mark.parametrize("subject", [None, "", "   ", [], {}, 123])
def test_malformed_subject_is_rejected(subject, auth_context):
    client, _ = auth_context
    token = jwt.encode(
        {"sub": subject, "exp": datetime.utcnow() + timedelta(minutes=5)},
        settings.jwt_secret, algorithm="HS256",
    )
    assert decode_token(token) is None
    assert client.get("/auth/me", headers={"Authorization": "Bearer " + token}).status_code == 401


def test_tokens_require_subject_and_expiration(auth_context):
    client, _ = auth_context
    for payload in ({"sub": "registered-user"}, {"exp": 9999999999}):
        token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
        assert client.get("/auth/me", headers={"Authorization": "Bearer " + token}).status_code == 401


@pytest.mark.parametrize("token", [None, 123, [], {}, ""])
def test_non_string_tokens_are_rejected(token):
    assert decode_token(token) is None


def test_http_bootstrap_cannot_promote_first_registered_user(auth_context):
    client, engine = auth_context
    assert client.post("/admin/setup-first-admin", headers=headers()).status_code == 404
    with Session(engine) as session:
        assert session.get(User, "registered-user").role == "user"


def test_operator_bootstrap_only_promotes_existing_account_once(auth_context):
    _, engine = auth_context
    with Session(engine) as session:
        with pytest.raises(ValueError, match="Register"):
            bootstrap_admin("missing@example.com", session)
        bootstrap_admin("member@example.com", session)
        assert session.get(User, "registered-user").role == "admin"
        with pytest.raises(ValueError, match="already exists"):
            bootstrap_admin("member@example.com", session)


def test_operator_cannot_promote_blocked_account(auth_context):
    _, engine = auth_context
    with Session(engine) as session:
        user = session.get(User, "registered-user")
        user.is_blocked = True
        session.add(user)
        session.commit()
        with pytest.raises(ValueError, match="blocked"):
            bootstrap_admin("member@example.com", session)
        assert user.role == "user"


@pytest.mark.parametrize("password", ["", "a" * 73, "é" * 37, "bad\x00password"])
def test_invalid_bcrypt_inputs_rejected_on_all_creation_and_login_paths(password, auth_context):
    client, engine = auth_context
    with Session(engine) as session:
        bootstrap_admin("member@example.com", session)
    payload = {"name": "New", "email": "new@example.com", "password": password}
    assert client.post("/auth/register", json=payload).status_code == 422
    assert client.post("/auth/login", json=payload).status_code == 422
    assert client.post("/admin/users", headers=headers(), json=payload).status_code == 422
    with pytest.raises(ValueError):
        hash_password(password)


def test_password_limit_is_bytes_and_does_not_accept_truncated_suffix():
    password = "é" * 36
    stored = hash_password(password)
    assert verify_password(password, stored)
    assert not verify_password(password + "suffix", stored)
    assert not verify_password("anything", "corrupt-hash")


def test_admin_creation_uses_same_canonical_email_as_registration_and_login(auth_context):
    client, engine = auth_context
    with Session(engine) as session:
        bootstrap_admin("member@example.com", session)
    user = {"name": "New Member", "email": " NEW@EXAMPLE.COM ", "password": "new-password"}
    created = client.post("/admin/users", headers=headers(), json=user)
    assert created.status_code == 200
    with Session(engine) as session:
        assert session.get(User, created.json()["id"]).email == "new@example.com"
    assert client.post("/auth/register", json={**user, "email": "new@example.com"}).status_code == 400
    assert client.post("/admin/users", headers=headers(), json={**user, "email": "New@example.com"}).status_code == 400
    assert client.post("/auth/login", json={"email": "NEW@example.com", "password": "new-password"}).status_code == 200
