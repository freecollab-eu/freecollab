"""Durable signed webhooks. Events contain authorized routing metadata only.

Endpoint requests use public, freshly resolved IP addresses with TLS hostname
verification, including through an inherited HTTP(S) proxy. No redirect or direct
fallback can turn a delivery into access to a private network.
"""
import argparse
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import ssl
import time
from urllib.parse import urlsplit, urlunsplit, unquote
from urllib.request import getproxies
from uuid import uuid4

import httpcore
from fastapi import HTTPException
from sqlalchemy import and_, or_, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ..config import settings
from ..models.channel import Channel, ChannelMember
from ..models.user import User
from ..models.webhook import WebhookDelivery, WebhookEndpoint
from ..models.workspace import WorkspaceChannel
from .account_security import seal_private, unseal_private
from . import oidc_transport as public_transport
from .workspaces import lock_workspace, require_workspace_admin, workspace_allows_channel

EVENT_TYPES = {"message.created", "file.created", "file.version.created", "decision.created", "decision.updated", "channel.activity"}
MAX_ATTEMPTS = 8
LEASE_SECONDS = 90


def webhooks_enabled() -> bool:
    return bool(getattr(settings, "webhooks_enabled", False))


def require_enabled():
    if not webhooks_enabled():
        raise HTTPException(503, "Webhook delivery is not enabled on this server")


def normalize_url(value: str) -> tuple[str, str, str]:
    if not isinstance(value, str) or len(value) > 2048 or re.search(r"[\x00-\x20\x7f]", value):
        raise HTTPException(400, "Use a public HTTPS webhook URL")
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").encode("idna").decode("ascii").lower()
        port = parts.port
    except (ValueError, UnicodeError):
        raise HTTPException(400, "Use a public HTTPS webhook URL") from None
    if parts.scheme != "https" or not host or len(host) > 253 or parts.username or parts.password or parts.fragment or port not in (None, 443):
        raise HTTPException(400, "Use a public HTTPS webhook URL on port 443 without credentials or a fragment")
    if host.endswith(".") or host in {"localhost", "metadata.google.internal"} or host.endswith((".localhost", ".local", ".internal")):
        raise HTTPException(400, "Webhook destinations must be public")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split(".")) or "." not in host:
            raise HTTPException(400, "Use a public webhook hostname")
        authority = host
    else:
        if not public_ip(address):
            raise HTTPException(400, "Webhook destinations must be public")
        authority = "[" + host + "]" if address.version == 6 else host
    origin = "https://" + authority
    return urlunsplit(("https", authority, parts.path or "/", parts.query, "")), origin, host


def public_ip(address) -> bool:
    return public_transport.public_ip(address)


def public_addresses(host: str) -> list[str]:
    # Share the bounded admission pool and public-unicast policy used by the
    # other customer-configured transports. A DNS timeout cannot leave an
    # unlimited queue of work behind four stalled operating-system lookups.
    try:
        return public_transport.public_addresses(host)
    except public_transport.TransportError:
        raise HTTPException(400, "Webhook destinations must resolve only to public addresses") from None


def validate_destination(value: str):
    normalized, origin, host = normalize_url(value)
    public_addresses(host)
    return normalized, origin


def channel_ids(row):
    return json.loads(row.channel_ids_json)


def event_types(row):
    return json.loads(row.event_types_json)


def require_grants(session, workspace_id: str, user_id: str, channels):
    require_workspace_admin(session, workspace_id, user_id)
    actor = session.get(User, user_id)
    if not actor or actor.is_blocked:
        raise HTTPException(403, "Active workspace administrator required")
    for identifier in channels:
        mapping = session.get(WorkspaceChannel, identifier)
        channel = session.get(Channel, identifier)
        member = session.get(ChannelMember, (identifier, user_id))
        if (not mapping or mapping.workspace_id != workspace_id or not channel or channel.type == "direct"
                or not member or member.role != "admin" or not workspace_allows_channel(session, identifier, user_id)):
            raise HTTPException(403, "Webhook grants require administrator access to each selected group channel")


def may_manage(session, row, user_id: str) -> bool:
    try:
        require_grants(session, row.workspace_id, user_id, channel_ids(row))
    except HTTPException:
        return False
    return True


def endpoint_data(row):
    return {"id": row.id, "workspace_id": row.workspace_id, "name": row.name, "origin": row.origin,
            "channel_ids": channel_ids(row), "event_types": event_types(row), "enabled": row.enabled,
            "generation": row.generation, "created_at": row.created_at, "updated_at": row.updated_at}


def delivery_data(row):
    return {name: getattr(row, name) for name in ("id", "event_id", "event_type", "channel_id", "status", "attempts", "http_status", "failure", "created_at", "next_attempt_at", "delivered_at")}


def seal_endpoint(row, url=None, secret=None):
    if url is not None:
        row.encrypted_url = seal_private(url, "webhook-url", row.id)
    if secret is not None:
        row.encrypted_secret = seal_private(secret, "webhook-secret", row.id)


def cancel_pending(session, row):
    session.execute(update(WebhookDelivery).where(WebhookDelivery.webhook_id == row.id,
                    WebhookDelivery.status.in_(["pending", "processing", "failed"])).values(status="cancelled", claim_token=None, failure="Configuration changed"))


def _enqueue(session, row, event_type: str, resource_id: str | None, channel_id: str | None, *, unique_key: str | None = None):
    key = hashlib.sha256((unique_key or "\0".join((event_type, channel_id or "", resource_id or ""))).encode()).hexdigest()
    existing = session.exec(select(WebhookDelivery).where(WebhookDelivery.webhook_id == row.id, WebhookDelivery.event_key == key)).first()
    if existing:
        return existing
    event_id = uuid4().hex
    occurred = datetime.utcnow().isoformat(timespec="milliseconds") + "Z"
    data = {"workspace_id": row.workspace_id}
    if channel_id:
        data.update(channel_id=channel_id, resource_id=resource_id)
    payload = json.dumps({"schema_version": 1, "id": event_id, "type": event_type, "occurred_at": occurred, "data": data}, sort_keys=True, separators=(",", ":"))
    delivery = WebhookDelivery(webhook_id=row.id, event_key=key, event_id=event_id,
                               event_type=event_type, channel_id=channel_id, payload=payload, generation=row.generation)
    try:
        with session.begin_nested():
            session.add(delivery)
            session.flush()
    except IntegrityError:
        return session.exec(select(WebhookDelivery).where(WebhookDelivery.webhook_id == row.id, WebhookDelivery.event_key == key)).one()
    return delivery


def enqueue_channel_event(session, channel_id: str, event_type: str, resource_id: str, *, workflow_metadata: dict | None = None):
    """Join the caller's content transaction; disabled installations do no work."""
    if getattr(settings, "workflows_enabled", False):
        kind = {"file.created": "file_uploaded", "file.version.created": "file_uploaded",
                "decision.created": "decision_updated", "decision.updated": "decision_updated"}.get(event_type)
        if kind:
            mapping = session.get(WorkspaceChannel, channel_id)
            if mapping:
                from .workflow import queue_event
                queue_event(session, event_type + ":" + resource_id, mapping.workspace_id, channel_id, kind, resource_id, metadata=workflow_metadata)
    if not webhooks_enabled():
        return 0
    if event_type not in EVENT_TYPES or not isinstance(resource_id, str) or not 1 <= len(resource_id) <= 1024:
        raise ValueError("Unsupported webhook event")
    mapping = session.get(WorkspaceChannel, channel_id)
    if not mapping:
        return 0
    queued = 0
    for row in session.exec(select(WebhookEndpoint).where(WebhookEndpoint.workspace_id == mapping.workspace_id, WebhookEndpoint.enabled == True, WebhookEndpoint.deleted_at == None)).all():
        if channel_id not in channel_ids(row) or event_type not in event_types(row) or not may_manage(session, row, row.granted_by):
            continue
        _enqueue(session, row, event_type, resource_id, channel_id)
        queued += 1
    return queued


def enqueue_test(session, row):
    return _enqueue(session, row, "webhook.test", None, None, unique_key="test:" + uuid4().hex)


def signature_headers(secret: str, payload: bytes, event_id: str, event_type: str, timestamp: int | None = None):
    stamp = str(int(time.time()) if timestamp is None else timestamp)
    digest = hmac.new(secret.encode(), stamp.encode() + b"." + payload, hashlib.sha256).hexdigest()
    return {"Content-Type": "application/json", "User-Agent": "FreeCollab-Webhooks/1",
            "X-FreeCollab-Signature": "v1=" + digest, "X-FreeCollab-Timestamp": stamp,
            "X-FreeCollab-Event-ID": event_id, "X-FreeCollab-Event": event_type}


class _TLSNameStream(httpcore.NetworkStream):
    def __init__(self, stream, ip, hostname, deadline=None, *, proxy_tls=False):
        self.stream, self.ip, self.hostname = stream, ip, hostname
        self.deadline = time.monotonic() + 30 if deadline is None else deadline
        self.proxy_tls = proxy_tls

    def timeout(self, value):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise httpcore.ReadTimeout("Webhook delivery deadline reached")
        return min(remaining, value) if value is not None else remaining

    def read(self, max_bytes, timeout=None):
        return self.stream.read(max_bytes, self.timeout(timeout))

    def write(self, buffer, timeout=None):
        return self.stream.write(buffer, self.timeout(timeout))

    def close(self):
        return self.stream.close()

    def get_extra_info(self, info):
        return self.stream.get_extra_info(info)

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        # A proxy's own HTTPS certificate keeps its own name. Only the pinned
        # destination's inner TLS handshake uses the original webhook hostname.
        name = server_hostname if self.proxy_tls else self.hostname if server_hostname == self.ip else server_hostname
        return _TLSNameStream(self.stream.start_tls(ssl_context, name, self.timeout(timeout)), self.ip, self.hostname, self.deadline)


class _PinnedBackend(httpcore.NetworkBackend):
    def __init__(self, ip, hostname, *, proxy_tls=False):
        self.ip, self.hostname, self.backend = ip, hostname, httpcore.SyncBackend()
        self.deadline = time.monotonic() + 30
        self.proxy_tls = proxy_tls

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise httpcore.ConnectTimeout("Webhook delivery deadline reached")
        # The pinned destination is numeric, but a trusted inherited proxy may
        # have a hostname. Resolve it under the same deadline rather than leave
        # an unbounded getaddrinfo call inside socket.create_connection.
        try:
            ipaddress.ip_address(host)
        except ValueError:
            host = public_transport._resolve(host, port, min(timeout, remaining) if timeout is not None else remaining)[0]
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise httpcore.ConnectTimeout("Webhook delivery deadline reached")
        stream = self.backend.connect_tcp(host, port, min(timeout, remaining) if timeout is not None else remaining, local_address, socket_options)
        return _TLSNameStream(stream, self.ip, self.hostname, self.deadline, proxy_tls=self.proxy_tls)

    def connect_unix_socket(self, *args, **kwargs):
        raise httpcore.ConnectError("Unix sockets are not webhook destinations")

    def sleep(self, seconds):
        self.backend.sleep(seconds)


def post_webhook(url: str, payload: bytes, headers: dict):
    normalized, _, host = normalize_url(url)
    address = public_addresses(host)[0]
    parts = urlsplit(normalized)
    authority = "[" + address + "]" if ":" in address else address
    pinned_url = urlunsplit(("https", authority, parts.path, parts.query, ""))
    # Honor the worker's CA and outbound proxy. There is deliberately no retry
    # through a direct connection when a configured proxy denies a destination.
    proxy = getproxies().get("https")
    proxy_tls = False
    if proxy:
        parsed = urlsplit(proxy)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Unsupported outbound proxy")
        auth = (unquote(parsed.username).encode(), unquote(parsed.password or "").encode()) if parsed.username else None
        proxy_url = urlunsplit((parsed.scheme, ("[" + parsed.hostname + "]" if ":" in parsed.hostname else parsed.hostname) + (":" + str(parsed.port) if parsed.port else ""), "", "", ""))
        proxy_tls = parsed.scheme == "https"
    context = ssl.create_default_context()
    backend = _PinnedBackend(address, host, proxy_tls=proxy_tls)
    options = {"ssl_context": context, "network_backend": backend, "max_connections": 1, "max_keepalive_connections": 0}
    pool = (httpcore.HTTPProxy(proxy_url, proxy_auth=auth, proxy_ssl_context=context if proxy_tls else None, **options)
            if proxy else httpcore.ConnectionPool(**options))
    outgoing = {**headers, "Host": parts.netloc}
    with pool:
        with pool.stream("POST", pinned_url, headers=outgoing, content=payload,
                         extensions={"timeout": {"connect": 10, "read": 15, "write": 15, "pool": 5}}) as response:
            retry_after = next((value.decode("ascii", errors="ignore") for key, value in response.headers if key.lower() == b"retry-after"), None)
            return response.status, retry_after


def retry_delay(attempts: int, retry_after=None):
    delay = min(3600, 30 * 2 ** min(attempts, 7))
    try:
        seconds = int(retry_after)
    except (ValueError, TypeError):
        try:
            seconds = int((parsedate_to_datetime(retry_after).astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            seconds = 0
    return max(delay, min(max(seconds, 0), 3600))


def deliver_pending(session, limit=20, send=None):
    if not webhooks_enabled():
        return 0
    now = datetime.utcnow()
    due = or_(WebhookDelivery.status == "pending", and_(WebhookDelivery.status == "processing", WebhookDelivery.claimed_at < now - timedelta(seconds=LEASE_SECONDS)))
    identifiers = session.exec(select(WebhookDelivery.id).where(due, WebhookDelivery.next_attempt_at <= now).order_by(WebhookDelivery.created_at).limit(min(max(limit, 1), 100))).all()
    delivered = 0
    sender = send or post_webhook
    for identifier in identifiers:
        claim = secrets.token_hex(16)
        changed = session.execute(update(WebhookDelivery).where(WebhookDelivery.id == identifier, due, WebhookDelivery.next_attempt_at <= now).values(status="processing", claimed_at=datetime.utcnow(), claim_token=claim, attempts=WebhookDelivery.attempts + 1))
        session.commit()
        if changed.rowcount != 1:
            continue
        session.expire_all()
        delivery = session.get(WebhookDelivery, identifier)
        row = session.get(WebhookEndpoint, delivery.webhook_id)
        if row:
            # Grant/config mutations share this lock. A revocation waits for an
            # already-started bounded delivery and fences everything after it.
            lock_workspace(session, row.workspace_id)
            session.expire_all()
            delivery = session.get(WebhookDelivery, identifier)
            row = session.get(WebhookEndpoint, delivery.webhook_id)
        if delivery.claim_token != claim:
            session.rollback()
            continue
        allowed = row and row.enabled and row.deleted_at is None and row.generation == delivery.generation and may_manage(session, row, row.granted_by)
        if delivery.channel_id and (not row or delivery.channel_id not in channel_ids(row)):
            allowed = False
        status, failure, code, retry_after = "cancelled", "Authorization or configuration changed", None, None
        if allowed:
            try:
                url = unseal_private(row.encrypted_url, "webhook-url", row.id)
                secret = unseal_private(row.encrypted_secret, "webhook-secret", row.id)
                headers = signature_headers(secret, delivery.payload.encode(), delivery.event_id, delivery.event_type)
                code, retry_after = sender(url, delivery.payload.encode(), headers)
            except Exception:
                # Destination URLs and exception strings may contain credentials.
                status, failure = ("failed" if delivery.attempts >= MAX_ATTEMPTS else "pending"), "Delivery failed"
            else:
                if 200 <= code < 300:
                    status, failure = "delivered", None
                elif code in {408, 425, 429} or code >= 500:
                    status, failure = ("failed" if delivery.attempts >= MAX_ATTEMPTS else "pending"), "Receiver temporarily unavailable"
                else:
                    status, failure = "failed", "Receiver rejected delivery"
        values = {"status": status, "failure": failure, "http_status": code, "claim_token": None,
                  "delivered_at": datetime.utcnow() if status == "delivered" else None,
                  "next_attempt_at": datetime.utcnow() + timedelta(seconds=retry_delay(delivery.attempts, retry_after))}
        acknowledged = session.execute(update(WebhookDelivery).where(WebhookDelivery.id == identifier, WebhookDelivery.claim_token == claim).values(**values))
        session.commit()
        if status == "delivered" and acknowledged.rowcount == 1:
            delivered += 1
    return delivered


def main():
    parser = argparse.ArgumentParser(description="Deliver signed, metadata-only FreeCollab webhooks")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=5)
    options = parser.parse_args()
    from .. import main as _registered_models  # noqa: F401
    from ..database import engine, init_db
    init_db()
    while True:
        with Session(engine) as session:
            count = deliver_pending(session)
        if options.once:
            print(f"Delivered {count} queued webhook(s).")
            break
        time.sleep(max(1, min(options.interval, 60)))


if __name__ == "__main__":
    main()
