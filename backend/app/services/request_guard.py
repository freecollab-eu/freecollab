"""Recheck authenticated request authority after locks and awaited work.

Only digests and verified claim metadata are retained in the request's SQLModel
session. Background jobs have separate explicit creator/grant checks and do not
inherit a browser request credential.
"""
from datetime import datetime
import time

from fastapi import HTTPException
from sqlmodel import select

from ..models.account_security import AccountSecurity
from ..models.session import ActiveSession, RevokedToken
from ..models.user import User

_KEY = "freecollab_verified_request"


def remember_request(session, token_hash: str, claims: dict):
    session.info[_KEY] = {"token_hash": token_hash, "claims": dict(claims)}


def clear_request(session):
    session.info.pop(_KEY, None)


def recheck_actor(session, user_id: str):
    """Fresh scalar checks; safe even when ORM identities predate a row lock."""
    actor = session.exec(select(User.id, User.is_blocked).where(User.id == user_id)).first()
    if not actor or actor[1]:
        raise HTTPException(401, "Account access changed. Please sign in again.")
    context = session.info.get(_KEY)
    if context is None:
        # Trusted service/worker callers still need their own current resource
        # grant checks. The HTTP dependency always supplies the context below.
        return
    claims, digest = context["claims"], context["token_hash"]
    version = session.exec(select(AccountSecurity.auth_version).where(AccountSecurity.user_id == user_id)).first()
    tracked = session.exec(select(ActiveSession.user_id, ActiveSession.revoked_at, ActiveSession.expires_at).where(
        ActiveSession.token_hash == digest)).first()
    now = datetime.utcnow()
    if (claims.get("sub") != user_id or claims.get("av", 0) != (version or 0)
            or claims["exp"] <= time.time() or session.get(RevokedToken, digest)
            or not tracked or tracked[0] != user_id or tracked[1] is not None or tracked[2] <= now):
        raise HTTPException(401, "Account access changed. Please sign in again.")
    from .sso_scope import bind_scope_for_hash
    if not bind_scope_for_hash(session, digest, user_id, claims=claims):
        session.info["workspace_scope_invalid"] = True
        raise HTTPException(401, "Workspace access changed. Please sign in again.")
