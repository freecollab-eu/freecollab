"""Channel-scoped projects and immutable task content revisions."""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class ProjectBoard(SQLModel, table=True):
    id: str = Field(primary_key=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    created_by: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    revision: int = 0
    archived: bool = False


class TaskCard(SQLModel, table=True):
    id: str = Field(primary_key=True)
    project_id: str = Field(foreign_key="projectboard.id", index=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    created_by: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    revision: int = 0
    archived: bool = False
    automation_key: str | None = Field(default=None, unique=True, index=True)


class ProjectTaskRevision(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("kind", "item_id", "revision"),)
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    kind: str
    item_id: str = Field(index=True)
    revision: int
    created_by: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    content_mode: str
    encrypted_payload: str | None = None
    matrix_event_id: str | None = None
    archived: bool = False
