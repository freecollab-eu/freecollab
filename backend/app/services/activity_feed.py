"""Activity content never crosses the native/Matrix encryption boundary."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Literal

from fastapi import HTTPException
from nacl.exceptions import CryptoError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import update
from sqlmodel import select

from ..config import settings
from ..models.activity_feed import (ActivityFeedChoice, ActivityFeedChoiceRevision, ActivityFeedComment,
    ActivityFeedCommentRevision, ActivityFeedFollow, ActivityFeedPost, ActivityFeedRead, ActivityFeedRevision)
from ..models.notification import Notification, NotificationPreference
from ..models.user import User
from .access import lock_channel_writes, require_channel_member
from .auth import active_user_for_token
from .chat_notifications import require_native_channel
from .crypto import decrypt_message, encrypt_message
from .matrix_bridge import is_secure_channel, validate_secure_event
from .request_guard import recheck_actor
from .workspaces import require_direct_message_writable

MAX_POSTS = 500
MAX_COMMENTS = 200
MAX_REVISIONS = 100
MAX_CHOICES = 1000
ID_PATTERN = r"^[A-Za-z0-9_-]{1,80}$"
OPAQUE_PATTERN = r"^[0-9a-f]{32}$"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PollOption(Strict):
    id: str = Field(pattern=OPAQUE_PATTERN)
    label: str = Field(min_length=1, max_length=160)

    @field_validator("label")
    @classmethod
    def label_text(cls, value):
        if not value.strip(): raise ValueError("Write an option label")
        return value


class PollRouting(Strict):
    id: str = Field(pattern=OPAQUE_PATTERN)
    option_ids: list[str] = Field(min_length=2, max_length=8)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    multiple: bool = False
    closes_at: str | None = Field(default=None, max_length=40)

    @field_validator("option_ids")
    @classmethod
    def distinct_ids(cls, value):
        import re
        if len(set(value)) != len(value) or any(not re.fullmatch(OPAQUE_PATTERN, item) for item in value):
            raise ValueError("Poll option identities must be distinct random identifiers")
        return value

    @field_validator("closes_at")
    @classmethod
    def deadline(cls, value):
        if value is None: return value
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None: raise ValueError("Use a timezone-aware poll deadline")
        return parsed.astimezone(timezone.utc).isoformat()


class PollFields(Strict):
    id: str = Field(pattern=OPAQUE_PATTERN)
    nonce: str = Field(pattern=OPAQUE_PATTERN)
    question: str = Field(min_length=1, max_length=240)
    options: list[PollOption] = Field(min_length=2, max_length=8)
    multiple: bool = False
    closes_at: str | None = Field(default=None, max_length=40)

    @model_validator(mode="after")
    def valid(self):
        if not self.question.strip(): raise ValueError("Write a poll question")
        route = routing(self)
        self.closes_at = route.closes_at
        return self


class PostFields(Strict):
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(default="", max_length=20000)
    poll: PollFields | None = None

    @field_validator("title")
    @classmethod
    def title_text(cls, value):
        if not value.strip(): raise ValueError("Write a post title")
        return value


class CommentFields(Strict):
    body: str = Field(min_length=1, max_length=4000)

    @field_validator("body")
    @classmethod
    def body_text(cls, value):
        if not value.strip(): raise ValueError("Write a comment")
        return value


class VoteFields(Strict):
    option_ids: list[str] = Field(max_length=8)

    @field_validator("option_ids")
    @classmethod
    def distinct(cls, value):
        if len(set(value)) != len(value): raise ValueError("Choose each option once")
        return value


class ReactionFields(Strict):
    emoji: Literal["like", "heart", "celebrate"] | None = None


SCHEMAS = {"post": PostFields, "comment": CommentFields, "vote": VoteFields, "reaction": ReactionFields}


def routing(poll):
    value = poll.model_dump(mode="json")
    deadline = value["closes_at"]
    if deadline:
        value["closes_at"] = datetime.fromisoformat(deadline.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    digest = sha256(json_text(value).encode()).hexdigest()
    return PollRouting(id=poll.id, option_ids=[item.id for item in poll.options], digest=digest, multiple=poll.multiple, closes_at=poll.closes_at)


def json_text(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def enabled():
    if not getattr(settings, "activity_feeds_enabled", True): raise HTTPException(404, "Activity feeds are unavailable")


def member(session, channel_id, actor_id):
    enabled()
    row = require_channel_member(session, channel_id, actor_id)
    actor = session.get(User, actor_id, populate_existing=True)
    if not actor or actor.is_blocked: raise HTTPException(403, "Current channel membership required")
    return row


def locked_member(session, channel_id, actor_id, token, *, writable=True):
    # Blocking an account and changing its memberships cannot race dispatch.
    session.execute(update(User).where(User.id == actor_id).values(name=User.name))
    lock_channel_writes(session, channel_id)
    session.expire_all()
    recheck_actor(session, actor_id)
    actor = active_user_for_token(token, session)
    if not actor or actor.id != actor_id: raise HTTPException(401, "Your session changed; sign in again")
    current = member(session, channel_id, actor_id)
    if writable: require_direct_message_writable(session, channel_id, actor_id)
    return current


def post_for(session, channel_id, identifier):
    post = session.get(ActivityFeedPost, identifier, populate_existing=True)
    if not post or post.channel_id != channel_id: raise HTTPException(404, "Activity post not found")
    return post


def comment_for(session, post, identifier):
    comment = session.get(ActivityFeedComment, identifier, populate_existing=True)
    if not comment or comment.post_id != post.id: raise HTTPException(404, "Activity comment not found")
    return comment


def editor(item, actor_id, membership):
    if item.created_by != actor_id and membership.role != "admin":
        raise HTTPException(403, "Only the author or channel administrator can revise this item")


def poll_data(post):
    return json.loads(post.poll_json) if post.poll_json else None


def validate_content(session, channel_id, kind, fields, event_id, actor_id):
    if is_secure_channel(session, channel_id):
        if fields is not None or not event_id: raise HTTPException(409, "This channel requires an encrypted activity event")
        validate_secure_event(session, channel_id, event_id, actor_id)
        return None
    if event_id: raise HTTPException(400, "Encrypted activity references require an encrypted channel")
    try:
        return SCHEMAS[kind].model_validate(fields).model_dump(mode="json")
    except (ValueError, TypeError):
        raise HTTPException(422, "Invalid activity content") from None


def seal_fields(channel_id, kind, identifier, revision, fields):
    envelope = {"kind": kind, "id": identifier, "revision": revision, "fields": fields}
    return encrypt_message(json_text(envelope), "activity-feed:" + channel_id)


def open_fields(session, channel_id, kind, identifier, revision, encrypted_payload):
    """Explicit native serializer, also used by authorized data exports."""
    require_native_channel(session, channel_id)
    try:
        envelope = json.loads(decrypt_message(encrypted_payload, "activity-feed:" + channel_id))
        if envelope.get("kind") != kind or envelope.get("id") != identifier or envelope.get("revision") != revision:
            raise ValueError("Activity envelope context mismatch")
        return SCHEMAS[kind].model_validate(envelope["fields"]).model_dump(mode="json")
    except (CryptoError, ValueError, TypeError, KeyError, AttributeError):
        raise HTTPException(409, "Stored activity content could not be verified") from None


def revision_for(session, kind, identifier, number, *, actor_id=None):
    if kind == "post":
        return session.exec(select(ActivityFeedRevision).where(ActivityFeedRevision.post_id == identifier, ActivityFeedRevision.revision == number)).first()
    if kind == "comment":
        return session.exec(select(ActivityFeedCommentRevision).where(ActivityFeedCommentRevision.comment_id == identifier, ActivityFeedCommentRevision.revision == number)).first()
    return session.exec(select(ActivityFeedChoiceRevision).where(ActivityFeedChoiceRevision.post_id == identifier,
        ActivityFeedChoiceRevision.user_id == actor_id, ActivityFeedChoiceRevision.kind == kind, ActivityFeedChoiceRevision.revision == number)).first()


def content_record(session, channel_id, kind, item, revision):
    if not revision: raise HTTPException(409, "Activity revision is unavailable")
    choice = kind in {"vote", "reaction"}
    identifier = item.post_id + ":" + item.user_id + ":" + kind if choice else item.id
    record = {"id": identifier, "kind": kind, "channel_id": channel_id,
        "post_id": item.post_id if kind != "post" else item.id,
        "created_by": item.user_id if choice else item.created_by,
        "created_at": item.updated_at if choice else item.created_at,
        "revision": revision.revision, "revised_by": revision.user_id if choice else revision.created_by,
        "revised_at": revision.created_at, "archived": getattr(revision, "archived", False),
        "poll": json.loads(revision.poll_json) if kind == "post" and revision.poll_json else None,
        "poll_id": revision.poll_id if choice else None,
        "content_mode": revision.content_mode, "matrix_event_id": revision.matrix_event_id}
    if revision.content_mode == "server":
        record["fields"] = open_fields(session, channel_id, kind, identifier, revision.revision, revision.encrypted_payload)
        if kind == "post":
            actual = routing(PostFields.model_validate(record["fields"]).poll).model_dump() if record["fields"].get("poll") else None
            if actual != record["poll"]: raise HTTPException(409, "Stored poll metadata does not match its content")
    return record


def post_record(session, post, actor_id):
    result = content_record(session, post.channel_id, "post", post, revision_for(session, "post", post.id, post.revision))
    seen = session.get(ActivityFeedRead, (post.id, actor_id))
    result["seen_revision"] = seen.seen_revision if seen else 0
    return result


def store_revision(session, post, item, kind, data, fields, actor_id, *, poll=None):
    if item.revision != data.expected_revision: raise HTTPException(409, "This activity changed; refresh before saving")
    if item.revision >= MAX_REVISIONS: raise HTTPException(409, "This item reached its retained revision limit")
    number = item.revision + 1
    choice = kind in {"vote", "reaction"}
    identifier = post.id + ":" + actor_id + ":" + kind if choice else item.id
    values = {"revision": number, "content_mode": "matrix" if data.matrix_event_id else "server",
        "matrix_event_id": data.matrix_event_id,
        "encrypted_payload": None if fields is None else seal_fields(post.channel_id, kind, identifier, number, fields)}
    if kind == "post":
        row = ActivityFeedRevision(post_id=post.id, created_by=actor_id, archived=data.archived,
            poll_json=json_text(poll) if poll else None, **values)
        item.poll_json = row.poll_json
    elif kind == "comment":
        row = ActivityFeedCommentRevision(comment_id=item.id, created_by=actor_id, archived=data.archived, **values)
    else:
        row = ActivityFeedChoiceRevision(post_id=post.id, user_id=actor_id, kind=kind, poll_id=data.poll_id, **values)
        item.poll_id = data.poll_id
    item.revision = number
    item.updated_at = datetime.utcnow()
    if not choice: item.archived = data.archived
    session.add(item); session.add(row)
    return row


def validate_poll_change(session, post, fields, supplied_poll):
    if fields is None:
        poll = supplied_poll.model_dump() if supplied_poll else None
    else:
        if supplied_poll is not None: raise HTTPException(400, "Native poll metadata is derived from its content")
        parsed = PostFields.model_validate(fields)
        poll = routing(parsed.poll).model_dump() if parsed.poll else None
    if poll != poll_data(post) and session.exec(select(ActivityFeedChoice).where(ActivityFeedChoice.post_id == post.id, ActivityFeedChoice.kind == "vote")).first():
        raise HTTPException(409, "Poll settings cannot change after the first vote; publish another poll")
    return poll


def validate_vote(post, data, fields):
    poll = poll_data(post)
    if not poll or data.poll_id != poll["id"]: raise HTTPException(409, "This poll changed or is unavailable")
    if poll["closes_at"] and datetime.fromisoformat(poll["closes_at"]) <= datetime.now(timezone.utc):
        raise HTTPException(409, "This poll has closed")
    if fields is not None:
        choices = fields["option_ids"]
        if set(choices) - set(poll["option_ids"]) or not poll["multiple"] and len(choices) > 1:
            raise HTTPException(422, "Choose valid options for this poll")


def active_choices(session, post, kind):
    choices = session.exec(select(ActivityFeedChoice).where(ActivityFeedChoice.post_id == post.id, ActivityFeedChoice.kind == kind)
        .order_by(ActivityFeedChoice.user_id).limit(MAX_CHOICES)).all()
    result = []
    for choice in choices:
        try: member(session, post.channel_id, choice.user_id)
        except HTTPException: continue
        result.append(content_record(session, post.channel_id, kind, choice,
            revision_for(session, kind, post.id, choice.revision, actor_id=choice.user_id)))
    return result


def notify(session, post, actor_id, recipients, kind, identity):
    for user_id in set(recipients) - {actor_id}:
        try: member(session, post.channel_id, user_id)
        except HTTPException: continue
        prefs = session.exec(select(NotificationPreference).where(NotificationPreference.user_id == user_id)).first()
        flag = {"post": "follows", "comment": "comments", "reaction": "likes"}[kind]
        if prefs and (not prefs.in_app or not getattr(prefs, flag)): continue
        identifier = sha256(("activity-feed:" + identity + ":" + user_id).encode()).hexdigest()[:32]
        if session.get(Notification, identifier): continue
        titles = {"post": "New activity from someone you follow", "comment": "New activity comment", "reaction": "New activity reaction"}
        session.add(Notification(id=identifier, user_id=user_id, type="system", actor_id=actor_id,
            entity_type="activity_feed_post", entity_id=post.id, title=titles[kind], body=None,
            action_url="#channel=" + post.channel_id + "&feed=" + post.id))
