"""Persistent LiveKit rooms and revocable device leases; never media or provider secrets."""
from datetime import datetime
from uuid import uuid4
from sqlalchemy import Index
from sqlmodel import SQLModel, Field


class SFURoom(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    host_user_id: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime
    ended_at: datetime | None = None
    max_participants: int
    provisioned: bool = False


class SFUParticipant(SQLModel, table=True):
    __table_args__ = (Index('ix_sfu_authorization_queue', 'disconnected', 'authorization_checked_at', 'id'),)
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    meeting_id: str = Field(foreign_key="sfuroom.id", index=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    name: str
    kind: str = "member"
    state: str = "admitted"
    auth_token_hash: str
    auth_version: int
    scope_workspace_id: str | None = None
    scope_provider_revision: int | None = None
    expires_at: datetime
    last_seen_at: datetime = Field(default_factory=datetime.utcnow)
    disconnected: bool = False
    # Independent of browser heartbeats: only the authorization worker advances
    # this persisted fair-queue position, including unsuccessful removal tries.
    authorization_checked_at: datetime = Field(default_factory=datetime.utcnow)


class SFUWorkerLease(SQLModel, table=True):
    id: str = Field(default="livekit", primary_key=True)
    seen_at: datetime = Field(default_factory=datetime.utcnow)
    configuration_hash: str = ""
    scan_budget_exhausted: bool = True
