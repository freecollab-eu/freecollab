"""Discovery pointers for device-encrypted work. Never stores resource content."""
from datetime import datetime
from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class EncryptedWorkPointer(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("channel_id", "matrix_event_id"),)
    sequence: int | None = Field(default=None, primary_key=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    matrix_event_id: str = Field(max_length=1024)
    matrix_user_id: str = Field(max_length=512)
    published_by: str | None = Field(default=None, foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class EncryptedWorkNotice(SQLModel, table=True):
    """Private opaque progress, not decrypted resource or message metadata."""
    id: str = Field(primary_key=True, max_length=64)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    source: str = Field(max_length=16)
    sequence: int = 0


class EncryptedWorkRemoteCursor(SQLModel, table=True):
    connection_id: str = Field(primary_key=True, foreign_key="federatedconnection.id")
    sequence: int = 0
    checked_at: datetime = Field(default_factory=lambda: datetime(1970, 1, 1))
    last_error: str | None = Field(default=None, max_length=40)


class EncryptedWorkAuthority(SQLModel, table=True):
    """Publication-time authority for an opaque event, never client-claimed roles."""
    id: str = Field(primary_key=True, max_length=64)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    matrix_event_id: str = Field(max_length=1024)
    room_id: str = Field(max_length=512)
    matrix_user_id: str = Field(max_length=512)
    role: str = Field(max_length=16)
    published_by: str | None = Field(default=None, foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
