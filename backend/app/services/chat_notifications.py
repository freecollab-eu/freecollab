"""Member-scoped chat notifications. Notification text never copies message text."""
from datetime import datetime, timedelta
import logging
import json

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy import update
from sqlmodel import select

from ..models.channel import ChannelMember
from ..models.collaboration import ChannelNotificationSetting, EncryptedMessageActivity, EncryptedThreadSubscription, MessageChange, NotificationDelivery, ThreadSubscription
from ..models.message import Message
from ..models.notification import NotificationPreference
from ..models.user import User
from ..services.access import require_channel_member

logger = logging.getLogger(__name__)


def require_native_channel(session, channel_id: str):
    from .matrix_bridge import is_secure_channel
    if is_secure_channel(session, channel_id):
        raise HTTPException(409, "This channel requires the encrypted messaging client")


def message_mentions(message: Message) -> list[str]:
    try:
        value = json.loads(message.mentions or "[]")
    except (ValueError, TypeError):
        return []
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def validate_mentions(session, channel_id: str, ids: list[str]) -> list[str]:
    result = list(dict.fromkeys(ids))
    for user_id in result:
        try:
            require_channel_member(session, channel_id, user_id)
        except HTTPException:
            raise HTTPException(400, "Mentioned people must be current channel members") from None
        user = session.get(User, user_id)
        if not user or user.is_blocked:
            raise HTTPException(400, "Mentioned people must be active channel members")
    return result


def record_message_change(session, message: Message, kind: str):
    session.flush()
    session.add(MessageChange(channel_id=message.channel_id, message_id=message.id, kind=kind))


def subscribe_participant(session, user_id: str, parent: Message):
    if session.get(ThreadSubscription, (user_id, parent.id)) is None:
        session.add(ThreadSubscription(user_id=user_id, message_id=parent.id, watching=True))


def is_watching(session, user_id: str, parent: Message) -> bool:
    subscription = session.get(ThreadSubscription, (user_id, parent.id))
    return subscription.watching if subscription else parent.author_id == user_id


async def notify_chat_message(session, message: Message, *, mention_ids: list[str] | None = None):
    """Persist at most one notification per message/recipient, then send best effort."""
    parent = session.get(Message, message.parent_id) if message.parent_id else None
    members = session.exec(select(ChannelMember.user_id).where(ChannelMember.channel_id == message.channel_id)).all()
    watchers = {user_id for user_id in members if parent and is_watching(session, user_id, parent)}
    await deliver_activity(session, channel_id=message.channel_id, actor_id=message.author_id,
                           event_id="message:" + message.id, entity_type="message", entity_id=message.id,
                           mentioned=set(mention_ids if mention_ids is not None else message_mentions(message)), watchers=watchers)


async def notify_encrypted_message(session, activity: EncryptedMessageActivity, *, mention_ids: list[str], parent_author_id: str | None = None):
    watchers = set()
    if activity.parent_event_id:
        subscriptions = session.exec(select(EncryptedThreadSubscription).where(
            EncryptedThreadSubscription.channel_id == activity.channel_id,
            EncryptedThreadSubscription.matrix_event_id == activity.parent_event_id,
        )).all()
        watchers = {record.user_id for record in subscriptions if record.watching}
        if parent_author_id and not any(record.user_id == parent_author_id and not record.watching for record in subscriptions):
            watchers.add(parent_author_id)
    await deliver_activity(session, channel_id=activity.channel_id, actor_id=activity.actor_id,
                           event_id="matrix:" + activity.event_id, entity_type="encrypted_message", entity_id=activity.event_id,
                           mentioned=set(mention_ids), watchers=watchers)


async def deliver_activity(session, *, channel_id: str, actor_id: str, event_id: str,
                           entity_type: str, entity_id: str, mentioned: set[str], watchers: set[str]):
    from ..api.notifications import create_notification
    from .workspaces import require_direct_message_writable
    try:
        require_direct_message_writable(session, channel_id, actor_id)
    except HTTPException:
        return
    members = session.exec(select(ChannelMember.user_id).where(ChannelMember.channel_id == channel_id)).all()
    for user_id in members:
        if user_id == actor_id:
            continue
        recipient = session.get(User, user_id)
        if not recipient or recipient.is_blocked:
            continue
        try:
            require_channel_member(session, channel_id, recipient.id)
        except HTTPException:
            continue
        settings = session.get(ChannelNotificationSetting, (recipient.id, channel_id))
        level = settings.level if settings else "mentions"
        if level == "muted":
            continue
        if recipient.id in mentioned:
            notification_type, title = "mention", "You were mentioned"
        elif recipient.id in watchers:
            notification_type, title = "reply", "New reply in a watched thread"
        elif level == "all":
            notification_type, title = "system", "New channel message"
        else:
            continue
        key = (event_id, recipient.id)
        if session.get(NotificationDelivery, key):
            continue
        delivery = NotificationDelivery(event_id=key[0], user_id=recipient.id, channel_id=channel_id)
        session.add(delivery)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            continue
        notification = await create_notification(
            session, recipient.id, notification_type, title,
            body="Open FreeCollab to view this activity.", actor_id=actor_id,
            entity_type=entity_type, entity_id=entity_id,
            action_url="#channel=" + channel_id + "&message=" + entity_id,
        )
        if notification:
            delivery.notification_id = notification.id
        session.add(delivery)
        session.commit()


def queue_due_digests(session, enqueue_mail, public_app_url: str, now: datetime | None = None) -> int:
    """Queue opt-in activity counts atomically with their cursor; never quote content.

    The worker calls this with a persistent-outbox enqueue function accepting
    (session, recipient, subject, body) without committing. Sending is separate.
    """
    from .mail import mail_available
    if not mail_available():
        return 0
    now = now or datetime.utcnow()
    queued = 0
    settings = session.exec(select(ChannelNotificationSetting).where(ChannelNotificationSetting.email_digest != "none")).all()
    for setting in settings:
        interval = timedelta(days=1 if setting.email_digest == "daily" else 7)
        since = setting.digest_last_queued_at or setting.digest_since
        if now - since < interval:
            continue
        user = session.get(User, setting.user_id)
        if not user or user.is_blocked:
            continue
        preferences = session.exec(select(NotificationPreference).where(NotificationPreference.user_id == user.id)).first()
        if preferences and not preferences.email:
            continue
        try:
            require_channel_member(session, setting.channel_id, setting.user_id)
            from .workspaces import require_direct_message_writable
            require_direct_message_writable(session, setting.channel_id, setting.user_id)
        except HTTPException:
            continue
        changes = session.exec(select(MessageChange).where(
            MessageChange.channel_id == setting.channel_id,
            MessageChange.kind == "created", MessageChange.changed_at > since,
            MessageChange.changed_at <= now,
        )).all()
        count = 0
        for change in changes:
            message = session.get(Message, change.message_id)
            if message and not message.deleted and message.author_id != user.id:
                count += 1
        encrypted_changes = session.exec(select(EncryptedMessageActivity).where(
            EncryptedMessageActivity.channel_id == setting.channel_id,
            EncryptedMessageActivity.actor_id != user.id,
            EncryptedMessageActivity.created_at > since,
            EncryptedMessageActivity.created_at <= now,
        )).all()
        count += len(encrypted_changes)
        # Claim the period in the same transaction as enqueueing the email.
        # Concurrent workers cannot enqueue the same completed period twice.
        expected = ChannelNotificationSetting.digest_last_queued_at.is_(None) if setting.digest_last_queued_at is None else ChannelNotificationSetting.digest_last_queued_at == setting.digest_last_queued_at
        claimed = session.exec(update(ChannelNotificationSetting).where(
            ChannelNotificationSetting.user_id == setting.user_id,
            ChannelNotificationSetting.channel_id == setting.channel_id,
            expected,
        ).values(digest_last_queued_at=now).execution_options(synchronize_session=False))
        if claimed.rowcount != 1:
            continue
        if count:
            enqueue_mail(session, user.email, "Your FreeCollab activity summary",
                         f"There are {count} new messages in a channel you follow.\n\nOpen your workspace: {public_app_url.rstrip('/')}\n\nYour message content is not included in this email.")
            queued += 1
    session.commit()
    return queued
