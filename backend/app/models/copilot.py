"""Private opt-in AI conversations; provider material is encrypted at rest."""
from datetime import datetime
from uuid import uuid4
from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

class CopilotConfig(SQLModel, table=True):
    workspace_id: str = Field(primary_key=True, foreign_key='workspace.id')
    revision: int = 1
    enabled: bool = False
    encrypted_config: str
    updated_by: str = Field(foreign_key='user.id')
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class CopilotConsent(SQLModel, table=True):
    workspace_id: str = Field(primary_key=True, foreign_key='workspace.id')
    user_id: str = Field(primary_key=True, foreign_key='user.id')
    config_revision: int
    enabled: bool = False
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class CopilotConversation(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    workspace_id: str = Field(foreign_key='workspace.id', index=True)
    channel_id: str = Field(foreign_key='channel.id', index=True)
    user_id: str = Field(foreign_key='user.id', index=True)
    secure: bool
    revision: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)

class CopilotTurn(SQLModel, table=True):
    __table_args__ = (UniqueConstraint('conversation_id', 'request_id'),)
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    conversation_id: str = Field(foreign_key='copilotconversation.id', index=True)
    request_id: str
    request_digest: str
    config_revision: int
    status: str = 'processing'
    encrypted_result: str | None = None
    # Secure turns retain opaque refs/metadata only; never relay plaintext.
    encrypted_sources: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None

class CopilotToolApproval(SQLModel, table=True):
    turn_id: str = Field(primary_key=True, foreign_key='copilotturn.id')
    proposal_id: str = Field(primary_key=True)
    user_id: str = Field(foreign_key='user.id')
    resource_id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)

class CopilotActivity(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    workspace_id: str = Field(foreign_key='workspace.id', index=True)
    user_id: str = Field(foreign_key='user.id')
    kind: str
    source_count: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
