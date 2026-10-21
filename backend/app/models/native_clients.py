"""PKCE-bound native clients. Bearer credentials are never stored here."""
from datetime import datetime
from uuid import uuid4
from sqlmodel import Field, SQLModel
from .secure_channel import MatrixSession  # noqa: F401 -- register the additive FK target


class NativeClientGrant(SQLModel, table=True):
    code_hash: str = Field(primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    parent_token_hash: str = Field(index=True)
    source_claims: str
    code_challenge: str
    redirect_uri: str
    client_kind: str
    expires_at: datetime = Field(index=True)
    consumed_at: datetime | None = None


class NativeClientSession(SQLModel, table=True):
    token_hash: str = Field(primary_key=True, foreign_key="activesession.token_hash")
    id: str = Field(default_factory=lambda: uuid4().hex, unique=True)
    parent_token_hash: str = Field(index=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    source_claims: str
    client_kind: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class NativeClientMatrixBinding(SQLModel, table=True):
    """Independent marker keeps loss of a native sidecar from broadening Matrix."""
    matrix_token_hash: str = Field(primary_key=True, foreign_key="matrixsession.matrix_token_hash")
    native_client_id: str
