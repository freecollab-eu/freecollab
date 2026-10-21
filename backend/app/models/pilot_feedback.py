"""Five voluntary self-reports per workspace participant; private notes are sealed."""
from datetime import datetime
from sqlalchemy import CheckConstraint, Index
from sqlmodel import Field, SQLModel


class PilotFeedback(SQLModel, table=True):
    __table_args__ = (
        CheckConstraint("task IN ('first_message','file_share','invite_team','connect_service','find_content')", name="ck_pilotfeedback_task"),
        CheckConstraint("revision >= 1 AND revision <= 1000000", name="ck_pilotfeedback_revision"),
        CheckConstraint("rating IS NULL OR (rating >= 1 AND rating <= 5)", name="ck_pilotfeedback_rating"),
        CheckConstraint("(deleted AND outcome IS NULL AND rating IS NULL AND encrypted_note IS NULL AND created_at IS NULL AND updated_at IS NULL) OR (NOT deleted AND outcome IS NOT NULL AND outcome IN ('completed','blocked') AND created_at IS NOT NULL AND updated_at IS NOT NULL)", name="ck_pilotfeedback_erasure"),
        Index("ix_pilotfeedback_summary", "workspace_id", "deleted", "task"),
    )
    workspace_id: str = Field(primary_key=True, foreign_key="workspace.id")
    user_id: str = Field(primary_key=True, foreign_key="user.id", index=True)
    task: str = Field(primary_key=True, max_length=32)
    revision: int = 1
    deleted: bool = False
    outcome: str | None = None
    rating: int | None = None
    encrypted_note: str | None = None
    created_at: datetime | None = Field(default_factory=datetime.utcnow)
    updated_at: datetime | None = Field(default_factory=datetime.utcnow)
