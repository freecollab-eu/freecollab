from sqlmodel import SQLModel, Field
from datetime import datetime
from typing import Optional
import uuid


class Post(SQLModel, table=True):
    """A post in a flow/activity stream."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    author_id: str = Field(index=True)
    
    # Content
    content: str  # Main text content (can be markdown)
    content_type: str = Field(default="text")  # text, markdown, html
    
    # Optional media attachments (JSON array of file IDs)
    attachments: Optional[str] = None  # JSON: ["file_id_1", "file_id_2"]
    
    # Context - what this post belongs to
    flow_id: str = Field(index=True)  # Could be channel_id, project_id, etc.
    flow_type: str = Field(default="channel")  # channel, project, user, global
    
    # Engagement metrics (denormalized for performance)
    like_count: int = Field(default=0)
    comment_count: int = Field(default=0)
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None
    
    # Soft delete
    deleted: bool = Field(default=False)


class PostLike(SQLModel, table=True):
    """A like on a post."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    post_id: str = Field(index=True)
    user_id: str = Field(index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Comment(SQLModel, table=True):
    """A comment on a post or other entity."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    author_id: str = Field(index=True)
    
    # What this comment is on
    entity_type: str = Field(index=True)  # post, file, comment (for replies)
    entity_id: str = Field(index=True)
    
    # Parent comment for nested replies
    parent_id: Optional[str] = Field(default=None, index=True)
    
    # Content
    content: str
    
    # Engagement
    like_count: int = Field(default=0)
    reply_count: int = Field(default=0)
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    edited_at: Optional[datetime] = None
    
    # Soft delete
    deleted: bool = Field(default=False)


class CommentLike(SQLModel, table=True):
    """A like on a comment."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    comment_id: str = Field(index=True)
    user_id: str = Field(index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
