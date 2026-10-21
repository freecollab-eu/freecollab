"""Generic, consented Web Push through the same pinned public-HTTPS boundary.

Subscription capabilities are context-encrypted. Every delivery rechecks the
originating live session, provider scope and channel membership. Payloads never
contain names, message text, channel titles or account credentials.
"""
import base64
from datetime import datetime, timedelta
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException
from sqlalchemy import and_, or_, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from ..config import settings
from ..models.account_security import AccountSecurity
from ..models.collaboration import NotificationDelivery
from ..models.notification import Notification, NotificationPreference
from ..models.push import PushDelivery, PushSubscription
from ..models.session import ActiveSession, RevokedToken
from ..models.user import User
from .access import require_channel_member
from .account_security import digest, seal_private, unseal_private
from .remote_transport import ProviderError, request, validate_url
from .sso_scope import bind_scope_for_hash
from .workspaces import require_direct_message_writable

LEASE_SECONDS = 300
MAX_SUBSCRIPTIONS = 10


def _private_key():
    secret = getattr(settings, "push_vapid_private_key", None)
    value = secret.get_secret_value() if secret else ""
    try:
        key = serialization.load_pem_private_key(value.encode(), password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
            raise ValueError()
        return key
    except Exception:
        raise HTTPException(503, "Web Push signing is not configured") from None


def configuration():
    enabled = bool(getattr(settings, "push_enabled", False))
    public = None
    if enabled:
        try:
            key = _private_key()
            subject = getattr(settings, "push_vapid_subject", "")
            if (len(subject) > 512 or any(c.isspace() or ord(c) < 32 for c in subject)
                    or not (subject.startswith("mailto:") and "@" in subject[7:] or subject.startswith("https://"))):
                raise ValueError()
            value = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
            public = base64.urlsafe_b64encode(value).decode().rstrip("=")
        except (HTTPException, ValueError):
            enabled = False
    return {"enabled": enabled, "application_server_key": public if enabled else None,
            "payload": "generic_activity_only", "ownership_confirmation": True}


def require_configured():
    if not configuration()["enabled"]:
        raise HTTPException(503, "Web Push is disabled or not configured on this installation")


def validate_subscription(data):
    endpoint = data.get("endpoint")
    if not isinstance(endpoint, str) or len(endpoint) > 4096:
        raise HTTPException(422, "Invalid push subscription")
    try:
        validate_url(endpoint)
        keys = data["keys"]
        if not isinstance(keys, dict) or set(keys) != {"p256dh", "auth"}:
            raise ValueError()
        decoded = {}
        for name, length in (("p256dh", 65), ("auth", 16)):
            value = keys[name]
            if not isinstance(value, str) or not 1 <= len(value) <= 128 or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_=" for c in value):
                raise ValueError()
            raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
            if len(raw) != length:
                raise ValueError()
            decoded[name] = raw
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), decoded["p256dh"])
    except (ValueError, TypeError, KeyError, ProviderError):
        raise HTTPException(422, "Invalid or unsafe push subscription") from None
    return {"endpoint": endpoint, "keys": {key: keys[key] for key in ("p256dh", "auth")}}


def dispatch(subscription, payload):
    """Use standard Web Push crypto, then dispatch with pinned TLS and no redirects."""
    require_configured()
    try:
        from pywebpush import WebPusher
        from py_vapid import Vapid02
        endpoint = subscription["endpoint"]
        parsed = urlsplit(endpoint)
        audience = parsed.scheme + "://" + parsed.netloc
        key = _private_key()
        vapid = Vapid02(key)
        auth = vapid.sign({"aud": audience, "sub": settings.push_vapid_subject,
                           "exp": int(time.time()) + 6 * 3600})
        prepared = WebPusher(subscription)._prepare_send_data(
            data=json.dumps(payload, separators=(",", ":")), headers=auth,
            ttl=300, content_encoding="aes128gcm")
        response = request("POST", endpoint, headers={**{str(k): str(v) for k, v in prepared["headers"].items()}, "Content-Type": "application/octet-stream"},
                           body=prepared["data"], max_bytes=4096, timeout=15)
        if response.status not in {200, 201, 202, 204}:
            raise ProviderError("subscription_expired" if response.status in {404, 410} else "push_unavailable", status=response.status)
    except ProviderError:
        raise
    except HTTPException:
        raise
    except Exception:
        raise ProviderError("push_unavailable") from None


def open_subscription(row):
    return json.loads(unseal_private(row.encrypted_subscription, "push-subscription", row.id))


def subscription_live(session, row, *, channel_id=None):
    """Fresh scalar checks avoid cached account/session/scope state across I/O."""
    identifier = row if isinstance(row, str) else row.id
    session.expire_all()
    current = session.get(PushSubscription, identifier, populate_existing=True)
    if not current or not current.active or current.confirmed_at is None:
        return None
    user = session.get(User, current.user_id, populate_existing=True)
    state = session.get(AccountSecurity, current.user_id, populate_existing=True)
    token = session.exec(select(ActiveSession).where(ActiveSession.token_hash == current.auth_token_hash)).first()
    claims = ({"sso_workspace_id": current.scope_workspace_id, "sso_provider_revision": current.scope_provider_revision}
              if current.scope_workspace_id is not None else {})
    if (not user or user.is_blocked or not token or token.user_id != current.user_id
            or token.revoked_at is not None or token.expires_at <= datetime.utcnow()
            or session.get(RevokedToken, current.auth_token_hash)
            or current.auth_version != (state.auth_version if state else 0)
            or not bind_scope_for_hash(session, current.auth_token_hash, current.user_id, claims=claims)):
        return None
    preference = session.exec(select(NotificationPreference).where(NotificationPreference.user_id == user.id)).first()
    if preference is None or not preference.push:
        return None
    if channel_id:
        try:
            require_channel_member(session, channel_id, user.id)
            require_direct_message_writable(session, channel_id, user.id)
        except HTTPException:
            return None
    return current


def queue_activity(session, *, limit=10):
    if not configuration()["enabled"]:
        return 0
    count = 0
    subscriptions = session.exec(select(PushSubscription).where(
        PushSubscription.active == True, PushSubscription.confirmed_at.is_not(None))
        .order_by(PushSubscription.last_scanned_at, PushSubscription.id).limit(max(1, min(limit, 20)))).all()
    for saved in subscriptions:
        row = subscription_live(session, saved)
        if row is None:
            saved.active = False
            session.add(saved)
            session.commit()
            continue
        notifications = session.exec(select(Notification, NotificationDelivery.channel_id).join(
            NotificationDelivery, NotificationDelivery.notification_id == Notification.id).where(
            Notification.user_id == row.user_id,
            or_(Notification.created_at > row.last_notification_at,
                and_(Notification.created_at == row.last_notification_at, Notification.id > row.last_notification_id)))
            .order_by(Notification.created_at, Notification.id).limit(50)).all()
        for notification, channel_id in notifications:
            if not notification.read and not session.exec(select(PushDelivery.id).where(
                PushDelivery.subscription_id == row.id, PushDelivery.notification_id == notification.id)).first():
                session.add(PushDelivery(subscription_id=row.id, notification_id=notification.id, channel_id=channel_id))
                count += 1
            row.last_notification_at, row.last_notification_id = notification.created_at, notification.id
        row.last_scanned_at = datetime.utcnow()
        session.add(row)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
    return count


def run_pending(session, *, limit=5):
    if not configuration()["enabled"]:
        return 0
    queue_activity(session, limit=10)
    now = datetime.utcnow()
    due = or_(PushDelivery.status == "pending", and_(PushDelivery.status == "processing", PushDelivery.claimed_at < now - timedelta(seconds=LEASE_SECONDS)))
    identifiers = session.exec(select(PushDelivery.id).where(due, PushDelivery.next_attempt_at <= now)
                               .order_by(PushDelivery.created_at, PushDelivery.id).limit(max(1, min(limit, 20)))).all()
    delivered = 0
    for identifier in identifiers:
        changed = session.exec(update(PushDelivery).where(PushDelivery.id == identifier, due,
            PushDelivery.next_attempt_at <= now).values(status="processing", claimed_at=now, attempts=PushDelivery.attempts + 1))
        session.commit()
        if changed.rowcount != 1:
            continue
        row = session.get(PushDelivery, identifier, populate_existing=True)
        subscription = session.get(PushSubscription, row.subscription_id)
        current = subscription_live(session, subscription, channel_id=row.channel_id) if subscription else None
        notification = session.get(Notification, row.notification_id, populate_existing=True)
        if not current or not notification or notification.read or notification.user_id != subscription.user_id:
            row.status, row.failure_code = "cancelled", "access_or_activity_changed"
            session.add(row); session.commit(); continue
        try:
            destination = open_subscription(current)
            subscription_id = current.id
            # Release read transactions before bounded remote I/O; repeat the live
            # scope check immediately before dispatch using fresh state.
            session.commit()
            if subscription_live(session, subscription_id, channel_id=row.channel_id) is None:
                row.status, row.failure_code = "cancelled", "access_or_activity_changed"
            else:
                dispatch(destination, {"type": "activity", "id": identifier})
                row.status, row.failure_code, row.delivered_at = "delivered", None, datetime.utcnow()
                delivered += 1
        except (ProviderError, HTTPException) as exc:
            code = exc.code if isinstance(exc, ProviderError) else "push_unavailable"
            row.failure_code = code if code in {"subscription_expired", "unsafe_destination"} else "push_unavailable"
            row.status = "failed" if row.attempts >= 5 or code == "subscription_expired" else "pending"
            row.next_attempt_at = datetime.utcnow() + timedelta(seconds=min(3600, 30 * 2 ** row.attempts))
            if code == "subscription_expired":
                current.active = False
                session.add(current)
        session.add(row); session.commit()
    return delivered
