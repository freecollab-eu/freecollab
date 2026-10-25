"""Additional limits for a dedicated disposable sample installation only.

The ordinary application is unchanged when DEMO_INSTANCE is false. These guards
do not authenticate in place of product dependencies: every allowed API handler
still applies its normal actor, membership, revision and content checks.
"""
from collections import OrderedDict
import asyncio
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import re
from threading import Lock
from time import monotonic

from fastapi import HTTPException
from sqlalchemy import update
from sqlmodel import Session, select
from starlette.responses import JSONResponse

from .. import database
from ..config import settings


LIMITATION = "This action is unavailable in the public sample. Use your own FreeCollab installation to configure it."
EXPIRED = "This sample session has expired. Start a new sample workspace."
JSON_LIMIT = 1024 * 1024
UPLOAD_LIMIT = 2 * 1024 * 1024
VISIT_MUTATIONS = 600
VISIT_MUTATION_BYTES = 8 * 1024 * 1024
SOCKET_PACKET_LIMIT = 64 * 1024
_ID = r"[A-Za-z0-9_-]{1,128}"


def assert_public_demo_safe_configuration():
    """Fail closed before a demo instance can consume operator credentials."""
    if not settings.demo_instance:
        return
    settings.validate_public_demo()
    secrets = (
        "smtp_host", "smtp_username", "smtp_password", "oidc_client_id", "oidc_client_secret",
        "matrix_registration_shared_secret", "push_vapid_private_key", "native_push_fcm_service_account",
        "native_push_apns_private_key", "meetings_turn_secret", "meetings_sfu_api_key",
        "meetings_sfu_api_secret", "enterprise_vault_token", "s3_access_key_id",
        "s3_secret_access_key", "s3_session_token", "operations_metrics_token",
        "semantic_opensearch_url", "semantic_opensearch_username", "semantic_opensearch_password", "semantic_opensearch_ca_bundle",
    )
    for name in secrets:
        value = getattr(settings, name, None)
        if hasattr(value, "get_secret_value"):
            value = value.get_secret_value()
        if value:
            # Never include a value, URL, field input or credential in the error.
            raise RuntimeError("Public demo must not contain external-service credentials")


# Explicit native operations. New product routes remain unavailable until their
# sample scope and side effects have been reviewed. No endpoint is emulated.
_READ = (
    r"/mentions/channels/" + _ID + r"/(?:members|suggestions|groups)",
    r"/channels/" + _ID + r"/conversation-work",
    r"/channels/" + _ID + r"/content-collections",
    r"/users/" + _ID + r"/avatar",
    r"/auth/me", r"/auth/users/" + _ID + r"/public-key", r"/account/(?:security|sessions)",
    r"/workspaces", r"/workspaces/" + _ID + r"(?:/members|/logo)?",
    r"/channels", r"/channels/" + _ID + r"(?:/members|/invites|/metadata)?",
    r"/messages/channels/" + _ID + r"(?:/sync(?:/head)?)?", r"/messages/" + _ID,
    r"/threads/messages/" + _ID + r"(?:/subscription)?", r"/reactions/messages/" + _ID,
    r"/read-receipts/channels/" + _ID + r"(?:/unread-count)?",
    r"/presence/(?:online|users/" + _ID + r"|channel/" + _ID + r")",
    r"/preferences/(?:appearance|language)", r"/mentions/me", r"/my-day",
    r"/direct-messages(?:/blocks)?", r"/group-conversations",
    r"/files/(?:channels/" + _ID + r"|" + _ID + r"(?:/versions)?)",
    r"/channels/" + _ID + r"/file-experience/" + _ID + r"/(?:comments|preview)",
    r"/secure/(?:status|channels)",
    r"/notifications(?:/unread-count|/preferences|/channels/" + _ID + r"/settings)?",
    r"/bookmarks", r"/channels/" + _ID + r"/(?:pins|decisions|meetings|clips|scheduled-messages|reminders)",
    r"/channels/" + _ID + r"/(?:meetings|clips)/" + _ID,
    r"/channels/" + _ID + r"/meeting-media-configuration",
    r"/channels/" + _ID + r"/meeting-captures/" + _ID + r"/(?:segments|publications)/" + _ID,
    r"/meetings/mesh/" + _ID + r"/capture",
    r"/meeting-access/" + _ID + r"/(?:poll|capture)",
    r"/decisions/" + _ID + r"(?:/revisions)?", r"/reminders/" + _ID + r"/target",
    r"/channels/" + _ID + r"/shared-documents(?:/" + _ID + r"/(?:updates|comments|snapshots))?",
    r"/channels/" + _ID + r"/shared-document-organization(?:/message-preview/" + _ID + r")?",
    r"/channels/" + _ID + r"/projects(?:/" + _ID + r"/(?:tasks(?:/" + _ID + r"/revisions)?|revisions))?",
    r"/channels/" + _ID + r"/activity-feed(?:/following/" + _ID + r"|/" + _ID + r"(?:/revisions|/comments/" + _ID + r"/revisions|/choices/(?:vote|reaction)/revisions)?)?",
    r"/channels/" + _ID + r"/integrations/(?:documents|calendar)(?:/" + _ID + r"/revisions)?",
    r"/channels/" + _ID + r"/calendar/" + _ID + r"/attendees",
    r"/workspaces/" + _ID + r"/connection-setup",
    r"/workspaces/" + _ID + r"/copilot",
    r"/workspaces/" + _ID + r"/copilot/runtime(?:/(?:tools|executions)|/runs/" + _ID + r")?",
    r"/workspaces/" + _ID + r"/personal-calendar",
    r"/workspaces/" + _ID + r"/apps(?:/catalog)?",
    r"/workspaces/" + _ID + r"/channels/" + _ID + r"/apps/(?:commands|cards|ai/(?:drafts|recaps))",
    r"/workspaces/" + _ID + r"/channels/" + _ID + r"/slack-bridges",
    r"/workspaces/" + _ID + r"/channels/" + _ID + r"/workflows(?:/runs/history|/runs/" + _ID + r"|/" + _ID + r"/revisions)?",
)
_WRITE = {
    "POST": (
        r"/mentions/channels/" + _ID + r"/(?:groups|references/resolve)",
        r"/channels/" + _ID + r"/conversation-work",
        r"/channels/" + _ID + r"/content-collections(?:/" + _ID + r"/items)?",
        r"/workspaces/" + _ID + r"/semantic-search",
        r"/workspaces/" + _ID + r"/personal-calendar",
        r"/auth/logout", r"/channels", r"/direct-messages", r"/group-conversations",
        r"/messages/channels/" + _ID, r"/threads/messages/" + _ID + r"/reply",
        r"/mobile/offline/channels/" + _ID + r"/messages",
        r"/reactions/messages/" + _ID, r"/read-receipts/channels/" + _ID,
        r"/files/(?:channels/" + _ID + r"|" + _ID + r"/versions)",
        r"/channels/" + _ID + r"/file-experience/" + _ID + r"/comments",
        r"/channels/" + _ID + r"/(?:pins|decisions|clips|reminders|scheduled-messages)", r"/bookmarks",
        r"/channels/" + _ID + r"/meetings",
        r"/meetings/" + _ID + r"/(?:join|end|participants/" + _ID + r"/(?:admit|remove))",
        r"/meeting-access/" + _ID + r"/(?:ready|signal|leave|capture/consent)",
        r"/meetings/mesh/" + _ID + r"/capture(?:/(?:consent|control|caption))?",
        r"/channels/" + _ID + r"/meeting-captures/" + _ID + r"/(?:publication|ai/revoke)",
        r"/decisions/" + _ID + r"/revisions", r"/notifications/(?:read-all|" + _ID + r"/read)",
        r"/channels/" + _ID + r"/shared-documents(?:/" + _ID + r"/(?:updates|comments|snapshots))?",
        r"/channels/" + _ID + r"/shared-document-organization/(?:stacks(?:/" + _ID + r"/message-references)?|relations)",
        r"/channels/" + _ID + r"/projects(?:/" + _ID + r"/tasks)?",
        r"/channels/" + _ID + r"/activity-feed(?:/" + _ID + r"/(?:comments|vote|reaction))?",
        r"/channels/" + _ID + r"/integrations/(?:documents|calendar)",
        r"/channels/" + _ID + r"/calendar/availability",
        r"/workspaces/" + _ID + r"/channels/" + _ID + r"/workflows/(?:" + _ID + r"/run|runs/" + _ID + r"/(?:vote|cancel))",
    ),
    "PUT": (
        r"/messages/" + _ID, r"/threads/messages/" + _ID + r"/subscription",
        r"/preferences/(?:appearance|language)",
        r"/notifications/(?:preferences|channels/" + _ID + r"/settings)",
        r"/channels/" + _ID + r"/shared-document-organization/documents/" + _ID + r"/placement",
        r"/channels/" + _ID + r"/activity-feed/(?:following/" + _ID + r"|" + _ID + r"/read)",
        r"/channels/" + _ID + r"/calendar/" + _ID + r"/(?:attendees|response)",
    ),
    "PATCH": (
        r"/mentions/channels/" + _ID + r"/groups/" + _ID,
        r"/channels/" + _ID + r"/metadata", r"/workspaces/" + _ID,
        r"/direct-messages/" + _ID, r"/reminders/" + _ID,
        r"/bookmarks/" + _ID + r"/" + _ID + r"/completion",
        r"/channels/" + _ID + r"/shared-documents/" + _ID + r"(?:/comments/" + _ID + r")?",
        r"/channels/" + _ID + r"/shared-document-organization/stacks/" + _ID,
    ),
    "DELETE": (
        r"/mentions/channels/" + _ID + r"/groups/" + _ID,
        r"/channels/" + _ID + r"/conversation-work/" + _ID,
        r"/channels/" + _ID + r"/content-collections/" + _ID + r"/items",
        r"/workspaces/" + _ID + r"/personal-calendar/" + _ID,
        r"/demo/session", r"/scheduled-messages/" + _ID,
        r"/messages/" + _ID, r"/files/" + _ID,
        r"/reactions/messages/" + _ID + r"/[^/]{1,32}",
        r"/notifications(?:/" + _ID + r")?", r"/bookmarks/" + _ID + r"/" + _ID,
        r"/channels/" + _ID + r"/(?:pins/" + _ID + r"|clips/" + _ID + r")",
        r"/channels/" + _ID + r"/shared-document-organization/(?:stacks|relations|message-references)/" + _ID,
    ),
}
_READ_RE = tuple(re.compile(pattern + r"\Z") for pattern in _READ)
_WRITE_RE = {method: tuple(re.compile(pattern + r"\Z") for pattern in patterns) for method, patterns in _WRITE.items()}


def allowed_public_demo_route(method: str, path: str) -> bool:
    if not path.startswith("/") or any(value in path for value in ("//", "\\", "..", "\x00")):
        return False
    patterns = _READ_RE if method in {"GET", "HEAD"} else _WRITE_RE.get(method, ())
    return any(pattern.fullmatch(path) for pattern in patterns)


class _Buckets:
    """Per-process abuse limits; persistent visit budgets provide storage bounds."""
    def __init__(self, maximum=2048):
        self.maximum = maximum
        self.rows = OrderedDict()
        self.lock = Lock()

    def allow(self, key: str, limit: int, now: float | None = None) -> bool:
        now = monotonic() if now is None else now
        with self.lock:
            for expired in [key for key, (started, _) in self.rows.items() if now - started >= 60]:
                self.rows.pop(expired, None)
            if key not in self.rows:
                if len(self.rows) >= self.maximum:
                    return False
                self.rows[key] = (now, 0)
            started, count = self.rows[key]
            if count >= limit:
                return False
            self.rows[key] = (started, count + 1)
            return True


_buckets = _Buckets()


def _ip_key(scope):
    # Only the ASGI peer/trusted server proxy policy is authoritative; no raw
    # X-Forwarded-For supplied by this request is parsed or retained here.
    address = str((scope.get("client") or ("unknown", 0))[0])
    return sha256(address.encode()).hexdigest()


@dataclass(frozen=True)
class _Actor:
    user_id: str
    visit_id: str


def _actor(scope) -> _Actor:
    from .auth import active_user_for_token, decode_token_claims, token_digest
    from .public_demo import get_active_demo_visit
    from ..models.session import ActiveSession
    auth = [value for key, value in scope.get("headers", []) if key.lower() == b"authorization"]
    if len(auth) != 1:
        raise HTTPException(401, EXPIRED)
    try:
        scheme, token = auth[0].decode("ascii").split(" ", 1)
    except (ValueError, UnicodeError):
        raise HTTPException(401, EXPIRED) from None
    if scheme.lower() != "bearer" or not token or any(char.isspace() for char in token):
        raise HTTPException(401, EXPIRED)
    claims = decode_token_claims(token)
    visit_id = claims.get("demo_visit_id") if claims else None
    if not isinstance(visit_id, str) or not re.fullmatch(r"[a-f0-9]{32}", visit_id):
        raise HTTPException(401, EXPIRED)
    with Session(database.engine) as session:
        user = active_user_for_token(token, session)
        tracked = session.exec(select(ActiveSession).where(ActiveSession.token_hash == token_digest(token))).first()
        if user is None or tracked is None or tracked.user_id != user.id:
            raise HTTPException(401, EXPIRED)
        visit = get_active_demo_visit(session, user.id, visit_id=visit_id)
        return _Actor(user.id, visit.id)


def _meeting_actor(scope, actor: _Actor):
    """Sample capabilities belong to this visit's authenticated participant."""
    if not scope.get("path", "").startswith("/meeting-access/"):
        return
    from .auth import token_digest
    from ..models.meetings import Meeting, MeetingParticipant
    from ..models.public_demo import PublicDemoVisit
    from ..models.workspace import WorkspaceChannel
    meeting_id = scope["path"].split("/")[2]
    headers = [value for key, value in scope.get("headers", []) if key.lower() == b"x-meeting-token"]
    if len(headers) != 1 or not 32 <= len(headers[0]) <= 256:
        raise HTTPException(403, "This sample meeting is unavailable")
    try:
        token = headers[0].decode("ascii")
    except UnicodeError:
        raise HTTPException(403, "This sample meeting is unavailable") from None
    with Session(database.engine) as session:
        visit = session.get(PublicDemoVisit, actor.visit_id)
        meeting = session.get(Meeting, meeting_id)
        participant = session.exec(select(MeetingParticipant).where(MeetingParticipant.meeting_id == meeting_id,
            MeetingParticipant.user_id == actor.user_id, MeetingParticipant.credential_hash == token_digest(token))).first()
        if (not visit or not meeting or not participant or participant.kind != "member"
                or not session.exec(select(WorkspaceChannel).where(WorkspaceChannel.workspace_id == visit.workspace_id,
                    WorkspaceChannel.channel_id == meeting.channel_id)).first()):
            raise HTTPException(403, "This sample meeting is unavailable")


def reserve_public_demo_mutation(visit_id: str, size: int):
    """Atomic bounded reservation; failed writes consume their request budget."""
    from ..models.public_demo import PublicDemoVisit
    if size < 0 or size > UPLOAD_LIMIT:
        raise HTTPException(413, "Public sample request is too large")
    with Session(database.engine) as session:
        result = session.exec(update(PublicDemoVisit).where(
            PublicDemoVisit.id == visit_id,
            PublicDemoVisit.revoked_at.is_(None),
            PublicDemoVisit.expires_at > datetime.utcnow(),
            PublicDemoVisit.mutation_count < VISIT_MUTATIONS,
            PublicDemoVisit.mutation_bytes <= VISIT_MUTATION_BYTES - size,
        ).values(mutation_count=PublicDemoVisit.mutation_count + 1,
                 mutation_bytes=PublicDemoVisit.mutation_bytes + size))
        session.commit()
        if result.rowcount != 1:
            raise HTTPException(429, "This sample workspace has reached its temporary editing limit. Start a new sample workspace.")


async def _respond(scope, receive, send, detail: str, status=403):
    response = JSONResponse({"detail": detail, "code": "public_demo_limit"}, status_code=status,
                            headers={"Cache-Control": "private, no-store", "Pragma": "no-cache", "Retry-After": "60"} if status == 429 else
                            {"Cache-Control": "private, no-store", "Pragma": "no-cache"})
    await response(scope, receive, send)


async def _buffer_body(scope, receive, limit):
    lengths = [value for key, value in scope.get("headers", []) if key.lower() == b"content-length"]
    if lengths and (len(lengths) != 1 or not lengths[0].isdigit()):
        raise HTTPException(400, "Invalid Content-Length")
    if lengths and (len(lengths[0]) > 10 or int(lengths[0]) > limit):
        raise HTTPException(413, "Public sample request is too large")
    body, size, chunks = bytearray(), 0, 0
    deadline = monotonic() + 15
    while True:
        try:
            event = await asyncio.wait_for(receive(), max(0.001, deadline - monotonic()))
        except TimeoutError:
            raise HTTPException(408, "Public sample request timed out") from None
        if event["type"] == "http.disconnect":
            raise HTTPException(400, "Request disconnected")
        if event["type"] != "http.request":
            raise HTTPException(400, "Invalid request body")
        size += len(event.get("body", b""))
        chunks += 1
        if size > limit:
            raise HTTPException(413, "Public sample request is too large")
        if chunks > 8192:
            raise HTTPException(413, "Public sample request has too many chunks")
        body.extend(event.get("body", b""))
        if not event.get("more_body", False):
            break
    if lengths and size != int(lengths[0]):
        raise HTTPException(400, "Content-Length does not match request")
    sent = False
    async def replay():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}
        return await receive()
    return replay, size


class PublicDemoBoundaryMiddleware:
    """Register on FastAPI. Native endpoint handlers remain the real product."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if not settings.demo_instance or scope["type"] != "http":
            return await self.app(scope, receive, send)
        method, path = scope.get("method", ""), scope.get("path", "")
        if len(scope.get("query_string", b"")) > 4096:
            return await _respond(scope, receive, send, "Public sample query is too large", 414)
        if method in {"GET", "HEAD"} and path in {"/health", "/ready", "/capabilities"}:
            return await self.app(scope, receive, send)
        if method == "OPTIONS":
            requested = next((value.decode("ascii", "ignore") for key, value in scope.get("headers", []) if key.lower() == b"access-control-request-method"), "")
            if allowed_public_demo_route(requested, path) or path == "/demo/session" and requested == "POST":
                return await self.app(scope, receive, send)
        starting = method == "POST" and path == "/demo/session"
        if not starting and not allowed_public_demo_route(method, path):
            return await _respond(scope, receive, send, LIMITATION)
        try:
            if starting:
                origins = [value for key, value in scope.get("headers", []) if key.lower() == b"origin"]
                sites = [value for key, value in scope.get("headers", []) if key.lower() == b"sec-fetch-site"]
                expected_origin = settings.public_app_url.rstrip("/").encode("ascii")
                if origins and (len(origins) != 1 or origins[0] != expected_origin) or b"cross-site" in sites:
                    # CORS alone cannot stop a cross-site form from consuming a
                    # finite sample slot. This is not a credentialed login.
                    raise HTTPException(403, "Start the sample from its own product page.")
                if not _buckets.allow("start:" + _ip_key(scope), settings.demo_session_rate_limit_per_minute):
                    raise HTTPException(429, "Too many sample workspaces requested. Please try again shortly.")
                replay, _ = await _buffer_body(scope, receive, 1024)
                return await self.app(scope, replay, send)
            actor = _actor(scope)
            _meeting_actor(scope, actor)
            if method == "DELETE" and path == "/demo/session":
                # Leaving/resetting must remain possible after an edit budget
                # is exhausted. Revocation does not create more sample content.
                replay, _ = await _buffer_body(scope, receive, 1024)
                return await self.app(scope, replay, send)
            if not _buckets.allow("api:" + actor.visit_id, 300):
                raise HTTPException(429, "Too many sample requests. Please try again shortly.")
            if method not in {"GET", "HEAD"}:
                if not _buckets.allow("write:" + actor.visit_id, 120):
                    raise HTTPException(429, "Too many sample edits. Please try again shortly.")
                upload = method in {"POST", "PUT"} and path.startswith("/files/") and not path.endswith("/comments")
                replay, size = await _buffer_body(scope, receive, UPLOAD_LIMIT if upload else JSON_LIMIT)
                reserve_public_demo_mutation(actor.visit_id, size)
                return await self.app(scope, replay, send)
            return await self.app(scope, receive, send)
        except HTTPException as failure:
            return await _respond(scope, receive, send, str(failure.detail), failure.status_code)


class PublicDemoSocketBoundaryMiddleware:
    """Wrap socketio.ASGIApp externally; keep normal Socket.IO authorization."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if not settings.demo_instance or scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if path.startswith("/socket.io/") and path != "/socket.io/":
            # Engine.IO dispatches every path beginning with /socket.io/ to its
            # transport, including suffixed paths. Reject those before the
            # wrapped dispatcher so they cannot evade the canonical-path caps.
            if scope["type"] == "websocket":
                return await send({"type": "websocket.close", "code": 1008})
            return await _respond(scope, receive, send, "Use the canonical realtime endpoint", 404)
        if path not in {"/socket.io", "/socket.io/"}:
            if scope["type"] == "websocket":
                return await send({"type": "websocket.close", "code": 1008})
            return await self.app(scope, receive, send)
        peer = _ip_key(scope)
        if scope["type"] == "http":
            if not _buckets.allow("socket-http:" + peer, 300):
                return await _respond(scope, receive, send, "Too many realtime requests", 429)
            try:
                replay, _ = await _buffer_body(scope, receive, SOCKET_PACKET_LIMIT)
            except HTTPException as failure:
                return await _respond(scope, receive, send, str(failure.detail), failure.status_code)
            return await self.app(scope, replay, send)
        if not _buckets.allow("socket-open:" + peer, 30):
            return await send({"type": "websocket.close", "code": 1008})
        closed = False
        async def bounded_receive():
            nonlocal closed
            if closed:
                return {"type": "websocket.disconnect", "code": 1008}
            event = await receive()
            if event["type"] == "websocket.receive":
                size = len(event.get("bytes") or b"") + len((event.get("text") or "").encode())
                if size > SOCKET_PACKET_LIMIT or not _buckets.allow("socket-frame:" + peer, 300):
                    closed = True
                    await send({"type": "websocket.close", "code": 1008})
                    return {"type": "websocket.disconnect", "code": 1008}
            return event
        async def guarded_send(event):
            if not closed:
                await send(event)
        return await self.app(scope, bounded_receive, guarded_send)
