"""Explicit calendar-provider effects and revocable native subscription grants."""
from datetime import datetime
from uuid import uuid4
from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class CalendarProviderPublication(SQLModel, table=True):
    __table_args__ = (UniqueConstraint('item_id', 'connection_id'),)
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    workspace_id: str = Field(foreign_key='workspace.id', index=True)
    channel_id: str = Field(foreign_key='channel.id', index=True)
    item_id: str = Field(foreign_key='integrationcontent.id', index=True)
    connection_id: str = Field(foreign_key='remotesyncconnection.id', index=True)
    user_id: str = Field(foreign_key='user.id')
    connection_revision: int
    revision: int = 1
    remote_id: str | None = None
    remote_etag: str | None = None
    encrypted_result: str | None = None
    state: str = 'draft'
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class CalendarProviderJob(SQLModel, table=True):
    __table_args__ = (UniqueConstraint('user_id', 'client_id'),)
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    client_id: str
    request_hash: str
    user_id: str = Field(foreign_key='user.id', index=True)
    channel_id: str = Field(foreign_key='channel.id', index=True)
    item_id: str = Field(foreign_key='integrationcontent.id')
    publication_id: str = Field(foreign_key='calendarproviderpublication.id', index=True)
    action: str
    event_revision: int
    publication_revision: int
    connection_revision: int
    encrypted_payload: str | None = None
    session_hash: str
    claims_json: str
    status: str = Field(default='queued', index=True)
    claim_token: str | None = None
    lease_expires_at: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    finished_at: datetime | None = None
    failure_code: str | None = None


class CalendarSubscription(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    workspace_id: str = Field(foreign_key='workspace.id', index=True)
    channel_id: str = Field(foreign_key='channel.id', index=True)
    user_id: str = Field(foreign_key='user.id', index=True)
    token_hash: str = Field(unique=True)
    auth_version: int
    include_titles: bool = False
    expires_at: datetime
    revoked_at: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
