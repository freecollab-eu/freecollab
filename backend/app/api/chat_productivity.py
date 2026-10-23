"""Member-owned group DMs, scheduling, reminders and saved-item completion."""
from datetime import datetime
import json
from hashlib import sha256
import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import update
from sqlmodel import select

from ..database import get_session
from ..models.channel import ChannelMember
from ..models.chat_productivity import GroupConversation, MessageReminder, SavedItemCompletion, ScheduledMessage
from ..models.collaboration import Bookmark
from ..models.message import Message
from ..models.notification import Notification
from ..services.access import lock_channel_writes, require_channel_member
from ..services.auth import get_current_user
from ..services.chat_notifications import require_native_channel, validate_mentions
from ..services.chat_productivity import authority, group_data, open_group, schedule_data, utc_due
from ..services.crypto import encrypt_message
from ..services.request_guard import recheck_actor
from ..services.sso_scope import require_channel_scope
from ..services.workspaces import require_direct_message_writable, require_workspace_member
from ..schemas import MessageText
from .saved_items import SavedTarget, validate_target
from ..services.message_references import ReferenceInput, validate_references, store_references

router = APIRouter(tags=["chat productivity"])
Identifier = Annotated[str, StringConstraints(min_length=1, max_length=128)]


class OpenGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: Identifier
    participant_ids: list[Identifier] = Field(min_length=2, max_length=9)


class Schedule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{16,64}$")
    content: MessageText
    due_at: datetime
    mentions: list[Identifier] = Field(default_factory=list, max_length=50)
    parent_id: Identifier | None = None
    references: list[ReferenceInput] = Field(default_factory=list, max_length=50)


class Reminder(SavedTarget):
    due_at: datetime


class ReminderUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    completed: bool = False
    due_at: datetime | None = None


class Completion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    completed: bool


@router.post("/group-conversations")
def create_group(data: OpenGroup, user=Depends(get_current_user), session=Depends(get_session)):
    return open_group(session, data.workspace_id, user.id, data.participant_ids)


@router.get("/group-conversations")
def list_groups(workspace_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    require_workspace_member(session, workspace_id, user.id)
    rows = session.exec(select(GroupConversation).join(ChannelMember, ChannelMember.channel_id == GroupConversation.channel_id)
        .where(GroupConversation.workspace_id == workspace_id, ChannelMember.user_id == user.id)).all()
    return [group_data(session, row, user.id) for row in rows]


@router.post("/group-conversations/{channel_id}/leave")
def leave_group(channel_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    from ..services.workspaces import lock_workspace
    from ..services.matrix_bridge import remove_secure_member
    row = session.get(GroupConversation, channel_id)
    if not row:
        raise HTTPException(404, "Group conversation not found")
    lock_workspace(session, row.workspace_id)
    lock_channel_writes(session, channel_id)
    recheck_actor(session, user.id)
    member = require_channel_member(session, channel_id, user.id)
    remove_secure_member(session, channel_id, user.id)
    session.delete(member)
    session.commit()
    return {"left": True}


@router.post("/channels/{channel_id}/scheduled-messages", status_code=201)
def schedule_message(channel_id: str, data: Schedule, user=Depends(get_current_user), session=Depends(get_session)):
    lock_channel_writes(session, channel_id)
    recheck_actor(session, user.id)
    require_channel_member(session, channel_id, user.id)
    require_direct_message_writable(session, channel_id, user.id)
    require_native_channel(session, channel_id)
    from ..services.account_security import _secret_key
    fingerprint_payload = data.model_dump(mode='json')
    if not fingerprint_payload['references']:
        fingerprint_payload.pop('references')  # Existing empty-reference receipt compatibility.
    fingerprint = hmac.new(_secret_key(), b"scheduled-message-v1\0" + json.dumps(
        {"channel_id": channel_id, **fingerprint_payload}, sort_keys=True,
        separators=(",", ":")).encode(), sha256).hexdigest()
    existing = session.exec(select(ScheduledMessage).where(ScheduledMessage.user_id == user.id,
                                                           ScheduledMessage.client_id == data.client_id)).first()
    if existing:
        if existing.request_digest != fingerprint:
            raise HTTPException(409, "This request identifier already belongs to another scheduled message")
        return schedule_data(existing, content=True)
    due = utc_due(data.due_at)
    digest, claims = authority(session, due)
    references, mention_ids = validate_references(session, channel_id, user.id, data.content, data.references, data.mentions)
    if data.parent_id:
        parent = session.get(Message, data.parent_id)
        if not parent or parent.deleted or parent.channel_id != channel_id:
            raise HTTPException(404, "Parent message not found in channel")
    from ..services.enterprise_governance import enforce_native_content
    enforce_native_content(session, channel_id, user.id, data.content, surface="message")
    count = len(session.exec(select(ScheduledMessage.id).where(ScheduledMessage.user_id == user.id,
                                                              ScheduledMessage.status == "pending").limit(100)).all())
    if count >= 100:
        raise HTTPException(409, "Your scheduled message queue is full")
    row = ScheduledMessage(user_id=user.id, channel_id=channel_id, client_id=data.client_id,
        request_digest=fingerprint,
        encrypted_content=encrypt_message(data.content, channel_id), due_at=due, session_hash=digest,
        claims_json=claims, mentions_json=json.dumps(data.mentions), parent_id=data.parent_id)
    session.add(row)
    store_references(session, row, references, scheduled=True)
    session.commit()
    return schedule_data(row, content=True)


@router.get("/channels/{channel_id}/scheduled-messages")
def scheduled_messages(channel_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    require_channel_member(session, channel_id, user.id)
    require_native_channel(session, channel_id)
    rows = session.exec(select(ScheduledMessage).where(ScheduledMessage.user_id == user.id,
        ScheduledMessage.channel_id == channel_id).order_by(ScheduledMessage.created_at.desc()).limit(100)).all()
    return [schedule_data(row, content=True) for row in rows]


@router.delete("/scheduled-messages/{identifier}")
def cancel_scheduled(identifier: str, user=Depends(get_current_user), session=Depends(get_session)):
    row = session.get(ScheduledMessage, identifier)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "Scheduled message not found")
    require_channel_scope(session, row.channel_id)
    cancelled = session.execute(update(ScheduledMessage).where(ScheduledMessage.id == identifier,
        ScheduledMessage.status == "pending").values(status="cancelled", encrypted_content="", claims_json="{}",
                                                      finished_at=datetime.utcnow()))
    if cancelled.rowcount:
        store_references(session, row, [], scheduled=True)
    session.commit()
    return {"cancelled": bool(cancelled.rowcount)}


def reminder_data(row):
    from datetime import timezone
    return {"id": row.id, "channel_id": row.channel_id, "item_id": row.item_id, "kind": row.kind,
            "due_at": row.due_at.replace(tzinfo=timezone.utc), "status": row.status,
            "completed_at": row.completed_at.replace(tzinfo=timezone.utc) if row.completed_at else None}


@router.post("/channels/{channel_id}/reminders", status_code=201)
def create_reminder(channel_id: str, data: Reminder, user=Depends(get_current_user), session=Depends(get_session)):
    require_channel_member(session, channel_id, user.id)
    item_id, kind = validate_target(session, channel_id, data, user.id)
    due = utc_due(data.due_at)
    digest, claims = authority(session, due)
    count = len(session.exec(select(MessageReminder.id).where(MessageReminder.user_id == user.id,
        MessageReminder.status.in_(["pending", "notified"])).limit(100)).all())
    if count >= 100:
        raise HTTPException(409, "Complete an existing reminder before adding another")
    row = MessageReminder(user_id=user.id, channel_id=channel_id, item_id=item_id, kind=kind,
                          due_at=due, session_hash=digest, claims_json=claims)
    session.add(row)
    session.commit()
    return reminder_data(row)


@router.get("/channels/{channel_id}/reminders")
def list_reminders(channel_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    require_channel_member(session, channel_id, user.id)
    return [reminder_data(row) for row in session.exec(select(MessageReminder).where(
        MessageReminder.user_id == user.id, MessageReminder.channel_id == channel_id)
        .order_by(MessageReminder.created_at.desc()).limit(100)).all()]


@router.patch("/reminders/{identifier}")
def update_reminder(identifier: str, data: ReminderUpdate, user=Depends(get_current_user), session=Depends(get_session)):
    session.execute(update(MessageReminder).where(MessageReminder.id == identifier,
        MessageReminder.user_id == user.id).values(status=MessageReminder.status))
    row = session.get(MessageReminder, identifier, populate_existing=True)
    if not row or row.user_id != user.id:
        raise HTTPException(404, "Reminder not found")
    require_channel_member(session, row.channel_id, user.id)
    if data.completed and data.due_at is not None:
        raise HTTPException(422, "Choose completion or a new reminder time")
    if data.completed:
        row.status, row.completed_at, row.claims_json = "completed", datetime.utcnow(), "{}"
    elif data.due_at is not None:
        if row.status in {"completed", "cancelled"}:
            raise HTTPException(409, "Create a new reminder for this message")
        row.due_at = utc_due(data.due_at)
        row.session_hash, row.claims_json = authority(session, row.due_at)
        if row.notification_id:
            notification = session.get(Notification, row.notification_id)
            if notification:
                session.delete(notification)
        row.status, row.notification_id = "pending", None
    else:
        raise HTTPException(422, "Choose completion or a new reminder time")
    session.add(row)
    session.commit()
    return reminder_data(row)


@router.get("/reminders/{identifier}/target")
def reminder_target(identifier: str, user=Depends(get_current_user), session=Depends(get_session)):
    row = session.get(MessageReminder, identifier)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "Reminder not found")
    require_channel_member(session, row.channel_id, user.id)
    from ..services.matrix_bridge import is_secure_channel
    if is_secure_channel(session, row.channel_id) != (row.kind == "matrix"):
        raise HTTPException(409, "Encryption mode changed")
    if row.kind == "message":
        target = session.get(Message, row.item_id)
        if target is None or target.deleted or target.channel_id != row.channel_id:
            raise HTTPException(404, "Message no longer available")
    return {"channel_id": row.channel_id, "item_id": row.item_id, "kind": row.kind}


@router.patch("/bookmarks/{channel_id}/{item_id:path}/completion")
def complete_saved(channel_id: str, item_id: str, data: Completion, user=Depends(get_current_user), session=Depends(get_session)):
    require_channel_member(session, channel_id, user.id)
    key = (user.id, channel_id, item_id)
    if session.get(Bookmark, key) is None:
        raise HTTPException(404, "Saved item not found")
    session.execute(update(Bookmark).where(Bookmark.user_id == user.id, Bookmark.channel_id == channel_id,
        Bookmark.item_id == item_id).values(kind=Bookmark.kind))
    row = session.get(SavedItemCompletion, key)
    if data.completed and row is None:
        session.add(SavedItemCompletion(user_id=user.id, channel_id=channel_id, item_id=item_id))
    elif not data.completed and row is not None:
        session.delete(row)
    session.commit()
    return {"completed": data.completed}
