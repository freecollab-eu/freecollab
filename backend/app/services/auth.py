from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from jwt import PyJWTError
import bcrypt
from datetime import datetime, timedelta
from hashlib import sha256
from uuid import uuid4
from math import isfinite
from sqlmodel import delete, select
from sqlalchemy.exc import IntegrityError
from ..config import settings
from ..database import get_session
from ..models.user import User
from ..models.session import RevokedToken, ActiveSession
from ..models.account_security import AccountSecurity

security = HTTPBearer()


def normalize_email(value):
    return value.strip().lower() if isinstance(value, str) else value


def validate_password(password: str) -> str:
    """Reject inputs bcrypt would truncate or cannot safely process."""
    if not 1 <= len(password.encode("utf-8")) <= 72:
        raise ValueError("Password must contain between 1 and 72 UTF-8 bytes")
    if "\x00" in password:
        raise ValueError("Password must not contain null characters")
    return password


def hash_password(password: str) -> str:
    validate_password(password)
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hash: str) -> bool:
    try:
        validate_password(password)
        return bcrypt.checkpw(password.encode(), hash.encode())
    except (ValueError, TypeError):
        return False


def create_token(user_id: str, session=None, request=None, *, mfa_verified: bool = False, session_info: dict | None = None, expected_auth_version: int | None = None, auth_methods: list[str] | None = None, workspace_scope: dict | None = None) -> str:
    version = 0
    if session is not None:
        from .workforce_access import is_managed, managed_scope_valid
        if is_managed(session, user_id) and (auth_methods != ["sso"] or not workspace_scope
                or not managed_scope_valid(session, user_id, workspace_scope.get("workspace_id"))):
            raise HTTPException(403, "Managed accounts require their organization's workspace sign-in")
        from .account_security import state_for
        state = state_for(session, user_id)
        version = state.auth_version
        if expected_auth_version is not None and version != expected_auth_version:
            raise HTTPException(401, "Account security changed. Please sign in again.")
        if state.totp_secret and not mfa_verified:
            raise HTTPException(403, "Authenticator verification is required")
    now = datetime.utcnow()
    methods = list(auth_methods) if auth_methods is not None else ["pwd"]
    if not methods or any(method not in {"pwd", "sso"} for method in methods):
        raise ValueError("Invalid authentication method")
    if mfa_verified:
        methods.append("otp")
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(days=7),
        "jti": uuid4().hex,
        "av": expected_auth_version if expected_auth_version is not None else version,
        "amr": methods,
    }
    if workspace_scope is not None:
        workspace_id = workspace_scope.get("workspace_id")
        revision = workspace_scope.get("provider_revision")
        if not isinstance(workspace_id, str) or not workspace_id or type(revision) is not int or revision < 1:
            raise ValueError("Invalid workspace authentication scope")
        payload["sso_workspace_id"] = workspace_id
        payload["sso_provider_revision"] = revision
    token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
    if session is not None:
        track_session(token, session, request=request, info=session_info)
    if request is not None:
        from .security_audit import authenticated_actor
        authenticated_actor(request, user_id, workspace_id=workspace_scope.get("workspace_id") if workspace_scope else None)
    return token


def decode_token_claims(token: str) -> dict | None:
    if not isinstance(token, str) or not token:
        return None
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            options={"require": ["sub", "exp"]},
        )
        subject = payload.get("sub")
        if not isinstance(subject, str) or not subject.strip():
            return None
        expires = payload.get("exp")
        if isinstance(expires, bool) or not isinstance(expires, (int, float)) or not isfinite(expires):
            return None
        # Avoid impossible timestamps reaching revocation storage.
        if expires > 253402300799:
            return None
        version = payload.get("av", 0)
        if type(version) is not int or version < 0:
            return None
        return payload
    except (PyJWTError, TypeError, ValueError, OverflowError):
        return None


def decode_token(token: str) -> str | None:
    """Verify the signature and claims; authorization also requires DB checks."""
    claims = decode_token_claims(token)
    return claims["sub"] if claims else None


def token_digest(token: str) -> str:
    return sha256(token.encode()).hexdigest()


def track_session(token: str, session, request=None, info: dict | None = None) -> ActiveSession:
    claims = decode_token_claims(token)
    if not claims:
        raise HTTPException(401, "Invalid token")
    digest = token_digest(token)
    existing = session.query(ActiveSession).filter(ActiveSession.token_hash == digest).first()
    if existing:
        return existing
    info = info or {}
    user_agent = request.headers.get("user-agent", "Unknown browser") if request else info.get("user_agent", "Unknown browser")
    address = request.client.host if request and request.client else info.get("client_address", "")
    row = ActiveSession(user_id=claims["sub"], token_hash=digest, expires_at=datetime.utcfromtimestamp(claims["exp"]),
                        user_agent=user_agent[:256], client_address=address[:64])
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        row = session.query(ActiveSession).filter(ActiveSession.token_hash == digest).first()
        if row is None:
            raise
    return row


def token_is_revoked(token: str, session, claims: dict) -> bool:
    digest = token_digest(token)
    if session.get(RevokedToken, digest):
        return True
    version = session.exec(select(AccountSecurity.auth_version).where(AccountSecurity.user_id == claims["sub"])).first()
    if claims.get("av", 0) != (version if version is not None else 0):
        return True
    tracked = session.exec(select(ActiveSession.revoked_at, ActiveSession.expires_at).where(ActiveSession.token_hash == digest)).first()
    return bool(tracked and (tracked.revoked_at is not None or tracked.expires_at <= datetime.utcnow()))


def active_user_for_token(token: str, session) -> User | None:
    """Shared REST/Socket.IO check, including revocation and account state."""
    from .sso_scope import clear_scope, bind_scope_for_hash
    clear_scope(session)
    claims = decode_token_claims(token)
    if not claims or token_is_revoked(token, session, claims):
        return None
    if not bind_scope_for_hash(session, token_digest(token), claims["sub"], claims=claims):
        return None
    user = session.get(User, claims["sub"])
    return user if user and not user.is_blocked else None


def revoke_token(token: str, session) -> None:
    claims = decode_token_claims(token)
    if not claims:
        raise HTTPException(401, "Invalid token")
    digest = token_digest(token)
    # Keeping only unexpired digests bounds persistent revocation history.
    session.exec(delete(RevokedToken).where(RevokedToken.expires_at <= datetime.utcnow()))
    if not session.get(RevokedToken, digest):
        session.add(RevokedToken(token_hash=digest, expires_at=datetime.utcfromtimestamp(claims["exp"])))
    tracked = session.query(ActiveSession).filter(ActiveSession.token_hash == digest).first()
    if tracked:
        tracked.revoked_at = datetime.utcnow()
        session.add(tracked)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        # Concurrent logout requests may race to insert the same digest.
        if not session.get(RevokedToken, digest):
            raise


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    session=Depends(get_session),
    request: Request = None,
) -> User:
    from .request_guard import clear_request, remember_request
    clear_request(session)
    from .sso_scope import clear_scope, bind_scope_for_hash, require_request_scope
    clear_scope(session)
    claims = decode_token_claims(credentials.credentials)
    if not claims:
        raise HTTPException(401, "Invalid token")

    if token_is_revoked(credentials.credentials, session, claims):
        raise HTTPException(401, "Session has been logged out")

    if not bind_scope_for_hash(session, token_digest(credentials.credentials), claims["sub"], claims=claims):
        raise HTTPException(401, "Workspace sign-in is no longer valid. Please sign in again.")
    if request is not None:
        require_request_scope(session, request.url.path)
    user = session.get(User, claims["sub"])
    if not user:
        raise HTTPException(401, "User not found")
    if user.is_blocked:
        raise HTTPException(403, "Account is blocked")

    from .account_security import state_for
    state_for(session, user.id)
    track_session(credentials.credentials, session)
    remember_request(session, token_digest(credentials.credentials), claims)
    if request is not None:
        from .security_audit import authenticated_actor
        authenticated_actor(request, user.id)
    return user
