from sqlmodel import SQLModel, Field
from datetime import datetime


class Channel(SQLModel, table=True):
    id: str = Field(primary_key=True)
    name: str | None = None
    type: str = "group"  # direct, group, public
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ChannelMember(SQLModel, table=True):
    channel_id: str = Field(foreign_key="channel.id", primary_key=True)
    user_id: str = Field(foreign_key="user.id", primary_key=True)
    role: str = "member"  # admin, member
    encrypted_channel_key: str | None = None  # Channel key encrypted for this user
    joined_at: datetime = Field(default_factory=datetime.utcnow)


class ChannelInvitation(SQLModel, table=True):
    """Single-use invitations. The raw bearer token is never persisted."""
    id: str = Field(primary_key=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    token_hash: str = Field(unique=True, index=True)
    created_by: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime
    accepted_at: datetime | None = None
    accepted_by: str | None = Field(default=None, foreign_key="user.id")
    revoked_at: datetime | None = None
