"""Channel activity with immutable encrypted content and actor-bound choices."""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class ActivityFeedPost(SQLModel, table=True):
    id: str = Field(primary_key=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    created_by: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    revision: int = 0
    archived: bool = False
    poll_json: str | None = None  # Random option identities, deadline and mode only.


class ActivityFeedRevision(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("post_id", "revision"),)
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    post_id: str = Field(foreign_key="activityfeedpost.id", index=True)
    revision: int
    created_by: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    archived: bool = False
    poll_json: str | None = None
    content_mode: str
    encrypted_payload: str | None = None
    matrix_event_id: str | None = None


class ActivityFeedComment(SQLModel, table=True):
    id: str = Field(primary_key=True)
    post_id: str = Field(foreign_key="activityfeedpost.id", index=True)
    created_by: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    revision: int = 0
    archived: bool = False


class ActivityFeedCommentRevision(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("comment_id", "revision"),)
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    comment_id: str = Field(foreign_key="activityfeedcomment.id", index=True)
    revision: int
    created_by: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    archived: bool = False
    content_mode: str
    encrypted_payload: str | None = None
    matrix_event_id: str | None = None


class ActivityFeedChoice(SQLModel, table=True):
    post_id: str = Field(foreign_key="activityfeedpost.id", primary_key=True)
    user_id: str = Field(foreign_key="user.id", primary_key=True)
    kind: str = Field(primary_key=True)  # vote or reaction; no actor supplied by client.
    revision: int = 0
    poll_id: str | None = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ActivityFeedChoiceRevision(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("post_id", "user_id", "kind", "revision"),)
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    post_id: str = Field(foreign_key="activityfeedpost.id", index=True)
    user_id: str = Field(foreign_key="user.id")
    kind: str
    revision: int
    poll_id: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    content_mode: str
    encrypted_payload: str | None = None
    matrix_event_id: str | None = None


class ActivityFeedFollow(SQLModel, table=True):
    channel_id: str = Field(foreign_key="channel.id", primary_key=True)
    user_id: str = Field(foreign_key="user.id", primary_key=True)
    target_id: str = Field(foreign_key="user.id", primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ActivityFeedRead(SQLModel, table=True):
    post_id: str = Field(foreign_key="activityfeedpost.id", primary_key=True)
    user_id: str = Field(foreign_key="user.id", primary_key=True)
    seen_revision: int
    updated_at: datetime = Field(default_factory=datetime.utcnow)
