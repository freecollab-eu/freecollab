"""Short-lived channel meetings. Media never passes through these tables."""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class Meeting(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    host_user_id: str = Field(foreign_key="user.id")
    host_participant_id: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime
    ended_at: datetime | None = None
    signal_sequence: int = 0


class MeetingParticipant(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    meeting_id: str = Field(foreign_key="meeting.id", index=True)
    user_id: str | None = Field(default=None, foreign_key="user.id")
    name: str
    kind: str = "member"
    state: str = "waiting"
    media_ready: bool = False
    credential_hash: str = Field(unique=True, index=True)
    auth_token_hash: str | None = None
    auth_version: int | None = None
    scope_workspace_id: str | None = None
    scope_provider_revision: int | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_seen_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime


class MeetingInvitation(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    meeting_id: str = Field(foreign_key="meeting.id", index=True)
    token_hash: str = Field(unique=True, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime
    used_at: datetime | None = None


class MeetingSignal(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("meeting_id", "sequence"),)
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    meeting_id: str = Field(foreign_key="meeting.id", index=True)
    sequence: int
    sender_id: str = Field(foreign_key="meetingparticipant.id")
    recipient_id: str = Field(foreign_key="meetingparticipant.id", index=True)
    encrypted_payload: str
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
