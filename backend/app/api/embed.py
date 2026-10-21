"""Customer-owned embedding: exact origins, real SSO, one-use PKCE grants."""
from datetime import datetime, timedelta, timezone
import hmac
from html import escape
import secrets
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse
import jwt
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, update
from sqlmodel import select

from ..config import settings
from ..database import get_session
from ..models.account_security import AccountSecurity
from ..models.channel import ChannelMember
from ..models.embed import EmbedApplication, EmbedGrant, EmbedSession
from ..models.user import User
from ..models.session import ActiveSession
from ..models.secure_channel import MatrixSession
from ..models.workspace import WorkspaceChannel
from ..models.workspace_oidc import WorkspaceOIDCSession
from ..services import embed
from ..services.account_security import account_limit, ensure_reauthentication
from ..services.auth import active_user_for_token, decode_token_claims, get_current_user, security, token_digest, revoke_token, validate_password
from ..services.rate_limit import request_address
from ..services.sso_scope import current_workspace_scope, require_unrestricted
from ..services.workspaces import lock_workspace, require_workspace_admin, require_workspace_member

router = APIRouter(tags=["embedded applications"])


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Registration(StrictInput):
    name: str = Field(min_length=1, max_length=80)
    parent_origin: str = Field(min_length=1, max_length=2048)
    enabled: bool = True
    password: str = Field(min_length=1, max_length=72)
    code: str | None = Field(default=None, max_length=64)
    _password = field_validator("password")(validate_password)
    _origin = field_validator("parent_origin")(embed.exact_origin)

    @field_validator("name")
    @classmethod
    def nonempty(cls, value):
        if not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("A nonempty name without control characters is required")
        return value.strip()


class Launch(StrictInput):
    application_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    channel_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    parent_origin: str = Field(min_length=1, max_length=2048)
    code_challenge: str = Field(min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]+$")
    code_challenge_method: str = Field(default="S256", pattern=r"^S256$")
    _origin = field_validator("parent_origin")(embed.exact_origin)


class Exchange(StrictInput):
    code: str = Field(min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    code_verifier: str = Field(min_length=43, max_length=128, pattern=r"^[A-Za-z0-9._~-]+$")
    parent_origin: str = Field(min_length=1, max_length=2048)
    channel_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    _origin = field_validator("parent_origin")(embed.exact_origin)


def _private(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _registration(row):
    return {"application_id": row.id, "workspace_id": row.workspace_id, "name": row.name,
            "parent_origin": row.parent_origin, "enabled": row.enabled, "revision": row.revision,
            "created_at": row.created_at, "updated_at": row.updated_at}


def _native_admin(session, user, credentials, workspace_id, data):
    require_unrestricted(session)
    account_limit(session, "embed-config", user.id, 20, 300)
    session.execute(update(AccountSecurity).where(AccountSecurity.user_id == user.id).values(auth_version=AccountSecurity.auth_version))
    session.expire_all()
    if active_user_for_token(credentials.credentials, session) is None:
        raise HTTPException(401, "Account security changed. Please sign in again.")
    require_unrestricted(session)
    lock_workspace(session, workspace_id)
    require_workspace_admin(session, workspace_id, user.id)
    ensure_reauthentication(session, user, data.password, data.code)


def _issue_child(session, *, request, user, application, channel_id, provider_revision, parent_hash,
                 source_issued_at, source_expires_at, source_auth_version, source_otp, now):
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    expiry = min(source_expires_at, now_epoch + embed.CHILD_SESSION_SECONDS)
    if expiry <= now_epoch:
        raise HTTPException(401, "The source workspace sign-in has expired")
    payload = {"sub": user.id, "iat": source_issued_at, "exp": source_expires_at,
               "jti": uuid4().hex, "av": source_auth_version, "amr": ["sso"] + (["otp"] if source_otp else []),
               "sso_workspace_id": application.workspace_id, "sso_provider_revision": provider_revision,
               "embed_application_id": application.id}
    token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
    session.add(ActiveSession(user_id=user.id, token_hash=token_digest(token), expires_at=datetime.utcfromtimestamp(expiry),
        user_agent=request.headers.get("user-agent", "Unknown browser")[:256],
        client_address=(request.client.host if request.client else "")[:64]))
    session.flush()
    session.add(WorkspaceOIDCSession(token_hash=token_digest(token), workspace_id=application.workspace_id,
        provider_revision=provider_revision))
    session.add(EmbedSession(token_hash=token_digest(token), application_id=application.id,
        application_revision=application.revision, workspace_id=application.workspace_id, channel_id=channel_id,
        parent_token_hash=parent_hash, source_expires_at=source_expires_at))
    return token, expiry


def _session_result(user, application, channel_id, token, expiry, now):
    return {"access_token": token, "token_type": "bearer", "user_id": user.id,
            "public_key": user.public_key or "", "encrypted_private_key": user.encrypted_private_key,
            "workspace_id": application.workspace_id, "application_id": application.id, "channel_id": channel_id,
            "expires_in": max(0, expiry - int(now.replace(tzinfo=timezone.utc).timestamp())), "expires_at": expiry}


@router.get("/workspaces/{workspace_id}/embed/applications")
def registrations(workspace_id: str, response: Response, user=Depends(get_current_user), session=Depends(get_session)):
    _private(response)
    require_unrestricted(session)
    require_workspace_admin(session, workspace_id, user.id)
    return {"applications": [_registration(row) for row in session.exec(select(EmbedApplication).where(
        EmbedApplication.workspace_id == workspace_id)).all()]}


@router.post("/workspaces/{workspace_id}/embed/applications", status_code=201)
def register(workspace_id: str, data: Registration, response: Response, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    _private(response)
    _native_admin(session, user, credentials, workspace_id, data)
    if data.parent_origin == embed.public_origin():
        raise HTTPException(422, "Use a separate origin for the product and its trusted FreeCollab client")
    count = len(session.exec(select(EmbedApplication.id).where(EmbedApplication.workspace_id == workspace_id)).all())
    if count >= 50:
        raise HTTPException(409, "A workspace supports up to 50 embedded applications")
    row = EmbedApplication(workspace_id=workspace_id, name=data.name, parent_origin=data.parent_origin,
                           enabled=data.enabled, created_by=user.id)
    session.add(row)
    session.commit()
    return _registration(row)


@router.put("/workspaces/{workspace_id}/embed/applications/{application_id}")
def edit_registration(workspace_id: str, application_id: str, data: Registration, response: Response, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    _private(response)
    _native_admin(session, user, credentials, workspace_id, data)
    if data.parent_origin == embed.public_origin():
        raise HTTPException(422, "Use a separate origin for the product and its trusted FreeCollab client")
    row = session.get(EmbedApplication, application_id, populate_existing=True)
    if not row or row.workspace_id != workspace_id:
        raise HTTPException(404, "Embedded application not found")
    row.name, row.parent_origin, row.enabled = data.name, data.parent_origin, data.enabled
    row.revision += 1
    row.updated_at = datetime.utcnow()
    session.add(row)
    session.commit()
    return _registration(row)


@router.get("/embed/applications/{application_id}")
@router.get("/embed/{application_id}/bootstrap")
def bootstrap(application_id: str, response: Response, parent_origin: str = Query(min_length=1, max_length=2048), session=Depends(get_session)):
    _private(response)
    row, provider = embed.current_application(session, application_id, parent_origin=parent_origin)
    return {"application_id": row.id, "workspace_id": row.workspace_id, "name": row.name,
            "parent_origin": row.parent_origin, "enabled": True, "revision": row.revision,
            "login_id": provider.login_id, "frame_url": embed.frame_url(row, row.parent_origin), "modules": embed.MODULES}


@router.get("/embed/{application_id}/document", response_class=HTMLResponse)
def document(application_id: str, parent_origin: str = Query(min_length=1, max_length=2048), session=Depends(get_session)):
    row, _ = embed.current_application(session, application_id, parent_origin=parent_origin)
    own_origin = embed.public_origin()
    # A trusted, fixed entry point keeps crypto keys/content inside this origin.
    # No host content, credentials or executable configuration enter the document.
    html = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>FreeCollab</title><link rel="stylesheet" href="/embed-entry.css"></head><body><div id="root" data-embed-application="' + escape(row.id, quote=True) + '" data-embed-parent-origin="' + escape(row.parent_origin, quote=True) + '"></div><script type="module" src="/embed-entry.js"></script></body></html>'
    response = _private(HTMLResponse(html))
    websocket_origin = own_origin.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    response.headers["Content-Security-Policy"] = ("default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; media-src 'self' blob:; font-src 'self'; connect-src 'self' " + websocket_origin + "; worker-src 'self' blob:; frame-src 'self' https:; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors " + row.parent_origin + " " + own_origin)
    response.headers["Permissions-Policy"] = "camera=(self), microphone=(self), geolocation=(), payment=()"
    return response


@router.post("/workspaces/{workspace_id}/embed/launch")
def launch(workspace_id: str, data: Launch, request: Request, response: Response, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    _private(response)
    account_limit(session, "embed-launch", user.id, 40, 300)
    lock_workspace(session, workspace_id)
    # Recheck under the same workspace write lock as configuration changes.
    session.expire_all()
    if active_user_for_token(credentials.credentials, session) is None or current_workspace_scope(session) != workspace_id:
        raise HTTPException(403, "Embedded applications require this workspace's current customer SSO session")
    row, provider = embed.current_application(session, data.application_id, parent_origin=data.parent_origin)
    if row.workspace_id != workspace_id:
        raise HTTPException(403, "The embedded application belongs to another workspace")
    origin = request.headers.get("origin")
    if origin is not None and origin not in {row.parent_origin, embed.public_origin()}:
        raise HTTPException(403, "This origin cannot launch the embedded application")
    claims = decode_token_claims(credentials.credentials)
    source_hash = token_digest(credentials.credentials)
    if (not claims or type(claims.get("iat")) is not int or type(claims.get("exp")) is not int
            or "sso" not in claims.get("amr", []) or not embed.parent_session_valid(session, source_hash, user.id)):
        raise HTTPException(403, "A current, non-embedded customer SSO session is required")
    mapping = session.get(WorkspaceChannel, data.channel_id)
    if not mapping or mapping.workspace_id != workspace_id or not session.get(ChannelMember, (data.channel_id, user.id)):
        raise HTTPException(403, "Channel membership in the embedded workspace is required")
    require_workspace_member(session, workspace_id, user.id)
    now = datetime.utcnow()
    # Bound private handoff metadata retention without a separate worker. Each
    # authenticated launch removes at most 200 expired records from this app.
    expired = session.exec(select(EmbedGrant.code_hash).where(EmbedGrant.application_id == row.id,
        EmbedGrant.expires_at <= now).order_by(EmbedGrant.expires_at).limit(200)).all()
    if expired:
        session.execute(delete(EmbedGrant).where(EmbedGrant.code_hash.in_(expired)))
    code = secrets.token_urlsafe(32)
    expiry = min(now + timedelta(seconds=60), datetime.utcfromtimestamp(claims["exp"]))
    session.add(EmbedGrant(code_hash=token_digest(code), application_id=row.id, application_revision=row.revision,
        workspace_id=workspace_id, provider_revision=provider.revision, channel_id=data.channel_id,
        parent_origin=row.parent_origin, code_challenge=data.code_challenge, user_id=user.id,
        parent_token_hash=source_hash, source_issued_at=int(claims["iat"]), source_expires_at=int(claims["exp"]),
        source_auth_version=claims.get("av", 0), source_otp="otp" in claims.get("amr", []), expires_at=expiry))
    session.commit()
    return {"code": code, "expires_in": max(0, int((expiry - now).total_seconds())), "application_id": row.id,
            "workspace_id": workspace_id, "channel_id": data.channel_id, "frame_url": embed.frame_url(row, row.parent_origin)}


@router.post("/embed/{application_id}/exchange")
def exchange(application_id: str, data: Exchange, request: Request, response: Response, session=Depends(get_session)):
    _private(response)
    account_limit(session, "embed-exchange", request_address(request), 80, 300)
    # Called only by the trusted document, never directly from the parent page.
    if request.headers.get("origin") not in {None, embed.public_origin()}:
        raise HTTPException(403, "Only the trusted FreeCollab client can consume an embedding handoff")
    grant = session.get(EmbedGrant, token_digest(data.code))
    now = datetime.utcnow()
    invalid = HTTPException(400, "The embedding handoff is invalid or has expired")
    if (not grant or grant.application_id != application_id or grant.consumed_at is not None or grant.expires_at <= now
            or grant.channel_id != data.channel_id or grant.parent_origin != data.parent_origin
            or not hmac.compare_digest(grant.code_challenge, embed.proof_challenge(data.code_verifier))):
        raise invalid
    lock_workspace(session, grant.workspace_id)
    row, provider = embed.current_application(session, application_id, parent_origin=data.parent_origin, revision=grant.application_revision)
    state = session.get(AccountSecurity, grant.user_id, populate_existing=True)
    user = session.get(User, grant.user_id, populate_existing=True)
    source = session.get(WorkspaceOIDCSession, grant.parent_token_hash, populate_existing=True)
    mapping = session.get(WorkspaceChannel, data.channel_id, populate_existing=True)
    if (not user or user.is_blocked or not state or state.auth_version != grant.source_auth_version
            or not source or source.workspace_id != grant.workspace_id or source.provider_revision != grant.provider_revision
            or provider.revision != grant.provider_revision or not embed.parent_session_valid(session, grant.parent_token_hash, grant.user_id, now=now)
            or not mapping or mapping.workspace_id != grant.workspace_id or not session.get(ChannelMember, (data.channel_id, grant.user_id))):
        raise invalid
    require_workspace_member(session, grant.workspace_id, grant.user_id)
    changed = session.execute(update(EmbedGrant).where(EmbedGrant.code_hash == grant.code_hash,
        EmbedGrant.consumed_at.is_(None), EmbedGrant.expires_at > now).values(consumed_at=now))
    if changed.rowcount != 1:
        session.rollback()
        raise invalid
    # Preserve source authentication age and bound lifetime. A handoff cannot renew SSO
    # freshness, extend a session, bypass MFA or create account-wide authority.
    # All rows and the single-use transition commit together. No source bearer
    # is persisted; only verified identity/freshness fields and token digests.
    token, expiry = _issue_child(session, request=request, user=user, application=row,
        channel_id=grant.channel_id, provider_revision=grant.provider_revision, parent_hash=grant.parent_token_hash,
        source_issued_at=grant.source_issued_at, source_expires_at=grant.source_expires_at,
        source_auth_version=grant.source_auth_version, source_otp=grant.source_otp, now=now)
    session.commit()
    from ..services.security_audit import authenticated_actor
    authenticated_actor(request, grant.user_id, workspace_id=grant.workspace_id)
    return _session_result(user, row, grant.channel_id, token, expiry, now)


@router.post("/embed/{application_id}/renew")
def renew(application_id: str, request: Request, response: Response, user=Depends(get_current_user),
          credentials=Depends(security), session=Depends(get_session)):
    _private(response)
    if request.headers.get("origin") not in {None, embed.public_origin()}:
        raise HTTPException(403, "Only the trusted FreeCollab client can renew this session")
    account_limit(session, "embed-renew", user.id, 120, 300)
    old_hash = token_digest(credentials.credentials)
    child = session.get(EmbedSession, old_hash, populate_existing=True)
    if not child or child.application_id != application_id:
        raise HTTPException(403, "A current embedded child session is required")
    lock_workspace(session, child.workspace_id)
    session.expire_all()
    if active_user_for_token(credentials.credentials, session) is None:
        raise HTTPException(401, "The embedded session has ended")
    child = session.get(EmbedSession, old_hash, populate_existing=True)
    application, provider = embed.current_application(session, application_id, revision=child.application_revision)
    claims = decode_token_claims(credentials.credentials)
    source = session.exec(select(ActiveSession).where(ActiveSession.token_hash == child.parent_token_hash)).first()
    scope = session.get(WorkspaceOIDCSession, old_hash, populate_existing=True)
    mapping = session.get(WorkspaceChannel, child.channel_id, populate_existing=True)
    if (not claims or not source or not scope or scope.workspace_id != child.workspace_id
            or scope.provider_revision != provider.revision or not embed.parent_session_valid(session, child.parent_token_hash, user.id)
            or not mapping or mapping.workspace_id != child.workspace_id or not session.get(ChannelMember, (child.channel_id, user.id))):
        raise HTTPException(401, "The embedded session's source authority changed")
    require_workspace_member(session, child.workspace_id, user.id)
    now = datetime.utcnow()
    source_expiry = min(child.source_expires_at, int(source.expires_at.replace(tzinfo=timezone.utc).timestamp()))
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    expiry = min(source_expiry, now_epoch + embed.CHILD_SESSION_SECONDS)
    changed = session.execute(update(ActiveSession).where(ActiveSession.token_hash == old_hash,
        ActiveSession.revoked_at.is_(None), ActiveSession.expires_at > now).values(expires_at=datetime.utcfromtimestamp(expiry)))
    if changed.rowcount != 1:
        session.rollback()
        raise HTTPException(401, "The embedded session lease expired or was revoked")
    # Extend only the authoritative server lease. Keep the same bearer/device,
    # authentication age and personal unlock; never replay pending user writes.
    session.execute(update(MatrixSession).where(MatrixSession.auth_token_hash == old_hash).values(
        auth_expires_at=datetime.utcfromtimestamp(expiry)))
    session.commit()
    return _session_result(user, application, child.channel_id, credentials.credentials, expiry, now)


@router.post("/embed/{application_id}/logout")
def logout(application_id: str, response: Response, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    _private(response)
    child = session.get(EmbedSession, token_digest(credentials.credentials))
    if not child or child.application_id != application_id:
        raise HTTPException(403, "Only this embedded session can be signed out here")
    revoke_token(credentials.credentials, session)
    return {"signed_out": True}
