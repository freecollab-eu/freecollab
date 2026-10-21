"""Immutable community packages; installation remains an explicit local grant."""
from datetime import datetime
from uuid import uuid4
from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class IntegrationPublisher(SQLModel, table=True):
    app_id: str = Field(primary_key=True)
    publisher_id: str = Field(foreign_key="user.id")


class IntegrationRelease(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("app_id", "version"),)
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    app_id: str = Field(index=True)
    version: str
    publisher_id: str = Field(foreign_key="user.id")
    package_json: str
    package_digest: str
    status: str = "pending"
    reviewer_id: str | None = Field(default=None, foreign_key="user.id")
    reviewed_at: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class LibraryInstallation(SQLModel, table=True):
    installation_id: str = Field(primary_key=True, foreign_key="appinstallation.id")
    release_id: str
    package_digest: str
