"""Bounded collaborative document transport and immutable named revisions."""
from datetime import datetime
import base64

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from ..database import get_session
from ..models.shared_documents import SharedDocument, SharedDocumentComment, SharedDocumentSnapshot, SharedDocumentUpdate
# Reference validation registers scheduled-reference foreign keys as well.
from ..models.chat_productivity import ScheduledMessage  # noqa: F401
from ..services.auth import get_current_user, security
from ..services import shared_documents as service
from ..services.message_references import ReferenceInput, validate_references


def no_store(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/channels/{channel_id}/shared-documents", tags=["shared documents"], dependencies=[Depends(no_store)])
Identifier = str


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UpdateInput(StrictInput):
    update_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    update_base64: str | None = Field(default=None, min_length=4, max_length=350000)
    matrix_event_id: str | None = Field(default=None, min_length=1, max_length=1024)
    title: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def mode(self):
        if (self.update_base64 is None) == (self.matrix_event_id is None):
            raise ValueError("Supply either a native Yjs update or an encrypted event reference")
        if self.matrix_event_id is not None and self.title is not None:
            raise ValueError("Secure document titles belong inside the encrypted event")
        return self


class CreateInput(UpdateInput):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")


class SnapshotInput(StrictInput):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    covers_sequence: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    update_base64: str | None = Field(default=None, min_length=4, max_length=700000)
    matrix_event_id: str | None = Field(default=None, min_length=1, max_length=1024)

    @model_validator(mode="after")
    def mode(self):
        if self.matrix_event_id:
            if self.update_base64 is not None or self.name is not None:
                raise ValueError("Encrypted snapshots accept only an event reference")
        elif self.update_base64 is None or self.name is None or not self.name.strip():
            raise ValueError("A snapshot needs a name and a Yjs state")
        return self


class CommentAnchor(StrictInput):
    version: int = Field(default=1, ge=1, le=1)
    document_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    field: str = Field(default='body', pattern=r'^body$')
    start_relative: str = Field(min_length=4, max_length=4096)
    end_relative: str = Field(min_length=4, max_length=4096)
    quote: str = Field(min_length=1, max_length=2000)
    source_sequence: int = Field(ge=1)

    @field_validator('start_relative', 'end_relative')
    @classmethod
    def canonical_position(cls, value):
        try:
            raw = base64.b64decode(value, validate=True)
            if not 1 <= len(raw) <= 2048 or base64.b64encode(raw).decode() != value:
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError('Use bounded canonical Yjs relative positions') from None
        return value


class CommentInput(StrictInput):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    text: str | None = Field(default=None, min_length=1, max_length=2000)
    references: list[ReferenceInput] | None = Field(default=None, max_length=50)
    anchor: CommentAnchor | None = None
    matrix_event_id: str | None = Field(default=None, min_length=1, max_length=1024)

    @model_validator(mode="after")
    def mode(self):
        if self.matrix_event_id:
            if self.text is not None or self.references is not None or self.anchor is not None:
                raise ValueError("Encrypted comments accept only an event reference")
        elif self.text is None or not self.text.strip():
            raise ValueError("Write a comment")
        return self


class StateInput(StrictInput):
    expected_version: int = Field(ge=1)
    archived: bool


class ResolveInput(StrictInput):
    expected_version: int = Field(ge=1)
    resolved: bool


def document_changed(background_tasks, channel_id, document_id, part):
    from ..realtime.socket import broadcast_to_channel
    background_tasks.add_task(broadcast_to_channel, channel_id, "shared_document_changed",
                              {"channel_id": channel_id, "document_id": document_id, "part": part})


def commit(session):
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "The document changed; reload before trying again") from None


@router.get("")
def list_documents(channel_id: str, archived: bool = False, user=Depends(get_current_user), session=Depends(get_session)):
    service.require_channel_member(session, channel_id, user.id)
    rows = session.exec(select(SharedDocument).where(SharedDocument.channel_id == channel_id,
        SharedDocument.archived == archived).order_by(SharedDocument.updated_at.desc(), SharedDocument.id).limit(service.MAX_DOCUMENTS)).all()
    return [service.metadata(row, session) for row in rows]


@router.post("", status_code=201)
def create_document(channel_id: str, data: CreateInput, background_tasks: BackgroundTasks, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    actor = user.id
    service.locked_member(session, channel_id, actor, credentials.credentials)
    previous = session.get(SharedDocument, data.id)
    if previous:
        if previous.channel_id != channel_id:
            raise HTTPException(404, "Shared document not found")
        update = session.get(SharedDocumentUpdate, (data.id, data.update_id))
        if update is None or previous.created_by != actor:
            raise HTTPException(409, "This document identifier has already been used")
        service.append_update(session, previous, data, actor, credentials.credentials)
        return service.metadata(previous, session)
    count = session.exec(select(func.count()).select_from(SharedDocument).where(SharedDocument.channel_id == channel_id)).one()
    if count >= service.MAX_DOCUMENTS:
        raise HTTPException(409, "This channel reached its retained document limit")
    document = SharedDocument(id=data.id, channel_id=channel_id, created_by=actor)
    session.add(document)
    session.flush()
    service.append_update(session, document, data, actor, credentials.credentials)
    from ..services.workspaces import channel_workspace_id
    workspace_id = channel_workspace_id(session, channel_id)
    if workspace_id is not None:
        from ..services.workflow import queue_event
        queue_event(session, "document:" + document.id, workspace_id, channel_id,
                    "document_created", document.id, actor_id=actor)
    commit(session)
    document_changed(background_tasks, channel_id, document.id, "updates")
    return service.metadata(document, session)


@router.get("/{document_id}/updates")
def list_updates(channel_id: str, document_id: str, after_sequence: int = Query(default=0, ge=0),
                 limit: int = Query(default=100, ge=1, le=200), user=Depends(get_current_user), session=Depends(get_session)):
    document = service.document_for(session, channel_id, document_id, user.id)
    rows = session.exec(select(SharedDocumentUpdate).where(SharedDocumentUpdate.document_id == document.id,
        SharedDocumentUpdate.sequence > after_sequence).order_by(SharedDocumentUpdate.sequence).limit(limit + 1)).all()
    selected = rows[:limit]
    return {"document": service.metadata(document, session), "items": [service.content_record(session, document, row, "update") for row in selected],
            "has_more": len(rows) > limit, "next_sequence": selected[-1].sequence if selected else after_sequence}


@router.post("/{document_id}/updates")
def append(channel_id: str, document_id: str, data: UpdateInput, background_tasks: BackgroundTasks, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    actor = user.id
    document, _ = service.writable_document(session, channel_id, document_id, actor, credentials.credentials)
    row = service.append_update(session, document, data, actor, credentials.credentials)
    commit(session)
    document_changed(background_tasks, channel_id, document.id, "updates")
    return service.content_record(session, document, row, "update")


@router.patch("/{document_id}")
def archive(channel_id: str, document_id: str, data: StateInput, background_tasks: BackgroundTasks, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    actor = user.id
    member = service.locked_member(session, channel_id, actor, credentials.credentials)
    document = service.document_for(session, channel_id, document_id, actor)
    if document.created_by != actor and member.role != "admin":
        raise HTTPException(403, "Only the author or channel administrator can archive this document")
    if document.version != data.expected_version:
        raise HTTPException(409, "The document changed; reload before trying again")
    document.archived = data.archived
    document.version += 1
    document.updated_at = datetime.utcnow()
    session.add(document)
    commit(session)
    document_changed(background_tasks, channel_id, document.id, "metadata")
    return service.metadata(document, session)


@router.get("/{document_id}/snapshots")
def list_snapshots(channel_id: str, document_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    document = service.document_for(session, channel_id, document_id, user.id)
    rows = session.exec(select(SharedDocumentSnapshot).where(SharedDocumentSnapshot.document_id == document.id)
        .order_by(SharedDocumentSnapshot.created_at.desc(), SharedDocumentSnapshot.id).limit(service.MAX_SNAPSHOTS)).all()
    return [service.content_record(session, document, row, "snapshot") for row in rows]


@router.post("/{document_id}/snapshots", status_code=201)
def snapshot(channel_id: str, document_id: str, data: SnapshotInput, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    actor = user.id
    document, _ = service.writable_document(session, channel_id, document_id, actor, credentials.credentials)
    secure = service.validate_mode(session, channel_id, actor, data.matrix_event_id, credentials.credentials)
    if document.update_count != data.covers_sequence:
        raise HTTPException(409, "New edits arrived; synchronize the document before saving its revision")
    if session.get(SharedDocumentSnapshot, data.id):
        raise HTTPException(409, "This revision identifier has already been used")
    count = session.exec(select(func.count()).select_from(SharedDocumentSnapshot).where(SharedDocumentSnapshot.document_id == document.id)).one()
    if count >= service.MAX_SNAPSHOTS:
        raise HTTPException(409, "This document reached its named revision limit")
    fields = None if secure else {"name": data.name.strip(), "update_base64": service.canonical_update(data.update_base64, snapshot=True)}
    if fields:
        from ..services.enterprise_governance import enforce_native_document
        enforce_native_document(session, channel_id, actor, fields, opaque_update=True)
    row = SharedDocumentSnapshot(id=data.id, document_id=document.id, covers_sequence=data.covers_sequence,
        created_by=actor, content_mode="matrix" if secure else "server", matrix_event_id=data.matrix_event_id,
        encrypted_payload=None if secure else service.seal(channel_id, "snapshot", document.id, data.id, fields))
    session.add(row)
    commit(session)
    return service.content_record(session, document, row, "snapshot")


@router.get("/{document_id}/comments")
def list_comments(channel_id: str, document_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    document = service.document_for(session, channel_id, document_id, user.id)
    rows = session.exec(select(SharedDocumentComment).where(SharedDocumentComment.document_id == document.id)
        .order_by(SharedDocumentComment.created_at, SharedDocumentComment.id).limit(service.MAX_COMMENTS)).all()
    return [service.content_record(session, document, row, "comment") for row in rows]


@router.post("/{document_id}/comments", status_code=201)
def comment(channel_id: str, document_id: str, data: CommentInput, background_tasks: BackgroundTasks, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    actor = user.id
    document, _ = service.writable_document(session, channel_id, document_id, actor, credentials.credentials)
    secure = service.validate_mode(session, channel_id, actor, data.matrix_event_id, credentials.credentials)
    fields = None if secure else {"text": data.text.strip()}
    if fields and data.anchor:
        if data.anchor.document_id != document.id or data.anchor.source_sequence > document.update_count:
            raise HTTPException(409, 'The comment passage belongs to a different or unsynchronized document')
        fields['anchor'] = data.anchor.model_dump()
    if fields and data.references:
        leading = len(data.text[:len(data.text) - len(data.text.lstrip())].encode("utf-16-le")) // 2
        maximum = len(fields["text"].encode("utf-16-le")) // 2
        if any(ref.start < leading or ref.end - leading > maximum for ref in data.references):
            raise HTTPException(422, "A reference must stay inside its displayed comment")
        shifted = [ReferenceInput(kind=ref.kind, id=ref.id, start=ref.start - leading, end=ref.end - leading) for ref in data.references]
        fields["references"], _ = validate_references(session, channel_id, actor, fields["text"], shifted)
        # A target resolver may refresh authority or invoke an extension. Keep
        # the current credential, document and native mode after that lookup.
        document, _ = service.writable_document(session, channel_id, document_id, actor, credentials.credentials)
        service.validate_mode(session, channel_id, actor, None, credentials.credentials)
    if fields:
        from ..services.enterprise_governance import enforce_native_document
        enforce_native_document(session, channel_id, actor, fields)
    checksum = service.fingerprint(fields, data.matrix_event_id)
    previous = session.get(SharedDocumentComment, data.id)
    if previous:
        if previous.document_id != document.id:
            raise HTTPException(404, "Comment not found")
        if previous.created_by != actor or previous.payload_hash != checksum:
            raise HTTPException(409, "This comment identifier has already been used")
        return service.content_record(session, document, previous, "comment")
    count = session.exec(select(func.count()).select_from(SharedDocumentComment).where(SharedDocumentComment.document_id == document.id)).one()
    if count >= service.MAX_COMMENTS:
        raise HTTPException(409, "This document reached its retained comment limit")
    row = SharedDocumentComment(id=data.id, document_id=document.id, created_by=actor,
        content_mode="matrix" if secure else "server", matrix_event_id=data.matrix_event_id, payload_hash=checksum,
        encrypted_payload=None if secure else service.seal(channel_id, "comment", document.id, data.id, fields))
    session.add(row)
    commit(session)
    document_changed(background_tasks, channel_id, document.id, "comments")
    return service.content_record(session, document, row, "comment")


@router.patch("/{document_id}/comments/{comment_id}")
def resolve(channel_id: str, document_id: str, comment_id: str, data: ResolveInput, background_tasks: BackgroundTasks, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    actor = user.id
    document, member = service.writable_document(session, channel_id, document_id, actor, credentials.credentials)
    row = session.get(SharedDocumentComment, comment_id)
    if row is None or row.document_id != document.id:
        raise HTTPException(404, "Comment not found")
    if row.created_by != actor and member.role != "admin":
        raise HTTPException(403, "Only the comment author or channel administrator can resolve it")
    if row.version != data.expected_version:
        raise HTTPException(409, "This comment changed; reload its current state")
    row.resolved, row.version = data.resolved, row.version + 1
    session.add(row)
    commit(session)
    document_changed(background_tasks, channel_id, document.id, "comments")
    return service.content_record(session, document, row, "comment")
