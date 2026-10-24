"""Production activity feeds, separate from the legacy experimental posts API."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import Field, model_validator
from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from ..database import get_session
from ..models.activity_feed import (ActivityFeedChoice, ActivityFeedChoiceRevision, ActivityFeedComment,
    ActivityFeedCommentRevision, ActivityFeedFollow, ActivityFeedPost, ActivityFeedRead, ActivityFeedRevision)
from ..services import activity_feed as service
from ..services.account_security import account_limit
from ..services.auth import get_current_user, security


def private(response: Response): response.headers["Cache-Control"] = "no-store"
router = APIRouter(prefix="/channels/{channel_id}/activity-feed", tags=["activity feeds"], dependencies=[Depends(private)])


class ContentWrite(service.Strict):
    expected_revision: int = Field(default=0, ge=0, le=service.MAX_REVISIONS)
    fields: dict | None = None
    matrix_event_id: str | None = Field(default=None, min_length=1, max_length=1024)

    @model_validator(mode="after")
    def exclusive(self):
        if (self.fields is None) == (self.matrix_event_id is None): raise ValueError("Provide content or an encrypted event reference")
        return self


class PostWrite(ContentWrite):
    id: str = Field(pattern=service.ID_PATTERN)
    archived: bool = False
    poll: service.PollRouting | None = None


class CommentWrite(ContentWrite):
    id: str = Field(pattern=service.ID_PATTERN)
    archived: bool = False


class ChoiceWrite(ContentWrite):
    poll_id: str | None = Field(default=None, pattern=service.OPAQUE_PATTERN)


class FollowWrite(service.Strict): following: bool
class ReadWrite(service.Strict): seen_revision: int = Field(ge=1, le=service.MAX_REVISIONS)


def commit(session):
    try: session.commit()
    except IntegrityError:
        session.rollback(); raise HTTPException(409, "Activity changed; refresh before trying again") from None


def prepare(session, channel_id, actor, token, *, writable=True):
    account_limit(session, "activity-feed-write", actor, 200, 3600)
    return service.locked_member(session, channel_id, actor, token, writable=writable)


def repeat(session, channel_id, kind, item, data, fields, actor, *, poll=None):
    if item.revision != data.expected_revision + 1: return False
    revision = service.revision_for(session, kind, item.post_id if kind in {"vote", "reaction"} else item.id,
        item.revision, actor_id=actor)
    if not revision or (revision.user_id if kind in {"vote", "reaction"} else revision.created_by) != actor: return False
    record = service.content_record(session, channel_id, kind, item, revision)
    return (record.get("fields") == fields and record["matrix_event_id"] == data.matrix_event_id
        and record["archived"] == getattr(data, "archived", False)
        and (kind != "post" or record["poll"] == poll)
        and (kind not in {"vote", "reaction"} or record["poll_id"] == data.poll_id))


@router.get("")
def posts(channel_id: str, before_id: str | None = Query(default=None, max_length=80),
          limit: int = Query(default=20, ge=1, le=50), archived: bool = False, following: bool = False,
          user=Depends(get_current_user), session=Depends(get_session)):
    service.member(session, channel_id, user.id)
    follows = session.exec(select(ActivityFeedFollow.target_id).where(ActivityFeedFollow.channel_id == channel_id, ActivityFeedFollow.user_id == user.id)).all()
    query = select(ActivityFeedPost).where(ActivityFeedPost.channel_id == channel_id, ActivityFeedPost.archived == archived)
    if following: query = query.where(ActivityFeedPost.created_by.in_(follows))
    if before_id:
        anchor = service.post_for(session, channel_id, before_id)
        query = query.where(or_(ActivityFeedPost.created_at < anchor.created_at,
            and_(ActivityFeedPost.created_at == anchor.created_at, ActivityFeedPost.id < anchor.id)))
    rows = session.exec(query.order_by(ActivityFeedPost.created_at.desc(), ActivityFeedPost.id.desc()).limit(limit + 1)).all()
    return {"items": [service.post_record(session, post, user.id) for post in rows[:limit]],
        "next_before_id": rows[limit - 1].id if len(rows) > limit else None, "following": follows}


@router.put("/following/{target_id}")
def follow(channel_id: str, target_id: str, data: FollowWrite, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    prepare(session, channel_id, user.id, credentials.credentials)
    if target_id == user.id: raise HTTPException(400, "Choose another channel member")
    row = session.get(ActivityFeedFollow, (channel_id, user.id, target_id))
    if data.following:
        service.member(session, channel_id, target_id)
        if not row: session.add(ActivityFeedFollow(channel_id=channel_id, user_id=user.id, target_id=target_id))
    elif row: session.delete(row)
    commit(session)
    return {"following": data.following}


@router.post("", status_code=201)
def save_post(channel_id: str, data: PostWrite, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    membership = prepare(session, channel_id, user.id, credentials.credentials)
    item = session.get(ActivityFeedPost, data.id)
    if item:
        item = service.post_for(session, channel_id, data.id); service.editor(item, user.id, membership)
    else:
        if data.expected_revision or data.archived: raise HTTPException(404, "Activity post not found")
        count = session.exec(select(func.count()).select_from(ActivityFeedPost).where(ActivityFeedPost.channel_id == channel_id)).one()
        if count >= service.MAX_POSTS: raise HTTPException(409, "This channel reached its retained activity limit")
        item = ActivityFeedPost(id=data.id, channel_id=channel_id, created_by=user.id)
        session.add(item); session.flush()
    fields = service.validate_content(session, channel_id, "post", data.fields, data.matrix_event_id, user.id, previous_item=item)
    service.locked_member(session, channel_id, user.id, credentials.credentials)
    poll = service.validate_poll_change(session, item, fields, data.poll)
    if repeat(session, channel_id, "post", item, data, fields, user.id, poll=poll): return service.post_record(session, item, user.id)
    fresh = item.revision == 0
    row = service.store_revision(session, item, item, "post", data, fields, user.id, poll=poll)
    if fresh:
        followers = session.exec(select(ActivityFeedFollow.user_id).where(ActivityFeedFollow.channel_id == channel_id, ActivityFeedFollow.target_id == user.id)).all()
        service.notify(session, item, user.id, followers, "post", row.id)
    commit(session)
    return service.post_record(session, item, user.id)


@router.get("/{post_id}")
def detail(channel_id: str, post_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    service.member(session, channel_id, user.id)
    post = service.post_for(session, channel_id, post_id)
    comments = session.exec(select(ActivityFeedComment).where(ActivityFeedComment.post_id == post.id)
        .order_by(ActivityFeedComment.created_at, ActivityFeedComment.id).limit(service.MAX_COMMENTS)).all()
    return {"post": service.post_record(session, post, user.id), "comments": [service.content_record(session, channel_id, "comment", row,
        service.revision_for(session, "comment", row.id, row.revision)) for row in comments],
        "votes": service.active_choices(session, post, "vote"), "reactions": service.active_choices(session, post, "reaction")}


@router.get("/{post_id}/revisions")
def history(channel_id: str, post_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    service.member(session, channel_id, user.id)
    post = service.post_for(session, channel_id, post_id)
    rows = session.exec(select(ActivityFeedRevision).where(ActivityFeedRevision.post_id == post.id).order_by(ActivityFeedRevision.revision.desc()).limit(service.MAX_REVISIONS)).all()
    return [service.content_record(session, channel_id, "post", post, row) for row in rows]


@router.post("/{post_id}/comments", status_code=201)
def comment(channel_id: str, post_id: str, data: CommentWrite, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    membership = prepare(session, channel_id, user.id, credentials.credentials)
    post = service.post_for(session, channel_id, post_id)
    if post.archived: raise HTTPException(409, "Reopen this post before commenting")
    item = session.get(ActivityFeedComment, data.id)
    if item:
        item = service.comment_for(session, post, data.id); service.editor(item, user.id, membership)
    else:
        if data.expected_revision or data.archived: raise HTTPException(404, "Activity comment not found")
        count = session.exec(select(func.count()).select_from(ActivityFeedComment).where(ActivityFeedComment.post_id == post.id)).one()
        if count >= service.MAX_COMMENTS: raise HTTPException(409, "This post reached its retained comment limit")
        item = ActivityFeedComment(id=data.id, post_id=post.id, created_by=user.id)
        session.add(item); session.flush()
    fields = service.validate_content(session, channel_id, "comment", data.fields, data.matrix_event_id, user.id, previous_item=item)
    service.locked_member(session, channel_id, user.id, credentials.credentials)
    if repeat(session, channel_id, "comment", item, data, fields, user.id):
        return service.content_record(session, channel_id, "comment", item, service.revision_for(session, "comment", item.id, item.revision))
    fresh = item.revision == 0
    revision = service.store_revision(session, post, item, "comment", data, fields, user.id)
    if fresh: service.notify(session, post, user.id, [post.created_by], "comment", revision.id)
    commit(session)
    return service.content_record(session, channel_id, "comment", item, revision)


@router.get("/{post_id}/comments/{comment_id}/revisions")
def comment_history(channel_id: str, post_id: str, comment_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    service.member(session, channel_id, user.id)
    post = service.post_for(session, channel_id, post_id); item = service.comment_for(session, post, comment_id)
    rows = session.exec(select(ActivityFeedCommentRevision).where(ActivityFeedCommentRevision.comment_id == item.id)
        .order_by(ActivityFeedCommentRevision.revision.desc()).limit(service.MAX_REVISIONS)).all()
    return [service.content_record(session, channel_id, "comment", item, row) for row in rows]


def save_choice(channel_id, post_id, kind, data, user, token, session):
    prepare(session, channel_id, user.id, token)
    post = service.post_for(session, channel_id, post_id)
    if post.archived: raise HTTPException(409, "Reopen this post before responding")
    if kind == "reaction" and data.poll_id: raise HTTPException(400, "Reactions do not specify a poll")
    fields = service.validate_content(session, channel_id, kind, data.fields, data.matrix_event_id, user.id)
    service.locked_member(session, channel_id, user.id, token)
    if kind == "vote": service.validate_vote(post, data, fields)
    item = session.get(ActivityFeedChoice, (post.id, user.id, kind))
    if item is None:
        if kind == "vote" and fields is not None and not fields["option_ids"]:
            raise HTTPException(422, "Choose an option before submitting your first vote")
        if data.expected_revision: raise HTTPException(409, "This choice changed; refresh before responding")
        count = session.exec(select(func.count()).select_from(ActivityFeedChoice).where(ActivityFeedChoice.post_id == post.id, ActivityFeedChoice.kind == kind)).one()
        if count >= service.MAX_CHOICES: raise HTTPException(409, "This post reached its retained response limit")
        item = ActivityFeedChoice(post_id=post.id, user_id=user.id, kind=kind)
    elif repeat(session, channel_id, kind, item, data, fields, user.id):
        return service.content_record(session, channel_id, kind, item, service.revision_for(session, kind, post.id, item.revision, actor_id=user.id))
    revision = service.store_revision(session, post, item, kind, data, fields, user.id)
    if kind == "reaction": service.notify(session, post, user.id, [post.created_by], "reaction", revision.id)
    commit(session)
    return service.content_record(session, channel_id, kind, item, revision)


@router.post("/{post_id}/vote")
def vote(channel_id: str, post_id: str, data: ChoiceWrite, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    return save_choice(channel_id, post_id, "vote", data, user, credentials.credentials, session)


@router.post("/{post_id}/reaction")
def reaction(channel_id: str, post_id: str, data: ChoiceWrite, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    return save_choice(channel_id, post_id, "reaction", data, user, credentials.credentials, session)


@router.get("/{post_id}/choices/{kind}/revisions")
def choice_history(channel_id: str, post_id: str, kind: str, user=Depends(get_current_user), session=Depends(get_session)):
    service.member(session, channel_id, user.id); post = service.post_for(session, channel_id, post_id)
    if kind not in {"vote", "reaction"}: raise HTTPException(404, "Activity choice not found")
    item = session.get(ActivityFeedChoice, (post.id, user.id, kind))
    if item is None: return []
    rows = session.exec(select(ActivityFeedChoiceRevision).where(ActivityFeedChoiceRevision.post_id == post.id,
        ActivityFeedChoiceRevision.user_id == user.id, ActivityFeedChoiceRevision.kind == kind)
        .order_by(ActivityFeedChoiceRevision.revision.desc()).limit(service.MAX_REVISIONS)).all()
    return [service.content_record(session, channel_id, kind, item, row) for row in rows]


@router.put("/{post_id}/read")
def read(channel_id: str, post_id: str, data: ReadWrite, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    prepare(session, channel_id, user.id, credentials.credentials, writable=False)
    post = service.post_for(session, channel_id, post_id)
    if data.seen_revision > post.revision: raise HTTPException(409, "That post revision is unavailable")
    row = session.get(ActivityFeedRead, (post.id, user.id))
    if row is None: row = ActivityFeedRead(post_id=post.id, user_id=user.id, seen_revision=data.seen_revision)
    else: row.seen_revision = max(row.seen_revision, data.seen_revision); row.updated_at = datetime.utcnow()
    session.add(row); commit(session)
    return {"seen_revision": row.seen_revision}
