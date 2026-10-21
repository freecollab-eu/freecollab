"""OIDC sign-in and explicit account linking; bearer tokens never enter URLs."""
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import update, delete
from sqlmodel import select

from ..config import settings
from ..database import get_session
from ..models.account_security import AccountSecurity
from ..models.oidc import OIDCIdentity, OIDCLoginAttempt, OIDCMFAChallenge
from ..models.user import User
from ..models.workspace_oidc import WorkspaceOIDCProvider, WorkspaceOIDCIdentity, WorkspaceOIDCMFAChallenge
from ..services import oidc, workspace_oidc as tenants, workforce
from ..services.sso_scope import require_unrestricted
from ..services.workspaces import require_workspace_member
from ..services.account_security import account_limit, create_mfa_challenge, digest, ensure_reauthentication, revoke_all_sessions
from ..services.auth import active_user_for_token, create_token, get_current_user, security, token_digest, validate_password
from ..services.rate_limit import request_address

router = APIRouter(prefix="/auth/oidc", tags=["organization sign-in"])


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LinkStart(StrictInput):
    workspace_id: str | None = Field(default=None, min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=72)
    code: str | None = Field(default=None, max_length=64)
    _password = field_validator("password")(validate_password)


class LoginStart(StrictInput):
    login_id: str | None = Field(default=None, min_length=20, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    invitation_token: str | None = Field(default=None, min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class Exchange(StrictInput):
    code: str = Field(min_length=20, max_length=256, pattern=r"^[A-Za-z0-9_-]+$")


def _private(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.get("/providers")
def providers(response: Response, login_id: str | None = Query(default=None, min_length=20, max_length=128),
              workspace_id: str | None = Query(default=None, min_length=1, max_length=128),
              credentials=Depends(HTTPBearer(auto_error=False)), session=Depends(get_session)):
    _private(response)
    if login_id and workspace_id:
        raise HTTPException(422, "Choose one workspace provider lookup")
    if workspace_id:
        user = active_user_for_token(credentials.credentials, session) if credentials else None
        if not user:
            raise HTTPException(401, "Sign in to view your workspace provider")
        require_workspace_member(session, workspace_id, user.id)
        provider = session.get(WorkspaceOIDCProvider, workspace_id)
        return {"providers": [tenants.safe_provider(provider, onboarding_mode=workforce.identity_policy(session, workspace_id).mode)] if provider and provider.enabled else []}
    if login_id:
        provider = tenants.provider_for_login(session, login_id)
        return {"providers": [tenants.safe_provider(provider, onboarding_mode=workforce.identity_policy(session, provider.workspace_id).mode)]}
    if not getattr(settings, "oidc_enabled", False):
        return {"providers": []}
    config = oidc.provider_config()
    return {"providers": [{"id": "default", "name": config["name"], "issuer": config["issuer"]}]}


def _start(request, response, session, *, user=None, version=None, session_hash=None, config=None, invitation_token=None):
    account_limit(session, "oidc-start", request_address(request), 30, 300)
    try:
        attempt, browser, url = oidc.begin_login(session, user=user, auth_version=version, session_hash=session_hash, config=config, invitation_token=invitation_token)
    except oidc.OIDCFailure:
        raise HTTPException(503, "Organization sign-in is temporarily unavailable") from None
    response.set_cookie(oidc.cookie_name(attempt.state_hash), browser, httponly=True, samesite="lax",
                        secure=attempt.redirect_uri.startswith("https://"), max_age=600, path="/")
    _private(response)
    return {"authorization_url": url, "expires_in": 600}


@router.post("/start")
def start(request: Request, response: Response, data: LoginStart | None = None, session=Depends(get_session)):
    if data and data.invitation_token and not data.login_id:
        raise HTTPException(422, "A workforce invitation requires a workspace sign-in link")
    config = tenants.config_for_provider(tenants.provider_for_login(session, data.login_id)) if data and data.login_id else None
    return _start(request, response, session, config=config, invitation_token=data.invitation_token if data else None)


@router.post("/link/start")
def start_link(data: LinkStart, request: Request, response: Response, user=Depends(get_current_user),
               credentials=Depends(security), session=Depends(get_session)):
    require_unrestricted(session)
    if data.workspace_id:
        require_workspace_member(session, data.workspace_id, user.id)
        config = tenants.config_for_provider(session.get(WorkspaceOIDCProvider, data.workspace_id))
    else:
        config = oidc.provider_config()
    account_limit(session, "oidc-link-account", user.id, 10, 300)
    session.expire_all()
    if active_user_for_token(credentials.credentials, session) is None:
        raise HTTPException(401, "Account security changed. Please sign in again.")
    state = ensure_reauthentication(session, user, data.password, data.code)
    if not state.email_verified_at:
        raise HTTPException(403, "Verify your FreeCollab email before linking SSO")
    return _start(request, response, session, user=user, version=state.auth_version,
                  session_hash=token_digest(credentials.credentials), config=config)


@router.get("/callback")
def callback(request: Request, state: str = Query(default="", max_length=256),
             code: str = Query(default="", max_length=4096), error: str | None = Query(default=None, max_length=256),
             session=Depends(get_session)):
    destination = settings.public_app_url.rstrip("/") + "/#sso_error=sign_in_failed"
    attempt = None
    try:
        attempt, config = oidc.consume_callback(session, state, request.cookies.get(oidc.cookie_name(digest(state))))
        if error:
            raise oidc.OIDCFailure("provider_denied")
        if not code:
            raise oidc.OIDCFailure()
        claims = oidc.verified_claims(attempt, config, code)
        handoff = oidc.resolve_identity(session, attempt, config, claims)
        destination = settings.public_app_url.rstrip("/") + "/#sso=" + quote(handoff, safe="")
    except (oidc.OIDCFailure, HTTPException) as exc:
        session.rollback()
        if attempt:
            session.exec(update(OIDCLoginAttempt).where(OIDCLoginAttempt.state_hash == attempt.state_hash,
                         OIDCLoginAttempt.phase == "processing").values(phase="failed", encrypted_verifier=""))
            session.commit()
        reason = exc.reason if isinstance(exc, oidc.OIDCFailure) else "sign_in_failed"
        destination = settings.public_app_url.rstrip("/") + "/#sso_error=" + reason
    return _private(RedirectResponse(destination, status_code=303))


@router.post("/exchange")
def exchange(data: Exchange, request: Request, response: Response, session=Depends(get_session)):
    _private(response)
    account_limit(session, "oidc-exchange", request_address(request), 40, 300)
    attempt = session.exec(select(OIDCLoginAttempt).where(OIDCLoginAttempt.handoff_hash == digest(data.code))).first()
    now = datetime.utcnow()
    if not attempt or attempt.phase != "ready" or attempt.expires_at <= now or not oidc.valid_browser(attempt, request.cookies.get(oidc.cookie_name(attempt.state_hash))):
        raise HTTPException(400, "Organization sign-in handoff is invalid or has expired")
    try:
        config = oidc.config_for_attempt(session, attempt, lock=True)
    except (HTTPException, oidc.OIDCFailure):
        raise HTTPException(400, "Workspace sign-in configuration or membership changed. Please sign in again.") from None
    scope = tenants.scope_for_config(config)
    extra = {"workspace_id": scope["workspace_id"]} if scope else {}
    if (attempt.issuer, attempt.client_id, attempt.redirect_uri) != (config["issuer"], config["client_id"], config["redirect_uri"]):
        raise HTTPException(400, "Organization sign-in configuration changed. Please sign in again.")
    user = session.get(User, attempt.user_id)
    state = session.get(AccountSecurity, attempt.user_id)
    if not user or user.is_blocked or not state or state.auth_version != attempt.auth_version:
        raise HTTPException(400, "Account security changed. Please sign in again.")
    if attempt.linking:
        try:
            oidc.valid_link_session(session, attempt)
        except oidc.OIDCFailure:
            raise HTTPException(400, "Account security changed. Please sign in again.") from None
    changed = session.exec(update(OIDCLoginAttempt).where(OIDCLoginAttempt.state_hash == attempt.state_hash,
                           OIDCLoginAttempt.phase == "ready", OIDCLoginAttempt.expires_at > now).values(phase="consumed", handoff_hash=None))
    if changed.rowcount != 1:
        session.rollback()
        raise HTTPException(400, "Organization sign-in handoff is invalid or has expired")
    session.commit()
    response.delete_cookie(oidc.cookie_name(attempt.state_hash), path="/")
    if attempt.linking:
        from ..services.security_audit import authenticated_actor
        authenticated_actor(request, user.id, workspace_id=scope["workspace_id"] if scope else None)
        return {"linked": True, "user_id": user.id, "provider": config["name"], **extra}
    if state.totp_secret:
        result = create_mfa_challenge(session, user, request, expected_auth_version=attempt.auth_version)
        session.add(OIDCMFAChallenge(token_hash=digest(result["challenge_token"])))
        if scope:
            # Creation of a challenge commits; recheck before exposing it.
            tenants.current_config(session, scope["workspace_id"], scope["provider_revision"], user_id=user.id, lock=True)
            session.add(WorkspaceOIDCMFAChallenge(token_hash=digest(result["challenge_token"]), **scope))
        session.commit()
        return {**result, **extra}
    token = create_token(user.id, session, request, expected_auth_version=attempt.auth_version,
                         auth_methods=["sso"], workspace_scope=scope)
    tenants.register_scoped_session(session, token, scope)
    return {"access_token": token, "token_type": "bearer", "user_id": user.id,
            "public_key": user.public_key or "", "encrypted_private_key": user.encrypted_private_key, **extra}


@router.get("/identities")
def identities(response: Response, user=Depends(get_current_user), session=Depends(get_session)):
    _private(response)
    rows = session.exec(select(OIDCIdentity).where(OIDCIdentity.user_id == user.id)).all()
    return [{"id": row.id, "issuer": row.issuer, "provider": row.provider_name, "linked_at": row.created_at,
             "workspace_id": binding.workspace_id if (binding := session.get(WorkspaceOIDCIdentity, row.id)) else None} for row in rows]


@router.delete("/identities/{identity_id}")
def unlink_identity(identity_id: str, data: LinkStart, request: Request, user=Depends(get_current_user),
                    credentials=Depends(security), session=Depends(get_session)):
    require_unrestricted(session)
    # Removing a retired provider must work even when sign-in is disabled.
    account_limit(session, "oidc-unlink-account", user.id, 10, 300)
    session.execute(update(AccountSecurity).where(AccountSecurity.user_id == user.id).values(auth_version=AccountSecurity.auth_version))
    session.expire_all()
    if active_user_for_token(credentials.credentials, session) is None:
        raise HTTPException(401, "Account security changed. Please sign in again.")
    state = ensure_reauthentication(session, user, data.password, data.code)
    if not state.email_verified_at:
        raise HTTPException(403, "Verify your FreeCollab email before removing SSO")
    identity = session.get(OIDCIdentity, identity_id)
    if not identity or identity.user_id != user.id:
        raise HTTPException(404, "Sign-in connection not found")
    session.execute(delete(WorkspaceOIDCIdentity).where(WorkspaceOIDCIdentity.identity_id == identity_id))
    session.execute(delete(OIDCIdentity).where(OIDCIdentity.id == identity_id, OIDCIdentity.user_id == user.id))
    revoke_all_sessions(session, user.id, expected_version=state.auth_version)
    session.commit()
    return {"unlinked": True, "requires_login": True}
