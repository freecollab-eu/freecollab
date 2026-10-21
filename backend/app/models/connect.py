"""Bilateral shared channels. Organization identities never change tenant."""
from datetime import datetime
from uuid import uuid4

from sqlmodel import Field, SQLModel


class ConnectConnection(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    channel_id: str = Field(foreign_key="channel.id", unique=True, index=True)
    source_workspace_id: str = Field(foreign_key="workspace.id", index=True)
    target_workspace_id: str = Field(foreign_key="workspace.id", index=True)
    created_by: str = Field(foreign_key="user.id")
    accepted_by: str | None = Field(default=None, foreign_key="user.id")
    token_hash: str = Field(unique=True, index=True)
    status: str = Field(default="pending", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime
    accepted_at: datetime | None = None
    revoked_at: datetime | None = None
    secure_sync_pending: bool = False


class ConnectGrant(SQLModel, table=True):
    connection_id: str = Field(primary_key=True, foreign_key="connectconnection.id")
    user_id: str = Field(primary_key=True, foreign_key="user.id")
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    created_by: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    revoked_at: datetime | None = None
