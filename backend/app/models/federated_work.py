"""Opaque encrypted-work discovery; content remains exclusively in Matrix."""
from datetime import datetime
from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class FederatedWorkPointer(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("connection_id", "matrix_event_id"),)
    sequence: int | None = Field(default=None, primary_key=True)
    connection_id: str = Field(foreign_key="federatedconnection.id", index=True)
    matrix_event_id: str = Field(max_length=1024)
    matrix_user_id: str = Field(max_length=512)
    published_by: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
