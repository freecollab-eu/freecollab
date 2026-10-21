"""Native-password step-up for the shared account's encryption identity only."""
from datetime import datetime
from sqlmodel import Field, SQLModel


class WorkspaceEncryptionUnlock(SQLModel, table=True):
    token_hash: str = Field(primary_key=True, foreign_key="activesession.token_hash")
    workspace_id: str = Field(foreign_key="workspace.id")
    provider_revision: int
    auth_version: int
    expires_at: datetime
