"""Additive collaboration records; no existing message schema is altered."""
from datetime import datetime
from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel
import uuid


class MessageChange(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    message_id: str = Field(foreign_key="message.id", index=True)
    kind: str
    changed_at: datetime = Field(default_factory=datetime.utcnow)


class ThreadSubscription(SQLModel, table=True):
    user_id: str = Field(foreign_key="user.id", primary_key=True)
    message_id: str = Field(foreign_key="message.id", primary_key=True)
    watching: bool = True
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class EncryptedThreadSubscription(SQLModel, table=True):
    user_id: str = Field(foreign_key="user.id", primary_key=True)
    channel_id: str = Field(foreign_key="channel.id", primary_key=True)
    matrix_event_id: str = Field(primary_key=True)
    watching: bool = True
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class EncryptedMessageActivity(SQLModel, table=True):
    channel_id: str = Field(foreign_key="channel.id", primary_key=True)
    event_id: str = Field(primary_key=True, index=True)
    actor_id: str = Field(foreign_key="user.id")
    parent_event_id: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ChannelNotificationSetting(SQLModel, table=True):
    user_id: str = Field(foreign_key="user.id", primary_key=True)
    channel_id: str = Field(foreign_key="channel.id", primary_key=True)
    level: str = "mentions"
    email_digest: str = "none"
    digest_since: datetime = Field(default_factory=datetime.utcnow)
    digest_last_queued_at: datetime | None = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class NotificationDelivery(SQLModel, table=True):
    event_id: str = Field(primary_key=True)
    user_id: str = Field(foreign_key="user.id", primary_key=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    notification_id: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ChannelPin(SQLModel, table=True):
    channel_id: str = Field(foreign_key="channel.id", primary_key=True)
    item_id: str = Field(primary_key=True)
    kind: str = "message"
    created_by: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Bookmark(SQLModel, table=True):
    user_id: str = Field(foreign_key="user.id", primary_key=True)
    channel_id: str = Field(foreign_key="channel.id", primary_key=True)
    item_id: str = Field(primary_key=True)
    kind: str = "message"
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Decision(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    created_by: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class DecisionRevision(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("decision_id", "revision"),)
    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    decision_id: str = Field(foreign_key="decision.id", index=True)
    revision: int
    created_by: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    content_mode: str = "server"
    encrypted_payload: str | None = None
    matrix_event_id: str | None = None
