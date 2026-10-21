"""Customer registrations and explicit personal provider consent."""
from datetime import datetime, timedelta
import hmac
import json
import secrets
from typing import Literal
from urllib.parse import urlencode, urlsplit, urlunsplit
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import func, update
from sqlmodel import select

from ..database import get_session
from ..models.enterprise_search import EnterpriseSearchConnection
from ..models.personal_oauth import PersonalOAuthAttempt, PersonalOAuthClient, PersonalOAuthGrant
from ..services import personal_oauth as service, search_providers
from ..services.account_security import account_limit, ensure_reauthentication, seal_private, unseal_private
from ..services.auth import decode_token_claims, get_current_user, security, token_digest
from ..services.request_guard import recheck_actor
from ..services.sso_scope import current_workspace_scope, require_unrestricted, require_workspace_scope
from ..services.workspaces import lock_workspace, require_workspace_admin, require_workspace_member
from .enterprise_search import PrivateInputRoute
from .remote_sync import Reauthenticate

COOKIE = "fc_personal_oauth_browser"


def private(response: Response):
    response.headers.update({"Cache-Control": "private, no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer"})


router = APIRouter(tags=["personal provider OAuth"], dependencies=[Depends(private)], route_class=PrivateInputRoute)


class RegistrationInput(Reauthenticate):
    client_id: str = Field(min_length=1, max_length=1024)
    tenant_id: str = Field(default="", max_length=100)
    client_secret: SecretStr = Field(min_length=1, max_length=4096)
    expected_revision: int = Field(default=0, ge=0)
    enabled: bool = True


class StartInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: Literal["search", "calendar-action"]
    name: str = Field(min_length=1, max_length=80)
    config: dict[str, str] = Field(default_factory=dict)
    installation_id: str | None = Field(default=None, min_length=1, max_length=128)
    installation_generation: int | None = Field(default=None, ge=1)
    consent: bool


def member(session, workspace_id, actor_id):
    recheck_actor(session, actor_id)
    require_workspace_member(session, workspace_id, actor_id)


def can_manage(session, workspace_id, user_id):
    try:
        require_unrestricted(session)
        require_workspace_admin(session, workspace_id, user_id)
        return True
    except HTTPException:
        return False


@router.get("/workspaces/{workspace_id}/personal-oauth")
def overview(workspace_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    member(session, workspace_id, user.id)
    clients = session.exec(select(PersonalOAuthClient).where(PersonalOAuthClient.workspace_id == workspace_id)).all()
    grants = session.exec(select(PersonalOAuthGrant).where(PersonalOAuthGrant.workspace_id == workspace_id,
        PersonalOAuthGrant.user_id == user.id).order_by(PersonalOAuthGrant.created_at.desc()).limit(100)).all()
    return {"callback_url": service.callback_url(), "can_manage": can_manage(session, workspace_id, user.id),
        "purposes": ["search"] if current_workspace_scope(session) is not None else ["search", "calendar-action"],
        "clients": [service.public_client(row) for row in clients], "grants": [service.public_grant(session, row) for row in grants],
        "source_policy": "original-current-sign-in", "registrations_owned_by": "customer"}


@router.put("/workspaces/{workspace_id}/personal-oauth/clients/{provider}/{purpose}")
def configure(workspace_id: str, provider: Literal["google", "microsoft"], purpose: Literal["search", "calendar-action"],
              data: RegistrationInput, user=Depends(get_current_user), session=Depends(get_session)):
    account_limit(session, "personal-oauth-config", user.id, 20, 300)
    service.lock_owner(session, user.id)
    lock_workspace(session, workspace_id)
    member(session, workspace_id, user.id)
    require_unrestricted(session)
    require_workspace_admin(session, workspace_id, user.id)
    ensure_reauthentication(session, user, data.password, data.code)
    other = session.exec(select(PersonalOAuthClient).where(PersonalOAuthClient.workspace_id == workspace_id,
        PersonalOAuthClient.provider == provider, PersonalOAuthClient.purpose != purpose,
        PersonalOAuthClient.client_id == data.client_id.strip())).first()
    if other:
        raise HTTPException(422, "Use a separate customer OAuth application for read-only search and calendar write actions")
    row = session.exec(select(PersonalOAuthClient).where(PersonalOAuthClient.workspace_id == workspace_id,
        PersonalOAuthClient.provider == provider, PersonalOAuthClient.purpose == purpose)).first()
    if row:
        if row.revision != data.expected_revision:
            raise HTTPException(409, "The customer application changed; reload its configuration")
        row.revision += 1
        for grant in session.exec(select(PersonalOAuthGrant).where(PersonalOAuthGrant.client_id == row.id)).all():
            if grant.status not in {"revoked", "registration_changed"}:
                service.invalidate(session, grant, "registration_changed")
    else:
        if data.expected_revision:
            raise HTTPException(409, "A new customer registration starts at revision zero")
        row = PersonalOAuthClient(workspace_id=workspace_id, provider=provider, purpose=purpose,
            client_id=data.client_id, configured_by=user.id)
    row.client_id, row.tenant_id = service.clean(data.client_id), service.clean(data.tenant_id, 100, empty=True)
    row.enabled, row.configured_by, row.updated_at = data.enabled, user.id, datetime.utcnow()
    service.configuration(row, secret=False)
    row.encrypted_secret = seal_private(service.clean(data.client_secret.get_secret_value(), 4096), "personal-oauth-client", row.id)
    session.add(row)
    session.commit()
    return service.public_client(row)


@router.post("/workspaces/{workspace_id}/personal-oauth/{provider}/start")
def start(workspace_id: str, provider: Literal["google", "microsoft"], data: StartInput, response: Response,
          user=Depends(get_current_user), credential=Depends(security), session=Depends(get_session)):
    member(session, workspace_id, user.id)
    account_limit(session, "personal-oauth-start", user.id, 15, 300)
    service.lock_owner(session, user.id)
    lock_workspace(session, workspace_id)
    session.expire_all()
    member(session, workspace_id, user.id)
    if not data.consent:
        raise HTTPException(422, "Review and explicitly accept the selected provider scope")
    claims = decode_token_claims(credential.credentials)
    if not claims or claims.get("sub") != user.id:
        raise HTTPException(401, "Sign in again before connecting your provider account")
    if claims.get("native_client_id") or claims.get("embed_application_id"):
        raise HTTPException(409, "Open this installation directly in your system browser to connect a provider account")
    client = session.exec(select(PersonalOAuthClient).where(PersonalOAuthClient.workspace_id == workspace_id,
        PersonalOAuthClient.provider == provider, PersonalOAuthClient.purpose == data.purpose)).first()
    if not client or not client.enabled:
        raise HTTPException(409, "A workspace administrator must configure your organization's provider app first")
    service.configuration(client)
    count = session.exec(select(func.count()).select_from(PersonalOAuthGrant).where(PersonalOAuthGrant.user_id == user.id,
        PersonalOAuthGrant.status.in_(["active", "pending"]))).one()
    if count >= 40:
        raise HTTPException(409, "Disconnect unused personal delegations before adding another")
    if data.purpose == "search":
        if data.installation_id or data.installation_generation:
            raise HTTPException(422, "A read-only search grant cannot target an installed write app")
        try:
            config = search_providers.config(provider, data.config)
        except search_providers.ProviderError:
            raise HTTPException(422, "Select the provider folder and drive for your personal search") from None
        target_id, revision = uuid4().hex, 1
    else:
        if not data.installation_id or not data.installation_generation or data.config:
            raise HTTPException(422, "Select an installed calendar app and its current generation")
        config, target_id, revision = {}, data.installation_id, data.installation_generation
    row = PersonalOAuthGrant(workspace_id=workspace_id, user_id=user.id, client_id=client.id,
        client_revision=client.revision, provider=provider, purpose=data.purpose, name=service.clean(data.name, 80),
        target_id=target_id, target_revision=revision, config_json=json.dumps(config),
        source_hash=token_digest(credential.credentials), encrypted_source_claims="")
    row.encrypted_source_claims = seal_private(json.dumps(claims), "personal-oauth-source", row.id)
    session.add(row)
    session.flush()
    service.current_grant(session, row.id, user_id=user.id, status="pending")
    state, verifier, browser = secrets.token_urlsafe(32), secrets.token_urlsafe(48), secrets.token_urlsafe(32)
    url = service.authorization_url(client, state, verifier)
    session.add(PersonalOAuthAttempt(state_hash=token_digest(state), grant_id=row.id, browser_hash=token_digest(browser),
        encrypted_verifier=seal_private(verifier, "personal-oauth-pkce", token_digest(state)),
        expires_at=datetime.utcnow() + timedelta(minutes=10)))
    session.commit()
    response.set_cookie(COOKIE, browser, max_age=600, httponly=True, secure=service.callback_url().startswith("https://"),
        samesite="lax", path="/")
    return {"authorization_url": url, "grant_id": row.id, "scope": service.scope(provider, data.purpose)}


@router.post("/workspaces/{workspace_id}/personal-oauth/clients/{provider}/{purpose}/disable")
def disable(workspace_id: str, provider: Literal["google", "microsoft"], purpose: Literal["search", "calendar-action"],
            data: Reauthenticate, user=Depends(get_current_user), session=Depends(get_session)):
    account_limit(session, "personal-oauth-config", user.id, 20, 300)
    service.lock_owner(session, user.id)
    lock_workspace(session, workspace_id)
    member(session, workspace_id, user.id)
    require_unrestricted(session)
    require_workspace_admin(session, workspace_id, user.id)
    ensure_reauthentication(session, user, data.password, data.code)
    row = session.exec(select(PersonalOAuthClient).where(PersonalOAuthClient.workspace_id == workspace_id,
        PersonalOAuthClient.provider == provider, PersonalOAuthClient.purpose == purpose)).first()
    if not row:
        raise HTTPException(404, "Customer application not found")
    row.enabled, row.encrypted_secret, row.revision, row.updated_at = False, "", row.revision + 1, datetime.utcnow()
    session.add(row)
    for grant in session.exec(select(PersonalOAuthGrant).where(PersonalOAuthGrant.client_id == row.id)).all():
        service.invalidate(session, grant, "registration_changed")
    session.commit()
    return {"disabled": True, "local_credentials_erased": True}


@router.get("/personal-oauth/callback")
def callback(request: Request, code: str | None = Query(default=None, max_length=8192),
             state: str | None = Query(default=None, max_length=128), error: str | None = Query(default=None, max_length=128),
             session=Depends(get_session)):
    origin = urlsplit(service.callback_url())
    result = {"provider_error": "consent_failed"}
    try:
        if not code or not state or error:
            raise HTTPException(403, "Provider consent failed")
        attempt = session.get(PersonalOAuthAttempt, token_digest(state), populate_existing=True)
        browser = request.cookies.get(COOKIE, "")
        if (not attempt or attempt.used_at or attempt.expires_at <= datetime.utcnow() or not browser
                or not hmac.compare_digest(attempt.browser_hash, token_digest(browser))):
            raise HTTPException(403, "Provider consent expired")
        row, client = service.current_grant(session, attempt.grant_id, status="pending")
        identifier, workspace_id, actor_id = row.id, row.workspace_id, row.user_id
        verifier = unseal_private(attempt.encrypted_verifier, "personal-oauth-pkce", attempt.state_hash)
        config, provider, purpose = service.configuration(client), row.provider, row.purpose
        changed = session.execute(update(PersonalOAuthAttempt).where(PersonalOAuthAttempt.state_hash == attempt.state_hash,
            PersonalOAuthAttempt.used_at.is_(None)).values(used_at=datetime.utcnow(), encrypted_verifier=""))
        session.commit()
        if changed.rowcount != 1:
            raise HTTPException(403, "Provider consent already used")
        token = service.token_request(provider, purpose, config, {"grant_type": "authorization_code",
            "code": code, "code_verifier": verifier, "redirect_uri": service.callback_url()})
        # A logout, provider replacement, app generation change or workspace
        # removal during HTTPS cannot attach the returned provider credential.
        service.lock_owner(session, actor_id)
        lock_workspace(session, workspace_id)
        session.expire_all()
        attempt = session.get(PersonalOAuthAttempt, token_digest(state), populate_existing=True)
        if not attempt or attempt.expires_at <= datetime.utcnow():
            raise HTTPException(403, "Provider consent expired while completing the request")
        row, _ = service.current_grant(session, identifier, status="pending")
        service.store_tokens(row, token)
        row.status = "active"
        if row.purpose == "search":
            session.add(EnterpriseSearchConnection(id=row.target_id, workspace_id=row.workspace_id,
                user_id=row.user_id, name=row.name, provider=row.provider, config_json=row.config_json,
                encrypted_credentials="", revision=row.target_revision))
        else:
            for previous in session.exec(select(PersonalOAuthGrant).where(PersonalOAuthGrant.id != row.id,
                PersonalOAuthGrant.user_id == row.user_id, PersonalOAuthGrant.target_id == row.target_id,
                PersonalOAuthGrant.purpose == row.purpose, PersonalOAuthGrant.status == "active")).all():
                service.invalidate(session, previous, "replaced")
        session.add(row)
        session.commit()
        result = {"provider_connected": row.id, "workspace_id": row.workspace_id}
    except (HTTPException, service.transport.ProviderError, ValueError, TypeError, KeyError):
        session.rollback()
    response = RedirectResponse(urlunsplit((origin.scheme, origin.netloc, "/", "", urlencode(result))), status_code=303)
    private(response)
    response.delete_cookie(COOKIE, path="/")
    return response


@router.delete("/workspaces/{workspace_id}/personal-oauth/grants/{grant_id}")
def revoke(workspace_id: str, grant_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    service.lock_owner(session, user.id)
    lock_workspace(session, workspace_id)
    recheck_actor(session, user.id)
    require_workspace_scope(session, workspace_id)
    row = session.get(PersonalOAuthGrant, grant_id, populate_existing=True)
    if not row or row.workspace_id != workspace_id or row.user_id != user.id:
        raise HTTPException(404, "Personal provider delegation not found")
    provider, remote_token = row.provider, service.revocation_token(row)
    service.invalidate(session, row)
    session.commit()
    # Google exposes a per-token revoke endpoint. Microsoft has no equivalent;
    # do not revoke every sign-in session of the person's Microsoft account.
    remote = service.revoke_at_provider(provider, remote_token)
    return {"revoked": True, "local_credentials_erased": True, "provider_revocation": remote}
