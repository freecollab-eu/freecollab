"""Two organizations sharing an encrypted channel on this installation."""
from datetime import datetime, timedelta
from hashlib import sha256
import secrets
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, update
from sqlmodel import select

from ..config import settings
from ..database import get_session
from ..models.channel import Channel, ChannelMember
from ..models.connect import ConnectConnection, ConnectGrant
from ..models.secure_channel import SecureChannel
from ..models.user import User
from ..models.workspace import Workspace, WorkspaceChannel, WorkspaceMember
from ..realtime.socket import broadcast_to_channel, send_to_user
from ..services.auth import get_current_user
from ..services.connect import require_connection_admin
from ..services.matrix_bridge import create_secure_room, remove_secure_member, require_matrix, sync_secure_membership
from ..services.workforce_access import principal_snapshot, require_principal_workspace
from ..services.workspaces import lock_workspace, require_workspace_admin

router = APIRouter(prefix="/connect", tags=["shared channels"])


class CreateConnection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    target_workspace_id: str = Field(min_length=1, max_length=100)
    expires_in_hours: int = Field(default=24, ge=1, le=168)


class AcceptConnection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=32, max_length=128)


class GrantMember(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(min_length=1, max_length=128)


def connection_data(session, connection):
    source = session.get(Workspace, connection.source_workspace_id)
    target = session.get(Workspace, connection.target_workspace_id)
    channel = session.get(Channel, connection.channel_id)
    return {"id": connection.id, "channel_id": connection.channel_id,
            "name": channel.name if channel else "Shared channel",
            "source_workspace_id": connection.source_workspace_id,
            "target_workspace_id": connection.target_workspace_id,
            "source_workspace_name": source.name if source else "Unavailable workspace",
            "target_workspace_name": target.name if target else "Unavailable workspace",
            "status": connection.status, "created_at": connection.created_at,
            "expires_at": connection.expires_at, "accepted_at": connection.accepted_at,
            "revoked_at": connection.revoked_at, "transport": "local", "secure": True,
            "secure_sync_pending": connection.secure_sync_pending}


def source_authority_valid(session, connection):
    role = session.exec(select(WorkspaceMember.role).where(
        WorkspaceMember.workspace_id == connection.source_workspace_id,
        WorkspaceMember.user_id == connection.created_by,
    )).first()
    blocked = session.exec(select(User.is_blocked).where(User.id == connection.created_by)).first()
    principal = principal_snapshot(session, connection.created_by)
    return role in {"owner", "admin"} and blocked is False and (
        principal is None or (principal[1] and principal[0] == connection.source_workspace_id))


@router.get("/capabilities")
def capabilities(user=Depends(get_current_user)):
    return {"local_shared_channels": True, "cross_installation": False,
            "encryption_configured": bool(getattr(settings, "matrix_enabled", False)),
            "transport": "local",
            "external_requirement": "Separate installations require an operator-managed federated Matrix/provider integration; this endpoint does not enable federation."}


@router.get("/workspaces/{workspace_id}/connections")
def list_connections(workspace_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    require_workspace_admin(session, workspace_id, user.id)
    connections = session.exec(select(ConnectConnection).where(or_(
        ConnectConnection.source_workspace_id == workspace_id,
        ConnectConnection.target_workspace_id == workspace_id,
    )).order_by(ConnectConnection.created_at.desc()).limit(100)).all()
    return [connection_data(session, connection) for connection in connections]


@router.post("/workspaces/{workspace_id}/connections", status_code=201)
def create_connection(workspace_id: str, data: CreateConnection, response: Response,
                      user=Depends(get_current_user), session=Depends(get_session)):
    require_workspace_admin(session, workspace_id, user.id)
    require_matrix()
    lock_workspace(session, workspace_id)
    session.expire_all()
    from ..services.request_guard import recheck_actor
    recheck_actor(session, user.id)
    require_workspace_admin(session, workspace_id, user.id)
    if data.target_workspace_id == workspace_id:
        raise HTTPException(422, "Choose a different organization workspace")
    if not session.get(Workspace, data.target_workspace_id):
        raise HTTPException(404, "Target workspace not found")
    if not data.name.strip():
        raise HTTPException(422, "Shared channel name is required")
    open_connections = session.exec(select(ConnectConnection.id).where(
        ConnectConnection.source_workspace_id == workspace_id,
        ConnectConnection.status.in_(["pending", "active"]),
    ).limit(100)).all()
    if len(open_connections) >= 100:
        raise HTTPException(409, "Revoke a connection before creating another")
    now, token = datetime.utcnow(), secrets.token_urlsafe(32)
    channel = Channel(id=uuid4().hex, name=data.name.strip(), type="group")
    session.add(channel)
    session.flush()
    session.add(WorkspaceChannel(channel_id=channel.id, workspace_id=workspace_id))
    session.add(ChannelMember(channel_id=channel.id, user_id=user.id, role="admin"))
    binding = SecureChannel(channel_id=channel.id, created_by=user.id)
    session.add(binding)
    connection = ConnectConnection(channel_id=channel.id, source_workspace_id=workspace_id,
        target_workspace_id=data.target_workspace_id, created_by=user.id,
        token_hash=sha256(token.encode()).hexdigest(), expires_at=now + timedelta(hours=data.expires_in_hours))
    session.add(connection)
    session.flush()
    session.add(ConnectGrant(connection_id=connection.id, user_id=user.id,
                             workspace_id=workspace_id, created_by=user.id))
    # The encrypted fence is durable before remote provisioning can commit.
    session.commit()
    try:
        create_secure_room(session, binding)
        sync_secure_membership(session, channel.id)
        session.commit()
    except HTTPException as exc:
        if exc.status_code != 503:
            raise
        connection.secure_sync_pending = True
        session.add(connection)
        session.commit()
    response.headers["Cache-Control"] = "no-store"
    return {**connection_data(session, connection), "invitation_token": token}


@router.post("/workspaces/{workspace_id}/accept")
def accept_connection(workspace_id: str, data: AcceptConnection,
                      user=Depends(get_current_user), session=Depends(get_session)):
    require_workspace_admin(session, workspace_id, user.id)
    connection = session.exec(select(ConnectConnection).where(
        ConnectConnection.token_hash == sha256(data.token.encode()).hexdigest(),
        ConnectConnection.target_workspace_id == workspace_id,
    )).first()
    if not connection:
        raise HTTPException(404, "Invitation is unavailable or expired")
    connection = require_connection_admin(session, workspace_id, connection.id, user.id, lock=True)
    now = datetime.utcnow()
    if connection.status != "pending" or connection.expires_at <= now or not source_authority_valid(session, connection):
        raise HTTPException(404, "Invitation is unavailable or expired")
    accepted = session.execute(update(ConnectConnection).where(
        ConnectConnection.id == connection.id, ConnectConnection.status == "pending",
        ConnectConnection.expires_at > now,
    ).values(status="active", accepted_at=now, accepted_by=user.id))
    if accepted.rowcount != 1:
        raise HTTPException(404, "Invitation is unavailable or expired")
    session.commit()
    session.refresh(connection)
    # Acceptance establishes organizational consent, never channel membership.
    return connection_data(session, connection)


@router.get("/workspaces/{workspace_id}/connections/{connection_id}/members")
def list_members(workspace_id: str, connection_id: str,
                 user=Depends(get_current_user), session=Depends(get_session)):
    require_connection_admin(session, workspace_id, connection_id, user.id)
    grants = session.exec(select(ConnectGrant, User.name).join(User, User.id == ConnectGrant.user_id).where(
        ConnectGrant.connection_id == connection_id, ConnectGrant.workspace_id == workspace_id,
    )).all()
    return [{"user_id": grant.user_id, "name": name, "workspace_id": workspace_id,
             "active": grant.revoked_at is None} for grant, name in grants]


@router.post("/workspaces/{workspace_id}/connections/{connection_id}/members", status_code=201)
async def grant_member(workspace_id: str, connection_id: str, data: GrantMember,
                       user=Depends(get_current_user), session=Depends(get_session)):
    connection = require_connection_admin(session, workspace_id, connection_id, user.id, lock=True)
    if connection.status != "active":
        raise HTTPException(409, "Both organizations must accept before adding members")
    require_principal_workspace(session, data.user_id, workspace_id)
    member = session.get(WorkspaceMember, (workspace_id, data.user_id))
    target = session.get(User, data.user_id)
    principal = principal_snapshot(session, data.user_id)
    if not member or member.role not in {"owner", "admin", "member", "guest"} or not target or target.is_blocked or (principal is not None and not principal[1]):
        raise HTTPException(404, "Active member of your own workspace required")
    grant = session.get(ConnectGrant, (connection.id, data.user_id))
    if grant and grant.workspace_id != workspace_id:
        raise HTTPException(409, "This account already participates through its other organization")
    if not grant:
        grant = ConnectGrant(connection_id=connection.id, user_id=data.user_id,
                             workspace_id=workspace_id, created_by=user.id)
    grant.revoked_at = None
    session.add(grant)
    if not session.get(ChannelMember, (connection.channel_id, data.user_id)):
        session.add(ChannelMember(channel_id=connection.channel_id, user_id=data.user_id, role="member"))
    # The newly enrolled member performs authenticated Matrix sync when opening
    # the channel. An administrator does not gain a seat or content access here.
    connection.secure_sync_pending = True
    session.add(connection)
    session.commit()
    await send_to_user(data.user_id, "channels_changed", {"workspace_id": workspace_id})
    await broadcast_to_channel(connection.channel_id, "members_changed", {"channel_id": connection.channel_id})
    return {"user_id": data.user_id, "workspace_id": workspace_id, "active": True, "secure_sync_pending": True}


async def revoke_grants(session, connection, grants):
    channel_id = connection.channel_id
    affected = [grant.user_id for grant in grants if grant.revoked_at is None]
    homes = {grant.user_id: grant.workspace_id for grant in grants}
    now = datetime.utcnow()
    for grant in grants:
        grant.revoked_at = grant.revoked_at or now
        session.add(grant)
        membership = session.get(ChannelMember, (channel_id, grant.user_id))
        if membership:
            session.delete(membership)
    # Gate REST, sockets and the Matrix proxy immediately, even during an outage.
    connection.secure_sync_pending = bool(affected)
    session.add(connection)
    session.commit()
    pending = False
    for member_id in affected:
        try:
            remove_secure_member(session, channel_id, member_id)
        except HTTPException as exc:
            if exc.status_code != 503:
                raise
            pending = True
        await send_to_user(member_id, "channel_removed", {"channel_id": channel_id, "workspace_id": homes[member_id]})
    connection.secure_sync_pending = pending
    session.add(connection)
    session.commit()
    await broadcast_to_channel(channel_id, "members_changed", {"channel_id": channel_id})
    return pending


@router.delete("/workspaces/{workspace_id}/connections/{connection_id}/members/{member_user_id}")
async def revoke_member(workspace_id: str, connection_id: str, member_user_id: str,
                        user=Depends(get_current_user), session=Depends(get_session)):
    connection = require_connection_admin(session, workspace_id, connection_id, user.id, lock=True)
    grant = session.get(ConnectGrant, (connection_id, member_user_id))
    if not grant or grant.workspace_id != workspace_id:
        raise HTTPException(404, "Your organization's member grant was not found")
    pending = await revoke_grants(session, connection, [grant])
    return {"removed": True, "secure_sync_pending": pending}


@router.delete("/workspaces/{workspace_id}/connections/{connection_id}")
async def revoke_connection(workspace_id: str, connection_id: str,
                            user=Depends(get_current_user), session=Depends(get_session)):
    connection = require_connection_admin(session, workspace_id, connection_id, user.id, lock=True)
    connection.status, connection.revoked_at = "revoked", connection.revoked_at or datetime.utcnow()
    session.add(connection)
    grants = session.exec(select(ConnectGrant).where(ConnectGrant.connection_id == connection_id)).all()
    pending = await revoke_grants(session, connection, grants)
    return {"revoked": True, "secure_sync_pending": pending, **connection_data(session, connection)}


@router.post("/workspaces/{workspace_id}/connections/{connection_id}/retry-sync")
def retry_sync(workspace_id: str, connection_id: str,
                user=Depends(get_current_user), session=Depends(get_session)):
    connection = require_connection_admin(session, workspace_id, connection_id, user.id, lock=True)
    if connection.status == "revoked":
        # Retry remote removal without restoring any product membership.
        grants = session.exec(select(ConnectGrant).where(ConnectGrant.connection_id == connection_id)).all()
        for grant in grants:
            remove_secure_member(session, connection.channel_id, grant.user_id)
    else:
        # Admins without an explicit content grant cannot provision/read the room.
        from ..services.access import require_channel_member
        require_channel_member(session, connection.channel_id, user.id)
        binding = session.get(SecureChannel, connection.channel_id)
        if not binding.matrix_room_id:
            create_secure_room(session, binding)
        sync_secure_membership(session, connection.channel_id)
    connection.secure_sync_pending = False
    session.add(connection)
    session.commit()
    return connection_data(session, connection)
