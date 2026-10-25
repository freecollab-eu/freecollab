"""Installable, least-privilege apps and explicit-context AI automation.

There is no bot account token. Installations can receive only invoked, reviewed
arguments and can publish declarative cards to their exact native-channel grant.
"""
from datetime import datetime, timedelta
import hashlib
import hmac
import json
import re
import secrets
import time
from types import SimpleNamespace
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator
from sqlalchemy import and_, delete, func, or_, update
from sqlmodel import select

from ..models.app_platform import AppAIDraft, AppCard, AppInstallation, AppJob, AppRevision, ChannelRecapSchedule
from ..models.account_security import AccountSecurity
from ..models.channel import Channel
from ..models.message import Message
from ..models.user import User
from ..models.workspace import WorkspaceChannel
from . import copilot, webhooks, workflow
from .access import lock_channel_writes, require_channel_member
from .account_security import seal_private, unseal_private
from .matrix_bridge import is_secure_channel
from .request_guard import recheck_actor
from .sso_scope import require_unrestricted
from .workspaces import lock_workspace, require_workspace_member

MAX_ATTEMPTS = 8
LEASE_SECONDS = 90
SCOPES = {"commands:invoke", "cards:write", "actions:write"}


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


# Declaration metadata is normalized; explicitly reviewed content is literal.
ReviewedArgument = Annotated[str, StringConstraints(strip_whitespace=False)]


class Argument(Strict):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    label: str = Field(min_length=1, max_length=80)
    required: bool = True
    max_length: int = Field(default=2000, ge=1, le=8000)
    type: Literal["text", "number", "boolean", "choice"] = "text"
    choices: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def typed(self):
        if len(set(self.choices)) != len(self.choices) or any(not value or len(value) > self.max_length for value in self.choices):
            raise ValueError("Choose unique bounded argument choices")
        if (self.type == "choice") != bool(self.choices):
            raise ValueError("Only a choice argument has a nonempty choice list")
        return self


class Command(Strict):
    name: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    description: str = Field(min_length=1, max_length=200)
    arguments: list[Argument] = Field(default_factory=list, max_length=8)
    # Direct provider recipes are compiled to these bounded schemas, not scripts.
    protocol: Literal["signed-webhook", "github.issue", "jira.issue", "zendesk.ticket", "salesforce.case", "google.calendar-event", "microsoft.calendar-event"] = "signed-webhook"

    @model_validator(mode="after")
    def names(self):
        if len({row.name for row in self.arguments}) != len(self.arguments):
            raise ValueError("Argument names must be unique")
        expected = {"github.issue": {"title", "body"}, "jira.issue": {"project", "summary", "description"},
                    "zendesk.ticket": {"subject", "body"}, "salesforce.case": {"subject", "description"},
                    "google.calendar-event": {"title", "start", "end"}, "microsoft.calendar-event": {"title", "start", "end"}}
        if self.protocol in expected and {row.name for row in self.arguments} != expected[self.protocol]:
            raise ValueError("Use the exact provider recipe argument schema")
        return self


class Manifest(Strict):
    schema_version: int = Field(default=1, ge=1, le=1)
    app_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,63}$")
    name: str = Field(min_length=1, max_length=80)
    version: str = Field(pattern=r"^\d{1,4}\.\d{1,4}\.\d{1,4}$")
    description: str = Field(min_length=1, max_length=400)
    scopes: list[Literal["commands:invoke", "cards:write", "actions:write"]] = Field(min_length=1, max_length=3)
    commands: list[Command] = Field(default_factory=list, max_length=8)
    secure_foreground: bool = False

    @model_validator(mode="after")
    def coherent(self):
        if len(set(self.scopes)) != len(self.scopes) or len({row.name for row in self.commands}) != len(self.commands):
            raise ValueError("Scopes and command names must be unique")
        if self.commands and "commands:invoke" not in self.scopes:
            raise ValueError("Commands require commands:invoke")
        if any(row.protocol != "signed-webhook" for row in self.commands) and "actions:write" not in self.scopes:
            raise ValueError("Provider write actions require actions:write")
        if self.secure_foreground and "cards:write" in self.scopes:
            raise ValueError("Secure foreground apps cannot publish plaintext card callbacks")
        return self


class InstallInput(Strict):
    manifest: Manifest
    channel_ids: list[str] = Field(min_length=1, max_length=25)
    endpoint: str = Field(min_length=1, max_length=2048)
    authorization: str = Field(default="", max_length=4096)
    expected_generation: int = Field(default=0, ge=0)

    @field_validator("channel_ids")
    @classmethod
    def unique_channels(cls, values):
        if len(set(values)) != len(values) or any(not value or len(value) > 128 for value in values):
            raise ValueError("Select unique channels")
        return values

    @field_validator("authorization")
    @classmethod
    def auth_header(cls, value):
        if value and (not re.fullmatch(r"(?:Bearer|Basic) [A-Za-z0-9._~+/=-]{8,4000}", value)):
            raise ValueError("Use a Bearer token or Basic base64 authorization value")
        return value


class Invocation(Strict):
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,80}$")
    channel_id: str = Field(min_length=1, max_length=128)
    command: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    arguments: dict[str, ReviewedArgument] = Field(default_factory=dict)
    confirm: bool
    disclose_to_app: bool
    expected_generation: int = Field(ge=1)

    @model_validator(mode="before")
    @classmethod
    def reviewed_generation(cls, value):
        if isinstance(value, dict) and "expected_generation" not in value:
            raise ValueError("Reload and review the current installed command and recipient, then send its expected_generation")
        return value


class CardField(Strict):
    label: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=500)


class Card(Strict):
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(default="", max_length=4000)
    url: str | None = Field(default=None, max_length=2048)
    fields: list[CardField] = Field(default_factory=list, max_length=10)

    @field_validator("url")
    @classmethod
    def safe_link(cls, value):
        if value:
            # Declarative link only. No preview fetching, embeds, images, HTML,
            # event handlers or executable card action handlers exist.
            webhooks.normalize_url(value)
        return value


def catalog():
    def arg(name, required=True):
        return {"name": name, "label": name.replace("_", " ").title(), "required": required, "max_length": 2000}
    recipes = [
        ("signed-webhook", "Signed app / n8n", "Run your own app or n8n webhook, verified using an installation signing secret.",
         "invoke", [arg("text")], "https://YOUR-APP.example.com/freecollab", "Receive signed JSON; return 2xx. Publish cards through the signed callback API. No provider credentials required."),
        ("github.issue", "GitHub issues", "Create an issue in one explicitly configured repository.", "create-issue", [arg("title"), arg("body", False)],
         "https://api.github.com/repos/OWNER/REPOSITORY/issues", "Use your GitHub App installation token or fine-grained token: repository Issues write only. Renew expiring tokens in your own environment."),
        ("jira.issue", "Jira issues", "Create a Jira issue using your own Jira Cloud authorization.", "create-issue", [arg("project"), arg("summary"), arg("description", False)],
         "https://YOUR-TENANT.atlassian.net/rest/api/3/issue", "Use Basic base64(email:API-token), or a customer gateway with a scoped OAuth token. Project create-issue permission required; issue type Task is used."),
        ("zendesk.ticket", "Zendesk tickets", "Create a ticket in your own Zendesk tenant.", "create-ticket", [arg("subject"), arg("body")],
         "https://YOUR-TENANT.zendesk.com/api/v2/tickets.json", "Use Bearer OAuth token with tickets:write or Basic base64(email/token:API-token), owned and rotated by your organization."),
        ("salesforce.case", "Salesforce cases", "Create a Case in your own Salesforce organization.", "create-case", [arg("subject"), arg("description", False)],
         "https://YOUR-INSTANCE.my.salesforce.com/services/data/v61.0/sobjects/Case", "Use a customer-owned Connected App OAuth access token with API access and Case create permission. Your gateway owns token refresh; FreeCollab does not act as your SSO provider."),
        ("google.calendar-event", "Google Calendar", "Create an explicitly reviewed event in your own Google calendar.", "create-event", [arg("title"), arg("start"), arg("end")],
         "https://www.googleapis.com/calendar/v3/calendars/primary/events", "Install this recipe, then connect your personal Google account in Provider accounts. Your organization supplies its own OAuth registration. Enter start/end as ISO dates with an explicit timezone. No attendee invitations are sent."),
        ("microsoft.calendar-event", "Microsoft Calendar", "Create an explicitly reviewed event in your own Microsoft calendar.", "create-event", [arg("title"), arg("start"), arg("end")],
         "https://graph.microsoft.com/v1.0/me/events", "Install this recipe, then connect your personal Microsoft account in Provider accounts. Your organization supplies its own OAuth registration. Enter start/end as ISO dates with an explicit timezone. No attendee invitations are sent."),
    ]
    result = []
    for protocol, name, description, command, arguments, endpoint, setup in recipes:
        manifest = Manifest(app_id="freecollab." + protocol.replace(".", "-"), name=name, version="1.0.0", description=description,
            scopes=["commands:invoke", "cards:write"] if protocol == "signed-webhook" else ["commands:invoke", "actions:write"],
            commands=[Command(name=command, description=description, arguments=[Argument(**item) for item in arguments], protocol=protocol)])
        result.append({"id": protocol, "manifest": manifest.model_dump(), "endpoint_example": endpoint, "setup": setup,
                       "credential_required": protocol not in {"signed-webhook", "google.calendar-event", "microsoft.calendar-event"},
                       "oauth_provider": protocol.split(".")[0] if protocol.endswith(".calendar-event") else None, "status": "recipe"})
    return result


def manifest_for(row):
    return Manifest.model_validate_json(row.manifest_json)


def channels(row):
    return json.loads(row.channel_ids_json)


def native_channel(session, workspace_id, channel_id, user_id, *, allow_secure=False):
    require_workspace_member(session, workspace_id, user_id)
    actor = session.get(User, user_id, populate_existing=True)
    if not actor or actor.is_blocked:
        raise HTTPException(403, "Active channel membership required")
    mapping = session.get(WorkspaceChannel, channel_id)
    channel = session.get(Channel, channel_id)
    if not mapping or mapping.workspace_id != workspace_id or not channel or channel.type in {"direct", "group_direct"}:
        raise HTTPException(404, "Choose a native workspace channel")
    require_channel_member(session, channel_id, user_id)
    if is_secure_channel(session, channel_id) and not allow_secure:
        raise HTTPException(409, "Apps and recurring AI do not receive encrypted-channel plaintext. Use explicitly reviewed context in the trusted-device copilot.")


def install_grants(session, workspace_id, user_id, channel_ids, *, allow_secure=False):
    require_unrestricted(session)
    webhooks.require_grants(session, workspace_id, user_id, channel_ids)
    for channel_id in channel_ids:
        native_channel(session, workspace_id, channel_id, user_id, allow_secure=allow_secure)


def allowed_installation(session, row):
    if not row or not row.enabled or row.revoked_at:
        return False
    try:
        install_grants(session, row.workspace_id, row.granted_by, channels(row), allow_secure=manifest_for(row).secure_foreground)
        return True
    except HTTPException:
        return False


def installation_data(row):
    return {"id": row.id, "app_id": row.app_id, "bot_id": row.bot_id, "generation": row.generation,
            "manifest": json.loads(row.manifest_json), "channel_ids": channels(row), "origin": row.origin,
            "enabled": row.enabled, "revoked_at": row.revoked_at, "created_at": row.created_at, "updated_at": row.updated_at}


def job_data(row):
    return {key: getattr(row, key) for key in ("id", "installation_id", "channel_id", "actor_id", "command", "generation", "status", "attempts", "http_status", "failure", "created_at", "delivered_at", "next_attempt_at")}


def cancel_pending(session, row):
    session.execute(update(AppJob).where(AppJob.installation_id == row.id, AppJob.status.in_(["pending", "processing"]))
        .values(status="cancelled", failure="Installation revoked or changed", claim_token=None, encrypted_payload=""))


def lock_invoking_account(session, user_id):
    """Use the same actor-first order as account erasure and shared AI usage."""
    session.execute(update(User).where(User.id == user_id).values(name=User.name))
    session.execute(update(AccountSecurity).where(AccountSecurity.user_id == user_id)
        .values(auth_version=AccountSecurity.auth_version))


def save_installation(session, workspace_id, user_id, data, row=None):
    lock_invoking_account(session, user_id)
    lock_workspace(session, workspace_id)
    for channel_id in sorted(data.channel_ids):
        lock_channel_writes(session, channel_id)
    session.expire_all()
    recheck_actor(session, user_id)
    install_grants(session, workspace_id, user_id, data.channel_ids, allow_secure=data.manifest.secure_foreground)
    normalized, origin = webhooks.validate_destination(data.endpoint)
    # A provider token must never be attached to an arbitrary recipe URL.
    parts = urlsplit(normalized)
    protocols = {command.protocol for command in data.manifest.commands}
    if len(protocols) > 1:
        raise HTTPException(422, "One installation has one transport protocol")
    protocol = next(iter(protocols), "signed-webhook")
    oauth_endpoints = {"google.calendar-event": "https://www.googleapis.com/calendar/v3/calendars/primary/events",
        "microsoft.calendar-event": "https://graph.microsoft.com/v1.0/me/events"}
    if protocol in oauth_endpoints and (normalized != oauth_endpoints[protocol] or data.authorization):
        raise HTTPException(422, "Use this recipe's fixed calendar endpoint and connect your personal OAuth account separately")
    patterns = {
        "github.issue": r"/repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/issues",
        "jira.issue": r"/rest/api/3/issue", "zendesk.ticket": r"/api/v2/tickets\.json",
        "salesforce.case": r"/services/data/v\d{2}\.0/sobjects/Case",
    }
    if protocol in patterns and (not data.authorization or parts.query or not re.fullmatch(patterns[protocol], parts.path)):
        raise HTTPException(422, "Use the documented provider resource endpoint and your own authorization")
    if row:
        row = session.get(AppInstallation, row.id, populate_existing=True)
        if row.revoked_at or row.generation != data.expected_generation or row.app_id != data.manifest.app_id:
            raise HTTPException(409, "Installation changed; reload before granting a new version")
        install_grants(session, workspace_id, user_id, channels(row), allow_secure=manifest_for(row).secure_foreground)
        cancel_pending(session, row)
        row.generation += 1
    else:
        if data.expected_generation != 0:
            raise HTTPException(409, "A new installation starts at generation zero")
        count = session.exec(select(func.count()).select_from(AppInstallation).where(AppInstallation.workspace_id == workspace_id, AppInstallation.revoked_at.is_(None))).one()
        if count >= 30:
            raise HTTPException(409, "At most thirty active app installations per workspace")
        row = AppInstallation(workspace_id=workspace_id, app_id=data.manifest.app_id, granted_by=user_id,
            channel_ids_json="[]", manifest_json="{}", encrypted_config="", encrypted_secret="", origin=origin)
    secret = secrets.token_urlsafe(32)
    row.manifest_json = data.manifest.model_dump_json()
    row.channel_ids_json = dump(data.channel_ids)
    row.origin, row.granted_by, row.enabled, row.updated_at = origin, user_id, True, datetime.utcnow()
    row.encrypted_config = seal_private(dump({"endpoint": normalized, "authorization": data.authorization}), "app-config", row.id)
    row.encrypted_secret = seal_private(secret, "app-signing", row.id)
    session.add(row)
    session.flush()
    session.add(AppRevision(installation_id=row.id, generation=row.generation, manifest_json=row.manifest_json,
        channel_ids_json=row.channel_ids_json, granted_by=user_id))
    return row, secret


def arguments_for(command, arguments):
    if not isinstance(arguments, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in arguments.items()):
        raise HTTPException(422, "Installed command arguments must be an object of text fields")
    definitions = {field.name: field for field in command.arguments}
    if set(arguments) - definitions.keys() or len(arguments) > 8:
        raise HTTPException(422, "Arguments must match the installed command schema")
    for name, field in definitions.items():
        value = arguments.get(name, "")
        if not isinstance(value, str) or len(value) > field.max_length or "\x00" in value or field.required and not value.strip():
            raise HTTPException(422, "An installed command argument is missing or too long")
        if value and field.type == "number":
            from decimal import Decimal, InvalidOperation
            import math
            try:
                if (not re.fullmatch(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?", value)
                        or not Decimal(value).is_finite() or not math.isfinite(float(value))): raise ValueError()
            except (InvalidOperation, ValueError):
                raise HTTPException(422, "Enter a finite number for this installed argument") from None
        if value and field.type == "boolean" and value not in {"true", "false"}:
            raise HTTPException(422, "Use true or false for this installed argument")
        if value and field.type == "choice" and value not in field.choices:
            raise HTTPException(422, "Choose an installed argument value")
    result = {name: arguments.get(name, "") for name in definitions}
    if command.protocol in {"google.calendar-event", "microsoft.calendar-event"}:
        try:
            from datetime import timezone
            start, end = (datetime.fromisoformat(result[key].replace("Z", "+00:00")) for key in ("start", "end"))
            if start.tzinfo is None or end.tzinfo is None or not timedelta(0) < end - start <= timedelta(days=7):
                raise ValueError()
            result["start"], result["end"] = start.astimezone(timezone.utc).isoformat(), end.astimezone(timezone.utc).isoformat()
        except (ValueError, TypeError):
            raise HTTPException(422, "Enter start and end with an explicit timezone and a duration of at most seven days") from None
    return result


def queue_invocation(session, row, actor_id, data):
    if not data.confirm or not data.disclose_to_app:
        raise HTTPException(422, "Review and explicitly approve the exact arguments shared with this app")
    if data.expected_generation != row.generation:
        raise HTTPException(409, "The installed app changed; review its current command and recipient")
    native_channel(session, row.workspace_id, data.channel_id, actor_id)
    if not allowed_installation(session, row) or data.channel_id not in channels(row):
        raise HTTPException(403, "App is not currently granted to this channel")
    manifest = manifest_for(row)
    command = next((value for value in manifest.commands if value.name == data.command), None)
    if not command or "commands:invoke" not in manifest.scopes:
        raise HTTPException(404, "Command is not installed")
    args = arguments_for(command, data.arguments)
    provider_grant_id = None
    if command.protocol in {"google.calendar-event", "microsoft.calendar-event"}:
        from . import personal_oauth
        grant = personal_oauth.grant_for_target(session, "calendar-action", row.id, actor_id)
        if not grant:
            raise HTTPException(409, "Connect your own provider account for this installed calendar app first")
        personal_oauth.current_grant(session, grant.id, user_id=actor_id)
        provider_grant_id = grant.id
    from .enterprise_governance import enforce_native_content
    enforce_native_content(session, data.channel_id, actor_id, dump(args), surface="app")
    identity = [row.generation, actor_id, data.channel_id, data.command, args]
    if provider_grant_id:
        identity.append(provider_grant_id)
    digest = hashlib.sha256(dump(identity).encode()).hexdigest()
    previous = session.exec(select(AppJob).where(AppJob.installation_id == row.id, AppJob.request_id == data.request_id)).first()
    if previous:
        if previous.request_digest != digest:
            raise HTTPException(409, "Request identifier was used for different arguments or authorization")
        return previous
    pending = session.exec(select(func.count()).select_from(AppJob).where(AppJob.installation_id == row.id,
        AppJob.status.in_(["pending", "processing"]))).one()
    if pending >= 100:
        raise HTTPException(429, "App queue is full")
    job = AppJob(installation_id=row.id, generation=row.generation, channel_id=data.channel_id, actor_id=actor_id,
        command=data.command, request_id=data.request_id, request_digest=digest, encrypted_payload="")
    payload = {"schema_version": 1, "id": job.id, "type": "app.command", "installation_id": row.id,
               "generation": row.generation, "workspace_id": row.workspace_id, "channel_id": data.channel_id,
               "actor_id": actor_id, "bot_id": row.bot_id, "command": command.name, "arguments": args}
    if provider_grant_id:
        payload["provider_grant_id"] = provider_grant_id
    job.encrypted_payload = seal_private(dump(payload), "app-job", job.id)
    session.add(job)
    return job


def provider_payload(protocol, payload):
    args = payload["arguments"]
    if protocol == "signed-webhook":
        return payload
    if protocol == "github.issue":
        return {"title": args["title"], "body": args["body"]}
    if protocol == "jira.issue":
        return {"fields": {"project": {"key": args["project"]}, "summary": args["summary"], "issuetype": {"name": "Task"},
            "description": {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": args["description"] or "Created from FreeCollab"}]}]}}}
    if protocol == "zendesk.ticket":
        return {"ticket": {"subject": args["subject"], "comment": {"body": args["body"]}}}
    if protocol == "google.calendar-event":
        # A stable client identifier makes manual outcome inspection practical;
        # the worker still never blindly retries an ambiguous create request.
        return {"id": hashlib.sha256(payload["id"].encode()).hexdigest(), "summary": args["title"],
            "start": {"dateTime": args["start"]}, "end": {"dateTime": args["end"]}}
    if protocol == "microsoft.calendar-event":
        return {"subject": args["title"], "transactionId": payload["id"],
            "start": {"dateTime": args["start"].replace("+00:00", ""), "timeZone": "UTC"},
            "end": {"dateTime": args["end"].replace("+00:00", ""), "timeZone": "UTC"}}
    return {"Subject": args["subject"], "Description": args["description"]}


def run_pending(session, limit=2, send=None):
    """At-least-once, leased delivery. Consumers dedupe the stable event ID.

Provider writes are NOT automatically retried after ambiguous network failures:
GitHub/Jira/Zendesk/Salesforce create endpoints have no universal idempotency
contract. Signed app receivers may retry with the stable request identifier.
"""
    now = datetime.utcnow()
    due = or_(AppJob.status == "pending", and_(AppJob.status == "processing", AppJob.claimed_at < now-timedelta(seconds=LEASE_SECONDS)))
    candidates = session.exec(select(AppJob.id, AppJob.status).where(due, AppJob.next_attempt_at <= now).order_by(AppJob.created_at).limit(min(max(limit, 1), 20))).all()
    completed = 0
    for identifier, previous_status in candidates:
        claim = secrets.token_hex(16)
        changed = session.execute(update(AppJob).where(AppJob.id == identifier, due, AppJob.next_attempt_at <= now)
            .values(status="processing", claimed_at=datetime.utcnow(), claim_token=claim, attempts=AppJob.attempts+1))
        session.commit()
        if changed.rowcount != 1:
            continue
        session.expire_all()
        job = session.get(AppJob, identifier)
        row = session.get(AppInstallation, job.installation_id)
        if row:
            lock_workspace(session, row.workspace_id)
            lock_channel_writes(session, job.channel_id)
            session.expire_all()
            job = session.get(AppJob, identifier)
            row = session.get(AppInstallation, job.installation_id)
        if job.claim_token != claim:
            session.rollback()
            continue
        status, failure, code, retry_after = "cancelled", "Authorization or configuration changed", None, None
        allowed = row and row.generation == job.generation and allowed_installation(session, row) and job.channel_id in channels(row)
        try:
            if allowed:
                native_channel(session, row.workspace_id, job.channel_id, job.actor_id)
        except HTTPException:
            allowed = False
        if allowed:
            protocol = next(value.protocol for value in manifest_for(row).commands if value.name == job.command)
            try:
                if previous_status == "processing" and protocol != "signed-webhook":
                    raise ValueError("An interrupted provider write has an unknown outcome")
                config = json.loads(unseal_private(row.encrypted_config, "app-config", row.id))
                secret = unseal_private(row.encrypted_secret, "app-signing", row.id)
                raw = json.loads(unseal_private(job.encrypted_payload, "app-job", job.id))
                from .enterprise_governance import enforce_native_content
                enforce_native_content(session, job.channel_id, job.actor_id, dump(raw["arguments"]), surface="app")
                body = dump(provider_payload(protocol, raw)).encode()
                headers = webhooks.signature_headers(secret, body, job.id, "app.command")
                headers["Idempotency-Key"] = job.id
                if protocol in {"google.calendar-event", "microsoft.calendar-event"}:
                    from . import personal_oauth
                    # Approval binds an immutable consent, not whichever
                    # provider account this person connects most recently.
                    # Token rotation preserves the grant ID; new consent does
                    # not transfer already-approved external write effects.
                    provider_grant_id = raw.get("provider_grant_id")
                    if not isinstance(provider_grant_id, str):
                        raise HTTPException(409, "Review this action under a current personal provider consent")
                    grant, _ = personal_oauth.current_grant(session, provider_grant_id, user_id=job.actor_id)
                    if (grant.purpose != "calendar-action" or grant.target_id != row.id
                            or grant.workspace_id != row.workspace_id or protocol != personal_oauth.PROTOCOLS.get(grant.provider)):
                        raise HTTPException(403, "This action belongs to another provider delegation")
                    headers["Authorization"] = "Bearer " + personal_oauth.access_token(session, grant.id, user_id=job.actor_id)
                    # Refresh can release SQL locks. Reacquire the installation
                    # and channel fences before disclosing any reviewed arguments.
                    lock_workspace(session, row.workspace_id)
                    lock_channel_writes(session, job.channel_id)
                    session.expire_all()
                    row = session.get(AppInstallation, job.installation_id)
                    job = session.get(AppJob, identifier)
                    if (not row or not job or job.claim_token != claim or row.generation != job.generation
                            or not allowed_installation(session, row) or job.channel_id not in channels(row)):
                        raise HTTPException(403, "The installed provider grant changed")
                    native_channel(session, row.workspace_id, job.channel_id, job.actor_id)
                    personal_oauth.current_grant(session, grant.id, user_id=job.actor_id)
                elif config["authorization"]:
                    headers["Authorization"] = config["authorization"]
                if protocol == "github.issue":
                    headers.update({"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
                code, retry_after = (send or webhooks.post_webhook)(config["endpoint"], body, headers)
            except HTTPException:
                status, failure = "cancelled", "Authorization or native content policy changed"
            except Exception:
                status = "pending" if protocol == "signed-webhook" and job.attempts < MAX_ATTEMPTS else "uncertain"
                failure = "Delivery outcome unknown; inspect the receiving system before retrying"
            else:
                if 200 <= code < 300:
                    status, failure = "delivered", None
                elif code == 429 or protocol == "signed-webhook" and (code in {408, 425} or code >= 500):
                    status = "pending" if job.attempts < MAX_ATTEMPTS else "failed"
                    failure = "Receiver temporarily unavailable"
                elif code >= 500:
                    status, failure = "uncertain", "Provider outcome unknown; inspect the receiving system before retrying"
                else:
                    status, failure = "failed", "Receiver rejected delivery"
        values = {"status": status, "failure": failure, "http_status": code, "claim_token": None,
                  "delivered_at": datetime.utcnow() if status == "delivered" else None,
                  "next_attempt_at": datetime.utcnow()+timedelta(seconds=webhooks.retry_delay(job.attempts, retry_after))}
        # Keep failed arguments encrypted for an explicit review/retry, but erase
        # terminal delivered/cancelled request content from this outbox.
        if status in {"delivered", "cancelled"}:
            values["encrypted_payload"] = ""
        changed = session.execute(update(AppJob).where(AppJob.id == identifier, AppJob.claim_token == claim).values(**values))
        session.commit()
        completed += int(status == "delivered" and changed.rowcount == 1)
    return completed


def add_card(session, workspace_id, channel_id, bot_id, bot_name, card, actor_id, installation_id=None, citations=None):
    from .enterprise_governance import enforce_native_content
    enforce_native_content(session, channel_id, actor_id, card.model_dump_json(), surface="app")
    row = AppCard(workspace_id=workspace_id, channel_id=channel_id, bot_id=bot_id, bot_name=bot_name,
                  installation_id=installation_id, encrypted_card="", citations_json=dump(citations or []))
    row.encrypted_card = seal_private(card.model_dump_json(), "app-card", row.id)
    session.add(row)
    return row


def card_data(session, row, viewer_id):
    from .enterprise_governance import enforce_native_content
    card = Card.model_validate_json(unseal_private(row.encrypted_card, "app-card", row.id))
    enforce_native_content(session, row.channel_id, viewer_id, card.model_dump_json(), surface="app")
    refs = json.loads(row.citations_json)
    if refs:
        copilot.check_sources(session, SimpleNamespace(channel_id=row.channel_id, secure=False), refs, viewer_id)
    return {"id": row.id, "bot_id": row.bot_id, "bot_name": row.bot_name, "created_at": row.created_at,
            "card": card.model_dump(), "citations": refs}


class AIInput(Strict):
    expected_config_revision: int = Field(ge=1, strict=True)
    kind: Literal["summary", "workflow", "actions"] = "summary"
    prompt: str = Field(default="Summarize decisions, open questions and next steps.", min_length=1, max_length=2000)
    sources: list[copilot.Source] = Field(default_factory=list, max_length=8)
    installation_ids: list[str] = Field(default_factory=list, max_length=3)
    # Explicit recipients only; AI cannot discover a directory or project.
    recipient_ids: list[str] = Field(default_factory=list, max_length=20)
    disclose_to_provider: bool


def ai_context(session, workspace_id, channel_id, owner_id):
    native_channel(session, workspace_id, channel_id, owner_id)
    config = copilot.configuration(session, workspace_id)
    copilot.consent_for(session, workspace_id, owner_id, config.revision)
    return config


def generate_draft(session, workspace_id, channel_id, owner_id, data, complete=None):
    # Match the common provider budget/account-erasure lock order before an
    # AIUsage row takes its invoking-account foreign-key reference.
    copilot.grant(session, workspace_id, channel_id, owner_id, lock=True)
    config = ai_context(session, workspace_id, channel_id, owner_id)
    if data.expected_config_revision != config.revision:
        raise HTTPException(409, "Provider configuration changed; review the current destination before sending")
    count = session.exec(select(func.count()).select_from(AppAIDraft).where(AppAIDraft.channel_id == channel_id,
        AppAIDraft.owner_id == owner_id)).one()
    if count >= 200:
        raise HTTPException(409, "Remove older private drafts before creating more than two hundred in this channel")
    if not data.disclose_to_provider:
        raise HTTPException(422, "Approve disclosure of the selected context to your workspace AI provider")
    if data.kind == "summary" and not data.sources:
        raise HTTPException(422, "Choose at least one source to summarize")
    if len({(source.kind, source.id) for source in data.sources}) != len(data.sources):
        raise HTTPException(422, "Select each source once")
    context = SimpleNamespace(channel_id=channel_id, secure=False)
    refs, pieces = [], []
    for index, source in enumerate(data.sources):
        ref, content = copilot.source_text(session, context, source, owner_id)
        ref["citation"] = "S" + str(index+1)
        refs.append(ref)
        pieces.append("[" + ref["citation"] + "] " + content)
    selected_context = "\n\n".join(pieces)
    if len(selected_context) > copilot.MAX_CONTEXT:
        raise HTTPException(413, "Select at most 24,000 context characters")
    app_specs = []
    for identifier in data.installation_ids:
        row = session.get(AppInstallation, identifier, populate_existing=True)
        if not row or row.workspace_id != workspace_id or channel_id not in channels(row) or not allowed_installation(session, row):
            raise HTTPException(403, "Choose currently granted channel apps")
        app_specs.append({"installation_id": row.id, "generation": row.generation,
                          "commands": [command.model_dump() for command in manifest_for(row).commands]})
    for recipient in data.recipient_ids:
        require_channel_member(session, channel_id, recipient)
    from .enterprise_governance import enforce_native_content
    enforce_native_content(session, channel_id, owner_id, data.prompt+"\n"+"\n".join(pieces), surface="assistant")
    instructions = copilot.SYSTEM
    if data.kind == "workflow":
        workflow.require_channel(session, workspace_id, channel_id, owner_id, admin=True)
        if not data.recipient_ids:
            raise HTTPException(422, "Choose at least one current channel recipient for the proposed workflow")
        instructions += (' Return ONLY a JSON object matching this workflow schema: ' + dump(workflow.DefinitionFields.model_json_schema()) +
            '. Use manual trigger. Only notify/approval steps. No project/task fields. Recipients may only be from: ' + dump(data.recipient_ids))
    elif data.kind == "actions":
        if not app_specs:
            raise HTTPException(422, "Select an installed app before proposing cross-app actions")
        instructions += (' Return ONLY JSON {"actions":[{"installation_id":"...","command":"...","arguments":{"field":"value"}}]}.'
                         ' Propose one to three actions from this exact catalog, never execute them: ' + dump(app_specs))
    provider, revision = copilot.config_fields(config), config.revision
    draft_id = secrets.token_hex(16)
    messages = [{"role": "system", "content": instructions}, {"role": "user", "content": data.prompt + "\nSELECTED UNTRUSTED CONTEXT:\n" + selected_context}]
    # Legacy app drafts and recurring recaps share the same reservations and
    # usage timeline as foreground/durable Copilot, rather than bypass budgets.
    from . import ai_runtime
    ledger_id, runtime_provider = ai_runtime.reserve_external_call(session, workspace_id, channel_id, owner_id,
        "app-draft:" + draft_id, provider, messages, expected_revision=revision, max_tokens=2048)
    session.commit()
    try:
        answer = (complete or copilot.completion)(provider, {"model": provider.model, "stream": True, "max_tokens": 2048, "n": 1, "messages": messages})
    except Exception:
        ai_runtime.settle(session, ledger_id, runtime_provider, sent=True); session.commit()
        raise
    ai_runtime.settle(session, ledger_id, runtime_provider, answer.get("usage")); session.commit()
    lock_workspace(session, workspace_id)
    lock_channel_writes(session, channel_id)
    session.expire_all()
    recheck_actor(session, owner_id)
    if ai_context(session, workspace_id, channel_id, owner_id).revision != revision:
        raise HTTPException(409, "AI provider or consent changed while creating this draft")
    copilot.check_sources(session, context, refs, owner_id)
    if answer.get("tool_calls"):
        raise HTTPException(502, "AI returned an unrequested tool; no action was executed")
    result = {"text": answer["text"], "citations": refs}
    enforce_native_content(session, channel_id, owner_id, answer["text"], surface="assistant")
    if data.kind == "summary":
        cited = re.findall(r"\[S(\d+)\]", answer["text"])
        if len(answer["text"]) > 4000 or not cited or any(not 1 <= int(marker) <= len(refs) for marker in cited):
            raise HTTPException(502, "AI summary must cite the selected sources")
    else:
        try:
            proposed = json.loads(answer["text"], object_pairs_hook=copilot.transport._pairs, parse_constant=copilot.transport._constant)
            if data.kind == "workflow":
                fields = workflow.DefinitionFields.model_validate(proposed)
                if fields.trigger != "manual" or any(step.kind not in {"notify", "approval"} or set(step.recipients + step.escalation_recipients)-set(data.recipient_ids) for step in fields.steps):
                    raise ValueError("Destination changed")
                workflow.validate_fields(session, workspace_id, channel_id, fields)
                result["fields"] = fields.model_dump(mode="json")
            else:
                if not isinstance(proposed, dict) or set(proposed) != {"actions"} or not isinstance(proposed["actions"], list) or not 1 <= len(proposed["actions"]) <= 3:
                    raise ValueError("Invalid action list")
                specs = {spec["installation_id"]: spec for spec in app_specs}
                safe = []
                for action in proposed["actions"]:
                    if not isinstance(action, dict) or set(action) != {"installation_id", "command", "arguments"}:
                        raise ValueError("Invalid action")
                    spec = specs[action["installation_id"]]
                    command = next(Command(**item) for item in spec["commands"] if item["name"] == action["command"])
                    try:
                        arguments = arguments_for(command, action["arguments"])
                    except HTTPException:
                        raise ValueError("Invalid proposed arguments") from None
                    safe.append({**action, "arguments": arguments, "generation": spec["generation"]})
                result["actions"] = safe
        except (ValueError, TypeError, KeyError, StopIteration):
            raise HTTPException(502, "AI proposal exceeded the reviewed schema or destination; no action was executed") from None
    row = AppAIDraft(id=draft_id, workspace_id=workspace_id, channel_id=channel_id, owner_id=owner_id, kind=data.kind,
        provider_revision=revision, encrypted_result="")
    row.encrypted_result = seal_private(dump(result), "app-ai-draft", row.id)
    session.add(row)
    copilot.audit(session, workspace_id, owner_id, "automation_draft", len(refs))
    return row


def draft_result(session, row, owner_id):
    if row.owner_id != owner_id:
        raise HTTPException(404, "Private AI draft not found")
    config = ai_context(session, row.workspace_id, row.channel_id, owner_id)
    if config.revision != row.provider_revision:
        raise HTTPException(409, "AI draft belongs to an earlier provider or consent revision")
    result = json.loads(unseal_private(row.encrypted_result, "app-ai-draft", row.id))
    copilot.check_sources(session, SimpleNamespace(channel_id=row.channel_id, secure=False), result["citations"], owner_id)
    from .enterprise_governance import enforce_native_content
    enforce_native_content(session, row.channel_id, owner_id, result["text"], surface="assistant")
    return {"id": row.id, "kind": row.kind, "status": row.status, "created_at": row.created_at, "resource_id": row.resource_id, **result}


def run_recaps(session, limit=1, complete=None):
    """Bounded private previews; consent is checked at every provider request.

    A five-minute claim fences the bounded thirty-second provider request.
    Publication always needs a human; interrupted claims become eligible again.
"""
    now = datetime.utcnow()
    ids = session.exec(select(ChannelRecapSchedule.id).where(ChannelRecapSchedule.enabled.is_(True),
        ChannelRecapSchedule.next_run_at <= now).order_by(ChannelRecapSchedule.next_run_at).limit(min(max(limit, 1), 5))).all()
    made = 0
    for identifier in ids:
        claimed = session.execute(update(ChannelRecapSchedule).where(ChannelRecapSchedule.id == identifier,
            ChannelRecapSchedule.enabled.is_(True), ChannelRecapSchedule.next_run_at <= now).values(next_run_at=now+timedelta(minutes=5)))
        session.commit()
        if claimed.rowcount != 1:
            continue
        row = session.get(ChannelRecapSchedule, identifier, populate_existing=True)
        generation = row.generation
        try:
            config = ai_context(session, row.workspace_id, row.channel_id, row.owner_id)
            install_grants(session, row.workspace_id, row.owner_id, [row.channel_id])
            if config.revision != row.provider_revision:
                raise HTTPException(409, "Provider changed")
            # last_run_at is the disclosure/check cursor, initialized when the
            # user grants this future-only schedule. Legacy rows without a
            # cursor start now; absence never authorizes historical backfill.
            since = row.last_run_at or now
            messages = session.exec(select(Message).where(Message.channel_id == row.channel_id, Message.deleted.is_(False),
                Message.created_at > since).order_by(Message.created_at.desc(), Message.id).limit(8)).all()
            if messages:
                draft = generate_draft(session, row.workspace_id, row.channel_id, row.owner_id,
                    AIInput(expected_config_revision=row.provider_revision, sources=[copilot.Source(kind="message", id=message.id) for message in reversed(messages)], disclose_to_provider=True), complete=complete)
                session.flush()
                status = "draft_ready"
            else:
                status = "no_messages"
        except Exception:
            session.rollback()
            status = "paused_authorization_or_provider_failure"
        row = session.get(ChannelRecapSchedule, identifier, populate_existing=True)
        if not row or row.generation != generation or not row.enabled:
            session.rollback()
            continue
        if row and row.generation == generation:
            row.last_status, row.last_run_at = status, now
            row.next_run_at = now+timedelta(hours=row.interval_hours)
            if status.startswith("paused"):
                row.enabled = False
            session.add(row)
        session.commit()
        made += int(status == "draft_ready")
    return made


def close_account_resources(session, user_id):
    """Stop delegated authority and erase this account's private copies only.

    Published channel cards, manifest revisions and routing/status provenance
    remain shared records under the retained authorship stub.
    """
    from .enterprise_governance import assert_deletion_allowed
    from .personal_oauth import close_account_resources as close_provider_grants
    close_provider_grants(session, user_id)
    drafts = session.exec(select(AppAIDraft).where(AppAIDraft.owner_id == user_id)).all()
    jobs = session.exec(select(AppJob).where(AppJob.actor_id == user_id, AppJob.encrypted_payload != "")).all()
    for channel_id in sorted({row.channel_id for row in drafts+jobs}):
        lock_channel_writes(session, channel_id)
        assert_deletion_allowed(session, channel_id, author_id=user_id)
    rows = session.exec(select(AppInstallation).where(AppInstallation.granted_by == user_id,
        AppInstallation.revoked_at.is_(None))).all()
    for row in rows:
        row.enabled, row.revoked_at, row.generation = False, datetime.utcnow(), row.generation+1
        row.encrypted_config = row.encrypted_secret = ""
        cancel_pending(session, row)
        session.add(row)
    for job in jobs:
        if job.status in {"pending", "processing", "failed", "uncertain"}:
            job.status, job.failure, job.claim_token = "cancelled", "Invoking account erased", None
        job.encrypted_payload = ""
        session.add(job)
    session.execute(delete(ChannelRecapSchedule).where(ChannelRecapSchedule.owner_id == user_id))
    session.execute(delete(AppAIDraft).where(AppAIDraft.owner_id == user_id))


def export_private_drafts(session, workspace_id, channel_ids, requester_id):
    rows = session.exec(select(AppAIDraft).where(AppAIDraft.workspace_id == workspace_id,
        AppAIDraft.channel_id.in_(channel_ids), AppAIDraft.owner_id == requester_id)
        .order_by(AppAIDraft.created_at, AppAIDraft.id).limit(1001)).all()
    if len(rows) > 1000:
        raise HTTPException(413, "Select fewer channels to export at most one thousand private AI drafts")
    exported = []
    for row in rows:
        try:
            result = draft_result(session, row, requester_id)
        except HTTPException:
            result = {"id": row.id, "kind": row.kind, "created_at": row.created_at, "status": "source_or_consent_changed"}
        exported.append({"channel_id": row.channel_id, **result})
    return exported
