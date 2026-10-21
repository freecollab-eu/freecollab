"""OIDC identity bindings and short-lived browser-bound authorization state.

No provider access/refresh token or ID token is retained. These additive tables
do not change the schema of existing accounts, sessions, or content.
"""
from datetime import datetime

from sqlmodel import Field, SQLModel


class OIDCIdentity(SQLModel, table=True):
    id: str = Field(primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    issuer: str
    subject: str
    provider_name: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class OIDCLoginAttempt(SQLModel, table=True):
    state_hash: str = Field(primary_key=True)
    browser_hash: str
    nonce_hash: str
    encrypted_verifier: str
    issuer: str
    client_id: str
    redirect_uri: str
    expires_at: datetime = Field(index=True)
    phase: str = "pending"
    used_at: datetime | None = None
    handoff_hash: str | None = Field(default=None, unique=True, index=True)
    user_id: str | None = Field(default=None, foreign_key="user.id")
    auth_version: int | None = None
    linking: bool = False
    linking_session_hash: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class OIDCMFAChallenge(SQLModel, table=True):
    """Preserve the first authentication method through native MFA checks."""
    token_hash: str = Field(primary_key=True, foreign_key="mfachallenge.token_hash")
