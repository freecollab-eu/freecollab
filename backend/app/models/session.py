"""Only token digests are persisted; raw bearer credentials never enter storage."""
from datetime import datetime

from sqlmodel import SQLModel, Field
from uuid import uuid4


class RevokedToken(SQLModel, table=True):
    token_hash: str = Field(primary_key=True)
    expires_at: datetime = Field(index=True)
    revoked_at: datetime = Field(default_factory=datetime.utcnow)


class ActiveSession(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    token_hash: str = Field(unique=True, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime = Field(index=True)
    user_agent: str = "Unknown browser"
    client_address: str = ""
    revoked_at: datetime | None = None
