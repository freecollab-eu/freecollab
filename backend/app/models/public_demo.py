"""Expiry and ownership for a disposable sample-data installation only."""
from datetime import datetime
from sqlmodel import Field, SQLModel


class PublicDemoVisit(SQLModel, table=True):
    id: str = Field(primary_key=True)
    user_id: str = Field(foreign_key="user.id", unique=True, index=True)
    workspace_id: str = Field(foreign_key="workspace.id", unique=True, index=True)
    channel_id: str = Field(foreign_key="channel.id")
    participant_ids_json: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime = Field(index=True)
    revoked_at: datetime | None = None
    mutation_count: int = 0
    mutation_bytes: int = 0
