"""Explicit, source-bound native sign-in. PKCE protects intercepted callbacks."""
import base64
from datetime import datetime, timedelta
from hashlib import sha256
import hmac
import json
from math import ceil
import re
import secrets
import time
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import HTTPException
import jwt
from sqlmodel import select
from sqlalchemy import delete, update

from ..config import settings
from ..models.account_security import AccountSecurity
from ..models.native_clients import NativeClientGrant, NativeClientSession
from ..models.session import ActiveSession, RevokedToken
from ..models.user import User
from ..models.workspace import Workspace, WorkspaceMember
from ..models.workspace_oidc import WorkspaceOIDCProvider, WorkspaceOIDCSession

CLAIMS = ("sub", "iat", "exp", "av", "amr", "sso_workspace_id", "sso_provider_revision")


def redirect_uri(value, kind):
    if not isinstance(value, str) or len(value) > 256 or any(c.isspace() for c in value):
        raise HTTPException(422, "Invalid native callback")
    if kind == "mobile" and value == "freecollab://native/complete":
        return value
    try:
        parsed = urlsplit(value)
        valid = (kind == "desktop" and parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
                 and parsed.port is not None and 1024 <= parsed.port <= 65535
                 and parsed.path == "/freecollab-native/callback" and not parsed.username
                 and not parsed.password and not parsed.query and not parsed.fragment
                 and value == f"http://127.0.0.1:{parsed.port}/freecollab-native/callback")
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(422, "Native callback must be the explicit loopback or mobile callback")
    return value


def _source_valid(session, token_hash, claims):
    """Fresh scalar checks also support a native session after its parent logs out."""
    now = datetime.utcnow()
    if not isinstance(claims, dict) or not isinstance(claims.get("exp"), (int, float)):
        return False
    if claims["exp"] <= time.time():
        return False
    tracked = session.exec(select(ActiveSession.user_id, ActiveSession.expires_at, ActiveSession.revoked_at).where(
        ActiveSession.token_hash == token_hash)).first()
    user = session.exec(select(User.is_blocked).where(User.id == claims.get("sub"))).first()
    version = session.exec(select(AccountSecurity.auth_version).where(AccountSecurity.user_id == claims.get("sub"))).first()
    if (not tracked or tracked[0] != claims.get("sub") or tracked[1] <= now or tracked[2] is not None
            or user is None or user or session.get(RevokedToken, token_hash)
            or claims.get("av", 0) != (version or 0)):
        return False
    scope = session.exec(select(WorkspaceOIDCSession.workspace_id, WorkspaceOIDCSession.provider_revision).where(
        WorkspaceOIDCSession.token_hash == token_hash)).first()
    from .workforce_access import is_managed, managed_scope_valid
    if scope is None:
        return not any(key in claims for key in ("sso_workspace_id", "sso_provider_revision")) and not is_managed(session, claims["sub"])
    if claims.get("sso_workspace_id") != scope[0] or claims.get("sso_provider_revision") != scope[1]:
        return False
    provider = session.exec(select(WorkspaceOIDCProvider.enabled, WorkspaceOIDCProvider.revision).where(
        WorkspaceOIDCProvider.workspace_id == scope[0])).first()
    member = session.exec(select(WorkspaceMember.role).join(Workspace, Workspace.id == WorkspaceMember.workspace_id).where(
        WorkspaceMember.workspace_id == scope[0], WorkspaceMember.user_id == claims["sub"])).first()
    return bool(provider and provider[0] and provider[1] == scope[1] and member in {"owner", "admin", "member", "guest"}
                and managed_scope_valid(session, claims["sub"], scope[0]))


def child_session_valid(session, token_hash, claims):
    """A signed marker and its durable sidecar must both exist, including on sockets."""
    row = session.get(NativeClientSession, token_hash, populate_existing=True)
    if row is not None:
        try:
            source = json.loads(row.source_claims)
        except (ValueError, TypeError):
            return False
        if any(claims.get(key) != source.get(key) for key in CLAIMS):
            return False
    return hash_session_valid(session, token_hash, claims.get("sub"), claims=claims)


def hash_session_valid(session, token_hash, user_id, *, claims=None):
    """Matrix holds an authenticated token digest rather than the browser JWT."""
    row = session.get(NativeClientSession, token_hash, populate_existing=True)
    if row is None:
        return claims is None or claims.get("native_client_id") is None
    if user_id != row.user_id or (claims is not None and claims.get("native_client_id") != row.id):
        return False
    try:
        source = json.loads(row.source_claims)
    except (ValueError, TypeError):
        return False
    if claims is not None and any(claims[key] != source.get(key) for key in CLAIMS if key in claims):
        return False
    # Digest-only Matrix authority must retain the original child workspace
    # boundary even if its scope sidecar disappears independently of the JWT.
    scope = session.exec(select(WorkspaceOIDCSession.workspace_id, WorkspaceOIDCSession.provider_revision).where(
        WorkspaceOIDCSession.token_hash == token_hash)).first()
    expected = source.get("sso_workspace_id")
    if expected is not None:
        if scope is None or tuple(scope) != (expected, source.get("sso_provider_revision")):
            return False
    elif scope is not None:
        return False
    return _source_valid(session, row.parent_token_hash, source)


def issue_grant(session, user_id, token, *, challenge, callback, kind):
    from .auth import decode_token_claims, token_digest, track_session
    from .request_guard import recheck_actor
    claims = decode_token_claims(token)
    if (not claims or claims.get("sub") != user_id or claims.get("native_client_id")
            or claims.get("embed_application_id")):
        raise HTTPException(403, "Sign in directly in your browser before connecting a native client")
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", challenge):
        raise HTTPException(422, "Native sign-in requires PKCE S256")
    redirect_uri(callback, kind)
    track_session(token, session)
    session.execute(update(User).where(User.id == user_id).values(name=User.name))
    recheck_actor(session, user_id)
    if not _source_valid(session, token_digest(token), claims):
        raise HTTPException(401, "Browser sign-in changed; start again")
    now = datetime.utcnow()
    session.execute(delete(NativeClientGrant).where(NativeClientGrant.expires_at < now - timedelta(days=1)))
    pending = session.exec(select(NativeClientGrant).where(NativeClientGrant.user_id == user_id,
        NativeClientGrant.consumed_at == None, NativeClientGrant.expires_at > now)).all()
    if len(pending) >= 10:
        raise HTTPException(429, "Too many pending native sign-ins; wait one minute")
    code = secrets.token_urlsafe(32)
    expires = min(now + timedelta(seconds=60), datetime.utcfromtimestamp(claims["exp"]))
    session.add(NativeClientGrant(code_hash=token_digest(code), user_id=user_id,
        parent_token_hash=token_digest(token), source_claims=json.dumps({key: claims[key] for key in CLAIMS if key in claims}),
        code_challenge=challenge, redirect_uri=callback, client_kind=kind,
        expires_at=expires))
    session.commit()
    return {"code": code, "expires_in": max(0, ceil((expires-now).total_seconds())), "redirect_uri": callback}


def exchange_grant(session, code, verifier, callback):
    from .auth import token_digest
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", code) or not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier):
        raise HTTPException(401, "Native sign-in is invalid or expired")
    digest = token_digest(code)
    # Lock and consume in the same transaction as the new session. No token is
    # returned before its parent-bound restriction and workspace scope commit.
    row = session.exec(select(NativeClientGrant).where(NativeClientGrant.code_hash == digest).with_for_update()).first()
    challenge = base64.urlsafe_b64encode(sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    if (not row or row.consumed_at or row.expires_at <= datetime.utcnow()
            or callback != row.redirect_uri or not hmac.compare_digest(row.code_challenge, challenge)):
        raise HTTPException(401, "Native sign-in is invalid or expired")
    source = json.loads(row.source_claims)
    if not _source_valid(session, row.parent_token_hash, source):
        raise HTTPException(401, "Browser sign-in changed; start again")
    claimed = session.execute(update(NativeClientGrant).where(NativeClientGrant.code_hash == digest,
        NativeClientGrant.consumed_at == None).values(consumed_at=datetime.utcnow()))
    if claimed.rowcount != 1:
        raise HTTPException(401, "Native sign-in was already used")
    identifier = uuid4().hex
    payload = {**source, "jti": uuid4().hex, "native_client_id": identifier}
    token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
    token_hash = token_digest(token)
    session.add(ActiveSession(user_id=row.user_id, token_hash=token_hash,
        expires_at=datetime.utcfromtimestamp(source["exp"]), user_agent="FreeCollab " + row.client_kind))
    session.flush()
    session.add(NativeClientSession(id=identifier, token_hash=token_hash, user_id=row.user_id,
        parent_token_hash=row.parent_token_hash, source_claims=row.source_claims, client_kind=row.client_kind))
    if source.get("sso_workspace_id"):
        session.add(WorkspaceOIDCSession(token_hash=token_hash, workspace_id=source["sso_workspace_id"],
            provider_revision=source["sso_provider_revision"]))
    session.commit()
    user = session.get(User, row.user_id)
    return {"access_token": token, "token_type": "bearer", "user_id": row.user_id,
        "public_key": user.public_key or "", "encrypted_private_key": user.encrypted_private_key,
        "workspace_id": source.get("sso_workspace_id"), "expires_at": source["exp"]}
