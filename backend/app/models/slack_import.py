"""Bounded, encrypted, reviewable Slack archive imports."""
from datetime import datetime
from uuid import uuid4
from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class SlackImport(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("workspace_id", "archive_digest"),)
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    actor_id: str = Field(foreign_key="user.id")
    archive_digest: str
    encrypted_archive: str
    status: str = "preview"
    request_digest: str | None = None
    result_json: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime


class SlackImportMessage(SQLModel, table=True):
    message_id: str = Field(primary_key=True, foreign_key="message.id")
    import_id: str = Field(foreign_key="slackimport.id", index=True)
    source_channel: str
    source_timestamp: str
