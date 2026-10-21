"""Account recovery, factor verification and epoch-based session revocation.

These secrets authenticate an account. They are never E2EE content keys.
"""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import struct
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse, quote

from fastapi import HTTPException
from nacl.exceptions import CryptoError
from nacl.secret import SecretBox
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import delete, select

from ..config import settings
from ..models.account_security import (
    AccountSecurity, AccountToken, AccountRecoveryCode, AccountRateBucket,
    MFAChallenge, MailOutbox,
)
from ..models.session import ActiveSession
from ..models.user import User


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _secret_key() -> bytes:
    configured = getattr(settings, "account_security_key", None)
    raw = configured.get_secret_value() if hasattr(configured, "get_secret_value") else configured
    if raw:
        try:
            key = base64.b64decode(raw, validate=True)
        except (ValueError, TypeError):
            raise HTTPException(503, "Account security key is invalid") from None
        if len(key) != SecretBox.KEY_SIZE:
            raise HTTPException(503, "Account security key is invalid")
        return key
    if settings.environment == "production":
        raise HTTPException(503, "Account security is not configured on this server")
    path = Path(getattr(settings, "account_security_key_file", "./data/account-security.key"))
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "wb") as stream:
            stream.write(secrets.token_bytes(SecretBox.KEY_SIZE))
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise HTTPException(503, "Account security key file must be private")
            key = stream.read(SecretBox.KEY_SIZE + 1)
    except OSError:
        raise HTTPException(503, "Account security key is unavailable") from None
    if len(key) != SecretBox.KEY_SIZE:
        raise HTTPException(503, "Account security key is invalid")
    return key


def seal_private(value: str, purpose: str, subject: str) -> str:
    body = json.dumps({"version": 1, "purpose": purpose, "subject": subject, "value": value}, separators=(",", ":")).encode()
    return "fcas1:" + base64.b64encode(bytes(SecretBox(_secret_key()).encrypt(body))).decode()


def unseal_private(value: str, purpose: str, subject: str) -> str:
    try:
        if not value.startswith("fcas1:"):
            raise ValueError()
        body = json.loads(SecretBox(_secret_key()).decrypt(base64.b64decode(value[6:], validate=True)))
        if body.get("version") != 1 or body.get("purpose") != purpose or body.get("subject") != subject or not isinstance(body.get("value"), str):
            raise ValueError()
        return body["value"]
    except (ValueError, TypeError, KeyError, CryptoError, json.JSONDecodeError):
        raise HTTPException(503, "Account security material could not be opened") from None


def state_for(session, user_id: str) -> AccountSecurity:
    state = session.get(AccountSecurity, user_id)
    if state:
        return state
    state = AccountSecurity(user_id=user_id)
    session.add(state)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        state = session.get(AccountSecurity, user_id)
        if state is None:
            raise
    return state


def account_limit(session, scope: str, subject: str, limit: int, seconds: int) -> None:
    """Persistent bounded-time buckets; addresses and account identifiers are hashed."""
    now = datetime.utcnow()
    key = digest(scope + "\0" + subject)
    session.exec(delete(AccountRateBucket).where(AccountRateBucket.expires_at <= now))
    bucket = session.get(AccountRateBucket, key)
    if not bucket:
        bucket = AccountRateBucket(key=key, started_at=now, attempts=0, expires_at=now + timedelta(seconds=seconds))
        session.add(bucket)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
    result = session.exec(update(AccountRateBucket).where(AccountRateBucket.key == key, AccountRateBucket.attempts < limit).values(attempts=AccountRateBucket.attempts + 1))
    if result.rowcount != 1:
        session.rollback()
        raise HTTPException(429, "Too many security requests. Please try again later.", headers={"Retry-After": str(seconds)})
    session.commit()


def revoke_all_sessions(session, user_id: str, expected_version: int | None = None) -> int:
    state = state_for(session, user_id)
    query = update(AccountSecurity).where(AccountSecurity.user_id == user_id)
    if expected_version is not None:
        query = query.where(AccountSecurity.auth_version == expected_version)
    changed = session.exec(query.values(auth_version=AccountSecurity.auth_version + 1,
                                      totp_pending_secret=None, totp_pending_expires_at=None))
    if changed.rowcount != 1:
        raise HTTPException(400, "This security request is no longer valid")
    now = datetime.utcnow()
    session.exec(update(ActiveSession).where(ActiveSession.user_id == user_id, ActiveSession.revoked_at.is_(None)).values(revoked_at=now))
    # Unused recovery links and unfinished password-first MFA challenges become
    # invalid immediately; the version check also covers concurrent requests.
    session.exec(update(AccountToken).where(AccountToken.user_id == user_id, AccountToken.used_at.is_(None)).values(used_at=now))
    session.exec(update(MFAChallenge).where(MFAChallenge.user_id == user_id, MFAChallenge.used_at.is_(None)).values(used_at=now))
    session.flush()
    session.refresh(state)
    return state.auth_version


def issue_account_token(session, user: User, purpose: str, minutes: int) -> str:
    from .workforce_access import require_personal_account
    require_personal_account(session, user.id)
    state = state_for(session, user.id)
    now = datetime.utcnow()
    token = secrets.token_urlsafe(32)
    session.exec(update(AccountToken).where(AccountToken.user_id == user.id, AccountToken.purpose == purpose, AccountToken.used_at.is_(None)).values(used_at=now))
    session.add(AccountToken(token_hash=digest(token), user_id=user.id, purpose=purpose, auth_version=state.auth_version,
                             target_email=user.email, expires_at=now + timedelta(minutes=minutes)))
    return token


def account_mail_base_url() -> str:
    """Validate configuration before looking up a recovery email address."""
    _secret_key()
    base = getattr(settings, "public_app_url", "http://127.0.0.1:3000").rstrip("/")
    try:
        parsed = urlparse(base)
        valid = parsed.scheme in ("http", "https") and parsed.hostname and not (parsed.query or parsed.fragment or parsed.username or parsed.password)
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(503, "The public application URL is not configured correctly")
    if settings.environment == "production" and parsed.scheme != "https":
        raise HTTPException(503, "The public application URL requires HTTPS")
    return base


def queue_account_email(session, user: User, purpose: str) -> None:
    from .mail import enqueue_mail
    base = account_mail_base_url()
    if purpose == "email-verify":
        token = issue_account_token(session, user, purpose, 24 * 60)
        fragment, title, instruction = "verify-email", "Verify your FreeCollab email", "Verify the email address for your FreeCollab account. This link expires in 24 hours."
    else:
        token = issue_account_token(session, user, "password-reset", 30)
        fragment, title, instruction = "reset-password", "Reset your FreeCollab password", "Reset your FreeCollab account password. This link expires in 30 minutes."
    link = f"{base}/#{fragment}={quote(token, safe='')}"
    body = f"{instruction}\n\n{link}\n\nIf you did not request this, you can ignore this message.\n\nAn account password reset does not restore encrypted message keys or history."
    enqueue_mail(session, user.email, title, body)


def valid_account_token(session, token: str, purpose: str):
    row = session.get(AccountToken, digest(token))
    if not row or row.purpose != purpose or row.used_at or row.expires_at <= datetime.utcnow():
        raise HTTPException(400, "This link is invalid or has expired")
    user = session.get(User, row.user_id)
    from .workforce_access import is_managed
    if is_managed(session, row.user_id):
        raise HTTPException(400, "This link is invalid or has expired")
    state = session.get(AccountSecurity, row.user_id)
    if not user or user.is_blocked or not state or row.auth_version != state.auth_version or row.target_email != user.email:
        raise HTTPException(400, "This link is invalid or has expired")
    return row, user, state


def consume_account_token(session, row: AccountToken) -> None:
    result = session.exec(update(AccountToken).where(AccountToken.token_hash == row.token_hash, AccountToken.used_at.is_(None), AccountToken.expires_at > datetime.utcnow()).values(used_at=datetime.utcnow()))
    if result.rowcount != 1:
        session.rollback()
        raise HTTPException(400, "This link is invalid or has expired")


def totp_at(secret: str, counter: int, digits: int = 6) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 15
    value = (struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7fffffff) % (10 ** digits)
    return str(value).zfill(digits)


def match_totp(secret: str, code: str, at: float | None = None) -> int | None:
    if not re.fullmatch(r"[0-9]{6}", code):
        return None
    counter = int((time.time() if at is None else at) // 30)
    for candidate in (counter, counter - 1, counter + 1):
        if candidate >= 0 and hmac.compare_digest(totp_at(secret, candidate), code):
            return candidate
    return None


def recovery_hash(code: str) -> str:
    return digest(re.sub(r"[\s-]", "", code).upper())


def replace_recovery_codes(session, user_id: str) -> list[str]:
    session.exec(delete(AccountRecoveryCode).where(AccountRecoveryCode.user_id == user_id))
    codes = []
    for _ in range(10):
        raw = secrets.token_hex(8).upper()
        code = "-".join(raw[i:i + 4] for i in range(0, len(raw), 4))
        session.add(AccountRecoveryCode(user_id=user_id, code_hash=recovery_hash(code)))
        codes.append(code)
    return codes


def verify_factor(session, state: AccountSecurity, code: str, *, prevent_totp_replay: bool = False) -> bool:
    if not state.totp_secret:
        return False
    secret = unseal_private(state.totp_secret, "totp", state.user_id)
    counter = match_totp(secret, code)
    if counter is not None:
        if prevent_totp_replay:
            changed = session.exec(update(AccountSecurity).where(AccountSecurity.user_id == state.user_id, AccountSecurity.totp_last_counter < counter).values(totp_last_counter=counter))
            return changed.rowcount == 1
        return True
    used = session.exec(update(AccountRecoveryCode).where(AccountRecoveryCode.user_id == state.user_id,
                        AccountRecoveryCode.code_hash == recovery_hash(code), AccountRecoveryCode.used_at.is_(None)).values(used_at=datetime.utcnow()))
    return used.rowcount == 1


def ensure_reauthentication(session, user: User, password: str, code: str | None = None) -> AccountSecurity:
    from .workforce_access import require_personal_account
    require_personal_account(session, user.id)
    from .auth import verify_password
    state = state_for(session, user.id)
    if not verify_password(password, user.password_hash):
        raise HTTPException(403, "Current password is incorrect")
    if state.totp_secret and (not code or not verify_factor(session, state, code)):
        raise HTTPException(403, "A valid authenticator or recovery code is required")
    return state


def create_mfa_challenge(session, user: User, request, *, expected_auth_version: int | None = None) -> dict:
    state = state_for(session, user.id)
    version = state.auth_version if expected_auth_version is None else expected_auth_version
    if state.auth_version != version:
        raise HTTPException(401, "Account security changed. Please sign in again.")
    raw = secrets.token_urlsafe(32)
    session.add(MFAChallenge(token_hash=digest(raw), user_id=user.id, auth_version=version,
                            expires_at=datetime.utcnow() + timedelta(minutes=5),
                            user_agent=request.headers.get("user-agent", "Unknown browser")[:256],
                            client_address=request.client.host[:64] if request.client else ""))
    session.commit()
    return {"mfa_required": True, "challenge_token": raw, "expires_in": 300}
