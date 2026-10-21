"""Explicit data-management jobs; no deletion is enabled by installation default."""
from datetime import datetime
from uuid import uuid4

from sqlmodel import Field, SQLModel


class WorkspaceRetentionPolicy(SQLModel, table=True):
    workspace_id: str = Field(primary_key=True, foreign_key="workspace.id")
    enabled: bool = False
    hold: bool = True
    retention_days: int = 365
    revision: int = 1
    updated_by: str | None = Field(default=None, foreign_key="user.id")
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class DataControlPreview(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    requested_by: str = Field(foreign_key="user.id")
    token_hash: str
    auth_version: int
    policy_revision: int
    encrypted_plan: str
    fingerprint: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime
    used_at: datetime | None = None


class DataControlJob(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    requested_by: str = Field(foreign_key="user.id", index=True)
    token_hash: str
    auth_version: int
    kind: str
    encrypted_request: str
    status: str = Field(default="queued", index=True)
    counts_json: str = "{}"
    artifact_size: int = 0
    artifact_sha256: str | None = None
    failure: str | None = None
    attempts: int = 0
    lease_expires_at: datetime | None = None
    lease_token: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: datetime | None = None
    expires_at: datetime


class DataFileErasure(SQLModel, table=True):
    job_id: str = Field(primary_key=True, foreign_key="datacontroljob.id")
    file_id: str = Field(primary_key=True)
    erased_at: datetime | None = None


class AccountIdentityErasure(SQLModel, table=True):
    user_id: str = Field(primary_key=True, foreign_key="user.id")
    erased_at: datetime = Field(default_factory=datetime.utcnow)
    retained_authorship_stub: bool = True
