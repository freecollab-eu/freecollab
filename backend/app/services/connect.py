"""Current, scalar authorization for explicitly shared encrypted channels.

These predicates intentionally do not bind or broaden a workspace SSO session.
The caller still verifies that session against its own identity provider.
"""
from fastapi import HTTPException
from datetime import datetime
from sqlalchemy import update
from sqlmodel import select

from ..models.channel import Channel, ChannelMember
from ..models.connect import ConnectConnection, ConnectGrant
from ..models.secure_channel import SecureChannel
from ..models.user import User
from ..models.workforce import WorkforcePrincipal
from ..models.workspace import Workspace, WorkspaceChannel, WorkspaceMember


def connection_for_channel(session, channel_id):
    return session.exec(select(ConnectConnection).where(
        ConnectConnection.channel_id == channel_id,
    ).execution_options(populate_existing=True)).first()


def shared_channel_home(session, channel_id, user_id):
    """Return the explicitly granting tenant, or None. No default membership."""
    row = session.exec(select(
        ConnectConnection.status, ConnectConnection.source_workspace_id,
        ConnectConnection.target_workspace_id, ConnectGrant.workspace_id,
        ConnectGrant.revoked_at,
    ).join(ConnectGrant, ConnectGrant.connection_id == ConnectConnection.id).where(
        ConnectConnection.channel_id == channel_id, ConnectGrant.user_id == user_id,
    )).first()
    if not row:
        return None
    status, source, target, home, revoked = row
    if revoked is not None or status not in {"pending", "active"}:
        return None
    if home not in {source, target} or (home == target and status != "active"):
        return None
    if any(not session.exec(select(Workspace.id).where(Workspace.id == identifier)).first()
           for identifier in (source, target)):
        return None
    owner = session.exec(select(WorkspaceChannel.workspace_id).where(
        WorkspaceChannel.channel_id == channel_id,
    )).first()
    if owner != source or not session.exec(select(SecureChannel.channel_id).where(
        SecureChannel.channel_id == channel_id,
    )).first():
        return None
    role = session.exec(select(WorkspaceMember.role).join(
        Workspace, Workspace.id == WorkspaceMember.workspace_id,
    ).where(WorkspaceMember.workspace_id == home, WorkspaceMember.user_id == user_id)).first()
    user = session.exec(select(User.is_blocked).where(User.id == user_id)).first()
    principal = session.exec(select(WorkforcePrincipal.workspace_id, WorkforcePrincipal.active).where(
        WorkforcePrincipal.user_id == user_id,
    )).first()
    if role not in {"owner", "admin", "member", "guest"} or user is None or user:
        return None
    if principal is not None and (not principal[1] or principal[0] != home):
        return None
    if not session.exec(select(ChannelMember.user_id).where(
        ChannelMember.channel_id == channel_id, ChannelMember.user_id == user_id,
    )).first():
        return None
    return home


def shared_channel_allows(session, channel_id, user_id, workspace_id=None):
    home = shared_channel_home(session, channel_id, user_id)
    return home is not None and (workspace_id is None or home == workspace_id)


def require_connection_admin(session, workspace_id, connection_id, actor_id, *, lock=False):
    from .workspaces import lock_workspace, require_workspace_admin
    require_workspace_admin(session, workspace_id, actor_id)
    if lock:
        lock_workspace(session, workspace_id)
        result = session.execute(update(ConnectConnection).where(
            ConnectConnection.id == connection_id,
        ).values(status=ConnectConnection.status))
        if result.rowcount != 1:
            raise HTTPException(404, "Shared connection not found")
    connection = session.exec(select(ConnectConnection).where(
        ConnectConnection.id == connection_id,
    ).execution_options(populate_existing=True)).first()
    if not connection or workspace_id not in {connection.source_workspace_id, connection.target_workspace_id}:
        raise HTTPException(404, "Shared connection not found")
    if lock:
        # Administrators control grants without acquiring a content seat.
        # Serialize revocation against message/file writes on the same row.
        session.execute(update(Channel).where(Channel.id == connection.channel_id).values(name=Channel.name))
        session.expire_all()
        from .request_guard import recheck_actor
        recheck_actor(session, actor_id)
        require_workspace_admin(session, workspace_id, actor_id)
    return connection


def reject_shared_membership_changes(session, channel_id):
    if connection_for_channel(session, channel_id):
        raise HTTPException(409, "Each organization manages its own shared-channel members in Connect")


def _workspace_grants(session, workspace_id, user_id):
    from .sso_scope import require_workspace_scope
    require_workspace_scope(session, workspace_id)
    # Lifecycle cleanup must also find inactive principals and already-orphaned
    # grants, which the current-content authorization predicate correctly hides.
    return session.exec(select(ConnectGrant, ConnectConnection.channel_id).join(
        ConnectConnection, ConnectConnection.id == ConnectGrant.connection_id,
    ).where(ConnectGrant.workspace_id == workspace_id, ConnectGrant.user_id == user_id,
        (ConnectConnection.source_workspace_id == workspace_id) |
        (ConnectConnection.target_workspace_id == workspace_id))).all()


def workspace_grant_channel_ids(session, workspace_id, user_id):
    """Own-tenant shared seats for authorized remote-first offboarding."""
    return sorted({channel_id for _, channel_id in _workspace_grants(session, workspace_id, user_id)})


def revoke_workspace_grants(session, workspace_id, user_id):
    """Permanently revoke own grants in the caller's membership transaction."""
    rows = _workspace_grants(session, workspace_id, user_id)
    now = datetime.utcnow()
    for grant, channel_id in rows:
        grant.revoked_at = grant.revoked_at or now
        session.add(grant)
        member = session.get(ChannelMember, (channel_id, user_id))
        if member is not None:
            session.delete(member)
    return sorted({channel_id for _, channel_id in rows})


def channel_view_workspace_id(session, channel_id, user_id, preferred=None):
    """Project shared channels into the viewer's tenant; ownership stays intact."""
    from .sso_scope import current_workspace_scope
    from .workspaces import channel_workspace_id
    connection = connection_for_channel(session, channel_id)
    if connection:
        home = shared_channel_home(session, channel_id, user_id)
        scope = current_workspace_scope(session)
        return home if home and (scope is None or scope == home) and (preferred is None or preferred == home) else None
    return channel_workspace_id(session, channel_id)


def shared_channel_metadata(session, channel_id):
    connection = connection_for_channel(session, channel_id)
    return {"shared": connection is not None,
            "owner_workspace_id": connection.source_workspace_id if connection else None,
            "connect_connection_id": connection.id if connection else None}
