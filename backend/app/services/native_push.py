"""Customer-owned FCM HTTP v1 and APNs alerts; no text or identity in payloads.

Standard notifications can wake/display while the app is backgrounded. They are
not PushKit/CallKit, a media background service, or an automatic call admission.
"""
from datetime import datetime, timedelta
import json
import re
import time
from urllib.parse import urlencode

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi import HTTPException
import jwt
from sqlalchemy import and_, or_, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from ..config import settings
from ..models.account_security import AccountSecurity
from ..models.collaboration import NotificationDelivery
from ..models.meetings import Meeting
from ..models.native_clients import NativeClientSession
from ..models.native_push import NativePushDevice, NativePushDelivery
from ..models.notification import Notification, NotificationPreference
from ..models.session import ActiveSession, RevokedToken
from ..models.sfu import SFURoom, SFUParticipant
from ..models.user import User
from .access import require_channel_member
from .account_security import unseal_private
from .remote_transport import ProviderError, request
from .sso_scope import bind_scope_for_hash
from .workspaces import require_direct_message_writable

MAX_DEVICES = 10
LEASE_SECONDS = 60


def _secret(name):
    value = getattr(settings, name, None)
    return value.get_secret_value() if value else ""


def _fcm_config():
    try:
        project = settings.native_push_fcm_project_id
        data = json.loads(_secret("native_push_fcm_service_account"))
        if (not re.fullmatch(r"[a-z][a-z0-9-]{4,61}[a-z0-9]", project)
                or data.get("type") != "service_account" or data.get("project_id") != project
                or not re.fullmatch(r"[A-Za-z0-9._-]+@[A-Za-z0-9.-]+\.iam\.gserviceaccount\.com", data["client_email"])):
            raise ValueError()
        key = serialization.load_pem_private_key(data["private_key"].encode(), password=None)
        if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
            raise ValueError()
        return project, data["client_email"], key
    except (ValueError, TypeError, KeyError, AttributeError):
        raise HTTPException(503, "FCM is not configured") from None


def _apns_config():
    try:
        team, kid, topic = settings.native_push_apns_team_id, settings.native_push_apns_key_id, settings.native_push_apns_topic
        if (not re.fullmatch(r"[A-Z0-9]{10}", team) or not re.fullmatch(r"[A-Z0-9]{10}", kid)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{2,199}", topic)):
            raise ValueError()
        key = serialization.load_pem_private_key(_secret("native_push_apns_private_key").encode(), password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
            raise ValueError()
        return team, kid, topic, key
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(503, "APNs is not configured") from None


def configuration():
    platforms = []
    if settings.native_push_enabled:
        for name, check in (("android", _fcm_config), ("ios", _apns_config)):
            try:
                check(); platforms.append(name)
            except HTTPException:
                pass
    return {"enabled": bool(platforms), "platforms": platforms, "payload": "generic_activity_and_call_alerts_only",
            "ownership_confirmation": True, "background_calls": "notification_then_explicit_foreground_join",
            "callkit": False}


def require_platform(platform):
    if platform not in configuration()["platforms"]:
        raise HTTPException(503, "Native push is disabled or not configured for this platform")


def validate_token(platform, token):
    pattern = r"[a-fA-F0-9]{64,200}" if platform == "ios" else r"[A-Za-z0-9_:.-]{20,4096}"
    if platform not in {"android", "ios"} or not isinstance(token, str) or not re.fullmatch(pattern, token):
        raise HTTPException(422, "Invalid native device registration")
    return token.lower() if platform == "ios" else token


def dispatch(platform, token, payload, *, ttl=300, authorize=None, mode="alert"):
    """Only fixed vendor destinations; credentials never select an outbound URL."""
    require_platform(platform)
    validate_token(platform, token)
    if (not isinstance(payload, dict) or payload.get("type") not in {"activity", "incoming_call", "subscription_confirmation", "native_call"}
            or set(payload) - {"type", "id", "device_id", "challenge"}
            or any(not isinstance(v, str) or len(v) > 128 for v in payload.values())):
        raise ProviderError("invalid_payload")
    if mode not in {"alert", "android_call", "voip"} or (payload["type"] == "native_call") != (mode != "alert") or mode == "voip" and platform != "ios" or mode == "android_call" and platform != "android":
        raise ProviderError("invalid_payload")
    def recheck():
        if authorize is not None and not authorize():
            raise ProviderError("authorization_changed")
    title = "FreeCollab"
    body = "Incoming FreeCollab call" if payload["type"] == "incoming_call" else "New FreeCollab activity"
    ttl = max(0, min(int(ttl), 300))
    if platform == "android":
        project, email, key = _fcm_config()
        now = int(time.time())
        assertion = jwt.encode({"iss": email, "scope": "https://www.googleapis.com/auth/firebase.messaging",
                                "aud": "https://oauth2.googleapis.com/token", "iat": now, "exp": now + 300}, key, algorithm="RS256")
        # A short-lived scoped credential is obtained per bounded dispatch. Never
        # persist provider access tokens or return them to an app/browser.
        recheck()
        response = request("POST", "https://oauth2.googleapis.com/token", headers={"Content-Type": "application/x-www-form-urlencoded"},
                           body=urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion}).encode(), max_bytes=16384, timeout=10)
        if response.status != 200:
            raise ProviderError("push_unavailable", status=response.status)
        credential = response.json().get("access_token")
        if not isinstance(credential, str) or not 20 <= len(credential) <= 8192 or any(c.isspace() for c in credential):
            raise ProviderError("invalid_response")
        message = {"message": {"token": token, "data": payload,
            "notification": {"title": title, "body": body},
            "android": {"priority": "high", "ttl": str(ttl) + "s", "notification": {
                "channel_id": "freecollab_calls" if payload["type"] == "incoming_call" else "freecollab_activity",
                "tag": payload.get("id", "freecollab-confirmation"), "visibility": "PRIVATE"}}}}
        if mode == "android_call":
            # Data-only wakes the native Telecom service; never OS auto-display
            # a notification that prevents FirebaseMessagingService delivery.
            message["message"].pop("notification", None)
            message["message"]["android"].pop("notification", None)
        recheck()
        response = request("POST", "https://fcm.googleapis.com/v1/projects/" + project + "/messages:send",
                           headers={"Authorization": "Bearer " + credential, "Content-Type": "application/json"},
                           body=json.dumps(message, separators=(",", ":")).encode(), max_bytes=16384, timeout=10)
        if response.status != 200:
            # FCM 404 UNREGISTERED is permanent; inspect only typed code, never log body.
            permanent = False
            if response.status in {400, 404}:
                try:
                    permanent = any(d.get("errorCode") == "UNREGISTERED" for d in response.json().get("error", {}).get("details", []) if isinstance(d, dict))
                except ProviderError:
                    pass
            raise ProviderError("subscription_expired" if permanent else "push_unavailable", status=response.status)
    else:
        team, kid, topic, key = _apns_config()
        bearer = jwt.encode({"iss": team, "iat": int(time.time())}, key, algorithm="ES256", headers={"kid": kid})
        host = "api.sandbox.push.apple.com" if settings.native_push_apns_sandbox else "api.push.apple.com"
        body_bytes = json.dumps({"aps": {"alert": {"title": title, "body": body}, "sound": "default", "category": "freecollab_call" if payload["type"] == "incoming_call" else "freecollab_activity"}, **payload}, separators=(",", ":")).encode()
        if mode == "voip":
            body_bytes = json.dumps({"aps": {"content-available": 1}, **payload}, separators=(",", ":")).encode()
        recheck()
        response = request("POST", "https://" + host + "/3/device/" + token,
            headers={"Authorization": "bearer " + bearer, "apns-topic": topic + (".voip" if mode == "voip" else ""), "apns-push-type": "voip" if mode == "voip" else "alert",
                     "apns-priority": "10", "apns-expiration": "0" if mode == "voip" else str(int(time.time()) + ttl), "Content-Type": "application/json"},
            body=body_bytes, max_bytes=4096, timeout=10, http2=True)
        if response.status != 200:
            reason = None
            try:
                reason = response.json().get("reason")
            except ProviderError:
                pass
            raise ProviderError("subscription_expired" if response.status == 410 or reason in {"BadDeviceToken", "DeviceTokenNotForTopic", "Unregistered"} else "push_unavailable", status=response.status)


def claims_for(row):
    return json.loads(unseal_private(row.encrypted_claims, "native-push-authority", row.id))


def device_live(session, identifier, *, channel_id=None, confirmed=True, preferences=True):
    session.expire_all()
    row = session.get(NativePushDevice, identifier, populate_existing=True)
    if not row or confirmed and (not row.active or row.confirmed_at is None):
        return None
    try:
        require_platform(row.platform)
        claims = claims_for(row)
        user = session.get(User, row.user_id, populate_existing=True)
        state = session.get(AccountSecurity, row.user_id, populate_existing=True)
        tracked = session.exec(select(ActiveSession).where(ActiveSession.token_hash == row.auth_token_hash)).first()
        native = session.get(NativeClientSession, row.auth_token_hash, populate_existing=True)
        if (not user or user.is_blocked or claims.get("sub") != row.user_id or claims.get("exp", 0) <= time.time()
                or not native or native.client_kind != "mobile" or claims.get("native_client_id") != native.id
                or not tracked or tracked.user_id != row.user_id or tracked.revoked_at or tracked.expires_at <= datetime.utcnow()
                or session.get(RevokedToken, row.auth_token_hash) or claims.get("av", 0) != (state.auth_version if state else 0)
                or not bind_scope_for_hash(session, row.auth_token_hash, row.user_id, claims=claims)):
            return None
        if preferences:
            pref = session.exec(select(NotificationPreference).where(NotificationPreference.user_id == row.user_id)).first()
            if not pref or not pref.push:
                return None
        if channel_id:
            require_channel_member(session, channel_id, row.user_id)
            require_direct_message_writable(session, channel_id, row.user_id)
        return row
    except (HTTPException, ValueError, TypeError):
        return None


def call_live(session, delivery, user_id=None):
    if delivery.transport == "mesh":
        from .meetings import live_meeting
        try:
            meeting = live_meeting(session, delivery.meeting_id)
            from ..models.meetings import MeetingParticipant
            removed = session.exec(select(MeetingParticipant.id).where(MeetingParticipant.meeting_id == meeting.id,
                MeetingParticipant.user_id == user_id, MeetingParticipant.state == "removed")).first()
            return not removed and meeting.channel_id == delivery.channel_id
        except HTTPException:
            return False
    if delivery.transport == "livekit":
        from .sfu import active_room
        try:
            if settings.meetings_sfu_provider != "livekit":
                return False
            room = active_room(session, delivery.meeting_id)
            from .meetings import participant_valid
            removed = session.exec(select(SFUParticipant.id).where(SFUParticipant.meeting_id == room.id,
                SFUParticipant.user_id == user_id, SFUParticipant.state == "removed")).first()
            hosts = session.exec(select(SFUParticipant).where(SFUParticipant.meeting_id == room.id,
                SFUParticipant.user_id == room.host_user_id, SFUParticipant.state == "admitted", SFUParticipant.disconnected == False)).all()
            return not removed and room.channel_id == delivery.channel_id and any(participant_valid(session, host, room) for host in hosts)
        except HTTPException:
            return False
    return False


def activity_live(session, row, device):
    if row.expires_at <= datetime.utcnow():
        return False
    if row.kind == "incoming_call":
        return device.calls_enabled and call_live(session, row, device.user_id)
    notification = session.get(Notification, row.notification_id, populate_existing=True)
    return bool(notification and notification.user_id == device.user_id and not notification.read)


def queue_activity(session, *, limit=10):
    if not settings.native_push_enabled:
        return 0
    count = 0
    devices = session.exec(select(NativePushDevice).where(NativePushDevice.active == True, NativePushDevice.confirmed_at.is_not(None))
                          .order_by(NativePushDevice.last_scanned_at, NativePushDevice.id).limit(max(1, min(limit, 20)))).all()
    for saved in devices:
        row = device_live(session, saved.id)
        if not row:
            saved.active = False; session.add(saved); session.commit(); continue
        items = session.exec(select(Notification, NotificationDelivery.channel_id).join(NotificationDelivery, NotificationDelivery.notification_id == Notification.id).where(
            Notification.user_id == row.user_id,
            or_(Notification.created_at > row.last_notification_at, and_(Notification.created_at == row.last_notification_at, Notification.id > row.last_notification_id)))
            .order_by(Notification.created_at, Notification.id).limit(50)).all()
        cursor_at, cursor_id = row.last_notification_at, row.last_notification_id
        for item, channel_id in items:
            if not item.read:
                count += _enqueue(session, NativePushDelivery(device_id=row.id, source_key="activity:" + item.id,
                    channel_id=channel_id, notification_id=item.id, expires_at=datetime.utcnow() + timedelta(days=1)))
            cursor_at, cursor_id = item.created_at, item.id
        # Bounded incoming alerts for recently started rooms. Device confirmation
        # is the lower bound, so enabling push never rings an already-active call.
        if row.calls_enabled:
            since = max(row.confirmed_at, datetime.utcnow() - timedelta(seconds=120))
            for transport, model in (("mesh", Meeting), ("livekit", SFURoom)):
                calls = session.exec(select(model).where(model.created_at >= since, model.ended_at.is_(None), model.host_user_id != row.user_id)
                    .order_by(model.created_at.desc(), model.id).limit(100)).all()
                for call in calls:
                    count += _enqueue(session, NativePushDelivery(device_id=row.id, source_key="call:" + transport + ":" + call.id,
                        channel_id=call.channel_id, meeting_id=call.id, transport=transport, kind="incoming_call",
                        expires_at=min(call.expires_at, call.created_at + timedelta(seconds=120))))
        row.last_notification_at, row.last_notification_id = cursor_at, cursor_id
        row.last_scanned_at = datetime.utcnow(); session.add(row)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
    return count


def _enqueue(session, delivery):
    if session.exec(select(NativePushDelivery.id).where(NativePushDelivery.device_id == delivery.device_id, NativePushDelivery.source_key == delivery.source_key)).first():
        return 0
    # Do not persist even opaque references to channels outside current authority.
    if not device_live(session, delivery.device_id, channel_id=delivery.channel_id):
        return 0
    session.add(delivery)
    return 1


def _finish(session, identifier, *, status, failure_code=None, **values):
    # An owner may delete a delivery while its provider request is in flight.
    # Update by immutable ID, without touching an expired/deleted ORM instance.
    session.rollback()
    session.exec(update(NativePushDelivery).where(NativePushDelivery.id == identifier,
        NativePushDelivery.status == "processing").values(status=status, failure_code=failure_code, **values)
        .execution_options(synchronize_session=False))
    session.commit()


def run_pending(session, *, limit=5):
    if not settings.native_push_enabled:
        return 0
    queue_activity(session)
    now = datetime.utcnow()
    due = or_(NativePushDelivery.status == "pending", and_(NativePushDelivery.status == "processing", NativePushDelivery.claimed_at < now - timedelta(seconds=LEASE_SECONDS)))
    identifiers = session.exec(select(NativePushDelivery.id).where(due, NativePushDelivery.next_attempt_at <= now)
        .order_by(NativePushDelivery.created_at, NativePushDelivery.id).limit(max(1, min(limit, 20)))).all()
    delivered = 0
    for identifier in identifiers:
        claimed = session.exec(update(NativePushDelivery).where(NativePushDelivery.id == identifier, due, NativePushDelivery.next_attempt_at <= now)
            .values(status="processing", claimed_at=now, attempts=NativePushDelivery.attempts + 1))
        session.commit()
        if claimed.rowcount != 1:
            continue
        row = session.get(NativePushDelivery, identifier, populate_existing=True)
        if row is None:
            continue
        device_id, channel_id, kind = row.device_id, row.channel_id, row.kind
        source_key, attempts, expires_at = row.source_key, row.attempts, row.expires_at
        device = device_live(session, device_id, channel_id=channel_id)
        if not device or not activity_live(session, row, device):
            _finish(session, identifier, status="cancelled", failure_code="access_or_activity_changed")
            continue
        expected_hash, expected_confirmation = device.auth_token_hash, device.confirmed_at
        platform = device.platform
        try:
            destination = unseal_private(device.encrypted_token, "native-push-token", device_id)
            from .native_calls import delivery_target
            target = delivery_target(session, device_id, identifier) if kind == "incoming_call" else None
            mode = target["mode"] if target else "alert"
            if target and target["token"]: destination = target["token"]
            expected_target = (target["device_id"], target["credential_hash"]) if target else None
            session.commit()
            def authorized():
                session.expire_all()
                latest_delivery = session.get(NativePushDelivery, identifier, populate_existing=True)
                if (not latest_delivery or latest_delivery.status != "processing" or latest_delivery.device_id != device_id
                        or latest_delivery.channel_id != channel_id or latest_delivery.kind != kind or latest_delivery.source_key != source_key):
                    session.commit()
                    return False
                latest_device = device_live(session, device_id, channel_id=channel_id)
                allowed = bool(latest_device and latest_device.auth_token_hash == expected_hash
                    and latest_device.confirmed_at == expected_confirmation and latest_device.platform == platform
                    and activity_live(session, latest_delivery, latest_device))
                if allowed and expected_target:
                    current_target = delivery_target(session, device_id, identifier)
                    allowed = bool(current_target and current_target["mode"] == mode and
                        (current_target["device_id"], current_target["credential_hash"]) == expected_target
                        and (mode != "voip" or current_target["token"] == destination))
                # Release read transactions before either provider call so an
                # original-source revocation can commit during FCM OAuth.
                session.commit()
                return allowed
            if not authorized():
                _finish(session, identifier, status="cancelled", failure_code="access_or_activity_changed")
                continue
            payload = {"type": "native_call", "id": identifier, "device_id": expected_target[0]} if expected_target else {"type": kind, "id": identifier}
            options = {"mode": mode} if expected_target else {}
            dispatch(platform, destination, payload,
                ttl=max(0, int((expires_at - datetime.utcnow()).total_seconds())), authorize=authorized, **options)
            _finish(session, identifier, status="delivered", delivered_at=datetime.utcnow())
            delivered += 1
        except (ProviderError, HTTPException) as exc:
            if isinstance(exc, ProviderError) and exc.code == "authorization_changed":
                _finish(session, identifier, status="cancelled", failure_code="access_or_activity_changed")
                continue
            expired = isinstance(exc, ProviderError) and exc.code == "subscription_expired"
            _finish(session, identifier, status="failed" if expired or attempts >= 5 else "pending",
                failure_code="subscription_expired" if expired else "push_unavailable",
                next_attempt_at=datetime.utcnow() + timedelta(seconds=min(3600, 15 * 2 ** attempts)))
            if expired and mode == "voip" and expected_target:
                # A stale PushKit token must not revoke independent APNs alerts.
                from ..models.native_calls import NativeCallDevice
                current_call = session.get(NativeCallDevice, expected_target[0], populate_existing=True)
                if current_call and current_call.credential_hash == expected_target[1]:
                    current_call.encrypted_voip_token = ""; session.add(current_call); session.commit()
                continue
            if expired:
                current = session.get(NativePushDevice, device_id, populate_existing=True)
                if current and current.auth_token_hash == expected_hash and current.confirmed_at == expected_confirmation:
                    current.active = False; session.add(current); session.commit()
    return delivered
