"""Scoped webhook configuration and durable, metadata-only delivery outbox."""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class WebhookEndpoint(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    created_by: str = Field(foreign_key="user.id")
    granted_by: str = Field(foreign_key="user.id")
    name: str
    origin: str
    encrypted_url: str
    encrypted_secret: str
    channel_ids_json: str
    event_types_json: str
    enabled: bool = True
    generation: int = 1
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    deleted_at: datetime | None = None


class WebhookDelivery(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("webhook_id", "event_key", name="uq_webhook_event"),)
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    webhook_id: str = Field(foreign_key="webhookendpoint.id", index=True)
    event_key: str
    event_id: str
    event_type: str
    channel_id: str | None = Field(default=None, index=True)
    # Strictly constructed IDs/type/time only; never accepts a caller payload.
    payload: str
    generation: int
    status: str = Field(default="pending", index=True)
    attempts: int = 0
    next_attempt_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    claimed_at: datetime | None = None
    claim_token: str | None = None
    http_status: int | None = None
    failure: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    delivered_at: datetime | None = None
