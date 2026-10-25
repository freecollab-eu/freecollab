"""Additive private AI execution state; content and credentials are sealed."""
from datetime import datetime
from uuid import uuid4
from sqlalchemy import Index, UniqueConstraint
from sqlmodel import Field, SQLModel


class AIModelProfile(SQLModel, table=True):
    workspace_id: str = Field(primary_key=True, foreign_key="workspace.id")
    profile_id: str = Field(primary_key=True)
    revision: int = 1
    enabled: bool = True
    encrypted_config: str
    updated_by: str | None = Field(default=None, foreign_key="user.id")
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class AIProfileConsent(SQLModel, table=True):
    workspace_id: str = Field(primary_key=True, foreign_key="workspace.id")
    profile_id: str = Field(primary_key=True)
    user_id: str = Field(primary_key=True, foreign_key="user.id")
    revision: int
    enabled: bool = False


class AIPersonalKey(SQLModel, table=True):
    workspace_id: str = Field(primary_key=True, foreign_key="workspace.id")
    profile_id: str = Field(primary_key=True)
    user_id: str = Field(primary_key=True, foreign_key="user.id")
    profile_revision: int
    encrypted_key: str
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class AIBudget(SQLModel, table=True):
    workspace_id: str = Field(primary_key=True, foreign_key="workspace.id")
    revision: int = 1
    max_tokens: int = 200000
    # Zero disables the local monetary cap. Prices are administrator estimates,
    # not assertions about a provider invoice or billing exactly-once behavior.
    max_cost_micros: int = 0
    default_input_cost_per_million_micros: int = 0
    default_output_cost_per_million_micros: int = 0
    updated_by: str | None = Field(default=None, foreign_key="user.id")


class AIRun(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", "request_id"),)
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    request_id: str
    request_digest: str
    profile_id: str
    profile_revision: int
    operation: str
    status: str = "queued"
    revision: int = 1
    generation: int = 1
    max_steps: int = 1
    max_tokens: int = 2048
    encrypted_payload: str
    encrypted_authority: str
    encrypted_result: str | None = None
    error_code: str | None = None
    claim_token: str | None = None
    claim_until: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class AIRunStep(SQLModel, table=True):
    run_id: str = Field(primary_key=True, foreign_key="airun.id")
    index: int = Field(primary_key=True)
    status: str = "queued"
    attempts: int = 0
    encrypted_result: str | None = None
    usage_json: str | None = None
    error_code: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class AIRunSource(SQLModel, table=True):
    run_id: str = Field(primary_key=True, foreign_key="airun.id")
    index: int = Field(primary_key=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    kind: str
    source_id: str


class AIUsage(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("execution_id", "step_index", "attempt"), Index("ix_aiusage_workspace_period", "workspace_id", "created_at"))
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    workspace_id: str | None = Field(default=None, foreign_key="workspace.id", index=True)
    user_id: str | None = Field(default=None, foreign_key="user.id", index=True)
    # Also covers ephemeral foreground requests without persisting their text.
    execution_id: str = Field(index=True)
    step_index: int = 0
    attempt: int = 1
    profile_id: str
    profile_revision: int
    status: str = "reserved"
    reserved_tokens: int
    reserved_cost_micros: int
    input_tokens: int = 0
    output_tokens: int = 0
    charged_tokens: int = 0
    estimated_cost_micros: int = 0
    usage_reported: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class AIRunApproval(SQLModel, table=True):
    run_id: str = Field(primary_key=True, foreign_key="airun.id")
    proposal_id: str = Field(primary_key=True)
    user_id: str = Field(foreign_key="user.id")
    resource_id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AIReadTool(SQLModel, table=True):
    workspace_id: str = Field(primary_key=True, foreign_key="workspace.id")
    tool_id: str = Field(primary_key=True)
    revision: int = 1
    enabled: bool = True
    encrypted_config: str
    updated_by: str | None = Field(default=None, foreign_key="user.id")
    updated_at: datetime = Field(default_factory=datetime.utcnow)
