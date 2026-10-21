"""First-class direct inbox with strict pair identity and personal controls."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import select
from sqlalchemy import update

from ..database import get_session
from ..models.channel import Channel, ChannelMember
from ..models.direct_message import DirectMessage, DirectMessageState, UserBlock
from ..models.user import User
from ..models.workspace import WorkspaceMember
from ..services.auth import get_current_user
from ..services.direct_messages import open_direct_channel, shared_direct_channel_ids, lock_direct_delivery
from ..services.matrix_bridge import remove_secure_member, sync_secure_membership
from ..services.workspaces import (
    channel_security_metadata, lock_workspace, require_workspace_member, users_blocked, visible_workspace_user_ids,
    workspace_allows_channel,
)

router = APIRouter(prefix="/direct-messages", tags=["direct-messages"])


class OpenDirect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str = Field(min_length=1, max_length=100)
    user_id: str = Field(min_length=1, max_length=100)


class InboxState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    archived: bool


def direct_response(session, channel, direct, user_id):
    other_id = direct.user_high if direct.user_low == user_id else direct.user_low
    other = session.get(User, other_id)
    member = session.get(ChannelMember, (channel.id, user_id))
    state = session.get(DirectMessageState, (channel.id, user_id))
    return {
        "id": channel.id, "name": other.name if other else "Former member", "type": "direct",
        "workspace_id": direct.workspace_id, "role": member.role if member else "member",
        "encrypted_channel_key": member.encrypted_channel_key if member else None,
        "other_user": {"id": other_id, "name": other.name if other else "Former member"},
        "archived": state.archived if state else False,
        "blocked": users_blocked(session, user_id, other_id) or not workspace_allows_channel(session, channel.id, other_id),
        "blocked_by_me": bool(session.get(UserBlock, (user_id, other_id))),
        **channel_security_metadata(session, channel.id),
    }


@router.post("")
def open_direct(data: OpenDirect, user=Depends(get_current_user), session=Depends(get_session)):
    channel, created = open_direct_channel(session, data.workspace_id, user.id, data.user_id, return_created=True)
    return {**direct_response(session, channel, session.get(DirectMessage, channel.id), user.id), "created": created}


@router.get("")
def list_direct(workspace_id: str | None = None, user=Depends(get_current_user), session=Depends(get_session)):
    if workspace_id:
        require_workspace_member(session, workspace_id, user.id)
    query = select(DirectMessage).where((DirectMessage.user_low == user.id) | (DirectMessage.user_high == user.id))
    if workspace_id:
        query = query.where(DirectMessage.workspace_id == workspace_id)
    result = []
    for direct in session.exec(query).all():
        channel = session.get(Channel, direct.channel_id)
        if channel and session.get(ChannelMember, (channel.id, user.id)) and workspace_allows_channel(session, channel.id, user.id):
            result.append(direct_response(session, channel, direct, user.id))
    return result


@router.patch("/{channel_id}")
def update_inbox(channel_id: str, data: InboxState, user=Depends(get_current_user), session=Depends(get_session)):
    direct = session.get(DirectMessage, channel_id)
    if not direct or user.id not in {direct.user_low, direct.user_high}:
        raise HTTPException(404, "Direct conversation not found")
    lock_workspace(session, direct.workspace_id)
    require_workspace_member(session, direct.workspace_id, user.id)
    state = session.get(DirectMessageState, (channel_id, user.id))
    if not state:
        state = DirectMessageState(channel_id=channel_id, user_id=user.id)
    state.archived = data.archived
    session.add(state)
    session.commit()
    return {"channel_id": channel_id, "archived": state.archived}


@router.get("/blocks")
def list_blocks(user=Depends(get_current_user), session=Depends(get_session)):
    result = []
    for block in session.exec(select(UserBlock).where(UserBlock.blocker_id == user.id)).all():
        other = session.get(User, block.blocked_id)
        result.append({"user_id": block.blocked_id, "name": other.name if other else "Former member"})
    return result


@router.post("/blocks/{other_id}")
def block_person(other_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    if other_id == user.id:
        raise HTTPException(400, "You cannot block your own account")
    # Lock workspace delivery before personal state, matching secure membership
    # sync and new DM creation so an in-flight sync cannot revive a blocked room.
    lock_direct_delivery(session, user.id, other_id)
    session.execute(update(User).where(User.id == user.id).values(name=User.name))
    known = False
    for membership in session.exec(select(WorkspaceMember).where(WorkspaceMember.user_id == user.id)).all():
        if other_id in visible_workspace_user_ids(session, membership.workspace_id, user.id):
            known = True
            break
    if not known or not session.get(User, other_id):
        raise HTTPException(404, "Person is unavailable")
    if not session.get(UserBlock, (user.id, other_id)):
        # Matrix clients send directly to the encrypted room. Revoke both
        # participants before promising a block so it also fences SDK delivery.
        for channel_id in shared_direct_channel_ids(session, user.id, other_id):
            for participant in (user.id, other_id):
                remove_secure_member(session, channel_id, participant)
        session.add(UserBlock(blocker_id=user.id, blocked_id=other_id))
        session.commit()
    return {"blocked": True, "user_id": other_id}


@router.delete("/blocks/{other_id}")
def unblock_person(other_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    session.execute(update(User).where(User.id == user.id).values(name=User.name))
    block = session.get(UserBlock, (user.id, other_id))
    if block:
        session.delete(block)
        session.commit()
    secure_sync_pending = False
    # A remote outage after unblocking leaves the room closed. Reopening the
    # conversation or explicit secure sync can safely retry the invitation.
    for channel_id in shared_direct_channel_ids(session, user.id, other_id):
        try:
            sync_secure_membership(session, channel_id)
            session.commit()
        except HTTPException as exc:
            if exc.status_code != 503:
                raise
            session.commit()
            secure_sync_pending = True
    return {"blocked": False, "user_id": other_id, "secure_sync_pending": secure_sync_pending}
