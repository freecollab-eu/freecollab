"""Customer-owned embedding registrations and one-use, credential-free handoffs."""
from datetime import datetime
from uuid import uuid4

from sqlmodel import Field, SQLModel
from .secure_channel import MatrixSession  # noqa: F401 -- register additive FK target


class EmbedApplication(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    name: str
    parent_origin: str
    enabled: bool = True
    revision: int = 1
    created_by: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class EmbedGrant(SQLModel, table=True):
    code_hash: str = Field(primary_key=True)
    application_id: str = Field(foreign_key="embedapplication.id", index=True)
    application_revision: int
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    provider_revision: int
    channel_id: str = Field(foreign_key="channel.id")
    parent_origin: str
    code_challenge: str
    user_id: str = Field(foreign_key="user.id")
    parent_token_hash: str = Field(index=True)
    source_issued_at: int
    source_expires_at: int
    source_auth_version: int
    source_otp: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime = Field(index=True)
    consumed_at: datetime | None = None


class EmbedSession(SQLModel, table=True):
    token_hash: str = Field(primary_key=True, foreign_key="activesession.token_hash")
    application_id: str = Field(foreign_key="embedapplication.id", index=True)
    application_revision: int
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    channel_id: str = Field(foreign_key="channel.id")
    parent_token_hash: str = Field(index=True)
    source_expires_at: int


class EmbedMatrixBinding(SQLModel, table=True):
    """Independent marker prevents a lost child sidecar from broadening Matrix."""
    matrix_token_hash: str = Field(primary_key=True, foreign_key="matrixsession.matrix_token_hash")
    application_id: str
