"""Shared resource guards, including legacy dangling membership records."""
from fastapi import HTTPException
from sqlalchemy import update
from ..models.channel import Channel, ChannelMember
from .workspaces import workspace_allows_channel
from .sso_scope import require_channel_scope


def require_channel_member(session, channel_id: str, user_id: str):
    require_channel_scope(session, channel_id)
    if not session.get(Channel, channel_id):
        raise HTTPException(404, "Channel not found")
    member = session.get(ChannelMember, (channel_id, user_id))
    if not member:
        raise HTTPException(403, "Not a member")
    if not workspace_allows_channel(session, channel_id, user_id):
        raise HTTPException(403, "Workspace membership required")
    return member


def lock_channel_writes(session, channel_id: str):
    """Serialize plaintext mutations with the durable encrypted-channel fence.

    A no-op UPDATE locks the channel row on PostgreSQL and the writer on
    SQLite, which does not implement SELECT FOR UPDATE. Call before resource
    authorization and the native-channel check, then commit the content or
    preparing binding in the same transaction.
    """
    require_channel_scope(session, channel_id)
    result = session.execute(update(Channel).where(Channel.id == channel_id).values(name=Channel.name))
    if result.rowcount != 1:
        raise HTTPException(404, "Channel not found")
