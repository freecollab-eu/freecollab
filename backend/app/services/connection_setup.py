"""Read-only customer setup recipes; never decrypt or contact a provider.

Queries select public routing/grant metadata and booleans for sealed material.
Opening the center creates no account-security, directory or preference rows.
"""
from datetime import datetime, timedelta
import json
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit
from fastapi import HTTPException
from sqlmodel import select
from ..config import settings
from ..models.channel import Channel
from ..models.cloud_files import CloudFileConnection
from ..models.copilot import CopilotConfig
from ..models.external_meetings import ExternalMeetingConnection
from ..models.remote_sync import RemoteSyncConnection
from ..models.scim import SCIMDirectory
from ..models.secure_channel import SecureChannel
from ..models.user import User
from ..models.webhook import WebhookDelivery, WebhookEndpoint
from ..models.workspace import WorkspaceChannel, WorkspaceMember
from ..models.workspace_oidc import WorkspaceOIDCProvider
from .access import require_channel_member
from .request_guard import recheck_actor
from .sso_scope import require_unrestricted
from .workforce_access import require_personal_account
from .workspaces import require_direct_message_writable, require_workspace_admin

LOCAL_NOTE = "Ready describes saved local configuration, not a live provider connectivity guarantee. Tests and external actions run only when you explicitly start them."
CHANNEL_REQUIRED = "Choose a channel you currently administer."
NATIVE_REQUIRED = "Choose a native channel for this connection. Encrypted content stays on trusted devices."
GRANT_REQUIRED = "An authorizing administrator no longer has the required workspace and channel grants."
RECONNECT_CALENDAR = "Reconnect the selected calendar OAuth grant after its consent or channel access changes."
OAUTH_REQUIRED = "Review the provider consent screen before using this connection."
VERIFY_REQUIRED = "Run the existing explicit provider test for the saved revision."
PAUSED = "Resume this saved connection explicitly when ready."
FLAG_REQUIRED = "An operator must enable this feature on this installation."
ORIGIN_REQUIRED = "Configure a valid public HTTPS application origin before starting OAuth."
SSO_HTTP_REQUIRED = "Workspace SSO allows HTTP loopback callbacks only in the test environment."
OWNER_REQUIRED = "A current workspace owner must configure this connection."
AI_REVIEW = "Review the saved AI provider, model and your explicit consent in the AI panel. Connectivity is checked only by an explicit request."
SAVE = "Save the customer application with your FreeCollab password and MFA or recovery confirmation."
CALLBACK = "Register the exact callback URL shown here in the customer application's redirect allowlist."
CONSENT = "Choose Connect and review the provider's own consent screen. Opening this recipe never authorizes an account."
TEST_SYNC = "Test the saved connection, then explicitly enable two-way synchronization if intended."
SYNC_WARNING = "Enabling synchronization shares existing and future channel content with the selected collection; changes and deletions can travel both ways."
FILE_ROOT = "A selected provider folder and current workspace and channel administrator access."
CAL_ROOT = "A selected provider calendar and current workspace and channel administrator access."
CLIENT = "A customer-owned OAuth application client ID and its secret when required by the provider."


def recipe(identifier, name, category, description, target, provider, kind=None, *, flag=None,
           channel=False, secure=True, callback=None, scopes=(), docs=None, prerequisites=(), steps=()):
    return {"id": identifier, "name": name, "category": category, "description": description,
        "target": target, "provider": provider, "kind": kind, "enabled": True if flag is None else bool(getattr(settings, flag, False)),
        "requires_channel": channel, "secure_supported": secure, "can_configure": False, "callback_url": None, "scopes": list(scopes), "docs_url": docs,
        "prerequisites": list(prerequisites) + [LOCAL_NOTE], "steps": list(steps), "status": "not_configured", "blockers": [], "connections": [], "_callback": callback}


def recipes():
    result = [recipe("sso.oidc", "Organization sign-in", "identity", "Connect this workspace to its own OIDC identity provider; personal FreeCollab sign-in remains separate.", "sso", "oidc",
        callback="/api/auth/oidc/callback", scopes=("openid", "profile", "email"), docs="https://openid.net/specs/openid-connect-core-1_0.html",
        prerequisites=("Your organization's HTTPS OIDC issuer, client ID and client secret or public PKCE client.",),
        steps=("Select a provider preset or enter your organization's issuer. A preset never creates a central FreeCollab identity provider.", CALLBACK, "Match the requested OIDC scopes in the customer application.", SAVE,
               "Test the saved provider, then explicitly enable workspace sign-in and share its workspace-specific sign-in link.")),
        recipe("directory.scim", "Directory provisioning", "identity", "Provision organization accounts and explicitly mapped workspace or channel grants from your own SCIM directory.", "people", "scim",
        docs="https://www.rfc-editor.org/rfc/rfc7644", prerequisites=("A current workspace owner must issue and securely store the provisioning token.",),
        steps=("Open People and copy this workspace's SCIM base URL.", "Create the provisioning token with owner password and MFA confirmation, then save it in your identity provider's secure provisioning settings.",
               "Confirm the token is stored, choose group mappings deliberately, and run the identity provider's explicit provisioning test.", "Provisioning and organization sign-in are separate; configure the workspace's own OIDC provider for organization login."))]
    for provider, name, scopes, docs in (
        ("google", "Google Drive", ("https://www.googleapis.com/auth/drive",), "https://developers.google.com/drive/api/guides/api-specific-auth"),
        ("microsoft", "OneDrive / SharePoint", ("offline_access", "Files.ReadWrite.All"), "https://learn.microsoft.com/graph/permissions-reference#filesreadwriteall")):
        extra = ("For Microsoft, supply the customer tenant ID, drive ID and selected folder ID.",) if provider == "microsoft" else ("Enable the Google Drive API in the customer application project.",)
        result.append(recipe("files." + provider, name, "files", "Prepare two-way native file synchronization. The file picker reuses this broader read/write grant for the same channel.", "sync", provider, "files", flag="sync_enabled", channel=True, secure=False,
            callback="/api/sync/oauth/callback", scopes=scopes, docs=docs, prerequisites=(CLIENT, FILE_ROOT, *extra),
            steps=(CALLBACK, "Set the exact requested provider scopes; this is a two-way read/write grant, not a read-only picker grant.", SAVE, CONSENT, TEST_SYNC, SYNC_WARNING)))
    result.append(recipe("files.nextcloud", "Nextcloud files", "files", "Prepare two-way native file synchronization with a customer-owned WebDAV folder.", "sync", "nextcloud", "files", flag="sync_enabled", channel=True, secure=False,
        docs="https://docs.nextcloud.com/server/latest/user_manual/en/files/access_webdav.html", prerequisites=(FILE_ROOT, "A public HTTPS WebDAV folder URL, username and a dedicated application password."),
        steps=("Copy the WebDAV folder URL from your Nextcloud account's Files settings.", SAVE, TEST_SYNC, SYNC_WARNING)))
    for provider, name, scopes, docs, prerequisite in (
        ("dropbox", "Dropbox picker", ("files.metadata.read", "files.content.read"), "https://developers.dropbox.com/oauth-guide", "Enable files.metadata.read and files.content.read in the Dropbox app; select its permitted root folder path."),
        ("box", "Box picker", (), "https://developer.box.com/guides/authentication/oauth2/", "Configure read access to files and folders in the Box application; supply its client secret and numeric root folder ID. Box scopes are configured in the customer application, not an OAuth scope parameter.")):
        result.append(recipe("files." + provider, name, "files", "Browse and explicitly import a selected native file revision with a read-only customer provider connection.", "cloud_files", provider, "files", flag="cloud_files_enabled", channel=True, secure=False,
            callback="/api/cloud-files/oauth/callback", scopes=scopes, docs=docs, prerequisites=(CLIENT, FILE_ROOT, prerequisite, "Encrypted channels support explicit encrypted provider links only; this OAuth picker does not proxy or import plaintext there."),
            steps=(CALLBACK, SAVE, CONSENT, "Browse the permitted folder, select a file, and confirm copying that exact revision into the channel. The picker never enables background sync.", "The imported copy follows any already approved synchronization rules for this channel.")))
    for provider, name, scopes, docs in (
        ("google", "Google Calendar", ("https://www.googleapis.com/auth/calendar.events",), "https://developers.google.com/calendar/api/auth"),
        ("microsoft", "Microsoft Calendar", ("offline_access", "Calendars.ReadWrite"), "https://learn.microsoft.com/graph/permissions-reference#calendarsreadwrite")):
        result.append(recipe("calendar." + provider, name, "calendar", "Prepare two-way native calendar synchronization with the selected provider calendar.", "sync", provider, "calendar", flag="sync_enabled", channel=True, secure=False,
            callback="/api/sync/oauth/callback", scopes=scopes, docs=docs, prerequisites=(CLIENT, CAL_ROOT, "Microsoft connections also require the customer tenant ID." if provider == "microsoft" else "Enable the Google Calendar API in the customer application project."),
            steps=(CALLBACK, SAVE, CONSENT, TEST_SYNC, SYNC_WARNING)))
    result.append(recipe("calendar.caldav", "CalDAV calendar", "calendar", "Prepare two-way native calendar synchronization with a customer-owned CalDAV collection.", "sync", "caldav", "calendar", flag="sync_enabled", channel=True, secure=False,
        docs="https://www.rfc-editor.org/rfc/rfc4791", prerequisites=(CAL_ROOT, "A public HTTPS CalDAV collection URL, username and a dedicated application password."),
        steps=("Obtain the calendar collection URL from the customer's CalDAV server.", SAVE, TEST_SYNC, SYNC_WARNING)))
    for provider, name, scopes, docs, prerequisite in (
        ("google_meet", "Google Meet", ("https://www.googleapis.com/auth/calendar.events",), "https://developers.google.com/calendar/api/v3/reference/events/insert", "A Google account and calendar with Google Meet conference creation available."),
        ("microsoft_teams", "Microsoft Teams", ("offline_access", "Calendars.ReadWrite"), "https://learn.microsoft.com/graph/api/user-post-events", "A customer Microsoft tenant, selected calendar and an account licensed for Teams online meetings."),
        ("zoom", "Zoom", ("meeting:write:meeting", "meeting:read:meeting", "meeting:read:list_meetings", "meeting:delete:meeting"), "https://developers.zoom.us/docs/integrations/oauth/", "A customer Zoom OAuth application with meeting read, list, write and delete permissions, client ID and client secret.")):
        result.append(recipe("meetings." + provider, name, "meetings", "Schedule an external provider meeting only after explicit disclosure; FreeCollab's own encrypted WebRTC calls remain separate.", "external_meetings", provider, "calendar", flag="external_meetings_enabled", channel=True,
            callback="/api/external-meetings/oauth/callback", scopes=scopes, docs=docs, prerequisites=(CLIENT, prerequisite),
            steps=(CALLBACK, "For Meet or Teams, optionally reuse this channel's current calendar OAuth grant only after separate meeting consent. File tokens are never reused.", SAVE, CONSENT,
                   "Run the explicit provider test, then confirm each meeting's title and schedule before sending them to the provider.")))
    result.append(recipe("ai.openai_compatible", "OpenAI-compatible AI", "ai", "Configure a customer-chosen model endpoint; selected channel context is disclosed only with explicit user consent.", "copilot", "openai_compatible", flag="copilot_enabled",
        docs="https://platform.openai.com/docs/api-reference/chat/create", prerequisites=("A current workspace owner and a public HTTPS port 443 chat-completions endpoint, model ID and provider API key.",),
        steps=("Choose a compatible endpoint preset or enter the customer's endpoint and model. Review the destination before saving.", SAVE, "Review provider configuration and opt in to its current revision before choosing context.",
               "Choose a channel and explicitly approve the selected context for each request. Encrypted content is decrypted on the trusted client before any separately approved disclosure.")))
    result.append(recipe("automation.webhook", "Webhook receiver", "automation", "Deliver signed metadata-only channel events to a customer-owned receiver under explicit current channel grants.", "webhooks", "webhook", flag="webhooks_enabled", channel=True,
        docs="https://www.rfc-editor.org/rfc/rfc2104", prerequisites=("A public HTTPS port 443 receiver and administrator access to every selected group channel.",),
        steps=("Enter the receiver URL and deliberately choose channels and event types.", "Save the receiver and securely store the generated signing secret; FreeCollab shows it only once.",
               "Verify HMAC signatures at the receiver, then explicitly send a test and review its delivery result.", "Webhook payloads contain event metadata, never message or file contents.")))
    def field(key, label, required=True, secret=False):
        return {"key": key, "label": label, "required": required, "secret": secret}
    for item in result:
        target, provider, kind = item["target"], item["provider"], item["kind"]
        fields = []
        if target == "sso":
            fields = [field("name", "Provider name"), field("issuer_url", "Organization OIDC issuer URL"), field("client_id", "Application client ID"),
                field("client_secret", "Application client secret", False, True), field("scopes", "OIDC scopes")]
        elif target == "sync":
            fields = [field("name", "Connection name")]
            if provider in {"google", "microsoft"}:
                fields += [field("client_id", "Application client ID"), field("client_secret", "Application client secret", False, True)]
                if provider == "microsoft": fields += [field("tenant_id", "Customer tenant ID")]
                if kind == "files":
                    if provider == "microsoft": fields += [field("drive_id", "OneDrive drive ID")]
                    fields += [field("folder_id", "Selected root folder ID")]
                else: fields += [field("calendar_id", "Selected calendar ID")]
            else:
                fields += [field("base_url", "WebDAV folder URL" if kind == "files" else "CalDAV calendar collection URL"),
                    field("username", "Provider username"), field("app_password", "Provider application password", True, True)]
        elif target == "cloud_files":
            fields = [field("name", "Connection name"), field("client_id", "Application client ID"),
                field("client_secret", "Application client secret", provider == "box", True),
                field("folder_id", "Selected root folder ID" if provider == "box" else "Root folder path (empty uses the app root)", provider == "box")]
        elif target == "external_meetings":
            fields = [field("name", "Connection name"), field("client_id", "Application client ID"), field("client_secret", "Application client secret", provider == "zoom", True)]
            if provider == "microsoft_teams": fields += [field("tenant_id", "Customer tenant ID")]
            if provider != "zoom": fields += [field("calendar_id", "Selected calendar ID")]
        elif target == "copilot":
            fields = [field("endpoint", "HTTPS chat-completions endpoint"), field("model", "Provider model ID"), field("api_key", "Provider API key", True, True), field("agents", "Enabled AI agents")]
        elif target == "webhooks":
            fields = [field("name", "Connection name"), field("url", "HTTPS receiver URL"), field("channel_ids", "Selected channel IDs"), field("event_types", "Selected event types")]
        item["fields"] = fields
    return result


def public_origin():
    try:
        raw = settings.public_app_url
        if not isinstance(raw, str) or len(raw) > 4096 or any(ord(c) <= 32 or ord(c) == 127 for c in raw) or "\\" in raw: raise ValueError()
        parsed = urlsplit(raw); host = parsed.hostname; parsed.port
        if not host or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment: raise ValueError()
        if parsed.scheme == "http" and host in {"localhost", "127.0.0.1", "::1"} and settings.environment != "production":
            return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")), "local_development"
        if parsed.scheme != "https": raise ValueError()
        return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")), "valid"
    except (ValueError, TypeError): return None, "invalid"


def authorize(session, workspace_id, actor_id, channel_id=None):
    recheck_actor(session, actor_id)
    require_unrestricted(session); require_personal_account(session, actor_id)
    role = require_workspace_admin(session, workspace_id, actor_id)
    if channel_id is not None:
        mapping = session.get(WorkspaceChannel, channel_id)
        member = require_channel_member(session, channel_id, actor_id)
        if not mapping or mapping.workspace_id != workspace_id: raise HTTPException(404, "Channel not found in this workspace")
        if member.role != "admin": raise HTTPException(403, "Channel administrator access required")
        require_direct_message_writable(session, channel_id, actor_id)
    return role


def _admin_grant(session, workspace_id, channel_id, actor_id, *, native=False):
    if not session.exec(select(User.id).where(User.id == actor_id, User.is_blocked.is_(False))).first(): return False
    try:
        require_workspace_admin(session, workspace_id, actor_id)
        member = require_channel_member(session, channel_id, actor_id)
        mapping = session.get(WorkspaceChannel, channel_id)
        if member.role != "admin" or not mapping or mapping.workspace_id != workspace_id: return False
        require_direct_message_writable(session, channel_id, actor_id)
        return not native or not session.get(SecureChannel, channel_id)
    except HTTPException: return False


def _rows(session, model, workspace_id, channel_id, columns):
    if channel_id is None: return []
    names = [name for name in columns if name != "has_credentials"]
    selected = [getattr(model, name) for name in names]
    if "has_credentials" in columns:
        selected.append((model.encrypted_credentials.is_not(None) & (model.encrypted_credentials != "")).label("has_credentials")); names.append("has_credentials")
    values = session.exec(select(*selected).where(model.workspace_id == workspace_id, model.channel_id == channel_id).order_by(model.id).limit(40)).all()
    return [SimpleNamespace(**dict(zip(names, value))) for value in values]


def _connection(identifier, name, state, enabled, revision, verified_at=None):
    return {"id": identifier, "name": name, "status": state, "enabled": enabled, "revision": revision,
            "verified_at": verified_at.isoformat() + "Z" if verified_at else None}


def _state(row, *, oauth=False, picker=False, now=None):
    if row.status in {"error", "attention"}: return "attention"
    if oauth and (row.status in {"oauth_required", "oauth_pending", "disconnected", "draft"} or not row.has_credentials): return "authorization_required"
    if not row.has_credentials: return "draft"
    if picker: return "ready" if row.status == "connected" else "attention"
    if row.enabled and row.tested_revision == row.revision and row.tested_at: return "ready"
    if row.status == "disabled": return "paused"
    if row.tested_revision == row.revision and row.tested_at and (now is None or row.tested_at > now - timedelta(hours=1)): return "paused"
    return "verification_required"


def _finish(item):
    if not item["enabled"]: item["blockers"].insert(0, FLAG_REQUIRED); item["status"] = "unavailable"
    elif item["status"] != "unavailable" and item["connections"]:
        states = {value["status"] for value in item["connections"]}
        item["status"] = next(state for state in ("attention", "ready", "verification_required", "authorization_required", "paused", "draft") if state in states)
    for state, message in (("authorization_required", OAUTH_REQUIRED), ("verification_required", VERIFY_REQUIRED), ("paused", PAUSED)):
        if item["status"] == state and item["target"] not in {"copilot", "sso", "people"}: item["blockers"].append(message)
    item["blockers"] = list(dict.fromkeys(item["blockers"]))
    item.pop("_callback", None)
    return item


def catalog(session, workspace_id, actor_id, channel_id=None):
    actor = authorize(session, workspace_id, actor_id, channel_id)
    origin, origin_status = public_origin(); now = datetime.utcnow()
    secure = bool(channel_id and session.get(SecureChannel, channel_id))
    sync_rows = _rows(session, RemoteSyncConnection, workspace_id, channel_id,
        ["id", "name", "provider", "kind", "grant_user_id", "revision", "enabled", "status", "tested_revision", "tested_at", "has_credentials"])
    cloud_rows = _rows(session, CloudFileConnection, workspace_id, channel_id,
        ["id", "name", "provider", "grant_user_id", "revision", "status", "has_credentials"])
    meeting_rows = _rows(session, ExternalMeetingConnection, workspace_id, channel_id,
        ["id", "name", "provider", "grant_user_id", "revision", "enabled", "status", "tested_revision", "tested_at", "source_connection_id", "source_revision", "has_credentials"])
    result = []
    for item in recipes():
        path = item["_callback"]
        oidc_http_invalid = item["id"] == "sso.oidc" and origin_status == "local_development" and settings.environment != "test"
        if path and origin is not None and not oidc_http_invalid: item["callback_url"] = origin + path
        if path and item["callback_url"] is None:
            item["blockers"].append(SSO_HTTP_REQUIRED if oidc_http_invalid else ORIGIN_REQUIRED); item["status"] = "unavailable"
        if item["requires_channel"] and channel_id is None: item["blockers"].append(CHANNEL_REQUIRED)
        if item["requires_channel"] and secure and not item["secure_supported"]:
            item["blockers"].append(NATIVE_REQUIRED); item["status"] = "unavailable"
        if item["target"] == "sync":
            for row in sync_rows:
                if row.provider != item["provider"] or row.kind != item["kind"]: continue
                valid = _admin_grant(session, workspace_id, channel_id, row.grant_user_id, native=True)
                state = _state(row, oauth=row.provider in {"google", "microsoft"}, now=now) if valid else "attention"
                if not valid: item["blockers"].append(GRANT_REQUIRED)
                item["connections"].append(_connection(row.id, row.name, state, row.enabled, row.revision, row.tested_at if row.tested_revision == row.revision else None))
        elif item["target"] == "cloud_files":
            for row in cloud_rows:
                if row.provider != item["provider"]: continue
                valid = _admin_grant(session, workspace_id, channel_id, row.grant_user_id, native=True)
                state = _state(row, oauth=True, picker=True) if valid else "attention"
                if not valid: item["blockers"].append(GRANT_REQUIRED)
                item["connections"].append(_connection("cloud:" + row.id, row.name, state, state == "ready", row.revision))
        elif item["target"] == "external_meetings":
            for row in meeting_rows:
                if row.provider != item["provider"]: continue
                valid = _admin_grant(session, workspace_id, channel_id, row.grant_user_id)
                has_credentials = row.has_credentials
                if row.source_connection_id:
                    source = next((source for source in sync_rows if source.id == row.source_connection_id), None)
                    source_valid = bool(source and source.revision == row.source_revision and source.kind == "calendar"
                        and source.provider == {"google_meet": "google", "microsoft_teams": "microsoft"}.get(row.provider)
                        and source.has_credentials and _admin_grant(session, workspace_id, channel_id, source.grant_user_id, native=True))
                    if not source_valid: valid = False; item["blockers"].append(RECONNECT_CALENDAR)
                    has_credentials = bool(source_valid)
                state = _state(SimpleNamespace(**{**vars(row), "has_credentials": has_credentials}), oauth=True) if valid else "attention"
                if not valid: item["blockers"].append(GRANT_REQUIRED)
                item["connections"].append(_connection(row.id, row.name, state, row.enabled, row.revision, row.tested_at if row.tested_revision == row.revision else None))
        elif item["target"] == "sso":
            row = session.exec(select(WorkspaceOIDCProvider.name, WorkspaceOIDCProvider.enabled, WorkspaceOIDCProvider.revision,
                WorkspaceOIDCProvider.tested_revision, WorkspaceOIDCProvider.tested_at, WorkspaceOIDCProvider.scopes).where(WorkspaceOIDCProvider.workspace_id == workspace_id)).first()
            if row:
                name, enabled, revision, tested_revision, tested_at, scopes = row
                state = "ready" if enabled and tested_revision == revision and tested_at else "paused" if tested_revision == revision and tested_at and tested_at > now - timedelta(hours=1) else "verification_required"
                item["connections"].append(_connection(workspace_id, name, state, enabled, revision, tested_at if tested_revision == revision else None))
                parts = scopes.split() if isinstance(scopes, str) else []
                if "openid" in parts and len(parts) <= 30 and all(len(part) <= 128 for part in parts): item["scopes"] = list(dict.fromkeys(parts))
                else: item["connections"][0]["status"] = "attention"
                if state == "verification_required": item["blockers"].append("Test the saved OIDC provider before enabling workspace sign-in.")
        elif item["target"] == "people":
            row = session.exec(select(SCIMDirectory.directory_id, SCIMDirectory.enabled, SCIMDirectory.token_version, SCIMDirectory.token_hash.is_not(None), SCIMDirectory.token_issued_by,
                SCIMDirectory.last_used_at).where(SCIMDirectory.workspace_id == workspace_id)).first()
            if row:
                identity, enabled, revision, has_token, owner, used_at = row
                active_owner = session.exec(select(User.id).join(WorkspaceMember, WorkspaceMember.user_id == User.id).where(User.id == owner,
                    User.is_blocked.is_(False), WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.role == "owner")).first() if owner else None
                state = "ready" if enabled and has_token and active_owner else "attention" if enabled and not (has_token and active_owner) else "paused" if revision else "draft"
                item["connections"].append(_connection(identity, "Workspace directory", state, enabled, revision, used_at))
                if enabled and not active_owner: item["blockers"].append("The provisioning credential must be rotated by a current active workspace owner.")
            if actor.role != "owner" and (not row or item["connections"][0]["status"] != "ready"): item["blockers"].append(OWNER_REQUIRED)
        elif item["target"] == "copilot":
            row = session.exec(select(CopilotConfig.revision, CopilotConfig.enabled).where(CopilotConfig.workspace_id == workspace_id)).first()
            if row:
                revision, enabled = row
                item["connections"].append(_connection(workspace_id, "Workspace AI provider", "verification_required" if enabled else "paused", enabled, revision)); item["blockers"].append(AI_REVIEW)
            if actor.role != "owner": item["blockers"].append(OWNER_REQUIRED)
        elif item["target"] == "webhooks" and channel_id:
            endpoints = session.exec(select(WebhookEndpoint.id, WebhookEndpoint.name, WebhookEndpoint.granted_by,
                WebhookEndpoint.enabled, WebhookEndpoint.generation, WebhookEndpoint.channel_ids_json).where(
                WebhookEndpoint.workspace_id == workspace_id, WebhookEndpoint.deleted_at.is_(None)).order_by(WebhookEndpoint.id).limit(20)).all()
            for identity, name, granted_by, enabled, revision, raw_channels in endpoints:
                try:
                    channels = json.loads(raw_channels)
                    if (not isinstance(channels, list) or not 1 <= len(channels) <= 25 or channel_id not in channels
                        or any(not isinstance(value, str) or not value or len(value) > 128 for value in channels)): continue
                except (ValueError, TypeError): continue
                if any(not _admin_grant(session, workspace_id, value, actor_id) for value in channels): continue
                valid = all(_admin_grant(session, workspace_id, value, granted_by) and session.get(Channel, value).type != "direct" for value in channels)
                verified_at = session.exec(select(WebhookDelivery.delivered_at).where(WebhookDelivery.webhook_id == identity,
                    WebhookDelivery.generation == revision, WebhookDelivery.status == "delivered",
                    (WebhookDelivery.channel_id == channel_id) | WebhookDelivery.channel_id.is_(None)).order_by(WebhookDelivery.delivered_at.desc()).limit(1)).first()
                state = "attention" if not valid else "paused" if not enabled else "ready" if verified_at else "verification_required"
                if not valid: item["blockers"].append(GRANT_REQUIRED)
                item["connections"].append(_connection(identity, name, state, enabled, revision, verified_at))
        item["can_configure"] = bool(item["enabled"] and item["status"] != "unavailable"
            and (not item["requires_channel"] or channel_id is not None)
            and (item["target"] not in {"copilot", "people"} or actor.role == "owner"))
        if item["target"] == "webhooks" and channel_id and session.get(Channel, channel_id).type == "direct":
            item["can_configure"] = False; item["status"] = "unavailable"
            item["blockers"].append("Webhook connections require a group channel.")
        result.append(_finish(item))
    session.expire_all(); authorize(session, workspace_id, actor_id, channel_id)
    return {"workspace_id": workspace_id, "channel_id": channel_id, "public_origin": origin, "origin_status": origin_status, "recipes": result}
