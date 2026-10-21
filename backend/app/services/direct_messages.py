"""Idempotent, workspace-scoped direct conversations."""
from uuid import uuid4

from fastapi import HTTPException
from sqlmodel import select

from ..models.channel import Channel, ChannelMember
from ..models.direct_message import DirectMessage, DirectMessageState
from ..models.user import User
from ..models.workspace import WorkspaceChannel, WorkspaceMember
from .group_crypto import GroupCryptoService
from .workspaces import lock_workspace, require_workspace_member, users_blocked, visible_workspace_user_ids


def open_direct_channel(session, workspace_id: str, user_id: str, other_id: str, *, return_created: bool = False):
    if other_id == user_id:
        raise HTTPException(400, "Choose another person for a direct conversation")
    lock_workspace(session, workspace_id)
    require_workspace_member(session, workspace_id, user_id)
    other = session.get(User, other_id)
    if not other or other.is_blocked or other_id not in visible_workspace_user_ids(session, workspace_id, user_id):
        raise HTTPException(404, "Person is unavailable in this workspace")
    require_workspace_member(session, workspace_id, other_id)
    if users_blocked(session, user_id, other_id):
        raise HTTPException(403, "Messaging is unavailable for this conversation")
    low, high = sorted((user_id, other_id))
    direct = session.exec(select(DirectMessage).where(
        DirectMessage.workspace_id == workspace_id, DirectMessage.user_low == low,
        DirectMessage.user_high == high,
    )).first()
    if direct:
        channel = session.get(Channel, direct.channel_id)
        actual_members = set(session.exec(select(ChannelMember.user_id).where(
            ChannelMember.channel_id == direct.channel_id,
        )).all())
        if not channel or actual_members != {user_id, other_id}:
            raise HTTPException(409, "This direct conversation requires migration")
        state = session.get(DirectMessageState, (channel.id, user_id))
        if state:
            state.archived = False
            session.add(state)
        session.commit()
        return (channel, False) if return_created else channel
    channel = Channel(id=uuid4().hex, name=None, type="direct")
    session.add(channel)
    session.flush()
    session.add(WorkspaceChannel(channel_id=channel.id, workspace_id=workspace_id))
    session.add(DirectMessage(channel_id=channel.id, workspace_id=workspace_id, user_low=low, user_high=high))
    # Retain the legacy key fields for old client compatibility. These server
    # generated keys are not presented as client-side end-to-end encryption.
    users = [session.get(User, user_id), other]
    encrypted_keys = GroupCryptoService.encrypt_key_for_members(
        GroupCryptoService.generate_channel_key(), {person.id: person.public_key for person in users if person.public_key},
    )
    for person in users:
        session.add(ChannelMember(channel_id=channel.id, user_id=person.id, role="admin",
                                  encrypted_channel_key=encrypted_keys.get(person.id)))
    session.commit()
    return (channel, True) if return_created else channel


def restore_direct_inbox(session, channel_id: str):
    """Call after a new message so personal archives can return to the inbox."""
    for state in session.exec(select(DirectMessageState).where(DirectMessageState.channel_id == channel_id)).all():
        if state.archived:
            state.archived = False
            session.add(state)


def shared_direct_channel_ids(session, first_id: str, second_id: str):
    """Include preserved legacy duplicate DMs when revoking remote delivery."""
    channels = session.exec(select(Channel.id).join(
        ChannelMember, ChannelMember.channel_id == Channel.id,
    ).where(Channel.type == "direct", ChannelMember.user_id == first_id)).all()
    return [channel_id for channel_id in channels if session.get(ChannelMember, (channel_id, second_id))]


def lock_direct_delivery(session, first_id: str, second_id: str):
    """Serialize personal blocks with per-workspace Matrix membership sync."""
    first_workspaces = set(session.exec(select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == first_id)).all())
    second_workspaces = set(session.exec(select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == second_id)).all())
    for workspace_id in sorted(first_workspaces & second_workspaces):
        lock_workspace(session, workspace_id)
    session.expire_all()
