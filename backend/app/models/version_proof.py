"""Optional timestamp receipts for exact immutable versions; never document text."""
from datetime import datetime
import uuid

from sqlalchemy import Column, LargeBinary, UniqueConstraint
from sqlmodel import Field, SQLModel


class VersionProof(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("channel_id", "target_kind", "target_id", "sha256"),)
    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    target_kind: str
    target_id: str = Field(index=True)
    sha256: str
    content_binding: str
    requested_by: str = Field(foreign_key="user.id", index=True)
    provider_origin: str
    state: str = Field(default="queued", index=True)
    receipt_id: str | None = None
    provider_timestamp: str | None = None
    attempts: int = 0
    failure_count: int = 0
    failure: str | None = None
    next_attempt_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    claimed_at: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    commitment_checked: bool = False
    bitcoin_attestation_present: bool = False
    bitcoin_verified: bool = False
    proof_data: bytes | None = Field(default=None, sa_column=Column(LargeBinary, nullable=True))
