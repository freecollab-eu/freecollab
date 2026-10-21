"""Explicit member-ID mentions for ordinary chat; no plaintext name parsing."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, or_
from sqlmodel import select

from ..database import get_session
from ..models.message import Message
from ..models.user import User
from ..models.channel import ChannelMember
from ..services.auth import get_current_user
from ..services.access import require_channel_member
from ..services.chat_notifications import message_mentions, notify_chat_message, require_native_channel

router = APIRouter(prefix="/mentions", tags=["mentions"])


@router.get("/me")
def get_my_mentions(limit: int = Query(50, ge=1, le=100), before: str | None = None,
                    user=Depends(get_current_user), session=Depends(get_session)):
    query = session.query(Message).join(ChannelMember, ChannelMember.channel_id == Message.channel_id).filter(
        ChannelMember.user_id == user.id, Message.deleted == False,
        Message.mentions.contains('"' + user.id + '"'),
    )
    if before:
        cursor = session.get(Message, before)
        if not cursor:
            raise HTTPException(404, "Mention cursor not found")
        require_channel_member(session, cursor.channel_id, user.id)
        require_native_channel(session, cursor.channel_id)
        if user.id not in message_mentions(cursor):
            raise HTTPException(404, "Mention cursor not found")
        query = query.filter(or_(Message.created_at < cursor.created_at,
                                 and_(Message.created_at == cursor.created_at, Message.id < cursor.id)))
    result = []
    from .messages import message_data
    for message in query.order_by(Message.created_at.desc(), Message.id.desc()):
        if user.id not in message_mentions(message):
            continue
        try:
            require_channel_member(session, message.channel_id, user.id)
            require_native_channel(session, message.channel_id)
        except HTTPException:
            continue
        result.append(message_data(message))
        if len(result) >= limit:
            break
    return result


@router.get("/channels/{channel_id}/members")
def get_mentionable_members(channel_id: str, query: str = Query("", max_length=80),
                            user=Depends(get_current_user), session=Depends(get_session)):
    require_channel_member(session, channel_id, user.id)
    users = session.exec(select(User).join(ChannelMember, ChannelMember.user_id == User.id).where(
        ChannelMember.channel_id == channel_id, User.is_blocked == False,
    ).order_by(User.name, User.id)).all()
    needle = query.casefold().strip()
    result = []
    for member in users:
        if needle and needle not in member.name.casefold():
            continue
        try:
            require_channel_member(session, channel_id, member.id)
        except HTTPException:
            continue
        result.append({"id": member.id, "name": member.name, "username": member.name})
        if len(result) >= 50:
            break
    return result


async def process_message_mentions(message, channel, author, session, mention_ids=None):
    """The client supplies validated IDs; encrypted storage is not name parsed."""
    await notify_chat_message(session, message, mention_ids=mention_ids)
