"""Native audio admission bounded by original mobile sign-in and device consent.

Only native calls are authorized by this capability. Matrix keys/messages and
normal account APIs remain unavailable. Customer LiveKit can access media.
"""
from datetime import datetime, timedelta
import hmac
import json
import re
import secrets
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, func, update
from sqlmodel import select

from ..config import settings
from ..models.native_calls import NativeCallConsent, NativeCallDevice, NativeCallSession
from ..models.native_push import NativePushDelivery, NativePushDevice
from ..models.secure_channel import SecureChannel
from ..models.sfu import SFUParticipant, SFURoom
from ..models.user import User
from . import native_push, sfu
from .access import lock_channel_writes, require_channel_member
from .account_security import digest, seal_private, unseal_private
from .auth import token_digest
from .meetings import bind_participant_authority, participant_valid
from .request_guard import remember_request, recheck_actor

HEARTBEAT_SECONDS = 5
OFFLINE_SECONDS = 15


def configured():
    enabled = bool(getattr(settings, "native_calls_enabled", False))
    if enabled:
        try:
            sfu.configuration()
        except HTTPException:
            enabled = False
    return {"enabled": enabled, "transport": "customer-livekit-native-audio", "audio_e2ee": False,
            "matrix_message_e2ee": "unchanged", "explicit_answer_required": True,
            "secure_channel_media_consent_required": True, "heartbeat_seconds": HEARTBEAT_SECONDS,
            "max_offline_seconds": OFFLINE_SECONDS}


def require_configured():
    if not configured()["enabled"]:
        raise HTTPException(503, "Native calling needs the operator's configured LiveKit service")


def claims_for(row):
    return json.loads(unseal_private(row.encrypted_claims, "native-call-source", row.id))


def live_device(session, identifier, *, channel_id=None):
    require_configured()
    session.expire_all()
    row = session.get(NativeCallDevice, identifier, populate_existing=True)
    if not row or not row.active or row.expires_at <= datetime.utcnow():
        raise HTTPException(401, "Native calling access ended")
    push = native_push.device_live(session, row.push_device_id, channel_id=channel_id)
    if (not push or not push.calls_enabled or push.auth_token_hash != row.source_hash
            or push.confirmed_at != row.push_confirmed_at or push.user_id != row.user_id or push.platform != row.platform):
        raise HTTPException(401, "The original mobile sign-in or notification device changed")
    claims = claims_for(row)
    # Retain the complete independently authenticated snapshot; missing native
    # child/scope sidecars must never broaden a background capability.
    from .sso_scope import bind_scope_for_hash
    if claims != native_push.claims_for(push) or not bind_scope_for_hash(session, row.source_hash, row.user_id, claims=claims):
        raise HTTPException(401, "The original mobile sign-in changed")
    remember_request(session, row.source_hash, claims)
    if channel_id:
        require_channel_member(session, channel_id, row.user_id)
    return row


def authenticate(session, identifier, secret, *, live=True):
    if (not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier)
            or not isinstance(secret, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", secret)):
        raise HTTPException(401, "Native calling credential is invalid")
    row = session.get(NativeCallDevice, identifier, populate_existing=True)
    if not row or not hmac.compare_digest(row.credential_hash, digest(secret)):
        raise HTTPException(401, "Native calling credential is invalid")
    return live_device(session, identifier) if live else row


def register(session, user_id, token, push_id):
    require_configured()
    from ..api.native_push import lock_actor
    lock_actor(session, user_id)
    push = native_push.device_live(session, push_id)
    if (not push or push.user_id != user_id or not push.calls_enabled
            or push.auth_token_hash != token_digest(token)):
        raise HTTPException(403, "Enable and confirm call alerts on this exact mobile sign-in first")
    claims = native_push.claims_for(push)
    revoke_devices(session, push_device_id=push_id)
    secret = secrets.token_urlsafe(32)
    row = NativeCallDevice(user_id=user_id, push_device_id=push_id, source_hash=push.auth_token_hash,
        push_confirmed_at=push.confirmed_at, encrypted_claims="", credential_hash=digest(secret), platform=push.platform,
        expires_at=datetime.utcfromtimestamp(claims["exp"]))
    row.encrypted_claims = seal_private(json.dumps(claims, separators=(",", ":")), "native-call-source", row.id)
    session.add(row); session.commit()
    return {"device_id": row.id, "call_secret": secret, "expires_at": row.expires_at,
            "heartbeat_seconds": HEARTBEAT_SECONDS, "max_offline_seconds": OFFLINE_SECONDS,
            "transport": "livekit", "audio_e2ee": False}


def revoke_devices(session, *, user_id=None, push_device_id=None, device_id=None):
    query = select(NativeCallDevice)
    if user_id is not None: query = query.where(NativeCallDevice.user_id == user_id)
    elif push_device_id is not None: query = query.where(NativeCallDevice.push_device_id == push_device_id)
    elif device_id is not None: query = query.where(NativeCallDevice.id == device_id)
    else: raise ValueError("Select one native calling owner or device")
    rows = session.exec(query).all()
    owners = {row.user_id for row in rows}
    if user_id is not None: owners.add(user_id)
    elif push_device_id is not None:
        owner = session.exec(select(NativePushDevice.user_id).where(NativePushDevice.id == push_device_id)).first()
        if owner: owners.add(owner)
    # Serialize revoke against call registration/admission using the same User
    # first order as closure. A late admission cannot survive a completed revoke
    # or turn device erasure into a foreign-key failure on PostgreSQL.
    for owner in sorted(owners):
        session.exec(update(User).where(User.id == owner).values(name=User.name))
    session.expire_all()
    rows = session.exec(query).all()
    identifiers = [row.id for row in rows]
    if not identifiers: return 0
    calls = session.exec(select(NativeCallSession).where(NativeCallSession.device_id.in_(identifiers))).all()
    participants = [row.participant_id for row in calls]
    if participants:
        session.exec(update(SFUParticipant).where(SFUParticipant.id.in_(participants)).values(state="left", expires_at=datetime.utcnow()))
    session.exec(delete(NativeCallConsent).where(NativeCallConsent.device_id.in_(identifiers)))
    session.exec(delete(NativeCallSession).where(NativeCallSession.device_id.in_(identifiers)))
    session.exec(delete(NativeCallDevice).where(NativeCallDevice.id.in_(identifiers)))
    session.flush()
    return len(identifiers)


def consent_required(session, device_id, channel_id):
    secure = session.get(SecureChannel, channel_id, populate_existing=True)
    if not secure or secure.status != "active": return False
    consent = session.get(NativeCallConsent, (device_id, channel_id), populate_existing=True)
    return not (consent and consent.matrix_room_id == secure.matrix_room_id and consent.expires_at > datetime.utcnow())


def acknowledge_media(session, device_id, channel_id):
    device = live_device(session, device_id, channel_id=channel_id)
    secure = session.get(SecureChannel, channel_id, populate_existing=True)
    if not secure or secure.status != "active":
        return {"required": False, "audio_e2ee": False}
    row = session.get(NativeCallConsent, (device_id, channel_id)) or NativeCallConsent(
        device_id=device_id, channel_id=channel_id, matrix_room_id=secure.matrix_room_id, expires_at=device.expires_at)
    row.matrix_room_id, row.expires_at, row.created_at = secure.matrix_room_id, device.expires_at, datetime.utcnow()
    session.add(row); session.commit()
    return {"required": True, "acknowledged": True, "audio_e2ee": False, "matrix_message_e2ee": "unchanged"}


def incoming(session, device_id, delivery_id):
    device = live_device(session, device_id)
    delivery = session.get(NativePushDelivery, delivery_id, populate_existing=True)
    if (not delivery or delivery.device_id != device.push_device_id or delivery.kind != "incoming_call"
            or delivery.transport != "livekit" or not native_push.activity_live(session, delivery, session.get(NativePushDevice, device.push_device_id))):
        raise HTTPException(410, "This native incoming call is unavailable")
    live_device(session, device_id, channel_id=delivery.channel_id)
    if consent_required(session, device_id, delivery.channel_id):
        raise HTTPException(409, "Review this encrypted channel's native audio transport in the foreground first")
    return device, delivery


def offer(session, device_id, delivery_id):
    _, delivery = incoming(session, device_id, delivery_id)
    return {"call_id": delivery.id, "room_id": delivery.meeting_id, "expires_at": delivery.expires_at,
            "display_name": "FreeCollab call", "video": False, "audio_e2ee": False}


def join(session, device_id, *, room_id=None, delivery_id=None, client_call_id):
    device = live_device(session, device_id)
    if delivery_id:
        _, delivery = incoming(session, device_id, delivery_id)
        room_id = delivery.meeting_id
    room = sfu.active_room(session, room_id)
    # Match closure's user-first serialization, then the existing channel/SFU
    # admission fences. The capability never becomes a general bearer JWT.
    session.exec(update(User).where(User.id == device.user_id).values(name=User.name))
    lock_channel_writes(session, room.channel_id)
    device = live_device(session, device_id, channel_id=room.channel_id)
    recheck_actor(session, device.user_id)
    if consent_required(session, device_id, room.channel_id):
        raise HTTPException(409, "Review this encrypted channel's native audio transport in the foreground first")
    sfu.lock_admission(session)
    room = sfu.active_room(session, room_id)
    existing = session.exec(select(NativeCallSession).where(NativeCallSession.device_id == device_id,
        NativeCallSession.client_call_id == client_call_id)).first()
    if existing:
        if existing.room_id != room_id or existing.delivery_id != delivery_id or existing.ended_at:
            raise HTTPException(409, "This native call identifier cannot be reused")
        return access(session, existing, device)
    other = session.exec(select(NativeCallSession).where(NativeCallSession.device_id == device_id,
        NativeCallSession.ended_at.is_(None), NativeCallSession.expires_at > datetime.utcnow())).first()
    if other: raise HTTPException(409, "End the current native call before joining another")
    participants = session.exec(select(SFUParticipant).where(SFUParticipant.meeting_id == room_id)).all()
    if any(row.user_id == device.user_id and row.state == "removed" for row in participants):
        raise HTTPException(403, "The host removed this account from the meeting")
    if any(row.user_id == device.user_id and row.auth_token_hash == device.source_hash and row.state == "admitted"
           and participant_valid(session, row, room) for row in participants):
        raise HTTPException(409, "This sign-in already has a device in this meeting")
    if sum(row.state == "admitted" and participant_valid(session, row, room) for row in participants) >= room.max_participants:
        raise HTTPException(409, "This meeting is full")
    if len(participants) >= room.max_participants * 10:
        raise HTTPException(409, "Start a new room after this room's device lease limit")
    user = session.get(User, device.user_id)
    claims = claims_for(device)
    participant = SFUParticipant(meeting_id=room_id, user_id=user.id, name=user.name[:80], auth_token_hash=device.source_hash,
        auth_version=claims.get("av", 0), scope_workspace_id=claims.get("sso_workspace_id"),
        scope_provider_revision=claims.get("sso_provider_revision"), expires_at=min(room.expires_at, device.expires_at))
    session.add(participant); session.flush()
    call = NativeCallSession(device_id=device.id, participant_id=participant.id, room_id=room.id, channel_id=room.channel_id,
        client_call_id=client_call_id, delivery_id=delivery_id, expires_at=participant.expires_at)
    session.add(call); session.flush()
    bind_participant_authority(session, participant, {**claims, "native_call_device_id": device.id,
        "native_call_session_id": call.id}, transport="sfu")
    session.commit()
    return access(session, call, device)


def participant_link_live(session, participant, claims):
    identifier = claims.get("native_call_session_id")
    device_id = claims.get("native_call_device_id")
    if not identifier or not device_id: return False
    call = session.get(NativeCallSession, identifier, populate_existing=True)
    if (not call or call.device_id != device_id or call.participant_id != participant.id or call.ended_at
            or call.expires_at <= datetime.utcnow() or call.room_id != participant.meeting_id
            or call.heartbeat_at < datetime.utcnow() - timedelta(seconds=OFFLINE_SECONDS)):
        return False
    try:
        device = live_device(session, device_id, channel_id=call.channel_id)
        return (device.source_hash == participant.auth_token_hash and device.user_id == participant.user_id
            and not consent_required(session, device.id, call.channel_id))
    except HTTPException:
        return False


def live_call(session, device_id, identifier):
    device = live_device(session, device_id)
    call = session.get(NativeCallSession, identifier, populate_existing=True)
    if not call or call.device_id != device_id or call.ended_at or call.expires_at <= datetime.utcnow():
        raise HTTPException(410, "Native call access ended")
    room = sfu.active_room(session, call.room_id)
    live_device(session, device_id, channel_id=call.channel_id)
    participant = session.get(SFUParticipant, call.participant_id, populate_existing=True)
    if not participant or participant.disconnected or not participant_valid(session, participant, room):
        raise HTTPException(410, "Native call access ended")
    return device, call, participant


def access(session, call, device):
    device, call, participant = live_call(session, device.id, call.id)
    return {"session_id": call.id, "participant_id": participant.id, "room_id": call.room_id,
        "url": sfu.configuration()[0], "token": sfu.encode({"roomJoin": True, "room": sfu.room_name(call.room_id),
            "canPublish": True, "canSubscribe": True, "canPublishData": False, "canPublishSources": ["microphone"]}, subject=participant.id, name=participant.name),
        "token_expires_in": 60, "expires_at": call.expires_at, "muted": call.muted, "held": call.held,
        "heartbeat_seconds": HEARTBEAT_SECONDS, "max_offline_seconds": OFFLINE_SECONDS, "audio_e2ee": False}


def heartbeat(session, device_id, identifier, *, muted=None, held=None):
    _, call, participant = live_call(session, device_id, identifier)
    if not sfu.status(session)["ready"]:
        raise HTTPException(503, "Native call authorization worker is unavailable")
    if muted is not None: call.muted = muted
    if held is not None: call.held = held
    call.heartbeat_at = participant.last_seen_at = datetime.utcnow()
    session.add(call); session.add(participant); session.commit()
    return {"active": True, "muted": call.muted, "held": call.held, "expires_at": call.expires_at,
            "heartbeat_seconds": HEARTBEAT_SECONDS, "max_offline_seconds": OFFLINE_SECONDS}


def end(session, device_id, identifier):
    # Possession of an expired/revoked capability can only end its own media.
    call = session.get(NativeCallSession, identifier, populate_existing=True)
    if not call or call.device_id != device_id: return {"ended": True}
    if not call.ended_at:
        call.ended_at = datetime.utcnow(); session.add(call)
        participant = session.get(SFUParticipant, call.participant_id)
        if participant:
            participant.state = "left"; participant.expires_at = datetime.utcnow(); session.add(participant)
        session.commit()
    return {"ended": True}


def delivery_target(session, push_id, delivery_id):
    if not configured()["enabled"]: return None
    row = session.exec(select(NativeCallDevice).where(NativeCallDevice.push_device_id == push_id, NativeCallDevice.active == True)).first()
    if not row: return None
    try:
        device, _ = incoming(session, row.id, delivery_id)
    except HTTPException:
        return None
    if device.platform == "ios" and not device.encrypted_voip_token: return None
    token = unseal_private(device.encrypted_voip_token, "native-call-voip", device.id) if device.platform == "ios" else None
    return {"mode": "voip" if device.platform == "ios" else "android_call", "token": token,
            "device_id": device.id, "credential_hash": device.credential_hash}


def expire_devices(session, *, limit=100):
    """Erase unusable call capabilities without relying on a foreground app."""
    identifiers = session.exec(select(NativeCallDevice.id).where(NativeCallDevice.expires_at <= datetime.utcnow())
        .order_by(NativeCallDevice.user_id, NativeCallDevice.expires_at, NativeCallDevice.id).limit(max(1, min(limit, 1000)))).all()
    for identifier in identifiers:
        revoke_devices(session, device_id=identifier)
    session.commit()
    return len(identifiers)
