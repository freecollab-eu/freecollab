"""Exact-origin, PKCE-bound launch grants for the trusted FreeCollab client.

No source bearer is persisted, even encrypted. A consumed grant creates a child
workspace session with the source authentication age and hard JWT expiry. An
authoritative ten-minute server lease limits offline access; renewing that lease
does not rotate the user's device or authentication. Central scope checks keep
it linked to its source session and registration.
"""
from datetime import datetime
import base64
import hashlib
import re
from urllib.parse import urlsplit, urlunsplit

from fastapi import HTTPException
from sqlmodel import select

from ..config import settings
from ..models.embed import EmbedApplication, EmbedSession
from ..models.session import ActiveSession, RevokedToken
from ..models.workspace_oidc import WorkspaceOIDCProvider

MODULES = ("workspace", "chat", "tasks", "documents", "meetings", "calendar", "workflows", "decisions", "files", "feed", "integrations", "search", "copilot", "cloud", "external-meetings")
CHILD_SESSION_SECONDS = 600


def exact_origin(value: str) -> str:
    """Return the browser origin; paths, userinfo, aliases and wildcard rules fail."""
    try:
        if not isinstance(value, str) or value != value.strip() or any(ord(c) < 33 or ord(c) == 127 for c in value):
            raise ValueError()
        parsed = urlsplit(value)
        test_http = settings.environment in {"test", "development"} and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if (not parsed.hostname or parsed.username is not None or parsed.password is not None or
                parsed.query or parsed.fragment or parsed.path not in {"", "/"} or
                (parsed.scheme != "https" and not (test_http and parsed.scheme == "http")) or
                "\\" in value or "*" in value):
            raise ValueError()
        host = parsed.hostname.encode("idna").decode("ascii").lower()
        if ":" in host:
            host = "[" + host + "]"
        port = parsed.port
        authority = host if port is None or (parsed.scheme == "https" and port == 443) or (parsed.scheme == "http" and port == 80) else f"{host}:{port}"
        result = urlunsplit((parsed.scheme, authority, "", "", ""))
        # Use browser-canonical exact origins, not silently equivalent URL input.
        if value.rstrip("/") != result:
            raise ValueError()
        return result
    except (TypeError, ValueError, UnicodeError):
        raise HTTPException(422, "Use an exact HTTPS browser origin without a path, wildcard, credentials or query") from None


def public_origin() -> str:
    try:
        return exact_origin(settings.public_app_url.rstrip("/"))
    except HTTPException:
        raise HTTPException(503, "The public application origin is not configured correctly") from None


def frame_url(application, parent_origin):
    from urllib.parse import quote
    return public_origin() + "/api/embed/" + application.id + "/document?parent_origin=" + quote(parent_origin, safe="")


def current_application(session, application_id, *, parent_origin=None, revision=None):
    row = session.get(EmbedApplication, application_id, populate_existing=True)
    if not row or not row.enabled or (revision is not None and row.revision != revision):
        raise HTTPException(404, "Embedded application is unavailable")
    if parent_origin is not None and row.parent_origin != exact_origin(parent_origin):
        raise HTTPException(403, "This origin is not registered for the embedded application")
    if row.parent_origin == public_origin():
        raise HTTPException(403, "The host product and trusted FreeCollab client require separate origins")
    provider = session.get(WorkspaceOIDCProvider, row.workspace_id, populate_existing=True)
    if not provider or not provider.enabled:
        raise HTTPException(404, "Workspace sign-in is not enabled")
    return row, provider


def proof_challenge(verifier: str) -> str:
    if not isinstance(verifier, str) or not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier):
        raise HTTPException(400, "The embedding handoff is invalid or has expired")
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


def parent_session_valid(session, parent_hash, user_id, *, now=None):
    now = now or datetime.utcnow()
    source = session.exec(select(ActiveSession.user_id, ActiveSession.revoked_at, ActiveSession.expires_at).where(
        ActiveSession.token_hash == parent_hash)).first()
    return bool(source and source[0] == user_id and source[1] is None and source[2] > now
                and not session.get(RevokedToken, parent_hash) and not session.get(EmbedSession, parent_hash))


def child_session_valid(session, token_hash, user_id, workspace_id, *, claims=None):
    """Called by REST, sockets and Matrix's shared workspace-scope fence."""
    child = session.get(EmbedSession, token_hash, populate_existing=True)
    if child is None:
        return claims is None or "embed_application_id" not in claims
    if claims is not None and "embed_application_id" in claims and claims["embed_application_id"] != child.application_id:
        return False
    application = session.exec(select(EmbedApplication.enabled, EmbedApplication.revision, EmbedApplication.workspace_id).where(
        EmbedApplication.id == child.application_id)).first()
    return bool(application and application[0] and application[1] == child.application_revision
                and application[2] == child.workspace_id == workspace_id
                and parent_session_valid(session, child.parent_token_hash, user_id))
