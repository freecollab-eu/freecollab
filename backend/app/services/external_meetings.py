"""Explicit provider disclosure with durable, bounded meeting orchestration."""
from datetime import datetime
import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import update
from sqlmodel import select

from ..config import settings
from ..models.external_meetings import ExternalMeeting, ExternalMeetingConnection
from ..models.integration_content import IntegrationContent, IntegrationContentRevision
from ..models.remote_sync import RemoteSyncConnection
from ..models.user import User
from ..models.workspace import WorkspaceChannel
from . import external_meeting_providers as providers
from . import remote_sync
from .access import lock_channel_writes, require_channel_member
from .account_security import seal_private, unseal_private
from .crypto import decrypt_message, encrypt_message
from .integration_content import CalendarFields
from .matrix_bridge import is_secure_channel
from .request_guard import recheck_actor
from . import remote_transport
from .workspaces import lock_workspace, require_direct_message_writable, require_workspace_admin

MAX_CONNECTIONS = 12
MAX_MEETINGS = 500


def feature():
    if not getattr(settings, "external_meetings_enabled", False):
        raise HTTPException(503, "External meeting providers are disabled on this installation")


def pack(value, purpose, identity):
    return seal_private(json.dumps(value, separators=(",", ":"), ensure_ascii=False), purpose, identity)


def unpack(value, purpose, identity):
    return json.loads(unseal_private(value, purpose, identity)) if value else {}


def credentials(row):
    return unpack(row.encrypted_credentials, "external-meeting-credentials", row.id)


def save_credentials(row, value):
    row.encrypted_credentials = pack(value, "external-meeting-credentials", row.id) if value else None


def require_channel(session, channel_id, user_id, *, workspace_id=None, admin=False):
    member = require_channel_member(session, channel_id, user_id)
    mapping = session.get(WorkspaceChannel, channel_id)
    if not mapping or (workspace_id and mapping.workspace_id != workspace_id):
        raise HTTPException(404, "Channel not found in this workspace")
    require_direct_message_writable(session, channel_id, user_id)
    if admin:
        require_workspace_admin(session, mapping.workspace_id, user_id)
        if member.role != "admin":
            raise HTTPException(403, "Channel administrator access required")
    return member


def connection(session, workspace_id, identifier, *, lock=False):
    row = session.get(ExternalMeetingConnection, identifier, populate_existing=True)
    if not row or row.workspace_id != workspace_id:
        raise HTTPException(404, "Meeting provider connection not found")
    if lock:
        lock_workspace(session, workspace_id)
        lock_channel_writes(session, row.channel_id)
        session.expire_all()
        row = session.get(ExternalMeetingConnection, identifier)
        if not row or row.workspace_id != workspace_id:
            raise HTTPException(404, "Meeting provider connection not found")
    return row


def grant(session, row):
    owner = session.get(User, row.grant_user_id, populate_existing=True)
    if not owner or owner.is_blocked:
        raise HTTPException(403, "The provider connection administrator no longer has access")
    require_channel(session, row.channel_id, owner.id, workspace_id=row.workspace_id, admin=True)
    source = None
    if row.source_connection_id:
        source = session.get(RemoteSyncConnection, row.source_connection_id, populate_existing=True)
        if (not source or source.revision != row.source_revision or source.workspace_id != row.workspace_id
                or source.channel_id != row.channel_id or source.kind != "calendar"
                or source.provider != providers.PROVIDERS[row.provider] or not source.encrypted_credentials):
            raise HTTPException(409, "The calendar OAuth grant changed. Reconnect and consent again.")
        remote_sync.require_native_grant(session, source)
    return source


def material(session, row):
    source = grant(session, row)
    secret = remote_sync.credentials(source) if source else credentials(row)
    public = json.loads(source.config_json) if source else json.loads(row.config_json)
    public = {key: value for key, value in public.items() if key in {"client_id", "tenant_id", "calendar_id"}}
    public = providers.config(row.provider, public)
    providers.check_scope(row.provider, secret)
    return public, secret, source


def current(session, row_id, expected, actor_id, *, enabled=True):
    initial = session.get(ExternalMeetingConnection, row_id, populate_existing=True)
    if initial is None:
        raise HTTPException(404, "Meeting provider connection not found")
    row = connection(session, initial.workspace_id, row_id, lock=True)
    recheck_actor(session, actor_id)
    require_channel(session, row.channel_id, actor_id, workspace_id=row.workspace_id)
    grant(session, row)
    if row.revision != expected or (enabled and (not row.enabled or row.tested_revision != row.revision)):
        raise HTTPException(409, "The meeting provider connection changed or needs a successful test")
    feature()
    return row


def call(session, row, actor_id, function, *args):
    """A 401 is safe to refresh once; persist rotated refresh tokens first."""
    budget = remote_transport.begin_budget(45)
    try:
        expected, row_id = row.revision, row.id
        public, secret, source = material(session, row)
        try:
            return function(row.provider, public, secret, *args)
        except providers.ProviderError as error:
            if error.status != 401 or not secret.get("refresh_token"):
                raise
            updated = providers.refresh(row.provider, public, secret)
            fresh = current(session, row_id, expected, actor_id, enabled=False)
            source = grant(session, fresh)
            if source:
                remote_sync.save_credentials(source, updated); session.add(source)
            else:
                save_credentials(fresh, updated); session.add(fresh)
            session.commit()
            fresh = current(session, row_id, expected, actor_id, enabled=False)
            return function(fresh.provider, public, updated, *args)
    finally:
        remote_transport.end_budget(budget)


def public_connection(row, *, admin=False):
    return {"id": row.id, "workspace_id": row.workspace_id, "channel_id": row.channel_id,
        "name": row.name, "provider": row.provider, "enabled": row.enabled,
        "status": row.status, "revision": row.revision, "tested": row.tested_revision == row.revision,
        "source_connection_id": row.source_connection_id,
        **({"config": json.loads(row.config_json), "has_credentials": bool(row.encrypted_credentials), "tested_at": row.tested_at} if admin else {})}


def request_hash(connection_id, fields):
    return hashlib.sha256(json.dumps({"connection": connection_id, "fields": fields}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def meeting(session, channel_id, identity, actor_id, *, lock=False, manage=False):
    if lock:
        mapping = session.get(WorkspaceChannel, channel_id)
        if not mapping: raise HTTPException(404, "Channel not found")
        lock_workspace(session, mapping.workspace_id)
        lock_channel_writes(session, channel_id)
        recheck_actor(session, actor_id)
    member = require_channel(session, channel_id, actor_id)
    row = session.get(ExternalMeeting, identity, populate_existing=True)
    if not row or row.channel_id != channel_id:
        raise HTTPException(404, "External meeting not found")
    if manage and row.created_by != actor_id and member.role != "admin":
        raise HTTPException(403, "Only the meeting creator or channel administrator can change this meeting")
    return row


def result(row):
    return unpack(row.encrypted_result, "external-meeting-result", row.id)


def public_meeting(session, row, *, explicit_result=None):
    secure_now = is_secure_channel(session, row.channel_id)
    value = {"id": row.id, "request_id": row.request_id, "channel_id": row.channel_id,
        "connection_id": row.connection_id, "created_by": row.created_by, "state": row.state,
        "revision": row.revision, "secure": row.secure or secure_now, "calendar_item_id": row.calendar_item_id,
        "calendar_revision": row.calendar_revision, "created_at": row.created_at, "last_error": row.last_error}
    if not row.secure and not secure_now:
        value["fields"] = json.loads(decrypt_message(row.encrypted_request, row.channel_id)) if row.encrypted_request else None
        value["join_url"] = result(row).get("join_url")
    elif row.secure and explicit_result:
        # Secure links are returned only for the explicit disclosed operation;
        # the durable store contains opaque ids, not the link/title/times.
        value["join_url"] = explicit_result.get("join_url")
        value["provider_fields"] = {"title": explicit_result.get("title"), "starts_at": explicit_result.get("start"), "ends_at": explicit_result.get("end"), "timezone": "UTC"}
    return value


def latest_calendar(session, identity):
    return session.exec(select(IntegrationContentRevision).where(IntegrationContentRevision.item_id == identity)
        .order_by(IntegrationContentRevision.revision.desc()).limit(1)).first()


def calendar_fields(fields, remote):
    link = remote["join_url"]
    return CalendarFields(title=fields["title"], starts_at=fields["starts_at"], ends_at=fields["ends_at"],
        timezone=fields["timezone"], location=link if len(link) <= 1000 else "Online meeting",
        description="Join meeting: " + link, reminder_minutes=None).model_dump(mode="json")


def persist_result(session, row, remote, actor_id, *, fields=None):
    if row.secure != is_secure_channel(session, row.channel_id):
        raise HTTPException(409, "The channel encryption mode changed; review the provider meeting manually")
    if remote.get("deleted"):
        raise HTTPException(409, "The provider meeting was cancelled outside FreeCollab")
    if fields:
        try:
            response = CalendarFields(title=remote["title"], starts_at=remote["start"], ends_at=remote["end"], timezone="UTC")
        except (ValueError, TypeError, KeyError):
            raise providers.ProviderError("invalid_response") from None
        if (response.title, response.starts_at, response.ends_at) != (fields["title"], fields["starts_at"], fields["ends_at"]):
            raise providers.ProviderError("provider_event_conflict")
    row.remote_fingerprint = providers.fingerprint(remote)
    stored = {key: remote.get(key) for key in (("id", "etag") if row.secure else remote)}
    row.encrypted_result = pack(stored, "external-meeting-result", row.id)
    row.state = "awaiting_provider_link" if not remote.get("join_url") else "awaiting_secure_calendar" if row.secure and not row.calendar_item_id else "ready"
    if remote.get("join_url") and not row.secure:
        if not fields:
            fields = json.loads(decrypt_message(row.encrypted_request, row.channel_id))
        identity = "meeting_" + row.id
        item = session.get(IntegrationContent, identity)
        if item and (item.channel_id != row.channel_id or item.kind != "calendar" or item.created_by != row.created_by):
            raise HTTPException(409, "The linked calendar item conflicts with another item")
        previous = latest_calendar(session, identity)
        if previous:
            if row.calendar_item_id != identity or previous.revision != row.calendar_revision or previous.deleted:
                raise HTTPException(409, "The linked calendar item changed; review it before retrying")
        else:
            count = session.exec(select(IntegrationContent.id).where(IntegrationContent.channel_id == row.channel_id).limit(1000)).all()
            if len(count) >= 1000:
                raise HTTPException(409, "This channel has reached its calendar item limit")
            item = IntegrationContent(id=identity, channel_id=row.channel_id, kind="calendar", created_by=row.created_by)
            session.add(item); session.flush()
            payload = {"id": identity, "kind": "calendar", "revision": 1, "fields": calendar_fields(fields, remote), "deleted": False}
            session.add(IntegrationContentRevision(item_id=identity, revision=1, created_by=actor_id, content_mode="server",
                encrypted_payload=encrypt_message(json.dumps(payload, ensure_ascii=False), row.channel_id)))
            row.calendar_item_id, row.calendar_revision = identity, 1
    row.last_error = None
    row.updated_at = datetime.utcnow()
    session.add(row)


def check_calendar(session, row, expected_revision):
    if not row.calendar_item_id:
        if expected_revision is not None:
            raise HTTPException(409, "The calendar link changed")
        return None
    revision = latest_calendar(session, row.calendar_item_id)
    if not revision or revision.deleted or revision.revision != expected_revision:
        raise HTTPException(409, "The calendar item changed; review its current revision")
    return revision


def tombstone_calendar(session, row, revision, actor_id):
    if not revision or row.secure:
        return
    number = revision.revision + 1
    payload = {"id": row.calendar_item_id, "kind": "calendar", "revision": number, "fields": None, "deleted": True}
    session.add(IntegrationContentRevision(item_id=row.calendar_item_id, revision=number, created_by=actor_id,
        content_mode="server", deleted=True, encrypted_payload=encrypt_message(json.dumps(payload), row.channel_id)))
    row.calendar_revision = number
