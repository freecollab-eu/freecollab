"""Additive invitation metadata; event details stay in integration revisions."""
from datetime import datetime
from sqlmodel import Field, SQLModel


class CalendarAttendee(SQLModel, table=True):
    item_id: str = Field(primary_key=True, foreign_key="integrationcontent.id")
    user_id: str = Field(primary_key=True, foreign_key="user.id")
    invited_by: str = Field(foreign_key="user.id")
    invited_at: datetime = Field(default_factory=datetime.utcnow)
    response: str = "needs-action"
    responded_at: datetime | None = None
    revision: int
