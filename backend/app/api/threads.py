from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, and_
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from typing import Optional, Annotated
import json
from ..database import get_session
from ..models.message import Message
from ..models.collaboration import ThreadSubscription, EncryptedThreadSubscription, EncryptedMessageActivity
from ..services.auth import get_current_user
from ..services.access import require_channel_member, lock_channel_writes
from ..services.workspaces import require_direct_message_writable
from ..services.direct_messages import restore_direct_inbox
from ..services.crypto import encrypt_message, decrypt_message
from ..schemas import MessageText
from ..services.chat_notifications import require_native_channel, validate_mentions, message_mentions, record_message_change, subscribe_participant, is_watching, notify_chat_message
from ..realtime.socket import broadcast_to_channel
from datetime import datetime
import uuid
from nacl.exceptions import CryptoError

router = APIRouter(prefix="/threads", tags=["threads"])


class ThreadReply(BaseModel):
    content: MessageText
    mentions: list[Annotated[str, StringConstraints(min_length=1, max_length=128)]] = Field(default_factory=list, max_length=50)


class SubscriptionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    watching: bool


def encrypted_subscription(session, channel_id: str, event_id: str, user_id: str):
    from ..services.matrix_bridge import is_secure_channel
    require_channel_member(session, channel_id, user_id)
    if not is_secure_channel(session, channel_id):
        raise HTTPException(400, "Encrypted thread references require an encrypted channel")
    if not 1 <= len(event_id) <= 1024:
        raise HTTPException(400, "Invalid encrypted thread reference")
    return session.get(EncryptedThreadSubscription, (user_id, channel_id, event_id))


@router.get("/channels/{channel_id}/events/{event_id:path}/subscription")
def get_encrypted_subscription(channel_id: str, event_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    record = encrypted_subscription(session, channel_id, event_id, user.id)
    activity = session.get(EncryptedMessageActivity, (channel_id, event_id))
    return {"message_id": event_id, "watching": record.watching if record else bool(activity and activity.actor_id == user.id)}


@router.put("/channels/{channel_id}/events/{event_id:path}/subscription")
def set_encrypted_subscription(channel_id: str, event_id: str, data: SubscriptionUpdate, user=Depends(get_current_user), session=Depends(get_session)):
    record = encrypted_subscription(session, channel_id, event_id, user.id)
    if record is None:
        record = EncryptedThreadSubscription(user_id=user.id, channel_id=channel_id, matrix_event_id=event_id)
    record.watching = data.watching
    record.updated_at = datetime.utcnow()
    session.add(record)
    session.commit()
    return {"message_id": event_id, "watching": record.watching}


def thread_parent(session, message_id: str, user_id: str):
    parent = session.get(Message, message_id)
    if not parent or parent.deleted:
        raise HTTPException(404, "Message not found")
    require_channel_member(session, parent.channel_id, user_id)
    require_native_channel(session, parent.channel_id)
    return parent


@router.get("/messages/{message_id}/subscription")
def get_subscription(message_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    parent = thread_parent(session, message_id, user.id)
    return {"message_id": parent.id, "watching": is_watching(session, user.id, parent)}


@router.put("/messages/{message_id}/subscription")
def set_subscription(message_id: str, data: SubscriptionUpdate, user=Depends(get_current_user), session=Depends(get_session)):
    parent = thread_parent(session, message_id, user.id)
    record = session.get(ThreadSubscription, (user.id, parent.id))
    if record is None:
        record = ThreadSubscription(user_id=user.id, message_id=parent.id)
    record.watching = data.watching
    record.updated_at = datetime.utcnow()
    session.add(record)
    session.commit()
    return {"message_id": parent.id, "watching": record.watching}


@router.post("/messages/{message_id}/reply")
async def reply_to_message(
    message_id: str,
    data: ThreadReply,
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    """Reply to a message, creating a thread."""
    parent = session.get(Message, message_id)
    if not parent or parent.deleted:
        raise HTTPException(404, "Message not found")
    
    lock_channel_writes(session, parent.channel_id)
    require_channel_member(session, parent.channel_id, user.id)
    require_direct_message_writable(session, parent.channel_id, user.id)
    require_native_channel(session, parent.channel_id)
    mention_ids = validate_mentions(session, parent.channel_id, data.mentions)
    
    from ..services.enterprise_governance import enforce_native_content
    warnings = enforce_native_content(session, parent.channel_id, user.id, data.content)
    # Create reply message
    reply = Message(
        id=str(uuid.uuid4()),
        channel_id=parent.channel_id,
        author_id=user.id,
        content=encrypt_message(data.content, parent.channel_id),
        encrypted=True,
        parent_id=message_id,  # Link to parent
        mentions=json.dumps(mention_ids),
    )
    session.add(reply)
    
    # Update thread count on parent
    parent.thread_count = (parent.thread_count or 0) + 1
    subscribe_participant(session, user.id, parent)
    record_message_change(session, reply, "created")
    record_message_change(session, parent, "thread")
    restore_direct_inbox(session, parent.channel_id)
    
    session.commit()
    session.refresh(reply)
    
    reply_data = {
        "id": reply.id,
        "channel_id": reply.channel_id,
        "author_id": reply.author_id,
        "content": data.content,
        "encrypted": reply.encrypted,
        "parent_id": reply.parent_id,
        "created_at": reply.created_at.isoformat(),
        "mentions": mention_ids,
        "policy_warnings": warnings,
    }
    
    # Broadcast thread reply
    await broadcast_to_channel(
        parent.channel_id,
        "thread_reply",
        {
            "parent_id": message_id,
            "reply": reply_data,
            "thread_count": parent.thread_count,
        },
    )
    await notify_chat_message(session, reply, mention_ids=mention_ids)
    
    return reply_data


@router.get("/messages/{message_id}")
def get_thread(
    message_id: str,
    limit: int = Query(50, ge=1, le=100),
    before: Optional[str] = None,
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    """Get all replies in a thread."""
    parent = session.get(Message, message_id)
    if not parent or parent.deleted:
        raise HTTPException(404, "Message not found")
    
    require_channel_member(session, parent.channel_id, user.id)
    require_native_channel(session, parent.channel_id)
    
    query = (
        session.query(Message)
        .filter(
            Message.parent_id == message_id,
            Message.channel_id == parent.channel_id,
            Message.deleted == False,
        )
        .order_by(Message.created_at.desc(), Message.id.desc())
    )
    
    if before:
        before_msg = session.get(Message, before)
        if not before_msg or before_msg.parent_id != message_id or before_msg.channel_id != parent.channel_id:
            raise HTTPException(404, "Thread cursor not found")
        query = query.filter(or_(
            Message.created_at < before_msg.created_at,
            and_(Message.created_at == before_msg.created_at, Message.id < before_msg.id),
        ))
    
    replies = list(reversed(query.limit(limit).all()))

    # Older clients could incorrectly mark plaintext as encrypted. Do not
    # silently expose unauthenticated content or fail with an unhandled error.
    reply_contents = {}
    for reply in replies:
        try:
            reply_contents[reply.id] = (
                decrypt_message(reply.content, reply.channel_id)
                if reply.encrypted else reply.content
            )
        except (CryptoError, ValueError):
            raise HTTPException(409, "Stored thread reply could not be decrypted")
    
    return {
        "parent_id": message_id,
        "thread_count": parent.thread_count or 0,
        "replies": [
            {
                "id": r.id,
                "channel_id": r.channel_id,
                "author_id": r.author_id,
                "content": reply_contents[r.id],
                "encrypted": r.encrypted,
                "parent_id": r.parent_id,
                "created_at": r.created_at.isoformat(),
                "edited_at": r.edited_at.isoformat() if r.edited_at else None,
                "mentions": message_mentions(r),
            }
            for r in replies
        ],
    }
