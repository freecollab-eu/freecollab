"""Private account photos, independent of channel files and additive to User."""
from datetime import datetime

from sqlmodel import Field, SQLModel


class UserAvatar(SQLModel, table=True):
    user_id: str = Field(primary_key=True, foreign_key="user.id")
    blob_id: str = Field(unique=True)
    size: int
    width: int
    height: int
    sha256: str
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class AvatarErasure(SQLModel, table=True):
    """Retry private-byte erasure after committed replacement/account closure."""
    blob_id: str = Field(primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
