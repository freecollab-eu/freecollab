"""Consent and publication receipts only; no meeting audio or transcript plaintext."""
from datetime import datetime
from uuid import uuid4
from sqlalchemy import UniqueConstraint
from sqlmodel import SQLModel, Field


class MeetingCapture(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("transport", "meeting_id", "active_key"),)
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    transport: str = Field(index=True)
    meeting_id: str = Field(index=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    created_by: str = Field(foreign_key="user.id")
    generation: int = 1
    revision: int = 1
    state: str = "requesting"
    active_key: str | None = "active"
    roster_digest: str
    video: bool = False
    captions: bool = False
    save_transcript: bool = False
    language: str = "auto"
    recorder_participant_id: str
    content_mode: str = "unknown"
    policy_invalidated: bool = False
    ai_enabled: bool = False
    ai_profile_id: str | None = None
    ai_profile_revision: int | None = None
    ai_recipient: str | None = None
    ai_model: str | None = None
    ai_protocol: str | None = None
    ai_expires_at: datetime | None = None
    ai_max_segments: int = 24
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class MeetingCaptureConsent(SQLModel, table=True):
    capture_id: str = Field(foreign_key="meetingcapture.id", primary_key=True)
    participant_id: str = Field(primary_key=True)
    generation: int
    granted: bool
    user_id: str | None = Field(default=None, foreign_key="user.id")
    revoke_token_hash: str | None = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class MeetingCaptureReceipt(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("capture_id", "generation", "request_id"),)
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    capture_id: str = Field(foreign_key="meetingcapture.id", index=True)
    generation: int
    channel_id: str = Field(foreign_key="channel.id", index=True)
    user_id: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime
    clip_id: str | None = None
    request_id: str | None = None
    erased_at: datetime | None = None


class MeetingCaptureAIUse(SQLModel, table=True):
    capture_id: str = Field(foreign_key="meetingcapture.id", primary_key=True)
    clip_id: str = Field(primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
