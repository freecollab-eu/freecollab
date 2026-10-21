"""Redacted operational mail status and worker readiness.

Reading status never contacts an SMTP service or opens encrypted mail bodies.
"""
from datetime import datetime, timedelta
from email.message import EmailMessage
from urllib.parse import urlsplit

from sqlalchemy import func
from sqlmodel import select

from ..config import settings
from ..models.account_security import MailOutbox
from ..models.operations import WorkerHeartbeat
from ..models.webhook import WebhookDelivery
from ..models.version_proof import VersionProof
from ..models import file_version  # Register the sync file-state dependency.
from ..models.remote_sync import RemoteSyncConnection
from ..models.workflow import WorkflowRun
from ..models.data_control import DataControlJob
from ..models.push import PushDelivery
from .mail import delivery_mode, mail_available


FAILURE_MESSAGE = "Delivery failed; inspect server delivery configuration"


def mail_configuration() -> dict:
    mode = delivery_mode()
    implicit = bool(settings.smtp_implicit_tls)
    starttls = bool(settings.smtp_starttls)
    tls = "implicit" if implicit else "starttls" if starttls else "none"
    issues = []
    if mode == "disabled":
        issues.append("mail_disabled")
    elif mode == "file":
        if settings.environment == "production":
            issues.append("file_delivery_not_allowed_in_production")
    else:
        if not settings.smtp_host:
            issues.append("smtp_host_missing")
        if implicit and starttls:
            issues.append("smtp_tls_modes_conflict")
        if settings.environment == "production" and tls == "none":
            issues.append("smtp_tls_required")
        password = settings.smtp_password
        has_password = bool(password and password.get_secret_value())
        if bool(settings.smtp_username) != has_password:
            issues.append("smtp_credentials_incomplete")
    try:
        message = EmailMessage()
        message["From"] = settings.smtp_from
        addresses = message["From"].addresses
        sender_configured = len(addresses) == 1 and bool(addresses[0].username and addresses[0].domain) and not message["From"].defects
    except (ValueError, TypeError, AttributeError, IndexError):
        sender_configured = False
    if mode == "smtp" and not sender_configured:
        issues.append("smtp_sender_invalid")
    public_https = urlsplit(settings.public_app_url).scheme == "https"
    if mode != "disabled" and settings.environment == "production" and not public_https:
        issues.append("public_app_https_required")
    return {
        "mode": mode,
        "configured": mail_available() and not issues,
        "tls": tls if mode == "smtp" else "not_applicable",
        "authentication_configured": bool(settings.smtp_username and settings.smtp_password and settings.smtp_password.get_secret_value()),
        "sender_configured": bool(sender_configured),
        "public_url_https": public_https,
        "issues": issues,
    }


def queue_status(session, now: datetime | None = None) -> dict:
    now = now or datetime.utcnow()
    counts = {status: int(count) for status, count in session.exec(
        select(MailOutbox.status, func.count(MailOutbox.id)).group_by(MailOutbox.status)
    ).all()}
    first = session.exec(select(func.min(MailOutbox.created_at)).where(
        MailOutbox.status.in_(["pending", "processing"])
    )).one()
    return {
        **{status: counts.get(status, 0) for status in ("pending", "processing", "failed", "delivered")},
        "oldest_pending_seconds": max(0, int((now - first).total_seconds())) if first else 0,
    }


def worker_status(session, now: datetime | None = None, *, name: str = "mail") -> dict:
    now = now or datetime.utcnow()
    heartbeat = session.get(WorkerHeartbeat, name)
    return {
        "last_seen_at": heartbeat.last_seen_at if heartbeat else None,
        "status": "unknown" if not heartbeat else "healthy" if heartbeat.last_seen_at >= now - timedelta(minutes=3) else "stale",
    }


def record_worker_heartbeat(session, *, name: str = "mail") -> None:
    if name not in {"mail", "integrations"}:
        raise ValueError("Unknown operational worker")
    row = session.get(WorkerHeartbeat, name) or WorkerHeartbeat(name=name)
    row.last_seen_at = datetime.utcnow()
    session.add(row)
    session.commit()


def integration_queue_status(session, now: datetime | None = None) -> dict:
    now = now or datetime.utcnow()
    result = {}
    for key, model, column, states, waiting in (
        ("webhooks", WebhookDelivery, WebhookDelivery.status,
         ("pending", "processing", "failed", "delivered", "cancelled"), ("pending", "processing")),
        ("otrust", VersionProof, VersionProof.state,
         ("queued", "pending", "processing", "failed", "proof_available"), ("queued", "processing")),
        ("sync", RemoteSyncConnection, RemoteSyncConnection.status,
         ("draft", "oauth_required", "oauth_pending", "tested", "pending", "processing", "connected",
          "attention", "failed", "disabled", "disconnected"), ("queued", "syncing")),
        ("workflows", WorkflowRun, WorkflowRun.status,
         ("pending", "processing", "waiting_approval", "completed", "rejected", "expired", "failed", "cancelled"), ("queued", "processing", "waiting_approval")),
        ("data", DataControlJob, DataControlJob.status,
         ("pending", "processing", "erasing_files", "ready", "completed", "expired", "failed"), ("queued", "processing", "erasing_files")),
        ("push", PushDelivery, PushDelivery.status,
         ("pending", "processing", "failed", "delivered", "cancelled"), ("pending", "processing")),
    ):
        counts = {status: int(count) for status, count in session.exec(select(column, func.count(model.id)).group_by(column)).all()}
        if key in {"workflows", "data"}:
            counts["pending"] = counts.pop("queued", 0)
        if key == "sync":
            counts["pending"] = counts.pop("queued", 0)
            counts["processing"] = counts.pop("syncing", 0)
            counts["failed"] = counts.pop("error", 0)
        queued_at = RemoteSyncConnection.next_run_at if key == "sync" else model.created_at
        first = session.exec(select(func.min(queued_at)).where(column.in_(waiting))).one()
        result[key] = {
            **{status: counts.get(status, 0) for status in states},
            "oldest_pending_seconds": max(0, int((now - first).total_seconds())) if first else 0,
        }
    return result


def safe_delivery(row: MailOutbox) -> dict:
    return {
        "id": row.id,
        "created_at": row.created_at,
        "status": row.status,
        "attempts": row.attempts,
        "next_attempt_at": row.next_attempt_at,
        "delivered_at": row.delivered_at,
        # Never return raw provider exception strings, even for legacy records.
        "failure": FAILURE_MESSAGE if row.failure else None,
        "retryable": row.status == "failed",
    }
