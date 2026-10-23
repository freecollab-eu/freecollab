"""Additive native document organization; content titles remain purpose sealed."""
from datetime import datetime
from uuid import uuid4
from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class SharedDocumentStack(SQLModel, table=True):
    id: str = Field(primary_key=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    created_by: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    version: int = 1
    generation: str = Field(default_factory=lambda: uuid4().hex)
    encrypted_payload: str


class SharedDocumentPlacement(SQLModel, table=True):
    document_id: str = Field(foreign_key="shareddocument.id", primary_key=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    stack_id: str | None = Field(default=None, foreign_key="shareddocumentstack.id", index=True)
    version: int = 1
    updated_by: str = Field(foreign_key="user.id")
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class SharedDocumentRelation(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("first_document_id", "second_document_id"),)
    id: str = Field(primary_key=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    first_document_id: str = Field(foreign_key="shareddocument.id", index=True)
    second_document_id: str = Field(foreign_key="shareddocument.id", index=True)
    created_by: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class SharedDocumentStackReference(SQLModel, table=True):
    """A routing reference only; message text is resolved under current access."""
    __table_args__ = (UniqueConstraint("stack_id", "message_id"),)
    id: str = Field(primary_key=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    stack_id: str = Field(foreign_key="shareddocumentstack.id", index=True)
    message_id: str = Field(foreign_key="message.id", index=True)
    created_by: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
