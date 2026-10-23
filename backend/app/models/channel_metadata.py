"""Additive, versioned channel labels; secure rows retain opaque references only."""
from datetime import datetime

from sqlmodel import Field, SQLModel


class ChannelMetadata(SQLModel, table=True):
    channel_id: str = Field(foreign_key="channel.id", primary_key=True)
    version: int = 1
    content_mode: str = "server"
    encrypted_payload: str | None = None
    matrix_event_id: str | None = None
    created_by: str = Field(foreign_key="user.id")
    updated_by: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
