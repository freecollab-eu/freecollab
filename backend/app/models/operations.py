"""Operational state without messages, recipients or integration credentials."""
from datetime import datetime

from sqlmodel import Field, SQLModel


class WorkerHeartbeat(SQLModel, table=True):
    name: str = Field(primary_key=True)
    last_seen_at: datetime = Field(default_factory=datetime.utcnow)


class MailOperation(SQLModel, table=True):
    """Minimal audit for explicit operator actions; outbox content stays private."""
    outbox_id: str = Field(primary_key=True, foreign_key="mailoutbox.id")
    requested_by: str = Field(foreign_key="user.id")
    kind: str = "diagnostic"
    created_at: datetime = Field(default_factory=datetime.utcnow)
