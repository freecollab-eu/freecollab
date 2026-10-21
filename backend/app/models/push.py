"""Device-scoped Web Push and idempotent mobile message receipts."""
from datetime import datetime
from uuid import uuid4
from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class PushSubscription(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    endpoint_hash: str = Field(unique=True, index=True)
    encrypted_subscription: str
    auth_token_hash: str = Field(index=True)
    auth_version: int
    scope_workspace_id: str | None = None
    scope_provider_revision: int | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    confirmed_at: datetime | None = None
    challenge_hash: str | None = None
    challenge_expires_at: datetime | None = None
    active: bool = False
    last_notification_at: datetime = Field(default_factory=datetime.utcnow)
    last_notification_id: str = ""
    last_scanned_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class PushDelivery(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("subscription_id", "notification_id"),)
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    subscription_id: str = Field(foreign_key="pushsubscription.id", index=True)
    notification_id: str = Field(foreign_key="notification.id", index=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    status: str = Field(default="pending", index=True)
    attempts: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    next_attempt_at: datetime = Field(default_factory=datetime.utcnow)
    claimed_at: datetime | None = None
    delivered_at: datetime | None = None
    failure_code: str | None = None


class OfflineMessageReceipt(SQLModel, table=True):
    user_id: str = Field(foreign_key="user.id", primary_key=True)
    client_id: str = Field(primary_key=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    message_id: str | None = Field(default=None, foreign_key="message.id", unique=True)
    payload_digest: str
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
