"""Authorization-code/PKCE OIDC adapter with no provider token persistence."""
from datetime import datetime, timedelta
import base64
import hashlib
import hmac
import json
import secrets
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl, quote
from uuid import uuid4

from fastapi import HTTPException
import httpx
import jwt
from pydantic import TypeAdapter, EmailStr, ValidationError
from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import delete, select

from ..config import settings
from ..models.account_security import AccountSecurity
from ..models.oidc import OIDCIdentity, OIDCLoginAttempt
from ..models.session import ActiveSession, RevokedToken
from ..models.user import User
from ..models.workspace_oidc import WorkspaceOIDCAttempt, WorkspaceOIDCIdentity
from ..models.workforce import WorkforceOIDCAttempt
from . import workspace_oidc as tenants, oidc_transport, workforce
from .workspaces import require_workspace_member
from .account_security import digest, seal_private, unseal_private
from .auth import hash_password, normalize_email

MAX_RESPONSE_BYTES = 256 * 1024
ALGORITHMS = {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"}


class OIDCFailure(Exception):
    def __init__(self, reason="sign_in_failed"):
        self.reason = reason


def _secret(value):
    return value.get_secret_value() if hasattr(value, "get_secret_value") else value


def _url(value, *, issuer=False):
    """Operator-chosen provider URLs must use TLS; tests permit loopback only."""
    try:
        parsed = urlsplit(value)
        loopback = settings.environment == "test" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        valid = (parsed.scheme == "https" or (loopback and parsed.scheme == "http")) and parsed.hostname
        valid = valid and not (parsed.username or parsed.password or parsed.fragment)
        valid = valid and not any(c.isspace() for c in value)
        if issuer:
            valid = valid and not parsed.query
        if not valid:
            raise ValueError()
        parsed.port
        return value
    except (TypeError, ValueError):
        raise HTTPException(503, "The organization sign-in provider is not configured correctly") from None


def provider_config():
    if not getattr(settings, "oidc_enabled", False):
        raise HTTPException(404, "Organization sign-in is not enabled")
    issuer = _url(getattr(settings, "oidc_issuer_url", ""), issuer=True)
    redirect = _url(getattr(settings, "oidc_redirect_uri", ""))
    public = _url(settings.public_app_url, issuer=True)
    callback, app = urlsplit(redirect), urlsplit(public)
    if callback.query or (callback.scheme, callback.netloc) != (app.scheme, app.netloc):
        raise HTTPException(503, "Organization sign-in callback must use the public application origin")
    client_id = getattr(settings, "oidc_client_id", "")
    scopes = getattr(settings, "oidc_scopes", "openid profile email").split()
    if not client_id or len(client_id) > 512 or "openid" not in scopes:
        raise HTTPException(503, "The organization sign-in provider is not configured correctly")
    return {
        "issuer": issuer, "redirect_uri": redirect, "client_id": client_id,
        "client_secret": _secret(getattr(settings, "oidc_client_secret", None)),
        "scopes": " ".join(scopes), "name": getattr(settings, "oidc_provider_name", "Organization SSO")[:80],
    }


def request_json(method, url, *, data=None, auth=None):
    """No redirects, bounded bodies and a finite timeout, including metadata."""
    _url(url)
    try:
        with httpx.Client(timeout=httpx.Timeout(12.0, connect=5.0), follow_redirects=False) as client:
            with client.stream(method, url, data=data, auth=auth, headers={"Accept": "application/json"}) as response:
                if response.status_code != 200:
                    raise OIDCFailure()
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise OIDCFailure()
                result = json.loads(body)
                if not isinstance(result, dict):
                    raise OIDCFailure()
                return result
    except (httpx.HTTPError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
        raise OIDCFailure() from None


def _request(config, method, url, *, data=None, auth=None):
    if "workspace_id" not in config:
        return request_json(method, url, data=data, auth=auth)
    try:
        return oidc_transport.request_json(method, url, data=data, auth=auth)
    except oidc_transport.TransportError:
        raise OIDCFailure() from None


def discovery(config):
    document = _request(config, "GET", config["issuer"].rstrip("/") + "/.well-known/openid-configuration")
    if document.get("issuer") != config["issuer"]:
        raise OIDCFailure()
    if "code" not in document.get("response_types_supported", []):
        raise OIDCFailure()
    if "code_challenge_methods_supported" in document and "S256" not in document["code_challenge_methods_supported"]:
        raise OIDCFailure()
    for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not isinstance(document.get(field), str):
            raise OIDCFailure()
        if "workspace_id" in config:
            try:
                oidc_transport.validate_public_url(document[field])
            except oidc_transport.TransportError:
                raise OIDCFailure() from None
        else:
            _url(document[field])
    return document


def cookie_name(state_hash):
    return "fc_oidc_" + state_hash[:20]


def begin_login(session, *, user=None, auth_version=None, session_hash=None, config=None, invitation_token=None):
    config = config or provider_config()
    document = discovery(config)
    now = datetime.utcnow()
    # Stale authorization material is not retained indefinitely.
    expired = select(OIDCLoginAttempt.state_hash).where(OIDCLoginAttempt.expires_at < now - timedelta(hours=1))
    session.exec(delete(WorkforceOIDCAttempt).where(WorkforceOIDCAttempt.state_hash.in_(expired)))
    session.exec(delete(WorkspaceOIDCAttempt).where(WorkspaceOIDCAttempt.state_hash.in_(expired)))
    session.exec(delete(OIDCLoginAttempt).where(OIDCLoginAttempt.expires_at < now - timedelta(hours=1)))
    raw_state, nonce, verifier, browser = (secrets.token_urlsafe(32) for _ in range(4))
    hashed = digest(raw_state)
    attempt = OIDCLoginAttempt(
        state_hash=hashed, browser_hash=digest(browser), nonce_hash=digest(nonce),
        encrypted_verifier=seal_private(verifier, "oidc-pkce", hashed),
        issuer=config["issuer"], client_id=config["client_id"], redirect_uri=config["redirect_uri"],
        expires_at=now + timedelta(minutes=10), linking=user is not None,
        user_id=user.id if user else None, auth_version=auth_version, linking_session_hash=session_hash,
    )
    if "workspace_id" in config:
        # Discovery ran without a lock; bind only the still-current provider revision.
        tenants.current_config(session, config["workspace_id"], config["provider_revision"],
                               user_id=user.id if user else None, lock=True)
    session.add(attempt)
    session.flush()
    if "workspace_id" in config:
        session.add(WorkspaceOIDCAttempt(state_hash=hashed, workspace_id=config["workspace_id"],
                                         provider_revision=config["provider_revision"]))
        if user is None:
            workforce.bind_attempt(session, attempt, config, invitation_token)
    session.commit()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    params = dict(parse_qsl(urlsplit(document["authorization_endpoint"]).query))
    params.update(client_id=config["client_id"], redirect_uri=config["redirect_uri"], response_type="code",
                  scope=config["scopes"], state=raw_state, nonce=nonce,
                  code_challenge=challenge, code_challenge_method="S256")
    endpoint = urlsplit(document["authorization_endpoint"])
    authorization = urlunsplit((endpoint.scheme, endpoint.netloc, endpoint.path, urlencode(params), ""))
    return attempt, browser, authorization


def valid_browser(attempt, browser):
    return isinstance(browser, str) and len(browser) <= 128 and hmac.compare_digest(attempt.browser_hash, digest(browser))


def consume_callback(session, state, browser):
    if not isinstance(state, str) or not 20 <= len(state) <= 256:
        raise OIDCFailure()
    attempt = session.get(OIDCLoginAttempt, digest(state))
    now = datetime.utcnow()
    if not attempt or attempt.phase != "pending" or attempt.expires_at <= now or not valid_browser(attempt, browser):
        raise OIDCFailure()
    config = config_for_attempt(session, attempt, lock=True)
    if (attempt.issuer, attempt.client_id, attempt.redirect_uri) != (config["issuer"], config["client_id"], config["redirect_uri"]):
        raise OIDCFailure()
    consumed = session.exec(update(OIDCLoginAttempt).where(
        OIDCLoginAttempt.state_hash == attempt.state_hash, OIDCLoginAttempt.phase == "pending",
        OIDCLoginAttempt.used_at.is_(None), OIDCLoginAttempt.expires_at > now,
    ).values(phase="processing", used_at=now))
    if consumed.rowcount != 1:
        session.rollback()
        raise OIDCFailure()
    session.commit()
    session.refresh(attempt)
    return attempt, config


def verified_claims(attempt, config, code):
    document = discovery(config)
    verifier = unseal_private(attempt.encrypted_verifier, "oidc-pkce", attempt.state_hash)
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": attempt.redirect_uri,
            "client_id": config["client_id"], "code_verifier": verifier}
    methods = document.get("token_endpoint_auth_methods_supported", ["client_secret_basic"])
    auth = None
    if config["client_secret"]:
        if "client_secret_basic" in methods:
            encoded_auth = (quote(config["client_id"], safe=""), quote(config["client_secret"], safe=""))
            auth = encoded_auth if "workspace_id" in config else httpx.BasicAuth(*encoded_auth)
        elif "client_secret_post" in methods:
            form["client_secret"] = config["client_secret"]
        else:
            raise OIDCFailure()
    elif "none" not in methods:
        raise OIDCFailure()
    tokens = _request(config, "POST", document["token_endpoint"], data=form, auth=auth)
    encoded = tokens.get("id_token")
    if not isinstance(encoded, str) or len(encoded) > 64 * 1024:
        raise OIDCFailure()
    try:
        header = jwt.get_unverified_header(encoded)
        algorithm, kid = header.get("alg"), header.get("kid")
        if algorithm not in ALGORITHMS or not isinstance(kid, str) or not 1 <= len(kid) <= 256 or header.get("crit"):
            raise OIDCFailure()
        keys = _request(config, "GET", document["jwks_uri"]).get("keys")
        if not isinstance(keys, list) or len(keys) > 100:
            raise OIDCFailure()
        matching = [key for key in keys if isinstance(key, dict) and key.get("kid") == kid and key.get("use", "sig") == "sig" and key.get("alg", algorithm) == algorithm]
        if len(matching) != 1:
            raise OIDCFailure()
        if any(field in matching[0] for field in ("d", "p", "q", "dp", "dq", "qi", "oth")):
            raise OIDCFailure()
        key = jwt.PyJWK.from_dict(matching[0], algorithm=algorithm)
        claims = jwt.decode(encoded, key.key, algorithms=[algorithm], issuer=config["issuer"], audience=config["client_id"],
                            leeway=15, options={"require": ["iss", "sub", "aud", "exp", "iat", "nonce"]})
        subject, nonce = claims.get("sub"), claims.get("nonce")
        if not isinstance(subject, str) or not 1 <= len(subject) <= 255 or not isinstance(nonce, str):
            raise OIDCFailure()
        if not hmac.compare_digest(digest(nonce), attempt.nonce_hash):
            raise OIDCFailure()
        aud = claims["aud"]
        if (isinstance(aud, list) and len(aud) > 1 and claims.get("azp") != config["client_id"]) or ("azp" in claims and claims["azp"] != config["client_id"]):
            raise OIDCFailure()
        return claims
    except (jwt.PyJWTError, ValueError, TypeError, KeyError, AttributeError):
        raise OIDCFailure() from None


def config_for_attempt(session, attempt, *, lock=False):
    return tenants.config_for_attempt(session, attempt, lock=lock) or provider_config()


def _identity_id(issuer, subject):
    return digest(issuer + "\0" + subject)


def valid_link_session(session, attempt):
    user = session.get(User, attempt.user_id) if attempt.user_id else None
    state = session.get(AccountSecurity, attempt.user_id) if attempt.user_id else None
    tracked = session.exec(select(ActiveSession).where(ActiveSession.token_hash == attempt.linking_session_hash)).first()
    now = datetime.utcnow()
    if (not user or user.is_blocked or not state or state.auth_version != attempt.auth_version or
        not state.email_verified_at or not tracked or tracked.revoked_at or tracked.expires_at <= now or
        session.get(RevokedToken, attempt.linking_session_hash)):
        raise OIDCFailure()
    return user, state


def resolve_identity(session, attempt, config, claims):
    if "workspace_id" in config:
        return resolve_workspace_identity(session, attempt, config, claims)
    identity_id = _identity_id(config["issuer"], claims["sub"])
    identity = session.get(OIDCIdentity, identity_id)
    if identity and (identity.issuer, identity.subject) != (config["issuer"], claims["sub"]):
        raise OIDCFailure()
    if attempt.linking:
        # Serialize the final authorization check with native password/MFA edits.
        session.execute(update(AccountSecurity).where(AccountSecurity.user_id == attempt.user_id).values(auth_version=AccountSecurity.auth_version))
        session.expire_all()
        user, state = valid_link_session(session, attempt)
        if identity and identity.user_id != user.id:
            raise OIDCFailure("identity_already_linked")
    elif identity:
        user = session.get(User, identity.user_id)
        state = session.get(AccountSecurity, identity.user_id)
        if not user or user.is_blocked or not state:
            raise OIDCFailure()
    else:
        if claims.get("email_verified") is not True and not getattr(settings, "oidc_trust_email_claim", False):
            raise OIDCFailure("email_not_verified")
        raw_email = claims.get("email", claims.get("preferred_username"))
        try:
            email = normalize_email(str(TypeAdapter(EmailStr).validate_python(raw_email)))
        except (ValidationError, ValueError, TypeError):
            raise OIDCFailure("email_not_verified") from None
        if session.exec(select(User).where(func.lower(User.email) == email)).first():
            raise OIDCFailure("account_link_required")
        name = claims.get("name")
        if not isinstance(name, str) or not name.strip():
            name = email.split("@", 1)[0]
        user = User(id=uuid4().hex, name=name.strip()[:80], email=email, password_hash=hash_password(secrets.token_urlsafe(40)))
        state = AccountSecurity(user_id=user.id, email_verified_at=datetime.utcnow())
        session.add(user)
        session.add(state)
        session.flush()
    if not identity:
        session.add(OIDCIdentity(id=identity_id, user_id=user.id, issuer=config["issuer"], subject=claims["sub"], provider_name=config["name"]))
    attempt.user_id, attempt.auth_version = user.id, state.auth_version
    handoff = secrets.token_urlsafe(32)
    attempt.handoff_hash, attempt.phase = digest(handoff), "ready"
    attempt.encrypted_verifier = ""
    attempt.expires_at = datetime.utcnow() + timedelta(minutes=2)
    session.add(attempt)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        # Concurrent first provision/link requests may not pick an arbitrary user.
        raise OIDCFailure("account_link_required") from None
    return handoff


def resolve_workspace_identity(session, attempt, config, claims):
    """Personal links and managed identities occupy separate authority domains."""
    if attempt.linking:
        session.execute(update(AccountSecurity).where(AccountSecurity.user_id == attempt.user_id).values(auth_version=AccountSecurity.auth_version))
        session.expire_all()
        user, state = valid_link_session(session, attempt)
    # Recheck after all provider network activity; config edits and membership changes
    # serialize on this workspace. Managed accounts never claim a global email.
    current = tenants.current_config(session, config["workspace_id"], config["provider_revision"],
                                     user_id=attempt.user_id if attempt.linking else None, lock=True)
    if (attempt.issuer, attempt.client_id, attempt.redirect_uri) != (current["issuer"], current["client_id"], current["redirect_uri"]):
        raise OIDCFailure()
    identity_id = digest("workspace\0" + config["workspace_id"] + "\0" + config["issuer"] + "\0" + config["client_id"] + "\0" + claims["sub"])
    identity = session.get(OIDCIdentity, identity_id)
    binding = session.get(WorkspaceOIDCIdentity, identity_id)
    if identity and (not binding or binding.workspace_id != config["workspace_id"] or
                     (identity.issuer, identity.subject) != (config["issuer"], claims["sub"])):
        raise OIDCFailure()
    # Directory provisioning must not create a shadow that SCIM later disables
    # while an older personal-account SSO binding silently keeps signing in.
    # Matching is confined to the administrator-selected claim in this workspace;
    # it never looks up or converts a global personal account by email.
    policy = workforce.identity_policy(session, config["workspace_id"])
    external_id = claims.get(policy.identity_claim)
    mapped = workforce.principal_for_external_id(session, config["workspace_id"], external_id) if isinstance(external_id, str) else None
    existing_user_id = user.id if attempt.linking else identity.user_id if identity else None
    if mapped is not None and existing_user_id is not None and mapped.user_id != existing_user_id:
        raise OIDCFailure("workforce_identity_conflict")
    if attempt.linking:
        if identity and identity.user_id != user.id:
            raise OIDCFailure("identity_already_linked")
    elif identity:
        user = session.get(User, identity.user_id)
        state = session.get(AccountSecurity, identity.user_id)
        principal = workforce.managed_principal(session, identity.user_id)
        if not user or user.is_blocked or not state:
            raise OIDCFailure()
        if principal is not None:
            if not principal.active:
                raise OIDCFailure("workforce_access_required")
            if (principal.workspace_id != config["workspace_id"]
                    or (principal.issuer, principal.client_id, principal.subject) != (config["issuer"], config["client_id"], claims["sub"])):
                raise OIDCFailure()
        elif not state.email_verified_at:
            raise OIDCFailure()
        try:
            require_workspace_member(session, config["workspace_id"], user.id)
        except HTTPException:
            if principal is not None:
                raise OIDCFailure("workforce_access_required") from None
            raise
    else:
        try:
            user, state = workforce.resolve_managed_principal(session, attempt, config, claims)
        except HTTPException:
            reason = "workspace_link_required" if policy.mode == "linked" else "workforce_access_required"
            raise OIDCFailure(reason) from None
    if not identity:
        session.add(OIDCIdentity(id=identity_id, user_id=user.id, issuer=config["issuer"], subject=claims["sub"], provider_name=config["name"]))
        session.flush()
        session.add(WorkspaceOIDCIdentity(identity_id=identity_id, workspace_id=config["workspace_id"]))
    attempt.user_id, attempt.auth_version = user.id, state.auth_version
    handoff = secrets.token_urlsafe(32)
    attempt.handoff_hash, attempt.phase = digest(handoff), "ready"
    attempt.encrypted_verifier = ""
    attempt.expires_at = datetime.utcnow() + timedelta(minutes=2)
    session.add(attempt)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise OIDCFailure("workspace_link_required") from None
    return handoff
