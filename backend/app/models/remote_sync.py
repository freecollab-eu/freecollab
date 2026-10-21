"""Additive, tenant-bound remote synchronization state and immutable audit data."""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class RemoteSyncConnection(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    created_by: str = Field(foreign_key="user.id")
    grant_user_id: str = Field(foreign_key="user.id")
    name: str
    provider: str
    kind: str
    config_json: str
    encrypted_credentials: str | None = None
    enabled: bool = False
    revision: int = 1
    status: str = "draft"
    tested_revision: int | None = None
    tested_at: datetime | None = None
    consented_at: datetime = Field(default_factory=datetime.utcnow)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_synced_at: datetime | None = None
    last_error: str | None = None
    next_run_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    failure_count: int = 0
    encrypted_cursor: str | None = None
    scan_after_id: str | None = None
    lease_token: str | None = None
    lease_expires_at: datetime | None = None


class RemoteSyncItem(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("connection_id", "local_id"),
                      UniqueConstraint("connection_id", "remote_id"))
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    connection_id: str = Field(foreign_key="remotesyncconnection.id", index=True)
    local_id: str
    remote_id: str | None = None
    base_local_fingerprint: str | None = None
    base_remote_etag: str | None = None
    remote_deleted: bool = False
    read_only: bool = False
    encrypted_remote_snapshot: str | None = None
    pending_operation: str | None = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class RemoteSyncConflict(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    connection_id: str = Field(foreign_key="remotesyncconnection.id", index=True)
    item_id: str = Field(foreign_key="remotesyncitem.id", index=True)
    reason: str
    local_fingerprint: str | None = None
    remote_etag: str | None = None
    remote_deleted: bool = False
    read_only: bool = False
    resolution: str | None = None
    resolved_at: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class RemoteSyncActivity(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    connection_id: str = Field(foreign_key="remotesyncconnection.id", index=True)
    event: str
    status: str
    item_id: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class RemoteSyncFileState(SQLModel, table=True):
    """Deletion is a tombstone; synced immutable file versions are retained."""
    document_id: str = Field(primary_key=True, foreign_key="filedocument.id")
    deleted: bool = False
    revision: int = 1
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class RemoteSyncOAuthAttempt(SQLModel, table=True):
    state_hash: str = Field(primary_key=True)
    connection_id: str = Field(foreign_key="remotesyncconnection.id", index=True)
    revision: int
    user_id: str = Field(foreign_key="user.id")
    native_token_hash: str
    auth_version: int
    browser_hash: str
    encrypted_verifier: str
    expires_at: datetime
    used_at: datetime | None = None
