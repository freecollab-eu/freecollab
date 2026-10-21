"""Scoped CRDT transport. The server never interprets or decrypts Matrix updates."""
import base64
import binascii
from datetime import datetime
from hashlib import sha256
import json

from fastapi import HTTPException
from nacl.exceptions import CryptoError
from sqlmodel import select

from ..models.shared_documents import SharedDocument, SharedDocumentUpdate
from .access import lock_channel_writes, require_channel_member
from .auth import active_user_for_token
from .chat_notifications import require_native_channel
from .crypto import decrypt_message, encrypt_message
from .matrix_bridge import is_secure_channel, validate_secure_event
from .workspaces import require_direct_message_writable

MAX_DOCUMENTS = 50
MAX_UPDATES = 10000
MAX_STORED_BYTES = 32 * 1024 * 1024
MAX_UPDATE_BYTES = 256 * 1024
MAX_SNAPSHOT_BYTES = 512 * 1024
MAX_COMMENTS = 100
MAX_SNAPSHOTS = 100


def canonical_update(value: str, *, snapshot=False) -> str:
    try:
        binary = base64.b64decode(value, validate=True)
        maximum = MAX_SNAPSHOT_BYTES if snapshot else MAX_UPDATE_BYTES
        if not 2 <= len(binary) <= maximum or base64.b64encode(binary).decode() != value:
            raise ValueError
    except (ValueError, TypeError, binascii.Error):
        raise HTTPException(422, "Use a bounded canonical base64 Yjs update") from None
    return value


def locked_member(session, channel_id: str, user_id: str, token: str):
    lock_channel_writes(session, channel_id)
    session.expire_all()
    current = active_user_for_token(token, session)
    if current is None or current.id != user_id:
        raise HTTPException(401, "Your account or session changed; sign in again")
    member = require_channel_member(session, channel_id, user_id)
    require_direct_message_writable(session, channel_id, user_id)
    return member


def document_for(session, channel_id: str, document_id: str, user_id: str):
    require_channel_member(session, channel_id, user_id)
    row = session.get(SharedDocument, document_id)
    if row is None or row.channel_id != channel_id:
        raise HTTPException(404, "Shared document not found")
    return row


def writable_document(session, channel_id: str, document_id: str, user_id: str, token: str):
    member = locked_member(session, channel_id, user_id, token)
    row = document_for(session, channel_id, document_id, user_id)
    if row.archived:
        raise HTTPException(409, "This document is archived; restore it before editing")
    return row, member


def validate_mode(session, channel_id: str, user_id: str, matrix_event_id: str | None, token: str | None = None):
    secure = is_secure_channel(session, channel_id)
    if secure != (matrix_event_id is not None):
        raise HTTPException(409, "Use encrypted event references for this secure channel and native updates for a native channel")
    if secure:
        validate_secure_event(session, channel_id, matrix_event_id, user_id)
        # Matrix pointer verification performs network I/O. Do not retain a
        # pre-I/O cached account, session or membership decision afterwards.
        if token is not None:
            session.expire_all()
            current = active_user_for_token(token, session)
            if current is None or current.id != user_id:
                raise HTTPException(401, "Your account or session changed; sign in again")
            require_channel_member(session, channel_id, user_id)
            require_direct_message_writable(session, channel_id, user_id)
            if not is_secure_channel(session, channel_id):
                raise HTTPException(409, "Channel encryption changed; reload before trying again")
    else:
        require_native_channel(session, channel_id)
    return secure


def seal(channel_id: str, kind: str, document_id: str, identifier: str, fields: dict) -> str:
    return encrypt_message(json.dumps({"kind": kind, "document_id": document_id, "id": identifier,
                                      "fields": fields}, ensure_ascii=False, separators=(",", ":")), channel_id)


def open_payload(session, channel_id: str, kind: str, document_id: str, identifier: str, ciphertext: str) -> dict:
    require_native_channel(session, channel_id)
    try:
        payload = json.loads(decrypt_message(ciphertext, channel_id))
        if (payload.get("kind"), payload.get("document_id"), payload.get("id")) != (kind, document_id, identifier):
            raise ValueError
        if not isinstance(payload.get("fields"), dict):
            raise ValueError
        return payload["fields"]
    except (CryptoError, ValueError, TypeError, AttributeError, KeyError):
        raise HTTPException(409, "Stored shared document content could not be verified") from None


def metadata(row, session=None):
    result = {key: getattr(row, key) for key in ("id", "channel_id", "created_by", "created_at", "updated_at",
                                              "archived", "version", "update_count", "summary_event_id", "summary_update_id", "summary_by")}
    if session is not None and row.summary_encrypted_payload:
        result["title"] = open_payload(session, row.channel_id, "summary", row.id, row.id, row.summary_encrypted_payload)["title"]
    return result


def content_record(session, document: SharedDocument, row, kind: str):
    identifier = row.update_id if kind == "update" else row.id
    result = {"id": identifier, "document_id": document.id, "channel_id": document.channel_id,
              "created_by": row.created_by, "created_at": row.created_at,
              "content_mode": row.content_mode, "matrix_event_id": row.matrix_event_id}
    if kind == "update":
        result["sequence"] = row.sequence
    elif kind == "snapshot":
        result["covers_sequence"] = row.covers_sequence
    else:
        result.update(resolved=row.resolved, version=row.version)
    if row.content_mode == "server":
        result["fields"] = open_payload(session, document.channel_id, kind, document.id, identifier, row.encrypted_payload)
    return result


def fingerprint(fields: dict | None, matrix_event_id: str | None) -> str:
    return sha256(json.dumps({"fields": fields, "event": matrix_event_id}, sort_keys=True,
                             ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def append_update(session, document, data, user_id: str, token: str):
    secure = validate_mode(session, document.channel_id, user_id, data.matrix_event_id, token)
    fields = None if secure else {"update_base64": canonical_update(data.update_base64), "title": data.title}
    if fields:
        from .enterprise_governance import enforce_native_document
        enforce_native_document(session, document.channel_id, user_id, fields, opaque_update=True)
    checksum = fingerprint(fields, data.matrix_event_id)
    previous = session.get(SharedDocumentUpdate, (document.id, data.update_id))
    if previous is not None:
        if previous.created_by != user_id or previous.payload_hash != checksum:
            raise HTTPException(409, "This update identifier has already been used")
        return previous
    size = len(base64.b64decode(fields["update_base64"])) if fields else 512
    if document.update_count >= MAX_UPDATES or document.stored_bytes + size > MAX_STORED_BYTES:
        raise HTTPException(409, "This document reached its retained update limit; export or create a continuation document")
    row = SharedDocumentUpdate(document_id=document.id, update_id=data.update_id,
        sequence=document.update_count + 1, created_by=user_id, content_mode="matrix" if secure else "server",
        payload_hash=checksum, matrix_event_id=data.matrix_event_id, byte_size=size,
        encrypted_payload=None if secure else seal(document.channel_id, "update", document.id, data.update_id, fields))
    document.update_count += 1
    document.stored_bytes += size
    document.updated_at = datetime.utcnow()
    document.summary_by = user_id
    document.summary_update_id = data.update_id
    if secure:
        document.summary_event_id = data.matrix_event_id
        document.summary_encrypted_payload = None
    elif data.title is not None or document.summary_encrypted_payload is None:
        document.summary_encrypted_payload = seal(document.channel_id, "summary", document.id, document.id,
                                                 {"title": data.title or "Untitled document"})
    session.add(row)
    session.add(document)
    return row
