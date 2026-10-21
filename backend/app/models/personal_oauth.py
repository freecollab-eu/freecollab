"""Customer-owned OAuth apps and user-owned, source-bound provider grants.

Additive tables deliberately keep application secrets and token rotations out of
the legacy manual search/application credential formats.
"""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class PersonalOAuthClient(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("workspace_id", "provider", "purpose"),)
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    provider: str
    purpose: str
    client_id: str
    tenant_id: str = ""
    encrypted_secret: str = ""
    revision: int = 1
    enabled: bool = True
    configured_by: str = Field(foreign_key="user.id")
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class PersonalOAuthGrant(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    client_id: str = Field(foreign_key="personaloauthclient.id", index=True)
    client_revision: int
    provider: str
    purpose: str
    target_id: str = Field(index=True)
    target_revision: int = 1
    name: str
    config_json: str = "{}"
    source_hash: str
    encrypted_source_claims: str
    encrypted_tokens: str = ""
    revision: int = 1
    status: str = "pending"
    expires_at: datetime | None = None
    refresh_claim: str | None = None
    refresh_started_at: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_checked_at: datetime = Field(default_factory=lambda: datetime(1970, 1, 1), index=True)
    revoked_at: datetime | None = None


class PersonalOAuthAttempt(SQLModel, table=True):
    state_hash: str = Field(primary_key=True)
    grant_id: str = Field(foreign_key="personaloauthgrant.id", index=True)
    browser_hash: str
    encrypted_verifier: str
    expires_at: datetime = Field(index=True)
    used_at: datetime | None = None
