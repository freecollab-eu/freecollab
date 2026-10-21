"""Tenant-owned identities are separate from global personal accounts."""
from datetime import datetime

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class WorkspaceIdentityPolicy(SQLModel, table=True):
    workspace_id: str = Field(primary_key=True, foreign_key="workspace.id")
    mode: str = "linked"
    identity_claim: str = "sub"
    revision: int = 1
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class WorkforcePrincipal(SQLModel, table=True):
    # The canonical workspace/issuer/client/subject uniqueness is enforced by
    # OIDCIdentity's fixed-length digest key. Avoid an oversized PostgreSQL index
    # on customer-controlled issuer/client identifiers here.
    __table_args__ = (UniqueConstraint("workspace_id", "external_id"),)
    user_id: str = Field(primary_key=True, foreign_key="user.id")
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    external_id: str
    managed_email: str = ""
    active: bool = True
    issuer: str | None = None
    client_id: str | None = None
    subject: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class WorkforceOIDCAttempt(SQLModel, table=True):
    state_hash: str = Field(primary_key=True, foreign_key="oidcloginattempt.state_hash")
    workspace_id: str = Field(foreign_key="workspace.id")
    policy_revision: int = 1
    invitation_id: str | None = Field(default=None, foreign_key="workspaceinvitation.id")
    invitation_hash: str | None = None


class WorkforceInvitation(SQLModel, table=True):
    invitation_id: str = Field(primary_key=True, foreign_key="workspaceinvitation.id")
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
