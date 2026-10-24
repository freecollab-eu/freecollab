from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, and_, func
from nacl.exceptions import CryptoError
from pydantic import BaseModel, Field, StringConstraints
from typing import Optional, Annotated
from datetime import datetime
import json
from ..database import get_session
from ..models.message import Message
from ..models.channel import Channel
from ..models.collaboration import MessageChange
from ..services.auth import get_current_user
from ..services.request_guard import recheck_actor
from ..services.access import require_channel_member, lock_channel_writes
from ..services.workspaces import require_direct_message_writable
from ..services.direct_messages import restore_direct_inbox
from ..services.crypto import encrypt_message, decrypt_message
from ..services.enterprise_governance import assert_deletion_allowed, enforce_native_content
from ..schemas import MessageText
from ..realtime.socket import broadcast_to_channel
from ..api.mentions import process_message_mentions
from ..services.chat_notifications import require_native_channel, validate_mentions, message_mentions, record_message_change, subscribe_participant
from uuid import uuid4
from ..services.message_references import ReferenceInput, validate_references, store_references, stored_references

router = APIRouter(prefix="/messages", tags=["messages"])


class SendMessage(BaseModel):
    content: MessageText
    parent_id: Optional[str] = None  # For thread replies
    mentions: list[Annotated[str, StringConstraints(min_length=1, max_length=128)]] = Field(default_factory=list, max_length=50)
    references: list[ReferenceInput] = Field(default_factory=list, max_length=50)


class EditMessage(BaseModel):
    content: MessageText
    mentions: list[Annotated[str, StringConstraints(min_length=1, max_length=128)]] | None = Field(default=None, max_length=50)
    references: list[ReferenceInput] = Field(default_factory=list, max_length=50)


def message_data(message: Message):
    try:
        content = "[Message deleted]" if message.deleted else decrypt_message(message.content, message.channel_id) if message.encrypted else message.content
    except (CryptoError, ValueError):
        raise HTTPException(409, "Stored message could not be decrypted") from None
    return {"id": message.id, "channel_id": message.channel_id, "author_id": message.author_id,
            "content": content, "created_at": message.created_at, "edited_at": message.edited_at,
            "encrypted": message.encrypted, "deleted": message.deleted, "parent_id": message.parent_id,
            "thread_count": message.thread_count or 0, "mentions": message_mentions(message),
            "references": [] if message.deleted else stored_references(message)}


@router.post("/channels/{channel_id}")
async def send_message(
    channel_id: str,
    data: SendMessage,
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    lock_channel_writes(session, channel_id)
    recheck_actor(session, user.id)
    require_channel_member(session, channel_id, user.id)
    require_direct_message_writable(session, channel_id, user.id)
    require_native_channel(session, channel_id)
    references, mention_ids = validate_references(session, channel_id, user.id, data.content, data.references, data.mentions)

    # A reply must never reference or mutate a message in another channel.
    parent = None
    if data.parent_id:
        parent = session.get(Message, data.parent_id)
        if not parent or parent.deleted or parent.channel_id != channel_id:
            raise HTTPException(404, "Parent message not found in channel")

    warnings = enforce_native_content(session, channel_id, user.id, data.content)
    # Encrypt and store
    encrypted_content = encrypt_message(data.content, channel_id)

    message = Message(
        id=uuid4().hex,
        channel_id=channel_id,
        author_id=user.id,
        content=encrypted_content,
        encrypted=True,
        parent_id=data.parent_id,
        mentions=json.dumps(mention_ids),
    )
    session.add(message)
    store_references(session, message, references)

    # Update thread count if replying
    if parent:
        parent.thread_count = (parent.thread_count or 0) + 1
        subscribe_participant(session, user.id, parent)
    record_message_change(session, message, "created")
    if parent:
        record_message_change(session, parent, "thread")
    restore_direct_inbox(session, channel_id)

    from ..services.webhooks import enqueue_channel_event
    enqueue_channel_event(session, channel_id, "message.created", message.id)

    session.commit()

    # Process @mentions
    channel = session.get(Channel, channel_id)
    await process_message_mentions(message, channel, user, session, mention_ids=mention_ids)

    payload = {
        "id": message.id,
        "channel_id": channel_id,
        "author_id": user.id,
        "content": data.content,  # Send plaintext to authorized clients
        "created_at": message.created_at.isoformat(),
        "encrypted": True,
        "parent_id": message.parent_id,
        "thread_count": message.thread_count,
        "mentions": mention_ids,
        "references": references,
    }
    result = {
        **message.model_dump(),
        "content": data.content,
        "mentions": mention_ids,
        "references": references,
        "policy_warnings": warnings,
    }
    # Notification checks and expired ORM attributes can reopen a read
    # transaction after the mutation commits. Release it before fanout needs
    # independent, current recipient authorization connections.
    session.commit()
    await broadcast_to_channel(channel_id, "message", payload)
    return result


@router.get("/channels/{channel_id}")
def get_messages(
    channel_id: str,
    limit: int = Query(50, ge=1, le=100),
    before: str | None = None,
    roots_only: bool = False,
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    require_channel_member(session, channel_id, user.id)
    require_native_channel(session, channel_id)

    query = session.query(Message).filter(
        Message.channel_id == channel_id,
        Message.deleted == False,
    )
    if roots_only:
        query = query.filter(Message.parent_id.is_(None))

    if before:
        cursor = session.get(Message, before)
        if not cursor or cursor.channel_id != channel_id:
            raise HTTPException(404, "Message cursor not found in channel")
        query = query.filter(or_(
            Message.created_at < cursor.created_at,
            and_(Message.created_at == cursor.created_at, Message.id < cursor.id),
        ))

    messages = query.order_by(Message.created_at.desc(), Message.id.desc()).limit(limit).all()

    return [message_data(message) for message in messages]


@router.get("/channels/{channel_id}/sync")
def sync_messages(channel_id: str, after: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100),
                  user=Depends(get_current_user), session=Depends(get_session)):
    require_channel_member(session, channel_id, user.id)
    require_native_channel(session, channel_id)
    latest = session.query(func.max(MessageChange.id)).filter(MessageChange.channel_id == channel_id).scalar() or 0
    if after > latest:
        raise HTTPException(400, "Sync cursor is ahead of this channel")
    changes = session.query(MessageChange).filter(MessageChange.channel_id == channel_id, MessageChange.id > after).order_by(MessageChange.id).limit(limit + 1).all()
    has_more = len(changes) > limit
    selected = changes[:limit]
    ids = list(dict.fromkeys(change.message_id for change in selected))
    messages = [message_data(message) for message_id in ids if (message := session.get(Message, message_id)) is not None]
    return {"messages": messages, "next_cursor": selected[-1].id if selected else after, "has_more": has_more}


@router.get("/channels/{channel_id}/sync/head")
def sync_head(channel_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    require_channel_member(session, channel_id, user.id)
    require_native_channel(session, channel_id)
    cursor = session.query(func.max(MessageChange.id)).filter(MessageChange.channel_id == channel_id).scalar() or 0
    return {"cursor": cursor}


@router.get("/{message_id}")
def get_message(message_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    message = session.get(Message, message_id)
    if not message:
        raise HTTPException(404, "Message not found")
    require_channel_member(session, message.channel_id, user.id)
    require_native_channel(session, message.channel_id)
    return message_data(message)


@router.put("/{message_id}")
async def edit_message(
    message_id: str,
    data: EditMessage,
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    """Edit a message (only the author can edit)."""
    message = session.get(Message, message_id)
    if not message:
        raise HTTPException(404, "Message not found")

    lock_channel_writes(session, message.channel_id)
    recheck_actor(session, user.id)
    require_channel_member(session, message.channel_id, user.id)
    require_direct_message_writable(session, message.channel_id, user.id)
    require_native_channel(session, message.channel_id)

    if message.author_id != user.id:
        raise HTTPException(403, "Can only edit your own messages")


    if message.deleted:
        raise HTTPException(400, "Cannot edit deleted message")

    # An unaware editor must not preserve invisible typed recipients after it
    # removes their spans. Pure legacy mention messages retain their old rule.
    inherited_mentions = [] if stored_references(message) else message_mentions(message)
    references, mention_ids = validate_references(session, message.channel_id, user.id, data.content, data.references,
        data.mentions if data.mentions is not None else inherited_mentions)
    assert_deletion_allowed(session, message.channel_id, author_id=message.author_id)
    warnings = enforce_native_content(session, message.channel_id, user.id, data.content)

    # Encrypt new content
    encrypted_content = encrypt_message(data.content, message.channel_id)

    message.content = encrypted_content
    message.edited_at = datetime.utcnow()
    message.mentions = json.dumps(mention_ids)
    store_references(session, message, references)
    record_message_change(session, message, "edited")
    session.commit()

    # Process new @mentions
    channel = session.get(Channel, message.channel_id)
    await process_message_mentions(message, channel, user, session, mention_ids=mention_ids)

    channel_id = message.channel_id
    payload = {
        "id": message.id,
        "content": data.content,
        "edited_at": message.edited_at.isoformat(),
        "mentions": mention_ids,
        "references": references,
    }
    result = {**payload, "policy_warnings": warnings}
    session.commit()
    await broadcast_to_channel(channel_id, "message_edited", payload)
    return result


@router.delete("/{message_id}")
async def delete_message(
    message_id: str,
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    """Delete a message (soft delete, only the author can delete)."""
    message = session.get(Message, message_id)
    if not message:
        raise HTTPException(404, "Message not found")

    lock_channel_writes(session, message.channel_id)
    recheck_actor(session, user.id)
    require_channel_member(session, message.channel_id, user.id)
    require_native_channel(session, message.channel_id)

    if message.author_id != user.id:
        raise HTTPException(403, "Can only delete your own messages")


    if message.deleted:
        raise HTTPException(400, "Message already deleted")

    assert_deletion_allowed(session, message.channel_id, author_id=message.author_id)
    message.deleted = True
    message.content = "[Message deleted]"
    message.edited_at = datetime.utcnow()
    message.mentions = "[]"
    store_references(session, message, [])
    record_message_change(session, message, "deleted")
    channel_id = message.channel_id
    payload = {"id": message.id}
    session.commit()
    await broadcast_to_channel(channel_id, "message_deleted", payload)

    return {"deleted": True, "id": message_id}
