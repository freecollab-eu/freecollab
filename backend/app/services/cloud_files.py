"""Channel grant fences, encrypted link metadata and immutable picker imports."""
from datetime import datetime
from hashlib import sha256
import json
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlsplit
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlmodel import select

from ..config import settings
from ..models.cloud_files import CloudFileConnection, CloudFileImport, CloudFileLink
from ..models.file import File as StoredFile
from ..models.file_version import FileDocument, FileVersion
from ..models.remote_sync import RemoteSyncConnection
from ..models.workspace import WorkspaceChannel
from . import cloud_file_providers as providers, remote_providers, remote_sync
from .access import lock_channel_writes, require_channel_member
from .account_security import seal_private, unseal_private
from .crypto import decrypt_message, encrypt_message
from .matrix_bridge import is_secure_channel
from .request_guard import recheck_actor
from .sso_scope import require_unrestricted
from .storage import storage_service
from .workspaces import (lock_workspace, require_direct_message_writable,
                         require_workspace_admin, require_workspace_member)


class BrowseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_revision: int = Field(ge=1, le=2147483647)
    folder_id: str | None = Field(default=None, max_length=1024)
    page_token: str | None = Field(default=None, max_length=40000)


class ImportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_revision: int = Field(ge=1, le=2147483647)
    request_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    file_id: str = Field(min_length=1, max_length=1024)
    parent_id: str = Field(max_length=1024)
    etag: str = Field(min_length=1, max_length=1024)
    document_id: str | None = Field(default=None, max_length=128)
    expected_version: int | None = Field(default=None, ge=1, le=10000)
    consent: bool = False

    @model_validator(mode="after")
    def target(self):
        if bool(self.document_id) != bool(self.expected_version): raise ValueError("Choose a document and its current version")
        return self


class LinkFields(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=300)
    provider: str = Field(pattern=r"^(google|microsoft|dropbox|box)$")
    url: str = Field(min_length=1, max_length=8192)
    external_access_acknowledged: bool

    @model_validator(mode="after")
    def safe(self):
        if not self.external_access_acknowledged: raise ValueError("Acknowledge external provider access")
        validate_link(self.provider, self.url)
        return self


class LinkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    fields: LinkFields | None = None
    matrix_event_id: str | None = Field(default=None, min_length=1, max_length=1024)

    @model_validator(mode="after")
    def exclusive(self):
        if (self.fields is None) == (self.matrix_event_id is None): raise ValueError("Choose fields or an encrypted event")
        return self


def feature():
    if not getattr(settings, "cloud_files_enabled", False): raise HTTPException(404, "Cloud files are disabled on this installation")


def authorize(session, workspace_id, channel_id, actor_id, *, lock=False, admin=False, native=False):
    feature()
    if lock:
        lock_workspace(session, workspace_id)
        lock_channel_writes(session, channel_id)
        session.expire_all()
    recheck_actor(session, actor_id)
    require_workspace_member(session, workspace_id, actor_id)
    mapping = session.get(WorkspaceChannel, channel_id)
    if not mapping or mapping.workspace_id != workspace_id: raise HTTPException(404, "Channel not found in this workspace")
    member = require_channel_member(session, channel_id, actor_id)
    if admin:
        require_unrestricted(session)
        require_workspace_admin(session, workspace_id, actor_id)
        if member.role != "admin": raise HTTPException(403, "Channel administrator access required")
    if lock: require_direct_message_writable(session, channel_id, actor_id)
    if native and is_secure_channel(session, channel_id):
        raise HTTPException(409, "Encrypted channels support explicit encrypted provider links. Server browsing and import are unavailable.")
    return member


def connection(session, workspace_id, channel_id, public_id, actor_id, *, lock=False, revision=None):
    authorize(session, workspace_id, channel_id, actor_id, lock=lock, admin=True, native=True)
    source, separator, identifier = public_id.partition(":")
    if not separator or source not in {"sync", "cloud"}: raise HTTPException(404, "Cloud connection not found")
    if source == "sync" and not remote_sync.enabled(): raise HTTPException(404, "Remote synchronization is disabled")
    row = session.get(RemoteSyncConnection if source == "sync" else CloudFileConnection, identifier, populate_existing=True)
    if (not row or row.workspace_id != workspace_id or row.channel_id != channel_id or row.kind != "files"
            or row.provider not in ({"google", "microsoft"} if source == "sync" else {"dropbox", "box"})):
        raise HTTPException(404, "Cloud connection not found")
    remote_sync.require_native_grant(session, row)
    recheck_actor(session, actor_id)
    if revision is not None and row.revision != revision: raise HTTPException(409, "The cloud connection changed; reload before continuing")
    return row


def credentials(row):
    if isinstance(row, RemoteSyncConnection): return remote_sync.credentials(row)
    return json.loads(unseal_private(row.encrypted_credentials, "cloud-file-credentials", row.id)) if row.encrypted_credentials else {}


def save_credentials(row, data):
    if isinstance(row, RemoteSyncConnection): return remote_sync.save_credentials(row, data)
    row.encrypted_credentials = seal_private(json.dumps(data, separators=(",", ":")), "cloud-file-credentials", row.id) if data else None


def public_connection(row):
    return {"id": ("sync:" if isinstance(row, RemoteSyncConnection) else "cloud:") + row.id,
        "name": row.name, "provider": row.provider, "revision": row.revision, "status": row.status,
        "root_folder_id": json.loads(row.config_json)["folder_id"], "connected": bool(credentials(row).get("access_token"))}


def network_operation(session, workspace_id, channel_id, public_id, actor_id, operation, *, expected_revision):
    """Release DB locks around HTTPS, then recheck actor, grant and revision.

    No bytes or browse metadata are returned if membership/session/consent changes
    while waiting for the provider. Rotating refresh credentials are saved only
    into the exact connection revision that authorized this operation.
    """
    row = connection(session, workspace_id, channel_id, public_id, actor_id, lock=True, revision=expected_revision)
    revision, provider, config, secret = row.revision, row.provider, json.loads(row.config_json), credentials(row)
    if not secret.get("access_token"): raise HTTPException(409, "Connect the provider before browsing")
    session.commit()
    try:
        try: result = operation(provider, config, secret, revision)
        except providers.ProviderError as error:
            if error.status != 401 or not secret.get("refresh_token"): raise
            # The first HTTPS failure may have outlived a logout, removed grant
            # or replacement consent. Recheck before sending a refresh token.
            connection(session, workspace_id, channel_id, public_id, actor_id, lock=True, revision=revision)
            session.commit()
            refresh_config = {**config, **({"client_secret": secret["client_secret"]} if secret.get("client_secret") else {})}
            refreshed = (remote_providers.refresh_tokens(provider, refresh_config, secret["refresh_token"])
                if provider in {"google", "microsoft"} else providers.refresh_tokens(provider, refresh_config, secret["refresh_token"]))
            secret.update(refreshed)
            # Check revocation before retrying with fresh credentials.
            refreshed_row = connection(session, workspace_id, channel_id, public_id, actor_id, lock=True, revision=revision)
            # Rotating refresh tokens must survive a later read failure. Persist
            # only into the unchanged grant before retrying the provider once.
            save_credentials(refreshed_row, secret)
            session.add(refreshed_row)
            session.commit()
            result = operation(provider, config, secret, revision)
    except providers.ProviderError as error:
        connection(session, workspace_id, channel_id, public_id, actor_id, lock=True, revision=revision)
        status = 409 if error.code == "conflict" else 403 if error.status in {401, 403} else 422
        raise HTTPException(status, {"code": error.code, "message": "The cloud provider could not complete this request. Reconnect or reload the selected file."}) from None
    row = connection(session, workspace_id, channel_id, public_id, actor_id, lock=True, revision=revision)
    save_credentials(row, secret)
    session.add(row)
    return row, result


def browse(session, workspace_id, channel_id, public_id, actor_id, data):
    def operation(provider, config, secret, revision):
        cursor = None
        if data.page_token:
            try:
                page = json.loads(unseal_private(data.page_token, "cloud-files-page", public_id))
                if page["revision"] != revision or page["folder"] != data.folder_id or page["expires"] < datetime.utcnow().timestamp(): raise ValueError()
                cursor = page["cursor"]
            except Exception: raise HTTPException(409, "This page expired or belongs to another folder") from None
        result = providers.browse(provider, config, secret, data.folder_id, cursor)
        result["next_page_token"] = seal_private(json.dumps({"cursor": result.pop("cursor"), "folder": data.folder_id,
            "revision": revision, "expires": datetime.utcnow().timestamp() + 600}), "cloud-files-page", public_id) if result["cursor"] else None
        return result
    row, result = network_operation(session, workspace_id, channel_id, public_id, actor_id, operation, expected_revision=data.connection_revision)
    result["connection_revision"], result["connection_id"] = row.revision, public_id
    result["items"] = [{**item, "connection_revision": row.revision, "connection_id": public_id} for item in result["items"]]
    session.commit()
    return result


def fingerprint(public_id, data):
    return sha256(json.dumps({"connection": public_id, **data.model_dump(exclude={"request_id"})}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def receipt(session, channel_id, actor_id, data, digest):
    row = session.get(CloudFileImport, (actor_id, data.request_id))
    if not row: return None
    if row.channel_id != channel_id or row.fingerprint != digest: raise HTTPException(409, "This import request was used for another selection")
    if row.file_id is None: raise HTTPException(410, "This imported file was erased; the acknowledged request cannot be replayed")
    file = session.get(StoredFile, row.file_id)
    if not file or file.channel_id != channel_id: raise HTTPException(409, "The imported file is unavailable")
    from ..api.files import file_metadata
    version = session.get(FileVersion, file.id)
    return file_metadata(file, version, session.get(FileDocument, version.document_id) if version else None)


def import_file(session, workspace_id, channel_id, public_id, actor_id, data):
    if not data.consent: raise HTTPException(422, "Confirm copying this exact selected file into the channel")
    connection(session, workspace_id, channel_id, public_id, actor_id, lock=True, revision=data.connection_revision)
    digest = fingerprint(public_id, data)
    if existing := receipt(session, channel_id, actor_id, data, digest): return existing
    def operation(provider, config, secret, _revision):
        return providers.read_file(provider, config, secret, data.file_id, data.parent_id, data.etag)
    _, selected = network_operation(session, workspace_id, channel_id, public_id, actor_id, operation, expected_revision=data.connection_revision)
    if existing := receipt(session, channel_id, actor_id, data, digest): return existing
    from ..api.files import ALLOWED_TYPES, file_metadata, safe_filename
    content, mime = selected["content"], selected["mime_type"]
    if not isinstance(content, bytes) or len(content) > 50 * 1024 * 1024: raise HTTPException(413, "The selected file exceeds the upload limit")
    if mime not in ALLOWED_TYPES: raise HTTPException(400, "The selected file type is not supported for channel uploads")
    document = session.get(FileDocument, data.document_id) if data.document_id else None
    if data.document_id:
        if not document or document.channel_id != channel_id: raise HTTPException(404, "File document not found in this channel")
        if document.owner_id != actor_id: raise HTTPException(403, "Only the file owner can import a new version")
        if document.version_count >= 10000: raise HTTPException(409, "This document reached its version limit")
        if document.version_count != data.expected_version: raise HTTPException(409, "This file changed; reload before importing a new version")
    from .enterprise_governance import enforce_native_file
    warnings = enforce_native_file(session, channel_id, actor_id, safe_filename(selected["name"]), mime, content)
    identifier, created = uuid4().hex, False
    try:
        path = storage_service.save_file_sync(identifier, content)
        created = True
        file = StoredFile(id=identifier, channel_id=channel_id, uploader_id=actor_id, filename=safe_filename(selected["name"]),
            content_type=mime, size=len(content), storage_path=str(path))
        session.add(file); session.flush()
        if document:
            document.current_file_id, document.version_count = identifier, document.version_count + 1
        else:
            document = FileDocument(id=uuid4().hex, channel_id=channel_id, owner_id=actor_id, current_file_id=identifier)
        session.add(document); session.flush()
        version = FileVersion(file_id=identifier, document_id=document.id, number=document.version_count)
        session.add(version)
        session.add(CloudFileImport(user_id=actor_id, request_id=data.request_id, channel_id=channel_id, fingerprint=digest, file_id=identifier))
        remote_sync.restore_synced_file(session, document.id)
        from .webhooks import enqueue_channel_event
        enqueue_channel_event(session, channel_id, "file.created" if version.number == 1 else "file.version.created", identifier)
        session.commit()
        return {**file_metadata(file, version, document), "policy_warnings": warnings}
    except BaseException:
        session.rollback()
        if created: storage_service.delete_file_sync(identifier)
        raise


def validate_link(provider, raw):
    try:
        url = urlsplit(raw)
        host = (url.hostname or "").lower()
        hosts = {"google": {"drive.google.com", "docs.google.com"}, "microsoft": {"onedrive.live.com", "1drv.ms"},
                 "dropbox": {"www.dropbox.com", "dropbox.com"}, "box": {"app.box.com"}}
        if (url.scheme != "https" or url.username or url.password or url.port not in {None, 443} or not url.path or url.path == "/"
                or any(ord(c) <= 32 or ord(c) == 127 for c in raw) or "\\" in raw
                or provider not in hosts or (host not in hosts[provider] and not (provider == "microsoft" and host.endswith(".sharepoint.com")))
                or any(key.lower() in {"access_token", "refresh_token", "code", "id_token", "client_secret"} for key, _ in parse_qsl(url.query))):
            raise ValueError()
        # Require a file/document sharing path, rather than an arbitrary provider
        # page or consent endpoint. Existing provider permissions remain decisive.
        path = url.path.lower()
        allowed = {"google": ("/file/d/", "/document/d/", "/spreadsheets/d/", "/presentation/d/", "/open"),
                   "dropbox": ("/s/", "/scl/fi/"), "box": ("/file/", "/s/"), "microsoft": ("/",)}
        if not path.startswith(allowed[provider]): raise ValueError()
        return raw
    except (ValueError, TypeError): raise ValueError("Choose an HTTPS file link from the selected provider") from None


def serialize_link(row):
    record = {"id": row.id, "workspace_id": row.workspace_id, "channel_id": row.channel_id,
              "created_by": row.created_by, "created_at": row.created_at, "content_mode": row.content_mode, "matrix_event_id": row.matrix_event_id}
    if row.content_mode == "server":
        payload = json.loads(decrypt_message(row.encrypted_payload, row.channel_id))
        if payload.get("id") != row.id or payload.get("channel_id") != row.channel_id: raise HTTPException(409, "Cloud link context mismatch")
        record["fields"] = LinkFields.model_validate(payload["fields"]).model_dump()
    return record
