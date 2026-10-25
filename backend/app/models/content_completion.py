"""Additive content references, encrypted local semantic caches and private events."""
from datetime import datetime
from uuid import uuid4
from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class ContentCollectionItem(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("stack_id", "kind", "resource_id"),)
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    stack_id: str = Field(foreign_key="shareddocumentstack.id", index=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    kind: str
    resource_id: str
    parent_id: str | None = None
    created_by: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ChannelSemanticIndex(SQLModel, table=True):
    channel_id: str = Field(foreign_key="channel.id", primary_key=True)
    revision: int = 1
    mirror_backend: str = ""
    mirror_generation: str = ""
    encrypted_payload: str
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class PersonalCalendarEvent(SQLModel, table=True):
    id: str = Field(primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    revision: int = 1
    encrypted_payload: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
