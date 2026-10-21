"""Additive customer-owned OIDC configuration and authentication scopes."""
from datetime import datetime
import secrets

from sqlmodel import Field, SQLModel


class WorkspaceOIDCProvider(SQLModel, table=True):
    workspace_id: str = Field(primary_key=True, foreign_key="workspace.id")
    login_id: str = Field(default_factory=lambda: secrets.token_urlsafe(24), unique=True, index=True)
    name: str
    issuer_url: str
    client_id: str
    encrypted_client_secret: str | None = None
    scopes: str = "openid profile email"
    enabled: bool = False
    revision: int = 1
    tested_revision: int | None = None
    tested_at: datetime | None = None
    created_by: str = Field(foreign_key="user.id")
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class WorkspaceOIDCAttempt(SQLModel, table=True):
    state_hash: str = Field(primary_key=True, foreign_key="oidcloginattempt.state_hash")
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    provider_revision: int


class WorkspaceOIDCIdentity(SQLModel, table=True):
    identity_id: str = Field(primary_key=True, foreign_key="oidcidentity.id")
    workspace_id: str = Field(foreign_key="workspace.id", index=True)


class WorkspaceOIDCMFAChallenge(SQLModel, table=True):
    token_hash: str = Field(primary_key=True, foreign_key="mfachallenge.token_hash")
    workspace_id: str = Field(foreign_key="workspace.id")
    provider_revision: int


class WorkspaceOIDCSession(SQLModel, table=True):
    token_hash: str = Field(primary_key=True, foreign_key="activesession.token_hash")
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    provider_revision: int
