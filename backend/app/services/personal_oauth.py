"""Personal Google/Microsoft consent, bounded to a customer's OAuth registration.

No discovery URL or token destination comes from a user-controlled endpoint.
Refresh leases never retry an ambiguous rotating-token exchange. The initiating
FreeCollab sign-in remains part of the grant; logging out requires fresh consent.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta
import base64
import hashlib
import json
import re
import secrets
from urllib.parse import urlencode, urlsplit, urlunsplit
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, update
from sqlmodel import select

from ..config import settings
from ..models.app_platform import AppInstallation
from ..models.account_security import AccountSecurity
from ..models.enterprise_search import EnterpriseSearchConnection
from ..models.personal_oauth import PersonalOAuthAttempt, PersonalOAuthClient, PersonalOAuthGrant
from ..models.user import User
from . import remote_transport as transport
from .account_security import seal_private, unseal_private
from .request_guard import recheck_actor, remember_request
from .workspaces import require_workspace_member

SCOPES = {
    ("google", "search"): "https://www.googleapis.com/auth/drive.readonly",
    ("microsoft", "search"): "Files.Read.All",
    ("google", "calendar-action"): "https://www.googleapis.com/auth/calendar.events",
    ("microsoft", "calendar-action"): "Calendars.ReadWrite",
}
PROTOCOLS = {"google": "google.calendar-event", "microsoft": "microsoft.calendar-event"}
TOKEN_LIMIT = 16000


def lock_owner(session, user_id):
    """Match identity-closure lock order before retaining any private material."""
    session.execute(update(User).where(User.id == user_id).values(name=User.name))
    session.execute(update(AccountSecurity).where(AccountSecurity.user_id == user_id)
        .values(auth_version=AccountSecurity.auth_version))
    session.expire_all()


def clean(value, maximum=1024, *, empty=False):
    if (not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip())
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise HTTPException(422, "Check the customer OAuth configuration")
    return value.strip()


def callback_url():
    try:
        value = urlsplit(settings.public_app_url)
        local = value.hostname in {"localhost", "127.0.0.1", "::1"} and settings.environment != "production"
        if (not value.hostname or value.username or value.password or value.query or value.fragment
                or value.path not in {"", "/"} or value.scheme != "https" and not (local and value.scheme == "http")):
            raise ValueError()
        value.port
        return urlunsplit((value.scheme, value.netloc, "/api/personal-oauth/callback", "", ""))
    except (ValueError, TypeError):
        raise HTTPException(503, "Configure the public application HTTPS origin before connecting OAuth") from None


def scope(provider, purpose):
    result = SCOPES.get((provider, purpose))
    if not result:
        raise HTTPException(422, "Choose a supported provider and delegation purpose")
    return result


def configuration(client, *, secret=True):
    scope(client.provider, client.purpose)
    clean(client.client_id)
    if client.provider == "microsoft" and not re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", client.tenant_id):
        raise HTTPException(422, "Use the customer's exact Microsoft tenant UUID")
    result = {"client_id": client.client_id, "tenant_id": client.tenant_id}
    if secret and client.encrypted_secret:
        result["client_secret"] = unseal_private(client.encrypted_secret, "personal-oauth-client", client.id)
    return result


def authorization_url(client, state, verifier):
    configuration(client, secret=False)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    endpoint = ("https://accounts.google.com/o/oauth2/v2/auth" if client.provider == "google" else
                "https://login.microsoftonline.com/" + client.tenant_id + "/oauth2/v2.0/authorize")
    requested = scope(client.provider, client.purpose)
    extra = {"access_type": "offline", "prompt": "consent", "include_granted_scopes": "false"} if client.provider == "google" else {"response_mode": "query"}
    if client.provider == "microsoft":
        requested += " offline_access"
    return endpoint + "?" + urlencode({"client_id": client.client_id, "redirect_uri": callback_url(),
        "response_type": "code", "scope": requested, "state": state, "code_challenge": challenge,
        "code_challenge_method": "S256", **extra})


def token_request(provider, purpose, config, fields):
    endpoint = ("https://oauth2.googleapis.com/token" if provider == "google" else
                "https://login.microsoftonline.com/" + config["tenant_id"] + "/oauth2/v2.0/token")
    payload = {"client_id": config["client_id"], **fields}
    if config.get("client_secret"):
        payload["client_secret"] = config["client_secret"]
    budget = transport.begin_budget(25)
    try:
        result = transport.check_response(transport.request("POST", endpoint,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body=urlencode(payload).encode(), timeout=15), (200,)).json()
    finally:
        transport.end_budget(budget)
    try:
        access = clean(result.get("access_token"), TOKEN_LIMIT)
        refresh = clean(result.get("refresh_token"), TOKEN_LIMIT) if result.get("refresh_token") else None
        if any(character.isspace() for character in access) or refresh and any(character.isspace() for character in refresh):
            raise ValueError()
        if result.get("token_type", "Bearer").lower() != "bearer":
            raise ValueError()
        expires = result.get("expires_in", 3600)
        if type(expires) is not int or not 1 <= expires <= 86400:
            raise ValueError()
        returned = result.get("scope")
        if returned is not None:
            if not isinstance(returned, str) or len(returned) > 4000:
                raise ValueError()
            # Never accept a broader write delegation into the read-only search
            # credential slot, even when a provider returns combined consent.
            actual = set(returned.split())
            if provider == "microsoft":
                actual = {value.removeprefix("https://graph.microsoft.com/") for value in actual}
            required = scope(provider, purpose)
            allowed = {required, "offline_access", "openid", "email", "profile"}
            if required not in actual or actual - allowed:
                raise ValueError()
    except (HTTPException, AttributeError, ValueError, TypeError):
        raise transport.ProviderError("invalid_token_response") from None
    return {"access_token": access, **({"refresh_token": refresh} if refresh else {}),
        "expires_at": (datetime.utcnow() + timedelta(seconds=expires)).isoformat()}


def tokens(row):
    try:
        return json.loads(unseal_private(row.encrypted_tokens, "personal-oauth-tokens", row.id))
    except (ValueError, TypeError):
        raise HTTPException(409, "Reconnect this personal provider delegation") from None


def store_tokens(row, value):
    row.encrypted_tokens = seal_private(json.dumps(value, separators=(",", ":")), "personal-oauth-tokens", row.id)
    row.expires_at = datetime.fromisoformat(value["expires_at"])


@contextmanager
def source_authority(session, row):
    """Use only stored *verified* source claims, never infer missing sidecars."""
    previous = dict(session.info)
    try:
        claims = json.loads(unseal_private(row.encrypted_source_claims, "personal-oauth-source", row.id))
        if claims.get("sub") != row.user_id:
            raise ValueError()
        remember_request(session, row.source_hash, claims)
        recheck_actor(session, row.user_id)
        require_workspace_member(session, row.workspace_id, row.user_id)
        yield
    except (ValueError, TypeError, KeyError):
        raise HTTPException(403, "The original provider consent sign-in is no longer valid") from None
    finally:
        session.info.clear()
        session.info.update(previous)


def current_grant(session, identifier, *, user_id=None, status="active"):
    row = session.get(PersonalOAuthGrant, identifier, populate_existing=True)
    if not row or user_id is not None and row.user_id != user_id:
        raise HTTPException(404, "Personal provider delegation not found")
    client = session.get(PersonalOAuthClient, row.client_id, populate_existing=True)
    if (not client or not client.enabled or client.workspace_id != row.workspace_id
            or client.revision != row.client_revision or client.provider != row.provider or client.purpose != row.purpose
            or row.status != status or row.revoked_at):
        raise HTTPException(409, "The provider delegation changed; connect again")
    with source_authority(session, row):
        if row.purpose == "search" and status == "active":
            target = session.get(EnterpriseSearchConnection, row.target_id, populate_existing=True)
            if (not target or target.user_id != row.user_id or target.workspace_id != row.workspace_id
                    or target.provider != row.provider or target.revision != row.target_revision):
                raise HTTPException(409, "The personal search connection changed")
        if row.purpose == "calendar-action":
            from . import app_platform
            target = session.get(AppInstallation, row.target_id, populate_existing=True)
            if (not target or target.workspace_id != row.workspace_id or target.generation != row.target_revision
                    or not app_platform.allowed_installation(session, target)
                    or {command.protocol for command in app_platform.manifest_for(target).commands} != {PROTOCOLS[row.provider]}):
                raise HTTPException(409, "The installed app or its grants changed")
            # A user may consent only for an app they can currently invoke in at
            # least one granted native channel; actual jobs recheck their channel.
            visible = False
            for channel_id in app_platform.channels(target):
                try:
                    app_platform.native_channel(session, row.workspace_id, channel_id, row.user_id,
                        allow_secure=app_platform.manifest_for(target).secure_foreground)
                    visible = True
                except HTTPException:
                    continue
            if not visible:
                raise HTTPException(403, "Current app channel membership is required")
    return row, client


def grant_for_target(session, purpose, target_id, user_id):
    return session.exec(select(PersonalOAuthGrant).where(PersonalOAuthGrant.purpose == purpose,
        PersonalOAuthGrant.target_id == target_id, PersonalOAuthGrant.user_id == user_id)
        .order_by(PersonalOAuthGrant.created_at.desc(), PersonalOAuthGrant.id.desc()).limit(1)).first()


def access_token(session, identifier, *, user_id=None, force_refresh=False):
    row, client = current_grant(session, identifier, user_id=user_id)
    revision = row.revision
    secret = tokens(row)
    if not force_refresh and row.expires_at and row.expires_at > datetime.utcnow() + timedelta(seconds=45):
        return clean(secret.get("access_token"), TOKEN_LIMIT)
    if not secret.get("refresh_token"):
        invalidate(session, row, "reauthorization_required")
        session.commit()
        raise HTTPException(409, "The provider requires a new consent")
    now, claim = datetime.utcnow(), secrets.token_urlsafe(24)
    # Do not replay a possibly rotated refresh token after a crashed exchange.
    if row.refresh_claim:
        if row.refresh_started_at and row.refresh_started_at < now - timedelta(seconds=60):
            invalidate(session, row, "reauthorization_required")
            session.commit()
            raise HTTPException(409, "An interrupted token rotation requires new consent")
        raise HTTPException(409, "Provider credentials are being refreshed; try again shortly")
    config, provider, purpose = configuration(client), row.provider, row.purpose
    changed = session.execute(update(PersonalOAuthGrant).where(PersonalOAuthGrant.id == identifier,
        PersonalOAuthGrant.revision == revision, PersonalOAuthGrant.status == "active",
        PersonalOAuthGrant.refresh_claim.is_(None)).values(refresh_claim=claim, refresh_started_at=now))
    session.commit()
    if changed.rowcount != 1:
        raise HTTPException(409, "Provider credentials changed; try again")
    try:
        current_grant(session, identifier, user_id=user_id)
        session.commit()
        fresh = token_request(provider, purpose, config, {"grant_type": "refresh_token", "refresh_token": secret["refresh_token"]})
        from .workspaces import lock_workspace
        lock_owner(session, row.user_id)
        lock_workspace(session, row.workspace_id)
        row, _ = current_grant(session, identifier, user_id=user_id)
        if row.revision != revision or row.refresh_claim != claim:
            raise HTTPException(409, "Provider credentials changed during refresh")
        secret.update(fresh)
        encrypted = seal_private(json.dumps(secret, separators=(",", ":")), "personal-oauth-tokens", identifier)
        saved = session.execute(update(PersonalOAuthGrant).where(PersonalOAuthGrant.id == identifier,
            PersonalOAuthGrant.revision == revision, PersonalOAuthGrant.status == "active",
            PersonalOAuthGrant.refresh_claim == claim).values(encrypted_tokens=encrypted,
            expires_at=datetime.fromisoformat(secret["expires_at"]), refresh_claim=None,
            refresh_started_at=None, revision=revision + 1))
        if saved.rowcount != 1:
            raise HTTPException(409, "Provider credentials changed during refresh")
        session.commit()
    except (HTTPException, transport.ProviderError):
        session.rollback()
        row = session.get(PersonalOAuthGrant, identifier, populate_existing=True)
        if row and row.revision == revision and row.refresh_claim == claim:
            invalidate(session, row, "reauthorization_required")
            session.commit()
        raise HTTPException(409, "Provider refresh failed or consent changed; connect again") from None
    current_grant(session, identifier, user_id=user_id)
    return clean(secret["access_token"], TOKEN_LIMIT)


def invalidate(session, row, status="revoked"):
    row.status, row.revoked_at = status, datetime.utcnow()
    row.encrypted_tokens = row.encrypted_source_claims = ""
    row.refresh_claim = row.refresh_started_at = row.expires_at = None
    row.revision += 1
    session.add(row)
    session.execute(update(PersonalOAuthAttempt).where(PersonalOAuthAttempt.grant_id == row.id)
        .values(encrypted_verifier="", used_at=datetime.utcnow()))


def revocation_token(row):
    if not row.encrypted_tokens:
        return None
    saved = tokens(row)
    return saved.get("refresh_token") or saved.get("access_token")


def revoke_at_provider(provider, remote_token):
    """Called only after local erasure commits; no broad Microsoft sign-out."""
    if provider == "microsoft":
        return "not-supported"
    if provider != "google" or not remote_token:
        return "not-needed"
    try:
        result = transport.request("POST", "https://oauth2.googleapis.com/revoke",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body=urlencode({"token": remote_token}).encode(), timeout=10)
        return "requested" if result.status == 200 else "provider-rejected"
    except transport.ProviderError:
        return "provider-unavailable"


def public_client(row):
    return {"id": row.id, "provider": row.provider, "purpose": row.purpose, "client_id": row.client_id,
        "tenant_id": row.tenant_id, "revision": row.revision, "enabled": row.enabled,
        "has_secret": bool(row.encrypted_secret), "scope": scope(row.provider, row.purpose)}


def public_grant(session, row):
    active = False
    if row.status == "active":
        try:
            current_grant(session, row.id, user_id=row.user_id)
            active = True
        except HTTPException:
            pass
    return {"id": row.id, "provider": row.provider, "purpose": row.purpose, "name": row.name,
        "target_id": row.target_id, "status": row.status if active or row.status != "active" else "consent_expired",
        "scope": scope(row.provider, row.purpose), "connected": active, "expires_at": row.expires_at,
        "created_at": row.created_at}


def close_account_resources(session, user_id):
    """Erase personal tokens/PKCE material and customer secrets owned by closure."""
    clients = session.exec(select(PersonalOAuthClient).where(PersonalOAuthClient.configured_by == user_id)).all()
    client_ids = [row.id for row in clients]
    for client in clients:
        client.enabled, client.encrypted_secret, client.revision = False, "", client.revision + 1
        session.add(client)
    from sqlalchemy import or_
    grants = session.exec(select(PersonalOAuthGrant).where(or_(PersonalOAuthGrant.user_id == user_id,
        PersonalOAuthGrant.client_id.in_(client_ids)))).all()
    for row in grants:
        invalidate(session, row, "account_erased")
    from ..models.personal_oauth import PersonalOAuthAttempt
    if grants:
        session.execute(update(PersonalOAuthAttempt).where(PersonalOAuthAttempt.grant_id.in_([row.id for row in grants]))
            .values(encrypted_verifier="", used_at=datetime.utcnow()))


def reap_grants(session, limit=100):
    """Fair bounded local cleanup; no provider traffic or automatic reconsent."""
    from ..models.personal_oauth import PersonalOAuthAttempt
    now = datetime.utcnow()
    rows = session.exec(select(PersonalOAuthGrant).where(PersonalOAuthGrant.status.in_(["pending", "active"]),
        PersonalOAuthGrant.last_checked_at < now - timedelta(seconds=60))
        .order_by(PersonalOAuthGrant.last_checked_at, PersonalOAuthGrant.id).limit(min(max(limit, 1), 200))).all()
    erased = 0
    for row in rows:
        stale = False
        if row.status == "pending":
            attempt = session.exec(select(PersonalOAuthAttempt).where(PersonalOAuthAttempt.grant_id == row.id)).first()
            stale = not attempt or attempt.expires_at <= now
        if not stale:
            try:
                current_grant(session, row.id, status=row.status)
            except HTTPException as error:
                if error.status_code >= 500:
                    # A temporarily unavailable retained encryption key or
                    # database is not evidence that a delegation was revoked.
                    raise
                stale = True
        if stale:
            invalidate(session, row, "consent_expired")
            session.execute(update(PersonalOAuthAttempt).where(PersonalOAuthAttempt.grant_id == row.id)
                .values(encrypted_verifier="", used_at=now))
            erased += 1
        row.last_checked_at = now
        session.add(row)
    session.commit()
    return erased
