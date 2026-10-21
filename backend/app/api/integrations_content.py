"""Channel document links and calendars; secure content never reaches this API."""
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from nacl.exceptions import CryptoError
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from ..database import get_session
from ..models.integration_content import IntegrationContent, IntegrationContentRevision
from ..services.access import lock_channel_writes, require_channel_member
from ..services.auth import get_current_user
from ..services.chat_notifications import require_native_channel
from ..services.crypto import decrypt_message, encrypt_message
from ..services.integration_content import CalendarFields, DocumentFields
from ..services.matrix_bridge import is_secure_channel, validate_secure_event
from ..services.workspaces import require_direct_message_writable
from ..services.webhooks import enqueue_channel_event

router = APIRouter(tags=["document and calendar integrations"])
Kind = Literal["documents", "calendar"]


class ContentWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    expected_revision: int = Field(default=0, ge=0)
    fields: dict | None = None
    matrix_event_id: str | None = Field(default=None, min_length=1, max_length=1024)
    deleted: bool = False

    @model_validator(mode="after")
    def content_mode(self):
        if self.matrix_event_id is not None:
            if self.fields is not None:
                raise ValueError("Encrypted integrations accept only an opaque event reference")
        elif not self.deleted and self.fields is None:
            raise ValueError("This integration needs content")
        if self.deleted and self.fields is not None:
            raise ValueError("Deleted content must not include fields")
        return self


def latest_revision(session, item_id):
    return session.exec(select(IntegrationContentRevision).where(IntegrationContentRevision.item_id == item_id)
                        .order_by(IntegrationContentRevision.revision.desc()).limit(1)).first()


def serialize(session, item, revision):
    result = {"id": item.id, "channel_id": item.channel_id, "kind": item.kind,
              "created_by": item.created_by, "created_at": item.created_at,
              "revision": revision.revision, "revised_by": revision.created_by,
              "revised_at": revision.created_at, "deleted": revision.deleted,
              "content_mode": revision.content_mode, "matrix_event_id": revision.matrix_event_id}
    if revision.content_mode == "server":
        require_native_channel(session, item.channel_id)
        try:
            payload = json.loads(decrypt_message(revision.encrypted_payload, item.channel_id))
            if payload.get("id") != item.id or payload.get("revision") != revision.revision or payload.get("kind") != item.kind:
                raise ValueError("Integration context mismatch")
            result["fields"] = payload.get("fields")
        except (CryptoError, ValueError, TypeError, AttributeError):
            raise HTTPException(409, "Stored integration content could not be decrypted") from None
    return result


@router.get("/channels/{channel_id}/integrations/{kind}")
def list_content(channel_id: str, kind: Kind, user=Depends(get_current_user), session=Depends(get_session)):
    require_channel_member(session, channel_id, user.id)
    items = session.exec(select(IntegrationContent).where(IntegrationContent.channel_id == channel_id,
                        IntegrationContent.kind == kind).order_by(IntegrationContent.created_at.desc())).all()
    result = []
    for item in items:
        revision = latest_revision(session, item.id)
        if revision and not revision.deleted:
            result.append(serialize(session, item, revision))
    return result


@router.get("/channels/{channel_id}/integrations/{kind}/{item_id}/revisions")
def list_revisions(channel_id: str, kind: Kind, item_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    require_channel_member(session, channel_id, user.id)
    item = session.get(IntegrationContent, item_id)
    if not item or item.channel_id != channel_id or item.kind != kind:
        raise HTTPException(404, "Integration item not found")
    revisions = session.exec(select(IntegrationContentRevision).where(IntegrationContentRevision.item_id == item.id)
                             .order_by(IntegrationContentRevision.revision.desc())).all()
    return [serialize(session, item, revision) for revision in revisions]


@router.post("/channels/{channel_id}/integrations/{kind}")
def write_content(channel_id: str, kind: Kind, data: ContentWrite, user=Depends(get_current_user), session=Depends(get_session)):
    lock_channel_writes(session, channel_id)
    member = require_channel_member(session, channel_id, user.id)
    require_direct_message_writable(session, channel_id, user.id)
    secure = is_secure_channel(session, channel_id)
    if secure != (data.matrix_event_id is not None):
        raise HTTPException(409, "Use encrypted events for a secure channel, and native fields for a legacy channel")
    if secure:
        validate_secure_event(session, channel_id, data.matrix_event_id, user.id)
    item = session.get(IntegrationContent, data.id)
    previous = latest_revision(session, item.id) if item else None
    if item:
        if item.channel_id != channel_id or item.kind != kind:
            raise HTTPException(404, "Integration item not found")
        if item.created_by != user.id and member.role != "admin":
            raise HTTPException(403, "Only the author or channel administrator can change this item")
        if not previous or previous.revision != data.expected_revision or previous.deleted:
            raise HTTPException(409, "This item changed; reload its current version")
    else:
        if data.expected_revision or data.deleted:
            raise HTTPException(404, "Integration item not found")
        count = session.exec(select(IntegrationContent.id).where(IntegrationContent.channel_id == channel_id)).all()
        if len(count) >= 1000:
            raise HTTPException(409, "This channel has reached its integration item limit")
        item = IntegrationContent(id=data.id, channel_id=channel_id, kind=kind, created_by=user.id)
        session.add(item)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            raise HTTPException(409, "This item changed; reload its current version") from None
    fields = None
    if not secure and not data.deleted:
        try:
            schema = DocumentFields if kind == "documents" else CalendarFields
            fields = schema.model_validate(data.fields).model_dump(mode="json")
        except ValueError as error:
            raise HTTPException(422, "Invalid integration fields: " + str(error).split("\n")[0]) from None
    number = previous.revision + 1 if previous else 1
    payload = {"id": item.id, "kind": kind, "revision": number, "fields": fields, "deleted": data.deleted}
    revision = IntegrationContentRevision(item_id=item.id, revision=number, created_by=user.id,
              content_mode="matrix" if secure else "server", matrix_event_id=data.matrix_event_id,
              encrypted_payload=None if secure else encrypt_message(json.dumps(payload, ensure_ascii=False), channel_id),
              deleted=data.deleted)
    session.add(revision)
    enqueue_channel_event(session, channel_id, "channel.activity", revision.id)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "This item changed; reload its current version") from None
    return serialize(session, item, revision)
