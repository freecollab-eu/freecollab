"""Additive organization governance, native DLP and case preservation records.

Organization administration deliberately has no relationship to channel grants.
"""
from datetime import datetime
from uuid import uuid4

from sqlmodel import Field, SQLModel
from .data_control import DataControlJob  # noqa: F401 -- register referenced additive job table
from .user import User  # noqa: F401
from .workspace import Workspace  # noqa: F401


class Organization(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    name: str
    country_code: str = ""
    region_label: str = ""
    require_sso: bool = False
    revision: int = 1
    created_by: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class OrganizationMember(SQLModel, table=True):
    organization_id: str = Field(primary_key=True, foreign_key="organization.id")
    user_id: str = Field(primary_key=True, foreign_key="user.id")
    role: str = "admin"


class OrganizationWorkspace(SQLModel, table=True):
    workspace_id: str = Field(primary_key=True, foreign_key="workspace.id")
    organization_id: str = Field(foreign_key="organization.id", index=True)
    linked_by: str = Field(foreign_key="user.id")
    linked_at: datetime = Field(default_factory=datetime.utcnow)


class NativeDLPPolicy(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    name: str
    detector: str
    encrypted_literal: str | None = None
    action: str = "warn"
    surfaces_json: str = '["message","file","app","assistant","search","document"]'
    block_uninspectable_files: bool = False
    enabled: bool = True
    revision: int = 1
    created_by: str = Field(foreign_key="user.id")
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class GovernanceCase(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    encrypted_title: str
    channel_ids_json: str = "[]"
    custodian_ids_json: str = "[]"
    status: str = "active"
    revision: int = 1
    created_by: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    released_at: datetime | None = None


class GovernanceCaseExport(SQLModel, table=True):
    job_id: str = Field(primary_key=True, foreign_key="datacontroljob.id")
    case_id: str = Field(foreign_key="governancecase.id", index=True)
    requested_by: str = Field(foreign_key="user.id")
    case_revision: int
    reviewed_by: str | None = Field(default=None, foreign_key="user.id")
    reviewed_at: datetime | None = None
    review_state: str = "pending"


class GovernanceAudit(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    workspace_id: str | None = Field(default=None, foreign_key="workspace.id", index=True)
    organization_id: str | None = Field(default=None, foreign_key="organization.id", index=True)
    actor_id: str = Field(foreign_key="user.id")
    action: str
    target_id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
