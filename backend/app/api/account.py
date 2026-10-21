"""Account security API. Account recovery is separate from encrypted content."""
from datetime import datetime, timedelta
import base64
import secrets
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import select, delete

from ..database import get_session
from ..models.account_security import AccountSecurity, AccountRecoveryCode, MFAChallenge
from ..models.oidc import OIDCMFAChallenge
from ..models.workspace_oidc import WorkspaceOIDCMFAChallenge
from ..services import workspace_oidc as tenants
from ..models.session import ActiveSession
from ..models.user import User
from ..services.account_security import (
    account_limit, account_mail_base_url, consume_account_token, digest, ensure_reauthentication, match_totp,
    queue_account_email, replace_recovery_codes, revoke_all_sessions,
    seal_private, state_for, unseal_private, valid_account_token, verify_factor,
)
from ..services.auth import create_token, get_current_user, hash_password, normalize_email, security, token_digest, validate_password
from ..services.mail import mail_available, delivery_mode
from ..services.rate_limit import request_address

router = APIRouter(prefix="/account", tags=["account security"])
auth_router = APIRouter(tags=["account security"])
GENERIC_EMAIL_MESSAGE = "If this account can receive instructions, they have been queued for delivery."


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RecoveryRequest(StrictInput):
    email: EmailStr = Field(max_length=320)
    _canonical = field_validator("email", mode="before")(normalize_email)


class TokenInput(StrictInput):
    token: str = Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_-]+$")


class NewPassword(StrictInput):
    new_password: str = Field(min_length=12, max_length=72)
    _password = field_validator("new_password")(validate_password)


class ResetPassword(NewPassword):
    token: str = Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_-]+$")


class ChangePassword(NewPassword):
    current_password: str = Field(min_length=1, max_length=72)
    code: str | None = Field(default=None, max_length=64)
    _current = field_validator("current_password")(validate_password)


class Reauthenticate(StrictInput):
    password: str = Field(min_length=1, max_length=72)
    code: str | None = Field(default=None, max_length=64)
    _password = field_validator("password")(validate_password)


class FactorCode(StrictInput):
    code: str = Field(min_length=1, max_length=64)


class MFAVerify(FactorCode):
    challenge_token: str = Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_-]+$")


def _sensitive_limit(session, request, user_id):
    account_limit(session, "security-address", request_address(request), 30, 300)
    account_limit(session, "security-account", user_id, 15, 300)


def _token_payload(user, session, request=None, *, mfa=False, info=None, auth_version=None, auth_methods=None, workspace_scope=None):
    if workspace_scope:
        tenants.current_config(session, workspace_scope["workspace_id"], workspace_scope["provider_revision"], user_id=user.id, lock=True)
    token = create_token(user.id, session, request, mfa_verified=mfa, session_info=info,
                         expected_auth_version=auth_version, auth_methods=auth_methods, workspace_scope=workspace_scope)
    tenants.register_scoped_session(session, token, workspace_scope)
    return {
        **({"workspace_id": workspace_scope["workspace_id"]} if workspace_scope else {}),
        "access_token": token,
        "token_type": "bearer", "user_id": user.id, "public_key": user.public_key or "",
        "encrypted_private_key": user.encrypted_private_key,
    }


@router.get("/security")
def security_status(user=Depends(get_current_user), session=Depends(get_session)):
    state = state_for(session, user.id)
    count = session.exec(select(func.count()).select_from(ActiveSession).where(ActiveSession.user_id == user.id, ActiveSession.revoked_at.is_(None), ActiveSession.expires_at > datetime.utcnow())).one()
    return {"email": user.email, "email_verified": state.email_verified_at is not None,
            "mfa_enabled": bool(state.totp_secret), "session_count": count,
            "mail_delivery_available": mail_available(), "mail_delivery_mode": delivery_mode(),
            "content_recovery_separate": True}


@router.post("/verification/request")
def request_verification(request: Request, user=Depends(get_current_user), session=Depends(get_session)):
    account_limit(session, "verification-address", request_address(request), 20, 900)
    account_limit(session, "verification-account", user.id, 3, 900)
    state = state_for(session, user.id)
    if not state.email_verified_at:
        queue_account_email(session, user, "email-verify")
        session.commit()
    return {"message": GENERIC_EMAIL_MESSAGE}


@router.post("/verification/confirm")
def confirm_verification(data: TokenInput, request: Request, session=Depends(get_session)):
    account_limit(session, "verify-link-address", request_address(request), 30, 300)
    row, user, state = valid_account_token(session, data.token, "email-verify")
    consume_account_token(session, row)
    changed = session.exec(update(AccountSecurity).where(AccountSecurity.user_id == user.id, AccountSecurity.auth_version == row.auth_version).values(email_verified_at=datetime.utcnow()))
    if changed.rowcount != 1:
        session.rollback()
        raise HTTPException(400, "This link is invalid or has expired")
    session.commit()
    return {"verified": True}


@auth_router.post("/forgot-password")
def forgot_password(data: RecoveryRequest, request: Request, session=Depends(get_session)):
    account_limit(session, "forgot-address", request_address(request), 20, 900)
    account_limit(session, "forgot-email", data.email, 3, 900)
    if not mail_available():
        raise HTTPException(503, "Email delivery is not configured on this server")
    account_mail_base_url()
    matches = session.exec(select(User).where(func.lower(User.email) == data.email).limit(2)).all()
    from ..services.workforce_access import is_managed
    if len(matches) == 1 and not matches[0].is_blocked and not is_managed(session, matches[0].id):
        queue_account_email(session, matches[0], "password-reset")
        session.commit()
    return {"message": GENERIC_EMAIL_MESSAGE}


@auth_router.post("/reset-password")
def reset_password(data: ResetPassword, request: Request, session=Depends(get_session)):
    account_limit(session, "reset-address", request_address(request), 30, 300)
    row, user, state = valid_account_token(session, data.token, "password-reset")
    new_hash = hash_password(data.new_password)
    consume_account_token(session, row)
    revoke_all_sessions(session, user.id, row.auth_version)
    user.password_hash = new_hash
    session.add(user)
    session.commit()
    return {"password_reset": True, "requires_login": True, "content_recovery_separate": True}


@router.post("/password")
def change_password(data: ChangePassword, request: Request, user=Depends(get_current_user), session=Depends(get_session)):
    _sensitive_limit(session, request, user.id)
    state = ensure_reauthentication(session, user, data.current_password, data.code)
    new_hash = hash_password(data.new_password)
    revoke_all_sessions(session, user.id, state.auth_version)
    user.password_hash = new_hash
    session.add(user)
    session.commit()
    return {"password_changed": True, "requires_login": True, "content_recovery_separate": True}


@router.get("/sessions")
def list_sessions(user=Depends(get_current_user), credentials: HTTPAuthorizationCredentials = Depends(security), session=Depends(get_session)):
    current = token_digest(credentials.credentials)
    rows = session.exec(select(ActiveSession).where(ActiveSession.user_id == user.id, ActiveSession.revoked_at.is_(None), ActiveSession.expires_at > datetime.utcnow()).order_by(ActiveSession.created_at.desc())).all()
    return [{"id": row.id, "created_at": row.created_at.isoformat() + "Z", "expires_at": row.expires_at.isoformat() + "Z",
             "user_agent": row.user_agent, "client_address": row.client_address, "current": row.token_hash == current} for row in rows]


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, data: Reauthenticate, request: Request, user=Depends(get_current_user), credentials: HTTPAuthorizationCredentials = Depends(security), session=Depends(get_session)):
    _sensitive_limit(session, request, user.id)
    ensure_reauthentication(session, user, data.password, data.code)
    row = session.get(ActiveSession, session_id)
    if not row or row.user_id != user.id:
        raise HTTPException(404, "Session not found")
    row.revoked_at = datetime.utcnow()
    session.add(row)
    session.commit()
    return {"revoked": True, "current": row.token_hash == token_digest(credentials.credentials)}


@router.post("/sessions/revoke-all")
def revoke_sessions(data: Reauthenticate, request: Request, user=Depends(get_current_user), session=Depends(get_session)):
    _sensitive_limit(session, request, user.id)
    state = ensure_reauthentication(session, user, data.password, data.code)
    revoke_all_sessions(session, user.id, state.auth_version)
    session.commit()
    return {"revoked": True, "requires_login": True}


@router.post("/mfa/setup")
def setup_mfa(data: Reauthenticate, request: Request, user=Depends(get_current_user), session=Depends(get_session)):
    _sensitive_limit(session, request, user.id)
    state = ensure_reauthentication(session, user, data.password, data.code)
    if state.totp_secret:
        raise HTTPException(409, "Two-factor authentication is already enabled")
    raw = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
    state.totp_pending_secret = seal_private(raw, "totp-pending", user.id)
    state.totp_pending_expires_at = datetime.utcnow() + timedelta(minutes=10)
    session.add(state)
    session.commit()
    from urllib.parse import quote
    uri = "otpauth://totp/" + quote("FreeCollab:" + user.email, safe="") + "?" + urlencode({"secret": raw, "issuer": "FreeCollab", "algorithm": "SHA1", "digits": 6, "period": 30})
    return {"secret": raw, "otpauth_uri": uri, "expires_at": state.totp_pending_expires_at.isoformat() + "Z"}


@router.post("/mfa/confirm")
def confirm_mfa(data: FactorCode, request: Request, user=Depends(get_current_user), session=Depends(get_session)):
    _sensitive_limit(session, request, user.id)
    state = state_for(session, user.id)
    if not state.totp_pending_secret or not state.totp_pending_expires_at or state.totp_pending_expires_at <= datetime.utcnow() or state.totp_secret:
        raise HTTPException(400, "Two-factor setup is invalid or has expired")
    raw = unseal_private(state.totp_pending_secret, "totp-pending", user.id)
    counter = match_totp(raw, data.code)
    if counter is None:
        raise HTTPException(403, "Authenticator code is invalid")
    pending = state.totp_pending_secret
    changed = session.exec(update(AccountSecurity).where(AccountSecurity.user_id == user.id, AccountSecurity.totp_pending_secret == pending,
                        AccountSecurity.totp_pending_expires_at > datetime.utcnow(), AccountSecurity.totp_secret.is_(None)).values(
                        totp_secret=seal_private(raw, "totp", user.id), totp_pending_secret=None, totp_pending_expires_at=None, totp_last_counter=counter))
    if changed.rowcount != 1:
        session.rollback()
        raise HTTPException(400, "Two-factor setup is invalid or has expired")
    codes = replace_recovery_codes(session, user.id)
    version = revoke_all_sessions(session, user.id, state.auth_version)
    session.commit()
    return {"enabled": True, "recovery_codes": codes, "session": _token_payload(user, session, request, mfa=True, auth_version=version)}


@router.post("/mfa/disable")
def disable_mfa(data: Reauthenticate, request: Request, user=Depends(get_current_user), session=Depends(get_session)):
    _sensitive_limit(session, request, user.id)
    state = ensure_reauthentication(session, user, data.password, data.code)
    if not state.totp_secret:
        raise HTTPException(409, "Two-factor authentication is not enabled")
    state.totp_secret = None
    state.totp_pending_secret = None
    state.totp_pending_expires_at = None
    state.totp_last_counter = -1
    session.add(state)
    session.exec(delete(AccountRecoveryCode).where(AccountRecoveryCode.user_id == user.id))
    version = revoke_all_sessions(session, user.id, state.auth_version)
    session.commit()
    return {"enabled": False, "session": _token_payload(user, session, request, auth_version=version)}


@router.post("/mfa/recovery-codes")
def regenerate_recovery_codes(data: Reauthenticate, request: Request, user=Depends(get_current_user), session=Depends(get_session)):
    _sensitive_limit(session, request, user.id)
    state = ensure_reauthentication(session, user, data.password, data.code)
    if not state.totp_secret:
        raise HTTPException(409, "Two-factor authentication is not enabled")
    codes = replace_recovery_codes(session, user.id)
    session.commit()
    return {"recovery_codes": codes}


@auth_router.post("/mfa/verify")
def verify_mfa_login(data: MFAVerify, request: Request, session=Depends(get_session)):
    account_limit(session, "mfa-address", request_address(request), 30, 300)
    row = session.get(MFAChallenge, digest(data.challenge_token))
    if not row or row.used_at or row.expires_at <= datetime.utcnow() or row.attempts >= 5:
        raise HTTPException(401, "Sign-in challenge is invalid or has expired")
    state = session.get(AccountSecurity, row.user_id)
    user = session.get(User, row.user_id)
    if not state or not user or user.is_blocked or not state.totp_secret or state.auth_version != row.auth_version:
        raise HTTPException(401, "Sign-in challenge is invalid or has expired")
    account_limit(session, "mfa-account", user.id, 15, 300)
    binding = session.get(WorkspaceOIDCMFAChallenge, row.token_hash)
    workspace_scope = {"workspace_id": binding.workspace_id, "provider_revision": binding.provider_revision} if binding else None
    if workspace_scope:
        try:
            tenants.current_config(session, binding.workspace_id, binding.provider_revision, user_id=user.id)
        except HTTPException:
            raise HTTPException(401, "Workspace sign-in changed. Please sign in again.") from None
    if not verify_factor(session, state, data.code, prevent_totp_replay=True):
        session.exec(update(MFAChallenge).where(MFAChallenge.token_hash == row.token_hash, MFAChallenge.used_at.is_(None)).values(attempts=MFAChallenge.attempts + 1))
        session.commit()
        raise HTTPException(401, "Authenticator or recovery code is invalid")
    consumed = session.exec(update(MFAChallenge).where(MFAChallenge.token_hash == row.token_hash, MFAChallenge.used_at.is_(None), MFAChallenge.attempts < 5, MFAChallenge.expires_at > datetime.utcnow()).values(used_at=datetime.utcnow()))
    if consumed.rowcount != 1:
        session.rollback()
        raise HTTPException(401, "Sign-in challenge is invalid or has expired")
    session.commit()
    methods = ["sso"] if session.get(OIDCMFAChallenge, row.token_hash) else None
    return _token_payload(user, session, info={"user_agent": row.user_agent, "client_address": row.client_address}, mfa=True, auth_version=row.auth_version, auth_methods=methods, workspace_scope=workspace_scope)
