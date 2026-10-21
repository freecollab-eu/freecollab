"""Native administrators opt into customer-owned workforce onboarding."""
from datetime import datetime, timedelta, timezone
import secrets
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import update
from sqlmodel import select

from ..database import get_session
from ..models.account_security import AccountSecurity
from ..models.user import User
from ..models.workforce import WorkforceInvitation, WorkforcePrincipal
from ..models.workspace import WorkspaceInvitation
from ..models.workspace_oidc import WorkspaceOIDCProvider
from ..services.account_security import account_limit, digest, ensure_reauthentication
from ..services.auth import active_user_for_token, get_current_user, security, validate_password
from ..services.sso_scope import require_unrestricted
from ..services.workforce import identity_policy
from ..services.workspaces import lock_workspace, require_workspace_admin
from ..services.workspace_oidc import login_url

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["workforce onboarding"])


class Reauthenticate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=72)
    code: str | None = Field(default=None, max_length=64)
    _password = field_validator("password")(validate_password)


class IdentityPolicyInput(Reauthenticate):
    mode: Literal["linked", "provisioned", "invited", "jit"]
    identity_claim: Literal["sub", "oid", "email", "preferred_username"] = "sub"
    expected_revision: int | None = Field(default=None, ge=1)


class WorkforceInviteInput(Reauthenticate):
    role: Literal["member", "guest"] = "member"
    expires_in_hours: int = Field(default=24, ge=1, le=168)


def _private(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"


def _admin(session, workspace_id, user):
    require_unrestricted(session)
    return require_workspace_admin(session, workspace_id, user.id)


def _authorize(session, workspace_id, user, credentials, data):
    _admin(session, workspace_id, user)
    account_limit(session, "workforce-policy", user.id, 20, 300)
    session.execute(update(AccountSecurity).where(AccountSecurity.user_id == user.id)
                    .values(auth_version=AccountSecurity.auth_version))
    session.expire_all()
    actor = active_user_for_token(credentials.credentials, session)
    if actor is None:
        raise HTTPException(401, "Account security changed. Please sign in again.")
    lock_workspace(session, workspace_id)
    _admin(session, workspace_id, actor)
    ensure_reauthentication(session, actor, data.password, data.code)


def _payload(policy):
    return {"mode": policy.mode, "identity_claim": policy.identity_claim,
            "revision": policy.revision, "managed_accounts": True,
            "invitation_required": policy.mode == "invited"}


@router.get("/identity-policy")
def get_policy(workspace_id: str, response: Response, user=Depends(get_current_user), session=Depends(get_session)):
    _private(response)
    _admin(session, workspace_id, user)
    return _payload(identity_policy(session, workspace_id))


@router.put("/identity-policy")
def save_policy(workspace_id: str, data: IdentityPolicyInput, response: Response,
                user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    _private(response)
    _authorize(session, workspace_id, user, credentials, data)
    policy = identity_policy(session, workspace_id)
    if data.expected_revision is not None and policy.revision != data.expected_revision:
        raise HTTPException(409, "Workforce onboarding policy changed. Reload before saving.")
    policy.mode, policy.identity_claim = data.mode, data.identity_claim
    policy.revision += 1
    policy.updated_at = datetime.utcnow()
    session.add(policy)
    session.commit()
    return _payload(policy)


@router.get("/workforce")
def list_workforce(workspace_id: str, response: Response, user=Depends(get_current_user), session=Depends(get_session)):
    _private(response)
    _admin(session, workspace_id, user)
    rows = session.exec(select(WorkforcePrincipal, User).join(User, User.id == WorkforcePrincipal.user_id)
                        .where(WorkforcePrincipal.workspace_id == workspace_id)
                        .order_by(User.name, User.id).limit(500)).all()
    return [{"user_id": principal.user_id, "external_id": principal.external_id,
             "name": person.name, "email": principal.managed_email, "active": principal.active,
             "bound": principal.issuer is not None} for principal, person in rows]


@router.post("/workforce/invites", status_code=201)
def create_workforce_invitation(workspace_id: str, data: WorkforceInviteInput, response: Response,
                               user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    _private(response)
    _authorize(session, workspace_id, user, credentials, data)
    policy = identity_policy(session, workspace_id)
    provider = session.get(WorkspaceOIDCProvider, workspace_id)
    if policy.mode not in {"invited", "jit"}:
        raise HTTPException(409, "Enable invitation-based workforce onboarding first")
    if not provider or not provider.enabled:
        raise HTTPException(409, "Enable this workspace's identity provider first")
    now = datetime.utcnow()
    active = session.exec(select(WorkspaceInvitation.id).where(
        WorkspaceInvitation.workspace_id == workspace_id, WorkspaceInvitation.accepted_at.is_(None),
        WorkspaceInvitation.revoked_at.is_(None), WorkspaceInvitation.expires_at > now)).all()
    if len(active) >= 100:
        raise HTTPException(409, "Revoke an active invitation before creating another")
    token = secrets.token_urlsafe(32)
    invitation = WorkspaceInvitation(workspace_id=workspace_id, token_hash=digest(token), role=data.role,
        created_by=user.id, expires_at=now + timedelta(hours=data.expires_in_hours))
    session.add(invitation)
    session.flush()
    session.add(WorkforceInvitation(invitation_id=invitation.id, workspace_id=workspace_id))
    session.commit()
    return {"token": token, "invitation_id": invitation.id,
            "login_url": login_url(provider) + "&workspace-invite=" + token,
            "expires_at": invitation.expires_at.replace(tzinfo=timezone.utc)}


@router.get("/workforce/invites")
def list_workforce_invitations(workspace_id: str, response: Response, user=Depends(get_current_user), session=Depends(get_session)):
    _private(response)
    _admin(session, workspace_id, user)
    rows = session.exec(select(WorkspaceInvitation).join(WorkforceInvitation,
        WorkforceInvitation.invitation_id == WorkspaceInvitation.id)
        .where(WorkforceInvitation.workspace_id == workspace_id)
        .order_by(WorkspaceInvitation.created_at.desc()).limit(100)).all()
    def utc(value):
        return value.replace(tzinfo=timezone.utc) if value else None
    return {"invitations": [{"id": row.id, "role": row.role, "expires_at": utc(row.expires_at),
                            "used_at": utc(row.accepted_at), "revoked_at": utc(row.revoked_at)} for row in rows]}


@router.post("/workforce/invites/{invitation_id}/revoke")
def revoke_workforce_invitation(workspace_id: str, invitation_id: str, data: Reauthenticate, response: Response,
                                user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    _private(response)
    _authorize(session, workspace_id, user, credentials, data)
    marker = session.get(WorkforceInvitation, invitation_id)
    invitation = session.get(WorkspaceInvitation, invitation_id)
    if not marker or marker.workspace_id != workspace_id or not invitation:
        raise HTTPException(404, "Workspace sign-in invitation not found")
    if not invitation.revoked_at:
        invitation.revoked_at = datetime.utcnow()
        session.add(invitation)
    session.commit()
    return {"revoked": True}
