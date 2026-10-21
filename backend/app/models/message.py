from sqlmodel import SQLModel, Field
from datetime import datetime
from typing import Optional


class Message(SQLModel, table=True):
    id: str = Field(primary_key=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    author_id: str = Field(foreign_key="user.id")
    content: str
    encrypted: bool = True
    
    # Threading support
    parent_id: Optional[str] = Field(default=None, foreign_key="message.id", index=True)
    thread_count: int = Field(default=0)  # Number of replies
    
    # Mentions (stored as JSON array of user_ids)
    mentions: Optional[str] = Field(default=None)  # JSON: ["user1", "user2"]
    
    # Edit tracking
    edited_at: Optional[datetime] = Field(default=None)
    deleted: bool = Field(default=False)
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
