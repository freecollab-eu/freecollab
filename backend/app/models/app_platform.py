"""Workspace-owned apps. Bot identities have no account or administrative JWT."""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class AppInstallation(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    app_id: str = Field(index=True)
    bot_id: str = Field(default_factory=lambda: "bot_" + uuid4().hex, unique=True)
    granted_by: str = Field(foreign_key="user.id")
    generation: int = 1
    channel_ids_json: str
    manifest_json: str
    encrypted_config: str
    encrypted_secret: str
    origin: str
    enabled: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    revoked_at: datetime | None = None


class AppRevision(SQLModel, table=True):
    installation_id: str = Field(primary_key=True, foreign_key="appinstallation.id")
    generation: int = Field(primary_key=True)
    manifest_json: str
    channel_ids_json: str
    granted_by: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AppJob(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("installation_id", "request_id"),)
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    installation_id: str = Field(foreign_key="appinstallation.id", index=True)
    generation: int
    channel_id: str = Field(foreign_key="channel.id", index=True)
    actor_id: str = Field(foreign_key="user.id")
    command: str
    request_id: str
    request_digest: str
    encrypted_payload: str
    status: str = "pending"
    attempts: int = 0
    next_attempt_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    claimed_at: datetime | None = None
    claim_token: str | None = None
    http_status: int | None = None
    failure: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    delivered_at: datetime | None = None


class AppCallback(SQLModel, table=True):
    installation_id: str = Field(primary_key=True, foreign_key="appinstallation.id")
    event_id: str = Field(primary_key=True)
    body_digest: str
    card_id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AppCard(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    installation_id: str | None = Field(default=None, foreign_key="appinstallation.id", index=True)
    workspace_id: str = Field(foreign_key="workspace.id")
    channel_id: str = Field(foreign_key="channel.id", index=True)
    bot_id: str
    bot_name: str
    encrypted_card: str
    citations_json: str = "[]"
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ChannelRecapSchedule(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    channel_id: str = Field(foreign_key="channel.id")
    owner_id: str = Field(foreign_key="user.id")
    provider_revision: int
    interval_hours: int
    enabled: bool = True
    next_run_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    last_run_at: datetime | None = None
    last_status: str | None = None
    generation: int = 1


class AppAIDraft(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    owner_id: str = Field(foreign_key="user.id")
    kind: str
    provider_revision: int
    encrypted_result: str
    status: str = "draft"
    resource_id: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
