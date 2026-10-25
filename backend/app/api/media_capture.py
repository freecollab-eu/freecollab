"""All-participant recording consent, local-caption configuration and clip receipts."""
from datetime import datetime
from datetime import timedelta
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from sqlalchemy.exc import IntegrityError
from ..database import get_session
from ..models.media_capture import MeetingCapture, MeetingCaptureConsent
from ..services import media_capture as service
from ..services.auth import get_current_user, security
from ..services.account_security import account_limit
from ..services.request_guard import recheck_actor
from ..services.workspaces import channel_workspace_id
from ..services.matrix_bridge import is_secure_channel
from ..services.auth import token_digest
from sqlmodel import select


def no_store(response: Response):
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(tags=["meeting-capture"], dependencies=[Depends(no_store)])


@router.get("/channels/{channel_id}/meeting-media-configuration")
def media_configuration(channel_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    from ..services.access import require_channel_member
    require_channel_member(session, channel_id, user.id); recheck_actor(session, user.id)
    return {"ice_servers": service.meetings.ice_servers(), "captions": service.configuration(), "max_participants": 6}


class RequestCapture(BaseModel):
    model_config = ConfigDict(extra="forbid")
    video: bool = False
    captions: bool = False
    save_transcript: bool = False
    language: str = Field(default="auto", min_length=2, max_length=8)
    ai_profile_id: str | None = Field(default=None, max_length=128)
    ai_profile_revision: int | None = Field(default=None, ge=1)
    ai_disclose: bool = False
    caption_audience: Literal["admitted", "channel"] | None = None


class Vote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capture_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    generation: int = Field(ge=1)
    granted: bool


class Control(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capture_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    generation: int = Field(ge=1)
    action: Literal["start", "pause", "resume", "stop", "segment"]
    request_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{8,80}$")


class CaptionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capture_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    generation: int = Field(ge=1)
    start: float = Field(ge=0, le=7200)
    end: float = Field(ge=0, le=7200)
    text: str = Field(min_length=1, max_length=2000)


class PublicationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    receipt_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    clip_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")


def publication_projection(capture, receipt):
    return {"capture_id": capture.id, "generation": receipt.generation, "receipt_id": receipt.id,
            "clip_id": receipt.clip_id, "created_by": capture.created_by,
            "recorder_participant_id": capture.recorder_participant_id,
            "transport": capture.transport, "meeting_id": capture.meeting_id,
            "content_mode": capture.content_mode,
            "caption_audience": "channel" if capture.content_mode == "matrix" else "admitted" if capture.content_mode == "server" else None,
            "consent_authority": "native-authenticated-participants"}


@router.post("/channels/{channel_id}/meeting-captures/{capture_id}/publication")
def authorize_segment(channel_id: str, capture_id: str, data: PublicationInput,
                      user=Depends(get_current_user), session=Depends(get_session)):
    from ..services.access import require_channel_member
    service.lock_actor_scope(session, channel_id, user.id)
    require_channel_member(session, channel_id, user.id); recheck_actor(session, user.id)
    row = session.get(service.MeetingCaptureReceipt, data.receipt_id)
    if not row or row.capture_id != capture_id:
        raise HTTPException(404, "Recording publication receipt not found")
    service.authorize_publication(session, row.id, channel_id, user.id, data.clip_id)
    capture = session.get(MeetingCapture, capture_id)
    result = publication_projection(capture, row); session.commit()
    return result


@router.get("/channels/{channel_id}/meeting-captures/{capture_id}/publications/{receipt_id}")
def read_publication(channel_id: str, capture_id: str, receipt_id: str,
                     user=Depends(get_current_user), session=Depends(get_session)):
    # The encrypted-file reader verifies the actual Megolm author separately.
    # This projection proves native participant consent; it contains no media,
    # transcript, device keys or guest credentials.
    from ..services.access import require_channel_member
    require_channel_member(session, channel_id, user.id); recheck_actor(session, user.id)
    row = session.get(service.MeetingCaptureReceipt, receipt_id)
    capture = session.get(MeetingCapture, capture_id)
    if not row or row.erased_at is not None or not capture or row.capture_id != capture.id or capture.channel_id != channel_id or not row.clip_id:
        raise HTTPException(404, "Recording publication receipt not found")
    service.lock_capture(session, capture)
    if not service.mode_matches(session, capture) or row.generation != capture.generation:
        raise HTTPException(409, "This recording publication's consent was revoked")
    return publication_projection(capture, row)


@router.get("/meetings/{transport}/{meeting_id}/capture")
def status(transport: str, meeting_id: str, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    _, own, rows = service.native_authority(session, transport, meeting_id, user, credentials.credentials)
    capture = service.active_capture(session, transport, meeting_id)
    if capture:
        service.lock_capture(session, capture); service.refresh_roster(session, capture, rows)
    result = service.public(session, capture, rows, own); session.commit()
    return result


@router.post("/meetings/{transport}/{meeting_id}/capture", status_code=201)
def request_capture(transport: str, meeting_id: str, data: RequestCapture, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    meeting, own, rows = service.native_authority(session, transport, meeting_id, user, credentials.credentials)
    account_limit(session, "meeting-capture-request", user.id, 60, 3600)
    # account_limit commits; reacquire User/Auth→Workspace→Channel authority
    # before reading the current participant roster or creating its children.
    meeting, own, rows = service.native_authority(session, transport, meeting_id, user, credentials.credentials)
    if meeting.host_user_id != user.id:
        raise HTTPException(403, "Only the meeting host can request automatic recording")
    if data.language not in service.configuration().get("languages", []) or data.captions and not service.configuration()["captions"]:
        raise HTTPException(422, "Configure a supported local caption model and language")
    if data.save_transcript and not data.captions:
        raise HTTPException(422, "Enable captions before retaining a transcript")
    content_mode = "matrix" if is_secure_channel(session, meeting.channel_id) else "server"
    expected_audience = "channel" if content_mode == "matrix" else "admitted"
    if data.captions and data.caption_audience != expected_audience:
        raise HTTPException(422, "Review the exact live-caption recipient audience before requesting captions")
    if service.active_capture(session, transport, meeting_id):
        raise HTTPException(409, "A recording request is already active")
    ai_fields = {}
    if data.ai_profile_id or data.ai_profile_revision or data.ai_disclose:
        if not data.ai_profile_id or not data.ai_profile_revision or not data.ai_disclose or not data.captions or not data.save_transcript:
            raise HTTPException(422, "Automatic summaries require reviewed AI disclosure and a retained transcript")
        if is_secure_channel(session, meeting.channel_id):
            raise HTTPException(409, "Encrypted meeting AI stays on the trusted device; no server recording grant is stored")
        from ..services.ai_runtime import resolve_profile
        provider = resolve_profile(session, channel_workspace_id(session, meeting.channel_id), data.ai_profile_id, user.id, data.ai_profile_revision)
        ai_fields = {"ai_enabled": True, "ai_profile_id": data.ai_profile_id, "ai_profile_revision": data.ai_profile_revision,
            "ai_recipient": provider.endpoint, "ai_model": provider.model, "ai_protocol": provider.protocol,
            "ai_expires_at": datetime.utcnow() + timedelta(days=7)}
    capture = MeetingCapture(transport=transport, meeting_id=meeting_id, channel_id=meeting.channel_id, created_by=user.id,
        recorder_participant_id=own.id, roster_digest=service.roster_digest(rows), content_mode=content_mode,
        **data.model_dump(exclude={"ai_profile_id", "ai_profile_revision", "ai_disclose", "caption_audience"}), **ai_fields)
    try:
        session.add(capture); session.flush()
        session.add(MeetingCaptureConsent(capture_id=capture.id, participant_id=own.id, generation=1, granted=True, user_id=user.id))
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "A recording request is already active") from None
    return service.public(session, capture, rows, own)


@router.post("/meetings/{transport}/{meeting_id}/capture/consent")
def vote(transport: str, meeting_id: str, data: Vote, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    _, own, rows = service.native_authority(session, transport, meeting_id, user, credentials.credentials)
    account_limit(session, "meeting-capture-consent", own.id, 120, 3600)
    _, own, rows = service.native_authority(session, transport, meeting_id, user, credentials.credentials)
    capture = session.get(MeetingCapture, data.capture_id)
    if not capture or capture.transport != transport or capture.meeting_id != meeting_id:
        raise HTTPException(404, "Recording request not found")
    service.consent(session, capture, rows, own, data.generation, data.granted); session.commit()
    return service.public(session, capture, rows, own)


@router.post("/meetings/{transport}/{meeting_id}/capture/control")
def control(transport: str, meeting_id: str, data: Control, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    meeting, own, rows = service.native_authority(session, transport, meeting_id, user, credentials.credentials)
    account_limit(session, "meeting-capture-control", user.id, 300, 3600)
    meeting, own, rows = service.native_authority(session, transport, meeting_id, user, credentials.credentials)
    capture = session.get(MeetingCapture, data.capture_id)
    if not capture or capture.transport != transport or capture.meeting_id != meeting_id:
        raise HTTPException(404, "Recording request not found")
    if meeting.host_user_id != user.id or capture.created_by != user.id or capture.recorder_participant_id != own.id:
        raise HTTPException(403, "Only the recording host can control capture")
    service.lock_capture(session, capture); service.refresh_roster(session, capture, rows)
    if capture.generation != data.generation:
        raise HTTPException(409, "Recording participants or consent changed")
    result = {}
    if data.action in {"start", "resume"}:
        if not service.ready(session, capture, rows) or capture.state == "stopped":
            raise HTTPException(409, "Every admitted participant must consent to this recording")
        capture.state = "recording"
    elif data.action == "pause":
        if capture.state == "stopped": raise HTTPException(409, "This recording already stopped")
        capture.state = "paused"
    elif data.action in {"segment", "stop"}:
        previous_receipt = session.exec(select(service.MeetingCaptureReceipt).where(service.MeetingCaptureReceipt.capture_id == capture.id,
            service.MeetingCaptureReceipt.generation == capture.generation, service.MeetingCaptureReceipt.request_id == data.request_id)).first() if data.request_id else None
        if previous_receipt or capture.state == "recording" or data.action == "stop" and capture.state == "paused" and service.ready(session, capture, rows):
            receipt = service.receipt(session, capture, rows, user.id, allow_paused=data.action == "stop", request_id=data.request_id)
            result["publication_receipt"] = receipt.id
        elif data.action == "segment":
            raise HTTPException(409, "Only an actively consented recording can publish a segment")
        if data.action == "stop": capture.state = "stopped"; capture.active_key = None
    capture.updated_at = datetime.utcnow(); capture.revision += 1; session.add(capture); session.commit()
    return {**service.public(session, capture, rows, own), **result}


@router.get("/channels/{channel_id}/meeting-captures/{capture_id}/segments/{request_id}")
def recover_receipt(channel_id: str, capture_id: str, request_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    from ..services.access import require_channel_member
    require_channel_member(session, channel_id, user.id); recheck_actor(session, user.id)
    capture = session.get(MeetingCapture, capture_id)
    row = session.exec(select(service.MeetingCaptureReceipt).where(service.MeetingCaptureReceipt.capture_id == capture_id,
        service.MeetingCaptureReceipt.request_id == request_id, service.MeetingCaptureReceipt.user_id == user.id)).first()
    if not capture or capture.channel_id != channel_id or capture.created_by != user.id or not row:
        raise HTTPException(404, "Recording segment receipt not found")
    if row.erased_at is not None or not service.mode_matches(session, capture) or capture.generation != row.generation or row.expires_at <= datetime.utcnow():
        raise HTTPException(409, "Recording consent changed or the receipt expired")
    return {"publication_receipt": row.id}


@router.get("/meeting-access/{meeting_id}/capture")
def guest_status(meeting_id: str, meeting_token: str = Header(alias="X-Meeting-Token"), session=Depends(get_session)):
    _, own, rows = service.guest_authority(session, meeting_id, meeting_token)
    capture = service.active_capture(session, "mesh", meeting_id)
    if capture:
        service.lock_capture(session, capture); service.refresh_roster(session, capture, rows)
    result = service.public(session, capture, rows, own); session.commit()
    return result


@router.post("/meetings/{transport}/{meeting_id}/capture/caption")
def share_caption(transport: str, meeting_id: str, data: CaptionInput, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    meeting, own, rows = service.native_authority(session, transport, meeting_id, user, credentials.credentials)
    if meeting.host_user_id != user.id:
        raise HTTPException(403, "Only the consented recording host can share captions")
    capture = session.get(MeetingCapture, data.capture_id)
    if not capture or capture.transport != transport or capture.meeting_id != meeting_id or capture.created_by != user.id:
        raise HTTPException(404, "Recording request not found")
    if data.end < data.start:
        raise HTTPException(422, "Caption end precedes its start")
    account_limit(session, "meeting-caption", user.id, 1800, 3600)
    meeting, own, rows = service.native_authority(session, transport, meeting_id, user, credentials.credentials)
    if meeting.host_user_id != user.id or capture.recorder_participant_id != own.id:
        raise HTTPException(403, "Only the exact recording device can share captions")
    service.add_caption(session, capture, rows, data.generation, data.model_dump(include={"start", "end", "text"}))
    session.commit()
    return {"shared": True}


@router.post("/meeting-access/{meeting_id}/capture/consent")
def guest_vote(meeting_id: str, data: Vote, meeting_token: str = Header(alias="X-Meeting-Token"), session=Depends(get_session)):
    _, own, rows = service.guest_authority(session, meeting_id, meeting_token)
    account_limit(session, "meeting-capture-consent", own.id, 120, 3600)
    _, own, rows = service.guest_authority(session, meeting_id, meeting_token)
    capture = session.get(MeetingCapture, data.capture_id)
    if not capture or capture.transport != "mesh" or capture.meeting_id != meeting_id:
        raise HTTPException(404, "Recording request not found")
    service.consent(session, capture, rows, own, data.generation, data.granted); session.commit()
    return service.public(session, capture, rows, own)


@router.post("/channels/{channel_id}/meeting-captures/{capture_id}/ai/revoke")
def revoke_ai(channel_id: str, capture_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    from ..services.access import require_channel_member
    require_channel_member(session, channel_id, user.id); recheck_actor(session, user.id)
    capture = session.get(MeetingCapture, capture_id)
    if not capture or capture.channel_id != channel_id:
        raise HTTPException(404, "Recording request not found")
    participant = session.exec(select(MeetingCaptureConsent).where(MeetingCaptureConsent.capture_id == capture_id,
        MeetingCaptureConsent.user_id == user.id)).first()
    if capture.created_by != user.id and not participant:
        raise HTTPException(403, "Only a recording participant can revoke this AI disclosure")
    service.lock_capture(session, capture); capture.ai_enabled = False; capture.revision += 1; session.add(capture); session.commit()
    return {"revoked": True}


@router.post("/meeting-access/{meeting_id}/capture/{capture_id}/ai/revoke")
def guest_revoke_ai(meeting_id: str, capture_id: str, meeting_token: str = Header(alias="X-Meeting-Token"), session=Depends(get_session)):
    # Revocation remains available after leave/expiry. The old hashed guest
    # capability can only stop a disclosure, never restore access or consent.
    capture = session.get(MeetingCapture, capture_id)
    participant = session.exec(select(MeetingCaptureConsent).where(MeetingCaptureConsent.capture_id == capture_id,
        MeetingCaptureConsent.revoke_token_hash == token_digest(meeting_token))).first()
    if not capture or capture.meeting_id != meeting_id or not participant:
        raise HTTPException(404, "Recording disclosure not found")
    service.lock_capture(session, capture); capture.ai_enabled = False; capture.revision += 1; session.add(capture); session.commit()
    return {"revoked": True}
