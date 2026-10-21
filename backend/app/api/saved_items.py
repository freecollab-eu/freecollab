"""Shared pins and private bookmarks store references, never cleartext copies."""
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from ..database import get_session
from ..models.collaboration import Bookmark, ChannelPin
from ..models.message import Message
from ..services.access import require_channel_member
from ..services.auth import get_current_user
from ..services.sso_scope import require_channel_scope
from ..services.crypto import decrypt_message
from nacl.exceptions import CryptoError

router = APIRouter(tags=["saved items"])
Reference = Annotated[str, StringConstraints(min_length=1, max_length=1024)]


class SavedTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_id: Reference | None = None
    matrix_event_id: Reference | None = None

    @model_validator(mode="after")
    def one_target(self):
        if (self.message_id is None) == (self.matrix_event_id is None):
            raise ValueError("Choose exactly one message or encrypted event")
        return self


class BookmarkTarget(SavedTarget):
    channel_id: Reference


def validate_target(session, channel_id: str, data: SavedTarget, user_id: str):
    from ..services.matrix_bridge import is_secure_channel
    secure = is_secure_channel(session, channel_id)
    if data.matrix_event_id:
        if not secure:
            raise HTTPException(400, "Encrypted event references require an encrypted channel")
        # The authorized client validates/decrypts this event against the bound
        # Matrix room. An opaque reference is not a claim of verified content.
        from ..services.matrix_bridge import validate_secure_event
        validate_secure_event(session, channel_id, data.matrix_event_id, user_id, require_sender=False)
        return data.matrix_event_id, "matrix"
    if secure:
        raise HTTPException(409, "This channel requires an encrypted event reference")
    message = session.get(Message, data.message_id)
    if not message or message.deleted or message.channel_id != channel_id:
        raise HTTPException(404, "Message not found in channel")
    return message.id, "message"


def saved_data(session, record):
    result = record.model_dump()
    result["message_id"] = record.item_id if record.kind == "message" else None
    result["matrix_event_id"] = record.item_id if record.kind == "matrix" else None
    result["available"] = True
    if record.kind == "message":
        from ..services.matrix_bridge import is_secure_channel
        message = session.get(Message, record.item_id)
        result["available"] = bool(message and message.channel_id == record.channel_id and not message.deleted and not is_secure_channel(session, record.channel_id))
        if result["available"]:
            try:
                content = decrypt_message(message.content, message.channel_id) if message.encrypted else message.content
            except (CryptoError, ValueError):
                raise HTTPException(409, "Saved message could not be decrypted") from None
            result["message"] = {"id": message.id, "channel_id": message.channel_id, "content": content,
                                 "author_id": message.author_id, "parent_id": message.parent_id,
                                 "created_at": message.created_at, "edited_at": message.edited_at}
    return result


@router.get("/channels/{channel_id}/pins")
def get_pins(channel_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    require_channel_member(session, channel_id, user.id)
    records = session.exec(select(ChannelPin).where(ChannelPin.channel_id == channel_id).order_by(ChannelPin.created_at.desc())).all()
    return [saved_data(session, record) for record in records]


@router.post("/channels/{channel_id}/pins")
def pin_message(channel_id: str, data: SavedTarget, user=Depends(get_current_user), session=Depends(get_session)):
    member = require_channel_member(session, channel_id, user.id)
    if member.role != "admin":
        raise HTTPException(403, "Only channel administrators can change pins")
    item_id, kind = validate_target(session, channel_id, data, user.id)
    record = session.get(ChannelPin, (channel_id, item_id))
    if record is None:
        record = ChannelPin(channel_id=channel_id, item_id=item_id, kind=kind, created_by=user.id)
        session.add(record)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            record = session.get(ChannelPin, (channel_id, item_id))
            if record is None:
                raise
    return saved_data(session, record)


@router.delete("/channels/{channel_id}/pins/{item_id:path}")
def unpin_message(channel_id: str, item_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    member = require_channel_member(session, channel_id, user.id)
    if member.role != "admin":
        raise HTTPException(403, "Only channel administrators can change pins")
    record = session.get(ChannelPin, (channel_id, item_id))
    if record:
        session.delete(record)
        session.commit()
    return {"removed": True}


@router.get("/bookmarks")
def get_bookmarks(user=Depends(get_current_user), session=Depends(get_session)):
    records = session.exec(select(Bookmark).where(Bookmark.user_id == user.id).order_by(Bookmark.created_at.desc())).all()
    result = []
    for record in records:
        try:
            require_channel_member(session, record.channel_id, user.id)
        except HTTPException:
            continue
        result.append(saved_data(session, record))
    return result


@router.post("/bookmarks")
def bookmark_message(data: BookmarkTarget, user=Depends(get_current_user), session=Depends(get_session)):
    require_channel_member(session, data.channel_id, user.id)
    item_id, kind = validate_target(session, data.channel_id, data, user.id)
    record = session.get(Bookmark, (user.id, data.channel_id, item_id))
    if record is None:
        record = Bookmark(user_id=user.id, channel_id=data.channel_id, item_id=item_id, kind=kind)
        session.add(record)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            record = session.get(Bookmark, (user.id, data.channel_id, item_id))
            if record is None:
                raise
    return saved_data(session, record)


@router.delete("/bookmarks/{channel_id}/{item_id:path}")
def remove_bookmark(channel_id: str, item_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    # Removing one's own reference remains possible after losing channel access.
    require_channel_scope(session, channel_id)
    record = session.get(Bookmark, (user.id, channel_id, item_id))
    if record:
        session.delete(record)
        session.commit()
    return {"removed": True}
