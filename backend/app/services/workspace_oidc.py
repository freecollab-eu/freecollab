"""Customer-owned OIDC configuration; no global account provisioning or email trust."""
from datetime import datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

from fastapi import HTTPException
from sqlmodel import select

from ..config import settings
from ..models.workspace_oidc import WorkspaceOIDCProvider, WorkspaceOIDCAttempt, WorkspaceOIDCSession
from .account_security import seal_private, unseal_private
from . import oidc_transport
from .workspaces import lock_workspace, require_workspace_member

TEST_MAX_AGE = timedelta(hours=1)


def callback_url():
    """The operator's application origin fixes the callback, never the customer."""
    try:
        parsed = urlsplit(settings.public_app_url)
        test_http = settings.environment == "test" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or
                (parsed.scheme != "https" and not (test_http and parsed.scheme == "http"))):
            raise ValueError()
        parsed.port
        return urlunsplit((parsed.scheme, parsed.netloc, "/api/auth/oidc/callback", "", ""))
    except (ValueError, TypeError):
        raise HTTPException(503, "The public application origin is not configured correctly") from None


def login_url(provider):
    return settings.public_app_url.rstrip("/") + "/#workspace-sso=" + provider.login_id


def validate_configuration(issuer, scopes):
    try:
        oidc_transport.validate_public_url(issuer, issuer=True)
    except oidc_transport.TransportError:
        raise HTTPException(422, "The issuer must be a public HTTPS URL") from None
    parts = scopes.split()
    if "openid" not in parts or len(parts) > 30 or any(len(part) > 128 for part in parts):
        raise HTTPException(422, "OIDC scopes must include openid")
    return " ".join(dict.fromkeys(parts))


def secret_subject(workspace_id):
    return workspace_id


def encrypted_secret(value, workspace_id):
    return seal_private(value, "workspace-oidc-client-secret", secret_subject(workspace_id)) if value else None


def config_for_provider(provider, *, enabled=True):
    if not provider or (enabled and not provider.enabled):
        raise HTTPException(404, "Workspace sign-in is not enabled")
    try:
        secret = unseal_private(provider.encrypted_client_secret, "workspace-oidc-client-secret", secret_subject(provider.workspace_id)) if provider.encrypted_client_secret else None
    except Exception:
        # Never include ciphertext or credential errors in the response.
        raise HTTPException(503, "Workspace sign-in is temporarily unavailable") from None
    return {"issuer": provider.issuer_url, "redirect_uri": callback_url(), "client_id": provider.client_id,
            "client_secret": secret, "scopes": provider.scopes, "name": provider.name,
            "workspace_id": provider.workspace_id, "provider_revision": provider.revision,
            "login_id": provider.login_id}


def provider_for_login(session, login_id):
    provider = session.exec(select(WorkspaceOIDCProvider).where(WorkspaceOIDCProvider.login_id == login_id,
                                                              WorkspaceOIDCProvider.enabled.is_(True))).first()
    if not provider:
        raise HTTPException(404, "Workspace sign-in is not enabled")
    return provider


def current_config(session, workspace_id, revision, *, user_id=None, lock=False):
    """Refresh and optionally serialize against tenant edits/membership removal."""
    if lock:
        lock_workspace(session, workspace_id)
    provider = session.get(WorkspaceOIDCProvider, workspace_id, populate_existing=True)
    if not provider or not provider.enabled or provider.revision != revision:
        raise HTTPException(400, "Workspace sign-in configuration changed. Please sign in again.")
    if user_id is not None:
        require_workspace_member(session, workspace_id, user_id)
    return config_for_provider(provider)


def config_for_attempt(session, attempt, *, lock=False):
    binding = session.get(WorkspaceOIDCAttempt, attempt.state_hash)
    if not binding:
        return None
    return current_config(session, binding.workspace_id, binding.provider_revision,
                          user_id=attempt.user_id if attempt.user_id else None, lock=lock)


def scope_for_config(config):
    if "workspace_id" not in config:
        return None
    return {"workspace_id": config["workspace_id"], "provider_revision": config["provider_revision"]}


def register_scoped_session(session, token, scope):
    if scope:
        from .auth import token_digest, decode_token_claims
        claims = decode_token_claims(token)
        if not claims:
            raise HTTPException(401, "Sign-in session could not be created")
        current_config(session, scope["workspace_id"], scope["provider_revision"], user_id=claims["sub"], lock=True)
        session.add(WorkspaceOIDCSession(token_hash=token_digest(token), **scope))
        session.commit()


def provider_payload(provider):
    now = datetime.utcnow()
    tested = bool(provider.tested_revision == provider.revision and provider.tested_at and provider.tested_at > now - TEST_MAX_AGE)
    return {"workspace_id": provider.workspace_id, "login_id": provider.login_id, "name": provider.name,
            "issuer_url": provider.issuer_url, "client_id": provider.client_id, "scopes": provider.scopes,
            "has_secret": bool(provider.encrypted_client_secret), "enabled": provider.enabled,
            "revision": provider.revision, "tested": tested, "tested_at": provider.tested_at,
            "callback_url": callback_url(), "login_url": login_url(provider)}


def safe_provider(provider, *, onboarding_mode="linked"):
    return {"id": provider.login_id, "name": provider.name, "issuer": provider.issuer_url,
            "workspace_id": provider.workspace_id, "login_id": provider.login_id,
            "onboarding_mode": onboarding_mode}
