"""Additive account security state. No credentials or bearer tokens in plaintext."""
from datetime import datetime
from uuid import uuid4

from sqlmodel import SQLModel, Field


class AccountSecurity(SQLModel, table=True):
    user_id: str = Field(primary_key=True, foreign_key="user.id")
    auth_version: int = 0
    email_verified_at: datetime | None = None
    totp_secret: str | None = None
    totp_pending_secret: str | None = None
    totp_pending_expires_at: datetime | None = None
    totp_last_counter: int = -1
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AccountToken(SQLModel, table=True):
    token_hash: str = Field(primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    purpose: str = Field(index=True)
    auth_version: int
    target_email: str
    expires_at: datetime = Field(index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    used_at: datetime | None = None


class MFAChallenge(SQLModel, table=True):
    token_hash: str = Field(primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    auth_version: int
    expires_at: datetime = Field(index=True)
    user_agent: str = "Unknown browser"
    client_address: str = ""
    attempts: int = 0
    used_at: datetime | None = None


class AccountRecoveryCode(SQLModel, table=True):
    code_hash: str = Field(primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    used_at: datetime | None = None


class AccountRateBucket(SQLModel, table=True):
    key: str = Field(primary_key=True)
    started_at: datetime
    attempts: int = 0
    expires_at: datetime = Field(index=True)


class MailOutbox(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    recipient: str
    encrypted_message: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    status: str = Field(default="pending", index=True)
    attempts: int = 0
    next_attempt_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    claimed_at: datetime | None = None
    delivered_at: datetime | None = None
    failure: str | None = None
