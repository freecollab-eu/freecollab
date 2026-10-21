"""Additive workspace ownership; existing channel/user tables remain intact."""
from datetime import datetime
from uuid import uuid4

from sqlmodel import Field, SQLModel


class Workspace(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    name: str
    logo_filename: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    is_legacy: bool = False


class WorkspaceMember(SQLModel, table=True):
    workspace_id: str = Field(foreign_key="workspace.id", primary_key=True)
    user_id: str = Field(foreign_key="user.id", primary_key=True)
    role: str = "member"
    joined_at: datetime = Field(default_factory=datetime.utcnow)


class WorkspaceChannel(SQLModel, table=True):
    channel_id: str = Field(foreign_key="channel.id", primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)


class PersonalWorkspace(SQLModel, table=True):
    """The owner's private compatibility home, independent of custom team roles."""
    user_id: str = Field(foreign_key="user.id", primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", unique=True)


class WorkspaceInvitation(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    token_hash: str = Field(unique=True, index=True)
    role: str = "member"
    created_by: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime
    accepted_at: datetime | None = None
    accepted_by: str | None = Field(default=None, foreign_key="user.id")
    revoked_at: datetime | None = None
