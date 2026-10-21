from sqlmodel import SQLModel, Field
from datetime import datetime
from typing import Optional
import uuid


class Notification(SQLModel, table=True):
    """User notification."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    user_id: str = Field(index=True)  # Who receives the notification
    
    # Notification type
    type: str = Field(index=True)  # mention, like, comment, reply, follow, system
    
    # What triggered the notification
    actor_id: Optional[str] = None  # Who caused it (null for system notifications)
    
    # Reference to the related entity
    entity_type: Optional[str] = None  # post, comment, message, channel
    entity_id: Optional[str] = None
    
    # Display content
    title: str
    body: Optional[str] = None
    
    # URL/deep link to navigate to
    action_url: Optional[str] = None
    
    # Status
    read: bool = Field(default=False)
    read_at: Optional[datetime] = None
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)


class NotificationPreference(SQLModel, table=True):
    """User notification preferences."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    user_id: str = Field(index=True, unique=True)
    
    # Notification types enabled
    mentions: bool = Field(default=True)
    likes: bool = Field(default=True)
    comments: bool = Field(default=True)
    replies: bool = Field(default=True)
    follows: bool = Field(default=True)
    system: bool = Field(default=True)
    
    # Delivery methods
    in_app: bool = Field(default=True)
    email: bool = Field(default=False)
    push: bool = Field(default=False)
    
    # Quiet hours (optional)
    quiet_start: Optional[str] = None  # "22:00"
    quiet_end: Optional[str] = None  # "08:00"
