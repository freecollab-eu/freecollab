"""Authorization for customer-owned identities, separate from personal accounts."""
from fastapi import HTTPException
from sqlmodel import select

from ..models.channel import ChannelMember
from ..models.user import User
from ..models.workforce import WorkforcePrincipal
from ..models.workspace import PersonalWorkspace, WorkspaceChannel, WorkspaceMember


def principal_snapshot(session, user_id):
    # Scalar reads avoid stale identity-map data in long-lived Matrix/socket work.
    return session.exec(select(WorkforcePrincipal.workspace_id, WorkforcePrincipal.active,
                               WorkforcePrincipal.managed_email).where(
        WorkforcePrincipal.user_id == user_id,
    )).first()


def is_managed(session, user_id):
    return principal_snapshot(session, user_id) is not None


def managed_scope_valid(session, user_id, workspace_id):
    principal = principal_snapshot(session, user_id)
    if principal is None:
        return True
    if not principal[1] or not workspace_id or principal[0] != workspace_id:
        return False
    role = session.exec(select(WorkspaceMember.role).where(
        WorkspaceMember.user_id == user_id, WorkspaceMember.workspace_id == workspace_id,
    )).first()
    if role == "owner":
        return False
    identity = session.exec(select(User.email).where(User.id == user_id)).first()
    if identity != "workforce-" + user_id + "@identity.invalid":
        return False
    # A managed identity's Matrix account must never span personal/other tenants.
    if session.exec(select(PersonalWorkspace.user_id).where(PersonalWorkspace.user_id == user_id)).first():
        return False
    if session.exec(select(WorkspaceMember.workspace_id).where(
        WorkspaceMember.user_id == user_id, WorkspaceMember.workspace_id != workspace_id,
    )).first():
        return False
    outside = session.exec(select(ChannelMember.channel_id).outerjoin(
        WorkspaceChannel, WorkspaceChannel.channel_id == ChannelMember.channel_id,
    ).where(ChannelMember.user_id == user_id,
            (WorkspaceChannel.workspace_id.is_(None)) | (WorkspaceChannel.workspace_id != workspace_id))).all()
    # A bilateral grant exposes only its one shared channel. It never grants
    # membership of the other tenant or changes this identity's SSO authority.
    from .connect import shared_channel_allows
    return all(shared_channel_allows(session, channel_id, user_id, workspace_id) for channel_id in outside)


def require_personal_account(session, user_id):
    if is_managed(session, user_id):
        raise HTTPException(403, "This account is managed by its organization. Use your organization's sign-in.")


def require_principal_workspace(session, user_id, workspace_id):
    principal = principal_snapshot(session, user_id)
    if principal is not None and principal[0] != workspace_id:
        raise HTTPException(403, "Organization-managed accounts belong to their own workspace")
