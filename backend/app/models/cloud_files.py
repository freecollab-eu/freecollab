"""Customer-owned picker grants; bytes use the normal immutable file tables."""
from datetime import datetime
from uuid import uuid4
from sqlmodel import Field, SQLModel


class CloudFileConnection(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    created_by: str = Field(foreign_key="user.id")
    grant_user_id: str = Field(foreign_key="user.id")
    name: str
    provider: str
    kind: str = "files"
    config_json: str
    encrypted_credentials: str | None = None
    revision: int = 1
    status: str = "oauth_required"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class CloudFileOAuthAttempt(SQLModel, table=True):
    state_hash: str = Field(primary_key=True)
    connection_id: str = Field(foreign_key="cloudfileconnection.id", index=True)
    revision: int
    user_id: str = Field(foreign_key="user.id")
    native_token_hash: str
    auth_version: int
    browser_hash: str
    encrypted_verifier: str
    expires_at: datetime
    used_at: datetime | None = None


class CloudFileImport(SQLModel, table=True):
    user_id: str = Field(primary_key=True, foreign_key="user.id")
    request_id: str = Field(primary_key=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    fingerprint: str
    file_id: str | None = Field(default=None, foreign_key="file.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class CloudFileLink(SQLModel, table=True):
    id: str = Field(primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    created_by: str = Field(foreign_key="user.id")
    content_mode: str
    encrypted_payload: str | None = None
    matrix_event_id: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
