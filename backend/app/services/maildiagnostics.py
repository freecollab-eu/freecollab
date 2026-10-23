"""Redacted operational mail status and worker readiness.

Reading status never contacts an SMTP service or opens encrypted mail bodies.
"""
from datetime import datetime, timedelta
from email.message import EmailMessage
from urllib.parse import urlsplit

from sqlalchemy import and_, func
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
from ..models.app_platform import AppJob, ChannelRecapSchedule
from ..models.calendar_delivery import CalendarProviderJob
from ..models.chat_productivity import MessageReminder, ScheduledMessage
from ..models.native_calls import NativeCallDevice
from ..models.native_push import NativePushDelivery
from ..models.personal_oauth import PersonalOAuthGrant
from ..models.slack_import import SlackImport
from ..models import integration_content, message, notification  # noqa: F401 -- queue foreign keys
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
    for key, model, column, states, waiting, queued_at, aliases in (
        ("calendar-delivery", CalendarProviderJob, CalendarProviderJob.status,
         ("pending", "processing", "completed", "uncertain", "failed", "cancelled"), ("queued", "running"),
         CalendarProviderJob.created_at, {"queued": "pending", "running": "processing", "succeeded": "completed"}),
        ("native_push", NativePushDelivery, NativePushDelivery.status,
         ("pending", "processing", "delivered", "failed", "cancelled"), ("pending", "processing"),
         NativePushDelivery.next_attempt_at, {}),
        ("apps", AppJob, AppJob.status,
         ("pending", "processing", "delivered", "failed", "uncertain", "cancelled"), ("pending", "processing"),
         AppJob.next_attempt_at, {}),
    ):
        counts = {status: int(count) for status, count in session.exec(select(column, func.count(model.id)).group_by(column)).all()}
        for original, normalized in aliases.items():
            counts[normalized] = counts.pop(original, 0)
        first = session.exec(select(func.min(queued_at)).where(column.in_(waiting))).one()
        result[key] = {**{status: counts.get(status, 0) for status in states}, "oldest_pending_seconds": _pending_age(first, now)}

    chat = {state: 0 for state in ("pending", "processing", "sent", "notified", "completed", "cancelled", "notification_pending")}
    first_chat = []
    for model in (ScheduledMessage, MessageReminder):
        counts = session.exec(select(model.status, func.count(model.id)).group_by(model.status)).all()
        for state, count in counts:
            if state in chat:
                chat[state] += int(count)
        first_chat.append(session.exec(select(func.min(model.due_at)).where(model.status.in_(["pending", "processing"]))).one())
    notifications_waiting = and_(ScheduledMessage.status == "sent", ScheduledMessage.notifications_delivered.is_(False))
    chat["notification_pending"] = _count(session, ScheduledMessage, notifications_waiting)
    chat["sent"] -= chat["notification_pending"]
    first_chat.append(session.exec(select(func.min(ScheduledMessage.due_at)).where(notifications_waiting)).one())
    result["chat"] = {**chat, "oldest_pending_seconds": _pending_age(min((value for value in first_chat if value), default=None), now)}

    recap_due = and_(ChannelRecapSchedule.enabled.is_(True), ChannelRecapSchedule.next_run_at <= now)
    first = session.exec(select(func.min(ChannelRecapSchedule.next_run_at)).where(recap_due)).one()
    result["recaps"] = {
        "pending": _count(session, ChannelRecapSchedule, recap_due),
        "scheduled": _count(session, ChannelRecapSchedule, ChannelRecapSchedule.enabled.is_(True), ChannelRecapSchedule.next_run_at > now),
        "disabled": _count(session, ChannelRecapSchedule, ChannelRecapSchedule.enabled.is_(False)),
        "oldest_pending_seconds": _pending_age(first, now),
    }
    first = session.exec(select(func.min(NativeCallDevice.expires_at)).where(NativeCallDevice.expires_at <= now)).one()
    result["native-call-cleanup"] = {
        "pending": _count(session, NativeCallDevice, NativeCallDevice.expires_at <= now),
        "scheduled": _count(session, NativeCallDevice, NativeCallDevice.expires_at > now),
        "oldest_pending_seconds": _pending_age(first, now),
    }
    preview_active = SlackImport.status.in_(["preview", "in_progress"])
    first = session.exec(select(func.min(SlackImport.expires_at)).where(preview_active, SlackImport.expires_at <= now)).one()
    result["slack-import-cleanup"] = {
        "pending": _count(session, SlackImport, preview_active, SlackImport.expires_at <= now),
        "scheduled": _count(session, SlackImport, preview_active, SlackImport.expires_at > now),
        "inactive": _count(session, SlackImport, ~preview_active),
        "oldest_pending_seconds": _pending_age(first, now),
    }
    # This queue periodically validates local source authority; it does not send OAuth requests.
    active_grant = PersonalOAuthGrant.status.in_(["pending", "active"])
    check_before = now - timedelta(seconds=60)
    due_grant = and_(active_grant, PersonalOAuthGrant.last_checked_at < check_before)
    never_checked = session.exec(select(func.min(PersonalOAuthGrant.created_at)).where(due_grant,
        PersonalOAuthGrant.last_checked_at < PersonalOAuthGrant.created_at)).one()
    checked = session.exec(select(func.min(PersonalOAuthGrant.last_checked_at)).where(due_grant,
        PersonalOAuthGrant.last_checked_at >= PersonalOAuthGrant.created_at)).one()
    first = min((value for value in (never_checked, checked + timedelta(seconds=60) if checked else None) if value), default=None)
    result["personal-oauth-cleanup"] = {
        "pending": _count(session, PersonalOAuthGrant, due_grant),
        "scheduled": _count(session, PersonalOAuthGrant, active_grant, PersonalOAuthGrant.last_checked_at >= check_before),
        "inactive": _count(session, PersonalOAuthGrant, ~active_grant),
        "oldest_pending_seconds": _pending_age(first, now),
    }
    return result


def _count(session, model, *conditions) -> int:
    return int(session.exec(select(func.count()).select_from(model).where(*conditions)).one())


def _pending_age(first, now) -> int:
    return max(0, int((now - first).total_seconds())) if first else 0


def integration_worker_required(queues: dict) -> bool:
    """Feature flags plus persisted work from the worker's always-on local groups."""
    if any(getattr(settings, name, False) for name in ("webhooks_enabled", "otrust_enabled", "sync_enabled",
        "workflows_enabled", "data_controls_enabled", "push_enabled", "native_push_enabled", "copilot_enabled")):
        return True
    return any(queues.get(group, {}).get(state, 0) for group in
        ("apps", "chat", "native-call-cleanup", "slack-import-cleanup", "personal-oauth-cleanup")
        for state in ("pending", "processing", "scheduled", "notification_pending"))


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
