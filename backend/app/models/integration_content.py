"""Immutable channel integration metadata, separate from released schemas."""
from datetime import datetime
import uuid

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class IntegrationContent(SQLModel, table=True):
    id: str = Field(primary_key=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    kind: str
    created_by: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class IntegrationContentRevision(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("item_id", "revision"),)
    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    item_id: str = Field(foreign_key="integrationcontent.id", index=True)
    revision: int
    created_by: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    content_mode: str = "server"
    encrypted_payload: str | None = None
    matrix_event_id: str | None = None
    deleted: bool = False
