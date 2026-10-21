"""Persistent encrypted email outbox with explicit SMTP or private local delivery.

Queueing never sends mail. A separate worker delivers committed records.
"""
import argparse
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime
import json
import os
from pathlib import Path
import smtplib
import ssl
import stat
import time

from fastapi import HTTPException
from sqlalchemy import or_, and_, update
from sqlmodel import Session, select

from ..config import settings
from ..models.account_security import MailOutbox
from .account_security import seal_private, unseal_private


def delivery_mode() -> str:
    return getattr(settings, "mail_delivery_mode", "file" if settings.environment != "production" else "disabled")


def mail_available() -> bool:
    mode = delivery_mode()
    if mode == "file":
        return settings.environment != "production"
    return mode == "smtp" and bool(getattr(settings, "smtp_host", ""))


def enqueue_mail(session, to_email: str, subject: str, body: str) -> MailOutbox:
    if not mail_available():
        raise HTTPException(503, "Email delivery is not configured on this server")
    if any(character in to_email + subject for character in "\r\n") or len(subject) > 200 or len(body) > 50000:
        raise ValueError("Invalid email content")
    row = MailOutbox(recipient=to_email, encrypted_message="")
    message = json.dumps({"subject": subject, "body": body}, separators=(",", ":"))
    row.encrypted_message = seal_private(message, "outbox", row.id)
    session.add(row)
    return row


def _message(row: MailOutbox) -> EmailMessage:
    payload = json.loads(unseal_private(row.encrypted_message, "outbox", row.id))
    message = EmailMessage()
    message["From"] = getattr(settings, "smtp_from", "FreeCollab <noreply@freecollab.local>")
    message["To"] = row.recipient
    message["Subject"] = payload["subject"]
    message["Date"] = format_datetime(row.created_at.replace(tzinfo=timezone.utc))
    message["Message-ID"] = f"<{row.id}@freecollab.local>"
    message["X-FreeCollab-Outbox-ID"] = row.id
    message.set_content(payload["body"])
    return message


def _deliver_file(row: MailOutbox, message: EmailMessage) -> None:
    path = Path(getattr(settings, "mail_file_path", "./data/mail"))
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077:
        raise ValueError("Local mail directory must be private")
    target = path / f"{row.id}.eml"
    payload = message.as_bytes()
    temporary = path / f".{row.id}.{os.urandom(8).hex()}.tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            # Linking a fully written private file makes publication atomic.
            os.link(temporary, target)
        except FileExistsError:
            # A crash after publication but before DB acknowledgement is safe
            # to replay, but an incomplete or replaced file is not success.
            descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor, "rb") as existing:
                info = os.fstat(existing.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or existing.read(len(payload) + 1) != payload:
                    raise ValueError("Local mail file is invalid")
    finally:
        temporary.unlink(missing_ok=True)


def _deliver_smtp(message: EmailMessage) -> None:
    host = getattr(settings, "smtp_host", "")
    implicit = getattr(settings, "smtp_implicit_tls", False)
    starttls = getattr(settings, "smtp_starttls", True)
    if settings.environment == "production" and not (implicit or starttls):
        raise ValueError("Production SMTP requires TLS")
    context = ssl.create_default_context()
    port = getattr(settings, "smtp_port", 465 if implicit else 587)
    timeout = getattr(settings, "smtp_timeout", 15)
    connection = smtplib.SMTP_SSL(host, port, timeout=timeout, context=context) if implicit else smtplib.SMTP(host, port, timeout=timeout)
    with connection as client:
        if starttls and not implicit:
            client.starttls(context=context)
        username = getattr(settings, "smtp_username", None)
        secret = getattr(settings, "smtp_password", None)
        password = secret.get_secret_value() if hasattr(secret, "get_secret_value") else secret
        if username:
            client.login(username, password or "")
        client.send_message(message)


def deliver_pending(session, limit: int = 20, *, heartbeat=None) -> int:
    if not mail_available():
        return 0
    now = datetime.utcnow()
    due = or_(MailOutbox.status == "pending", and_(MailOutbox.status == "processing", MailOutbox.claimed_at < now - timedelta(minutes=10)))
    identifiers = session.exec(select(MailOutbox.id).where(due, MailOutbox.next_attempt_at <= now).order_by(MailOutbox.created_at).limit(min(max(limit, 1), 100))).all()
    delivered = 0
    for identifier in identifiers:
        if heartbeat is not None:
            heartbeat(session)
        changed = session.exec(update(MailOutbox).where(MailOutbox.id == identifier, due, MailOutbox.next_attempt_at <= now).values(status="processing", claimed_at=now, attempts=MailOutbox.attempts + 1))
        session.commit()
        if changed.rowcount != 1:
            continue
        row = session.get(MailOutbox, identifier)
        try:
            message = _message(row)
            if delivery_mode() == "file":
                _deliver_file(row, message)
            else:
                _deliver_smtp(message)
        except Exception:
            # Exception strings can include SMTP credentials or a message URL.
            # Store only a bounded non-sensitive status and retain for retry.
            row.failure = "Delivery failed; inspect server delivery configuration"
            row.status = "failed" if row.attempts >= 5 else "pending"
            row.next_attempt_at = datetime.utcnow() + timedelta(seconds=min(3600, 30 * 2 ** row.attempts))
        else:
            row.status = "delivered"
            row.delivered_at = datetime.utcnow()
            row.failure = None
            delivered += 1
        session.add(row)
        session.commit()
    return delivered


def main() -> None:
    parser = argparse.ArgumentParser(description="Deliver committed FreeCollab email outbox records")
    parser.add_argument("--once", action="store_true", help="Process one batch then exit")
    parser.add_argument("--interval", type=float, default=5, help="Polling seconds")
    options = parser.parse_args()
    from ..database import engine, init_db
    from ..models.user import User  # noqa: F401 -- register foreign-key metadata
    from .chat_notifications import queue_due_digests
    from .maildiagnostics import record_worker_heartbeat
    init_db()
    while True:
        with Session(engine) as session:
            record_worker_heartbeat(session)
            queue_due_digests(session, enqueue_mail, settings.public_app_url)
            count = deliver_pending(session, heartbeat=record_worker_heartbeat)
        if options.once:
            print(f"Delivered {count} queued email(s).")
            break
        time.sleep(max(1, min(options.interval, 60)))


if __name__ == "__main__":
    main()
