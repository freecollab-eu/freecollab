"""Additive tenant-owned SCIM directory state. Bearer credentials are hash-only."""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class SCIMDirectory(SQLModel, table=True):
    workspace_id: str = Field(primary_key=True, foreign_key="workspace.id")
    directory_id: str = Field(default_factory=lambda: uuid4().hex, unique=True, index=True)
    token_hash: str | None = Field(default=None, unique=True)
    token_issued_by: str | None = Field(default=None, foreign_key="user.id")
    enabled: bool = False
    token_version: int = 0
    default_role: str = "member"
    created_by: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    rotated_at: datetime | None = None
    last_used_at: datetime | None = None


class SCIMUser(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("workspace_id", "user_name_normalized"),
                      UniqueConstraint("workspace_id", "external_id"))
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    user_id: str = Field(foreign_key="user.id", unique=True)
    user_name: str
    user_name_normalized: str
    external_id: str
    profile_json: str = "{}"
    active: bool = True
    deleted: bool = False
    base_role: str = "member"
    applied_role: str = "member"
    version: int = 1
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class SCIMGroup(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("workspace_id", "display_name_normalized"),
                      UniqueConstraint("workspace_id", "external_id"))
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    display_name: str
    display_name_normalized: str
    external_id: str | None = None
    version: int = 1
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class SCIMGroupMember(SQLModel, table=True):
    group_id: str = Field(primary_key=True, foreign_key="scimgroup.id")
    scim_user_id: str = Field(primary_key=True, foreign_key="scimuser.id")


class SCIMGroupMapping(SQLModel, table=True):
    group_id: str = Field(primary_key=True, foreign_key="scimgroup.id")
    workspace_role: str | None = None


class SCIMChannelMapping(SQLModel, table=True):
    group_id: str = Field(primary_key=True, foreign_key="scimgroup.id")
    channel_id: str = Field(primary_key=True, foreign_key="channel.id")
    role: str = "member"
    granted_by: str = Field(foreign_key="user.id")


class SCIMChannelGrant(SQLModel, table=True):
    user_id: str = Field(primary_key=True, foreign_key="user.id")
    channel_id: str = Field(primary_key=True, foreign_key="channel.id")
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    applied_role: str = "member"
    was_present: bool = False
    previous_role: str | None = None


class SCIMAudit(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    action: str
    resource_type: str
    resource_id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
