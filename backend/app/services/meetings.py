"""Bounded WebRTC signalling and call-only guest admission.

Only opaque device capabilities are stored as hashes. Native participant access
continually checks the original login, provider revision, account and channel.
SDP/ICE is routing metadata encrypted at rest; browser media is never received.
"""
import base64
from datetime import datetime, timedelta
import hashlib
import hmac
import json
import secrets
import time

from fastapi import HTTPException
from sqlalchemy import delete, or_, update
from sqlmodel import select

from ..config import settings
from ..models.account_security import AccountSecurity
from ..models.channel import ChannelMember
from ..models.meetings import Meeting, MeetingInvitation, MeetingParticipant, MeetingSignal, MeetingParticipantAuthority
from nacl.exceptions import CryptoError
from ..models.session import ActiveSession, RevokedToken
from ..models.user import User
from ..services.access import require_channel_member, lock_channel_writes
from ..services.auth import active_user_for_token, decode_token_claims, token_digest
from ..services.crypto import encrypt_message, decrypt_message
from ..services.sso_scope import bind_scope_for_hash

MAX_PEERS = 6
MAX_PARTICIPANTS = 24
CALL_SECONDS = 2 * 60 * 60
SIGNAL_SECONDS = 5 * 60
AUTHORITY_CLAIMS = ("sub", "iat", "exp", "av", "amr", "sso_workspace_id", "sso_provider_revision",
                    "native_client_id", "embed_application_id", "jti", "native_call_device_id", "native_call_session_id")


def bind_participant_authority(session, participant, claims, *, transport):
    """Called only with a freshly verified JWT after current actor checks."""
    if transport not in {"mesh", "sfu"} or claims.get("sub") != participant.user_id:
        raise HTTPException(401, "Invalid meeting sign-in authority")
    snapshot = {key: claims[key] for key in AUTHORITY_CLAIMS if key in claims}
    payload = {"user_id": participant.user_id, "meeting_id": participant.meeting_id,
               "token_hash": participant.auth_token_hash, "claims": snapshot}
    session.add(MeetingParticipantAuthority(transport=transport, participant_id=participant.id,
        meeting_id=participant.meeting_id, user_id=participant.user_id, token_hash=participant.auth_token_hash,
        encrypted_claims=encrypt_message(json.dumps(payload, separators=(",", ":")),
            "meeting-authority:" + transport + ":" + participant.id)))


def participant_claims(session, participant):
    transport = "mesh" if isinstance(participant, MeetingParticipant) else "sfu"
    row = session.get(MeetingParticipantAuthority, (transport, participant.id), populate_existing=True)
    if (not row or row.token_hash != participant.auth_token_hash or row.user_id != participant.user_id
            or row.meeting_id != participant.meeting_id):
        return None
    try:
        payload = json.loads(decrypt_message(row.encrypted_claims, "meeting-authority:" + transport + ":" + participant.id))
        if (payload.get("user_id"), payload.get("meeting_id"), payload.get("token_hash")) != (participant.user_id, participant.meeting_id, participant.auth_token_hash):
            return None
        claims = payload.get("claims")
        if (not isinstance(claims, dict) or claims.get("sub") != participant.user_id
                or not isinstance(claims.get("exp"), (int, float)) or isinstance(claims["exp"], bool)
                or claims["exp"] <= time.time() or claims.get("av", 0) != participant.auth_version
                or claims.get("sso_workspace_id") != participant.scope_workspace_id
                or claims.get("sso_provider_revision") != participant.scope_provider_revision):
            return None
        return claims
    except (CryptoError, ValueError, TypeError, AttributeError, KeyError):
        return None


def fresh_user(session, user_id):
    return session.get(User, user_id, populate_existing=True)


def participant_valid(session, participant, meeting):
    now = datetime.utcnow()
    if participant.state not in {"waiting", "admitted"} or participant.expires_at <= now:
        return False
    if participant.kind == "guest":
        return True
    user = fresh_user(session, participant.user_id)
    active = session.exec(select(ActiveSession.user_id, ActiveSession.revoked_at, ActiveSession.expires_at).where(
        ActiveSession.token_hash == participant.auth_token_hash)).first()
    version = session.exec(select(AccountSecurity.auth_version).where(AccountSecurity.user_id == participant.user_id)).first()
    if (not user or user.is_blocked or not active or active[0] != participant.user_id
            or active[1] is not None or active[2] <= now or (version or 0) != participant.auth_version
            or session.get(RevokedToken, participant.auth_token_hash)):
        return False
    old_info = session.info.copy()
    try:
        # Never reconstruct restrictions from a mutable/missing child sidecar.
        # An absent snapshot also fences legacy in-progress calls after upgrade.
        claims = participant_claims(session, participant)
        if claims is None:
            return False
        if "native_call_device_id" in claims or "native_call_session_id" in claims:
            from .native_calls import participant_link_live
            if not participant_link_live(session, participant, claims):
                return False
        if not bind_scope_for_hash(session, participant.auth_token_hash, participant.user_id, claims=claims):
            return False
        require_channel_member(session, meeting.channel_id, participant.user_id)
        return True
    except HTTPException:
        return False
    finally:
        session.info.clear()
        session.info.update(old_info)


def lock_meeting(session, identifier):
    changed = session.execute(update(Meeting).where(Meeting.id == identifier).values(signal_sequence=Meeting.signal_sequence))
    if changed.rowcount != 1:
        raise HTTPException(404, "Meeting not found")
    session.expire_all()
    return session.get(Meeting, identifier)


def live_meeting(session, identifier, *, locked=False):
    meeting = lock_meeting(session, identifier) if locked else session.get(Meeting, identifier, populate_existing=True)
    if not meeting:
        raise HTTPException(404, "Meeting not found")
    if meeting.ended_at or meeting.expires_at <= datetime.utcnow():
        raise HTTPException(410, "This meeting has ended")
    host = session.get(MeetingParticipant, meeting.host_participant_id, populate_existing=True)
    if not host or host.state != "admitted" or not participant_valid(session, host, meeting):
        raise HTTPException(410, "The meeting host no longer has access")
    return meeting


def native_participant(session, meeting, user, native_token):
    now = datetime.utcnow()
    current = active_user_for_token(native_token, session)
    if current is None or current.id != user.id:
        raise HTTPException(401, "Your sign-in has changed. Sign in again before joining.")
    user = current
    require_channel_member(session, meeting.channel_id, user.id)
    rows = session.exec(select(MeetingParticipant).where(MeetingParticipant.meeting_id == meeting.id)).all()
    if any(row.user_id == user.id and row.state == "removed" for row in rows):
        raise HTTPException(403, "The host removed this account from the meeting")
    if any(row.user_id == user.id and row.auth_token_hash == token_digest(native_token)
           and row.state == "admitted" and participant_valid(session, row, meeting) for row in rows):
        raise HTTPException(409, "This sign-in already has a device in the meeting. Leave that device before joining again.")
    admitted = [row for row in rows if row.state == "admitted" and participant_valid(session, row, meeting)]
    if len(admitted) >= MAX_PEERS:
        raise HTTPException(409, "This meeting is full (six devices)")
    if len(rows) >= MAX_PARTICIPANTS:
        raise HTTPException(409, "This meeting has reached its participant limit")
    claims = decode_token_claims(native_token)
    if not claims:
        raise HTTPException(401, "Sign in again before joining")
    credential = secrets.token_urlsafe(32)
    row = MeetingParticipant(meeting_id=meeting.id, name=user.name[:80], credential_hash=token_digest(credential), expires_at=meeting.expires_at)
    row.name, row.kind, row.user_id, row.state = user.name[:80], "member", user.id, "admitted"
    row.media_ready = True
    row.credential_hash, row.auth_token_hash = token_digest(credential), token_digest(native_token)
    row.auth_version = claims.get("av", 0)
    row.scope_workspace_id = claims.get("sso_workspace_id")
    row.scope_provider_revision = claims.get("sso_provider_revision")
    row.last_seen_at = now
    session.add(row)
    session.flush()
    bind_participant_authority(session, row, claims, transport="mesh")
    session.flush()
    return row, credential


def capability(session, identifier, credential, *, locked=False):
    if not isinstance(credential, str) or not 32 <= len(credential) <= 256:
        raise HTTPException(401, "A valid meeting credential is required")
    participant = session.exec(select(MeetingParticipant).where(
        MeetingParticipant.meeting_id == identifier,
        MeetingParticipant.credential_hash == token_digest(credential))).first()
    if not participant:
        raise HTTPException(401, "A valid meeting credential is required")
    meeting = live_meeting(session, identifier, locked=locked)
    session.refresh(participant)
    if not hmac.compare_digest(participant.credential_hash, token_digest(credential)):
        raise HTTPException(401, "Meeting access has changed. Request to join again.")
    if not participant_valid(session, participant, meeting):
        raise HTTPException(401, "Meeting access has ended. Sign in again or contact the host.")
    return meeting, participant


def require_host(session, meeting, user):
    require_channel_member(session, meeting.channel_id, user.id)
    if user.id != meeting.host_user_id:
        raise HTTPException(403, "Only this meeting's host can manage participants")


def public_meeting(meeting):
    return {"id": meeting.id, "channel_id": meeting.channel_id, "created_at": meeting.created_at,
            "expires_at": meeting.expires_at, "host_user_id": meeting.host_user_id,
            "ended": bool(meeting.ended_at or meeting.expires_at <= datetime.utcnow()),
            "max_participants": MAX_PEERS, "encryption": "webrtc-dtls-srtp"}


def public_participant(row):
    return {"id": row.id, "name": row.name, "kind": row.kind, "state": row.state, "media_ready": row.media_ready}


def cleanup_signals(session):
    old = session.exec(select(MeetingSignal.id).where(MeetingSignal.created_at < datetime.utcnow() - timedelta(seconds=SIGNAL_SECONDS))
                       .order_by(MeetingSignal.created_at).limit(500)).all()
    if old:
        session.exec(delete(MeetingSignal).where(MeetingSignal.id.in_(old)))


def cleanup_meetings(session):
    cutoff = datetime.utcnow() - timedelta(days=1)
    identifiers = session.exec(select(Meeting.id).where(or_(Meeting.expires_at < cutoff, Meeting.ended_at < cutoff))
                               .order_by(Meeting.created_at).limit(20)).all()
    if identifiers:
        session.exec(delete(MeetingParticipantAuthority).where(MeetingParticipantAuthority.transport == "mesh",
            MeetingParticipantAuthority.meeting_id.in_(identifiers)))
        for model in (MeetingSignal, MeetingInvitation, MeetingParticipant):
            session.exec(delete(model).where(model.meeting_id.in_(identifiers)))
        session.exec(delete(Meeting).where(Meeting.id.in_(identifiers)))


def ice_servers():
    servers = []
    for url in getattr(settings, "meetings_ice_servers", []):
        if isinstance(url, str) and url.startswith(("stun:", "stuns:")) and len(url) <= 512:
            servers.append({"urls": url})
    secret = getattr(settings, "meetings_turn_secret", None)
    urls = getattr(settings, "meetings_turn_urls", [])
    if secret and urls:
        username = str(int(time.time()) + CALL_SECONDS + 300) + ":freecollab"
        credential = base64.b64encode(hmac.new(secret.get_secret_value().encode(), username.encode(), hashlib.sha1).digest()).decode()
        servers.append({"urls": [url for url in urls if url.startswith(("turn:", "turns:"))],
                        "username": username, "credential": credential})
    return servers[:8]
