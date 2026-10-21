"""Native file discussions and short-lived, digest-only Office capabilities."""
from datetime import datetime
from sqlmodel import Field, SQLModel


class FileComment(SQLModel, table=True):
    id: str = Field(primary_key=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    document_id: str = Field(index=True)
    file_id: str
    created_by: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    encrypted_text: str


class OfficeEditSession(SQLModel, table=True):
    id: str = Field(primary_key=True)
    secret_hash: str
    document_id: str = Field(index=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    native_session_hash: str
    claims_json: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime = Field(index=True)
    revoked_at: datetime | None = None
    last_file_id: str
    filename: str
    content_type: str


class OfficeEditLock(SQLModel, table=True):
    document_id: str = Field(primary_key=True)
    session_id: str
    token_hash: str
    expires_at: datetime
