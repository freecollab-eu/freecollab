"""Record allowlisted authenticated security actions, without opening payloads.

This is an operator activity trail, not an immutable external audit ledger.
It deliberately excludes anonymous failures, content routes, query strings,
addresses and credentials. Existing authentication rate counters cover anonymous
failures without creating an unbounded per-attempt event stream.
"""
from datetime import datetime, timedelta
import logging
import re

from sqlalchemy import delete, func, text
from sqlmodel import Session, select
from starlette.concurrency import run_in_threadpool

from ..config import settings
from ..models.security_event import SecurityEvent

log = logging.getLogger("freecollab.security_audit")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_ACTOR = "freecollab.security_actor"


def _identifier(value):
    return value if isinstance(value, str) and _IDENTIFIER.fullmatch(value) else None


def authenticated_actor(request, identifier: str, *, kind="account", workspace_id=None):
    """Called only after server-side credential validation, never from headers."""
    if request is None or not isinstance(getattr(request, "scope", None), dict) or kind not in {"account", "directory"} or not _identifier(identifier):
        return
    request.scope[_ACTOR] = {"actor_kind": kind, "actor_id": identifier,
                             "workspace_id": _identifier(workspace_id)}


def action_for(method, route):
    fixed = {
        ("PUT", "/workspaces/{workspace_id}/personal-oauth/clients/{provider}/{purpose}"): "provider.application_configure",
        ("POST", "/workspaces/{workspace_id}/personal-oauth/clients/{provider}/{purpose}/disable"): "provider.application_disable",
        ("POST", "/workspaces/{workspace_id}/personal-oauth/{provider}/start"): "provider.consent_start",
        ("DELETE", "/workspaces/{workspace_id}/personal-oauth/grants/{grant_id}"): "provider.consent_revoke",
        ("POST", "/connect/workspaces/{workspace_id}/connections"): "connect.invite_create",
        ("POST", "/connect/workspaces/{workspace_id}/accept"): "connect.accept",
        ("POST", "/connect/workspaces/{workspace_id}/connections/{connection_id}/members"): "connect.member_grant",
        ("DELETE", "/connect/workspaces/{workspace_id}/connections/{connection_id}/members/{member_user_id}"): "connect.member_revoke",
        ("DELETE", "/connect/workspaces/{workspace_id}/connections/{connection_id}"): "connect.revoke",
        ("POST", "/connect/workspaces/{workspace_id}/connections/{connection_id}/retry-sync"): "connect.sync",
        ("PUT", "/workspaces/{workspace_id}/data-controls/policy"): "data.retention_configure",
        ("POST", "/workspaces/{workspace_id}/data-controls/exports"): "data.export_request",
        ("POST", "/workspaces/{workspace_id}/data-controls/purges/{preview_id}"): "data.purge_request",
        ("POST", "/workspaces/{workspace_id}/data-controls/exports/{job_id}/download"): "data.export_download",
        ("POST", "/account/data-closure"): "account.identity_erase",
        ("POST", "/workspaces/{workspace_id}/channels/{channel_id}/workflows"): "workflow.create",
        ("PUT", "/workspaces/{workspace_id}/channels/{channel_id}/workflows/{workflow_id}"): "workflow.configure",
        ("POST", "/workspaces/{workspace_id}/channels/{channel_id}/workflows/{workflow_id}/disable"): "workflow.disable",
        ("POST", "/auth/login"): "account.sign_in",
        ("POST", "/auth/mfa/verify"): "account.sign_in",
        ("POST", "/auth/logout"): "account.sign_out",
        ("POST", "/auth/oidc/exchange"): "sso.handoff_complete",
        ("POST", "/workspaces/{workspace_id}/embed/applications"): "embedding.register",
        ("PUT", "/workspaces/{workspace_id}/embed/applications/{application_id}"): "embedding.configure",
        ("POST", "/workspaces/{workspace_id}/embed/launch"): "embedding.launch",
        ("POST", "/embed/{application_id}/exchange"): "embedding.handoff_complete",
        ("POST", "/embed/{application_id}/renew"): "embedding.session_renew",
        ("POST", "/embed/{application_id}/logout"): "embedding.sign_out",
        ("POST", "/auth/oidc/link/start"): "sso.link_start",
        ("DELETE", "/auth/oidc/identities/{identity_id}"): "sso.unlink",
        ("POST", "/auth/reset-password"): "account.password_reset",
        ("POST", "/account/password"): "account.password_change",
        ("DELETE", "/account/sessions/{session_id}"): "account.session_revoke",
        ("POST", "/account/sessions/revoke-all"): "account.sessions_revoke",
        ("POST", "/account/mfa/setup"): "account.mfa_setup",
        ("POST", "/account/mfa/confirm"): "account.mfa_enable",
        ("POST", "/account/mfa/disable"): "account.mfa_disable",
        ("POST", "/account/mfa/recovery-codes"): "account.recovery_codes_replace",
        ("POST", "/admin/users"): "installation.user_create",
        ("PATCH", "/admin/users/{user_id}/role"): "installation.role_change",
        ("PATCH", "/admin/users/{user_id}/block"): "installation.access_change",
        ("DELETE", "/admin/users/{user_id}"): "installation.user_remove",
        ("DELETE", "/admin/channels/{channel_id}"): "installation.channel_remove",
        ("PUT", "/workspaces/{workspace_id}/sso"): "sso.configure",
        ("PUT", "/workspaces/{workspace_id}/identity-policy"): "workforce.policy_change",
        ("POST", "/workspaces/{workspace_id}/workforce/invites"): "workforce.invite_create",
        ("POST", "/workspaces/{workspace_id}/workforce/invites/{invitation_id}/revoke"): "workforce.invite_revoke",
        ("POST", "/workspaces/{workspace_id}/scim/token"): "directory.credential_replace",
        ("POST", "/workspaces/{workspace_id}/scim/disable"): "directory.disable",
        ("PUT", "/workspaces/{workspace_id}/scim/mappings"): "directory.mapping_change",
        ("PATCH", "/workspaces/{workspace_id}/members/{member_user_id}"): "workspace.role_change",
        ("DELETE", "/workspaces/{workspace_id}/members/{member_user_id}"): "workspace.member_remove",
        ("POST", "/secure/unlock"): "encryption.unlock",
        ("POST", "/secure/channels/{channel_id}/enable"): "encryption.channel_enable",
        ("POST", "/secure/devices/{device_id}/revoke"): "encryption.device_revoke",
        ("POST", "/secure/devices/{device_id}/revoke-managed"): "encryption.device_revoke",
        ("POST", "/operations/mail/test"): "operations.mail_test",
        ("POST", "/operations/mail/deliveries/{identifier}/retry"): "operations.mail_retry",
    }
    if (method, route) in fixed:
        return fixed[method, route]
    if method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None
    for prefix, actions in (
        ("/workspaces/{workspace_id}/sso/", {"test", "enable", "disable"}),
        ("/workspaces/{workspace_id}/sync/{connection_id}/", {"test", "enable", "disable", "disconnect", "run", "oauth/start"}),
    ):
        if route.startswith(prefix) and route[len(prefix):] in actions:
            suffix = route[len(prefix):].replace("/", "_")
            return ("sso." if "/sso/" in prefix else "sync.") + suffix
    if route == "/workspaces/{workspace_id}/sync" and method == "POST":
        return "sync.create"
    if route == "/workspaces/{workspace_id}/sync/{connection_id}" and method == "PUT":
        return "sync.configure"
    if route == "/workspaces/{workspace_id}/sync/{connection_id}/conflicts/{conflict_id}/resolve" and method == "POST":
        return "sync.conflict_resolve"
    if route.startswith("/scim/v2/{directory_id}/"):
        resource = route.split("/")[4:]
        if resource in [["Users"], ["Users", "{resource_id}"], ["Groups"], ["Groups", "{resource_id}"]]:
            return "directory." + ("user_" if resource[0] == "Users" else "group_") + {"POST": "create", "PUT": "replace", "PATCH": "change", "DELETE": "deactivate"}[method]
    if route in {"/workspaces/{workspace_id}/webhooks", "/workspaces/{workspace_id}/webhooks/{identifier}",
                 "/workspaces/{workspace_id}/webhooks/{identifier}/rotate"}:
        return "webhook." + {"POST": "create" if route.endswith("/webhooks") else "credential_replace", "PATCH": "configure", "DELETE": "disable"}.get(method, "configure")
    return None


def persist_event(engine, event, *, now=None):
    now = now or datetime.utcnow()
    with Session(engine) as session:
        # Serialize retention/cap decisions across concurrent request threads.
        # These are the two supported installation database backends.
        if engine.dialect.name == "sqlite":
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        elif engine.dialect.name == "postgresql":
            session.exec(text("SELECT pg_advisory_xact_lock(1178812755, 1)"))
        cutoff = now - timedelta(days=settings.security_audit_retention_days)
        expired = session.exec(select(SecurityEvent.id).where(SecurityEvent.created_at < cutoff)
                               .order_by(SecurityEvent.created_at, SecurityEvent.id).limit(200)).all()
        if expired:
            session.exec(delete(SecurityEvent).where(SecurityEvent.id.in_(expired)))
        count = session.exec(select(func.count()).select_from(SecurityEvent)).one()
        if count >= settings.security_audit_max_records:
            oldest = session.exec(select(SecurityEvent.id).order_by(SecurityEvent.created_at, SecurityEvent.id)
                                  .limit(min(200, count - settings.security_audit_max_records + 1))).all()
            session.exec(delete(SecurityEvent).where(SecurityEvent.id.in_(oldest)))
        session.add(SecurityEvent(created_at=now, **event))
        session.commit()


class SecurityAuditMiddleware:
    def __init__(self, app, engine=None):
        self.app, self.engine = app, engine

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") not in {"POST", "PUT", "PATCH", "DELETE"}:
            return await self.app(scope, receive, send)
        status = 500

        async def capture(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, capture)
        finally:
            actor = scope.get(_ACTOR)
            route = getattr(scope.get("route"), "path", "")
            action = action_for(scope.get("method"), route)
            if actor and action:
                parameters = scope.get("path_params", {})
                target = next((_identifier(parameters[key]) for key in (
                    "user_id", "member_user_id", "session_id", "device_id", "channel_id", "connection_id",
                    "resource_id", "invitation_id", "identifier", "application_id", "grant_id") if key in parameters), None)
                event = {**actor, "action": action, "status_code": status,
                         "outcome": "success" if status < 400 else "denied" if status < 500 else "error",
                         "workspace_id": actor["workspace_id"] or _identifier(parameters.get("workspace_id")),
                         "target_id": target}
                try:
                    if self.engine is None:
                        from ..database import engine
                    else:
                        engine = self.engine
                    await run_in_threadpool(persist_event, engine, event)
                except Exception:
                    # A committed action must not be reported as failed because
                    # its separate activity record could not be persisted.
                    log.error("Security activity record could not be persisted")
