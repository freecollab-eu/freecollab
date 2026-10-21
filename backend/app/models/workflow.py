"""Bounded workflow configuration, durable runs and immutable approval metadata."""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class WorkflowDefinition(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    created_by: str = Field(foreign_key="user.id")
    granted_by: str = Field(foreign_key="user.id")
    current_revision: int = 1
    trigger: str = Field(index=True)
    enabled: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class WorkflowRevision(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("workflow_id", "revision"),)
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    workflow_id: str = Field(foreign_key="workflowdefinition.id", index=True)
    revision: int
    encrypted_definition: str
    created_by: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class WorkflowRun(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("workflow_id", "event_id"),)
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    workflow_id: str = Field(foreign_key="workflowdefinition.id", index=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    workflow_revision: int
    event_id: str
    event_kind: str
    resource_id: str | None = None
    actor_id: str | None = None
    status: str = Field(default="queued", index=True)
    step_index: int = 0
    due_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    deadline_at: datetime | None = None
    next_reminder_at: datetime | None = None
    lease_expires_at: datetime | None = None
    attempts: int = 0
    reminder_count: int = 0
    escalated: bool = False
    failure: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: datetime | None = None


class WorkflowVote(SQLModel, table=True):
    run_id: str = Field(foreign_key="workflowrun.id", primary_key=True)
    step_index: int = Field(primary_key=True)
    user_id: str = Field(foreign_key="user.id", primary_key=True)
    choice: str
    matrix_event_id: str | None = None
    encrypted_note: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class WorkflowActivity(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    run_id: str = Field(foreign_key="workflowrun.id", index=True)
    kind: str
    actor_id: str | None = None
    step_index: int
    created_at: datetime = Field(default_factory=datetime.utcnow)


class WorkflowNotice(SQLModel, table=True):
    run_id: str = Field(foreign_key="workflowrun.id", primary_key=True)
    step_index: int = Field(primary_key=True)
    user_id: str = Field(foreign_key="user.id", primary_key=True)
    kind: str = Field(primary_key=True)
    ordinal: int = Field(default=0, primary_key=True)
    notification_id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
