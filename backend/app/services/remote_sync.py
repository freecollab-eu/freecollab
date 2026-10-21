"""Durable two-way synchronization for explicitly granted native channels.

Providers receive content only after a native administrator grants a particular
channel and remote collection. Matrix channels are always rejected. Versions and
deletion tombstones are retained; concurrent changes require an explicit choice.
"""
from datetime import datetime, timedelta
import hashlib
import json
import re
import secrets
import time
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import or_, update
from sqlmodel import select

from ..config import settings
from ..models.channel import ChannelMember
from ..models.file import File
from ..models.file_version import FileDocument, FileVersion
from ..models.integration_content import IntegrationContent, IntegrationContentRevision
from ..models.remote_sync import (RemoteSyncActivity, RemoteSyncConnection, RemoteSyncConflict,
                                  RemoteSyncFileState, RemoteSyncItem)
from ..models.user import User
from ..models.workspace import WorkspaceChannel
from .account_security import seal_private, unseal_private
from .access import lock_channel_writes, require_channel_member
from .crypto import decrypt_message, encrypt_message
from .integration_content import CalendarFields
from .matrix_bridge import is_secure_channel
from .sso_scope import clear_scope
from .storage import storage_service
from .workspaces import lock_workspace, require_direct_message_writable, require_workspace_admin

MAX_ITEMS = 1000
MAX_FILE_SIZE = 50 * 1024 * 1024


def enabled():
    return bool(getattr(settings, "sync_enabled", False))


def credentials(connection):
    if not connection.encrypted_credentials:
        return {}
    return json.loads(unseal_private(connection.encrypted_credentials, "remote-sync-credentials", connection.id))


def save_credentials(connection, value):
    connection.encrypted_credentials = seal_private(json.dumps(value, separators=(",", ":")),
                                                   "remote-sync-credentials", connection.id) if value else None


def safe_connection(session, row):
    conflicts = session.exec(select(RemoteSyncConflict.id).where(
        RemoteSyncConflict.connection_id == row.id, RemoteSyncConflict.resolved_at.is_(None))).all()
    return {"id": row.id, "name": row.name, "provider": row.provider, "kind": row.kind,
            "channel_id": row.channel_id, "workspace_id": row.workspace_id,
            "config": json.loads(row.config_json), "status": row.status, "enabled": row.enabled,
            "revision": row.revision, "has_credentials": bool(row.encrypted_credentials),
            "tested": row.tested_revision == row.revision and row.tested_at is not None
                and row.tested_at > datetime.utcnow() - timedelta(hours=1),
            "last_synced_at": row.last_synced_at, "last_error": row.last_error,
            "next_run_at": row.next_run_at, "conflict_count": len(conflicts)}


def activity(session, row, event, status="ok", item_id=None):
    session.add(RemoteSyncActivity(connection_id=row.id, event=event, status=status, item_id=item_id))


def require_native_grant(session, connection, *, lock=False, actor_id=None):
    """Rechecked before and after I/O, and under locks before every mutation."""
    if lock:
        lock_workspace(session, connection.workspace_id)
        lock_channel_writes(session, connection.channel_id)
        session.expire_all()
    actor = session.get(User, actor_id or connection.grant_user_id, populate_existing=True)
    if not actor or actor.is_blocked:
        raise HTTPException(403, "The connection administrator is no longer active")
    require_workspace_admin(session, connection.workspace_id, actor.id)
    channel_member = require_channel_member(session, connection.channel_id, actor.id)
    mapping = session.get(WorkspaceChannel, connection.channel_id)
    if not mapping or mapping.workspace_id != connection.workspace_id or channel_member.role != "admin":
        raise HTTPException(403, "A channel administrator must authorize this connection")
    require_direct_message_writable(session, connection.channel_id, actor.id)
    if is_secure_channel(session, connection.channel_id):
        raise HTTPException(409, {"code": "unsupported_secure_channel",
            "message": "Background sync is available only for server-encrypted channels. End-to-end encrypted content stays on your devices."})
    return actor


def _current(session, connection_id, revision, lease=None, *, lock=False):
    if not enabled():
        raise HTTPException(503, "Remote synchronization is disabled on this installation")
    row = session.get(RemoteSyncConnection, connection_id, populate_existing=True)
    if not row or not row.enabled or row.revision != revision or (lease and row.lease_token != lease):
        raise HTTPException(409, "The sync connection changed")
    require_native_grant(session, row, lock=lock)
    if lock and lease:
        renewed = session.execute(update(RemoteSyncConnection).where(RemoteSyncConnection.id == connection_id,
            RemoteSyncConnection.revision == revision, RemoteSyncConnection.lease_token == lease,
            RemoteSyncConnection.enabled == True).values(lease_expires_at=datetime.utcnow() + timedelta(minutes=10)))
        if renewed.rowcount != 1:
            raise HTTPException(409, "The sync connection changed")
    return row


def is_file_deleted(session, document_id):
    state = session.get(RemoteSyncFileState, document_id)
    return bool(state and state.deleted)


def tombstone_synced_file(session, document):
    if not document:
        return False
    bound = session.exec(select(RemoteSyncItem.id).join(RemoteSyncConnection,
        RemoteSyncConnection.id == RemoteSyncItem.connection_id).where(
        RemoteSyncItem.local_id == document.id, RemoteSyncConnection.kind == "files")).first()
    configured = session.exec(select(RemoteSyncConnection.id).where(
        RemoteSyncConnection.channel_id == document.channel_id, RemoteSyncConnection.kind == "files")).first()
    state = session.get(RemoteSyncFileState, document.id)
    if not bound and not state and not configured:
        return False
    state = state or RemoteSyncFileState(document_id=document.id)
    state.deleted = True
    state.revision += 1
    state.updated_at = datetime.utcnow()
    session.add(state)
    return True


def restore_synced_file(session, document_id):
    state = session.get(RemoteSyncFileState, document_id)
    if state and state.deleted:
        state.deleted = False
        state.revision += 1
        state.updated_at = datetime.utcnow()
        session.add(state)


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _latest_calendar(session, item_id):
    return session.exec(select(IntegrationContentRevision).where(IntegrationContentRevision.item_id == item_id)
                        .order_by(IntegrationContentRevision.revision.desc()).limit(1)).first()


def local_item(session, connection, local_id):
    if connection.kind == "calendar":
        item = session.get(IntegrationContent, local_id)
        if not item or item.channel_id != connection.channel_id or item.kind != "calendar":
            return None
        revision = _latest_calendar(session, local_id)
        if not revision or revision.content_mode != "server":
            raise HTTPException(409, "Native calendar content is unavailable")
        payload = json.loads(decrypt_message(revision.encrypted_payload, connection.channel_id))
        if payload.get("id") != local_id or payload.get("revision") != revision.revision or payload.get("kind") != "calendar":
            raise HTTPException(409, "Calendar content context mismatch")
        fields = payload.get("fields")
        if not revision.deleted:
            fields = CalendarFields.model_validate(fields).model_dump(mode="json")
        return {"id": local_id, "deleted": revision.deleted, "fields": fields,
                "fingerprint": _hash({"revision": revision.revision, "deleted": revision.deleted, "fields": fields})}
    document = session.get(FileDocument, local_id)
    if not document or document.channel_id != connection.channel_id:
        return None
    record = session.get(File, document.current_file_id)
    if not record or record.channel_id != connection.channel_id:
        raise HTTPException(409, "Stored file content is unavailable")
    deleted = is_file_deleted(session, document.id)
    try:
        value = storage_service.read_file(record.id, max_bytes=MAX_FILE_SIZE, expected_size=record.size)
    except (OSError, ValueError):
        raise HTTPException(409, "Stored file content is unavailable") from None
    if value is None:
        raise HTTPException(409, "Stored file content is unavailable")
    return {"id": local_id, "deleted": deleted, "bytes": value, "name": record.filename,
            "mime_type": record.content_type, "fingerprint": _hash({"version": document.version_count,
                "hash": hashlib.sha256(value).hexdigest(), "name": record.filename,
                "mime_type": record.content_type, "deleted": deleted})}


def local_ids(session, connection):
    if connection.kind == "calendar":
        return session.exec(select(IntegrationContent.id).where(IntegrationContent.channel_id == connection.channel_id,
                                                               IntegrationContent.kind == "calendar")).all()
    documents = session.exec(select(FileDocument.id).where(FileDocument.channel_id == connection.channel_id)).all()
    # Adopt retained pre-versioning files without altering their original blobs.
    legacy = session.exec(select(File).outerjoin(FileVersion, FileVersion.file_id == File.id)
                          .where(File.channel_id == connection.channel_id, FileVersion.file_id.is_(None))).all()
    for record in legacy:
        session.add(FileDocument(id=record.id, channel_id=record.channel_id, owner_id=record.uploader_id,
                                current_file_id=record.id, created_at=record.created_at))
        session.flush()
        session.add(FileVersion(file_id=record.id, document_id=record.id, number=1))
        documents.append(record.id)
    session.flush()
    return documents


def _remote_snapshot(item):
    return json.loads(unseal_private(item.encrypted_remote_snapshot, "remote-sync-item", item.id)) if item.encrypted_remote_snapshot else None


def _remember_remote(item, remote):
    item.encrypted_remote_snapshot = seal_private(json.dumps(remote, separators=(",", ":")), "remote-sync-item", item.id)
    item.read_only = bool(remote.get("read_only"))
    item.remote_deleted = bool(remote.get("deleted"))


def _conflict(session, connection, item, reason, local, remote):
    row = session.exec(select(RemoteSyncConflict).where(RemoteSyncConflict.item_id == item.id,
        RemoteSyncConflict.resolved_at.is_(None)).limit(1)).first()
    if row is None:
        row = RemoteSyncConflict(connection_id=connection.id, item_id=item.id, reason=reason)
        activity(session, connection, "conflict.created", "attention", item.id)
    row.reason, row.resolution = reason, None
    row.local_fingerprint = local.get("fingerprint") if local else None
    row.remote_etag = remote.get("etag") if remote else None
    row.remote_deleted = bool(remote and remote.get("deleted"))
    row.read_only = bool(remote and remote.get("read_only"))
    session.add(row)
    return row


def _complete_item(session, item, local, remote):
    item.base_local_fingerprint = local["fingerprint"]
    item.base_remote_etag = remote.get("etag")
    item.remote_deleted = bool(remote.get("deleted"))
    item.pending_operation = None
    item.updated_at = datetime.utcnow()
    _remember_remote(item, remote)
    session.add(item)
    for conflict in session.exec(select(RemoteSyncConflict).where(RemoteSyncConflict.item_id == item.id,
        RemoteSyncConflict.resolved_at.is_(None))).all():
        conflict.resolved_at = datetime.utcnow()
        session.add(conflict)


def _import_remote(session, connection, item, remote, value):
    """Append native immutable content. Never overwrite or erase historical bytes."""
    from .webhooks import enqueue_channel_event
    if connection.kind == "calendar":
        existing = session.get(IntegrationContent, item.local_id)
        previous = _latest_calendar(session, item.local_id) if existing else None
        if not existing:
            existing = IntegrationContent(id=item.local_id, channel_id=connection.channel_id,
                                          kind="calendar", created_by=connection.grant_user_id)
            session.add(existing)
            session.flush()
        number = previous.revision + 1 if previous else 1
        deleted = bool(remote.get("deleted"))
        fields = None if deleted else CalendarFields.model_validate(value["fields"]).model_dump(mode="json")
        if fields:
            from .enterprise_governance import enforce_native_document
            enforce_native_document(session, connection.channel_id, connection.grant_user_id, fields)
        payload = {"id": item.local_id, "kind": "calendar", "revision": number, "fields": fields, "deleted": deleted}
        revision = IntegrationContentRevision(item_id=item.local_id, revision=number,
            created_by=connection.grant_user_id, content_mode="server", deleted=deleted,
            encrypted_payload=encrypt_message(json.dumps(payload, ensure_ascii=False), connection.channel_id))
        session.add(revision)
        session.flush()
        enqueue_channel_event(session, connection.channel_id, "channel.activity", revision.id)
        return None
    document = session.get(FileDocument, item.local_id)
    if remote.get("deleted"):
        if document:
            state = session.get(RemoteSyncFileState, document.id) or RemoteSyncFileState(document_id=document.id)
            state.deleted, state.updated_at = True, datetime.utcnow()
            state.revision += 1
            session.add(state)
            session.flush()
        return None
    from ..api.files import ALLOWED_TYPES, safe_filename
    content = value.get("bytes")
    mime = value.get("mime_type") or remote.get("mime_type") or "application/octet-stream"
    if not isinstance(content, bytes) or len(content) > MAX_FILE_SIZE:
        raise HTTPException(409, "Remote file exceeds the supported size")
    if mime not in ALLOWED_TYPES:
        raise HTTPException(409, "Remote file type is not supported")
    from .enterprise_governance import enforce_native_file
    enforce_native_file(session, connection.channel_id, connection.grant_user_id, safe_filename(remote.get("name")), mime, content)
    file_id = uuid4().hex
    created = False
    try:
        path = storage_service.save_file_sync(file_id, content)
        created = True
        record = File(id=file_id, channel_id=connection.channel_id, uploader_id=connection.grant_user_id,
            filename=safe_filename(remote.get("name")), content_type=mime, size=len(content), storage_path=str(path))
        session.add(record)
        session.flush()
        if document is None:
            document = FileDocument(id=item.local_id, channel_id=connection.channel_id,
                owner_id=connection.grant_user_id, current_file_id=file_id)
            session.add(document)
            session.flush()
            number = 1
        else:
            number = document.version_count + 1
            document.current_file_id, document.version_count = file_id, number
            session.add(document)
        session.add(FileVersion(file_id=file_id, document_id=document.id, number=number))
        restore_synced_file(session, document.id)
        session.flush()
        enqueue_channel_event(session, connection.channel_id, "file.version.created" if number > 1 else "file.created", file_id)
        return file_id
    except BaseException:
        if created:
            storage_service.delete_file_sync(file_id)
        raise


def _provider_config(connection, secret):
    config = json.loads(connection.config_json)
    if secret.get("client_secret"):
        config["client_secret"] = secret["client_secret"]
    return config


def frozen_connection(connection):
    """A network call must not lazily reload a changed ORM configuration."""
    return SimpleNamespace(**{key: getattr(connection, key) for key in
        ("id", "provider", "kind", "config_json", "revision", "lease_token")})


def _provider_call(session, connection, secret, function, *args, **kwargs):
    from . import remote_providers as providers
    try:
        return function(connection.provider, connection.kind, _provider_config(connection, secret), secret, *args, **kwargs)
    except providers.ProviderError as error:
        if getattr(error, "status", None) != 401 or not secret.get("refresh_token"):
            raise
        token = providers.refresh_tokens(connection.provider, _provider_config(connection, secret), secret["refresh_token"])
        secret.update(token)
        # Refresh tokens may rotate. Persist them before another network call,
        # even if the subsequent provider read/write fails.
        expected_revision, lease = connection.revision, connection.lease_token
        current = session.get(RemoteSyncConnection, connection.id, populate_existing=True)
        if current is None or current.revision != expected_revision or current.lease_token != lease:
            raise HTTPException(409, "The sync connection changed")
        require_native_grant(session, current, lock=True)
        save_credentials(current, secret)
        session.add(current)
        session.commit()
        if function in (providers.write_item, providers.delete_item):
            _current(session, current.id, expected_revision, lease, lock=True)
        return function(connection.provider, connection.kind, _provider_config(connection, secret), secret, *args, **kwargs)


def _sync_item(session, connection_id, revision, lease, item_id, secret):
    from . import remote_providers as providers
    connection = _current(session, connection_id, revision, lease)
    item = session.get(RemoteSyncItem, item_id)
    remote = _remote_snapshot(item)
    local = local_item(session, connection, item.local_id)
    conflict = session.exec(select(RemoteSyncConflict).where(RemoteSyncConflict.item_id == item.id,
        RemoteSyncConflict.resolved_at.is_(None)).limit(1)).first()
    choice = conflict.resolution if conflict else None
    if conflict and not choice:
        return
    if choice and ((local.get("fingerprint") if local else None) != conflict.local_fingerprint
                   or (remote.get("etag") if remote else None) != conflict.remote_etag
                   or bool(remote and remote.get("deleted")) != conflict.remote_deleted):
        _conflict(session, connection, item, "changed_after_conflict", local, remote)
        return
    if item.pending_operation and item.remote_id is None and not choice:
        _conflict(session, connection, item, "ambiguous_create", local, remote)
        return
    local_changed = local is not None and local["fingerprint"] != item.base_local_fingerprint
    remote_changed = remote is not None and (remote.get("etag") != item.base_remote_etag
                     or bool(remote.get("deleted")) != item.remote_deleted)
    if item.base_local_fingerprint is None and remote is not None and not remote.get("deleted"):
        remote_changed = True
        local_changed = local is not None
    if choice:
        local_changed, remote_changed = choice == "local", choice == "remote"
    if local is None and item.base_local_fingerprint:
        _conflict(session, connection, item, "local_content_missing", local, remote)
        return
    if local and remote and local.get("deleted") and (remote.get("deleted") or remote.get("read_only")):
        # Removing a read-only export hides the local copy; it never deletes the
        # provider's source document or silently reimports a later source edit.
        _complete_item(session, item, local, remote)
        return
    if local_changed and remote_changed:
        _conflict(session, connection, item, "both_changed", local, remote)
        return
    if remote_changed:
        if remote.get("deleted") and local is None:
            return
        # No lock held during download; check channel/admin/revision again before applying.
        request_connection = frozen_connection(connection)
        session.commit()
        value = {} if remote.get("deleted") else _provider_call(session, request_connection, secret, providers.read_item, remote)
        if not remote.get("deleted") and value.get("etag") and value["etag"] != remote.get("etag"):
            connection = _current(session, connection_id, revision, lease)
            _conflict(session, connection, item, "remote_changed_during_read", local, remote)
            return
        connection = _current(session, connection_id, revision, lease, lock=True)
        current_local = local_item(session, connection, item.local_id)
        if (current_local.get("fingerprint") if current_local else None) != (local.get("fingerprint") if local else None):
            _conflict(session, connection, item, "local_changed_during_read", current_local, remote)
            return
        path = None
        try:
            path = _import_remote(session, connection, item, remote, value)
            current_local = local_item(session, connection, item.local_id)
            _complete_item(session, item, current_local, remote)
            activity(session, connection, "sync.imported", item_id=item.id)
            save_credentials(connection, secret)
            session.add(connection)
            session.commit()
        except (HTTPException, ValueError) as error:
            session.rollback()
            if path:
                storage_service.delete_file_sync(path)
            connection = _current(session, connection_id, revision, lease, lock=True)
            item = session.get(RemoteSyncItem, item_id)
            _conflict(session, connection, item, "unsupported_remote_content", local, remote)
            session.commit()
        except BaseException:
            session.rollback()
            if path:
                storage_service.delete_file_sync(path)
            raise
        return
    if not local_changed:
        return
    if local is None:
        return
    if remote and remote.get("read_only"):
        _conflict(session, connection, item, "remote_read_only", local, remote)
        return
    if remote and remote.get("deleted") and not local.get("deleted"):
        # An acknowledged remote deletion plus a new explicit local version is
        # a deliberate restore. Create a fresh resource instead of stale PUT.
        item.remote_id = None
        remote = None
    if local.get("deleted") and item.remote_id is None:
        return
    # Keep the workspace/channel writer locks through the bounded external write:
    # removal or role revocation cannot interleave a newly authorized dispatch.
    connection = _current(session, connection_id, revision, lease, lock=True)
    if item.remote_id is None:
        item.pending_operation = "create"
        session.add(item)
        session.commit()  # Durable uncertainty marker before a possible remote create.
        connection = _current(session, connection_id, revision, lease, lock=True)
    try:
        if local.get("deleted"):
            _provider_call(session, connection, secret, providers.delete_item, item.remote_id,
                           expected_etag=remote.get("etag") if remote else item.base_remote_etag)
            updated = {**(remote or {}), "id": item.remote_id, "deleted": True}
        else:
            resource = dict(local.get("fields") or {key: local[key] for key in ("name", "mime_type", "bytes")})
            resource["remote_key"] = "fc-" + item.id
            updated = _provider_call(session, connection, secret, providers.write_item, resource,
                remote_id=item.remote_id, expected_etag=remote.get("etag") if remote else None)
            if not updated.get("id") or not updated.get("etag"):
                raise providers.ProviderError("invalid_response")
            item.remote_id = str(updated["id"])
        _current(session, connection_id, revision, lease)
        _complete_item(session, item, local, updated)
        activity(session, connection, "sync.exported", item_id=item.id)
        save_credentials(connection, secret)
        session.add(connection)
    except providers.ProviderError as error:
        if getattr(error, "status", None) in (409, 412):
            _conflict(session, connection, item, "remote_precondition_failed", local, remote)
        else:
            raise


def sync_connection(session, connection_id, revision, lease):
    from . import remote_providers as providers
    connection = _current(session, connection_id, revision, lease)
    started = time.monotonic()
    secret = credentials(connection)
    cursor = unseal_private(connection.encrypted_cursor, "remote-sync-cursor", connection.id) if connection.encrypted_cursor else None
    request_connection = frozen_connection(connection)
    session.commit()
    try:
        snapshot = _provider_call(session, request_connection, secret, providers.snapshot, cursor=cursor)
    except providers.ProviderError as error:
        if getattr(error, "code", None) != "cursor_expired":
            raise
        snapshot = _provider_call(session, request_connection, secret, providers.snapshot, cursor=None)
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("items"), list) or len(snapshot["items"]) > MAX_ITEMS:
        raise providers.ProviderError("invalid_response")
    connection = _current(session, connection_id, revision, lease, lock=True)
    locals_now = local_ids(session, connection)
    if len(locals_now) > MAX_ITEMS:
        raise HTTPException(409, "The channel exceeds the sync item limit")
    mappings = session.exec(select(RemoteSyncItem).where(RemoteSyncItem.connection_id == connection_id)).all()
    by_remote = {item.remote_id: item for item in mappings if item.remote_id}
    by_local = {item.local_id: item for item in mappings}
    by_key = {"fc-" + item.id: item for item in mappings if item.remote_id is None and item.pending_operation == "create"}
    seen = set()
    for remote in snapshot["items"]:
        remote_id = str(remote.get("id", ""))
        if not remote_id or len(remote_id) > 4096 or any(ord(c) < 32 for c in remote_id):
            raise providers.ProviderError("invalid_response")
        if remote_id in seen:
            raise providers.ProviderError("invalid_response")
        seen.add(remote_id)
        item = by_remote.get(remote_id) or by_key.get(remote.get("remote_key"))
        if item is None:
            if remote.get("deleted"):
                continue
            item = RemoteSyncItem(connection_id=connection_id, local_id=uuid4().hex, remote_id=remote_id)
            session.add(item)
            session.flush()
            mappings.append(item)
        item.remote_id = remote_id
        item.pending_operation = None
        # Keep base_remote_etag/deletion untouched until reconciliation succeeds.
        previous_deleted = item.remote_deleted
        _remember_remote(item, remote)
        item.remote_deleted = previous_deleted
        session.add(item)
    if snapshot.get("complete") is True:
        for item in mappings:
            if item.remote_id and item.remote_id not in seen:
                previous = _remote_snapshot(item) or {"id": item.remote_id, "etag": item.base_remote_etag}
                previous["deleted"] = True
                previous_deleted = item.remote_deleted
                _remember_remote(item, previous)
                item.remote_deleted = previous_deleted
                session.add(item)
    for local_id in locals_now:
        if local_id not in by_local:
            item = RemoteSyncItem(connection_id=connection_id, local_id=local_id)
            session.add(item)
            mappings.append(item)
    if len(mappings) > MAX_ITEMS:
        raise HTTPException(409, "The connection exceeds the sync item limit")
    save_credentials(connection, secret)
    session.add(connection)
    session.commit()
    candidates = sorted((item for item in mappings if not connection.scan_after_id or item.id > connection.scan_after_id), key=lambda item: item.id)
    processed, last_id = 0, connection.scan_after_id
    for item in candidates:
        if processed >= 20 or (processed > 0 and time.monotonic() - started >= 45):
            break
        _sync_item(session, connection_id, revision, lease, item.id, secret)
        session.commit()
        processed, last_id = processed + 1, item.id
    connection = _current(session, connection_id, revision, lease, lock=True)
    cursor_value = snapshot.get("cursor")
    if cursor_value is not None and (not isinstance(cursor_value, str) or len(cursor_value) > 16384):
        raise providers.ProviderError("invalid_response")
    connection.encrypted_cursor = seal_private(cursor_value, "remote-sync-cursor", connection.id) if cursor_value else None
    pending = processed < len(candidates)
    connection.scan_after_id = last_id if pending else None
    connection.status = "queued" if pending else "attention" if session.exec(select(RemoteSyncConflict.id).where(
        RemoteSyncConflict.connection_id == connection_id, RemoteSyncConflict.resolved_at.is_(None))).first() else "connected"
    if not pending:
        connection.last_synced_at = datetime.utcnow()
    connection.last_error, connection.failure_count = None, 0
    connection.next_run_at = datetime.utcnow() + timedelta(seconds=1 if pending else 60)
    connection.lease_token = connection.lease_expires_at = None
    save_credentials(connection, secret)
    session.add(connection)
    activity(session, connection, "sync.completed", connection.status)
    session.commit()


def process_pending(session, limit=2):
    if not enabled():
        return 0
    now = datetime.utcnow()
    candidates = session.exec(select(RemoteSyncConnection.id).where(RemoteSyncConnection.enabled == True,
        RemoteSyncConnection.next_run_at <= now,
        or_(RemoteSyncConnection.lease_expires_at.is_(None), RemoteSyncConnection.lease_expires_at <= now))
        .order_by(RemoteSyncConnection.next_run_at).limit(max(0, min(limit, 10)))).all()
    completed = 0
    for connection_id in candidates:
        clear_scope(session)
        lease = secrets.token_hex(24)
        claimed = session.execute(update(RemoteSyncConnection).where(RemoteSyncConnection.id == connection_id,
            RemoteSyncConnection.enabled == True,
            or_(RemoteSyncConnection.lease_expires_at.is_(None), RemoteSyncConnection.lease_expires_at <= now))
            .values(lease_token=lease, lease_expires_at=now + timedelta(minutes=10), status="syncing"))
        session.commit()
        if claimed.rowcount != 1:
            continue
        row = session.get(RemoteSyncConnection, connection_id, populate_existing=True)
        revision = row.revision
        try:
            sync_connection(session, connection_id, revision, lease)
            completed += 1
        except Exception as error:
            session.rollback()
            clear_scope(session)
            row = session.get(RemoteSyncConnection, connection_id, populate_existing=True)
            if row and row.lease_token == lease:
                code = getattr(error, "code", "provider_unavailable")
                safe_code = code if isinstance(code, str) and re.fullmatch(r"[a-z_]{1,60}", code) else "provider_unavailable"
                if isinstance(error, HTTPException):
                    if isinstance(error.detail, dict) and error.detail.get("code") == "unsupported_secure_channel":
                        safe_code = "unsupported_secure_channel"
                    else:
                        safe_code = "authorization_lost" if error.status_code == 403 else "connection_changed"
                row.failure_count += 1
                row.last_error, row.status = safe_code, "error"
                row.next_run_at = datetime.utcnow() + timedelta(seconds=min(3600, 30 * 2 ** min(row.failure_count, 6)))
                row.lease_token = row.lease_expires_at = None
                if safe_code in {"authorization_lost", "unsupported_secure_channel"}:
                    row.enabled = False
                session.add(row)
                activity(session, row, "sync.failed", safe_code)
                session.commit()
    return completed
