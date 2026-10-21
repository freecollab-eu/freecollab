from sqlmodel import SQLModel, Field
from datetime import datetime


class User(SQLModel, table=True):
    id: str = Field(primary_key=True)
    name: str
    email: str = Field(unique=True)
    password_hash: str
    public_key: str | None = None
    encrypted_private_key: str | None = None
    role: str = Field(default="user")  # user, moderator, admin (max 2 admins)
    is_blocked: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)