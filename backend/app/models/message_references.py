"""Additive message pointers and channel-local notification groups.

Content and resource titles stay in their canonical encrypted records. Spans
refer to the message's existing text; these tables store only typed IDs.
"""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlmodel import Field, SQLModel


class MessageReference(SQLModel, table=True):
    __table_args__ = (CheckConstraint('start >= 0 AND "end" > start'),)
    message_id: str = Field(foreign_key="message.id", primary_key=True)
    start: int = Field(primary_key=True)
    end: int
    kind: str
    target_id: str


class ScheduledMessageReference(SQLModel, table=True):
    __table_args__ = (CheckConstraint('start >= 0 AND "end" > start'),)
    schedule_id: str = Field(foreign_key="scheduledmessage.id", primary_key=True)
    start: int = Field(primary_key=True)
    end: int
    kind: str
    target_id: str


class MentionGroup(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("channel_id", "name_key"),)
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    name: str
    name_key: str
    created_by: str = Field(foreign_key="user.id")
    version: int = 1
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class MentionGroupMember(SQLModel, table=True):
    group_id: str = Field(foreign_key="mentiongroup.id", primary_key=True)
    user_id: str = Field(foreign_key="user.id", primary_key=True)
