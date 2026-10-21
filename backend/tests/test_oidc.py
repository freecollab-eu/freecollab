"""OIDC flows against a real local HTTP authorization-code/JWKS provider."""
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import threading
from urllib.parse import parse_qs, urlencode, urlsplit

from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import jwt
from jwt.algorithms import RSAAlgorithm
from pydantic import SecretStr
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.api import account, auth, oidc, workspaces
from app.config import settings
from app.database import get_session
from app.models.account_security import AccountSecurity, MFAChallenge
from app.models.oidc import OIDCIdentity, OIDCLoginAttempt
from app.models.session import ActiveSession
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from app.services.account_security import digest, seal_private, totp_at
from app.services.auth import create_token, decode_token_claims, hash_password, revoke_token
from app.services.rate_limit import install_auth_rate_limits

from tests.oidc_mock import LocalProvider

PASSWORD = "OIDC-local-password-2026"


@pytest.fixture
def sso(tmp_path, monkeypatch):
    provider = LocalProvider()
    monkeypatch.setattr(settings, "environment", "test")
    monkeypatch.setattr(settings, "oidc_enabled", True)
    monkeypatch.setattr(settings, "oidc_issuer_url", provider.issuer)
    monkeypatch.setattr(settings, "oidc_redirect_uri", "http://127.0.0.1:3000/api/auth/oidc/callback")
    monkeypatch.setattr(settings, "oidc_client_id", "freecollab-test")
    monkeypatch.setattr(settings, "oidc_client_secret", SecretStr("client-secret"))
    monkeypatch.setattr(settings, "oidc_provider_name", "Test Organization")
    monkeypatch.setattr(settings, "oidc_scopes", "openid profile email")
    monkeypatch.setattr(settings, "oidc_trust_email_claim", False)
    monkeypatch.setattr(settings, "public_app_url", "http://127.0.0.1:3000")
    monkeypatch.setattr(settings, "account_security_key", SecretStr(base64.b64encode(b"\x94" * 32).decode()))
    monkeypatch.setattr(settings, "mail_delivery_mode", "disabled")
    engine = create_engine("sqlite:///" + str(tmp_path / "oidc.db"), connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(account.router)
    app.include_router(oidc.router)
    app.include_router(workspaces.router)
    install_auth_rate_limits(app)

    def dependency():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = dependency
    with TestClient(app, base_url="http://127.0.0.1:3000") as client:
        yield client, provider, engine
    engine.dispose()
    provider.close()


def start(client, token=None, password=PASSWORD):
    response = client.post("/auth/oidc/link/start" if token else "/auth/oidc/start",
                           json={"password": password} if token else {},
                           headers={"Authorization": "Bearer " + token} if token else {})
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()["authorization_url"]


def provider_redirect(authorization):
    with httpx.Client(follow_redirects=False) as client:
        response = client.get(authorization)
        assert response.status_code == 302
        return "/auth/oidc/callback?" + urlsplit(response.headers["location"]).query


def callback(client, authorization):
    response = client.get(provider_redirect(authorization), follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["referrer-policy"] == "no-referrer"
    return response


def finish(client, authorization):
    response = callback(client, authorization)
    assert "/#sso=" in response.headers["location"], response.headers["location"]
    code = response.headers["location"].split("#sso=", 1)[1]
    response = client.post("/auth/oidc/exchange", json={"code": code})
    assert response.status_code == 200, response.text
    return response.json()


def native_user(engine, email="existing@example.com", *, verified=True):
    with Session(engine) as session:
        user = User(id=secrets.token_hex(12), name="Existing User", email=email, password_hash=hash_password(PASSWORD))
        session.add(user)
        session.add(AccountSecurity(user_id=user.id, email_verified_at=datetime.utcnow() if verified else None))
        session.commit()
        return user.id, create_token(user.id, session)


def test_real_code_pkce_jwks_provision_tracks_private_account_and_no_provider_secrets(sso):
    client, provider, engine = sso
    assert client.get("/auth/oidc/providers").json()["providers"][0]["name"] == "Test Organization"
    result = finish(client, start(client))
    token = result["access_token"]
    assert decode_token_claims(token)["amr"] == ["sso"]
    headers = {"Authorization": "Bearer " + token}
    assert client.get("/auth/me", headers=headers).json()["email"] == provider.email
    homes = client.get("/workspaces", headers=headers).json()
    assert len(homes) == 1 and homes[0]["role"] == "owner" and not homes[0]["is_legacy"]
    with Session(engine) as session:
        identity = session.exec(select(OIDCIdentity)).one()
        state = session.get(AccountSecurity, result["user_id"])
        assert state.email_verified_at is not None
        assert identity.subject == provider.subject
        stored = str([r.model_dump() for r in session.exec(select(OIDCLoginAttempt)).all()])
        assert "unused-provider-access-token" not in stored and "code_verifier" not in stored
        assert session.exec(select(ActiveSession)).one().user_id == result["user_id"]
        assert session.exec(select(WorkspaceMember)).one().user_id == result["user_id"]
    assert provider.exchange_count == 1
    assert client.get("/auth/oidc/identities", headers=headers).json()[0]["provider"] == "Test Organization"


def test_identity_is_issuer_subject_never_renames_or_reassigns_on_email_change(sso):
    client, provider, engine = sso
    first = finish(client, start(client))
    provider.email = "changed-at-provider@example.com"
    second = finish(client, start(client))
    assert second["user_id"] == first["user_id"]
    with Session(engine) as session:
        assert len(session.exec(select(User)).all()) == 1
        assert session.get(User, first["user_id"]).email == "oidc-person@example.com"


@pytest.mark.parametrize("claims", [
    {"nonce": "wrong-nonce"}, {"iss": "https://wrong-issuer.example"},
    {"aud": "wrong-client"}, {"exp": 1}, {"iat": 32503680000},
    {"aud": ["freecollab-test", "other"], "azp": "other"},
    {"azp": "other"}, {"sub": ""},
])
def test_signed_but_invalid_id_token_claims_are_rejected(sso, claims):
    client, provider, engine = sso
    provider.claim_overrides = claims
    response = callback(client, start(client))
    assert "#sso_error=sign_in_failed" in response.headers["location"]
    with Session(engine) as session:
        assert not session.exec(select(User)).all()


def test_wrong_signature_unknown_key_and_ambiguous_kid_are_rejected(sso):
    client, provider, engine = sso
    provider.signing_key = provider.other_key
    assert "sso_error" in callback(client, start(client)).headers["location"]
    provider.signing_key = provider.key
    provider.header = {"kid": "unknown"}
    assert "sso_error" in callback(client, start(client)).headers["location"]
    provider.header = {"kid": "test-rsa"}
    extra = json.loads(RSAAlgorithm.to_jwk(provider.key.public_key()))
    extra.update(kid="test-rsa", use="sig", alg="RS256")
    provider.jwks_extra.append(extra)
    assert "sso_error" in callback(client, start(client)).headers["location"]
    with Session(engine) as session:
        assert not session.exec(select(User)).all()


def test_browser_cookie_state_callback_and_handoff_are_one_use(sso):
    client, provider, engine = sso
    authorization = start(client)
    path = provider_redirect(authorization)
    cookies = dict(client.cookies)
    client.cookies.clear()
    assert "sso_error" in client.get(path, follow_redirects=False).headers["location"]
    assert provider.exchange_count == 0
    client.cookies.update(cookies)
    response = client.get(path, follow_redirects=False)
    handoff = response.headers["location"].split("#sso=", 1)[1]
    assert "sso_error" in client.get(path, follow_redirects=False).headers["location"]
    assert provider.exchange_count == 1
    client.cookies.clear()
    assert client.post("/auth/oidc/exchange", json={"code": handoff}).status_code == 400
    client.cookies.update(cookies)
    assert client.post("/auth/oidc/exchange", json={"code": handoff}).status_code == 200
    assert client.post("/auth/oidc/exchange", json={"code": handoff}).status_code == 400


def test_expired_state_and_handoff_fail_closed(sso):
    client, provider, engine = sso
    authorization = start(client)
    state = parse_qs(urlsplit(authorization).query)["state"][0]
    with Session(engine) as session:
        row = session.get(OIDCLoginAttempt, digest(state))
        row.expires_at = datetime.utcnow() - timedelta(seconds=1)
        session.add(row); session.commit()
    assert "sso_error" in callback(client, authorization).headers["location"]
    assert provider.exchange_count == 0
    response = callback(client, start(client))
    handoff = response.headers["location"].split("#sso=", 1)[1]
    with Session(engine) as session:
        row = session.exec(select(OIDCLoginAttempt).where(OIDCLoginAttempt.handoff_hash == digest(handoff))).one()
        row.expires_at = datetime.utcnow() - timedelta(seconds=1)
        session.add(row); session.commit()
    assert client.post("/auth/oidc/exchange", json={"code": handoff}).status_code == 400


def test_existing_email_requires_password_and_verified_native_account_link(sso):
    client, provider, engine = sso
    user_id, token = native_user(engine, provider.email)
    response = callback(client, start(client))
    assert "sso_error=account_link_required" in response.headers["location"]
    with Session(engine) as session:
        assert not session.exec(select(OIDCIdentity)).all()
    assert client.post("/auth/oidc/link/start", headers={"Authorization": "Bearer " + token}, json={"password": "wrong"}).status_code == 403
    linked = finish(client, start(client, token))
    assert linked["linked"] and linked["user_id"] == user_id
    assert client.get("/auth/me", headers={"Authorization": "Bearer " + token}).status_code == 200
    signed_in = finish(client, start(client))
    assert signed_in["user_id"] == user_id


def test_unverified_local_email_cannot_persist_a_link(sso):
    client, provider, engine = sso
    _, token = native_user(engine, verified=False)
    response = client.post("/auth/oidc/link/start", headers={"Authorization": "Bearer " + token}, json={"password": PASSWORD})
    assert response.status_code == 403 and "Verify your" in response.json()["detail"]
    with Session(engine) as session:
        assert not session.exec(select(OIDCIdentity)).all()


def test_unverified_provider_email_requires_explicit_operator_policy(sso, monkeypatch):
    client, provider, engine = sso
    provider.claim_overrides = {"email_verified": False}
    assert "sso_error=email_not_verified" in callback(client, start(client)).headers["location"]
    monkeypatch.setattr(settings, "oidc_trust_email_claim", True)
    assert "access_token" in finish(client, start(client))


def test_link_cannot_move_an_identity_between_accounts(sso):
    client, provider, engine = sso
    first = finish(client, start(client))
    _, token = native_user(engine)
    assert "sso_error=identity_already_linked" in callback(client, start(client, token)).headers["location"]
    with Session(engine) as session:
        assert session.exec(select(OIDCIdentity)).one().user_id == first["user_id"]


def test_logout_during_provider_redirect_invalidates_account_link(sso):
    client, provider, engine = sso
    _, token = native_user(engine)
    authorization = start(client, token)
    with Session(engine) as session:
        revoke_token(token, session)
    assert "sso_error" in callback(client, authorization).headers["location"]
    with Session(engine) as session:
        assert not session.exec(select(OIDCIdentity)).all()


def test_native_security_change_before_exchange_invalidates_handoff(sso):
    client, provider, engine = sso
    response = callback(client, start(client))
    handoff = response.headers["location"].split("#sso=", 1)[1]
    with Session(engine) as session:
        state = session.exec(select(AccountSecurity)).one()
        state.auth_version += 1
        session.add(state); session.commit()
    assert client.post("/auth/oidc/exchange", json={"code": handoff}).status_code == 400


def test_sso_preserves_native_mfa_and_authentication_method(sso):
    client, provider, engine = sso
    user = finish(client, start(client))
    secret = "JBSWY3DPEHPK3PXP"
    with Session(engine) as session:
        state = session.get(AccountSecurity, user["user_id"])
        state.totp_secret = seal_private(secret, "totp", user["user_id"])
        session.add(state); session.commit()
    challenge = finish(client, start(client))
    assert challenge["mfa_required"] is True and "access_token" not in challenge
    bad = client.post("/auth/mfa/verify", json={"challenge_token": challenge["challenge_token"], "code": "wrong"})
    assert bad.status_code == 401
    good = client.post("/auth/mfa/verify", json={"challenge_token": challenge["challenge_token"], "code": totp_at(secret, int(datetime.now(timezone.utc).timestamp() // 30))})
    assert good.status_code == 200, good.text
    assert decode_token_claims(good.json()["access_token"])["amr"] == ["sso", "otp"]
    assert client.post("/auth/mfa/verify", json={"challenge_token": challenge["challenge_token"], "code": "123456"}).status_code == 401


def test_blocked_account_and_changed_provider_configuration_cannot_exchange(sso, monkeypatch):
    client, provider, engine = sso
    first = finish(client, start(client))
    with Session(engine) as session:
        user = session.get(User, first["user_id"])
        user.is_blocked = True
        session.add(user); session.commit()
    assert "sso_error" in callback(client, start(client)).headers["location"]
    with Session(engine) as session:
        user = session.get(User, first["user_id"])
        user.is_blocked = False
        session.add(user); session.commit()
    response = callback(client, start(client))
    handoff = response.headers["location"].split("#sso=", 1)[1]
    monkeypatch.setattr(settings, "oidc_client_id", "new-client")
    assert client.post("/auth/oidc/exchange", json={"code": handoff}).status_code == 400


def test_discovery_cannot_change_issuer_or_downgrade_provider_tls(sso):
    client, provider, engine = sso
    provider.discovery_issuer = "https://different-provider.example"
    assert client.post("/auth/oidc/start").status_code == 503
    provider.discovery_issuer = None
    provider.redirect_endpoint = "http://untrusted.example/token"
    assert client.post("/auth/oidc/start").status_code == 503


def test_concurrent_callback_only_exchanges_authorization_code_once(sso):
    client, provider, engine = sso
    authorization = start(client)
    path = provider_redirect(authorization)
    cookies = dict(client.cookies)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: client.get(path, headers={"Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items())}, follow_redirects=False).headers["location"], range(2)))
    assert sum("/#sso=" in result for result in results) == 1
    assert provider.exchange_count == 1


@pytest.mark.parametrize("algorithm", ["none", "HS256"])
def test_id_token_algorithm_confusion_is_rejected(sso, algorithm):
    client, provider, engine = sso
    provider.token_signer = lambda claims: jwt.encode(claims, "" if algorithm == "none" else "attacker-secret", algorithm=algorithm, headers={"kid": "test-rsa"})
    assert "sso_error=sign_in_failed" in callback(client, start(client)).headers["location"]
    with Session(engine) as session:
        assert not session.exec(select(User)).all()


def test_concurrent_handoff_exchange_creates_only_one_native_session(sso):
    client, provider, engine = sso
    result = callback(client, start(client))
    code = result.headers["location"].split("#sso=", 1)[1]
    cookies = dict(client.cookies)
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _: client.post("/auth/oidc/exchange", json={"code": code}, headers={"Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items())}).status_code, range(2)))
    assert sorted(statuses) == [200, 400]
    with Session(engine) as session:
        assert len(session.exec(select(ActiveSession)).all()) == 1


def test_unlink_requires_own_identity_and_password_then_revokes_all_sessions(sso, monkeypatch):
    client, provider, engine = sso
    user_id, token = native_user(engine, provider.email)
    finish(client, start(client, token))
    other_session = finish(client, start(client))["access_token"]
    headers = {"Authorization": "Bearer " + token}
    identity = client.get("/auth/oidc/identities", headers=headers).json()[0]
    assert client.request("DELETE", "/auth/oidc/identities/" + identity["id"], headers=headers, json={"password": "wrong"}).status_code == 403
    _, outsider_token = native_user(engine)
    outsider = client.request("DELETE", "/auth/oidc/identities/" + identity["id"], headers={"Authorization": "Bearer " + outsider_token}, json={"password": PASSWORD})
    assert outsider.status_code == 404
    # A provider outage/disabled configuration must not prevent safe unlinking.
    monkeypatch.setattr(settings, "oidc_enabled", False)
    result = client.request("DELETE", "/auth/oidc/identities/" + identity["id"], headers=headers, json={"password": PASSWORD})
    assert result.status_code == 200 and result.json()["requires_login"]
    assert client.get("/auth/me", headers=headers).status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Bearer " + other_session}).status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Bearer " + outsider_token}).status_code == 200
    with Session(engine) as session:
        assert not session.exec(select(OIDCIdentity).where(OIDCIdentity.user_id == user_id)).all()
    assert client.post("/auth/login", json={"email": provider.email, "password": PASSWORD}).status_code == 200
