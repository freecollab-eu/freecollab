"""Account lifecycle security with isolated DBs and a private local mail sink."""
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from email import policy
from email.parser import BytesParser
import json
import re
import stat
import threading
import time

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api import account, auth
from app.config import settings
from app.database import get_session
from app.models.account_security import AccountRecoveryCode, AccountSecurity, AccountToken, MailOutbox, MFAChallenge
from app.models.session import ActiveSession
from app.models.user import User
from app.services import account_security as security_service, mail
from app.services.account_security import digest, issue_account_token, recovery_hash, seal_private, state_for, totp_at, unseal_private
from app.services.auth import active_user_for_token, create_token, decode_token_claims, hash_password, token_digest
from app.services.rate_limit import install_auth_rate_limits

PASSWORD = "valid-password"
NEW_PASSWORD = "new-strong-password"


def bearer(token):
    return {"Authorization": "Bearer " + token}


def make_app(engine):
    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(account.router)
    install_auth_rate_limits(app)
    def dependency():
        with Session(engine) as session:
            yield session
    app.dependency_overrides[get_session] = dependency
    return app


@pytest.fixture
def security(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "environment", "test")
    monkeypatch.setattr(settings, "account_security_key", SecretStr(base64.b64encode(b"\x91" * 32).decode()))
    monkeypatch.setattr(settings, "account_security_key_file", str(tmp_path / "account.key"))
    monkeypatch.setattr(settings, "mail_delivery_mode", "file")
    monkeypatch.setattr(settings, "mail_file_path", str(tmp_path / "mail"))
    monkeypatch.setattr(settings, "public_app_url", "http://testserver")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(User(id="member", name="Member", email="member@example.com", password_hash=hash_password(PASSWORD), encrypted_private_key="legacy-content-key"))
        session.commit()
    with TestClient(make_app(engine)) as client:
        yield client, engine, tmp_path
    engine.dispose()


def login(client, *, password=PASSWORD, agent="Security test browser"):
    response = client.post("/auth/login", json={"email": "member@example.com", "password": password}, headers={"User-Agent": agent})
    assert response.status_code == 200, response.text
    return response.json()


def latest_link(engine, fragment):
    with Session(engine) as session:
        rows = session.exec(select(MailOutbox).order_by(MailOutbox.created_at.desc())).all()
        for row in rows:
            payload = json.loads(unseal_private(row.encrypted_message, "outbox", row.id))
            match = re.search(r"#" + fragment + r"=([A-Za-z0-9_-]+)", payload["body"])
            if match:
                return match.group(1)
    raise AssertionError("Expected account instructions to be queued")


def enroll(client, engine):
    old_token = login(client)["access_token"]
    response = client.post("/account/mfa/setup", headers=bearer(old_token), json={"password": PASSWORD})
    assert response.status_code == 200, response.text
    secret = response.json()["secret"]
    code = totp_at(secret, int(time.time() // 30))
    response = client.post("/account/mfa/confirm", headers=bearer(old_token), json={"code": code})
    assert response.status_code == 200, response.text
    return old_token, secret, response.json()


def test_email_verification_is_one_use_and_outbox_is_encrypted(security):
    client, engine, tmp_path = security
    token = login(client)["access_token"]
    assert client.get("/account/security", headers=bearer(token)).json()["email_verified"] is False
    assert client.post("/account/verification/request", headers=bearer(token)).status_code == 200
    raw = latest_link(engine, "verify-email")
    with Session(engine) as session:
        stored = session.get(AccountToken, digest(raw))
        outbox = session.exec(select(MailOutbox)).one()
        assert stored.purpose == "email-verify"
        assert raw not in str(stored.model_dump()) + str(outbox.model_dump())
        assert "Verify your" not in outbox.encrypted_message
        assert mail.deliver_pending(session) == 1
        assert mail.deliver_pending(session) == 0
    directory = tmp_path / "mail"
    delivered = list(directory.glob("*.eml"))
    assert len(delivered) == 1
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(delivered[0].stat().st_mode) == 0o600
    message = BytesParser(policy=policy.default).parsebytes(delivered[0].read_bytes())
    assert raw in message.get_body(preferencelist=("plain",)).get_content()
    assert client.post("/account/verification/confirm", json={"token": raw}).json() == {"verified": True}
    assert client.post("/account/verification/confirm", json={"token": raw}).status_code == 400
    assert client.get("/account/security", headers=bearer(token)).json()["email_verified"] is True


def test_forgot_password_reply_does_not_enumerate_and_reset_revokes_every_token(security):
    client, engine, _ = security
    first = login(client)["access_token"]
    second = login(client, agent="Another browser")["access_token"]
    untracked = create_token("member")
    known = client.post("/auth/forgot-password", json={"email": " MEMBER@EXAMPLE.COM "})
    unknown = client.post("/auth/forgot-password", json={"email": "missing@example.com"})
    assert known.status_code == unknown.status_code == 200 and known.json() == unknown.json()
    with Session(engine) as session:
        assert len(session.exec(select(MailOutbox)).all()) == 1
    raw = latest_link(engine, "reset-password")
    response = client.post("/auth/reset-password", json={"token": raw, "new_password": NEW_PASSWORD})
    assert response.status_code == 200 and response.json()["requires_login"]
    for old in (first, second, untracked):
        assert client.get("/auth/me", headers=bearer(old)).status_code == 401
        with Session(engine) as session:
            assert active_user_for_token(old, session) is None
    assert client.post("/auth/reset-password", json={"token": raw, "new_password": "another-valid-password"}).status_code == 400
    assert client.post("/auth/login", json={"email": "member@example.com", "password": PASSWORD}).status_code == 401
    fresh = login(client, password=NEW_PASSWORD)
    assert fresh["encrypted_private_key"] == "legacy-content-key"
    assert decode_token_claims(fresh["access_token"])["av"] == 1


def test_new_reset_request_invalidates_older_links_and_links_expire(security):
    client, engine, _ = security
    client.post("/auth/forgot-password", json={"email": "member@example.com"})
    first = latest_link(engine, "reset-password")
    client.post("/auth/forgot-password", json={"email": "member@example.com"})
    second = latest_link(engine, "reset-password")
    assert first != second
    assert client.post("/auth/reset-password", json={"token": first, "new_password": NEW_PASSWORD}).status_code == 400
    with Session(engine) as session:
        row = session.get(AccountToken, digest(second))
        row.expires_at = datetime.utcnow() - timedelta(seconds=1)
        session.add(row)
        session.commit()
    assert client.post("/auth/reset-password", json={"token": second, "new_password": NEW_PASSWORD}).status_code == 400


def test_unavailable_mail_reply_is_uniform_and_verification_does_not_claim_success(security, monkeypatch):
    client, engine, _ = security
    token = login(client)["access_token"]
    monkeypatch.setattr(settings, "mail_delivery_mode", "disabled")
    responses = [client.post("/auth/forgot-password", json={"email": email}) for email in ("member@example.com", "absent@example.com")]
    assert responses[0].status_code == responses[1].status_code == 503
    assert responses[0].json() == responses[1].json()
    assert client.post("/account/verification/request", headers=bearer(token)).status_code == 503
    assert client.get("/account/security", headers=bearer(token)).json()["mail_delivery_available"] is False
    with Session(engine) as session:
        assert not session.exec(select(MailOutbox)).all()


def test_password_change_reauthenticates_and_revokes_rest_and_socket_sessions(security):
    client, engine, _ = security
    first, second = login(client)["access_token"], login(client)["access_token"]
    bad = client.post("/account/password", headers=bearer(first), json={"current_password": "wrong-password", "new_password": NEW_PASSWORD})
    assert bad.status_code == 403
    assert client.get("/auth/me", headers=bearer(first)).status_code == 200
    for password in ("short", "é" * 37, "has-null\x00password"):
        assert client.post("/account/password", headers=bearer(first), json={"current_password": PASSWORD, "new_password": password}).status_code == 422
    changed = client.post("/account/password", headers=bearer(first), json={"current_password": PASSWORD, "new_password": NEW_PASSWORD})
    assert changed.status_code == 200 and changed.json()["content_recovery_separate"]
    for old in (first, second):
        assert client.get("/auth/me", headers=bearer(old)).status_code == 401
        with Session(engine) as session:
            assert active_user_for_token(old, session) is None
    assert login(client, password=NEW_PASSWORD)["access_token"]


def test_session_listing_revokes_only_owned_session_and_requires_password(security):
    client, engine, _ = security
    first, second = login(client, agent="Browser A")["access_token"], login(client, agent="Browser B")["access_token"]
    rows = client.get("/account/sessions", headers=bearer(first)).json()
    assert len(rows) == 2 and sum(row["current"] for row in rows) == 1
    assert {row["user_agent"] for row in rows} == {"Browser A", "Browser B"}
    assert "token_hash" not in str(rows) and first not in str(rows)
    other = next(row for row in rows if not row["current"])
    assert client.request("DELETE", "/account/sessions/" + other["id"], headers=bearer(first), json={"password": "wrong-password"}).status_code == 403
    response = client.request("DELETE", "/account/sessions/" + other["id"], headers=bearer(first), json={"password": PASSWORD})
    assert response.json() == {"revoked": True, "current": False}
    assert client.get("/auth/me", headers=bearer(first)).status_code == 200
    assert client.get("/auth/me", headers=bearer(second)).status_code == 401
    with Session(engine) as session:
        session.add(User(id="other", name="Other", email="other@example.com", password_hash="unused"))
        session.commit()
        foreign = ActiveSession(user_id="other", token_hash="foreign", expires_at=datetime.utcnow() + timedelta(days=1))
        session.add(foreign)
        session.commit()
        foreign_id = foreign.id
    assert client.request("DELETE", "/account/sessions/" + foreign_id, headers=bearer(first), json={"password": PASSWORD}).status_code == 404
    own = next(row for row in rows if row["current"])
    assert client.request("DELETE", "/account/sessions/" + own["id"], headers=bearer(first), json={"password": PASSWORD}).json()["current"] is True
    assert client.get("/auth/me", headers=bearer(first)).status_code == 401


def test_revoke_all_also_invalidates_legacy_untracked_tokens_and_pending_setup(security):
    client, engine, _ = security
    token, legacy = login(client)["access_token"], create_token("member")
    client.post("/account/mfa/setup", headers=bearer(token), json={"password": PASSWORD})
    response = client.post("/account/sessions/revoke-all", headers=bearer(token), json={"password": PASSWORD})
    assert response.status_code == 200
    assert client.get("/auth/me", headers=bearer(legacy)).status_code == 401
    with Session(engine) as session:
        assert session.get(AccountSecurity, "member").totp_pending_secret is None


@pytest.mark.parametrize("timestamp,expected", [(59, "94287082"), (1111111109, "07081804"), (1111111111, "14050471"), (1234567890, "89005924"), (2000000000, "69279037"), (20000000000, "65353130")])
def test_totp_matches_rfc6238_sha1_vectors(timestamp, expected):
    secret = base64.b32encode(b"12345678901234567890").decode().rstrip("=")
    assert totp_at(secret, timestamp // 30, digits=8) == expected


def test_totp_rejects_non_ascii_digits_without_comparison_errors():
    assert security_service.match_totp("JBSWY3DPEHPK3PXP", "١٢٣٤٥٦") is None


def test_mfa_setup_confirm_challenge_and_recovery_are_one_use(security):
    client, engine, _ = security
    old, secret, enabled = enroll(client, engine)
    token = enabled["session"]["access_token"]
    assert len(enabled["recovery_codes"]) == 10
    assert client.get("/auth/me", headers=bearer(old)).status_code == 401
    assert client.get("/account/security", headers=bearer(token)).json()["mfa_enabled"] is True
    with Session(engine) as session:
        state = session.get(AccountSecurity, "member")
        assert secret not in str(state.model_dump()) and state.totp_pending_secret is None
        codes = session.exec(select(AccountRecoveryCode)).all()
        assert len(codes) == 10
        assert all(code not in str([row.model_dump() for row in codes]) for code in enabled["recovery_codes"])
        assert {row.code_hash for row in codes} == {recovery_hash(code) for code in enabled["recovery_codes"]}
    challenge = login(client)
    assert challenge["mfa_required"] is True and "access_token" not in challenge
    with Session(engine) as session:
        assert challenge["challenge_token"] not in str(session.exec(select(MFAChallenge)).all())
    # Enrollment consumed this time step, preventing replay as a fresh login.
    with Session(engine) as session:
        confirmed_counter = session.get(AccountSecurity, "member").totp_last_counter
    now_code = totp_at(secret, confirmed_counter)
    assert client.post("/auth/mfa/verify", json={"challenge_token": challenge["challenge_token"], "code": now_code}).status_code == 401
    code = enabled["recovery_codes"][0]
    response = client.post("/auth/mfa/verify", json={"challenge_token": challenge["challenge_token"], "code": code.lower().replace("-", " ")})
    assert response.status_code == 200 and response.json()["access_token"]
    assert "otp" in decode_token_claims(response.json()["access_token"])["amr"]
    assert client.post("/auth/mfa/verify", json={"challenge_token": challenge["challenge_token"], "code": enabled["recovery_codes"][1]}).status_code == 401
    another = login(client)
    assert client.post("/auth/mfa/verify", json={"challenge_token": another["challenge_token"], "code": code}).status_code == 401


def test_totp_login_replay_and_challenge_attempt_limit(security, monkeypatch):
    client, engine, _ = security
    _, secret, enabled = enroll(client, engine)
    counter = int(time.time() // 30) + 1
    monkeypatch.setattr(security_service.time, "time", lambda: counter * 30 + 1)
    first = login(client)
    code = totp_at(secret, counter)
    assert client.post("/auth/mfa/verify", json={"challenge_token": first["challenge_token"], "code": code}).status_code == 200
    second = login(client)
    assert client.post("/auth/mfa/verify", json={"challenge_token": second["challenge_token"], "code": code}).status_code == 401
    # Each challenge stops accepting factors after five unsuccessful attempts.
    for _ in range(4):
        assert client.post("/auth/mfa/verify", json={"challenge_token": second["challenge_token"], "code": "invalid"}).status_code == 401
    assert client.post("/auth/mfa/verify", json={"challenge_token": second["challenge_token"], "code": enabled["recovery_codes"][0]}).status_code == 401


def test_mfa_disable_and_backup_regeneration_require_password_and_factor(security):
    client, engine, _ = security
    _, _, enabled = enroll(client, engine)
    token = enabled["session"]["access_token"]
    assert client.post("/account/mfa/disable", headers=bearer(token), json={"password": PASSWORD}).status_code == 403
    assert client.post("/account/mfa/disable", headers=bearer(token), json={"password": "wrong-password", "code": enabled["recovery_codes"][0]}).status_code == 403
    regenerated = client.post("/account/mfa/recovery-codes", headers=bearer(token), json={"password": PASSWORD, "code": enabled["recovery_codes"][0]})
    assert regenerated.status_code == 200
    new_codes = regenerated.json()["recovery_codes"]
    assert not set(new_codes).intersection(enabled["recovery_codes"])
    assert client.post("/account/mfa/disable", headers=bearer(token), json={"password": PASSWORD, "code": enabled["recovery_codes"][1]}).status_code == 403
    disabled = client.post("/account/mfa/disable", headers=bearer(token), json={"password": PASSWORD, "code": new_codes[0]})
    assert disabled.status_code == 200 and disabled.json()["enabled"] is False
    assert client.get("/auth/me", headers=bearer(token)).status_code == 401
    assert client.get("/auth/me", headers=bearer(disabled.json()["session"]["access_token"])).status_code == 200
    assert "access_token" in login(client)
    with Session(engine) as session:
        assert not session.exec(select(AccountRecoveryCode)).all()


def test_account_reset_preserves_enabled_mfa_and_invalidates_password_first_challenge(security):
    client, engine, _ = security
    _, _, enabled = enroll(client, engine)
    challenge = login(client)
    client.post("/auth/forgot-password", json={"email": "member@example.com"})
    raw = latest_link(engine, "reset-password")
    assert client.post("/auth/reset-password", json={"token": raw, "new_password": NEW_PASSWORD}).status_code == 200
    assert client.post("/auth/mfa/verify", json={"challenge_token": challenge["challenge_token"], "code": enabled["recovery_codes"][0]}).status_code == 401
    assert login(client, password=NEW_PASSWORD)["mfa_required"]


def test_mail_retries_persist_without_sensitive_exception_strings(security, monkeypatch):
    _, engine, _ = security
    monkeypatch.setattr(settings, "mail_delivery_mode", "smtp")
    monkeypatch.setattr(settings, "smtp_host", "smtp.invalid")
    def fail(_):
        raise RuntimeError("secret-password-and-token-must-not-persist")
    monkeypatch.setattr(mail, "_deliver_smtp", fail)
    with Session(engine) as session:
        row = mail.enqueue_mail(session, "member@example.com", "Subject", "Private token body")
        session.commit()
        identifier = row.id
        assert mail.deliver_pending(session) == 0
    with Session(engine) as session:
        row = session.get(MailOutbox, identifier)
        assert row.status == "pending" and row.attempts == 1 and row.delivered_at is None
        assert "secret-password" not in row.failure
        row.next_attempt_at = datetime.utcnow() - timedelta(seconds=1)
        session.add(row)
        session.commit()
    delivered = []
    monkeypatch.setattr(mail, "_deliver_smtp", lambda message: delivered.append(message))
    with Session(engine) as session:
        assert mail.deliver_pending(session) == 1
        assert session.get(MailOutbox, identifier).status == "delivered"
        assert len(delivered) == 1


def test_file_mail_crash_replay_is_idempotent_and_partial_file_is_not_success(security):
    _, engine, tmp_path = security
    with Session(engine) as session:
        row = mail.enqueue_mail(session, "member@example.com", "A subject", "A body")
        session.commit()
        message = mail._message(row)
        mail._deliver_file(row, message)
        mail._deliver_file(row, message)
        target = tmp_path / "mail" / (row.id + ".eml")
        target.write_bytes(b"incomplete")
        assert mail.deliver_pending(session) == 0
        assert session.get(MailOutbox, row.id).status == "pending"


def test_private_material_is_bound_to_purpose_user_and_server_key(security, monkeypatch):
    encrypted = seal_private("TOP-SECRET", "totp", "member")
    assert "TOP-SECRET" not in encrypted
    assert unseal_private(encrypted, "totp", "member") == "TOP-SECRET"
    for purpose, user in (("totp-pending", "member"), ("totp", "someone-else")):
        with pytest.raises(HTTPException):
            unseal_private(encrypted, purpose, user)
    monkeypatch.setattr(settings, "account_security_key", SecretStr(base64.b64encode(b"\x92" * 32).decode()))
    with pytest.raises(HTTPException):
        unseal_private(encrypted, "totp", "member")


def test_production_refuses_file_delivery_and_unconfigured_account_secret(security, monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "account_security_key", None)
    assert not mail.mail_available()
    with pytest.raises(HTTPException) as error:
        seal_private("secret", "totp", "member")
    assert error.value.status_code == 503


@pytest.mark.parametrize("misconfiguration", ["key", "url"])
def test_mail_configuration_failure_cannot_enumerate_recovery_accounts(security, monkeypatch, misconfiguration):
    client, _, _ = security
    if misconfiguration == "key":
        monkeypatch.setattr(settings, "account_security_key", SecretStr("invalid-key"))
    else:
        monkeypatch.setattr(settings, "public_app_url", "https://[invalid")
    responses = [client.post("/auth/forgot-password", json={"email": address}) for address in ("member@example.com", "absent@example.com")]
    assert responses[0].status_code == responses[1].status_code == 503
    assert responses[0].json() == responses[1].json()


def test_development_key_file_is_private_persistent_and_symlink_safe(security, monkeypatch):
    _, _, tmp_path = security
    monkeypatch.setattr(settings, "account_security_key", None)
    encrypted = seal_private("persistent", "totp", "member")
    keyfile = tmp_path / "account.key"
    assert keyfile.exists() and stat.S_IMODE(keyfile.stat().st_mode) == 0o600
    assert unseal_private(encrypted, "totp", "member") == "persistent"
    keyfile.chmod(0o644)
    with pytest.raises(HTTPException):
        seal_private("secret", "totp", "member")
    keyfile.unlink()
    target = tmp_path / "target"
    target.write_bytes(b"x" * 32)
    keyfile.symlink_to(target)
    with pytest.raises(HTTPException):
        seal_private("secret", "totp", "member")


def test_security_email_throttle_is_persistent_and_does_not_disclose_accounts(security):
    client, _, _ = security
    for _ in range(3):
        assert client.post("/auth/forgot-password", json={"email": "missing@example.com"}).status_code == 200
    response = client.post("/auth/forgot-password", json={"email": "missing@example.com"})
    assert response.status_code == 429 and int(response.headers["Retry-After"]) > 0


@pytest.mark.parametrize("mfa_enabled", [False, True])
def test_stale_password_cannot_mint_token_or_mfa_challenge_after_reset(security, monkeypatch, mfa_enabled):
    client, engine, _ = security
    enabled = enroll(client, engine)[2] if mfa_enabled else None
    original_verify = auth.verify_password
    def reset_between_password_check_and_token(password, password_hash):
        valid = original_verify(password, password_hash)
        with Session(engine) as session:
            security_service.revoke_all_sessions(session, "member")
            session.commit()
        return valid
    monkeypatch.setattr(auth, "verify_password", reset_between_password_check_and_token)
    response = client.post("/auth/login", json={"email": "member@example.com", "password": PASSWORD})
    # A token bound to the old epoch is harmless even if a DB identity map holds
    # the previous state; it must never authenticate under the new epoch.
    if response.status_code == 200:
        if mfa_enabled:
            assert client.post("/auth/mfa/verify", json={"challenge_token": response.json()["challenge_token"], "code": enabled["recovery_codes"][0]}).status_code == 401
        else:
            assert client.get("/auth/me", headers=bearer(response.json()["access_token"])).status_code == 401
    else:
        assert response.status_code == 401


def test_reset_token_has_a_single_winner_under_concurrent_requests(security, monkeypatch):
    _, _, tmp_path = security
    engine = create_engine("sqlite:///" + str(tmp_path / "concurrent.db"), connect_args={"check_same_thread": False, "timeout": 15})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        user = User(id="member", name="Member", email="member@example.com", password_hash=hash_password(PASSWORD))
        session.add(user)
        session.commit()
        raw = issue_account_token(session, user, "password-reset", 30)
        session.commit()
    barrier = threading.Barrier(2)
    prepared_hash = hash_password(NEW_PASSWORD)
    def coordinated_hash(_):
        barrier.wait(timeout=10)
        return prepared_hash
    monkeypatch.setattr(account, "hash_password", coordinated_hash)
    app = make_app(engine)
    def reset():
        with TestClient(app) as client:
            return client.post("/auth/reset-password", json={"token": raw, "new_password": NEW_PASSWORD}).status_code
    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(lambda _: reset(), range(2)))
    assert sorted(statuses) == [200, 400]
    with Session(engine) as session:
        assert session.get(AccountSecurity, "member").auth_version == 1
    engine.dispose()
