"""Read-only operator diagnostics without opening secrets or contacting providers.

These checks describe configured controls and retained queue state. They do not
establish certificate validity, public reachability, backup freshness or an audit.
"""
import base64
import binascii
from datetime import datetime, timedelta
import ipaddress
import shutil
from urllib.parse import urlsplit

from sqlalchemy import and_, case, func, or_
from sqlmodel import select

from ..config import settings
from ..models.account_security import AccountSecurity, MailOutbox
from ..models import file_version  # noqa: F401 -- register sync's file-state FK.
from ..models.remote_sync import RemoteSyncConnection
from ..models.user import User
from ..models.version_proof import VersionProof
from ..models.webhook import WebhookDelivery
from ..models.workforce import WorkforcePrincipal
from .mail import mail_available
from .webhooks import LEASE_SECONDS as WEBHOOK_LEASE_SECONDS

MINIMUM_FREE_BYTES = 1024 ** 3
MINIMUM_FREE_PERCENT = 5


def https_origin(value: str, *, allow_root_slash: bool = False) -> str | None:
    """Validate origin syntax only. DNS and certificates are deliberately unread."""
    if (not isinstance(value, str) or not value or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)
            or "?" in value or "#" in value or "\\" in value):
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ({"", "/"} if allow_root_slash else {""})
                or "*" in parsed.netloc or "%" in parsed.hostname
                or parsed.hostname.lower() in {"localhost", "localhost.localdomain"}
                or parsed.hostname.lower().endswith(".localhost")):
            return None
        port = parsed.port
        if port == 0:
            return None
        try:
            if not ipaddress.ip_address(parsed.hostname).is_global:
                return None
        except ValueError:
            pass
        host = parsed.hostname.encode("idna").decode("ascii").lower()
        if ":" in host:
            host = "[" + host + "]"
        return "https://" + host + (":" + str(port) if port not in {None, 443} else "")
    except (ValueError, TypeError, UnicodeError):
        return None


def _decode_key(secret) -> bytes | None:
    if secret is None:
        return None
    try:
        value = base64.b64decode(secret.get_secret_value(), validate=True)
        return value if len(value) == 32 else None
    except (ValueError, TypeError, AttributeError, binascii.Error):
        return None


def independent_key_status() -> str:
    """Inspect explicit settings, never read or create development key files."""
    if not settings.account_security_key or not settings.message_encryption_keys:
        return "attention" if settings.environment == "production" else "unknown"
    account = _decode_key(settings.account_security_key)
    content = [_decode_key(value) for value in settings.message_encryption_keys.values()]
    signing = settings.jwt_secret.encode()
    derived = signing[:32].ljust(32, b"0")
    if (account is None or any(value is None for value in content)
            or settings.message_encryption_active_key_id not in settings.message_encryption_keys
            or len(signing) < 32 or settings.jwt_secret.startswith("change-")
            or account == derived or any(value == account or value == derived for value in content)):
        return "attention"
    return "pass"


def storage_status() -> dict:
    try:
        usage = shutil.disk_usage(settings.storage_path)
        if usage.total <= 0 or usage.free < 0 or usage.free > usage.total:
            raise ValueError("Invalid storage counters")
        percent = usage.free * 100 / usage.total
        return {
            "status": "attention" if usage.free < MINIMUM_FREE_BYTES or percent < MINIMUM_FREE_PERCENT else "pass",
            "free_bytes": usage.free,
            "total_bytes": usage.total,
            "free_percent": round(percent, 2),
        }
    except (OSError, ValueError, OverflowError):
        # A missing volume, access failure or OS error must not expose its path.
        return {"status": "unknown", "free_bytes": None, "total_bytes": None, "free_percent": None}


def _count(session, model, *conditions) -> int:
    return int(session.exec(select(func.count()).select_from(model).where(*conditions)).one())


def operator_coverage(session) -> dict:
    # Managed identities cannot be unrestricted installation operators, including
    # a corrupted/stale persisted global role which their scope rejects.
    base = (User.role == "admin", User.is_blocked == False, WorkforcePrincipal.user_id.is_(None))
    query = select(func.count(User.id),
        func.coalesce(func.sum(case((and_(AccountSecurity.totp_secret.is_not(None),
                                          AccountSecurity.totp_secret != ""), 1), else_=0)), 0),
        func.coalesce(func.sum(case((AccountSecurity.email_verified_at.is_not(None), 1), else_=0)), 0),
    ).select_from(User).outerjoin(
        AccountSecurity, AccountSecurity.user_id == User.id,
    ).outerjoin(WorkforcePrincipal, WorkforcePrincipal.user_id == User.id).where(*base)
    active, enabled, verified = map(int, session.exec(query).one())
    return {"active": active, "mfa_enabled": enabled, "without_mfa": active - enabled, "email_verified": verified}


def _delivery_queue(session, model, status, due_at, claimed_at, *, enabled: bool,
                    pending: tuple[str, ...], lease_seconds: int, now: datetime) -> dict:
    if not enabled:
        return {"enabled": False, "due": 0, "expired_leases": 0, "failed": 0}
    # NULL claim timestamps are invalid leases, never silently healthy. Reading
    # diagnostics does not reclaim or alter them.
    invalid_lease = and_(status == "processing", or_(
        claimed_at.is_(None), claimed_at < now - timedelta(seconds=lease_seconds),
    ))
    reclaimable = and_(status == "processing", claimed_at < now - timedelta(seconds=lease_seconds))
    return {
        "enabled": True,
        "due": _count(session, model, or_(status.in_(pending), reclaimable), due_at <= now),
        "expired_leases": _count(session, model, invalid_lease),
        "failed": _count(session, model, status == "failed"),
    }


def queue_diagnostics(session, now: datetime) -> dict:
    queues = {
        "mail": _delivery_queue(session, MailOutbox, MailOutbox.status, MailOutbox.next_attempt_at,
            MailOutbox.claimed_at, enabled=mail_available(), pending=("pending",),
            lease_seconds=10 * 60, now=now),
        "webhooks": _delivery_queue(session, WebhookDelivery, WebhookDelivery.status, WebhookDelivery.next_attempt_at,
            WebhookDelivery.claimed_at, enabled=settings.webhooks_enabled, pending=("pending",),
            lease_seconds=WEBHOOK_LEASE_SECONDS, now=now),
        "otrust": _delivery_queue(session, VersionProof, VersionProof.state, VersionProof.next_attempt_at,
            VersionProof.claimed_at, enabled=settings.otrust_enabled, pending=("queued", "pending"),
            lease_seconds=5 * 60, now=now),
    }
    if not settings.sync_enabled:
        queues["sync"] = {"enabled": False, "due": 0, "expired_leases": 0, "failed": 0}
    else:
        model = RemoteSyncConnection
        enabled = model.enabled == True
        ready_lease = or_(model.lease_expires_at.is_(None), model.lease_expires_at <= now)
        queues["sync"] = {
            "enabled": True,
            "due": _count(session, model, enabled, model.next_run_at <= now, ready_lease),
            "expired_leases": _count(session, model, enabled, model.status == "syncing", ready_lease),
            "failed": _count(session, model, enabled, model.status == "error"),
        }
    from ..models.workflow import WorkflowRun
    from ..models.data_control import DataControlJob
    from ..models.push import PushDelivery
    from .push import LEASE_SECONDS as PUSH_LEASE_SECONDS
    queues["push"] = _delivery_queue(session, PushDelivery, PushDelivery.status, PushDelivery.next_attempt_at,
        PushDelivery.claimed_at, enabled=settings.push_enabled, pending=("pending",),
        lease_seconds=PUSH_LEASE_SECONDS, now=now)
    for key, model, enabled, pending, processing in (
        ("workflows", WorkflowRun, settings.workflows_enabled, ("queued", "waiting_approval"), ("processing",)),
        ("data", DataControlJob, settings.data_controls_enabled, ("queued",), ("processing", "erasing_files")),
    ):
        if not enabled:
            queues[key] = {"enabled": False, "due": 0, "expired_leases": 0, "failed": 0}
            continue
        available = or_(model.lease_expires_at.is_(None), model.lease_expires_at <= now)
        ready = or_(model.status.in_(pending), and_(model.status.in_(processing), available))
        if key == "workflows":
            ready = and_(ready, model.due_at <= now)
        queues[key] = {"enabled": True, "due": _count(session, model, ready, available),
            "expired_leases": _count(session, model, model.status.in_(processing), available),
            "failed": _count(session, model, model.status == "failed")}
    return queues


def security_status(session, *, now: datetime | None = None) -> dict:
    now = now or datetime.utcnow()
    public = https_origin(settings.public_app_url, allow_root_slash=True)
    cors = [https_origin(value) for value in settings.cors_origins]
    # CORS matching is literal, while browser Origin serialization normalizes
    # host/scheme case, IDNA and default ports. A noncanonical configured string
    # can look safe yet never match the browser's actual Origin header.
    exact_cors = bool(cors) and all(origin is not None and origin == configured
                                  for origin, configured in zip(cors, settings.cors_origins))
    production = settings.environment == "production"
    operators = operator_coverage(session)
    storage = storage_status()
    queues = queue_diagnostics(session, now)
    checks = [
        {"id": "public_https_origin", "status": "pass" if public else "attention" if production else "unknown"},
        {"id": "cors_https_origins", "status": "pass" if exact_cors and public in cors else "attention" if production else "unknown"},
        {"id": "experimental_features", "status": "attention" if settings.enable_experimental_features else "disabled"},
        {"id": "demo_ai", "status": "attention" if settings.enable_demo_ai else "disabled"},
        {"id": "independent_server_keys", "status": independent_key_status()},
        {"id": "operator_mfa", "status": "pass" if operators["active"] and not operators["without_mfa"] else "attention"},
        {"id": "storage_capacity", "status": storage["status"]},
    ]
    for key, identifier in (("mail", "mail_leases"), ("webhooks", "webhook_leases"),
                            ("otrust", "proof_leases"), ("sync", "sync_leases"),
                            ("workflows", "workflow_leases"), ("data", "data_leases"), ("push", "push_leases")):
        counters = queues[key]
        checks.append({"id": identifier, "status": "disabled" if not counters["enabled"] else
                       "attention" if counters["expired_leases"] or counters["failed"] else "pass"})
    return {"checked_at": now, "environment": settings.environment, "checks": checks,
            "operators": operators, "storage": storage, "queues": queues}
