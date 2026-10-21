"""Append-only collaborative document updates; content lives in encrypted payloads."""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class SharedDocument(SQLModel, table=True):
    id: str = Field(primary_key=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    created_by: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    archived: bool = False
    version: int = 1
    update_count: int = 0
    stored_bytes: int = 0
    summary_encrypted_payload: str | None = None
    summary_event_id: str | None = None
    summary_update_id: str | None = None
    summary_by: str | None = Field(default=None, foreign_key="user.id")


class SharedDocumentUpdate(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("document_id", "sequence"),)
    document_id: str = Field(foreign_key="shareddocument.id", primary_key=True)
    update_id: str = Field(primary_key=True)
    sequence: int = Field(index=True)
    created_by: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    content_mode: str
    payload_hash: str
    encrypted_payload: str | None = None
    matrix_event_id: str | None = None
    byte_size: int = 0


class SharedDocumentSnapshot(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    document_id: str = Field(foreign_key="shareddocument.id", index=True)
    covers_sequence: int
    created_by: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    content_mode: str
    encrypted_payload: str | None = None
    matrix_event_id: str | None = None


class SharedDocumentComment(SQLModel, table=True):
    id: str = Field(primary_key=True)
    document_id: str = Field(foreign_key="shareddocument.id", index=True)
    created_by: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    content_mode: str
    payload_hash: str
    encrypted_payload: str | None = None
    matrix_event_id: str | None = None
    resolved: bool = False
    version: int = 1
