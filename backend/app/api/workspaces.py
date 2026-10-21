"""Workspace identity, roles and invitation lifecycle."""
import hashlib
import re
import secrets
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import update
from sqlmodel import select

from ..config import settings
from ..database import get_session
from ..models.channel import Channel, ChannelMember
from ..models.user import User
from ..models.workspace import Workspace, WorkspaceChannel, WorkspaceInvitation, WorkspaceMember
from ..models.workforce import WorkforceInvitation
from ..realtime.socket import broadcast_to_channel, send_to_user
from ..services.auth import get_current_user
from ..services.sso_scope import current_workspace_scope, require_unrestricted
from ..services.workspaces import (
    ensure_personal_workspace_membership, lock_workspace, require_workspace_admin,
    require_workspace_member, visible_workspace_user_ids, workspace_allows_channel,
)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkspaceInput(StrictModel):
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class InviteInput(StrictModel):
    role: Literal["admin", "member", "guest"] = "member"
    expires_in_hours: int = Field(default=24, ge=1, le=168)


class AcceptInvite(StrictModel):
    token: str = Field(min_length=32, max_length=128)


class MemberRole(StrictModel):
    role: Literal["owner", "admin", "member", "guest"]


def workspace_response(session, workspace, member):
    count = len(session.exec(select(WorkspaceMember.user_id).where(
        WorkspaceMember.workspace_id == workspace.id,
    )).all())
    return {
        "id": workspace.id, "name": workspace.name, "role": member.role,
        "logo_url": f"/workspaces/{workspace.id}/logo" if workspace.logo_filename else None,
        "is_legacy": workspace.is_legacy, "member_count": count,
    }


def invitation_response(invitation):
    def utc(value):
        return value.replace(tzinfo=timezone.utc) if value else None
    return {
        "id": invitation.id, "workspace_id": invitation.workspace_id, "role": invitation.role,
        "created_by": invitation.created_by, "created_at": utc(invitation.created_at),
        "expires_at": utc(invitation.expires_at), "accepted_at": utc(invitation.accepted_at),
        "revoked_at": utc(invitation.revoked_at),
    }


@router.get("")
def list_workspaces(response: Response, user=Depends(get_current_user), session=Depends(get_session)):
    ensure_personal_workspace_membership(session, user.id)
    session.commit()
    rows = session.exec(select(Workspace, WorkspaceMember).join(
        WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id,
    ).where(WorkspaceMember.user_id == user.id).order_by(Workspace.created_at, Workspace.id)).all()
    response.headers["Cache-Control"] = "private, no-store"
    scope = current_workspace_scope(session)
    return [workspace_response(session, workspace, member) for workspace, member in rows
            if scope is None or workspace.id == scope]


@router.post("", status_code=201)
def create_workspace(data: WorkspaceInput, user=Depends(get_current_user), session=Depends(get_session)):
    require_unrestricted(session)
    memberships = session.exec(select(WorkspaceMember).where(WorkspaceMember.user_id == user.id)).all()
    if len(memberships) >= 50:
        raise HTTPException(409, "Workspace membership limit reached")
    workspace = Workspace(name=data.name)
    session.add(workspace)
    session.flush()
    member = WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner")
    session.add(member)
    session.commit()
    return workspace_response(session, workspace, member)


@router.get("/{workspace_id}")
def get_workspace(workspace_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    member = require_workspace_member(session, workspace_id, user.id)
    return workspace_response(session, session.get(Workspace, workspace_id), member)


@router.patch("/{workspace_id}")
def rename_workspace(workspace_id: str, data: WorkspaceInput, user=Depends(get_current_user), session=Depends(get_session)):
    lock_workspace(session, workspace_id)
    member = require_workspace_admin(session, workspace_id, user.id)
    workspace = session.get(Workspace, workspace_id)
    workspace.name = data.name
    session.add(workspace)
    session.commit()
    return workspace_response(session, workspace, member)


@router.get("/{workspace_id}/members")
def list_members(workspace_id: str, query: str = Query("", max_length=100),
                 user=Depends(get_current_user), session=Depends(get_session)):
    visible_ids = visible_workspace_user_ids(session, workspace_id, user.id)
    rows = session.exec(select(User, WorkspaceMember).join(
        WorkspaceMember, WorkspaceMember.user_id == User.id,
    ).where(WorkspaceMember.workspace_id == workspace_id, User.id.in_(visible_ids))
      .order_by(User.name, User.id)).all()
    needle = query.strip().casefold()
    return [{"user_id": person.id, "name": person.name, "role": member.role, "active": not person.is_blocked}
            for person, member in rows if not needle or needle in person.name.casefold()][:100]


@router.post("/{workspace_id}/invites", status_code=201)
def create_invitation(workspace_id: str, response: Response, data: InviteInput = InviteInput(),
                      user=Depends(get_current_user), session=Depends(get_session)):
    lock_workspace(session, workspace_id)
    actor = require_workspace_admin(session, workspace_id, user.id)
    if data.role == "admin" and actor.role != "owner":
        raise HTTPException(403, "Only an owner can invite an administrator")
    now = datetime.utcnow()
    active = session.exec(select(WorkspaceInvitation).where(
        WorkspaceInvitation.workspace_id == workspace_id, WorkspaceInvitation.accepted_at == None,
        WorkspaceInvitation.revoked_at == None, WorkspaceInvitation.expires_at > now,
    )).all()
    if len(active) >= 100:
        raise HTTPException(409, "Revoke an active invitation before creating another")
    token = secrets.token_urlsafe(32)
    invitation = WorkspaceInvitation(workspace_id=workspace_id, role=data.role, created_by=user.id,
                                     token_hash=hashlib.sha256(token.encode()).hexdigest(),
                                     expires_at=now + timedelta(hours=data.expires_in_hours))
    session.add(invitation)
    session.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return {**invitation_response(invitation), "token": token}


@router.get("/{workspace_id}/invites")
def list_invitations(workspace_id: str, response: Response, user=Depends(get_current_user), session=Depends(get_session)):
    require_workspace_admin(session, workspace_id, user.id)
    response.headers["Cache-Control"] = "private, no-store"
    return [invitation_response(row) for row in session.exec(select(WorkspaceInvitation).where(
        WorkspaceInvitation.workspace_id == workspace_id,
    ).order_by(WorkspaceInvitation.created_at.desc()).limit(100)).all()]


@router.delete("/{workspace_id}/invites/{invitation_id}")
def revoke_invitation(workspace_id: str, invitation_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    lock_workspace(session, workspace_id)
    require_workspace_admin(session, workspace_id, user.id)
    invitation = session.get(WorkspaceInvitation, invitation_id)
    if not invitation or invitation.workspace_id != workspace_id:
        raise HTTPException(404, "Invitation not found")
    if not invitation.revoked_at:
        invitation.revoked_at = datetime.utcnow()
        session.add(invitation)
        session.commit()
    return {"revoked": True}


@router.post("/invites/accept")
def accept_invitation(data: AcceptInvite, user=Depends(get_current_user), session=Depends(get_session)):
    require_unrestricted(session)
    invitation = session.exec(select(WorkspaceInvitation).where(
        WorkspaceInvitation.token_hash == hashlib.sha256(data.token.encode()).hexdigest(),
    )).first()
    unavailable = HTTPException(404, "Invitation is unavailable or expired")
    if not invitation:
        raise unavailable
    if session.get(WorkforceInvitation, invitation.id):
        raise HTTPException(403, "Use your organization's sign-in link to accept this invitation")
    lock_workspace(session, invitation.workspace_id)
    session.refresh(invitation)
    now = datetime.utcnow()
    issuer = session.get(User, invitation.created_by)
    issuer_role = session.get(WorkspaceMember, (invitation.workspace_id, invitation.created_by))
    if (invitation.accepted_at or invitation.revoked_at or invitation.expires_at <= now
            or not issuer or issuer.is_blocked or not issuer_role or issuer_role.role not in {"owner", "admin"}
            or (invitation.role == "admin" and issuer_role.role != "owner")):
        raise unavailable
    if session.get(WorkspaceMember, (invitation.workspace_id, user.id)):
        raise HTTPException(409, "Already a workspace member")
    result = session.execute(update(WorkspaceInvitation).where(
        WorkspaceInvitation.id == invitation.id, WorkspaceInvitation.accepted_at == None,
        WorkspaceInvitation.revoked_at == None, WorkspaceInvitation.expires_at > now,
    ).values(accepted_at=now, accepted_by=user.id))
    if result.rowcount != 1:
        raise unavailable
    member = WorkspaceMember(workspace_id=invitation.workspace_id, user_id=user.id, role=invitation.role)
    session.add(member)
    session.commit()
    return workspace_response(session, session.get(Workspace, invitation.workspace_id), member)


def protect_owner(session, member):
    if member.role == "owner":
        owners = session.exec(select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == member.workspace_id, WorkspaceMember.role == "owner",
        )).all()
        if len(owners) <= 1:
            raise HTTPException(409, "Promote another owner before leaving or demoting the last owner")


@router.patch("/{workspace_id}/members/{member_user_id}")
def change_member_role(workspace_id: str, member_user_id: str, data: MemberRole,
                       user=Depends(get_current_user), session=Depends(get_session)):
    lock_workspace(session, workspace_id)
    actor = require_workspace_admin(session, workspace_id, user.id)
    target = require_workspace_member(session, workspace_id, member_user_id)
    if data.role == "owner":
        from ..services.workforce_access import is_managed
        if is_managed(session, member_user_id):
            raise HTTPException(403, "Organization-managed accounts cannot own workspaces")
    if actor.role != "owner" and (target.role in {"owner", "admin"} or data.role in {"owner", "admin"}):
        raise HTTPException(403, "Only an owner can manage owner and administrator roles")
    if target.role != data.role:
        protect_owner(session, target)
        target.role = data.role
        session.add(target)
        session.commit()
    return {"user_id": member_user_id, "role": target.role}


async def remove_workspace_membership(session, workspace_id: str, member_user_id: str, actor_id: str):
    lock_workspace(session, workspace_id)
    actor = require_workspace_member(session, workspace_id, actor_id)
    target = require_workspace_member(session, workspace_id, member_user_id)
    if actor_id != member_user_id:
        if actor.role not in {"owner", "admin"}:
            raise HTTPException(403, "Workspace administrator access required")
        if target.role in {"owner", "admin"} and actor.role != "owner":
            raise HTTPException(403, "Only an owner can remove an administrator")
    protect_owner(session, target)
    affected = session.exec(select(Channel, ChannelMember).join(
        ChannelMember, ChannelMember.channel_id == Channel.id,
    ).join(WorkspaceChannel, WorkspaceChannel.channel_id == Channel.id).where(
        WorkspaceChannel.workspace_id == workspace_id, ChannelMember.user_id == member_user_id,
    )).all()
    handovers = []
    from ..services.matrix_bridge import remove_secure_member
    # Complete every remote revocation before mutating native memberships.
    # Partial remote failure leaves native access unchanged and can be retried.
    for channel, _ in affected:
        remove_secure_member(session, channel.id, member_user_id)
    for channel, membership in affected:
        if channel.type == "direct":
            # Keep the canonical participant pair and retained history. The
            # workspace guard revokes this participant's access immediately.
            continue
        if membership.role == "admin":
            remaining = session.exec(select(ChannelMember).where(
                ChannelMember.channel_id == channel.id, ChannelMember.user_id != member_user_id,
            ).order_by(ChannelMember.joined_at, ChannelMember.user_id)).all()
            remaining = [row for row in remaining if workspace_allows_channel(session, channel.id, row.user_id)]
            if remaining and not any(row.role == "admin" for row in remaining):
                successor = remaining[0]
                successor.role = "admin"
                session.add(successor)
                handovers.append({"channel_id": channel.id, "user_id": successor.user_id})
        session.delete(membership)
    session.delete(target)
    session.commit()
    for channel, _ in affected:
        await broadcast_to_channel(channel.id, "members_changed", {"channel_id": channel.id})
        await send_to_user(member_user_id, "channel_removed", {"channel_id": channel.id})
    await send_to_user(member_user_id, "workspace_removed", {"workspace_id": workspace_id})
    return {"removed": True, "user_id": member_user_id, "workspace_id": workspace_id, "admin_handovers": handovers}


@router.delete("/{workspace_id}/members/{member_user_id}")
async def remove_member(workspace_id: str, member_user_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    return await remove_workspace_membership(session, workspace_id, member_user_id, user.id)


@router.post("/{workspace_id}/leave")
async def leave_workspace(workspace_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    return await remove_workspace_membership(session, workspace_id, user.id, user.id)


def logo_directory():
    directory = Path(settings.storage_path).resolve() / "workspace-logos"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def jpeg_dimensions(content):
    position = 2
    while position + 4 <= len(content):
        if content[position] != 0xff:
            return None
        while position < len(content) and content[position] == 0xff:
            position += 1
        if position >= len(content):
            return None
        marker = content[position]
        position += 1
        if marker in {0xd8, 0xd9}:
            continue
        if marker == 0xda or position + 2 > len(content):
            return None
        size = struct.unpack(">H", content[position:position + 2])[0]
        if size < 2 or position + size > len(content):
            return None
        if marker in {0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf}:
            if size < 8:
                return None
            height, width = struct.unpack(">HH", content[position + 3:position + 7])
            return width, height
        position += size
    return None


@router.post("/{workspace_id}/logo")
async def upload_logo(workspace_id: str, file: UploadFile = File(...),
                      user=Depends(get_current_user), session=Depends(get_session)):
    require_workspace_admin(session, workspace_id, user.id)
    content = await file.read(1024 * 1024 + 1)
    if not content or len(content) > 1024 * 1024:
        raise HTTPException(413, "Use a PNG or JPEG image up to 1 MB")
    if content.startswith(b"\x89PNG\r\n\x1a\n") and len(content) >= 24 and content[12:16] == b"IHDR":
        width, height = struct.unpack(">II", content[16:24])
        if not (1 <= width <= 4096 and 1 <= height <= 4096):
            raise HTTPException(400, "Logo dimensions must be between 1 and 4096 pixels")
        extension = ".png"
    elif content.startswith(b"\xff\xd8\xff") and content.endswith(b"\xff\xd9"):
        dimensions = jpeg_dimensions(content)
        if not dimensions or not all(1 <= size <= 4096 for size in dimensions):
            raise HTTPException(400, "Logo dimensions must be between 1 and 4096 pixels")
        extension = ".jpg"
    else:
        raise HTTPException(400, "Use a PNG or JPEG image")
    # Recheck after receiving the body: workspace access may have changed.
    session.expire_all()
    lock_workspace(session, workspace_id)
    member = require_workspace_admin(session, workspace_id, user.id)
    filename = uuid4().hex + extension
    path = logo_directory() / filename
    with path.open("xb") as stream:
        stream.write(content)
    workspace = session.get(Workspace, workspace_id)
    previous = workspace.logo_filename
    workspace.logo_filename = filename
    session.add(workspace)
    try:
        session.commit()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    if previous and re.fullmatch(r"[0-9a-f]{32}\.(png|jpg)", previous):
        (logo_directory() / previous).unlink(missing_ok=True)
    return workspace_response(session, workspace, member)


@router.delete("/{workspace_id}/logo")
def delete_logo(workspace_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    lock_workspace(session, workspace_id)
    member = require_workspace_admin(session, workspace_id, user.id)
    workspace = session.get(Workspace, workspace_id)
    previous = workspace.logo_filename
    workspace.logo_filename = None
    session.add(workspace)
    session.commit()
    if previous and re.fullmatch(r"[0-9a-f]{32}\.(png|jpg)", previous):
        (logo_directory() / previous).unlink(missing_ok=True)
    return workspace_response(session, workspace, member)


@router.get("/{workspace_id}/logo")
def get_logo(workspace_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    require_workspace_member(session, workspace_id, user.id)
    workspace = session.get(Workspace, workspace_id)
    if not workspace.logo_filename or not re.fullmatch(r"[0-9a-f]{32}\.(png|jpg)", workspace.logo_filename):
        raise HTTPException(404, "Logo not found")
    path = logo_directory() / workspace.logo_filename
    if not path.is_file():
        raise HTTPException(404, "Logo not found")
    return FileResponse(path, media_type="image/png" if path.suffix == ".png" else "image/jpeg",
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
