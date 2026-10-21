"""Bounded operator audit metadata; never request bodies or bearer credentials."""
from datetime import datetime
from uuid import uuid4

from sqlmodel import Field, SQLModel


class SecurityEvent(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    actor_kind: str
    actor_id: str
    workspace_id: str | None = None
    target_id: str | None = None
    action: str = Field(index=True)
    outcome: str = Field(index=True)
    status_code: int
