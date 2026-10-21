"""Immutable recordings; secure channel content remains an opaque Matrix event."""
from datetime import datetime
from sqlmodel import SQLModel, Field


class ChannelClip(SQLModel, table=True):
    id: str = Field(primary_key=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    created_by: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    content_mode: str
    matrix_event_id: str | None = None
    encrypted_payload: str | None = None
    payload_hash: str
    # Existing file deletion is independent; a deleted blob makes playback
    # unavailable without preventing the author from removing the clip entry.
    file_id: str | None = Field(default=None, index=True)

