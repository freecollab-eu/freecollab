"""Workspace administrators configure their own identity provider with native reauthentication."""
from datetime import datetime
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import update

from ..database import get_session
from ..models.account_security import AccountSecurity
from ..models.workspace_oidc import WorkspaceOIDCProvider
from ..services import oidc, workspace_oidc as tenants
from ..services.account_security import account_limit, ensure_reauthentication
from ..services.auth import active_user_for_token, get_current_user, security, validate_password
from ..services.sso_scope import require_unrestricted
from ..services.workspaces import lock_workspace, require_workspace_admin

router = APIRouter(prefix="/workspaces/{workspace_id}/sso", tags=["workspace sign-in"])


class Reauthenticate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=72)
    code: str | None = Field(default=None, max_length=64)
    _password = field_validator("password")(validate_password)


class ProviderDraft(Reauthenticate):
    name: str = Field(min_length=1, max_length=80)
    issuer_url: str = Field(min_length=1, max_length=2048)
    client_id: str = Field(min_length=1, max_length=512)
    client_secret: str | None = Field(default="", max_length=4096)
    scopes: str = Field(default="openid profile email", min_length=1, max_length=2048)

    @field_validator("name", "client_id")
    @classmethod
    def nonempty(cls, value):
        if not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("A nonempty value without control characters is required")
        return value.strip()


def _private(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"


def _result(provider):
    return {"configured": provider is not None, "callback_url": tenants.callback_url(),
            "login_url": tenants.login_url(provider) if provider else None,
            "provider": tenants.provider_payload(provider) if provider else None}


def _issuer_identity(value):
    """Compare network aliases without merging distinct OIDC issuer paths."""
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").encode("idna").decode("ascii").lower()
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return parsed.scheme, host, port, parsed.path, parsed.query
    except (TypeError, ValueError, UnicodeError):
        return None


def _lock_actor(session, user, credentials, workspace_id):
    # Keep lock order consistent with explicit linking and native account changes.
    session.execute(update(AccountSecurity).where(AccountSecurity.user_id == user.id).values(auth_version=AccountSecurity.auth_version))
    session.expire_all()
    if active_user_for_token(credentials.credentials, session) is None:
        raise HTTPException(401, "Account security changed. Please sign in again.")
    require_unrestricted(session)
    lock_workspace(session, workspace_id)
    require_workspace_admin(session, workspace_id, user.id)


def _authorize(session, user, credentials, workspace_id, data):
    require_unrestricted(session)
    account_limit(session, "workspace-sso-config", user.id, 20, 300)
    _lock_actor(session, user, credentials, workspace_id)
    return ensure_reauthentication(session, user, data.password, data.code)


@router.get("")
def get_provider(workspace_id: str, response: Response, user=Depends(get_current_user), session=Depends(get_session)):
    require_unrestricted(session)
    require_workspace_admin(session, workspace_id, user.id)
    _private(response)
    return _result(session.get(WorkspaceOIDCProvider, workspace_id))


@router.put("")
def save_provider(workspace_id: str, data: ProviderDraft, response: Response, user=Depends(get_current_user),
                  credentials=Depends(security), session=Depends(get_session)):
    _private(response)
    _authorize(session, user, credentials, workspace_id, data)
    scopes = tenants.validate_configuration(data.issuer_url, data.scopes)
    provider = session.get(WorkspaceOIDCProvider, workspace_id)
    identity_changed = bool(provider and (
        _issuer_identity(provider.issuer_url) != _issuer_identity(data.issuer_url)
        or provider.client_id != data.client_id
    ))
    if provider is None:
        provider = WorkspaceOIDCProvider(workspace_id=workspace_id, name=data.name, issuer_url=data.issuer_url,
                                         client_id=data.client_id, created_by=user.id)
    else:
        provider.revision += 1
    provider.name, provider.issuer_url, provider.client_id, provider.scopes = data.name, data.issuer_url, data.client_id, scopes
    # Retention is confined to the same issuer/client. Another administrator
    # cannot forward a saved confidential credential to a replacement provider.
    if data.client_secret is None or (identity_changed and not data.client_secret):
        provider.encrypted_client_secret = None
    elif data.client_secret:
        provider.encrypted_client_secret = tenants.encrypted_secret(data.client_secret, workspace_id)
    provider.enabled = False
    provider.tested_revision = provider.tested_at = None
    provider.updated_at = datetime.utcnow()
    session.add(provider)
    session.commit()
    return _result(provider)


def _test_snapshot(workspace_id, data, user, credentials, session):
    state = _authorize(session, user, credentials, workspace_id, data)
    provider = session.get(WorkspaceOIDCProvider, workspace_id)
    if provider is None:
        raise HTTPException(404, "Save a workspace sign-in provider first")
    config = tenants.config_for_provider(provider, enabled=False)
    revision, auth_version = provider.revision, state.auth_version
    session.commit()  # No database lock is held while contacting the customer's provider.
    try:
        oidc.discovery(config)
    except oidc.OIDCFailure:
        raise HTTPException(422, "Provider discovery failed. Check the public HTTPS issuer and OIDC endpoints.") from None
    _lock_actor(session, user, credentials, workspace_id)
    current_state = session.get(AccountSecurity, user.id)
    provider = session.get(WorkspaceOIDCProvider, workspace_id, populate_existing=True)
    if not provider or provider.revision != revision or not current_state or current_state.auth_version != auth_version:
        raise HTTPException(409, "The account or provider changed during the connection test. Test again.")
    return provider


@router.post("/test")
def test_provider(workspace_id: str, data: Reauthenticate, response: Response, user=Depends(get_current_user),
                  credentials=Depends(security), session=Depends(get_session)):
    _private(response)
    provider = _test_snapshot(workspace_id, data, user, credentials, session)
    provider.tested_revision, provider.tested_at = provider.revision, datetime.utcnow()
    session.add(provider)
    session.commit()
    return {"valid": True, "provider": tenants.provider_payload(provider)}


@router.post("/enable")
def enable_provider(workspace_id: str, data: Reauthenticate, response: Response, user=Depends(get_current_user),
                    credentials=Depends(security), session=Depends(get_session)):
    _private(response)
    # An explicit successful saved-draft test is required, and enable revalidates discovery.
    require_unrestricted(session)
    require_workspace_admin(session, workspace_id, user.id)
    existing = session.get(WorkspaceOIDCProvider, workspace_id)
    if not existing or not tenants.provider_payload(existing)["tested"]:
        raise HTTPException(409, "Test the saved provider before enabling workspace sign-in")
    provider = _test_snapshot(workspace_id, data, user, credentials, session)
    if not tenants.provider_payload(provider)["tested"]:
        raise HTTPException(409, "Test the saved provider before enabling workspace sign-in")
    provider.enabled = True
    provider.revision += 1
    provider.tested_revision, provider.tested_at = provider.revision, datetime.utcnow()
    provider.updated_at = datetime.utcnow()
    session.add(provider)
    session.commit()
    return _result(provider)


@router.post("/disable")
def disable_provider(workspace_id: str, data: Reauthenticate, response: Response, user=Depends(get_current_user),
                     credentials=Depends(security), session=Depends(get_session)):
    _private(response)
    _authorize(session, user, credentials, workspace_id, data)
    provider = session.get(WorkspaceOIDCProvider, workspace_id)
    if not provider:
        raise HTTPException(404, "Workspace sign-in is not configured")
    provider.enabled = False
    provider.revision += 1
    provider.tested_revision = provider.tested_at = None
    provider.updated_at = datetime.utcnow()
    session.add(provider)
    session.commit()
    return _result(provider)
