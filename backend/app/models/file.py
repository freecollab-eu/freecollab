from sqlmodel import SQLModel, Field
from datetime import datetime


class File(SQLModel, table=True):
    id: str = Field(primary_key=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    uploader_id: str = Field(foreign_key="user.id")
    filename: str
    content_type: str
    size: int
    storage_path: str
    encrypted: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
