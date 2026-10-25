"""Channel clips. Native file access uses existing file ACLs; secure clips never decrypt here."""
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func
from sqlmodel import select

from ..database import get_session
from ..models.clips import ChannelClip
from ..models.file import File
from ..services.auth import get_current_user, security
from ..services import shared_documents as content
from ..services import media_capture as capture_service
from .files import file_metadata
from ..services.field_references import ReferencedFields, require_field_reference_targets


def no_store(response: Response):
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(prefix="/channels/{channel_id}/clips", tags=["clips"], dependencies=[Depends(no_store)])


class Caption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: float = Field(ge=0, le=300)
    end: float = Field(ge=0, le=300)
    text: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def ordered(self):
        if self.end < self.start:
            raise ValueError("Caption end precedes its start")
        return self


class ClipInput(ReferencedFields):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    matrix_event_id: str | None = Field(default=None, min_length=1, max_length=1024)
    file_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    title: str | None = Field(default=None, min_length=1, max_length=200)
    duration: float | None = Field(default=None, gt=0, le=300)
    transcript: str | None = Field(default=None, max_length=30000)
    captions: list[Caption] | None = Field(default=None, max_length=300)
    publication_receipt: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")

    @model_validator(mode="before")
    @classmethod
    def opaque_references(cls, value):
        if isinstance(value, dict) and value.get("matrix_event_id") and "field_references" in value:
            raise ValueError("Secure clip references belong inside the encrypted event")
        return value

    def reference_texts(self):
        return {"title": self.title or "", "transcript": self.transcript or ""}

    @model_validator(mode="after")
    def mode(self):
        if self.matrix_event_id:
            if any(value is not None for value in (self.file_id, self.title, self.duration, self.transcript, self.captions)):
                raise ValueError("Secure clip media, titles and transcripts belong inside the encrypted event")
        elif self.file_id is None or self.title is None or not self.title.strip() or self.duration is None:
            raise ValueError("A recording needs its immutable uploaded file, title and duration")
        if self.duration is not None and self.captions and any(c.end > self.duration + 1 for c in self.captions):
            raise ValueError("Captions exceed the clip duration")
        return self


def public(session, row):
    result = {name: getattr(row, name) for name in ("id", "channel_id", "created_by", "created_at", "content_mode", "matrix_event_id")}
    if row.content_mode == "server":
        result["fields"] = content.open_payload(session, row.channel_id, "clip", row.id, row.id, row.encrypted_payload)
        file = session.get(File, row.file_id)
        result["file"] = file_metadata(file) if file and file.channel_id == row.channel_id else None
    return result


@router.get("")
def list_clips(channel_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    content.require_channel_member(session, channel_id, user.id)
    rows = session.exec(select(ChannelClip).where(ChannelClip.channel_id == channel_id)
        .order_by(ChannelClip.created_at.desc(), ChannelClip.id).limit(200)).all()
    return [public(session, row) for row in rows]


@router.get("/{clip_id}")
def get_clip(channel_id: str, clip_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    content.require_channel_member(session, channel_id, user.id)
    row = session.get(ChannelClip, clip_id)
    if not row or row.channel_id != channel_id:
        raise HTTPException(404, "Clip not found")
    return public(session, row)


@router.post("", status_code=201)
def create_clip(channel_id: str, data: ClipInput, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    capture_service.lock_actor_scope(session, channel_id, user.id)
    content.locked_member(session, channel_id, user.id, credentials.credentials)
    secure = content.validate_mode(session, channel_id, user.id, data.matrix_event_id, credentials.credentials)
    fields = None if secure else data.model_dump(mode="json", exclude={"id", "matrix_event_id", "file_id", "publication_receipt"}, exclude_none=True)
    checksum = content.fingerprint({"file_id": data.file_id, "fields": fields}, data.matrix_event_id)
    if data.publication_receipt:
        from ..services.media_capture import authorize_publication
        authorize_publication(session, data.publication_receipt, channel_id, user.id, data.id)
    previous = session.get(ChannelClip, data.id)
    if previous:
        if previous.channel_id != channel_id:
            raise HTTPException(404, "Clip not found")
        if previous.created_by != user.id or previous.payload_hash != checksum:
            raise HTTPException(409, "This clip identifier was already used")
        return public(session, previous)
    if not secure:
        require_field_reference_targets(session, channel_id, user.id, fields)
        content.locked_member(session, channel_id, user.id, credentials.credentials)
        content.validate_mode(session, channel_id, user.id, None, credentials.credentials)
        from ..services.enterprise_governance import enforce_native_document
        enforce_native_document(session, channel_id, user.id, fields)
        file = session.get(File, data.file_id)
        if not file or file.channel_id != channel_id or file.uploader_id != user.id:
            raise HTTPException(404, "Your uploaded recording was not found in this channel")
        if file.content_type.split(";")[0] not in {"audio/webm", "audio/ogg", "audio/mp4", "audio/wav", "video/webm", "video/mp4", "video/ogg"}:
            raise HTTPException(422, "Choose a supported audio or video recording")
        if not 0 < file.size <= 50 * 1024 * 1024:
            raise HTTPException(422, "Recording exceeds the 50 MiB file limit")
    count = session.exec(select(func.count()).select_from(ChannelClip).where(ChannelClip.channel_id == channel_id)).one()
    if count >= 200:
        raise HTTPException(409, "This channel reached its retained clip limit; delete older clips first")
    row = ChannelClip(id=data.id, channel_id=channel_id, created_by=user.id, content_mode="matrix" if secure else "server",
        matrix_event_id=data.matrix_event_id, file_id=data.file_id, payload_hash=checksum,
        encrypted_payload=None if secure else content.seal(channel_id, "clip", data.id, data.id, fields))
    session.add(row)
    session.commit()
    return public(session, row)


@router.delete("/{clip_id}")
def delete_clip(channel_id: str, clip_id: str, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    capture_service.lock_actor_scope(session, channel_id, user.id)
    member = content.locked_member(session, channel_id, user.id, credentials.credentials)
    row = session.get(ChannelClip, clip_id)
    if not row or row.channel_id != channel_id:
        raise HTTPException(404, "Clip not found")
    if row.created_by != user.id and member.role != "admin":
        raise HTTPException(403, "Only the author or channel administrator can remove this clip")
    from ..services.enterprise_governance import assert_deletion_allowed
    assert_deletion_allowed(session, channel_id, author_id=row.created_by)
    from ..services.collaboration_privacy import tombstone_erased_clips
    tombstone_erased_clips(session, [row.id])
    session.delete(row)
    session.commit()
    return {"deleted": True, "media_retained": True}
