"""Native channel meeting controls and separately scoped guest/device access."""
from datetime import datetime, timedelta
import json
import secrets
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, update
from sqlmodel import select

from ..config import settings
from ..database import get_session
from ..models.meetings import Meeting, MeetingInvitation, MeetingParticipant, MeetingSignal
from ..services import meetings as service
from ..services.access import require_channel_member, lock_channel_writes
from ..services.account_security import account_limit
from ..services.auth import get_current_user, security, token_digest
from ..services.crypto import encrypt_message, decrypt_message
from ..services.rate_limit import request_address
from ..services.request_guard import recheck_actor


def no_store(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(tags=["meetings"], dependencies=[Depends(no_store)])


class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GuestJoin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    invitation_token: str = Field(min_length=32, max_length=256)
    name: str = Field(min_length=1, max_length=80)


class Description(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["offer", "answer"]
    sdp: str = Field(min_length=1, max_length=49152)


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate: str = Field(max_length=8192)
    sdpMid: str | None = Field(default=None, max_length=256)
    sdpMLineIndex: int | None = Field(default=None, ge=0, le=128)
    usernameFragment: str | None = Field(default=None, max_length=256)


class Signal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipient_id: str = Field(min_length=1, max_length=64)
    description: Description | None = None
    candidate: Candidate | None = None

    @model_validator(mode="after")
    def one_payload(self):
        if (self.description is None) == (self.candidate is None):
            raise ValueError("Send exactly one description or candidate")
        return self


@router.get("/channels/{channel_id}/meetings")
def list_meetings(channel_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    require_channel_member(session, channel_id, user.id)
    rows = session.exec(select(Meeting).where(Meeting.channel_id == channel_id, Meeting.ended_at.is_(None),
                                            Meeting.expires_at > datetime.utcnow()).order_by(Meeting.created_at.desc()).limit(10)).all()
    result = []
    for meeting in rows:
        try:
            service.live_meeting(session, meeting.id)
            result.append(service.public_meeting(meeting))
        except HTTPException:
            pass
    return result


@router.post("/channels/{channel_id}/meetings", status_code=201)
def create_meeting(channel_id: str, data: Empty, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    account_limit(session, "meeting-create", user.id, 12, 3600)
    service.cleanup_meetings(session)
    lock_channel_writes(session, channel_id)
    recheck_actor(session, user.id)
    require_channel_member(session, channel_id, user.id)
    active = session.exec(select(Meeting.id).where(Meeting.channel_id == channel_id, Meeting.ended_at.is_(None), Meeting.expires_at > datetime.utcnow())).first()
    if active:
        try:
            service.live_meeting(session, active)
        except HTTPException:
            old = session.get(Meeting, active)
            old.ended_at = datetime.utcnow()
            session.add(old)
        else:
            raise HTTPException(409, "This channel already has an active meeting. Join it instead.")
    meeting = Meeting(channel_id=channel_id, host_user_id=user.id, expires_at=datetime.utcnow() + timedelta(seconds=service.CALL_SECONDS))
    session.add(meeting)
    session.flush()
    participant, token = service.native_participant(session, meeting, user, credentials.credentials)
    meeting.host_participant_id = participant.id
    session.add(meeting)
    session.commit()
    return {"meeting": service.public_meeting(meeting), "participant": service.public_participant(participant), "meeting_token": token}


@router.post("/meetings/{meeting_id}/join")
def join_meeting(meeting_id: str, data: Empty, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    meeting = service.live_meeting(session, meeting_id, locked=True)
    recheck_actor(session, user.id)
    participant, token = service.native_participant(session, meeting, user, credentials.credentials)
    session.commit()
    return {"meeting": service.public_meeting(meeting), "participant": service.public_participant(participant), "meeting_token": token}


@router.post("/meetings/{meeting_id}/invites", status_code=201)
def invite_guest(meeting_id: str, data: Empty, user=Depends(get_current_user), session=Depends(get_session)):
    meeting = service.live_meeting(session, meeting_id, locked=True)
    recheck_actor(session, user.id)
    service.require_host(session, meeting, user)
    count = session.exec(select(func.count()).select_from(MeetingInvitation).where(MeetingInvitation.meeting_id == meeting_id)).one()
    if count >= 20:
        raise HTTPException(409, "This meeting has reached its invitation limit")
    token = secrets.token_urlsafe(32)
    invitation = MeetingInvitation(meeting_id=meeting_id, token_hash=token_digest(token),
                                   expires_at=min(meeting.expires_at, datetime.utcnow() + timedelta(minutes=15)))
    session.add(invitation)
    session.commit()
    return {"invitation_token": token, "expires_at": invitation.expires_at,
            "url": settings.public_app_url.rstrip("/") + "/#meeting=" + meeting_id + "&meeting_invite=" + token}


@router.post("/meetings/{meeting_id}/participants/{participant_id}/{action}")
def manage_participant(meeting_id: str, participant_id: str, action: Literal["admit", "remove"], data: Empty,
                       user=Depends(get_current_user), session=Depends(get_session)):
    meeting = service.live_meeting(session, meeting_id, locked=True)
    recheck_actor(session, user.id)
    service.require_host(session, meeting, user)
    participant = session.get(MeetingParticipant, participant_id)
    if not participant or participant.meeting_id != meeting_id or participant.id == meeting.host_participant_id:
        raise HTTPException(404, "Participant not found")
    if action == "admit":
        if participant.state != "waiting" or not service.participant_valid(session, participant, meeting):
            raise HTTPException(409, "This participant is no longer waiting")
        rows = session.exec(select(MeetingParticipant).where(MeetingParticipant.meeting_id == meeting_id, MeetingParticipant.state == "admitted")).all()
        if sum(service.participant_valid(session, row, meeting) for row in rows) >= service.MAX_PEERS:
            raise HTTPException(409, "This meeting is full (six devices)")
        participant.state = "admitted"
    else:
        participant.state = "removed"
    session.add(participant)
    session.commit()
    return service.public_participant(participant)


@router.post("/meetings/{meeting_id}/end")
def end_meeting(meeting_id: str, data: Empty, user=Depends(get_current_user), session=Depends(get_session)):
    meeting = service.live_meeting(session, meeting_id, locked=True)
    recheck_actor(session, user.id)
    service.require_host(session, meeting, user)
    meeting.ended_at = datetime.utcnow()
    session.add(meeting)
    session.commit()
    return {"ended": True}


@router.post("/meeting-access/{meeting_id}/guest", status_code=201)
def guest_join(meeting_id: str, data: GuestJoin, request: Request, session=Depends(get_session)):
    account_limit(session, "meeting-guest-address", request_address(request), 30, 300)
    meeting = service.live_meeting(session, meeting_id, locked=True)
    invitation = session.exec(select(MeetingInvitation).where(MeetingInvitation.meeting_id == meeting_id,
        MeetingInvitation.token_hash == token_digest(data.invitation_token), MeetingInvitation.used_at.is_(None),
        MeetingInvitation.expires_at > datetime.utcnow())).first()
    if not invitation:
        raise HTTPException(401, "This guest invitation is invalid or has expired")
    name = data.name.strip()
    if not name:
        raise HTTPException(422, "Enter your name")
    count = session.exec(select(func.count()).select_from(MeetingParticipant).where(MeetingParticipant.meeting_id == meeting_id)).one()
    if count >= service.MAX_PARTICIPANTS:
        raise HTTPException(409, "This meeting has reached its participant limit")
    changed = session.exec(update(MeetingInvitation).where(MeetingInvitation.id == invitation.id,
        MeetingInvitation.used_at.is_(None)).values(used_at=datetime.utcnow()))
    if changed.rowcount != 1:
        raise HTTPException(401, "This guest invitation has already been used")
    token = secrets.token_urlsafe(32)
    participant = MeetingParticipant(meeting_id=meeting_id, name=name, kind="guest", state="waiting",
        credential_hash=token_digest(token), expires_at=meeting.expires_at)
    session.add(participant)
    session.commit()
    return {"meeting_id": meeting_id, "participant": service.public_participant(participant), "meeting_token": token}


@router.get("/meeting-access/{meeting_id}/poll")
def poll(meeting_id: str, after: int = Query(default=0, ge=0, le=2147483647), x_meeting_token: str = Header(default="", max_length=256), session=Depends(get_session)):
    meeting, participant = service.capability(session, meeting_id, x_meeting_token, locked=True)
    service.cleanup_signals(session)
    participant.last_seen_at = datetime.utcnow()
    session.add(participant)
    result = {"meeting_id": meeting_id, "participant_id": participant.id, "state": participant.state,
              "is_host": participant.id == meeting.host_participant_id, "expires_at": meeting.expires_at,
              "encryption": "webrtc-dtls-srtp", "participants": [], "signals": [], "cursor": meeting.signal_sequence, "ice_servers": []}
    if participant.state == "admitted":
        rows = session.exec(select(MeetingParticipant).where(MeetingParticipant.meeting_id == meeting_id,
            MeetingParticipant.state.in_(["waiting", "admitted"]))).all()
        valid = [row for row in rows if service.participant_valid(session, row, meeting)]
        result["participants"] = [service.public_participant(row) for row in valid
                                  if row.state == "admitted" or participant.id == meeting.host_participant_id]
        valid_ids = {row.id for row in valid if row.state == "admitted"}
        signals = session.exec(select(MeetingSignal).where(MeetingSignal.meeting_id == meeting_id,
            MeetingSignal.recipient_id == participant.id, MeetingSignal.sequence > after,
            MeetingSignal.created_at >= datetime.utcnow() - timedelta(seconds=service.SIGNAL_SECONDS))
            .order_by(MeetingSignal.sequence).limit(100)).all()
        result["signals"] = [{"sequence": row.sequence, "sender_id": row.sender_id,
            "payload": json.loads(decrypt_message(row.encrypted_payload, "meeting:" + meeting_id + ":" + participant.id))}
            for row in signals if row.sender_id in valid_ids]
        if len(signals) == 100:
            result["cursor"] = signals[-1].sequence
        result["ice_servers"] = service.ice_servers()
    session.commit()
    return result


@router.post("/meeting-access/{meeting_id}/resume", status_code=201)
def resume_guest(meeting_id: str, data: Empty, x_meeting_token: str = Header(default="", max_length=256), session=Depends(get_session)):
    if not 32 <= len(x_meeting_token) <= 256:
        raise HTTPException(401, "A valid guest meeting credential is required")
    previous = session.exec(select(MeetingParticipant).where(MeetingParticipant.meeting_id == meeting_id,
        MeetingParticipant.credential_hash == token_digest(x_meeting_token))).first()
    if not previous:
        raise HTTPException(401, "A valid guest meeting credential is required")
    meeting = service.live_meeting(session, meeting_id, locked=True)
    session.refresh(previous)
    if (previous.kind != "guest" or previous.state not in {"waiting", "admitted", "left"}
            or previous.expires_at <= datetime.utcnow()
            or previous.credential_hash != token_digest(x_meeting_token)):
        raise HTTPException(401, "This guest access cannot be resumed. Ask the host for a new invitation.")
    count = session.exec(select(func.count()).select_from(MeetingParticipant).where(MeetingParticipant.meeting_id == meeting_id)).one()
    if count >= service.MAX_PARTICIPANTS:
        raise HTTPException(409, "This meeting has reached its participant limit")
    token = secrets.token_urlsafe(32)
    previous.state = "left"
    previous.credential_hash = token_digest(secrets.token_urlsafe(32))
    participant = MeetingParticipant(meeting_id=meeting_id, name=previous.name, kind="guest", state="waiting",
        credential_hash=token_digest(token), expires_at=meeting.expires_at)
    session.add_all([previous, participant])
    session.commit()
    return {"meeting_id": meeting_id, "participant": service.public_participant(participant), "meeting_token": token}


@router.post("/meeting-access/{meeting_id}/signal", status_code=202)
def signal(meeting_id: str, data: Signal, x_meeting_token: str = Header(default="", max_length=256), session=Depends(get_session)):
    meeting, participant = service.capability(session, meeting_id, x_meeting_token, locked=True)
    if participant.state != "admitted" or not participant.media_ready:
        raise HTTPException(403, "Wait for the host to admit you")
    recipient = session.get(MeetingParticipant, data.recipient_id, populate_existing=True)
    if (not recipient or recipient.id == participant.id or recipient.meeting_id != meeting_id
            or recipient.state != "admitted" or not recipient.media_ready or not service.participant_valid(session, recipient, meeting)):
        raise HTTPException(404, "Active meeting participant not found")
    service.cleanup_signals(session)
    recent = session.exec(select(func.count()).select_from(MeetingSignal).where(MeetingSignal.sender_id == participant.id,
        MeetingSignal.created_at > datetime.utcnow() - timedelta(minutes=1))).one()
    total = session.exec(select(func.count()).select_from(MeetingSignal).where(MeetingSignal.meeting_id == meeting_id)).one()
    if recent >= 120 or total >= 3000:
        raise HTTPException(429, "Signalling limit reached. Wait before retrying.", headers={"Retry-After": "60"})
    meeting.signal_sequence += 1
    payload = data.model_dump(mode="json", exclude={"recipient_id"}, exclude_none=True)
    row = MeetingSignal(meeting_id=meeting_id, sequence=meeting.signal_sequence, sender_id=participant.id,
        recipient_id=recipient.id, encrypted_payload=encrypt_message(json.dumps(payload, separators=(",", ":")), "meeting:" + meeting_id + ":" + recipient.id))
    session.add_all([meeting, row])
    session.commit()
    return {"accepted": True, "sequence": row.sequence}


@router.post("/meeting-access/{meeting_id}/ready")
def ready(meeting_id: str, data: Empty, x_meeting_token: str = Header(default="", max_length=256), session=Depends(get_session)):
    meeting, participant = service.capability(session, meeting_id, x_meeting_token, locked=True)
    if participant.state != "admitted":
        raise HTTPException(403, "Wait for the host to admit you")
    participant.media_ready = True
    session.add(participant)
    session.commit()
    return {"ready": True}


@router.post("/meeting-access/{meeting_id}/leave")
def leave(meeting_id: str, data: Empty, x_meeting_token: str = Header(default="", max_length=256), session=Depends(get_session)):
    meeting, participant = service.capability(session, meeting_id, x_meeting_token, locked=True)
    participant.state = "left"
    session.add(participant)
    if participant.id == meeting.host_participant_id:
        meeting.ended_at = datetime.utcnow()
        session.add(meeting)
    session.commit()
    return {"left": True}
