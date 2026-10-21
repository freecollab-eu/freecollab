"""Stable two-person conversations and personal inbox controls."""
from datetime import datetime

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class DirectMessage(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("workspace_id", "user_low", "user_high", name="uq_dm_workspace_pair"),)
    channel_id: str = Field(foreign_key="channel.id", primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    user_low: str = Field(foreign_key="user.id")
    user_high: str = Field(foreign_key="user.id")


class DirectMessageState(SQLModel, table=True):
    channel_id: str = Field(foreign_key="channel.id", primary_key=True)
    user_id: str = Field(foreign_key="user.id", primary_key=True)
    archived: bool = False


class UserBlock(SQLModel, table=True):
    blocker_id: str = Field(foreign_key="user.id", primary_key=True)
    blocked_id: str = Field(foreign_key="user.id", primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
