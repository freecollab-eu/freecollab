"""Request-local authorization boundaries for customer-owned identity providers.

A customer IdP authenticates a member of that customer's workspace. It is never
an authority over the member's other workspaces or global FreeCollab account.
"""
from fastapi import HTTPException
from sqlmodel import select

from ..models.session import ActiveSession
from ..models.embed import EmbedSession  # noqa: F401 -- register additive session restrictions before init_db
from ..models.oidc import OIDCIdentity, OIDCLoginAttempt  # noqa: F401 -- referenced additive metadata
from ..models.workspace import Workspace, WorkspaceChannel, WorkspaceMember
from ..models.workspace_oidc import WorkspaceOIDCProvider, WorkspaceOIDCSession
from .workforce_access import is_managed, managed_scope_valid

_SCOPE = "workspace_scope"
_ACTOR = "workspace_scope_actor_id"
_HASH = "workspace_scope_token_hash"
_CLAIMS = "workspace_scope_claims"
_INVALID = "workspace_scope_invalid"


def clear_scope(session):
    session.info.pop(_SCOPE, None)
    session.info.pop(_ACTOR, None)
    session.info.pop(_HASH, None)
    session.info.pop(_CLAIMS, None)
    session.info.pop(_INVALID, None)


def current_workspace_scope(session) -> str | None:
    if session.info.get(_INVALID):
        raise HTTPException(401, "Workspace sign-in is no longer valid")
    if session.info.get(_HASH):
        token_hash, actor, claims = session.info[_HASH], session.info[_ACTOR], session.info[_CLAIMS]
        if not bind_scope_for_hash(session, token_hash, actor, claims=claims):
            # A caught authorization exception must never upgrade subsequent
            # checks in the same request to unrestricted account access.
            session.info[_INVALID] = True
            raise HTTPException(401, "Workspace sign-in is no longer valid")
    return session.info.get(_SCOPE)


def current_scope_actor_id(session) -> str | None:
    return session.info.get(_ACTOR)


def bind_scope_for_hash(session, token_hash: str, user_id: str, *, claims: dict | None = None) -> bool:
    """Clear old actor state, then validate the token's durable, current scope.

    Select scalar columns so long-lived Matrix/socket sessions cannot reuse stale
    ORM provider/membership objects. Signed claims also make a missing/deleted
    scope sidecar fail closed instead of upgrading a JWT to account access.
    Matrix credentials are bound to an existing native ActiveSession by hash.
    """
    clear_scope(session)
    with session.no_autoflush:
        scoped = session.exec(select(WorkspaceOIDCSession.workspace_id,
                                     WorkspaceOIDCSession.provider_revision).where(
            WorkspaceOIDCSession.token_hash == token_hash,
        )).first()
        claimed_workspace = claims.get("sso_workspace_id") if claims is not None else None
        claimed_revision = claims.get("sso_provider_revision") if claims is not None else None
        has_claims = claims is not None and ("sso_workspace_id" in claims or "sso_provider_revision" in claims)
        if scoped is None:
            return not has_claims and not is_managed(session, user_id)
        workspace_id, revision = scoped
        if not managed_scope_valid(session, user_id, workspace_id):
            return False
        if claims is not None and (
            not has_claims or claimed_workspace != workspace_id
            or type(claimed_revision) is not int or claimed_revision != revision
        ):
            return False
        native_user = session.exec(select(ActiveSession.user_id).where(
            ActiveSession.token_hash == token_hash,
        )).first()
        provider = session.exec(select(WorkspaceOIDCProvider.enabled, WorkspaceOIDCProvider.revision).where(
            WorkspaceOIDCProvider.workspace_id == workspace_id,
        )).first()
        member_role = session.exec(select(WorkspaceMember.role).join(
            Workspace, Workspace.id == WorkspaceMember.workspace_id,
        ).where(WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user_id)).first()
        if (native_user != user_id or provider is None or not provider[0]
                or provider[1] != revision or member_role not in {"owner", "admin", "member", "guest"}):
            return False
        from .embed import child_session_valid
        if not child_session_valid(session, token_hash, user_id, workspace_id, claims=claims):
            return False
    session.info[_SCOPE] = workspace_id
    session.info[_ACTOR] = user_id
    session.info[_HASH] = token_hash
    session.info[_CLAIMS] = {"sso_workspace_id": workspace_id, "sso_provider_revision": revision}
    if claims is not None and "embed_application_id" in claims:
        session.info[_CLAIMS]["embed_application_id"] = claims["embed_application_id"]
    return True


def require_unrestricted(session):
    if current_workspace_scope(session) is not None:
        raise HTTPException(403, "Sign in with your FreeCollab account to manage account security or global settings")


def require_workspace_scope(session, workspace_id: str):
    scope = current_workspace_scope(session)
    if scope is not None and scope != workspace_id:
        raise HTTPException(403, "This sign-in only grants access to its workspace")


def require_channel_scope(session, channel_id: str):
    scope = current_workspace_scope(session)
    if scope is not None:
        workspace_id = session.exec(select(WorkspaceChannel.workspace_id).where(
            WorkspaceChannel.channel_id == channel_id,
        )).first()
        if workspace_id != scope:
            raise HTTPException(403, "This sign-in only grants access to its workspace")


def require_request_scope(session, path: str):
    """Global account operations require a native account authentication context."""
    if current_workspace_scope(session) is None:
        return
    path = path.removeprefix("/api").rstrip("/")
    if (path.startswith("/account/") or path == "/account"
            or path.startswith("/operations")
            or (path.startswith("/admin") and path != "/admin/me")
            or path.startswith("/auth/oidc/identities")
            or path.startswith("/auth/oidc/link")
            or path.startswith("/direct-messages/blocks")):
        require_unrestricted(session)
