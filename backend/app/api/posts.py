from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from ..database import get_session
from ..models.post import Post, PostLike
from ..models.channel import Channel, ChannelMember
from ..services.access import require_channel_member, lock_channel_writes
from ..services.chat_notifications import require_native_channel
from ..services.workspaces import require_direct_message_writable
from ..models.file import File
from ..services.auth import get_current_user
from ..services.request_guard import recheck_actor
from ..services.enterprise_governance import assert_deletion_allowed, enforce_native_content
from ..realtime.socket import broadcast_to_channel
import json

router = APIRouter(prefix="/posts", tags=["posts"])


def require_channel_access(session, user, flow_id: str, flow_type: str = "channel", *, write: bool = False):
    """Only channel-backed flows currently have an authorization model."""
    if flow_type != "channel":
        raise HTTPException(400, "Only channel flows are supported")
    actor_id = user.id
    if write:
        lock_channel_writes(session, flow_id)
        session.expire_all()
        recheck_actor(session, actor_id)
    member = require_channel_member(session, flow_id, actor_id)
    require_native_channel(session, flow_id)
    if write:
        require_direct_message_writable(session, flow_id, actor_id)
    return member


class CreatePost(BaseModel):
    content: str
    content_type: str = "text"  # text, markdown
    attachments: Optional[List[str]] = None  # List of file IDs


class UpdatePost(BaseModel):
    content: str


@router.post("/flows/{flow_id}")
async def create_post(
    flow_id: str,
    data: CreatePost,
    flow_type: str = "channel",
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    """Create a new post in a flow."""
    require_channel_access(session, user, flow_id, flow_type, write=True)

    for file_id in data.attachments or []:
        attachment = session.get(File, file_id)
        if not attachment or attachment.channel_id != flow_id:
            raise HTTPException(400, "Attachment is not in this channel")

    warnings = enforce_native_content(session, flow_id, user.id, data.content)

    post = Post(
        author_id=user.id,
        content=data.content,
        content_type=data.content_type,
        attachments=json.dumps(data.attachments) if data.attachments else None,
        flow_id=flow_id,
        flow_type=flow_type,
    )
    session.add(post)
    session.commit()
    session.refresh(post)

    post_data = {
        "id": post.id,
        "author_id": post.author_id,
        "content": post.content,
        "content_type": post.content_type,
        "attachments": json.loads(post.attachments) if post.attachments else [],
        "flow_id": post.flow_id,
        "like_count": 0,
        "comment_count": 0,
        "created_at": post.created_at.isoformat(),
        "user_liked": False,
        "policy_warnings": warnings,
    }

    # Broadcast to flow
    await broadcast_to_channel(flow_id, "new_post", post_data)

    return post_data


@router.get("/flows/{flow_id}")
def get_flow(
    flow_id: str,
    limit: int = Query(20, ge=1, le=100),
    before: Optional[str] = None,
    flow_type: str = "channel",
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    """Get posts from a flow."""
    require_channel_access(session, user, flow_id, flow_type)

    query = (
        session.query(Post)
        .filter(
            Post.flow_id == flow_id,
            Post.flow_type == flow_type,
            Post.deleted == False,
        )
        .order_by(Post.created_at.desc())
    )

    if before:
        before_post = session.get(Post, before)
        if not before_post or before_post.flow_id != flow_id or before_post.flow_type != flow_type:
            raise HTTPException(400, "Invalid pagination cursor")
        query = query.filter(Post.created_at < before_post.created_at)

    posts = query.limit(limit).all()

    # Check which posts the user has liked
    post_ids = [p.id for p in posts]
    user_likes = (
        session.query(PostLike.post_id)
        .filter(PostLike.post_id.in_(post_ids), PostLike.user_id == user.id)
        .all()
    )
    liked_post_ids = {l.post_id for l in user_likes}

    return [
        {
            "id": p.id,
            "author_id": p.author_id,
            "content": p.content,
            "content_type": p.content_type,
            "attachments": json.loads(p.attachments) if p.attachments else [],
            "flow_id": p.flow_id,
            "like_count": p.like_count,
            "comment_count": p.comment_count,
            "created_at": p.created_at.isoformat(),
            "updated_at": p.updated_at.isoformat() if p.updated_at else None,
            "user_liked": p.id in liked_post_ids,
        }
        for p in posts
    ]


@router.get("/{post_id}")
def get_post(
    post_id: str,
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    """Get a single post."""
    post = session.get(Post, post_id)
    if not post or post.deleted:
        raise HTTPException(404, "Post not found")
    require_channel_access(session, user, post.flow_id, post.flow_type)

    # Check if user liked
    user_liked = (
        session.query(PostLike)
        .filter(PostLike.post_id == post_id, PostLike.user_id == user.id)
        .first()
    ) is not None

    return {
        "id": post.id,
        "author_id": post.author_id,
        "content": post.content,
        "content_type": post.content_type,
        "attachments": json.loads(post.attachments) if post.attachments else [],
        "flow_id": post.flow_id,
        "like_count": post.like_count,
        "comment_count": post.comment_count,
        "created_at": post.created_at.isoformat(),
        "updated_at": post.updated_at.isoformat() if post.updated_at else None,
        "user_liked": user_liked,
    }


@router.put("/{post_id}")
async def update_post(
    post_id: str,
    data: UpdatePost,
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    """Update a post (only author can update)."""
    post = session.get(Post, post_id)
    if not post or post.deleted:
        raise HTTPException(404, "Post not found")
    require_channel_access(session, user, post.flow_id, post.flow_type, write=True)
    session.refresh(post)
    if post.deleted:
        raise HTTPException(404, "Post not found")

    if post.author_id != user.id:
        raise HTTPException(403, "Can only edit your own posts")

    assert_deletion_allowed(session, post.flow_id, author_id=post.author_id)
    warnings = enforce_native_content(session, post.flow_id, user.id, data.content)
    post.content = data.content
    post.updated_at = datetime.utcnow()
    session.commit()

    await broadcast_to_channel(
        post.flow_id,
        "post_updated",
        {"id": post.id, "content": post.content, "updated_at": post.updated_at.isoformat()},
    )

    return {"updated": True, "id": post_id, "policy_warnings": warnings}


@router.delete("/{post_id}")
async def delete_post(
    post_id: str,
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    """Delete a post (soft delete, only author can delete)."""
    post = session.get(Post, post_id)
    if not post or post.deleted:
        raise HTTPException(404, "Post not found")
    require_channel_access(session, user, post.flow_id, post.flow_type, write=True)
    session.refresh(post)
    if post.deleted:
        raise HTTPException(404, "Post not found")

    if post.author_id != user.id:
        raise HTTPException(403, "Can only delete your own posts")

    assert_deletion_allowed(session, post.flow_id, author_id=post.author_id)
    post.deleted = True
    session.commit()

    await broadcast_to_channel(post.flow_id, "post_deleted", {"id": post.id})

    return {"deleted": True, "id": post_id}


@router.post("/{post_id}/like")
async def like_post(
    post_id: str,
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    """Like a post."""
    post = session.get(Post, post_id)
    if not post or post.deleted:
        raise HTTPException(404, "Post not found")
    require_channel_access(session, user, post.flow_id, post.flow_type, write=True)
    session.refresh(post)
    if post.deleted:
        raise HTTPException(404, "Post not found")

    # Check if already liked
    existing = (
        session.query(PostLike)
        .filter(PostLike.post_id == post_id, PostLike.user_id == user.id)
        .first()
    )
    if existing:
        raise HTTPException(400, "Already liked")

    like = PostLike(post_id=post_id, user_id=user.id)
    session.add(like)
    post.like_count += 1
    session.commit()

    await broadcast_to_channel(
        post.flow_id,
        "post_liked",
        {"post_id": post_id, "user_id": user.id, "like_count": post.like_count},
    )

    return {"liked": True, "like_count": post.like_count}


@router.delete("/{post_id}/like")
async def unlike_post(
    post_id: str,
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    """Unlike a post."""
    post = session.get(Post, post_id)
    if not post or post.deleted:
        raise HTTPException(404, "Post not found")
    require_channel_access(session, user, post.flow_id, post.flow_type, write=True)
    session.refresh(post)
    if post.deleted:
        raise HTTPException(404, "Post not found")

    like = (
        session.query(PostLike)
        .filter(PostLike.post_id == post_id, PostLike.user_id == user.id)
        .first()
    )
    if not like:
        raise HTTPException(400, "Not liked")

    session.delete(like)
    post.like_count = max(0, post.like_count - 1)
    session.commit()

    await broadcast_to_channel(
        post.flow_id,
        "post_unliked",
        {"post_id": post_id, "user_id": user.id, "like_count": post.like_count},
    )

    return {"unliked": True, "like_count": post.like_count}
