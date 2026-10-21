"""Additive private conversations and personal delivery records."""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class GroupConversation(SQLModel, table=True):
    channel_id: str = Field(foreign_key="channel.id", primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    participants_hash: str = Field(index=True)
    created_by: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ScheduledMessage(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("user_id", "client_id"),)
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    client_id: str
    request_digest: str
    encrypted_content: str
    mentions_json: str = "[]"
    parent_id: str | None = None
    due_at: datetime = Field(index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    status: str = Field(default="pending", index=True)
    session_hash: str
    claims_json: str
    message_id: str | None = None
    finished_at: datetime | None = Field(default=None, index=True)
    failure_code: str | None = None
    notifications_delivered: bool = False


class MessageReminder(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    item_id: str
    kind: str
    due_at: datetime = Field(index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    status: str = Field(default="pending", index=True)
    notification_id: str | None = None
    completed_at: datetime | None = None
    session_hash: str
    claims_json: str


class SavedItemCompletion(SQLModel, table=True):
    user_id: str = Field(foreign_key="user.id", primary_key=True)
    channel_id: str = Field(foreign_key="channel.id", primary_key=True)
    item_id: str = Field(primary_key=True)
    completed_at: datetime = Field(default_factory=datetime.utcnow)
