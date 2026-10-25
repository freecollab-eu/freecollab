"""Customer-granted native Slack bridges; secrets and message copies are sealed."""
from datetime import datetime
from uuid import uuid4
from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class SlackBridge(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("workspace_id", "channel_id", "slack_team_id", "slack_channel_id"),)
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    granted_by: str = Field(foreign_key="user.id")
    slack_team_id: str
    slack_channel_id: str
    slack_bot_user_id: str
    generation: int = 1
    encrypted_config: str
    inbound: bool = True
    outbound: bool = False
    enabled: bool = True
    outbound_cursor: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_status: str | None = None
    last_scanned_at: datetime = Field(default_factory=lambda: datetime(1970, 1, 1), index=True)
    revoked_at: datetime | None = None


class SlackBridgeJob(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("bridge_id", "event_key"),)
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    bridge_id: str = Field(foreign_key="slackbridge.id", index=True)
    generation: int
    direction: str
    event_key: str
    body_digest: str
    encrypted_payload: str
    status: str = Field(default="pending", index=True)
    attempts: int = 0
    claim_token: str | None = None
    claimed_at: datetime | None = None
    next_attempt_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    message_id: str | None = None
    failure: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: datetime | None = None
    notifications_delivered: bool = False


class SlackBridgeMessage(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("bridge_id", "message_id"),)
    bridge_id: str = Field(primary_key=True, foreign_key="slackbridge.id")
    slack_ts: str = Field(primary_key=True)
    message_id: str | None = Field(default=None, foreign_key="message.id", index=True)
    origin: str
    latest_event_ts: str
    erased_at: datetime | None = None
