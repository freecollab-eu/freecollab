from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from typing import Optional, Literal, Annotated
from datetime import datetime
import logging
from ..database import get_session
from ..models.notification import Notification, NotificationPreference
from ..models.collaboration import ChannelNotificationSetting, EncryptedMessageActivity, EncryptedThreadSubscription
from ..services.auth import get_current_user
from ..services.access import require_channel_member
from ..services.sso_scope import current_scope_actor_id, current_workspace_scope, require_unrestricted
from ..realtime.socket import send_to_user

router = APIRouter(prefix="/notifications", tags=["notifications"])

NOTIFICATION_CATEGORIES = {
    "mention": "mentions",
    "like": "likes",
    "comment": "comments",
    "reply": "replies",
    "follow": "follows",
    "system": "system",
}


def _workspace_scoped_actor(session, user_id: str) -> bool:
    return bool(current_workspace_scope(session) and current_scope_actor_id(session) == user_id)


def notification_channel(session, notification, _seen=None):
    """Resolve only known reference types; a caller-supplied URL proves no scope."""
    seen = set() if _seen is None else _seen
    key = (notification.entity_type, notification.entity_id)
    if key in seen or len(seen) >= 20:
        return None
    seen.add(key)
    kind, identifier = key
    if kind == "channel":
        return identifier
    if kind == "encrypted_work":
        from ..models.encrypted_work import EncryptedWorkNotice
        marker = session.get(EncryptedWorkNotice, identifier)
        return marker.channel_id if marker and marker.user_id == notification.user_id else None
    if kind == "calendar_invitation":
        from ..models.integration_content import IntegrationContent
        record = session.get(IntegrationContent, identifier)
    elif kind == "message_reminder":
        from ..models.chat_productivity import MessageReminder
        record = session.get(MessageReminder, identifier)
    elif kind == "encrypted_message":
        record = session.query(EncryptedMessageActivity).filter(EncryptedMessageActivity.event_id == identifier).first()
    elif kind in {"message", "thread"}:
        from ..models.message import Message
        record = session.get(Message, identifier)
    elif kind == "file":
        from ..models.file import File
        record = session.get(File, identifier)
    elif kind == "workflow_run":
        from ..models.workflow import WorkflowRun
        record = session.get(WorkflowRun, identifier)
    elif kind == "activity_feed_post":
        from ..models.activity_feed import ActivityFeedPost
        record = session.get(ActivityFeedPost, identifier)
    elif kind == "post":
        from ..models.post import Post
        record = session.get(Post, identifier)
        return record.flow_id if record and record.flow_type == "channel" else None
    elif kind == "comment":
        from ..models.post import Comment
        record = session.get(Comment, identifier)
        if not record or record.deleted:
            return None
        return notification_channel(session, Notification(user_id=notification.user_id, type=notification.type, title="", entity_type=record.entity_type, entity_id=record.entity_id), seen)
    else:
        return None
    return record.channel_id if record else None


def notification_visible(session, notification, user_id: str, _seen=None) -> bool:
    """Recheck referenced content; retained inbox text cannot bypass revocation."""
    seen = set() if _seen is None else _seen
    key = (notification.entity_type, notification.entity_id)
    if key in seen or len(seen) >= 20:
        return False
    seen.add(key)
    channel_id = None
    encrypted = notification.entity_type == "encrypted_message"
    metadata_only = notification.entity_type in {"workflow_run", "activity_feed_post", "message_reminder", "calendar_invitation", "encrypted_work"}
    if notification.entity_type == "encrypted_work":
        from ..models.encrypted_work import EncryptedWorkNotice
        from ..models.secure_channel import SecureChannel
        marker = session.get(EncryptedWorkNotice, notification.entity_id)
        binding = session.get(SecureChannel, marker.channel_id) if marker else None
        if not marker or marker.user_id != user_id or not binding or binding.status != "active":
            return False
        channel_id = marker.channel_id
    elif notification.entity_type == "calendar_invitation":
        from ..models.calendar_planning import CalendarAttendee
        from ..models.integration_content import IntegrationContent
        from .integrations_content import latest_revision
        item = session.get(IntegrationContent, notification.entity_id)
        invitation = session.get(CalendarAttendee, (notification.entity_id, user_id))
        revision = latest_revision(session, item.id) if item else None
        if not item or not invitation or not revision or revision.deleted:
            return False
        channel_id = item.channel_id
    elif notification.entity_type == "message_reminder":
        from ..models.chat_productivity import MessageReminder
        from ..services.matrix_bridge import is_secure_channel
        record = session.get(MessageReminder, notification.entity_id)
        if not record or record.user_id != user_id or record.status != "notified":
            return False
        if is_secure_channel(session, record.channel_id) != (record.kind == "matrix"):
            return False
        channel_id = record.channel_id
    elif encrypted:
        record = session.query(EncryptedMessageActivity).filter(EncryptedMessageActivity.event_id == notification.entity_id).first()
        if not record:
            return False
        channel_id = record.channel_id
    elif notification.entity_type in {"message", "thread"}:
        from ..models.message import Message
        record = session.get(Message, notification.entity_id)
        if not record or record.deleted:
            return False
        channel_id = record.channel_id
    elif notification.entity_type == "post":
        from ..models.post import Post
        record = session.get(Post, notification.entity_id)
        if not record or record.deleted or record.flow_type != "channel":
            return False
        channel_id = record.flow_id
    elif notification.entity_type == "comment":
        from ..models.post import Comment
        record = session.get(Comment, notification.entity_id)
        if not record or record.deleted or record.entity_type not in {"message", "post", "file", "comment"}:
            return False
        return notification_visible(session, Notification(user_id=user_id, type=notification.type, title="", entity_type=record.entity_type, entity_id=record.entity_id), user_id, seen)
    elif notification.entity_type == "file":
        from ..models.file import File
        record = session.get(File, notification.entity_id)
        if not record:
            return False
        channel_id = record.channel_id
    elif notification.entity_type == "channel":
        channel_id = notification.entity_id
    elif notification.entity_type == "activity_feed_post":
        from ..models.activity_feed import ActivityFeedPost
        record = session.get(ActivityFeedPost, notification.entity_id)
        if not record or record.archived:
            return False
        channel_id = record.channel_id
    elif metadata_only:
        from ..models.workflow import WorkflowRun
        record = session.get(WorkflowRun, notification.entity_id)
        if not record:
            return False
        channel_id = record.channel_id
    if not channel_id and _workspace_scoped_actor(session, user_id):
        # Account-wide records have no proven relationship to this workspace.
        return False
    if channel_id:
        from ..services.chat_notifications import require_native_channel
        from ..services.workspaces import require_direct_message_writable
        try:
            require_channel_member(session, channel_id, user_id)
            require_direct_message_writable(session, channel_id, user_id)
            if not encrypted and not metadata_only:
                require_native_channel(session, channel_id)
        except HTTPException:
            return False
    return True


class UpdatePreferences(BaseModel):
    mentions: Optional[bool] = None
    likes: Optional[bool] = None
    comments: Optional[bool] = None
    replies: Optional[bool] = None
    follows: Optional[bool] = None
    system: Optional[bool] = None
    in_app: Optional[bool] = None
    email: Optional[bool] = None
    push: Optional[bool] = None


class ChannelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    level: Literal["all", "mentions", "muted"] = "mentions"
    email_digest: Literal["none", "daily", "weekly"] = "none"


class EncryptedEventActivity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: Annotated[str, StringConstraints(min_length=1, max_length=1024)]
    parent_event_id: Annotated[str, StringConstraints(min_length=1, max_length=1024)] | None = None
    mentions: list[Annotated[str, StringConstraints(min_length=1, max_length=128)]] = Field(default_factory=list, max_length=50)


@router.post("/channels/{channel_id}/encrypted-events")
async def acknowledge_encrypted_event(channel_id: str, data: EncryptedEventActivity, user=Depends(get_current_user), session=Depends(get_session)):
    """Acknowledge a proven encrypted envelope; never accept message content."""
    from ..models.secure_channel import MatrixAccount
    from ..services.matrix_bridge import validate_secure_event
    from ..services.chat_notifications import validate_mentions, notify_encrypted_message
    from ..services.workspaces import require_direct_message_writable
    require_channel_member(session, channel_id, user.id)
    require_direct_message_writable(session, channel_id, user.id)
    mention_ids = validate_mentions(session, channel_id, data.mentions)
    validate_secure_event(session, channel_id, data.event_id, user.id)
    parent_author_id = None
    if data.parent_event_id:
        if data.parent_event_id == data.event_id:
            raise HTTPException(400, "An encrypted event cannot reply to itself")
        parent = validate_secure_event(session, channel_id, data.parent_event_id, user.id, require_sender=False)
        account = session.query(MatrixAccount).filter(MatrixAccount.matrix_user_id == parent.get("sender")).first()
        parent_author_id = account.user_id if account else None
    record = session.get(EncryptedMessageActivity, (channel_id, data.event_id))
    created = record is None
    if created:
        from sqlalchemy.exc import IntegrityError
        try:
            with session.begin_nested():
                record = EncryptedMessageActivity(channel_id=channel_id, event_id=data.event_id, actor_id=user.id, parent_event_id=data.parent_event_id)
                session.add(record)
                session.flush()
        except IntegrityError:
            created = False
            record = session.get(EncryptedMessageActivity, (channel_id, data.event_id))
    if not record or record.actor_id != user.id or record.parent_event_id != data.parent_event_id:
        raise HTTPException(409, "Encrypted event activity is already recorded")
    if data.parent_event_id and session.get(EncryptedThreadSubscription, (user.id, channel_id, data.parent_event_id)) is None:
        session.add(EncryptedThreadSubscription(user_id=user.id, channel_id=channel_id, matrix_event_id=data.parent_event_id, watching=True))
    if created:
        from ..services.webhooks import enqueue_channel_event
        enqueue_channel_event(session, channel_id, "channel.activity", data.event_id)
    session.commit()
    await notify_encrypted_message(session, record, mention_ids=mention_ids, parent_author_id=parent_author_id)
    return {"acknowledged": True, "created": created}


@router.get("/channels/{channel_id}/settings")
def get_channel_settings(channel_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    require_channel_member(session, channel_id, user.id)
    record = session.get(ChannelNotificationSetting, (user.id, channel_id))
    return {"level": record.level, "email_digest": record.email_digest} if record else ChannelSettings()


@router.put("/channels/{channel_id}/settings")
def set_channel_settings(channel_id: str, data: ChannelSettings, user=Depends(get_current_user), session=Depends(get_session)):
    require_channel_member(session, channel_id, user.id)
    record = session.get(ChannelNotificationSetting, (user.id, channel_id))
    if record is None:
        record = ChannelNotificationSetting(user_id=user.id, channel_id=channel_id)
    enabling_email = record.email_digest == "none" and data.email_digest != "none"
    if record.email_digest != data.email_digest:
        record.digest_since = datetime.utcnow()
        record.digest_last_queued_at = None
    record.level = data.level
    record.email_digest = data.email_digest
    record.updated_at = datetime.utcnow()
    session.add(record)
    if enabling_email and not _workspace_scoped_actor(session, user.id):
        # Choosing a digest is an explicit email opt-in. The global email
        # preference remains a master opt-out for every existing channel.
        prefs = session.query(NotificationPreference).filter(NotificationPreference.user_id == user.id).first()
        if prefs is None:
            prefs = NotificationPreference(user_id=user.id, email=True)
        else:
            prefs.email = True
        session.add(prefs)
    session.commit()
    return {"level": record.level, "email_digest": record.email_digest}


@router.get("")
def get_notifications(
    limit: int = Query(50, ge=1, le=100),
    unread_only: bool = False,
    before: Optional[str] = None,
    channel_id: Optional[str] = Query(None, min_length=1, max_length=128),
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    """Get user's notifications."""
    if channel_id:
        from ..services.workspaces import require_direct_message_writable
        require_channel_member(session, channel_id, user.id)
        require_direct_message_writable(session, channel_id, user.id)
    query = (
        session.query(Notification)
        .filter(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
    )
    
    if unread_only:
        query = query.filter(Notification.read == False)
    
    if before:
        before_notif = session.get(Notification, before)
        if (not before_notif or before_notif.user_id != user.id or not notification_visible(session, before_notif, user.id)
                or channel_id and notification_channel(session, before_notif) != channel_id):
            raise HTTPException(404, "Notification cursor not found")
        from sqlalchemy import and_, or_
        query = query.filter(or_(Notification.created_at < before_notif.created_at,
                                 and_(Notification.created_at == before_notif.created_at, Notification.id < before_notif.id)))
    
    notifications = []
    for record in query:
        if notification_visible(session, record, user.id) and (not channel_id or notification_channel(session, record) == channel_id):
            notifications.append(record)
            if len(notifications) >= limit:
                break
    
    return [
        {
            "id": n.id,
            "type": n.type,
            "actor_id": n.actor_id,
            "entity_type": n.entity_type,
            "entity_id": n.entity_id,
            "channel_id": notification_channel(session, n),
            "title": n.title,
            "body": n.body,
            "action_url": n.action_url,
            "read": n.read,
            "created_at": n.created_at.isoformat(),
        }
        for n in notifications
    ]


@router.get("/unread-count")
def get_unread_count(
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    """Get count of unread notifications."""
    records = (
        session.query(Notification)
        .filter(Notification.user_id == user.id, Notification.read == False)
        .all()
    )
    count = sum(notification_visible(session, record, user.id) for record in records)
    return {"unread_count": count}


@router.post("/{notification_id}/read")
def mark_as_read(
    notification_id: str,
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    """Mark a notification as read."""
    notification = session.get(Notification, notification_id)
    if not notification:
        raise HTTPException(404, "Notification not found")
    
    if notification.user_id != user.id:
        raise HTTPException(403, "Not your notification")
    if _workspace_scoped_actor(session, user.id) and not notification_visible(session, notification, user.id):
        raise HTTPException(404, "Notification not found")
    
    notification.read = True
    notification.read_at = datetime.utcnow()
    session.commit()
    
    return {"marked_read": True}


@router.post("/read-all")
def mark_all_as_read(
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    """Mark all notifications as read."""
    query = session.query(Notification).filter(
        Notification.user_id == user.id,
        Notification.read == False,
    )
    if _workspace_scoped_actor(session, user.id):
        read_at = datetime.utcnow()
        for notification in query:
            if notification_visible(session, notification, user.id):
                notification.read = True
                notification.read_at = read_at
                session.add(notification)
    else:
        query.update({"read": True, "read_at": datetime.utcnow()})
    session.commit()
    
    return {"marked_all_read": True}


@router.delete("/{notification_id}")
def delete_notification(
    notification_id: str,
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    """Delete a notification."""
    notification = session.get(Notification, notification_id)
    if not notification:
        raise HTTPException(404, "Notification not found")
    
    if notification.user_id != user.id:
        raise HTTPException(403, "Not your notification")
    if _workspace_scoped_actor(session, user.id) and not notification_visible(session, notification, user.id):
        raise HTTPException(404, "Notification not found")
    
    session.delete(notification)
    session.commit()
    
    return {"deleted": True}


@router.delete("")
def clear_all_notifications(
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    """Clear all notifications."""
    query = session.query(Notification).filter(
        Notification.user_id == user.id
    )
    if _workspace_scoped_actor(session, user.id):
        for notification in query:
            if notification_visible(session, notification, user.id):
                session.delete(notification)
    else:
        query.delete()
    session.commit()
    
    return {"cleared": True}


@router.get("/preferences")
def get_preferences(
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    """Get notification preferences."""
    prefs = (
        session.query(NotificationPreference)
        .filter(NotificationPreference.user_id == user.id)
        .first()
    )
    
    if not prefs:
        # Return defaults
        return {
            "mentions": True,
            "likes": True,
            "comments": True,
            "replies": True,
            "follows": True,
            "system": True,
            "in_app": True,
            "email": False,
            "push": False,
        }
    
    return {
        "mentions": prefs.mentions,
        "likes": prefs.likes,
        "comments": prefs.comments,
        "replies": prefs.replies,
        "follows": prefs.follows,
        "system": prefs.system,
        "in_app": prefs.in_app,
        "email": prefs.email,
        "push": prefs.push,
    }


@router.put("/preferences")
def update_preferences(
    data: UpdatePreferences,
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    """Update notification preferences."""
    require_unrestricted(session)
    prefs = (
        session.query(NotificationPreference)
        .filter(NotificationPreference.user_id == user.id)
        .first()
    )
    
    if not prefs:
        prefs = NotificationPreference(user_id=user.id)
        session.add(prefs)
    
    # Update only provided fields
    update_data = data.model_dump(exclude_none=True)
    for key, value in update_data.items():
        setattr(prefs, key, value)
    
    session.commit()
    
    return {"updated": True}


# Helper function to create notifications
async def create_notification(
    session,
    user_id: str,
    type: str,
    title: str,
    body: str = None,
    actor_id: str = None,
    entity_type: str = None,
    entity_id: str = None,
    action_url: str = None,
):
    """Create and send an enabled in-app notification to its recipient."""
    category = NOTIFICATION_CATEGORIES.get(type)
    if category is None:
        # New types need an explicit category; never bypass a user's settings.
        return None

    # Check preferences
    prefs = (
        session.query(NotificationPreference)
        .filter(NotificationPreference.user_id == user_id)
        .first()
    )
    
    if prefs and (not prefs.in_app or not getattr(prefs, category)):
        return None
    
    notification = Notification(
        user_id=user_id,
        type=type,
        actor_id=actor_id,
        entity_type=entity_type,
        entity_id=entity_id,
        title=title,
        body=body,
        action_url=action_url,
    )
    if not notification_visible(session, notification, user_id):
        return None
    session.add(notification)
    session.flush()
    payload = {
        "id": notification.id,
        "type": notification.type,
        "actor_id": notification.actor_id,
        "title": notification.title,
        "body": notification.body,
        "action_url": notification.action_url,
        "entity_type": notification.entity_type,
        "entity_id": notification.entity_id,
        "read": notification.read,
        "created_at": notification.created_at.isoformat(),
    }
    # Defaults are generated on the model/flush. Detach this durable return
    # value before commit expires ORM state; refreshing it would retain a new
    # connection while socket authorization waits for that same bounded pool.
    session.expunge(notification)
    session.commit()

    # Send real-time notification without retaining a SQL transaction.
    try:
        await send_to_user(user_id, "notification", payload)
    except Exception:
        # The inbox is already durable. A socket outage must not make clients
        # retry a successfully committed message and create duplicate content.
        logging.getLogger(__name__).warning("Realtime notification delivery unavailable; inbox retained")
    
    return notification
