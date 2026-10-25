"""Current participant consent gates capture and each immutable clip publication."""
from datetime import datetime, timedelta
import hashlib
import json
import os
import re
from collections import deque
from threading import Lock
from fastapi import HTTPException
from sqlalchemy import update
from sqlmodel import select
from ..models.media_capture import MeetingCapture, MeetingCaptureConsent, MeetingCaptureReceipt, MeetingCaptureAIUse
from ..models.meetings import MeetingParticipant
from ..models.sfu import SFUParticipant, SFURoom
from ..models.user import User
from ..models.account_security import AccountSecurity
from . import meetings, sfu
from .access import require_channel_member, lock_channel_writes
from .auth import token_digest
from .request_guard import recheck_actor
from .workspaces import channel_workspace_id, lock_workspace
from .matrix_bridge import is_secure_channel
from .crypto import encrypt_message, decrypt_message
LANGUAGES = ["auto", "en", "sv", "de", "fr", "es", "it", "nl", "da", "no", "fi"]
_captions = {}
_caption_lock = Lock()


def configuration():
    from ..config import settings
    limits = {"max_part_bytes": 1536 * 1024 if settings.demo_instance else 48 * 1024 * 1024,
              "max_part_seconds": 15 if settings.demo_instance else 299,
              "sample_upload_limit_bytes": 2 * 1024 * 1024 if settings.demo_instance else None,
              "sample_session_limit_bytes": 8 * 1024 * 1024 if settings.demo_instance else None}
    model = os.getenv("MEETING_CAPTION_MODEL", "onnx-community/whisper-tiny")
    base = os.getenv("MEETING_CAPTION_MODEL_BASE", "")
    if model and (not re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)?", model)
                  or base and (base.startswith("//") or not re.fullmatch(r"/[A-Za-z0-9_/-]*/", base) or ".." in base)
                  or not base and model != "onnx-community/whisper-tiny"):
        return {**limits, "captions": False, "engine": "local-whisper-wasm", "model": "", "model_base": "/models/",
                "languages": LANGUAGES, "audio_destination": "this device only", "reason": "The operator's local caption model path is invalid"}
    return {**limits, "captions": bool(model), "engine": "local-whisper-wasm", "model": model, "model_base": base,
            "model_revision": "ff4177021cc41f7db950912b73ea4fdf7d01d8e7",
            "model_origin": "this installation" if base else "Hugging Face (pinned model weights only)",
            "languages": LANGUAGES,
            "audio_destination": "this device only", "reason": "" if model else "The operator must install and configure a local Whisper model"}


def room(session, transport, identifier):
    if transport == "mesh":
        return meetings.live_meeting(session, identifier), MeetingParticipant
    if transport == "livekit":
        return sfu.active_room(session, identifier), SFUParticipant
    raise HTTPException(404, "Meeting transport not found")


def current_participants(session, transport, identifier):
    meeting, model = room(session, transport, identifier)
    rows = session.exec(select(model).where(model.meeting_id == identifier)).all()
    return meeting, [row for row in rows if row.state == "admitted"
                     and not getattr(row, "disconnected", False) and meetings.participant_valid(session, row, meeting)]


def native_authority(session, transport, identifier, user, credential):
    meeting, _ = room(session, transport, identifier)
    lock_actor_scope(session, meeting.channel_id, user.id)
    lock_meeting_scope(session, transport, identifier)
    recheck_actor(session, user.id)
    meeting, rows = current_participants(session, transport, identifier)
    require_channel_member(session, meeting.channel_id, user.id)
    own = next((row for row in rows if row.user_id == user.id and row.auth_token_hash == token_digest(credential)), None)
    if not own:
        raise HTTPException(403, "Join this meeting with the current sign-in before using recording controls")
    return meeting, own, rows


def lock_actor_scope(session, channel_id, actor_id):
    """Match account erasure before inserting user-bound capture/consent FKs."""
    workspace = channel_workspace_id(session, channel_id)
    session.execute(update(User).where(User.id == actor_id).values(name=User.name))
    session.execute(update(AccountSecurity).where(AccountSecurity.user_id == actor_id).values(auth_version=AccountSecurity.auth_version))
    if workspace:
        lock_workspace(session, workspace)
    lock_channel_writes(session, channel_id)
    session.expire_all(); recheck_actor(session, actor_id)


def lock_meeting_scope(session, transport, identifier):
    """The same admission fence as join/leave, before the capture row."""
    if transport == "mesh":
        meetings.lock_meeting(session, identifier)
    elif transport == "livekit":
        changed = session.execute(update(SFURoom).where(SFURoom.id == identifier).values(provisioned=SFURoom.provisioned))
        if changed.rowcount != 1:
            raise HTTPException(404, "Meeting not found")
        session.expire_all()
    else:
        raise HTTPException(404, "Meeting transport not found")


def guest_authority(session, identifier, credential):
    meeting, own = meetings.capability(session, identifier, credential, locked=True)
    _, rows = current_participants(session, "mesh", identifier)
    if own.state != "admitted":
        raise HTTPException(403, "Recording consent is available after admission")
    return meeting, own, rows


def roster_digest(rows):
    # Device/lease IDs are immutable and current participant_valid checks include
    # session revocation, account status, SSO provider and channel membership.
    return hashlib.sha256(json.dumps(sorted(row.id for row in rows), separators=(",", ":")).encode()).hexdigest()


def active_capture(session, transport, identifier):
    return session.exec(select(MeetingCapture).where(MeetingCapture.transport == transport,
        MeetingCapture.meeting_id == identifier, MeetingCapture.state != "stopped")
        .order_by(MeetingCapture.created_at.desc()).limit(1)).first()


def lock_capture(session, capture):
    session.execute(update(MeetingCapture).where(MeetingCapture.id == capture.id).values(updated_at=MeetingCapture.updated_at))
    session.expire(capture); session.refresh(capture)
    return capture


def mode_matches(session, capture):
    return (not capture.policy_invalidated and capture.content_mode in {"server", "matrix"}
            and capture.content_mode == ("matrix" if is_secure_channel(session, capture.channel_id) else "server"))


def invalidate_channel_captures(session, channel_id):
    """Encryption-mode mutation hook; called inside its existing channel fence."""
    for capture in session.exec(select(MeetingCapture).where(MeetingCapture.channel_id == channel_id)).all():
        if not capture.policy_invalidated:
            capture.policy_invalidated = True; capture.generation += 1; capture.revision += 1
            if capture.state != "stopped": capture.state = "paused"
            capture.updated_at = datetime.utcnow(); session.add(capture)


def refresh_roster(session, capture, rows):
    if not mode_matches(session, capture) and not capture.policy_invalidated:
        capture.policy_invalidated = True; capture.generation += 1; capture.revision += 1
        if capture.state != "stopped": capture.state = "paused"
        capture.updated_at = datetime.utcnow(); session.add(capture)
    digest = roster_digest(rows)
    if capture.state != "stopped" and capture.roster_digest != digest:
        capture.generation += 1
        capture.revision += 1
        capture.state = "paused"
        capture.roster_digest = digest
        capture.updated_at = datetime.utcnow()
        session.add(capture)
    return capture


def granted_ids(session, capture, rows):
    allowed = session.exec(select(MeetingCaptureConsent).where(MeetingCaptureConsent.capture_id == capture.id,
        MeetingCaptureConsent.generation == capture.generation, MeetingCaptureConsent.granted == True)).all()  # noqa: E712
    current = {row.id for row in rows}
    return sorted(row.participant_id for row in allowed if row.participant_id in current)


def ready(session, capture, rows):
    return mode_matches(session, capture) and bool(rows) and capture.roster_digest == roster_digest(rows) and set(granted_ids(session, capture, rows)) == {row.id for row in rows}


def public(session, capture, rows, own):
    if not capture:
        return {"capture": None, "participant_id": own.id, "configuration": configuration()}
    granted = granted_ids(session, capture, rows)
    return {"participant_id": own.id, "configuration": configuration(), "capture": {
        "id": capture.id, "generation": capture.generation, "revision": capture.revision, "state": capture.state, "created_by": capture.created_by,
        "recorder_participant_id": capture.recorder_participant_id,
        "content_mode": capture.content_mode, "policy_invalidated": capture.policy_invalidated,
        "caption_audience": "channel" if capture.content_mode == "matrix" else "admitted" if capture.content_mode == "server" else None,
        "video": capture.video, "captions": capture.captions, "save_transcript": capture.save_transcript,
        "language": capture.language, "all_consented": ready(session, capture, rows),
        "consented": granted, "awaiting": [row.id for row in rows if row.id not in granted],
        "participants": [{"id": row.id, "name": row.name} for row in rows],
        "encrypted_captions": is_secure_channel(session, capture.channel_id),
        "live_captions": live_captions(capture) if not is_secure_channel(session, capture.channel_id) else [],
        "ai": {"enabled": capture.ai_enabled, "profile_id": capture.ai_profile_id, "profile_revision": capture.ai_profile_revision,
               "recipient": capture.ai_recipient, "model": capture.ai_model, "protocol": capture.ai_protocol,
               "expires_at": capture.ai_expires_at, "max_segments": capture.ai_max_segments},
    }}


def add_caption(session, capture, rows, generation, data):
    lock_capture(session, capture); refresh_roster(session, capture, rows)
    if capture.generation != generation or capture.state != "recording" or not capture.captions or not ready(session, capture, rows):
        raise HTTPException(409, "Caption sharing requires current recording consent")
    if is_secure_channel(session, capture.channel_id):
        raise HTTPException(409, "Secure meeting captions must remain inside the encrypted Matrix room")
    now = datetime.utcnow()
    with _caption_lock:
        for key in list(_captions):
            if _captions[key][0] < now - timedelta(seconds=45): del _captions[key]
        if capture.id not in _captions and len(_captions) >= 200:
            raise HTTPException(503, "The live-caption lane reached its current bound")
        previous = _captions.get(capture.id, (now, deque(maxlen=12)))[1]
        previous.append(encrypt_message(json.dumps({**data, "generation": generation, "at": now.isoformat()}, separators=(",", ":")),
            "meeting-caption:" + capture.id))
        _captions[capture.id] = (now, previous)


def live_captions(capture):
    with _caption_lock:
        current = _captions.get(capture.id)
        values = list(current[1]) if current and current[0] >= datetime.utcnow() - timedelta(seconds=45) else []
    result = []
    for value in values:
        try:
            row = json.loads(decrypt_message(value, "meeting-caption:" + capture.id))
            if row["generation"] == capture.generation: result.append(row)
        except Exception:
            continue
    return result


def consent(session, capture, rows, own, generation, granted):
    lock_capture(session, capture); refresh_roster(session, capture, rows)
    if capture.policy_invalidated or capture.state == "stopped" or capture.generation != generation:
        raise HTTPException(409, "The recording participants or disclosure changed; review the current request")
    if not granted:
        # Revoke every old permission, including any in-flight segment receipt.
        capture.generation += 1; capture.state = "paused"; capture.updated_at = datetime.utcnow()
        session.add(capture)
    row = session.get(MeetingCaptureConsent, (capture.id, own.id))
    row = row or MeetingCaptureConsent(capture_id=capture.id, participant_id=own.id, generation=capture.generation, granted=granted)
    row.generation = capture.generation; row.granted = granted; row.updated_at = datetime.utcnow()
    capture.revision += 1; session.add(capture)
    row.user_id = own.user_id
    if own.kind == "guest": row.revoke_token_hash = own.credential_hash
    session.add(row)


def receipt(session, capture, rows, user_id, *, allow_paused=False, request_id=None):
    lock_capture(session, capture); refresh_roster(session, capture, rows)
    if request_id:
        previous = session.exec(select(MeetingCaptureReceipt).where(MeetingCaptureReceipt.capture_id == capture.id,
            MeetingCaptureReceipt.generation == capture.generation, MeetingCaptureReceipt.request_id == request_id)).first()
        if previous:
            if previous.erased_at is not None or previous.user_id != user_id or previous.expires_at <= datetime.utcnow():
                raise HTTPException(409, "The recording segment receipt is unavailable")
            return previous
    if capture.state not in ({"recording", "paused"} if allow_paused else {"recording"}) or not ready(session, capture, rows):
        raise HTTPException(409, "Current participant consent is required before saving this segment")
    row = MeetingCaptureReceipt(capture_id=capture.id, generation=capture.generation, channel_id=capture.channel_id,
        user_id=user_id, expires_at=datetime.utcnow() + timedelta(days=7), request_id=request_id)
    session.add(row); session.flush()
    return row


def authorize_publication(session, receipt_id, channel_id, user_id, clip_id):
    row = session.get(MeetingCaptureReceipt, receipt_id, populate_existing=True)
    if not row or row.erased_at is not None or row.channel_id != channel_id or row.user_id != user_id or row.expires_at <= datetime.utcnow():
        raise HTTPException(409, "This recording publication receipt is unavailable or expired")
    capture = session.get(MeetingCapture, row.capture_id, populate_existing=True)
    if not capture:
        raise HTTPException(409, "Recording consent changed before publication")
    lock_meeting_scope(session, capture.transport, capture.meeting_id)
    # Serialize with withdrawal/account erasure even after stop. A stopped
    # capture can still have an in-flight final upload; it is not an immutable
    # authorization snapshot until this transaction commits.
    lock_capture(session, capture)
    if not mode_matches(session, capture):
        raise HTTPException(409, "Channel encryption changed; create a new recording request before publishing")
    if capture.generation != row.generation:
        raise HTTPException(409, "Recording consent changed before publication")
    if capture.state != "stopped":
        _, rows = current_participants(session, capture.transport, capture.meeting_id)
        refresh_roster(session, capture, rows)
        if capture.generation != row.generation or not ready(session, capture, rows):
            raise HTTPException(409, "Recording participants or consent changed before publication")
    if row.clip_id is not None and row.clip_id != clip_id:
        raise HTTPException(409, "This recording receipt already published another clip")
    row.clip_id = clip_id; session.add(row)


def validate_ai_summary_scope(session, workspace_id, channel_id, actor_id, scope, profile_id, profile_revision, source_ids):
    """Rechecked by the AI runtime before queue, attempts, response and approval."""
    from .ai_runtime import resolve_profile
    from ..models.clips import ChannelClip
    if (not isinstance(scope, dict) or scope.get("kind") != "meeting_recording" or len(source_ids) != 1
            or is_secure_channel(session, channel_id) or channel_workspace_id(session, channel_id) != workspace_id):
        raise HTTPException(409, "Meeting summaries need one consented native recording from this workspace")
    require_channel_member(session, channel_id, actor_id); recheck_actor(session, actor_id)
    capture = session.get(MeetingCapture, scope.get("id"), populate_existing=True)
    if not capture:
        raise HTTPException(409, "The recording's AI disclosure was revoked, changed or expired")
    lock_meeting_scope(session, capture.transport, capture.meeting_id)
    lock_capture(session, capture)
    if (capture.content_mode != "server" or not mode_matches(session, capture) or capture.channel_id != channel_id or capture.created_by != actor_id or not capture.ai_enabled
            or capture.generation != scope.get("revision") or capture.ai_profile_id != profile_id
            or capture.ai_profile_revision != profile_revision or not capture.ai_expires_at
            or capture.ai_expires_at <= datetime.utcnow()):
        raise HTTPException(409, "The recording's AI disclosure was revoked, changed or expired")
    if capture.state != "stopped":
        _, rows = current_participants(session, capture.transport, capture.meeting_id)
        refresh_roster(session, capture, rows)
        if capture.generation != scope["revision"] or not ready(session, capture, rows):
            raise HTTPException(409, "Current meeting participants have not consented to this AI disclosure")
    provider = resolve_profile(session, workspace_id, profile_id, actor_id, profile_revision)
    if (provider.endpoint, provider.model, provider.protocol) != (capture.ai_recipient, capture.ai_model, capture.ai_protocol):
        raise HTTPException(409, "The reviewed meeting AI recipient changed")
    clip = session.get(ChannelClip, source_ids[0], populate_existing=True)
    receipt = session.exec(select(MeetingCaptureReceipt).where(MeetingCaptureReceipt.capture_id == capture.id,
        MeetingCaptureReceipt.generation == capture.generation, MeetingCaptureReceipt.clip_id == source_ids[0])).first()
    if not clip or clip.channel_id != channel_id or clip.created_by != actor_id or clip.content_mode != "server" or not receipt or receipt.erased_at is not None:
        raise HTTPException(409, "The selected clip was not published by this consented recording")
    usage = session.get(MeetingCaptureAIUse, (capture.id, clip.id))
    if not usage:
        existing = session.exec(select(MeetingCaptureAIUse.clip_id).where(MeetingCaptureAIUse.capture_id == capture.id)).all()
        if len(existing) >= capture.ai_max_segments:
            raise HTTPException(409, "This recording reached its reviewed AI segment budget")
        session.add(MeetingCaptureAIUse(capture_id=capture.id, clip_id=clip.id)); session.flush()


def close_account_resources(session, actor_id):
    """Privacy transaction helper: no commit, provider or network calls."""
    now = datetime.utcnow()
    own = session.exec(select(MeetingCapture).where(MeetingCapture.created_by == actor_id)).all()
    participations = session.exec(select(MeetingCaptureConsent).where(MeetingCaptureConsent.user_id == actor_id)).all()
    affected = {row.id: row for row in own}
    for consent_row in participations:
        capture = session.get(MeetingCapture, consent_row.capture_id)
        if capture: affected[capture.id] = capture
        consent_row.granted = False; consent_row.user_id = None; consent_row.revoke_token_hash = None
        consent_row.updated_at = now; session.add(consent_row)
    for capture in affected.values():
        capture.generation += 1; capture.ai_enabled = False; capture.ai_expires_at = now
        capture.revision += 1
        capture.updated_at = now
        if capture.created_by == actor_id:
            capture.state = "stopped"; capture.active_key = None
            for row in session.exec(select(MeetingCaptureConsent).where(MeetingCaptureConsent.capture_id == capture.id)).all():
                row.revoke_token_hash = None; session.add(row)
        elif capture.state != "stopped":
            capture.state = "paused"
        session.add(capture)
        with _caption_lock: _captions.pop(capture.id, None)
    for row in session.exec(select(MeetingCaptureReceipt).where(MeetingCaptureReceipt.user_id == actor_id)).all():
        row.expires_at = now; session.add(row)
    return {"captures_invalidated": len(affected), "consents_withdrawn": len(participations)}


def export_capture_metadata(session, channel_ids, actor):
    """Current native-channel scope, IDs/status only; no media/transcript/key data."""
    from ..models.channel import ChannelMember
    actor_id = actor.id if hasattr(actor, "id") else actor
    authorized = set(session.exec(select(ChannelMember.channel_id).where(ChannelMember.user_id == actor_id,
        ChannelMember.channel_id.in_(list(set(channel_ids))[:1000]))).all())
    authorized = {identifier for identifier in authorized if not is_secure_channel(session, identifier)}
    captures = session.exec(select(MeetingCapture).where(MeetingCapture.channel_id.in_(authorized))
        .order_by(MeetingCapture.created_at, MeetingCapture.id)).all() if authorized else []
    consent_rows = session.exec(select(MeetingCaptureConsent).where(MeetingCaptureConsent.user_id == actor_id,
        MeetingCaptureConsent.capture_id.in_([row.id for row in captures]))).all() if captures else []
    receipt_rows = session.exec(select(MeetingCaptureReceipt).where(MeetingCaptureReceipt.user_id == actor_id,
        MeetingCaptureReceipt.channel_id.in_(authorized))).all() if authorized else []
    return {"captures": [{key: getattr(row, key) for key in ("id", "channel_id", "meeting_id", "transport", "created_by",
        "generation", "state", "content_mode", "policy_invalidated", "video", "captions", "save_transcript", "language", "created_at", "updated_at",
        "ai_enabled", "ai_profile_id", "ai_profile_revision", "ai_expires_at", "ai_max_segments")} for row in captures],
        "own_consents": [{"capture_id": row.capture_id, "generation": row.generation, "granted": row.granted,
                          "updated_at": row.updated_at} for row in consent_rows],
        "own_publications": [{"capture_id": row.capture_id, "clip_id": row.clip_id, "created_at": row.created_at,
                              "expires_at": row.expires_at} for row in receipt_rows]}
