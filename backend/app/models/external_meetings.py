"""Explicit customer-provider meetings; never a media or channel-content store."""
from datetime import datetime
from uuid import uuid4
from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class ExternalMeetingConnection(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    created_by: str = Field(foreign_key="user.id")
    grant_user_id: str = Field(foreign_key="user.id")
    name: str
    provider: str
    config_json: str
    encrypted_credentials: str | None = None
    source_connection_id: str | None = Field(default=None, foreign_key="remotesyncconnection.id")
    source_revision: int | None = None
    revision: int = 1
    enabled: bool = False
    status: str = "oauth_required"
    tested_revision: int | None = None
    tested_at: datetime | None = None
    consented_at: datetime = Field(default_factory=datetime.utcnow)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ExternalMeetingOAuthAttempt(SQLModel, table=True):
    state_hash: str = Field(primary_key=True)
    connection_id: str = Field(foreign_key="externalmeetingconnection.id", index=True)
    revision: int
    user_id: str = Field(foreign_key="user.id")
    native_token_hash: str
    auth_version: int
    browser_hash: str
    encrypted_verifier: str
    expires_at: datetime
    used_at: datetime | None = None


class ExternalMeeting(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("channel_id", "request_id"),)
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    request_id: str
    connection_id: str = Field(foreign_key="externalmeetingconnection.id", index=True)
    connection_revision: int
    channel_id: str = Field(foreign_key="channel.id", index=True)
    created_by: str = Field(foreign_key="user.id")
    request_hash: str
    secure: bool
    encrypted_request: str | None = None
    encrypted_result: str | None = None
    remote_fingerprint: str | None = None
    calendar_item_id: str | None = Field(default=None, foreign_key="integrationcontent.id")
    calendar_revision: int | None = None
    state: str = "pending"
    revision: int = 1
    last_error: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
