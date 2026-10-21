from sqlmodel import SQLModel, Field
from datetime import datetime


class Reaction(SQLModel, table=True):
    """Message reactions (emoji)."""
    id: str = Field(primary_key=True)
    message_id: str = Field(foreign_key="message.id", index=True)
    user_id: str = Field(foreign_key="user.id")
    emoji: str  # 👍 ❤️ 😂 😮 😢 😡
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        # Unique constraint: one reaction type per user per message
        table_args = {"sqlite_autoincrement": True}
