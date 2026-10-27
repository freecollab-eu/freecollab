"""Additive document/version metadata; existing File rows remain immutable blobs."""
from datetime import datetime

from sqlalchemy import Index, UniqueConstraint
from sqlmodel import Field, SQLModel


class FileDocument(SQLModel, table=True):
    id: str = Field(primary_key=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    owner_id: str = Field(foreign_key="user.id", index=True)
    current_file_id: str = Field(foreign_key="file.id")
    version_count: int = 1
    created_at: datetime = Field(default_factory=datetime.utcnow)


class FileVersion(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("document_id", "number", name="uq_file_document_version"),)

    file_id: str = Field(primary_key=True, foreign_key="file.id")
    document_id: str = Field(foreign_key="filedocument.id", index=True)
    number: int


class FileUploadReceipt(SQLModel, table=True):
    """A retry references the same acknowledged immutable upload, never a new blob."""
    user_id: str = Field(primary_key=True, foreign_key="user.id")
    request_id: str = Field(primary_key=True)
    file_id: str | None = Field(default=None, foreign_key="file.id", index=True)
    fingerprint: str


class FileErasure(SQLModel, table=True):
    """Committed native blob deletion survives storage outages and restarts."""
    __table_args__ = (Index("ix_file_erasure_scope_created", "storage_scope", "created_at", "file_id"),)

    file_id: str = Field(primary_key=True)
    storage_scope: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
