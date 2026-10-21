from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from ..database import get_session
from ..models.post import Post, Comment, CommentLike
from ..models.file import File
from .posts import require_channel_access
from ..services.auth import get_current_user
from ..realtime.socket import broadcast_to_channel
from .notifications import create_notification

router = APIRouter(prefix="/comments", tags=["comments"])


def recheck_entities(session, ancestry):
    """A root or parent may have been deleted while its write fence was pending."""
    for model, entity_id in ancestry:
        entity = session.get(model, entity_id, populate_existing=True)
        if not entity or getattr(entity, "deleted", False):
            raise HTTPException(404, "Comment entity not found")


def resolve_entity(session, user, entity_type: str, entity_id: str, *, write: bool = False):
    """Resolve an entity to its authorized channel, rejecting corrupt ancestry."""
    seen = set()
    ancestry = []
    entity_author_id = None
    for _ in range(20):
        key = (entity_type, entity_id)
        if key in seen:
            raise HTTPException(400, "Invalid comment ancestry")
        seen.add(key)
        if entity_type == "post":
            entity = session.get(Post, entity_id)
            if not entity or entity.deleted:
                raise HTTPException(404, "Post not found")
            ancestry.append((Post, entity_id))
            require_channel_access(session, user, entity.flow_id, entity.flow_type, write=write)
            if write:
                recheck_entities(session, ancestry)
            return entity.flow_id, entity_author_id or entity.author_id, key, entity
        if entity_type == "file":
            entity = session.get(File, entity_id)
            if not entity:
                raise HTTPException(404, "File not found")
            ancestry.append((File, entity_id))
            require_channel_access(session, user, entity.channel_id, write=write)
            if write:
                recheck_entities(session, ancestry)
            return entity.channel_id, entity_author_id or entity.uploader_id, key, entity
        if entity_type != "comment":
            raise HTTPException(400, "Unsupported comment entity type")
        entity = session.get(Comment, entity_id)
        if not entity or entity.deleted:
            raise HTTPException(404, "Comment not found")
        ancestry.append((Comment, entity_id))
        if entity_author_id is None:
            entity_author_id = entity.author_id
        entity_type, entity_id = entity.entity_type, entity.entity_id
    raise HTTPException(400, "Comment ancestry is too deep")


def require_comment_access(session, user, comment, *, write: bool = False):
    return resolve_entity(session, user, comment.entity_type, comment.entity_id, write=write)


class CreateComment(BaseModel):
    content: str
    parent_id: Optional[str] = None  # For nested replies


class UpdateComment(BaseModel):
    content: str


@router.get("/{comment_id}/replies")
def get_replies(
    comment_id: str,
    limit: int = Query(50, ge=1, le=100),
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    """Get replies to a comment."""
    parent = session.get(Comment, comment_id)
    if not parent or parent.deleted:
        raise HTTPException(404, "Comment not found")
    _, _, parent_root_key, _ = require_comment_access(session, user, parent)
    replies = (
        session.query(Comment)
        .filter(
            Comment.parent_id == comment_id,
            Comment.deleted == False,
        )
        .order_by(Comment.created_at.asc())
        .limit(limit)
        .all()
    )

    # Legacy records could reference a parent in another channel/entity.
    # Authorize each reply before exposing its content.
    authorized_replies = []
    for reply in replies:
        try:
            _, _, reply_root_key, _ = require_comment_access(session, user, reply)
        except HTTPException:
            continue
        if reply_root_key == parent_root_key:
            authorized_replies.append(reply)
    replies = authorized_replies

    # Get user's likes
    reply_ids = [r.id for r in replies]
    user_likes = (
        session.query(CommentLike.comment_id)
        .filter(CommentLike.comment_id.in_(reply_ids), CommentLike.user_id == user.id)
        .all()
    )
    liked_ids = {l.comment_id for l in user_likes}

    return [
        {
            "id": r.id,
            "author_id": r.author_id,
            "parent_id": r.parent_id,
            "content": r.content,
            "like_count": r.like_count,
            "created_at": r.created_at.isoformat(),
            "user_liked": r.id in liked_ids,
        }
        for r in replies
    ]


@router.get("/{entity_type}/{entity_id}")
def get_comments(
    entity_type: str,
    entity_id: str,
    limit: int = Query(50, ge=1, le=100),
    before: Optional[str] = None,
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    """Get comments for an entity."""
    resolve_entity(session, user, entity_type, entity_id)
    if entity_type == "comment":
        return get_replies(entity_id, limit, user, session)
    query = (
        session.query(Comment)
        .filter(
            Comment.entity_type == entity_type,
            Comment.entity_id == entity_id,
            Comment.deleted == False,
            Comment.parent_id == None,  # Only top-level comments
        )
        .order_by(Comment.created_at.asc())
    )

    if before:
        before_comment = session.get(Comment, before)
        if (not before_comment or before_comment.entity_type != entity_type
                or before_comment.entity_id != entity_id):
            raise HTTPException(400, "Invalid pagination cursor")
        query = query.filter(Comment.created_at < before_comment.created_at)

    comments = query.limit(limit).all()

    # Get user's likes
    comment_ids = [c.id for c in comments]
    user_likes = (
        session.query(CommentLike.comment_id)
        .filter(CommentLike.comment_id.in_(comment_ids), CommentLike.user_id == user.id)
        .all()
    )
    liked_ids = {l.comment_id for l in user_likes}

    return [
        {
            "id": c.id,
            "author_id": c.author_id,
            "entity_type": c.entity_type,
            "entity_id": c.entity_id,
            "parent_id": c.parent_id,
            "content": c.content,
            "like_count": c.like_count,
            "reply_count": c.reply_count,
            "created_at": c.created_at.isoformat(),
            "edited_at": c.edited_at.isoformat() if c.edited_at else None,
            "user_liked": c.id in liked_ids,
        }
        for c in comments
    ]


@router.put("/{comment_id}")
async def update_comment(
    comment_id: str,
    data: UpdateComment,
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    """Update a comment (only author can update)."""
    comment = session.get(Comment, comment_id)
    if not comment or comment.deleted:
        raise HTTPException(404, "Comment not found")
    require_comment_access(session, user, comment, write=True)
    session.refresh(comment)
    if comment.deleted:
        raise HTTPException(404, "Comment not found")

    if comment.author_id != user.id:
        raise HTTPException(403, "Can only edit your own comments")

    comment.content = data.content
    comment.edited_at = datetime.utcnow()
    session.commit()

    return {
        "id": comment.id,
        "content": comment.content,
        "edited_at": comment.edited_at.isoformat(),
    }


@router.delete("/{comment_id}")
async def delete_comment(
    comment_id: str,
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    """Delete a comment (soft delete)."""
    comment = session.get(Comment, comment_id)
    if not comment or comment.deleted:
        raise HTTPException(404, "Comment not found")
    require_comment_access(session, user, comment, write=True)
    session.refresh(comment)
    if comment.deleted:
        raise HTTPException(404, "Comment not found")

    if comment.author_id != user.id:
        raise HTTPException(403, "Can only delete your own comments")

    comment.deleted = True
    comment.content = "[Comment deleted]"

    # Update parent's reply count
    if comment.parent_id:
        parent = session.get(Comment, comment.parent_id)
        if parent:
            parent.reply_count = max(0, parent.reply_count - 1)

    # All replies resolve to the same authorized root entity.
    _, _, _, root = require_comment_access(session, user, comment)
    if isinstance(root, Post):
        root.comment_count = max(0, root.comment_count - 1)

    session.commit()

    return {"deleted": True, "id": comment_id}


@router.post("/{comment_id}/like")
async def like_comment(
    comment_id: str,
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    """Like a comment."""
    comment = session.get(Comment, comment_id)
    if not comment or comment.deleted:
        raise HTTPException(404, "Comment not found")
    require_comment_access(session, user, comment, write=True)
    session.refresh(comment)
    if comment.deleted:
        raise HTTPException(404, "Comment not found")

    existing = (
        session.query(CommentLike)
        .filter(CommentLike.comment_id == comment_id, CommentLike.user_id == user.id)
        .first()
    )
    if existing:
        raise HTTPException(400, "Already liked")

    like = CommentLike(comment_id=comment_id, user_id=user.id)
    session.add(like)
    comment.like_count += 1
    session.commit()

    return {"liked": True, "like_count": comment.like_count}


@router.delete("/{comment_id}/like")
async def unlike_comment(
    comment_id: str,
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    """Unlike a comment."""
    comment = session.get(Comment, comment_id)
    if not comment or comment.deleted:
        raise HTTPException(404, "Comment not found")
    require_comment_access(session, user, comment, write=True)
    session.refresh(comment)
    if comment.deleted:
        raise HTTPException(404, "Comment not found")

    like = (
        session.query(CommentLike)
        .filter(CommentLike.comment_id == comment_id, CommentLike.user_id == user.id)
        .first()
    )
    if not like:
        raise HTTPException(400, "Not liked")

    session.delete(like)
    comment.like_count = max(0, comment.like_count - 1)
    session.commit()

    return {"unliked": True, "like_count": comment.like_count}


@router.post("/{entity_type}/{entity_id}")
async def create_comment(
    entity_type: str,
    entity_id: str,
    data: CreateComment,
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    """Create a comment on a post, file, or other entity."""
    channel_id, entity_author_id, root_key, root = resolve_entity(
        session, user, entity_type, entity_id, write=True
    )
    parent_id = data.parent_id
    if entity_type == "comment":
        if parent_id and parent_id != entity_id:
            raise HTTPException(400, "Parent must match the target comment")
        parent_id = entity_id
    if parent_id:
        parent = session.get(Comment, parent_id)
        if not parent or parent.deleted:
            raise HTTPException(404, "Parent comment not found")
        _, _, parent_root_key, _ = require_comment_access(session, user, parent)
        if parent_root_key != root_key:
            raise HTTPException(400, "Parent comment belongs to a different entity")
        parent.reply_count += 1
    if isinstance(root, Post):
        root.comment_count += 1

    comment = Comment(
        author_id=user.id,
        entity_type=entity_type,
        entity_id=entity_id,
        parent_id=parent_id,
        content=data.content,
    )
    session.add(comment)
    session.commit()
    session.refresh(comment)

    comment_data = {
        "id": comment.id,
        "author_id": comment.author_id,
        "entity_type": comment.entity_type,
        "entity_id": comment.entity_id,
        "parent_id": comment.parent_id,
        "content": comment.content,
        "like_count": 0,
        "reply_count": 0,
        "created_at": comment.created_at.isoformat(),
        "user_liked": False,
    }

    # Broadcast new comment
    await broadcast_to_channel(channel_id, "new_comment", comment_data)

    # Send notification to entity author (if not self)
    if entity_author_id and entity_author_id != user.id:
        await create_notification(
            session,
            user_id=entity_author_id,
            type="comment",
            actor_id=user.id,
            entity_type=entity_type,
            entity_id=entity_id,
            title="New comment",
            body=f"Someone commented on your {entity_type}",
        )
    return comment_data
