from sqlmodel import SQLModel, Field
from datetime import datetime


class ReadReceipt(SQLModel, table=True):
    """Tracks who has read messages in a channel."""
    channel_id: str = Field(foreign_key="channel.id", primary_key=True)
    user_id: str = Field(foreign_key="user.id", primary_key=True)
    last_read_message_id: str = Field(foreign_key="message.id")
    read_at: datetime = Field(default_factory=datetime.utcnow)
