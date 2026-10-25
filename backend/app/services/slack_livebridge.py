"""Opt-in continuous native-channel Slack delivery, with bounded durable receipts.

Slack is an explicitly disclosed external recipient. No server E2EE decryption,
historical backfill, provider redirects or ambiguous automatic write retries.
"""
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import json
import re
import secrets
import time
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field
from sqlalchemy import and_, func, or_, update
from sqlmodel import select

from ..models.collaboration import MessageChange
from ..models.message import Message
from ..models.slack_livebridge import SlackBridge, SlackBridgeJob, SlackBridgeMessage
from ..models.user import User
from . import app_platform, remote_transport
from .account_security import seal_private, unseal_private, state_for
from .access import lock_channel_writes
from .chat_notifications import record_message_change
from .crypto import decrypt_message, encrypt_message
from .enterprise_governance import enforce_native_content, assert_deletion_allowed
from .workspaces import lock_workspace
from .request_guard import recheck_actor

LEASE_SECONDS = 90
MAX_JOBS = 500
SLACK_ID = r"^[A-Z][A-Z0-9]{2,40}$"


class BridgeInput(app_platform.Strict):
    channel_id: str = Field(min_length=1, max_length=128)
    slack_team_id: str = Field(pattern=SLACK_ID)
    slack_channel_id: str = Field(pattern=SLACK_ID)
    slack_bot_user_id: str = Field(pattern=SLACK_ID)
    bot_token: str | None = Field(default=None, min_length=16, max_length=512, pattern=r"^xoxb-[A-Za-z0-9-]+$")
    signing_secret: str | None = Field(default=None, min_length=16, max_length=256, pattern=r"^[A-Za-z0-9_-]+$")
    expected_generation: int = Field(default=0, ge=0)
    inbound: bool = True
    outbound: bool = False
    enabled: bool = True
    disclose_future_messages: bool
    password: str = Field(min_length=1, max_length=72)
    code: str | None = Field(default=None, max_length=64)


def public(row):
    return {key: getattr(row, key) for key in ("id", "workspace_id", "channel_id", "slack_team_id",
        "slack_channel_id", "slack_bot_user_id", "generation", "inbound", "outbound", "enabled",
        "last_status", "created_at", "updated_at", "revoked_at")}


def job_data(row):
    return {key: getattr(row, key) for key in ("id", "generation", "direction", "status", "attempts",
        "message_id", "failure", "created_at", "finished_at", "next_attempt_at")}


def grant(session, row):
    if not row or not row.enabled or row.revoked_at:
        return False
    try:
        app_platform.install_grants(session, row.workspace_id, row.granted_by, [row.channel_id])
        return True
    except HTTPException:
        return False


def config(row):
    return json.loads(unseal_private(row.encrypted_config, "slack-bridge-config", row.id))


def cancel_pending(session, row, reason="Bridge changed or paused"):
    session.execute(update(SlackBridgeJob).where(SlackBridgeJob.bridge_id == row.id,
        SlackBridgeJob.status.in_(["pending", "processing"]))
        .values(status="cancelled", failure=reason, encrypted_payload="", claim_token=None, finished_at=datetime.utcnow()))


def save(session, workspace_id, actor_id, data, row=None):
    from .account_security import ensure_reauthentication
    state_for(session, actor_id)
    app_platform.lock_invoking_account(session, actor_id)
    lock_workspace(session, workspace_id); lock_channel_writes(session, data.channel_id); session.expire_all()
    recheck_actor(session, actor_id)
    app_platform.install_grants(session, workspace_id, actor_id, [data.channel_id])
    actor = session.get(User, actor_id)
    ensure_reauthentication(session, actor, data.password, data.code)
    if not data.inbound and not data.outbound:
        raise HTTPException(422, "Choose at least one bridge direction")
    if data.enabled and not data.disclose_future_messages:
        raise HTTPException(422, "Explicitly review future channel disclosure and Slack retention before enabling this bridge")
    previous = None
    if row:
        row = session.get(SlackBridge, row.id, populate_existing=True)
        if row.workspace_id != workspace_id or row.channel_id != data.channel_id or row.generation != data.expected_generation or row.revoked_at:
            raise HTTPException(409, "The bridge changed; review its current mapping")
        if (row.slack_team_id, row.slack_channel_id) != (data.slack_team_id, data.slack_channel_id):
            raise HTTPException(422, "Create a separate bridge for another Slack channel; existing message identifiers keep their original mapping")
        previous = config(row)
        if row.slack_team_id != data.slack_team_id and not data.bot_token:
            raise HTTPException(422, "A different Slack workspace requires its own bot token")
        cancel_pending(session, row)
        row.generation += 1
    else:
        if data.expected_generation:
            raise HTTPException(409, "Create a bridge at generation zero")
        count = session.exec(select(func.count()).select_from(SlackBridge).where(SlackBridge.workspace_id == workspace_id, SlackBridge.revoked_at == None)).one()
        if count >= 20:
            raise HTTPException(429, "This workspace has reached its bridge limit")
        existing = session.exec(select(SlackBridge).where(SlackBridge.workspace_id == workspace_id,
            SlackBridge.channel_id == data.channel_id, SlackBridge.slack_team_id == data.slack_team_id,
            SlackBridge.slack_channel_id == data.slack_channel_id)).first()
        if existing and not existing.revoked_at:
            raise HTTPException(409, "This channel mapping already exists; edit or revoke its current bridge")
        if existing:
            row = existing; row.generation += 1; row.revoked_at = None
        else:
            row = SlackBridge(workspace_id=workspace_id, channel_id=data.channel_id, granted_by=actor_id,
                slack_team_id=data.slack_team_id, slack_channel_id=data.slack_channel_id,
                slack_bot_user_id=data.slack_bot_user_id, encrypted_config="")
    token = data.bot_token or (previous or {}).get("bot_token")
    secret = data.signing_secret or (previous or {}).get("signing_secret")
    if not token or not secret:
        raise HTTPException(422, "Supply your Slack app's bot token and signing secret once")
    row.slack_team_id, row.slack_channel_id, row.slack_bot_user_id = data.slack_team_id, data.slack_channel_id, data.slack_bot_user_id
    row.granted_by, row.inbound, row.outbound, row.enabled = actor_id, data.inbound, data.outbound, data.enabled
    row.updated_at, row.last_status = datetime.utcnow(), "configured" if row.enabled else "paused"
    # Enabling/resuming intentionally starts at current changes, never silently
    # exports a backlog created while the bridge was paused or absent.
    row.outbound_cursor = session.exec(select(func.max(MessageChange.id)).where(MessageChange.channel_id == row.channel_id)).one() or 0
    row.encrypted_config = seal_private(app_platform.dump({"bot_token": token, "signing_secret": secret}), "slack-bridge-config", row.id)
    session.add(row); session.flush()
    return row


def timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,16}\.\d{1,9}", value):
        raise HTTPException(422, "Invalid Slack event timestamp")
    try:
        number = Decimal(value)
        if not number.is_finite() or number <= 0: raise InvalidOperation()
        return number
    except InvalidOperation:
        raise HTTPException(422, "Invalid Slack event timestamp") from None


def verify_signature(row, body, headers, now=None):
    stamp = headers.get("x-slack-request-timestamp", "")
    if not re.fullmatch(r"\d{10,11}", stamp) or abs((now or time.time()) - int(stamp)) > 300:
        raise HTTPException(401, "Invalid or expired Slack signature")
    signing_secret = config(row)["signing_secret"]
    expected = "v0=" + hmac.new(signing_secret.encode(), b"v0:" + stamp.encode() + b":" + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(headers.get("x-slack-signature", ""), expected):
        raise HTTPException(401, "Invalid or expired Slack signature")
    return hashlib.sha256(signing_secret.encode()).hexdigest()


def queue(session, row, direction, key, payload):
    raw = app_platform.dump(payload)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    old = session.exec(select(SlackBridgeJob).where(SlackBridgeJob.bridge_id == row.id, SlackBridgeJob.event_key == key)).first()
    if old:
        if old.body_digest != digest:
            raise HTTPException(409, "Slack event identifier was reused for different content")
        return old, True
    count = session.exec(select(func.count()).select_from(SlackBridgeJob).where(SlackBridgeJob.bridge_id == row.id,
        SlackBridgeJob.status.in_(["pending", "processing"]))).one()
    if count >= MAX_JOBS:
        raise HTTPException(429, "Bridge queue is full")
    job = SlackBridgeJob(bridge_id=row.id, generation=row.generation, direction=direction,
        event_key=key, body_digest=digest, encrypted_payload="", message_id=payload.get("message_id") if direction == "outbound" else None)
    job.encrypted_payload = seal_private(raw, "slack-bridge-job", job.id)
    session.add(job); session.flush()
    return job, False


def incoming(session, row, packet):
    if packet.get("team_id") != row.slack_team_id:
        raise HTTPException(403, "Slack workspace does not match this bridge")
    event, event_id = packet.get("event"), packet.get("event_id")
    if packet.get("type") != "event_callback" or not isinstance(event, dict) or not isinstance(event_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{3,128}", event_id):
        raise HTTPException(422, "Use a Slack event callback")
    if event.get("type") != "message" or event.get("channel") != row.slack_channel_id:
        return {"ignored": True}
    if not row.inbound:
        return {"ignored": True}
    subtype = event.get("subtype")
    if subtype not in (None, "message_changed", "message_deleted") or event.get("bot_id") or event.get("user") == row.slack_bot_user_id:
        return {"ignored": True}
    message = event.get("message", {}) if subtype == "message_changed" else event
    if not isinstance(message, dict) or message.get("bot_id") or message.get("user") == row.slack_bot_user_id:
        return {"ignored": True}
    ts = event.get("deleted_ts") if subtype == "message_deleted" else message.get("ts")
    timestamp(ts)
    authority = session.get(SlackBridgeMessage, (row.id, ts))
    if authority and authority.erased_at:
        return {"ignored": True, "erased": True}
    event_ts = event.get("event_ts", ts); timestamp(event_ts)
    text, user = message.get("text", ""), message.get("user", "unknown")
    if (not isinstance(text, str) or len(text) > 20000 or "\x00" in text or not isinstance(user, str) or len(user) > 64
            or subtype != "message_deleted" and (not re.fullmatch(SLACK_ID, user) or len(text) + len(user) + 9 > 20000)):
        raise HTTPException(422, "Slack message exceeds the bridge content bounds")
    thread = message.get("thread_ts")
    if thread: timestamp(thread)
    payload = {"kind": "delete" if subtype == "message_deleted" else "edit" if subtype == "message_changed" else "message",
        "ts": ts, "event_ts": event_ts, "text": text, "user": user, "thread_ts": thread}
    job, duplicate = queue(session, row, "inbound", "slack:" + event_id, payload)
    return {"accepted": True, "duplicate": duplicate, "job_id": job.id}


def _inbound(session, row, job, payload):
    link = session.get(SlackBridgeMessage, (row.id, payload["ts"]))
    if link and (link.erased_at or not link.message_id):
        return "cancelled"
    if link and timestamp(payload["event_ts"]) <= timestamp(link.latest_event_ts):
        return "delivered"
    if link and link.origin == "native":
        # A Slack edit cannot mutate a locally authored message or create an
        # attribution loop. Native writes remain under their author's control.
        return "cancelled"
    if payload["kind"] == "delete":
        if link:
            message = session.get(Message, link.message_id)
            if message and not message.deleted:
                assert_deletion_allowed(session, row.channel_id, author_id=message.author_id)
                message.deleted = True; message.content = ""; message.edited_at = datetime.utcnow(); session.add(message)
                record_message_change(session, message, "deleted")
                link.latest_event_ts = payload["event_ts"]; session.add(link)
        else:
            identifier = hashlib.sha256((row.id + ":" + payload["ts"]).encode()).hexdigest()[:32]
            message = Message(id=identifier, channel_id=row.channel_id, author_id=row.granted_by,
                content="", encrypted=True, deleted=True)
            session.add(message); session.flush()
            session.add(SlackBridgeMessage(bridge_id=row.id, slack_ts=payload["ts"], message_id=identifier,
                origin="slack", latest_event_ts=payload["event_ts"]))
        return "delivered"
    text = "Slack · " + payload["user"] + "\n" + payload["text"]
    enforce_native_content(session, row.channel_id, row.granted_by, text)
    if link:
        message = session.get(Message, link.message_id)
        if not message or message.deleted: return "cancelled"
        message.content = encrypt_message(text, row.channel_id); message.encrypted = True; message.edited_at = datetime.utcnow()
        session.add(message); record_message_change(session, message, "edited")
        link.latest_event_ts = payload["event_ts"]; session.add(link)
    else:
        parent = session.get(SlackBridgeMessage, (row.id, payload["thread_ts"])) if payload.get("thread_ts") and payload["thread_ts"] != payload["ts"] else None
        if parent and (parent.erased_at or not parent.message_id): parent = None
        identifier = hashlib.sha256((row.id + ":" + payload["ts"]).encode()).hexdigest()[:32]
        message = Message(id=identifier, channel_id=row.channel_id, author_id=row.granted_by,
            content=encrypt_message(text, row.channel_id), encrypted=True, parent_id=parent.message_id if parent else None)
        session.add(message); session.flush(); record_message_change(session, message, "created")
        if parent:
            root = session.get(Message, parent.message_id)
            root.thread_count = (root.thread_count or 0) + 1; session.add(root); record_message_change(session, root, "thread")
        session.add(SlackBridgeMessage(bridge_id=row.id, slack_ts=payload["ts"], message_id=identifier,
            origin="slack", latest_event_ts=payload["event_ts"]))
    job.message_id = message.id
    return "delivered"


def collect_outbound(session, limit=100):
    total = 0
    for row in session.exec(select(SlackBridge).where(SlackBridge.enabled == True,
            SlackBridge.revoked_at == None).order_by(SlackBridge.last_scanned_at, SlackBridge.id).limit(20)).all():
        app_platform.lock_invoking_account(session, row.granted_by)
        lock_workspace(session, row.workspace_id); lock_channel_writes(session, row.channel_id); session.expire_all()
        row = session.get(SlackBridge, row.id)
        row.last_scanned_at = datetime.utcnow()
        if not grant(session, row):
            row.enabled = False; row.generation += 1; row.last_status = "authorization_changed"; session.add(row)
            cancel_pending(session, row, "Bridge grant changed"); session.commit(); continue
        if not row.outbound:
            session.add(row); session.commit(); continue
        changes = session.exec(select(MessageChange).where(MessageChange.channel_id == row.channel_id,
            MessageChange.id > row.outbound_cursor).order_by(MessageChange.id).limit(max(1, min(limit, 100)))).all()
        for change in changes:
            row.outbound_cursor = change.id
            if change.kind not in {"created", "edited", "deleted"}: continue
            message = session.get(Message, change.message_id)
            if not message: continue
            link = session.exec(select(SlackBridgeMessage).where(SlackBridgeMessage.message_id == message.id, SlackBridgeMessage.origin == "slack")).first()
            if link: continue  # Never relay a foreign bridge back through another mapping.
            actor = session.get(User, message.author_id)
            if not actor or actor.is_blocked: continue
            text = "" if message.deleted else decrypt_message(message.content, message.channel_id) if message.encrypted else message.content
            payload = {"message_id": message.id, "content": text, "deleted": message.deleted,
                "author": actor.name[:100], "parent_id": message.parent_id}
            queue(session, row, "outbound", "native:" + str(change.id), payload); total += 1
        session.add(row); session.commit()
    return total


def slack_request(method, payload, token):
    if method not in {"chat.postMessage", "chat.update", "chat.delete", "auth.test"}:
        raise ValueError("Unsupported Slack method")
    response = remote_transport.request("POST", "https://slack.com/api/" + method,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json; charset=utf-8"},
        body=app_platform.dump(payload).encode(), timeout=25, max_bytes=65536)
    if response.status == 429:
        return {"ok": False, "error": "ratelimited", "retry_after": remote_transport.retry_after(response.headers)}
    if response.status != 200:
        # Non-success creates may already have run remotely. Do not blindly retry.
        raise remote_transport.ProviderError("delivery_unknown", response.status)
    result = response.json()
    if result.get("ok") is not True and not isinstance(result.get("error"), str):
        raise remote_transport.ProviderError("invalid_response")
    return result


def _outbound(session, row, job, payload, send, interrupted):
    message = session.get(Message, payload["message_id"])
    if not message or message.channel_id != row.channel_id: return "cancelled", None
    if not message.deleted:
        app_platform.native_channel(session, row.workspace_id, row.channel_id, message.author_id)
    actual = "" if message.deleted else decrypt_message(message.content, row.channel_id) if message.encrypted else message.content
    if actual != payload["content"] or message.deleted != payload["deleted"]: return "cancelled", None
    link = session.exec(select(SlackBridgeMessage).where(SlackBridgeMessage.bridge_id == row.id, SlackBridgeMessage.message_id == message.id)).first()
    if message.deleted and not link: return "delivered", None
    method = "chat.delete" if message.deleted else "chat.update" if link else "chat.postMessage"
    if interrupted and method == "chat.postMessage": return "uncertain", "Interrupted Slack create; inspect Slack before retrying"
    packet = {"channel": row.slack_channel_id}
    if link: packet["ts"] = link.slack_ts
    if not message.deleted:
        enforce_native_content(session, row.channel_id, message.author_id, actual, surface="app")
        packet.update(text=(payload["author"] + ": " + actual)[:38000], mrkdwn=False, parse="none", unfurl_links=False, unfurl_media=False)
        if not link:
            packet["client_msg_id"] = str(UUID(job.id))
            parent = session.exec(select(SlackBridgeMessage).where(SlackBridgeMessage.bridge_id == row.id, SlackBridgeMessage.message_id == message.parent_id)).first() if message.parent_id else None
            if parent: packet["thread_ts"] = parent.slack_ts
    result = send(method, packet, config(row)["bot_token"])
    if result.get("ok") is True:
        if not link and not message.deleted:
            ts = result.get("ts"); timestamp(ts)
            if result.get("channel") != row.slack_channel_id: raise ValueError("Slack returned another channel")
            session.add(SlackBridgeMessage(bridge_id=row.id, slack_ts=ts, message_id=message.id, origin="native", latest_event_ts=ts))
        job.message_id = message.id
        return "delivered", None
    error = result.get("error")
    if error == "ratelimited":
        job.next_attempt_at = datetime.utcnow() + timedelta(seconds=max(1, min(int(result.get("retry_after", 60)), 3600)))
        return ("pending" if job.attempts < 8 else "failed"), "Slack rate limit"
    if method == "chat.delete" and error == "message_not_found": return "delivered", None
    if error in {"invalid_auth", "token_revoked", "account_inactive", "not_authed", "missing_scope", "not_in_channel", "channel_not_found"}:
        row.enabled = False; row.last_status = "reauthorization_required"; session.add(row)
        return "failed", "Slack app authorization changed; reconnect before resuming"
    return "failed", "Slack rejected this reviewed operation"


def run_pending(session, limit=10, send=None):
    collect_outbound(session)
    now = datetime.utcnow()
    due = or_(SlackBridgeJob.status == "pending", and_(SlackBridgeJob.status == "processing", SlackBridgeJob.claimed_at < now - timedelta(seconds=LEASE_SECONDS)))
    candidates = session.exec(select(SlackBridgeJob.id, SlackBridgeJob.status).where(due, SlackBridgeJob.next_attempt_at <= now)
        .order_by(SlackBridgeJob.created_at, SlackBridgeJob.id).limit(max(1, min(limit, 50)))).all()
    completed = 0
    for identifier, prior in candidates:
        claim = secrets.token_hex(16)
        changed = session.execute(update(SlackBridgeJob).where(SlackBridgeJob.id == identifier, due)
            .values(status="processing", claimed_at=datetime.utcnow(), claim_token=claim, attempts=SlackBridgeJob.attempts + 1))
        session.commit()
        if changed.rowcount != 1: continue
        job = session.get(SlackBridgeJob, identifier, populate_existing=True)
        row = session.get(SlackBridge, job.bridge_id)
        app_platform.lock_invoking_account(session, row.granted_by)
        lock_workspace(session, row.workspace_id); lock_channel_writes(session, row.channel_id); session.expire_all()
        job = session.get(SlackBridgeJob, identifier); row = session.get(SlackBridge, job.bridge_id)
        if job.claim_token != claim: session.rollback(); continue
        status, failure = "cancelled", "Bridge authorization or generation changed"
        if row.generation == job.generation and grant(session, row):
            try:
                payload = json.loads(unseal_private(job.encrypted_payload, "slack-bridge-job", job.id))
                if job.direction == "inbound" and row.inbound:
                    status, failure = _inbound(session, row, job, payload), None
                elif job.direction == "outbound" and row.outbound:
                    status, failure = _outbound(session, row, job, payload, send or slack_request, prior == "processing")
            except HTTPException:
                status, failure = "cancelled", "Current access or content policy rejected this bridge operation"
            except Exception:
                status, failure = ("uncertain" if job.direction == "outbound" else "failed"), "Delivery outcome unknown; inspect the receiving system before retrying"
        job.status, job.failure, job.claim_token = status, failure, None
        if status != "pending": job.finished_at = datetime.utcnow()
        if status in {"delivered", "cancelled"}: job.encrypted_payload = ""
        if row.enabled or row.last_status not in {"reauthorization_required", "authorization_changed"}: row.last_status = status
        session.add(row); session.add(job); session.commit()
        completed += status == "delivered"
    # Same native notification policy and durable recipient dedupe as scheduled
    # messages. A crash after message commit cannot permanently lose activity.
    import asyncio
    from .chat_notifications import notify_chat_message
    notices = session.exec(select(SlackBridgeJob).where(SlackBridgeJob.direction == "inbound",
        SlackBridgeJob.status == "delivered", SlackBridgeJob.message_id != None,
        SlackBridgeJob.notifications_delivered == False).order_by(SlackBridgeJob.created_at).limit(20)).all()
    for job in notices:
        row = session.get(SlackBridge, job.bridge_id)
        message = session.get(Message, job.message_id)
        if message and not message.deleted and grant(session, row):
            asyncio.run(notify_chat_message(session, message))
        job.notifications_delivered = True; session.add(job); session.commit()
    return completed


def active_bridge_work(session, channel_ids):
    """Retention must not race enabled mirroring or a still-claimed writer."""
    channel_ids = list(set(channel_ids))
    if not channel_ids: return False
    rows = session.exec(select(SlackBridge).where(SlackBridge.channel_id.in_(channel_ids))).all()
    if any(row.enabled and not row.revoked_at for row in rows): return True
    identifiers = [row.id for row in rows]
    return bool(identifiers and session.exec(select(SlackBridgeJob.id).where(SlackBridgeJob.bridge_id.in_(identifiers),
        SlackBridgeJob.status.in_(["pending", "processing"])).limit(1)).first())


def tombstone_erased_messages(session, message_ids):
    """Keep content-free Slack authority so replay cannot resurrect erased data."""
    message_ids = list(set(message_ids))
    if not message_ids: return 0
    rows = session.exec(select(SlackBridgeMessage).where(SlackBridgeMessage.message_id.in_(message_ids))).all()
    for row in rows:
        row.message_id = None; row.erased_at = row.erased_at or datetime.utcnow(); session.add(row)
    for job in session.exec(select(SlackBridgeJob).where(SlackBridgeJob.message_id.in_(message_ids))).all():
        job.encrypted_payload = ""; job.claim_token = None; job.message_id = None
        if job.status not in {"delivered", "cancelled"}: job.status, job.failure = "cancelled", "Source message erased"
        session.add(job)
    return len(rows)


def close_account_resources(session, user_id):
    """Join account deletion: stop grants and erase private copies, no commit/I/O."""
    for row in session.exec(select(SlackBridge).where(SlackBridge.granted_by == user_id)).all():
        if not row.revoked_at: row.generation += 1
        row.enabled = False; row.revoked_at = row.revoked_at or datetime.utcnow(); row.encrypted_config = ""; row.last_status = "account_closed"
        cancel_pending(session, row, "Account closed"); session.add(row)
        for job in session.exec(select(SlackBridgeJob).where(SlackBridgeJob.bridge_id == row.id)).all():
            job.encrypted_payload = ""; job.claim_token = None
            if job.status != "delivered": job.status, job.failure = "cancelled", "Account closed"
            session.add(job)


def export_bridge_metadata(session, workspace_id, channel_ids, actor_id):
    """Current-access metadata only; no provider keys, text copies or claims."""
    result = []
    for channel_id in sorted(set(channel_ids)):
        # Mixed workspace exports may select encrypted channels too. Authorize
        # their metadata without pretending a native Slack mirror can exist.
        app_platform.native_channel(session, workspace_id, channel_id, actor_id, allow_secure=True)
        rows = session.exec(select(SlackBridge).where(SlackBridge.workspace_id == workspace_id, SlackBridge.channel_id == channel_id)).all()
        for row in rows:
            receipts = session.exec(select(SlackBridgeJob).where(SlackBridgeJob.bridge_id == row.id)
                .order_by(SlackBridgeJob.created_at).limit(1001)).all()
            if len(receipts) > 1000:
                raise HTTPException(413, "Bridge metadata export exceeds one thousand receipts; narrow the export or apply reviewed retention first")
            result.append({"bridge": public(row), "receipts": [job_data(job) for job in receipts]})
    return result


def retention_dependencies(session, channel_ids, cutoff, planned_message_ids):
    """Rows to delete in caller's reviewed retention transaction, no commit."""
    from ..models.app_platform import WorkflowAppReceipt
    from ..models.workflow import WorkflowRun
    channel_ids, planned = list(set(channel_ids)), set(planned_message_ids)
    if active_bridge_work(session, channel_ids):
        raise HTTPException(409, "Pause active Slack bridges and finish claimed work before retention")
    bridges = session.exec(select(SlackBridge).where(SlackBridge.channel_id.in_(channel_ids))).all() if channel_ids else []
    identifiers = [row.id for row in bridges]
    jobs, mappings = [], []
    if identifiers:
        jobs = [row for row in session.exec(select(SlackBridgeJob).where(SlackBridgeJob.bridge_id.in_(identifiers))).all()
            if row.status in {"delivered", "failed", "cancelled", "uncertain"}
            and (row.message_id in planned or (row.finished_at or row.created_at) < cutoff)]
        mappings = [row for row in session.exec(select(SlackBridgeMessage).where(SlackBridgeMessage.bridge_id.in_(identifiers))).all()
            if row.message_id in planned]
    run_ids = session.exec(select(WorkflowRun.id).where(WorkflowRun.channel_id.in_(channel_ids),
        WorkflowRun.status.in_(["completed", "failed", "cancelled", "rejected", "timed_out"]), WorkflowRun.finished_at < cutoff)).all() if channel_ids else []
    receipts = session.exec(select(WorkflowAppReceipt).where(WorkflowAppReceipt.run_id.in_(run_ids))).all() if run_ids else []
    return {"slackbridgejob": {"model": SlackBridgeJob, "rows": jobs},
        "slackbridgemessage": {"model": SlackBridgeMessage, "rows": mappings},
        "workflowappreceipt": {"model": WorkflowAppReceipt, "rows": receipts}}


def main():
    import argparse
    from threading import Event
    import signal
    from sqlmodel import Session
    from .. import main as application  # noqa: F401
    from ..database import engine, init_db
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--once", action="store_true"); parser.add_argument("--interval", type=float, default=5)
    args = parser.parse_args()
    if not 1 <= args.interval <= 60: parser.error("interval must be between1 and60 seconds")
    init_db(); stop = Event(); signal.signal(signal.SIGTERM, lambda *_: stop.set()); signal.signal(signal.SIGINT, lambda *_: stop.set())
    while not stop.is_set():
        with Session(engine) as session: run_pending(session)
        if args.once: return
        stop.wait(args.interval)


if __name__ == "__main__": main()
