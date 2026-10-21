"""Explicit customer-owned Meet, Teams and Zoom grants and meeting actions."""
import base64
from datetime import datetime, timedelta
import hashlib
import hmac
import json
import secrets
from typing import Literal
from urllib.parse import urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import func, update
from sqlmodel import select

from ..config import settings
from ..database import get_session
from ..models.account_security import AccountSecurity
from ..models.external_meetings import ExternalMeeting, ExternalMeetingConnection, ExternalMeetingOAuthAttempt
from ..models.integration_content import IntegrationContent
from ..models.remote_sync import RemoteSyncConnection
from ..models.session import ActiveSession, RevokedToken
from ..models.user import User
from ..services import external_meetings as service
from ..services import external_meeting_providers as providers
from ..services.access import lock_channel_writes
from ..services.account_security import account_limit, ensure_reauthentication, seal_private, state_for, unseal_private
from ..services.auth import get_current_user, security, token_digest
from ..services.integration_content import CalendarFields
from ..services.matrix_bridge import is_secure_channel, validate_secure_event
from ..services.request_guard import recheck_actor
from ..services.sso_scope import require_unrestricted
from ..services.workspaces import lock_workspace, require_workspace_admin


def private(response: Response):
    response.headers.update({"Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer"})


router = APIRouter(tags=["external meeting providers"], dependencies=[Depends(private)])
oauth_router = APIRouter(tags=["external meeting providers"])
COOKIE = "fc_external_meeting_oauth"


class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Reauthenticate(Empty):
    password: str = Field(min_length=1, max_length=1024)
    code: str | None = Field(default=None, max_length=64)


class ConnectionDraft(Reauthenticate):
    name: str = Field(min_length=1, max_length=80)
    provider: Literal["google_meet", "microsoft_teams", "zoom"]
    channel_id: str = Field(min_length=1, max_length=128)
    config: dict = Field(default_factory=dict)
    client_secret: SecretStr | None = Field(default=None, max_length=4096)
    source_connection_id: str | None = Field(default=None, max_length=80)
    provider_disclosure: bool = False
    expected_revision: int | None = Field(default=None, ge=1)


class MeetingFields(Empty):
    title: str = Field(min_length=1, max_length=200)
    starts_at: str = Field(min_length=1, max_length=64)
    ends_at: str = Field(min_length=1, max_length=64)
    timezone: str = Field(default="UTC", min_length=1, max_length=100)

    def normalized(self):
        value = CalendarFields(**self.model_dump()).model_dump(mode="json")
        start, end = (datetime.fromisoformat(value[key].replace("Z", "+00:00")) for key in ("starts_at", "ends_at"))
        if end - start > timedelta(days=1):
            raise HTTPException(422, "Use a timed meeting of at most 24 hours")
        return {key: value[key] for key in ("title", "starts_at", "ends_at", "timezone")}


class CreateMeeting(Empty):
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{16,80}$")
    connection_id: str = Field(min_length=1, max_length=80)
    connection_revision: int = Field(ge=1)
    fields: MeetingFields
    provider_disclosure: bool = False


class MeetingAction(Empty):
    expected_revision: int = Field(ge=1)
    expected_calendar_revision: int | None = Field(default=None, ge=1)
    provider_disclosure: bool = False


class CalendarLink(Empty):
    expected_revision: int = Field(ge=1)
    calendar_item_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    calendar_revision: int = Field(ge=1)
    matrix_event_id: str = Field(min_length=1, max_length=1024)


def callback_url():
    origin = urlsplit(settings.public_app_url)
    local = origin.hostname in {"localhost", "127.0.0.1", "::1"} and settings.environment != "production"
    if (not origin.hostname or origin.username or origin.password or origin.query or origin.fragment
            or (origin.scheme != "https" and not (local and origin.scheme == "http"))):
        raise HTTPException(503, "The public application origin is not configured correctly")
    return urlunsplit((origin.scheme, origin.netloc, "/api/external-meetings/oauth/callback", "", ""))


def admin(session, workspace_id, actor, data):
    require_unrestricted(session)
    state_for(session, actor.id)
    account_limit(session, "external-meeting-config", actor.id, 30, 300)
    session.execute(update(AccountSecurity).where(AccountSecurity.user_id == actor.id).values(auth_version=AccountSecurity.auth_version))
    recheck_actor(session, actor.id)
    require_workspace_admin(session, workspace_id, actor.id)
    state = ensure_reauthentication(session, session.get(User, actor.id, populate_existing=True), data.password, data.code)
    return state.auth_version


def draft_material(session, workspace_id, data, actor_id):
    if not data.provider_disclosure:
        raise HTTPException(422, "Consent to disclose explicitly scheduled meetings to the provider is required")
    service.require_channel(session, data.channel_id, actor_id, workspace_id=workspace_id, admin=True)
    name = data.name.strip()
    if not name or any(ord(c) < 32 for c in name):
        raise HTTPException(422, "Use a provider connection name without control characters")
    source = None
    if data.source_connection_id:
        source = session.get(RemoteSyncConnection, data.source_connection_id, populate_existing=True)
        if (not source or source.workspace_id != workspace_id or source.channel_id != data.channel_id or source.kind != "calendar"
                or source.provider != providers.PROVIDERS[data.provider] or data.provider == "zoom"):
            raise HTTPException(422, "Choose an existing calendar OAuth connection for this channel and provider")
        service.remote_sync.require_native_grant(session, source)
        public = {key: value for key, value in json.loads(source.config_json).items() if key in {"client_id", "tenant_id", "calendar_id"}}
        secret = service.remote_sync.credentials(source)
        providers.check_scope(data.provider, secret)
        if data.config or data.client_secret:
            raise HTTPException(422, "A reused calendar grant retains its own OAuth app configuration")
    else:
        public = data.config
    try:
        public = providers.config(data.provider, public)
    except providers.ProviderError:
        raise HTTPException(422, "Check the customer OAuth app, tenant and calendar identifiers") from None
    return name, public, source


@router.get("/workspaces/{workspace_id}/external-meetings/connections")
def list_connections(workspace_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    require_unrestricted(session); require_workspace_admin(session, workspace_id, user.id)
    rows = session.exec(select(ExternalMeetingConnection).where(ExternalMeetingConnection.workspace_id == workspace_id).limit(service.MAX_CONNECTIONS)).all()
    available = []
    for row in rows:
        try: service.require_channel(session, row.channel_id, user.id, workspace_id=workspace_id, admin=True)
        except HTTPException: continue
        available.append(service.public_connection(row, admin=True))
    return available


@router.post("/workspaces/{workspace_id}/external-meetings/connections", status_code=201)
def create_connection(workspace_id: str, data: ConnectionDraft, user=Depends(get_current_user), session=Depends(get_session)):
    service.feature(); admin(session, workspace_id, user, data)
    lock_workspace(session, workspace_id); lock_channel_writes(session, data.channel_id); recheck_actor(session, user.id)
    try: name, public, source = draft_material(session, workspace_id, data, user.id)
    except providers.ProviderError:
        raise HTTPException(422, "The calendar OAuth grant needs explicit calendar write consent") from None
    count = session.exec(select(func.count()).select_from(ExternalMeetingConnection).where(ExternalMeetingConnection.workspace_id == workspace_id)).one()
    if count >= service.MAX_CONNECTIONS: raise HTTPException(409, "This workspace has reached its meeting provider limit")
    row = ExternalMeetingConnection(workspace_id=workspace_id, channel_id=data.channel_id, created_by=user.id, grant_user_id=user.id,
        name=name, provider=data.provider, config_json=json.dumps(public), source_connection_id=source.id if source else None,
        source_revision=source.revision if source else None, status="connected" if source else "oauth_required")
    if data.client_secret:
        service.save_credentials(row, {"client_secret": data.client_secret.get_secret_value()})
    session.add(row); session.commit()
    return service.public_connection(row, admin=True)


@router.put("/workspaces/{workspace_id}/external-meetings/connections/{connection_id}")
def update_connection(workspace_id: str, connection_id: str, data: ConnectionDraft, user=Depends(get_current_user), session=Depends(get_session)):
    service.feature(); admin(session, workspace_id, user, data)
    row = service.connection(session, workspace_id, connection_id, lock=True); recheck_actor(session, user.id)
    service.require_channel(session, row.channel_id, user.id, workspace_id=workspace_id, admin=True)
    if row.revision != data.expected_revision: raise HTTPException(409, "The provider connection changed; reload it")
    if data.channel_id != row.channel_id or data.provider != row.provider:
        raise HTTPException(409, "Create a new connection to change the channel or provider")
    try: name, public, source = draft_material(session, workspace_id, data, user.id)
    except providers.ProviderError: raise HTTPException(422, "The calendar OAuth grant needs explicit calendar write consent") from None
    # Editing configuration always discards the old delegated grant. A different
    # admin never inherits another user's OAuth tokens through a metadata edit.
    row.name, row.config_json, row.grant_user_id = name, json.dumps(public), user.id
    row.source_connection_id, row.source_revision = (source.id, source.revision) if source else (None, None)
    service.save_credentials(row, {"client_secret": data.client_secret.get_secret_value()} if data.client_secret else {})
    row.revision += 1; row.enabled = False; row.tested_revision = row.tested_at = None
    row.status, row.updated_at, row.consented_at = "connected" if source else "oauth_required", datetime.utcnow(), datetime.utcnow()
    session.add(row); session.commit(); return service.public_connection(row, admin=True)


@router.post("/workspaces/{workspace_id}/external-meetings/connections/{connection_id}/test")
def test_connection(workspace_id: str, connection_id: str, data: Reauthenticate, user=Depends(get_current_user), session=Depends(get_session)):
    service.feature(); admin(session, workspace_id, user, data)
    row = service.connection(session, workspace_id, connection_id, lock=True); recheck_actor(session, user.id)
    service.require_channel(session, row.channel_id, user.id, workspace_id=workspace_id, admin=True)
    expected = row.revision
    try: service.call(session, row, user.id, providers.test)
    except providers.ProviderError as error:
        raise HTTPException(422, {"code": error.code, "message": "Provider test failed. Check OAuth consent, calendar access and the provider licence."}) from None
    row = service.current(session, connection_id, expected, user.id, enabled=False)
    service.require_channel(session, row.channel_id, user.id, workspace_id=workspace_id, admin=True)
    row.tested_revision, row.tested_at, row.enabled, row.status = row.revision, datetime.utcnow(), True, "ready"
    session.add(row); session.commit(); return service.public_connection(row, admin=True)


@router.post("/workspaces/{workspace_id}/external-meetings/connections/{connection_id}/disconnect")
def disconnect_connection(workspace_id: str, connection_id: str, data: Reauthenticate, user=Depends(get_current_user), session=Depends(get_session)):
    admin(session, workspace_id, user, data)
    row = service.connection(session, workspace_id, connection_id, lock=True); recheck_actor(session, user.id)
    service.require_channel(session, row.channel_id, user.id, workspace_id=workspace_id, admin=True)
    row.enabled, row.status, row.encrypted_credentials = False, "disconnected", None
    row.revision += 1; row.tested_at = row.tested_revision = None; row.updated_at = datetime.utcnow()
    session.add(row); session.commit(); return service.public_connection(row, admin=True)


@router.post("/workspaces/{workspace_id}/external-meetings/connections/{connection_id}/oauth/start")
def oauth_start(workspace_id: str, connection_id: str, data: Reauthenticate, response: Response, user=Depends(get_current_user), credential=Depends(security), session=Depends(get_session)):
    service.feature(); auth_version = admin(session, workspace_id, user, data)
    row = service.connection(session, workspace_id, connection_id, lock=True); recheck_actor(session, user.id)
    service.require_channel(session, row.channel_id, user.id, workspace_id=workspace_id, admin=True)
    if row.source_connection_id: raise HTTPException(409, "Authorize the selected calendar connection in synchronization settings")
    state, verifier, browser = secrets.token_urlsafe(32), secrets.token_urlsafe(48), secrets.token_urlsafe(32)
    digest = token_digest(state); challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    secret = service.credentials(row); secret = {"client_secret": secret["client_secret"]} if secret.get("client_secret") else {}
    try: url = providers.authorization_url(row.provider, json.loads(row.config_json), secret, callback_url(), state, challenge)
    except providers.ProviderError: raise HTTPException(422, "The customer OAuth app configuration is incomplete") from None
    service.save_credentials(row, secret)
    row.revision += 1; row.enabled = False; row.status = "oauth_pending"; row.tested_revision = row.tested_at = None
    row.grant_user_id = user.id; row.consented_at = datetime.utcnow()
    # Bound pending/expired attempts per connection. Used attempts are metadata,
    # not refresh credentials; retaining 20 is enough for concurrent tabs.
    attempts = session.exec(select(ExternalMeetingOAuthAttempt).where(ExternalMeetingOAuthAttempt.connection_id == row.id)
        .order_by(ExternalMeetingOAuthAttempt.expires_at.desc())).all()
    for attempt in attempts[19:]: session.delete(attempt)
    session.add(ExternalMeetingOAuthAttempt(state_hash=digest, connection_id=row.id, revision=row.revision, user_id=user.id,
        native_token_hash=token_digest(credential.credentials), auth_version=auth_version, browser_hash=token_digest(browser),
        encrypted_verifier=seal_private(verifier, "external-meeting-pkce", digest), expires_at=datetime.utcnow() + timedelta(minutes=10)))
    session.add(row); session.commit()
    response.set_cookie(COOKIE, browser, max_age=600, httponly=True, secure=settings.environment == "production", samesite="lax", path="/")
    return {"authorization_url": url, "callback_url": callback_url()}


def callback_actor(session, attempt):
    session.execute(update(AccountSecurity).where(AccountSecurity.user_id == attempt.user_id).values(auth_version=AccountSecurity.auth_version))
    session.execute(update(ActiveSession).where(ActiveSession.token_hash == attempt.native_token_hash).values(revoked_at=ActiveSession.revoked_at))
    row = session.get(ExternalMeetingConnection, attempt.connection_id, populate_existing=True)
    if not row or row.revision != attempt.revision: raise HTTPException(403, "Provider consent changed")
    row = service.connection(session, row.workspace_id, row.id, lock=True)
    actor = session.get(User, attempt.user_id, populate_existing=True)
    active = session.exec(select(ActiveSession).where(ActiveSession.token_hash == attempt.native_token_hash).execution_options(populate_existing=True)).first()
    version = session.get(AccountSecurity, attempt.user_id, populate_existing=True)
    if (row.revision != attempt.revision or not actor or actor.is_blocked or not active or active.user_id != attempt.user_id
            or active.revoked_at or active.expires_at <= datetime.utcnow() or not version or version.auth_version != attempt.auth_version
            or session.get(RevokedToken, attempt.native_token_hash)):
        raise HTTPException(403, "Native authorization expired")
    service.require_channel(session, row.channel_id, actor.id, workspace_id=row.workspace_id, admin=True)
    service.grant(session, row)
    return row


@oauth_router.get("/external-meetings/oauth/callback")
def oauth_callback(request: Request, code: str | None = Query(default=None, max_length=8192), state: str | None = Query(default=None, max_length=128), error: str | None = Query(default=None, max_length=128), session=Depends(get_session)):
    origin = urlsplit(settings.public_app_url); fragment = {"external_meeting_error": "authorization_failed"}
    try:
        service.feature()
        attempt = session.get(ExternalMeetingOAuthAttempt, token_digest(state)) if state else None
        browser = request.cookies.get(COOKIE, "")
        if error or not code or not attempt or attempt.used_at or attempt.expires_at <= datetime.utcnow() or not browser or not hmac.compare_digest(attempt.browser_hash, token_digest(browser)):
            raise HTTPException(403, "Provider authorization expired")
        row = callback_actor(session, attempt)
        claim = session.execute(update(ExternalMeetingOAuthAttempt).where(ExternalMeetingOAuthAttempt.state_hash == attempt.state_hash, ExternalMeetingOAuthAttempt.used_at.is_(None)).values(used_at=datetime.utcnow()))
        if claim.rowcount != 1: raise HTTPException(403, "Provider authorization already consumed")
        verifier = unseal_private(attempt.encrypted_verifier, "external-meeting-pkce", attempt.state_hash)
        public, secret, provider = json.loads(row.config_json), service.credentials(row), row.provider
        session.commit()
        token = providers.exchange_code(provider, public, secret, callback_url(), code, verifier)
        row = callback_actor(session, attempt)
        secret.update(token); service.save_credentials(row, secret)
        row.status, row.enabled, row.updated_at = "connected", False, datetime.utcnow()
        session.add(row); session.commit()
        fragment = {"external_meeting_connected": row.id, "workspace_id": row.workspace_id}
    except (HTTPException, providers.ProviderError, ValueError, TypeError, KeyError):
        session.rollback()
    response = RedirectResponse(urlunsplit((origin.scheme, origin.netloc, "/", "", urlencode(fragment))), status_code=303)
    private(response); response.delete_cookie(COOKIE, path="/"); return response


@router.get("/channels/{channel_id}/external-meetings")
def list_meetings(channel_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    service.require_channel(session, channel_id, user.id)
    rows = session.exec(select(ExternalMeeting).where(ExternalMeeting.channel_id == channel_id).order_by(ExternalMeeting.created_at.desc()).limit(100)).all()
    connections = session.exec(select(ExternalMeetingConnection).where(ExternalMeetingConnection.channel_id == channel_id).limit(service.MAX_CONNECTIONS)).all()
    available = []
    for row in connections:
        try: service.grant(session, row)
        except HTTPException: continue
        available.append(service.public_connection(row))
    return {"enabled": bool(getattr(settings, "external_meetings_enabled", False)), "connections": available,
        "meetings": [service.public_meeting(session, row) for row in rows]}


def provider_failure(session, row_id, channel_id, actor_id, error):
    session.rollback()
    try:
        row = service.meeting(session, channel_id, row_id, actor_id, lock=True, manage=True)
        row.last_error = error.code; row.updated_at = datetime.utcnow(); session.add(row); session.commit()
    except HTTPException: session.rollback()
    raise HTTPException(422, {"code": error.code, "message": "The provider result is unavailable. Recover the existing request before creating another meeting."}) from None


@router.post("/channels/{channel_id}/external-meetings", status_code=201)
def create_meeting(channel_id: str, data: CreateMeeting, user=Depends(get_current_user), session=Depends(get_session)):
    service.feature()
    if not data.provider_disclosure: raise HTTPException(422, "Explicit disclosure of this meeting title, time and account to the provider is required")
    try: fields = data.fields.normalized()
    except ValueError: raise HTTPException(422, "Use valid meeting dates and an end after its start") from None
    account_limit(session, "external-meeting-create", user.id, 20, 300)
    connection = session.get(ExternalMeetingConnection, data.connection_id)
    if not connection or connection.channel_id != channel_id: raise HTTPException(404, "Meeting provider connection not found")
    connection = service.current(session, connection.id, data.connection_revision, user.id)
    digest = service.request_hash(connection.id, fields)
    previous = session.exec(select(ExternalMeeting).where(ExternalMeeting.channel_id == channel_id, ExternalMeeting.request_id == data.request_id)).first()
    if previous:
        if previous.created_by != user.id or previous.request_hash != digest: raise HTTPException(409, "This request identifier was already used for different meeting details")
        if previous.state in {"uncertain", "cancelling"}: raise HTTPException(409, "The previous provider operation is uncertain. Recover that request before trying again.")
        return service.public_meeting(session, previous)
    service.material(session, connection)
    count = session.exec(select(func.count()).select_from(ExternalMeeting).where(ExternalMeeting.channel_id == channel_id)).one()
    if count >= service.MAX_MEETINGS: raise HTTPException(409, "This channel has reached its external meeting history limit")
    secure = is_secure_channel(session, channel_id)
    row = ExternalMeeting(request_id=data.request_id, connection_id=connection.id, connection_revision=connection.revision,
        channel_id=channel_id, created_by=user.id, request_hash=digest, secure=secure, state="uncertain")
    if not secure: row.encrypted_request = service.encrypt_message(json.dumps(fields), channel_id)
    session.add(row); session.commit()
    connection = service.current(session, row.connection_id, row.connection_revision, user.id)
    if row.secure != is_secure_channel(session, channel_id): raise HTTPException(409, "The channel encryption mode changed")
    try:
        remote = service.call(session, connection, user.id, providers.create, row.id, fields)
        service.current(session, connection.id, row.connection_revision, user.id)
        row = service.meeting(session, channel_id, row.id, user.id, lock=True, manage=True)
        service.persist_result(session, row, remote, user.id, fields=fields)
        session.commit()
        return service.public_meeting(session, row, explicit_result=remote)
    except providers.ProviderError as error: provider_failure(session, row.id, channel_id, user.id, error)


@router.post("/channels/{channel_id}/external-meetings/{meeting_id}/recover")
def recover_meeting(channel_id: str, meeting_id: str, data: MeetingAction, user=Depends(get_current_user), session=Depends(get_session)):
    service.feature()
    if not data.provider_disclosure: raise HTTPException(422, "Confirm the explicit provider lookup before recovering this meeting")
    account_limit(session, "external-meeting-recover", user.id, 30, 300)
    row = service.meeting(session, channel_id, meeting_id, user.id, lock=True, manage=True)
    if row.revision != data.expected_revision or row.state in {"cancelled", "cancelling"}: raise HTTPException(409, "The meeting changed; reload its current state")
    connection = service.current(session, row.connection_id, row.connection_revision, user.id)
    if row.secure != is_secure_channel(session, channel_id): raise HTTPException(409, "The channel encryption mode changed; use the provider to review the existing meeting")
    old = service.result(row)
    try:
        remote = service.call(session, connection, user.id, providers.read, old["id"]) if old.get("id") else service.call(session, connection, user.id, providers.reconcile, row.id)
        service.current(session, connection.id, row.connection_revision, user.id)
        row = service.meeting(session, channel_id, meeting_id, user.id, lock=True, manage=True)
        if row.remote_fingerprint and remote.get("join_url") and row.state == "ready" and providers.fingerprint(remote) != row.remote_fingerprint:
            raise HTTPException(409, "The provider meeting changed. Review it at the provider before changing the linked calendar.")
        fields = None if row.secure else json.loads(service.decrypt_message(row.encrypted_request, channel_id))
        service.persist_result(session, row, remote, user.id, fields=fields)
        row.revision += 1; session.add(row); session.commit()
        return service.public_meeting(session, row, explicit_result=remote)
    except providers.ProviderError as error: provider_failure(session, row.id, channel_id, user.id, error)


@router.post("/channels/{channel_id}/external-meetings/{meeting_id}/calendar-link")
def attach_secure_calendar(channel_id: str, meeting_id: str, data: CalendarLink, user=Depends(get_current_user), session=Depends(get_session)):
    row = service.meeting(session, channel_id, meeting_id, user.id, lock=True, manage=True)
    if not row.secure or not is_secure_channel(session, channel_id) or row.state not in {"ready", "awaiting_secure_calendar", "cancelled"} or row.revision != data.expected_revision:
        raise HTTPException(409, "This meeting cannot accept that encrypted calendar reference")
    if data.calendar_item_id != "meeting_" + row.id: raise HTTPException(422, "Use the calendar identity assigned to this meeting")
    item = session.get(IntegrationContent, data.calendar_item_id)
    revision = service.latest_calendar(session, data.calendar_item_id)
    if (not item or item.channel_id != channel_id or item.kind != "calendar" or item.created_by != row.created_by
            or not revision or revision.revision != data.calendar_revision or revision.content_mode != "matrix"
            or revision.matrix_event_id != data.matrix_event_id or revision.deleted != (row.state == "cancelled")):
        raise HTTPException(409, "The encrypted calendar reference could not be verified")
    validate_secure_event(session, channel_id, data.matrix_event_id, user.id)
    row.calendar_item_id, row.calendar_revision = item.id, revision.revision
    if row.state != "cancelled": row.state = "ready"
    row.revision += 1; row.updated_at = datetime.utcnow(); session.add(row); session.commit()
    return service.public_meeting(session, row)


@router.post("/channels/{channel_id}/external-meetings/{meeting_id}/cancel")
def cancel_meeting(channel_id: str, meeting_id: str, data: MeetingAction, user=Depends(get_current_user), session=Depends(get_session)):
    service.feature()
    if not data.provider_disclosure: raise HTTPException(422, "Confirm cancellation at the external provider")
    account_limit(session, "external-meeting-cancel", user.id, 20, 300)
    row = service.meeting(session, channel_id, meeting_id, user.id, lock=True, manage=True)
    if row.revision != data.expected_revision: raise HTTPException(409, "The meeting changed; reload it")
    if row.state == "cancelled": return service.public_meeting(session, row)
    connection = service.current(session, row.connection_id, row.connection_revision, user.id)
    if row.secure != is_secure_channel(session, channel_id): raise HTTPException(409, "The channel encryption mode changed; review this meeting at the provider")
    calendar = service.check_calendar(session, row, data.expected_calendar_revision)
    old = service.result(row)
    if not old.get("id"): raise HTTPException(409, "Recover the existing provider meeting before cancelling it")
    row.state = "cancelling"; session.add(row); session.commit()
    connection = service.current(session, row.connection_id, row.connection_revision, user.id)
    try:
        try: remote = service.call(session, connection, user.id, providers.read, old["id"])
        except providers.ProviderError as error:
            if error.status != 404: raise
            remote = None
        if remote is not None and not remote.get("deleted"):
            if providers.fingerprint(remote) != row.remote_fingerprint:
                raise HTTPException(409, "The provider meeting changed. Review it at the provider before cancelling.")
            service.call(session, connection, user.id, providers.cancel, old["id"], remote.get("etag"))
        service.current(session, connection.id, row.connection_revision, user.id)
        row = service.meeting(session, channel_id, meeting_id, user.id, lock=True, manage=True)
        calendar = service.check_calendar(session, row, data.expected_calendar_revision)
        service.tombstone_calendar(session, row, calendar, user.id)
        row.state, row.last_error, row.updated_at = "cancelled", None, datetime.utcnow(); row.revision += 1
        session.add(row); session.commit(); return service.public_meeting(session, row)
    except providers.ProviderError as error: provider_failure(session, row.id, channel_id, user.id, error)
