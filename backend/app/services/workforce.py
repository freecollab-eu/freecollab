"""Customer-managed accounts never claim a global user's email or identity."""
from datetime import datetime
import secrets
from uuid import uuid4

from fastapi import HTTPException
from sqlmodel import select

from ..models.account_security import AccountSecurity
from ..models.user import User
from ..models.workforce import WorkforceInvitation, WorkforceOIDCAttempt, WorkforcePrincipal, WorkspaceIdentityPolicy
from ..models.workspace import WorkspaceInvitation, WorkspaceMember
from .account_security import digest
from .auth import hash_password


def managed_principal(session, user_id):
    return session.get(WorkforcePrincipal, user_id, populate_existing=True)


def principal_for_external_id(session, workspace_id, external_id):
    return session.exec(select(WorkforcePrincipal).where(
        WorkforcePrincipal.workspace_id == workspace_id,
        WorkforcePrincipal.external_id == external_id,
    )).first()


def identity_policy(session, workspace_id):
    return session.get(WorkspaceIdentityPolicy, workspace_id, populate_existing=True) or WorkspaceIdentityPolicy(workspace_id=workspace_id)


def ensure_principal(session, workspace_id, external_id, name, email, *, active=True, role="member"):
    """Create/update a tenant-local principal under the caller's workspace lock.

    This helper does not commit, deactivate or erase memberships. Offboarding is
    remote-first through the normal workspace removal service before marking the
    principal inactive. Reactivation adds only workspace membership, never the old
    private channel grants.
    """
    if (not isinstance(external_id, str) or not 1 <= len(external_id) <= 512
            or any(ord(c) < 32 for c in external_id)):
        raise HTTPException(422, "An explicit external identity is required")
    if role not in {"admin", "member", "guest"}:
        raise HTTPException(422, "Managed accounts cannot be workspace owners")
    principal = principal_for_external_id(session, workspace_id, external_id)
    if principal and principal.active and not active:
        raise HTTPException(409, "Remove workspace access before deactivating a managed account")
    if principal is None:
        user_id = uuid4().hex
        user = User(id=user_id, name=(name or "Workspace member").strip()[:80] or "Workspace member",
                    email="workforce-" + user_id + "@identity.invalid",
                    password_hash=hash_password(secrets.token_urlsafe(40)))
        session.add(user)
        session.flush()
        # This is not verified ownership of the managed email claim. There is no
        # personal password, recovery mailbox or global identity to take over.
        session.add(AccountSecurity(user_id=user.id))
        principal = WorkforcePrincipal(user_id=user.id, workspace_id=workspace_id,
                                       external_id=external_id, managed_email=(email or "")[:320], active=active)
        session.add(principal)
    else:
        user = session.get(User, principal.user_id)
        if not user:
            raise HTTPException(409, "Managed account is unavailable")
        user.name = (name or user.name).strip()[:80] or "Workspace member"
        principal.managed_email = (email or "")[:320]
        principal.active = active
        session.add(user)
        session.add(principal)
    if active and session.get(WorkspaceMember, (workspace_id, principal.user_id)) is None:
        session.add(WorkspaceMember(workspace_id=workspace_id, user_id=principal.user_id, role=role))
    session.flush()
    return principal


def valid_invitation(session, workspace_id, token=None, *, invitation_id=None, token_hash=None):
    if token is not None:
        token_hash = digest(token)
        invitation = session.exec(select(WorkspaceInvitation).where(WorkspaceInvitation.token_hash == token_hash)).first()
    else:
        invitation = session.get(WorkspaceInvitation, invitation_id) if invitation_id else None
    now = datetime.utcnow()
    marker = session.get(WorkforceInvitation, invitation.id) if invitation else None
    issuer = session.get(User, invitation.created_by) if invitation else None
    issuer_role = session.get(WorkspaceMember, (workspace_id, invitation.created_by)) if invitation else None
    if (not invitation or not marker or marker.workspace_id != workspace_id
            or invitation.workspace_id != workspace_id or invitation.token_hash != token_hash
            or invitation.accepted_at or invitation.revoked_at or invitation.expires_at <= now
            or invitation.role not in {"member", "guest"} or not issuer or issuer.is_blocked
            or not issuer_role or issuer_role.role not in {"owner", "admin"}):
        raise HTTPException(403, "Workspace sign-in invitation is invalid or expired")
    return invitation


def bind_attempt(session, attempt, config, invitation_token=None):
    policy = identity_policy(session, config["workspace_id"])
    invitation = None
    if invitation_token:
        invitation = valid_invitation(session, config["workspace_id"], invitation_token)
    session.add(WorkforceOIDCAttempt(state_hash=attempt.state_hash, workspace_id=config["workspace_id"],
        policy_revision=policy.revision, invitation_id=invitation.id if invitation else None,
        invitation_hash=invitation.token_hash if invitation else None))


def resolve_managed_principal(session, attempt, config, claims):
    """Provision only into this customer namespace after verified OIDC claims."""
    workspace_id = config["workspace_id"]
    policy = identity_policy(session, workspace_id)
    bound_attempt = session.get(WorkforceOIDCAttempt, attempt.state_hash)
    if (bound_attempt is None or bound_attempt.workspace_id != workspace_id
            or bound_attempt.policy_revision != policy.revision or policy.mode == "linked"):
        raise HTTPException(403, "This workspace requires an existing linked account")
    external_id = claims.get(policy.identity_claim)
    if not isinstance(external_id, str) or not external_id or len(external_id) > 512:
        raise HTTPException(403, "The provider did not supply the configured workforce identity")
    principal = principal_for_external_id(session, workspace_id, external_id)
    invitation = None
    if principal is not None and not principal.active:
        raise HTTPException(403, "This workspace identity is inactive")
    if principal is None:
        if bound_attempt.invitation_id and policy.mode in {"invited", "jit"}:
            invitation = valid_invitation(session, workspace_id, invitation_id=bound_attempt.invitation_id,
                                          token_hash=bound_attempt.invitation_hash)
        elif policy.mode != "jit":
            raise HTTPException(403, "This workspace identity must be provisioned or invited")
        name = claims.get("name") if isinstance(claims.get("name"), str) else "Workspace member"
        email = claims.get("email") if isinstance(claims.get("email"), str) else ""
        principal = ensure_principal(session, workspace_id, external_id, name, email,
                                     role=invitation.role if invitation else "member")
    elif session.get(WorkspaceMember, (workspace_id, principal.user_id)) is None:
        # A removed person never regains membership merely by signing in again.
        raise HTTPException(403, "This workspace identity no longer has access")
    if principal.issuer is not None and (principal.issuer, principal.client_id, principal.subject) != (
            config["issuer"], config["client_id"], claims["sub"]):
        raise HTTPException(403, "This managed identity is already bound to a different provider identity")
    principal.issuer, principal.client_id, principal.subject = config["issuer"], config["client_id"], claims["sub"]
    session.add(principal)
    if invitation:
        invitation.accepted_at, invitation.accepted_by = datetime.utcnow(), principal.user_id
        session.add(invitation)
    session.flush()
    return session.get(User, principal.user_id), session.get(AccountSecurity, principal.user_id)
