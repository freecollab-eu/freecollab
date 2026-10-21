"""Native administrators grant one channel to one customer-owned remote collection."""
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
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import update
from sqlmodel import select

from ..config import settings
from ..database import get_session
from ..models.account_security import AccountSecurity
from ..models.channel import ChannelMember
from ..models.remote_sync import (RemoteSyncActivity, RemoteSyncConnection, RemoteSyncConflict,
                                  RemoteSyncItem, RemoteSyncOAuthAttempt)
from ..models.session import ActiveSession, RevokedToken
from ..models.user import User
from ..services import remote_providers as providers
from ..services import remote_sync as sync
from ..services.account_security import (account_limit, ensure_reauthentication, seal_private,
                                          state_for, unseal_private)
from ..services.auth import active_user_for_token, get_current_user, security, token_digest, validate_password
from ..services.access import require_channel_member
from ..services.sso_scope import require_unrestricted
from ..services.workspaces import require_workspace_admin

router = APIRouter(prefix="/workspaces/{workspace_id}/sync", tags=["remote synchronization"])
oauth_router = APIRouter(tags=["remote synchronization"])
COOKIE = "fc_sync_browser"


class Reauthenticate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str
    code: str | None = Field(default=None, max_length=64)
    _password = field_validator("password")(validate_password)


class ConnectionDraft(Reauthenticate):
    name: str = Field(min_length=1, max_length=80)
    provider: Literal["google", "microsoft", "nextcloud", "caldav"]
    kind: Literal["files", "calendar"]
    channel_id: str = Field(min_length=1, max_length=128)
    config: dict
    credentials: dict | None = None
    consent: bool = False

    @field_validator("name")
    @classmethod
    def name_text(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError("A connection needs a name without control characters")
        return value

    @field_validator("config")
    @classmethod
    def safe_config(cls, value):
        if len(json.dumps(value)) > 16000 or any(key.lower() in {
            "client_secret", "password", "app_password", "access_token", "refresh_token", "token", "authorization"
        } for key in value):
            raise ValueError("Place private credentials in the separate credentials field")
        return value

    @field_validator("credentials")
    @classmethod
    def secret_fields(cls, value):
        if value is None:
            return value
        if set(value) - {"client_secret", "username", "app_password"}:
            raise ValueError("Unsupported credential field")
        if any(item is not None and (not isinstance(item, str) or len(item) > 4096
               or any(ord(c) < 32 for c in item)) for item in value.values()):
            raise ValueError("Invalid credential value")
        return value


class ConflictChoice(Reauthenticate):
    choice: Literal["local", "remote"]


def _private(response):
    response.headers.update({"Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer"})


def callback_url():
    try:
        parsed = urlsplit(settings.public_app_url)
        local = parsed.hostname in {"localhost", "127.0.0.1", "::1"} and settings.environment != "production"
        if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
                or (parsed.scheme != "https" and not (local and parsed.scheme == "http"))):
            raise ValueError()
        parsed.port
        return urlunsplit((parsed.scheme, parsed.netloc, "/api/sync/oauth/callback", "", ""))
    except (ValueError, TypeError):
        raise HTTPException(503, "The public application origin is not configured correctly") from None


def _feature():
    if not sync.enabled():
        raise HTTPException(503, "Remote synchronization is disabled on this installation")


def _get(session, workspace_id, connection_id):
    row = session.get(RemoteSyncConnection, connection_id, populate_existing=True)
    if not row or row.workspace_id != workspace_id:
        raise HTTPException(404, "Sync connection not found")
    return row


def _channel_admin(session, row, user):
    member = require_channel_member(session, row.channel_id, user.id)
    if member.role != "admin":
        raise HTTPException(403, "Channel administrator access required")
    return row


def _native_actor(session, actor, credential):
    session.execute(update(AccountSecurity).where(AccountSecurity.user_id == actor.id)
                    .values(auth_version=AccountSecurity.auth_version))
    session.expire_all()
    user = active_user_for_token(credential.credentials, session)
    if user is None:
        raise HTTPException(401, "Account security changed. Please sign in again.")
    require_unrestricted(session)
    return user


def _authorize(session, workspace_id, actor, credential, data):
    require_unrestricted(session)
    state_for(session, actor.id)
    account_limit(session, "remote-sync-config", actor.id, 30, 300)
    actor = _native_actor(session, actor, credential)
    require_workspace_admin(session, workspace_id, actor.id)
    state = ensure_reauthentication(session, actor, data.password, data.code)
    return actor, state.auth_version


def _read(session, workspace_id, user):
    require_unrestricted(session)
    require_workspace_admin(session, workspace_id, user.id)


def _configuration(data):
    try:
        config = providers.validate_config(data.provider, data.kind, data.config)
    except providers.ProviderError:
        raise HTTPException(422, "Check the selected provider, resource identifiers and public HTTPS collection URL") from None
    # Unknown config fields cannot smuggle secrets into API responses.
    allowed = {"kind", "client_id", "tenant_id", "folder_id", "drive_id", "calendar_id", "base_url", "timezone"}
    if set(config) - allowed:
        raise HTTPException(422, "Unsupported provider configuration field")
    return config


def _merge_credentials(row, data, old_identity=None, grant_changed=False):
    secret = sync.credentials(row)
    current = json.loads(row.config_json)
    identity = (row.provider, current.get("client_id"), current.get("tenant_id"))
    if old_identity is not None and identity != old_identity:
        # An app secret belongs to this exact provider/client/tenant. A saved
        # credential must never be forwarded to a new application or origin.
        secret = {}
    elif grant_changed:
        # Account access was granted for one selected channel/collection.
        # Changing that scope requires fresh account authorization, even before
        # the first worker run. A fixed OAuth app secret may remain unchanged.
        secret = {"client_secret": secret["client_secret"]} if row.provider in {"google", "microsoft"} and secret.get("client_secret") else {}
    replacement_username = (data.credentials or {}).get("username")
    if row.provider in {"nextcloud", "caldav"} and replacement_username and replacement_username != secret.get("username"):
        # A new DAV username is a different provider account even when the URL
        # stays fixed. Never pair it with another account's retained password.
        secret.pop("app_password", None)
    for key, value in (data.credentials or {}).items():
        if value is None:
            secret.pop(key, None)
        elif value:
            secret[key] = value
    sync.save_credentials(row, secret)


def _credentials_ready(row):
    secret = sync.credentials(row)
    if row.provider in {"google", "microsoft"}:
        return bool(secret.get("access_token"))
    return bool(secret.get("username") and secret.get("app_password"))


def _unique_collection(session, row):
    # Two connections to the exact same collection in the same channel would
    # echo each other's imports into duplicate local documents indefinitely.
    keys = {"base_url", "folder_id", "drive_id", "calendar_id", "tenant_id"}
    identity = {key: value for key, value in json.loads(row.config_json).items() if key in keys}
    others = session.exec(select(RemoteSyncConnection).where(RemoteSyncConnection.workspace_id == row.workspace_id,
        RemoteSyncConnection.channel_id == row.channel_id, RemoteSyncConnection.provider == row.provider,
        RemoteSyncConnection.kind == row.kind, RemoteSyncConnection.id != row.id)).all()
    if any({key: value for key, value in json.loads(other.config_json).items() if key in keys} == identity for other in others):
        raise HTTPException(409, "This channel already has a connection to the selected collection; edit or reconnect it instead")


@router.get("")
def list_connections(workspace_id: str, response: Response, user=Depends(get_current_user), session=Depends(get_session)):
    _read(session, workspace_id, user)
    _private(response)
    rows = session.exec(select(RemoteSyncConnection).where(RemoteSyncConnection.workspace_id == workspace_id)
                        .order_by(RemoteSyncConnection.created_at)).all()
    return {"enabled": sync.enabled(), "callback_url": callback_url(),
            "connections": [sync.safe_connection(session, row) for row in rows
                if (member := session.get(ChannelMember, (row.channel_id, user.id))) and member.role == "admin"]}


@router.post("")
def create_connection(workspace_id: str, data: ConnectionDraft, response: Response, user=Depends(get_current_user),
                      credential=Depends(security), session=Depends(get_session)):
    _private(response)
    actor, _ = _authorize(session, workspace_id, user, credential, data)
    if data.consent is not True:
        raise HTTPException(422, "Explicit consent to share existing and future selected channel content is required")
    rows = session.exec(select(RemoteSyncConnection.id).where(RemoteSyncConnection.workspace_id == workspace_id)).all()
    if len(rows) >= 20:
        raise HTTPException(409, "This workspace has reached its sync connection limit")
    row = RemoteSyncConnection(workspace_id=workspace_id, channel_id=data.channel_id, created_by=actor.id,
        grant_user_id=actor.id, name=data.name, provider=data.provider, kind=data.kind,
        config_json=json.dumps(_configuration(data), separators=(",", ":")))
    sync.require_native_grant(session, row, lock=True)
    _unique_collection(session, row)
    _merge_credentials(row, data)
    row.status = "oauth_required" if row.provider in {"google", "microsoft"} else "draft"
    session.add(row)
    session.flush()
    sync.activity(session, row, "connection.created")
    session.commit()
    return sync.safe_connection(session, row)


@router.put("/{connection_id}")
def update_connection(workspace_id: str, connection_id: str, data: ConnectionDraft, response: Response,
                      user=Depends(get_current_user), credential=Depends(security), session=Depends(get_session)):
    _private(response)
    actor, _ = _authorize(session, workspace_id, user, credential, data)
    if data.consent is not True:
        raise HTTPException(422, "Explicit consent to share existing and future selected channel content is required")
    row = _get(session, workspace_id, connection_id)
    _channel_admin(session, row, actor)
    config = json.loads(row.config_json)
    identity = (row.provider, config.get("client_id"), config.get("tenant_id"))
    old_channel, old_kind = row.channel_id, row.kind
    if row.channel_id != data.channel_id or row.provider != data.provider or row.kind != data.kind:
        if session.exec(select(RemoteSyncItem.id).where(RemoteSyncItem.connection_id == row.id)).first():
            raise HTTPException(409, "Create a new connection to change the channel or provider after synchronization")
    row.name, row.provider, row.kind, row.channel_id = data.name, data.provider, data.kind, data.channel_id
    next_config = _configuration(data)
    old_resource = {key: value for key, value in config.items() if key not in {"client_id", "kind", "timezone"}}
    new_resource = {key: value for key, value in next_config.items() if key not in {"client_id", "kind", "timezone"}}
    if old_resource != new_resource and session.exec(select(RemoteSyncItem.id).where(RemoteSyncItem.connection_id == row.id)).first():
        raise HTTPException(409, "Create a new connection to select a different remote collection after synchronization")
    row.config_json = json.dumps(next_config, separators=(",", ":"))
    grant_changed = (old_resource != new_resource or old_channel != row.channel_id or old_kind != row.kind
                     or identity != (row.provider, next_config.get("client_id"), next_config.get("tenant_id")))
    fresh_dav_credentials = row.provider in {"nextcloud", "caldav"} and all(
        isinstance((data.credentials or {}).get(key), str) and (data.credentials or {}).get(key)
        for key in ("username", "app_password"))
    if grant_changed or fresh_dav_credentials:
        row.grant_user_id = actor.id
    # Retained credentials stay bound to their original authorizing account.
    # A metadata edit by another admin cannot bypass that person's offboarding.
    row.enabled = False
    sync.require_native_grant(session, row, lock=True)
    _unique_collection(session, row)
    _merge_credentials(row, data, identity, grant_changed)
    row.revision += 1
    row.tested_at = row.tested_revision = row.encrypted_cursor = row.lease_token = row.lease_expires_at = None
    row.status = "draft" if _credentials_ready(row) else "oauth_required" if row.provider in {"google", "microsoft"} else "draft"
    row.updated_at = row.consented_at = datetime.utcnow()
    row.last_error = None
    session.add(row)
    sync.activity(session, row, "connection.updated")
    session.commit()
    return sync.safe_connection(session, row)


def _test_saved(session, row, actor, credential):
    _feature()
    if not _credentials_ready(row):
        raise HTTPException(409, "Connect the provider or save an application password before testing")
    sync.require_native_grant(session, row, lock=True)
    actor = _native_actor(session, actor, credential)
    require_workspace_admin(session, row.workspace_id, actor.id)
    _channel_admin(session, row, actor)
    revision = row.revision
    secret = sync.credentials(row)
    request_connection = sync.frozen_connection(row)
    session.commit()
    try:
        sync._provider_call(session, request_connection, secret, providers.snapshot, cursor=None)
    except providers.ProviderError:
        raise HTTPException(422, "Provider test failed. Check authorization and the selected resource.") from None
    actor = _native_actor(session, actor, credential)
    current = _get(session, row.workspace_id, row.id)
    sync.require_native_grant(session, current, lock=True)
    # Provider I/O and waiting for the grant locks can both outlive the acting
    # administrator's permission or the saved connection revision.
    actor = _native_actor(session, actor, credential)
    if current.revision != revision:
        raise HTTPException(409, "The connection changed during the provider test")
    require_workspace_admin(session, current.workspace_id, actor.id)
    _channel_admin(session, current, actor)
    sync.save_credentials(current, secret)
    current.tested_revision, current.tested_at, current.status = revision, datetime.utcnow(), "tested"
    session.add(current)
    return current


@router.post("/{connection_id}/test")
def test_connection(workspace_id: str, connection_id: str, data: Reauthenticate, response: Response,
                    user=Depends(get_current_user), credential=Depends(security), session=Depends(get_session)):
    _private(response)
    actor, _ = _authorize(session, workspace_id, user, credential, data)
    row = _test_saved(session, _channel_admin(session, _get(session, workspace_id, connection_id), actor), actor, credential)
    sync.activity(session, row, "connection.tested")
    session.commit()
    return sync.safe_connection(session, row)


@router.post("/{connection_id}/enable")
def enable_connection(workspace_id: str, connection_id: str, data: Reauthenticate, response: Response,
                      user=Depends(get_current_user), credential=Depends(security), session=Depends(get_session)):
    _private(response)
    actor, _ = _authorize(session, workspace_id, user, credential, data)
    row = _get(session, workspace_id, connection_id)
    _channel_admin(session, row, actor)
    if not sync.safe_connection(session, row)["tested"]:
        raise HTTPException(409, "Test the saved connection before enabling synchronization")
    row = _test_saved(session, row, actor, credential)
    row.enabled, row.status = True, "queued"
    row.revision += 1
    row.tested_revision = row.revision
    row.next_run_at = row.updated_at = datetime.utcnow()
    row.lease_token = row.lease_expires_at = None
    session.add(row)
    sync.activity(session, row, "connection.enabled")
    session.commit()
    return sync.safe_connection(session, row)


@router.post("/{connection_id}/disable")
def disable_connection(workspace_id: str, connection_id: str, data: Reauthenticate, response: Response,
                       user=Depends(get_current_user), credential=Depends(security), session=Depends(get_session)):
    _private(response)
    actor, _ = _authorize(session, workspace_id, user, credential, data)
    row = _channel_admin(session, _get(session, workspace_id, connection_id), actor)
    row.enabled, row.status = False, "disabled"
    row.revision += 1
    row.lease_token = row.lease_expires_at = None
    row.updated_at = datetime.utcnow()
    session.add(row)
    sync.activity(session, row, "connection.disabled")
    session.commit()
    return sync.safe_connection(session, row)


@router.post("/{connection_id}/disconnect")
def disconnect_connection(workspace_id: str, connection_id: str, data: Reauthenticate, response: Response,
                          user=Depends(get_current_user), credential=Depends(security), session=Depends(get_session)):
    _private(response)
    actor, _ = _authorize(session, workspace_id, user, credential, data)
    row = _channel_admin(session, _get(session, workspace_id, connection_id), actor)
    row.enabled, row.status = False, "disconnected"
    row.revision += 1
    row.encrypted_credentials = row.encrypted_cursor = row.lease_token = row.lease_expires_at = None
    row.tested_at = row.tested_revision = None
    session.add(row)
    sync.activity(session, row, "connection.disconnected")
    session.commit()
    return sync.safe_connection(session, row)


@router.post("/{connection_id}/run")
def queue_sync(workspace_id: str, connection_id: str, data: Reauthenticate, response: Response,
               user=Depends(get_current_user), credential=Depends(security), session=Depends(get_session)):
    _private(response)
    _feature()
    actor, _ = _authorize(session, workspace_id, user, credential, data)
    row = _channel_admin(session, _get(session, workspace_id, connection_id), actor)
    sync.require_native_grant(session, row, lock=True)
    if not row.enabled:
        raise HTTPException(409, "Enable this connection before queuing synchronization")
    row.next_run_at, row.status = datetime.utcnow(), "queued"
    session.add(row)
    sync.activity(session, row, "sync.queued")
    session.commit()
    return sync.safe_connection(session, row)


@router.get("/{connection_id}/activity")
def connection_activity(workspace_id: str, connection_id: str, response: Response,
                        user=Depends(get_current_user), session=Depends(get_session)):
    _read(session, workspace_id, user)
    _channel_admin(session, _get(session, workspace_id, connection_id), user)
    _private(response)
    events = session.exec(select(RemoteSyncActivity).where(RemoteSyncActivity.connection_id == connection_id)
                          .order_by(RemoteSyncActivity.created_at.desc()).limit(100)).all()
    return {"events": [{"id": event.id, "event": event.event, "status": event.status,
                         "created_at": event.created_at, "item_id": event.item_id} for event in events]}


@router.get("/{connection_id}/conflicts")
def connection_conflicts(workspace_id: str, connection_id: str, response: Response,
                         user=Depends(get_current_user), session=Depends(get_session)):
    _read(session, workspace_id, user)
    _channel_admin(session, _get(session, workspace_id, connection_id), user)
    _private(response)
    rows = session.exec(select(RemoteSyncConflict).where(RemoteSyncConflict.connection_id == connection_id,
        RemoteSyncConflict.resolved_at.is_(None)).order_by(RemoteSyncConflict.created_at)).all()
    return {"conflicts": [{"id": row.id, "item_id": row.item_id, "reason": row.reason,
        "created_at": row.created_at, "read_only": row.read_only, "resolution": row.resolution,
        "local_id": item.local_id, "remote_id": item.remote_id} for row in rows
        if (item := session.get(RemoteSyncItem, row.item_id))]}


@router.post("/{connection_id}/conflicts/{conflict_id}/resolve")
def resolve_conflict(workspace_id: str, connection_id: str, conflict_id: str, data: ConflictChoice,
                     response: Response, user=Depends(get_current_user), credential=Depends(security), session=Depends(get_session)):
    _private(response)
    _feature()
    actor, _ = _authorize(session, workspace_id, user, credential, data)
    row = _channel_admin(session, _get(session, workspace_id, connection_id), actor)
    sync.require_native_grant(session, row, lock=True)
    conflict = session.get(RemoteSyncConflict, conflict_id)
    if not conflict or conflict.connection_id != connection_id or conflict.resolved_at is not None:
        raise HTTPException(404, "Sync conflict not found")
    if conflict.read_only and data.choice == "local":
        raise HTTPException(409, "This provider item is read-only; choose the remote version")
    conflict.resolution = data.choice
    row.next_run_at, row.status = datetime.utcnow(), "queued" if row.enabled else "disabled"
    session.add(conflict)
    session.add(row)
    sync.activity(session, row, "conflict.resolution_requested", data.choice, conflict.item_id)
    session.commit()
    return {"queued": row.enabled, "choice": data.choice}


@router.post("/{connection_id}/oauth/start")
def oauth_start(workspace_id: str, connection_id: str, data: Reauthenticate, response: Response,
                user=Depends(get_current_user), credential=Depends(security), session=Depends(get_session)):
    _private(response)
    _feature()
    actor, auth_version = _authorize(session, workspace_id, user, credential, data)
    row = _get(session, workspace_id, connection_id)
    _channel_admin(session, row, actor)
    row.enabled = False
    row.revision += 1
    row.status = "oauth_pending"
    row.tested_at = row.tested_revision = row.lease_token = row.lease_expires_at = None
    sync.require_native_grant(session, row, lock=True, actor_id=actor.id)
    if row.provider not in {"google", "microsoft"}:
        raise HTTPException(409, "This provider uses an application password")
    state, verifier, browser = secrets.token_urlsafe(32), secrets.token_urlsafe(48), secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    try:
        secret = sync.credentials(row)
        # Starting a replacement authorization never transfers the old account's
        # tokens to its initiator. Keep only the fixed workspace OAuth app secret.
        secret = {"client_secret": secret["client_secret"]} if secret.get("client_secret") else {}
        sync.save_credentials(row, secret)
        url = providers.authorization_url(row.provider, sync._provider_config(row, secret),
                                          callback_url(), state, challenge)
    except providers.ProviderError:
        raise HTTPException(422, "The OAuth client configuration is incomplete") from None
    session.add(RemoteSyncOAuthAttempt(state_hash=token_digest(state), connection_id=row.id,
        revision=row.revision, user_id=actor.id, native_token_hash=token_digest(credential.credentials),
        auth_version=auth_version, browser_hash=token_digest(browser),
        encrypted_verifier=seal_private(verifier, "remote-sync-pkce", token_digest(state)),
        expires_at=datetime.utcnow() + timedelta(minutes=10)))
    session.commit()
    response.set_cookie(COOKIE, browser, max_age=600, httponly=True,
                        secure=callback_url().startswith("https://"), samesite="lax", path="/")
    return {"authorization_url": url}


def _callback_native_actor(session, attempt):
    session.expire_all()
    tracked = session.exec(select(ActiveSession).where(ActiveSession.token_hash == attempt.native_token_hash)).first()
    version = session.exec(select(AccountSecurity.auth_version).where(AccountSecurity.user_id == attempt.user_id)).first()
    actor = session.get(User, attempt.user_id, populate_existing=True)
    if (not actor or actor.is_blocked or not tracked or tracked.user_id != actor.id
            or tracked.revoked_at or tracked.expires_at <= datetime.utcnow()
            or session.get(RevokedToken, attempt.native_token_hash)
            or version is None or version != attempt.auth_version):
        raise HTTPException(403, "Native authorization expired")
    return actor


def _callback_actor(session, attempt):
    # Serialize native security changes and current-session revocation before
    # taking workspace/channel locks. Revalidate after every lock wait; an ORM
    # refresh must not silently adopt a newer connection into an older consent.
    session.execute(update(AccountSecurity).where(AccountSecurity.user_id == attempt.user_id)
                    .values(auth_version=AccountSecurity.auth_version))
    session.execute(update(ActiveSession).where(ActiveSession.token_hash == attempt.native_token_hash)
                    .values(revoked_at=ActiveSession.revoked_at))
    actor = _callback_native_actor(session, attempt)
    row = session.get(RemoteSyncConnection, attempt.connection_id, populate_existing=True)
    if not row or row.revision != attempt.revision:
        raise HTTPException(403, "Connection authorization changed")
    sync.require_native_grant(session, row, lock=True, actor_id=actor.id)
    _callback_native_actor(session, attempt)
    row = session.get(RemoteSyncConnection, attempt.connection_id, populate_existing=True)
    if not row or row.revision != attempt.revision:
        raise HTTPException(403, "Connection authorization changed")
    return row


@oauth_router.get("/sync/oauth/callback")
def oauth_callback(request: Request, code: str | None = Query(default=None, max_length=8192),
                   state: str | None = Query(default=None, max_length=128),
                   error: str | None = Query(default=None, max_length=128), session=Depends(get_session)):
    origin = urlsplit(callback_url())
    fragment = {"sync_error": "connection_failed"}
    try:
        _feature()
        if not state or not code or error:
            raise HTTPException(403, "OAuth authorization failed")
        attempt = session.get(RemoteSyncOAuthAttempt, token_digest(state))
        browser = request.cookies.get(COOKIE, "")
        if (not attempt or attempt.used_at or attempt.expires_at <= datetime.utcnow() or not browser
                or not hmac.compare_digest(attempt.browser_hash, token_digest(browser))):
            raise HTTPException(403, "OAuth authorization expired")
        row = _callback_actor(session, attempt)
        claimed = session.execute(update(RemoteSyncOAuthAttempt).where(RemoteSyncOAuthAttempt.state_hash == attempt.state_hash,
            RemoteSyncOAuthAttempt.used_at.is_(None)).values(used_at=datetime.utcnow()))
        if claimed.rowcount != 1:
            raise HTTPException(403, "OAuth authorization already used")
        verifier = unseal_private(attempt.encrypted_verifier, "remote-sync-pkce", attempt.state_hash)
        secret = sync.credentials(row)
        request_connection = sync.frozen_connection(row)
        session.commit()
        token = providers.exchange_code(request_connection.provider, sync._provider_config(request_connection, secret), callback_url(), code, verifier)
        session.expire_all()
        row = _callback_actor(session, attempt)
        secret.update(token)
        sync.save_credentials(row, secret)
        row.grant_user_id = attempt.user_id
        row.status, row.enabled = "connected", False
        row.updated_at = datetime.utcnow()
        row.tested_at = row.tested_revision = None
        session.add(row)
        sync.activity(session, row, "connection.oauth_connected")
        session.commit()
        fragment = {"sync_connected": row.id, "workspace_id": row.workspace_id}
    except (HTTPException, providers.ProviderError, ValueError, TypeError, KeyError):
        session.rollback()
    response = RedirectResponse(urlunsplit((origin.scheme, origin.netloc, "/", "", urlencode(fragment))), status_code=303)
    _private(response)
    response.delete_cookie(COOKIE, path="/")
    return response


@oauth_router.get("/channels/{channel_id}/sync-status")
def channel_sync_status(channel_id: str, response: Response, user=Depends(get_current_user), session=Depends(get_session)):
    require_channel_member(session, channel_id, user.id)
    _private(response)
    rows = session.exec(select(RemoteSyncConnection).where(RemoteSyncConnection.channel_id == channel_id)).all()
    return {"connections": [{"id": row.id, "name": row.name, "provider": row.provider,
        "kind": row.kind, "enabled": row.enabled and sync.enabled(), "channel_id": row.channel_id} for row in rows]}
