from fastapi import APIRouter, Depends, HTTPException
from fastapi import Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import update
from sqlmodel import select
from datetime import datetime, timedelta, timezone
from typing import Literal
import hashlib
import secrets
from ..database import get_session
from ..models.channel import Channel, ChannelMember, ChannelInvitation
from ..models.user import User
from ..models.workspace import WorkspaceChannel, WorkspaceMember
from ..models.direct_message import DirectMessageState
from ..services.access import require_channel_member, lock_channel_writes
from ..services.connect import (channel_view_workspace_id, reject_shared_membership_changes,
                                shared_channel_metadata, connection_for_channel)
from ..services.direct_messages import open_direct_channel
from ..services.workspaces import (
    channel_workspace_id, channel_security_metadata, ensure_legacy_member, lock_workspace, prepare_channel_workspace,
    require_workspace_member, workspace_allows_channel, LEGACY_WORKSPACE_ID,
)
from ..services.matrix_bridge import remove_secure_member, sync_secure_membership
from ..services.auth import get_current_user
from ..services.group_crypto import GroupCryptoService
from ..realtime.socket import broadcast_to_channel, send_to_user
from uuid import uuid4

router = APIRouter(prefix="/channels", tags=["channels"])


class CreateChannel(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    type: Literal["group", "direct", "public"] = "group"
    member_ids: list[str] = Field(default_factory=list, max_length=100)
    workspace_id: str | None = Field(default=None, min_length=1, max_length=100)


class ChannelResponse(BaseModel):
    id: str
    name: str | None
    type: str
    encrypted_channel_key: str | None = None
    role: str = "member"
    workspace_id: str | None = None
    secure_sync_pending: bool = False
    secure: bool = False
    matrix_room_id: str | None = None
    shared: bool = False
    owner_workspace_id: str | None = None
    connect_connection_id: str | None = None


class CreateInvitation(BaseModel):
    expires_in_hours: int = Field(default=24, ge=1, le=168)


class AcceptInvitation(BaseModel):
    token: str = Field(min_length=32, max_length=128)


class ChangeMemberRole(BaseModel):
    role: Literal["admin", "member"]


def require_member(session, channel_id: str, user_id: str):
    return require_channel_member(session, channel_id, user_id)


def reject_direct_membership_changes(session, channel_id: str):
    reject_shared_membership_changes(session, channel_id)
    if session.get(Channel, channel_id).type in {"direct", "group_direct"}:
        raise HTTPException(409, "Direct conversation membership is managed through its conversation controls")


def require_admin(session, channel_id: str, user_id: str):
    member = require_member(session, channel_id, user_id)
    if member.role != "admin":
        raise HTTPException(403, "Channel admin access required")
    return member


def lock_channel(session, channel_id: str):
    workspace_id = channel_workspace_id(session, channel_id)
    if workspace_id and not connection_for_channel(session, channel_id):
        lock_workspace(session, workspace_id)
    lock_channel_writes(session, channel_id)


def protect_last_admin(session, member):
    if member.role == "admin":
        admins = session.exec(select(ChannelMember).where(
            ChannelMember.channel_id == member.channel_id,
            ChannelMember.role == "admin",
        )).all()
        if len(admins) <= 1:
            raise HTTPException(409, "Promote another member before removing the last channel admin")


def invitation_metadata(invitation):
    def utc(value):
        return value.replace(tzinfo=timezone.utc) if value else None
    return {
        "id": invitation.id,
        "channel_id": invitation.channel_id,
        "created_by": invitation.created_by,
        "created_at": utc(invitation.created_at),
        "expires_at": utc(invitation.expires_at),
        "accepted_at": utc(invitation.accepted_at),
        "revoked_at": utc(invitation.revoked_at),
    }


@router.post("", response_model=ChannelResponse)
def create_channel(
    data: CreateChannel,
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    all_member_ids = set(data.member_ids) | {user.id}
    workspace_id = prepare_channel_workspace(session, user.id, data.workspace_id, all_member_ids)
    if data.type == "direct":
        others = all_member_ids - {user.id}
        if len(others) != 1:
            raise HTTPException(400, "Direct conversations require exactly one other participant")
        channel = open_direct_channel(session, workspace_id, user.id, next(iter(others)))
        member = require_member(session, channel.id, user.id)
        return ChannelResponse(id=channel.id, name=channel.name, type=channel.type,
                               workspace_id=workspace_id, role=member.role,
                               encrypted_channel_key=member.encrypted_channel_key,
                               **channel_security_metadata(session, channel.id))
    channel = Channel(id=uuid4().hex, name=data.name, type=data.type)

    # Generate channel encryption key
    channel_key = GroupCryptoService.generate_channel_key()
    
    # Get all members including creator
    all_member_ids = set(data.member_ids) | {user.id}
    
    # Get public keys for all members
    members = session.query(User).filter(User.id.in_(all_member_ids)).all()
    if len(members) != len(all_member_ids) or any(m.is_blocked for m in members):
        raise HTTPException(400, "All channel members must be active users")
    session.add(channel)
    session.flush()
    session.add(WorkspaceChannel(channel_id=channel.id, workspace_id=workspace_id))
    member_public_keys = {m.id: m.public_key for m in members if m.public_key}
    
    # Encrypt channel key for each member
    encrypted_keys = GroupCryptoService.encrypt_key_for_members(channel_key, member_public_keys)
    
    # Add members with their encrypted keys
    for member_id in all_member_ids:
        role = "admin" if member_id == user.id else "member"
        session.add(ChannelMember(
            channel_id=channel.id,
            user_id=member_id,
            role=role,
            encrypted_channel_key=encrypted_keys.get(member_id),
        ))
    
    session.commit()
    
    return ChannelResponse(
        id=channel.id,
        name=channel.name,
        type=channel.type,
        encrypted_channel_key=encrypted_keys.get(user.id),
        role="admin",
        workspace_id=workspace_id,
        **channel_security_metadata(session, channel.id),
    )


@router.get("")
def list_channels(
    workspace_id: str | None = None,
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    if workspace_id:
        require_workspace_member(session, workspace_id, user.id)
    results = (
        session.query(Channel, ChannelMember.encrypted_channel_key, ChannelMember.role)
        .join(ChannelMember)
        .filter(ChannelMember.user_id == user.id)
        .all()
    )
    
    return [
        ChannelResponse(
            id=channel.id,
            name=channel.name,
            type=channel.type,
            encrypted_channel_key=encrypted_key,
            role=role,
            workspace_id=channel_view_workspace_id(session, channel.id, user.id),
            **shared_channel_metadata(session, channel.id),
            **channel_security_metadata(session, channel.id),
        )
        for channel, encrypted_key, role in results
        if workspace_allows_channel(session, channel.id, user.id)
        and (not workspace_id or channel_view_workspace_id(session, channel.id, user.id) == workspace_id)
        and not (channel.type == "direct" and (state := session.get(DirectMessageState, (channel.id, user.id))) and state.archived)
    ]


@router.get("/{channel_id}", response_model=ChannelResponse)
def get_channel(
    channel_id: str,
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    channel = session.get(Channel, channel_id)
    if not channel:
        raise HTTPException(404, "Channel not found")

    member = require_member(session, channel_id, user.id)

    return ChannelResponse(
        id=channel.id,
        name=channel.name,
        type=channel.type,
        encrypted_channel_key=member.encrypted_channel_key,
        role=member.role,
        workspace_id=channel_view_workspace_id(session, channel_id, user.id),
        **shared_channel_metadata(session, channel_id),
        **channel_security_metadata(session, channel.id),
    )


@router.get("/{channel_id}/members")
def get_channel_members(
    channel_id: str,
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    require_member(session, channel_id, user.id)
    
    members = (
        session.query(User, ChannelMember.role)
        .join(ChannelMember, User.id == ChannelMember.user_id)
        .filter(ChannelMember.channel_id == channel_id)
        .all()
    )
    
    return [
        {"user_id": u.id, "name": u.name, "public_key": u.public_key, "role": role}
        for u, role in members
        if workspace_allows_channel(session, channel_id, u.id)
    ]


@router.post("/{channel_id}/invites", status_code=201)
def create_invitation(
    channel_id: str,
    response: Response,
    data: CreateInvitation = CreateInvitation(),
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    lock_channel(session, channel_id)
    require_admin(session, channel_id, user.id)
    reject_direct_membership_changes(session, channel_id)
    now = datetime.utcnow()
    active = session.exec(select(ChannelInvitation).where(
        ChannelInvitation.channel_id == channel_id,
        ChannelInvitation.accepted_at == None,
        ChannelInvitation.revoked_at == None,
        ChannelInvitation.expires_at > now,
    )).all()
    if len(active) >= 100:
        raise HTTPException(409, "Revoke an active invitation before creating another")
    token = secrets.token_urlsafe(32)
    invitation = ChannelInvitation(
        id=uuid4().hex,
        channel_id=channel_id,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        created_by=user.id,
        expires_at=now + timedelta(hours=data.expires_in_hours),
    )
    session.add(invitation)
    session.commit()
    response.headers["Cache-Control"] = "no-store"
    return {**invitation_metadata(invitation), "token": token}


@router.get("/{channel_id}/invites")
def list_invitations(
    channel_id: str,
    limit: int = Query(50, ge=1, le=100),
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    require_admin(session, channel_id, user.id)
    reject_direct_membership_changes(session, channel_id)
    invitations = session.exec(select(ChannelInvitation).where(
        ChannelInvitation.channel_id == channel_id,
    ).order_by(ChannelInvitation.created_at.desc()).limit(limit)).all()
    return [invitation_metadata(invitation) for invitation in invitations]


@router.delete("/{channel_id}/invites/{invitation_id}")
def revoke_invitation(
    channel_id: str,
    invitation_id: str,
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    lock_channel(session, channel_id)
    require_admin(session, channel_id, user.id)
    reject_direct_membership_changes(session, channel_id)
    invitation = session.get(ChannelInvitation, invitation_id)
    if not invitation or invitation.channel_id != channel_id:
        raise HTTPException(404, "Invitation not found")
    if not invitation.revoked_at:
        invitation.revoked_at = datetime.utcnow()
        session.add(invitation)
        session.commit()
    return {"revoked": True, "id": invitation.id}


@router.post("/invites/accept", response_model=ChannelResponse)
async def accept_invitation(
    data: AcceptInvitation,
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    token_hash = hashlib.sha256(data.token.encode()).hexdigest()
    invitation = session.exec(select(ChannelInvitation).where(
        ChannelInvitation.token_hash == token_hash,
    )).first()
    if not invitation:
        raise HTTPException(404, "Invitation is unavailable or expired")
    lock_channel(session, invitation.channel_id)
    session.refresh(invitation)
    reject_direct_membership_changes(session, invitation.channel_id)
    now = datetime.utcnow()
    if invitation.accepted_at or invitation.revoked_at or invitation.expires_at <= now:
        raise HTTPException(404, "Invitation is unavailable or expired")
    # Invitations cannot outlive the issuer's active channel-admin access.
    issuer = session.get(User, invitation.created_by)
    issuer_membership = session.get(ChannelMember, (invitation.channel_id, invitation.created_by))
    if not issuer or issuer.is_blocked or not issuer_membership or issuer_membership.role != "admin" or not workspace_allows_channel(session, invitation.channel_id, issuer.id):
        raise HTTPException(404, "Invitation is unavailable or expired")
    if session.get(ChannelMember, (invitation.channel_id, user.id)):
        raise HTTPException(409, "Already a channel member")
    consumed = session.execute(update(ChannelInvitation).where(
        ChannelInvitation.id == invitation.id,
        ChannelInvitation.accepted_at == None,
        ChannelInvitation.revoked_at == None,
        ChannelInvitation.expires_at > now,
    ).values(accepted_at=now, accepted_by=user.id))
    if consumed.rowcount != 1:
        raise HTTPException(404, "Invitation is unavailable or expired")
    workspace_id = channel_workspace_id(session, invitation.channel_id)
    if workspace_id and not session.get(WorkspaceMember, (workspace_id, user.id)):
        if workspace_id == LEGACY_WORKSPACE_ID:
            ensure_legacy_member(session, user.id)
        else:
            # A channel invitation grants a new teammate access only to the
            # invited channel. Workspace administrators can upgrade this role.
            session.add(WorkspaceMember(workspace_id=workspace_id, user_id=user.id, role="guest"))
    session.add(ChannelMember(channel_id=invitation.channel_id, user_id=user.id, role="member"))
    session.commit()
    # Native membership is durable before Matrix can invite the person. On
    # outage, the explicit secure sync endpoint retries without consuming a new invite.
    secure_sync_pending = False
    try:
        sync_secure_membership(session, invitation.channel_id)
        session.commit()
    except HTTPException as exc:
        if exc.status_code != 503:
            raise
        # Preserve identity records created during a partial remote sync. The
        # accepted invitation remains successful and access can be retried.
        session.commit()
        secure_sync_pending = True
    channel = session.get(Channel, invitation.channel_id)
    await broadcast_to_channel(channel.id, "members_changed", {"channel_id": channel.id})
    return ChannelResponse(id=channel.id, name=channel.name, type=channel.type, role="member", workspace_id=workspace_id, secure_sync_pending=secure_sync_pending, **channel_security_metadata(session, channel.id))


@router.patch("/{channel_id}/members/{member_user_id}")
async def change_member_role(
    channel_id: str,
    member_user_id: str,
    data: ChangeMemberRole,
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    lock_channel(session, channel_id)
    require_admin(session, channel_id, user.id)
    reject_direct_membership_changes(session, channel_id)
    member = require_member(session, channel_id, member_user_id)
    if member.role != data.role:
        if data.role == "member":
            protect_last_admin(session, member)
        member.role = data.role
        session.add(member)
        session.commit()
    await broadcast_to_channel(channel_id, "members_changed", {"channel_id": channel_id})
    return {"user_id": member_user_id, "role": member.role}


async def remove_membership(session, channel_id, member_user_id, actor_id):
    lock_channel(session, channel_id)
    actor = require_member(session, channel_id, actor_id)
    connection = connection_for_channel(session, channel_id)
    if connection and member_user_id == actor_id:
        from ..models.connect import ConnectGrant
        from .connect import revoke_grants
        grant = session.get(ConnectGrant, (connection.id, actor_id))
        if not grant:
            raise HTTPException(403, "Explicit shared-channel membership required")
        pending = await revoke_grants(session, connection, [grant])
        return {"removed": True, "user_id": actor_id, "channel_id": channel_id,
                "secure_sync_pending": pending}
    reject_direct_membership_changes(session, channel_id)
    if actor_id != member_user_id and actor.role != "admin":
        raise HTTPException(403, "Channel admin access required")
    member = require_member(session, channel_id, member_user_id)
    protect_last_admin(session, member)
    remove_secure_member(session, channel_id, member_user_id)
    session.delete(member)
    session.commit()
    # This broadcast evicts removed sessions before notifying remaining members.
    await broadcast_to_channel(channel_id, "members_changed", {"channel_id": channel_id})
    await send_to_user(member_user_id, "channel_removed", {"channel_id": channel_id})
    return {"removed": True, "user_id": member_user_id, "channel_id": channel_id}


@router.delete("/{channel_id}/members/{member_user_id}")
async def remove_member(
    channel_id: str,
    member_user_id: str,
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    return await remove_membership(session, channel_id, member_user_id, user.id)


@router.post("/{channel_id}/leave")
async def leave_channel(
    channel_id: str,
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    return await remove_membership(session, channel_id, user.id, user.id)
