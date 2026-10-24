"""References attach real work to a source message without copying its state."""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlmodel import Field, SQLModel


class ConversationWorkReference(SQLModel, table=True):
    __table_args__ = (
        CheckConstraint("(resource_kind = 'document' AND document_id IS NOT NULL AND task_id IS NULL AND decision_id IS NULL) OR "
                        "(resource_kind = 'task' AND task_id IS NOT NULL AND document_id IS NULL AND decision_id IS NULL) OR "
                        "(resource_kind = 'decision' AND decision_id IS NOT NULL AND document_id IS NULL AND task_id IS NULL)",
                        name="conversation_work_one_target"),
        UniqueConstraint("source_message_id", "document_id"),
        UniqueConstraint("source_message_id", "task_id"),
        UniqueConstraint("source_message_id", "decision_id"),
    )
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    source_message_id: str = Field(foreign_key="message.id", index=True)
    resource_kind: str
    document_id: str | None = Field(default=None, foreign_key="shareddocument.id", index=True)
    task_id: str | None = Field(default=None, foreign_key="taskcard.id", index=True)
    decision_id: str | None = Field(default=None, foreign_key="decision.id", index=True)
    created_by: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
