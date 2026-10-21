"""LiveKit's public RoomService/JWT protocol, with current FreeCollab authorization.

Operators own this SFU. DTLS-SRTP terminates there; this adapter does not claim
Matrix or insertable-stream end-to-end encryption. No media is recorded here.
"""
from datetime import datetime, timedelta
from urllib.parse import urlsplit
import time
import hashlib
import json
import httpx
import jwt
from fastapi import HTTPException
from sqlalchemy import case, func, update
from sqlmodel import select
from ..config import settings
from ..models.sfu import SFUParticipant, SFURoom, SFUWorkerLease
from .meetings import participant_valid

HEARTBEAT_SECONDS = 20
ROOM_SECONDS = 2 * 60 * 60
RECONCILE_BATCH_SIZE = 2000
MAX_OUTSTANDING_LEASES = 2000
AUTHORIZATION_CHECK_SECONDS = 15
RECONCILE_BUDGET_SECONDS = 3


def backlog(session):
    count, oldest, pending = session.exec(select(func.count(), func.min(SFUParticipant.authorization_checked_at),
        func.sum(case((SFUParticipant.state != 'admitted', 1), else_=0))).where(
            SFUParticipant.disconnected == False)).one()  # noqa: E712
    return count, oldest, pending or 0


def configuration():
    if settings.meetings_sfu_provider != "livekit":
        raise HTTPException(503, "Large meetings need your operator's LiveKit server")
    public, service = settings.meetings_sfu_url.rstrip('/'), settings.meetings_sfu_api_url.rstrip('/')
    local = settings.environment in {"test", "development"}
    for value, schemes in ((public, {"wss", "ws"} if local else {"wss"}), (service, {"https", "http"} if local else {"https"})):
        parsed = urlsplit(value)
        if parsed.scheme not in schemes or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise HTTPException(503, "The operator must configure valid LiveKit origins with TLS")
    secret = settings.meetings_sfu_api_secret
    if not settings.meetings_sfu_api_key or not secret or len(secret.get_secret_value()) < 32:
        raise HTTPException(503, "The operator must configure LiveKit API credentials")
    return public, service, settings.meetings_sfu_api_key, secret.get_secret_value()


def status(session):
    try:
        configuration()
    except HTTPException as error:
        return {"provider": settings.meetings_sfu_provider, "configured": False, "ready": False, "reason": error.detail, "max_participants": settings.meetings_sfu_max_participants, "encryption": "sfu-dtls-srtp"}
    lease = session.get(SFUWorkerLease, "livekit", populate_existing=True)
    count, oldest, pending = backlog(session)
    cutoff = datetime.utcnow() - timedelta(seconds=AUTHORIZATION_CHECK_SECONDS)
    fresh = bool(lease and lease.configuration_hash == configuration_hash() and lease.seen_at > cutoff
        and not lease.scan_budget_exhausted)
    ready = fresh and count <= MAX_OUTSTANDING_LEASES and not pending and (oldest is None or oldest > cutoff)
    reason = '' if ready else ('Meeting authorization is catching up; new access is paused' if fresh else 'The meeting revocation worker must be running within its authorization budget')
    return {"provider": "livekit", "configured": True, "ready": ready, "reason": reason,
        "max_participants": settings.meetings_sfu_max_participants, "encryption": "sfu-dtls-srtp",
        "max_devices_per_worker": MAX_OUTSTANDING_LEASES, "authorization_check_seconds": AUTHORIZATION_CHECK_SECONDS}


def lock_admission(session):
    """Serialize cross-room admissions against the explicit worker safety bound."""
    changed = session.execute(update(SFUWorkerLease).where(SFUWorkerLease.id == 'livekit').values(seen_at=SFUWorkerLease.seen_at))
    if changed.rowcount != 1:
        raise HTTPException(503, 'The meeting revocation worker must be running')
    session.expire_all()
    current = status(session)
    if not current['ready']:
        raise HTTPException(503, current['reason'])
    if backlog(session)[0] >= MAX_OUTSTANDING_LEASES:
        raise HTTPException(503, 'This meeting worker reached its current device safety limit')


def room_name(identifier):
    return "freecollab_" + identifier


def configuration_hash():
    return hashlib.sha256(json.dumps(configuration(), separators=(',', ':')).encode()).hexdigest()


def encode(grant, *, subject=None, name=None, lifetime=60):
    _, _, key, secret = configuration()
    now = int(time.time())
    claims = {"iss": key, "nbf": now - 5, "exp": now + lifetime, "iat": now, "video": grant}
    if subject: claims["sub"] = subject
    if name: claims["name"] = name
    return jwt.encode(claims, secret, algorithm="HS256")


def rpc(method, payload, grant):
    _, origin, _, _ = configuration()
    try:
        with httpx.Client(timeout=httpx.Timeout(5, connect=3), follow_redirects=False) as client:
            response = client.post(origin + '/twirp/livekit.RoomService/' + method,
                headers={"Authorization": "Bearer " + encode(grant), "Content-Type": "application/json"}, json=payload)
        if method == "RemoveParticipant" and response.status_code == 404:
            return {}
        if response.status_code != 200 or len(response.content) > 1024 * 1024:
            raise ValueError
        result = response.json()
        if not isinstance(result, dict): raise ValueError
        return result
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, "The operator's LiveKit service is unavailable; retry when it is healthy") from None


def public(room):
    return {"id": room.id, "channel_id": room.channel_id, "host_user_id": room.host_user_id,
        "created_at": room.created_at, "expires_at": room.expires_at, "max_participants": room.max_participants,
        "transport": "livekit", "encryption": "sfu-dtls-srtp", "ended": bool(room.ended_at)}


def active_room(session, identifier):
    room = session.get(SFURoom, identifier, populate_existing=True)
    if not room: raise HTTPException(404, "Large meeting not found")
    if not room.provisioned or room.ended_at or room.expires_at <= datetime.utcnow():
        raise HTTPException(410, "This large meeting has ended")
    return room


def remove(room, participant):
    rpc("RemoveParticipant", {"room": room_name(room.id), "identity": participant.id}, {"roomAdmin": True, "room": room_name(room.id)})
    participant.disconnected = True


def reconcile(session):
    """Call every five seconds, including when no HTTP clients are polling."""
    if settings.meetings_sfu_provider != "livekit": return {"checked": 0, "removed": 0}
    configuration()
    # A fresh lease means the actual provider accepted these credentials, not
    # merely that a process is alive with syntactically valid environment vars.
    rpc('ListRooms', {'names': ['freecollab_health_probe']}, {'roomList': True})
    deadline = time.monotonic() + RECONCILE_BUDGET_SECONDS
    removed = checked = 0
    rows = session.exec(select(SFUParticipant).where(SFUParticipant.disconnected == False)
        .order_by(SFUParticipant.authorization_checked_at, SFUParticipant.id).limit(RECONCILE_BATCH_SIZE)).all()  # noqa: E712
    for row in rows:
        if time.monotonic() >= deadline:
            break
        now = datetime.utcnow()
        room = session.get(SFURoom, row.meeting_id)
        if (not room or room.ended_at or room.expires_at <= now or row.last_seen_at < now - timedelta(seconds=HEARTBEAT_SECONDS)
                or not participant_valid(session, row, room)):
            # A failed host removal must retain its permanent account ban.
            if row.state != 'removed': row.state = "left"
            if room:
                try: remove(room, row)
                except HTTPException:
                    # Rotate failed RPCs as well: an unhealthy identity cannot
                    # occupy the head of this bounded queue forever. Pending
                    # revocations independently fence readiness below.
                    row.authorization_checked_at = datetime.utcnow(); session.add(row); checked += 1
                    continue
            else: row.disconnected = True
            session.add(row); removed += 1
        row.authorization_checked_at = datetime.utcnow(); session.add(row); checked += 1
    lease = session.get(SFUWorkerLease, "livekit") or SFUWorkerLease(id="livekit")
    lease.scan_budget_exhausted = checked < len(rows) or time.monotonic() >= deadline
    if not lease.scan_budget_exhausted:
        lease.seen_at = datetime.utcnow(); lease.configuration_hash = configuration_hash()
    session.add(lease); session.commit()
    return {"checked": checked, "removed": removed, "budget_exhausted": lease.scan_budget_exhausted}
