"""Consent-bound native device capabilities and opaque delivery references."""
from datetime import datetime
from uuid import uuid4
from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class NativePushDevice(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    platform: str
    token_hash: str = Field(unique=True, index=True)
    encrypted_token: str
    auth_token_hash: str = Field(index=True)
    encrypted_claims: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    confirmed_at: datetime | None = None
    challenge_hash: str | None = None
    challenge_expires_at: datetime | None = None
    active: bool = False
    calls_enabled: bool = False
    last_notification_at: datetime = Field(default_factory=datetime.utcnow)
    last_notification_id: str = ""
    last_scanned_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class NativePushDelivery(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("device_id", "source_key"),)
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    device_id: str = Field(foreign_key="nativepushdevice.id", index=True)
    source_key: str
    channel_id: str = Field(foreign_key="channel.id", index=True)
    notification_id: str | None = Field(default=None, foreign_key="notification.id")
    meeting_id: str | None = None
    transport: str | None = None
    kind: str = "activity"
    status: str = Field(default="pending", index=True)
    attempts: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime
    next_attempt_at: datetime = Field(default_factory=datetime.utcnow)
    claimed_at: datetime | None = None
    delivered_at: datetime | None = None
    failure_code: str | None = None
