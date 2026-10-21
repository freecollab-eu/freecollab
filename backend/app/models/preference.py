"""Account-owned appearance settings, separate from the existing user table."""
from datetime import datetime

from sqlmodel import Field, SQLModel


class UserAppearance(SQLModel, table=True):
    user_id: str = Field(primary_key=True, foreign_key="user.id")
    values: str
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class UserLanguage(SQLModel, table=True):
    """Personal UI language, independent of workspace and identity provider."""
    user_id: str = Field(primary_key=True, foreign_key="user.id")
    locale: str = "auto"
    updated_at: datetime = Field(default_factory=datetime.utcnow)
