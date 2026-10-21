"""Durable native sends; encrypted events are scheduled only on trusted clients.

No bearer is stored. Delivery is bound to verified session metadata, current
workspace/provider/member policy and the original session's expiry/revocation.
Each job and its message/notification commit atomically under a conditional claim.
"""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from urllib.parse import urlencode

from fastapi import HTTPException
from sqlalchemy import update
from sqlmodel import select

from ..models.channel import Channel, ChannelMember
from ..models.chat_productivity import GroupConversation, MessageReminder, ScheduledMessage
from ..models.message import Message
from ..models.notification import Notification
from ..models.user import User
from ..models.workspace import WorkspaceChannel
from ..models.secure_channel import SecureChannel
from ..config import settings
from .access import lock_channel_writes, require_channel_member
from .chat_notifications import record_message_change, require_native_channel, subscribe_participant, validate_mentions
from .crypto import decrypt_message, encrypt_message
from .request_guard import remember_request, recheck_actor
from .sso_scope import clear_scope
from .workspaces import (channel_security_metadata, lock_workspace, require_direct_message_writable,
                         require_workspace_member, users_blocked, visible_workspace_user_ids)


def utc_due(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HTTPException(422, "Choose a time with a timezone")
    result = value.astimezone(timezone.utc).replace(tzinfo=None)
    now = datetime.utcnow()
    if result <= now or result > now + timedelta(days=7):
        raise HTTPException(422, "Choose a future time within seven days")
    return result


def authority(session, due_at):
    # Populated only by the verified HTTP dependency; never accept caller claims.
    context = session.info.get("freecollab_verified_request")
    if not context:
        raise HTTPException(401, "Sign in again before scheduling")
    claims = context["claims"]
    if due_at >= datetime.utcfromtimestamp(claims["exp"]):
        raise HTTPException(422, "Choose a time before your current sign-in expires")
    safe = {key: claims[key] for key in ("sub", "iat", "exp", "av", "amr", "sso_workspace_id",
            "sso_provider_revision", "embed_application_id", "native_client_id") if key in claims}
    return context["token_hash"], json.dumps(safe, separators=(",", ":"))


def bind_authority(session, row):
    clear_scope(session)
    remember_request(session, row.session_hash, json.loads(row.claims_json))
    recheck_actor(session, row.user_id)
    require_channel_member(session, row.channel_id, row.user_id)


def group_data(session, row, actor_id):
    channel = session.get(Channel, row.channel_id)
    member = require_channel_member(session, row.channel_id, actor_id)
    people = session.exec(select(User.id, User.name).join(ChannelMember, ChannelMember.user_id == User.id).where(
        ChannelMember.channel_id == row.channel_id).order_by(User.name, User.id)).all()
    return {"id": channel.id, "workspace_id": row.workspace_id, "type": "group_direct",
            "name": channel.name, "role": member.role, "encrypted_channel_key": member.encrypted_channel_key,
            "participants": [{"id": person[0], "name": person[1]} for person in people],
            **channel_security_metadata(session, channel.id)}


def open_group(session, workspace_id, actor_id, participant_ids):
    lock_workspace(session, workspace_id)
    recheck_actor(session, actor_id)
    require_workspace_member(session, workspace_id, actor_id)
    participants = set(participant_ids) | {actor_id}
    if not 3 <= len(participants) <= 10 or len(set(participant_ids)) != len(participant_ids):
        raise HTTPException(422, "Choose between two and nine different people")
    visible = visible_workspace_user_ids(session, workspace_id, actor_id)
    people = []
    for person_id in sorted(participants):
        person = session.get(User, person_id)
        if not person or person.is_blocked or person_id not in visible:
            raise HTTPException(404, "Person is unavailable in this workspace")
        require_workspace_member(session, workspace_id, person_id)
        if any(users_blocked(session, person_id, other_id) for other_id in participants if other_id != person_id):
            raise HTTPException(403, "This group includes people who cannot exchange direct messages")
        people.append(person)
    signature = sha256(json.dumps(sorted(participants), separators=(",", ":")).encode()).hexdigest()
    for existing in session.exec(select(GroupConversation).where(
            GroupConversation.workspace_id == workspace_id, GroupConversation.participants_hash == signature)).all():
        actual = set(session.exec(select(ChannelMember.user_id).where(ChannelMember.channel_id == existing.channel_id)).all())
        if actual == participants:
            session.commit()
            return {**group_data(session, existing, actor_id), "created": False}
    from uuid import uuid4
    channel = Channel(id=uuid4().hex, type="group_direct", name=", ".join(person.name for person in people)[:100])
    session.add(channel)
    session.flush()
    session.add(WorkspaceChannel(channel_id=channel.id, workspace_id=workspace_id))
    row = GroupConversation(channel_id=channel.id, workspace_id=workspace_id, participants_hash=signature, created_by=actor_id)
    session.add(row)
    if settings.matrix_enabled:
        session.add(SecureChannel(channel_id=channel.id, created_by=actor_id))
    for person in people:
        session.add(ChannelMember(channel_id=channel.id, user_id=person.id, role="admin"))
    session.commit()
    return {**group_data(session, row, actor_id), "created": True}


def schedule_data(row, *, content=False):
    data = {"id": row.id, "channel_id": row.channel_id, "due_at": row.due_at.replace(tzinfo=timezone.utc),
            "created_at": row.created_at.replace(tzinfo=timezone.utc), "status": row.status,
            "parent_id": row.parent_id, "message_id": row.message_id, "failure_code": row.failure_code}
    if content and row.status == "pending":
        data["content"] = decrypt_message(row.encrypted_content, row.channel_id)
        data["mentions"] = json.loads(row.mentions_json)
    return data


def run_pending(session, *, limit=10, now=None):
    """At-most-once DB effects across workers, with no network call in the claim.

    A crashed transaction rolls back its claim. Notifications use deterministic
    identifiers and no copied content. REST history remains authoritative.
    """
    now = now or datetime.utcnow()
    count = 0
    for model in (ScheduledMessage, MessageReminder):
        ids = session.exec(select(model.id).where(model.status == "pending", model.due_at <= now)
                           .order_by(model.due_at, model.id).limit(limit)).all()
        for identifier in ids:
            clear_scope(session)
            session.info.pop("freecollab_verified_request", None)
            claimed = session.execute(update(model).where(model.id == identifier, model.status == "pending",
                model.due_at <= now).values(status="processing"))
            if claimed.rowcount != 1:
                session.rollback()
                continue
            row = session.get(model, identifier, populate_existing=True)
            try:
                bind_authority(session, row)
                lock_channel_writes(session, row.channel_id)
                recheck_actor(session, row.user_id)
                require_channel_member(session, row.channel_id, row.user_id)
                if model is ScheduledMessage:
                    deliver_scheduled(session, row, now)
                else:
                    deliver_reminder(session, row)
                session.commit()
                count += 1
            except HTTPException:
                session.rollback()
                clear_scope(session)
                session.info.pop("freecollab_verified_request", None)
                # Do not overwrite a concurrent cancellation or snooze.
                values = {"status": "cancelled"}
                if model is ScheduledMessage:
                    values.update(encrypted_content="", claims_json="{}", failure_code="access_or_policy_changed", finished_at=now)
                else:
                    values.update(claims_json="{}")
                session.execute(update(model).where(model.id == identifier, model.status == "pending", model.due_at <= now).values(**values))
                session.commit()
            finally:
                clear_scope(session)
                session.info.pop("freecollab_verified_request", None)
    # NotificationDelivery deduplicates each recipient durably. It is a separate
    # retryable phase so a crash after message commit cannot silently lose mentions.
    import asyncio
    from .chat_notifications import notify_chat_message
    sent = session.exec(select(ScheduledMessage).where(ScheduledMessage.status == "sent",
        ScheduledMessage.notifications_delivered == False).order_by(ScheduledMessage.finished_at).limit(limit)).all()
    for row in sent:
        message = session.get(Message, row.message_id)
        if message is not None:
            asyncio.run(notify_chat_message(session, message))
        row.notifications_delivered = True
        session.add(row)
        session.commit()
    return count


def deliver_scheduled(session, row, now):
    require_native_channel(session, row.channel_id)
    require_direct_message_writable(session, row.channel_id, row.user_id)
    mentions = validate_mentions(session, row.channel_id, json.loads(row.mentions_json))
    content = decrypt_message(row.encrypted_content, row.channel_id)
    from .enterprise_governance import enforce_native_content
    enforce_native_content(session, row.channel_id, row.user_id, content, surface="message")
    parent = session.get(Message, row.parent_id) if row.parent_id else None
    if row.parent_id and (not parent or parent.deleted or parent.channel_id != row.channel_id):
        raise HTTPException(404, "Parent message no longer available")
    message = Message(id=row.id, channel_id=row.channel_id, author_id=row.user_id,
                      content=encrypt_message(content, row.channel_id), encrypted=True,
                      mentions=json.dumps(mentions), parent_id=row.parent_id)
    session.add(message)
    if parent:
        parent.thread_count = (parent.thread_count or 0) + 1
        subscribe_participant(session, row.user_id, parent)
        record_message_change(session, parent, "thread")
    record_message_change(session, message, "created")
    from .direct_messages import restore_direct_inbox
    restore_direct_inbox(session, row.channel_id)
    from .webhooks import enqueue_channel_event
    enqueue_channel_event(session, row.channel_id, "message.created", message.id)
    # Existing notification policy and deduplication remain in force. This
    # durable notice lets the sender navigate to the exact sent message.
    session.add(Notification(id="schedule-" + row.id, user_id=row.user_id, type="system",
        title="Scheduled message sent", entity_type="message", entity_id=message.id,
        action_url="#channel=" + row.channel_id + "&message=" + message.id))
    row.status, row.message_id, row.finished_at = "sent", message.id, now
    row.encrypted_content, row.claims_json = "", "{}"
    session.add(row)


def deliver_reminder(session, row):
    from .matrix_bridge import is_secure_channel
    if row.kind == "message":
        message = session.get(Message, row.item_id)
        if not message or message.deleted or message.channel_id != row.channel_id or is_secure_channel(session, row.channel_id):
            raise HTTPException(404, "Reminder target unavailable")
    elif not is_secure_channel(session, row.channel_id):
        raise HTTPException(409, "Encryption mode changed")
    notification_id = "reminder-" + row.id
    session.add(Notification(id=notification_id, user_id=row.user_id, type="system", title="Your message reminder",
        body="Open the conversation to view your saved reminder.", entity_type="message_reminder", entity_id=row.id,
        action_url="#" + urlencode({"channel": row.channel_id, "message": row.item_id})))
    row.status, row.notification_id, row.claims_json = "notified", notification_id, "{}"
    session.add(row)
