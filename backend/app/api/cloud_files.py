"""Explicit read-only customer cloud selection and encrypted external references."""
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
from pydantic import Field, field_validator
from sqlalchemy import func, update
from sqlmodel import select

from ..database import get_session
from ..models.account_security import AccountSecurity
from ..models.cloud_files import CloudFileConnection, CloudFileLink, CloudFileOAuthAttempt
from ..models.remote_sync import RemoteSyncConnection
from ..models.session import ActiveSession
from ..services import cloud_files as service, cloud_file_providers as providers, remote_sync
from ..services.account_security import account_limit, seal_private, unseal_private
from ..services.auth import get_current_user, security, token_digest
from ..services.crypto import encrypt_message
from ..services.matrix_bridge import is_secure_channel, validate_secure_event
from . import remote_sync as sync_api

COOKIE = "fc_cloud_files_browser"


def private(response: Response):
    response.headers.update({"Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer"})


router = APIRouter(prefix="/workspaces/{workspace_id}/channels/{channel_id}/cloud-files", tags=["cloud files"], dependencies=[Depends(private)])
oauth_router = APIRouter(tags=["cloud files"], dependencies=[Depends(private)])


class ConnectionDraft(sync_api.Reauthenticate):
    name: str = Field(min_length=1, max_length=80)
    provider: Literal["dropbox", "box"]
    client_id: str = Field(min_length=1, max_length=1024)
    client_secret: str | None = Field(default=None, max_length=4096)
    folder_id: str = Field(max_length=1024)
    consent: bool = False

    @field_validator("name", "client_id", "client_secret", "folder_id")
    @classmethod
    def clean(cls, value):
        if value is not None and any(ord(c) < 32 or ord(c) == 127 for c in value): raise ValueError("Control characters are unavailable")
        return value.strip() if isinstance(value, str) else value


def callback_url():
    origin = urlsplit(sync_api.callback_url())
    return urlunsplit((origin.scheme, origin.netloc, "/api/cloud-files/oauth/callback", "", ""))


def _own(session, workspace_id, channel_id, identifier):
    row = session.get(CloudFileConnection, identifier, populate_existing=True)
    if not row or row.workspace_id != workspace_id or row.channel_id != channel_id:
        raise HTTPException(404, "Cloud connection not found")
    return row


@router.get("/connections")
def connections(workspace_id: str, channel_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    service.authorize(session, workspace_id, channel_id, user.id)
    secure = is_secure_channel(session, channel_id)
    manageable = False
    try:
        service.authorize(session, workspace_id, channel_id, user.id, admin=True)
        manageable = True
    except HTTPException as error:
        if error.status_code != 403: raise
    rows = []
    if manageable and not secure:
        if remote_sync.enabled():
            rows += session.exec(select(RemoteSyncConnection).where(RemoteSyncConnection.workspace_id == workspace_id,
                RemoteSyncConnection.channel_id == channel_id, RemoteSyncConnection.kind == "files",
                RemoteSyncConnection.provider.in_(["google", "microsoft"])).order_by(RemoteSyncConnection.created_at).limit(20)).all()
        rows += session.exec(select(CloudFileConnection).where(CloudFileConnection.workspace_id == workspace_id,
            CloudFileConnection.channel_id == channel_id).order_by(CloudFileConnection.created_at).limit(20)).all()
        # A revoked grant must not disclose remote metadata or appear usable.
        rows = [row for row in rows if _grant_current(session, row)]
    return {"secure": secure, "can_manage": manageable, "sync_enabled": remote_sync.enabled(),
        "callback_url": callback_url(), "sync_callback_url": sync_api.callback_url(), "connections": [service.public_connection(row) for row in rows]}


def _grant_current(session, row):
    try: remote_sync.require_native_grant(session, row); return True
    except HTTPException: return False


@router.post("/connections")
def create_connection(workspace_id: str, channel_id: str, data: ConnectionDraft, user=Depends(get_current_user), credential=Depends(security), session=Depends(get_session)):
    service.feature()
    if not data.consent: raise HTTPException(422, "Explicit permission to connect this customer application is required")
    actor, _ = sync_api._authorize(session, workspace_id, user, credential, data)
    service.authorize(session, workspace_id, channel_id, actor.id, lock=True, admin=True, native=True)
    if session.exec(select(func.count()).select_from(CloudFileConnection).where(CloudFileConnection.workspace_id == workspace_id)).one() >= 20:
        raise HTTPException(409, "This workspace reached its 20 cloud-picker connection limit")
    try: config = providers.validate_config(data.provider, {"client_id": data.client_id, "folder_id": data.folder_id})
    except providers.ProviderError: raise HTTPException(422, "Check the application ID and selected folder") from None
    if data.provider == "box" and not data.client_secret: raise HTTPException(422, "Box requires the customer application's client secret")
    row = CloudFileConnection(workspace_id=workspace_id, channel_id=channel_id, created_by=actor.id, grant_user_id=actor.id,
        name=data.name, provider=data.provider, config_json=json.dumps(config))
    service.save_credentials(row, {"client_secret": data.client_secret} if data.client_secret else {})
    session.add(row); session.commit()
    return service.public_connection(row)


@router.post("/connections/{connection_id}/oauth/start")
def oauth_start(workspace_id: str, channel_id: str, connection_id: str, data: sync_api.Reauthenticate, response: Response,
                user=Depends(get_current_user), credential=Depends(security), session=Depends(get_session)):
    service.feature()
    actor, auth_version = sync_api._authorize(session, workspace_id, user, credential, data)
    service.authorize(session, workspace_id, channel_id, actor.id, lock=True, admin=True, native=True)
    row = _own(session, workspace_id, channel_id, connection_id)
    row.revision += 1; row.status = "oauth_pending"; row.updated_at = datetime.utcnow()
    old = service.credentials(row)
    secret = {"client_secret": old["client_secret"]} if old.get("client_secret") else {}
    service.save_credentials(row, secret)
    state, verifier, browser = secrets.token_urlsafe(32), secrets.token_urlsafe(48), secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    try: url = providers.authorization_url(row.provider, json.loads(row.config_json), callback_url(), state, challenge)
    except providers.ProviderError: raise HTTPException(422, "Check the customer OAuth application configuration") from None
    session.add(row)
    session.add(CloudFileOAuthAttempt(state_hash=token_digest(state), connection_id=row.id, revision=row.revision,
        user_id=actor.id, native_token_hash=token_digest(credential.credentials), auth_version=auth_version, browser_hash=token_digest(browser),
        encrypted_verifier=seal_private(verifier, "cloud-files-pkce", token_digest(state)), expires_at=datetime.utcnow() + timedelta(minutes=10)))
    session.commit()
    response.set_cookie(COOKIE, browser, max_age=600, httponly=True, secure=callback_url().startswith("https://"), samesite="lax", path="/")
    return {"authorization_url": url}


def _callback_actor(session, attempt):
    session.execute(update(AccountSecurity).where(AccountSecurity.user_id == attempt.user_id).values(auth_version=AccountSecurity.auth_version))
    session.execute(update(ActiveSession).where(ActiveSession.token_hash == attempt.native_token_hash).values(revoked_at=ActiveSession.revoked_at))
    actor = sync_api._callback_native_actor(session, attempt)
    row = session.get(CloudFileConnection, attempt.connection_id, populate_existing=True)
    if not row or row.revision != attempt.revision or row.status != "oauth_pending": raise HTTPException(403, "Connection authorization changed")
    service.authorize(session, row.workspace_id, row.channel_id, actor.id, lock=True, admin=True, native=True)
    sync_api._callback_native_actor(session, attempt)
    row = session.get(CloudFileConnection, attempt.connection_id, populate_existing=True)
    if not row or row.revision != attempt.revision or row.status != "oauth_pending": raise HTTPException(403, "Connection authorization changed")
    return row


@oauth_router.get("/cloud-files/oauth/callback")
def oauth_callback(request: Request, code: str | None = Query(default=None, max_length=8192), state: str | None = Query(default=None, max_length=128),
                   error: str | None = Query(default=None, max_length=128), session=Depends(get_session)):
    service.feature()
    origin = urlsplit(callback_url())
    fragment = {"cloud_files_error": "connection_failed"}
    try:
        if not state or not code or error: raise HTTPException(403, "OAuth authorization failed")
        attempt = session.get(CloudFileOAuthAttempt, token_digest(state))
        browser = request.cookies.get(COOKIE, "")
        if (not attempt or attempt.used_at or attempt.expires_at <= datetime.utcnow() or not browser
                or not hmac.compare_digest(attempt.browser_hash, token_digest(browser))): raise HTTPException(403, "OAuth authorization expired")
        row = _callback_actor(session, attempt)
        claimed = session.execute(update(CloudFileOAuthAttempt).where(CloudFileOAuthAttempt.state_hash == attempt.state_hash,
            CloudFileOAuthAttempt.used_at.is_(None)).values(used_at=datetime.utcnow()))
        if claimed.rowcount != 1: raise HTTPException(403, "OAuth authorization already used")
        verifier = unseal_private(attempt.encrypted_verifier, "cloud-files-pkce", attempt.state_hash)
        provider, config, secret = row.provider, json.loads(row.config_json), service.credentials(row)
        config.update({"client_secret": secret["client_secret"]} if secret.get("client_secret") else {})
        session.commit()
        token = providers.exchange_code(provider, config, code, verifier, callback_url())
        row = _callback_actor(session, attempt)
        secret.update(token); service.save_credentials(row, secret)
        row.grant_user_id, row.status, row.updated_at = attempt.user_id, "connected", datetime.utcnow()
        session.add(row); session.commit()
        fragment = {"cloud_files_connected": row.id, "workspace_id": row.workspace_id, "channel_id": row.channel_id}
    except (HTTPException, providers.ProviderError, ValueError, TypeError, KeyError): session.rollback()
    response = RedirectResponse(urlunsplit((origin.scheme, origin.netloc, "/", "", urlencode(fragment))), status_code=303)
    private(response); response.delete_cookie(COOKIE, path="/")
    return response


@router.post("/connections/{connection_id}/disconnect")
def disconnect(workspace_id: str, channel_id: str, connection_id: str, data: sync_api.Reauthenticate,
               user=Depends(get_current_user), credential=Depends(security), session=Depends(get_session)):
    service.feature()
    actor, _ = sync_api._authorize(session, workspace_id, user, credential, data)
    service.authorize(session, workspace_id, channel_id, actor.id, lock=True, admin=True, native=True)
    row = _own(session, workspace_id, channel_id, connection_id)
    row.revision += 1; row.status = "disconnected"; row.updated_at = datetime.utcnow()
    service.save_credentials(row, {"client_secret": service.credentials(row)["client_secret"]} if service.credentials(row).get("client_secret") else {})
    session.add(row); session.commit()
    return service.public_connection(row)


@router.post("/connections/{connection_id}/browse")
def browse(workspace_id: str, channel_id: str, connection_id: str, data: service.BrowseInput, user=Depends(get_current_user), session=Depends(get_session)):
    service.feature()
    account_limit(session, "cloud-file-browse", user.id, 60, 300)
    return service.browse(session, workspace_id, channel_id, connection_id, user.id, data)


@router.post("/connections/{connection_id}/import")
def import_file(workspace_id: str, channel_id: str, connection_id: str, data: service.ImportInput, user=Depends(get_current_user), session=Depends(get_session)):
    service.feature()
    account_limit(session, "cloud-file-import", user.id, 20, 300)
    return service.import_file(session, workspace_id, channel_id, connection_id, user.id, data)


@router.get("/links")
def links(workspace_id: str, channel_id: str, before: str | None = Query(default=None, max_length=80), user=Depends(get_current_user), session=Depends(get_session)):
    service.authorize(session, workspace_id, channel_id, user.id)
    query = select(CloudFileLink).where(CloudFileLink.channel_id == channel_id, CloudFileLink.workspace_id == workspace_id)
    if before: query = query.where(CloudFileLink.id < before)
    rows = session.exec(query.order_by(CloudFileLink.id.desc()).limit(101)).all()
    secure = is_secure_channel(session, channel_id)
    if secure and any(row.content_mode != "matrix" for row in rows): raise HTTPException(409, "Native cloud-link history cannot be opened in an encrypted channel")
    return {"items": [service.serialize_link(row) for row in rows[:100]], "next_before": rows[99].id if len(rows) > 100 else None}


@router.post("/links")
def create_link(workspace_id: str, channel_id: str, data: service.LinkInput, user=Depends(get_current_user), session=Depends(get_session)):
    service.authorize(session, workspace_id, channel_id, user.id, lock=True)
    secure = is_secure_channel(session, channel_id)
    if secure:
        if data.fields is not None: raise HTTPException(409, "Encrypted channels require an encrypted provider-link event")
        validate_secure_event(session, channel_id, data.matrix_event_id, user.id)
        service.authorize(session, workspace_id, channel_id, user.id, lock=True)
    elif data.matrix_event_id: raise HTTPException(400, "Encrypted link events require an encrypted channel")
    existing = session.get(CloudFileLink, data.id)
    if existing:
        if existing.channel_id != channel_id or existing.created_by != user.id: raise HTTPException(409, "This link identifier was already used")
        record = service.serialize_link(existing)
        if (secure and existing.matrix_event_id != data.matrix_event_id) or (not secure and record.get("fields") != data.fields.model_dump()):
            raise HTTPException(409, "This link identifier was used for another selection")
        return record
    count = session.exec(select(func.count()).select_from(CloudFileLink).where(CloudFileLink.channel_id == channel_id)).one()
    if count >= 1000: raise HTTPException(409, "This channel reached its 1,000 provider-link limit")
    row = CloudFileLink(id=data.id, workspace_id=workspace_id, channel_id=channel_id, created_by=user.id,
        content_mode="matrix" if secure else "server", matrix_event_id=data.matrix_event_id,
        encrypted_payload=None if secure else encrypt_message(json.dumps({"id": data.id, "channel_id": channel_id,
            "fields": data.fields.model_dump()}, ensure_ascii=False), channel_id))
    session.add(row); session.commit()
    return service.serialize_link(row)


@router.delete("/links/{link_id}")
def remove_link(workspace_id: str, channel_id: str, link_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    member = service.authorize(session, workspace_id, channel_id, user.id, lock=True)
    row = session.get(CloudFileLink, link_id)
    if not row or row.channel_id != channel_id or row.workspace_id != workspace_id: raise HTTPException(404, "Provider link not found")
    if row.created_by != user.id and member.role != "admin": raise HTTPException(403, "Only the creator or channel administrator can remove this link")
    session.delete(row); session.commit()
    return {"removed": True}
