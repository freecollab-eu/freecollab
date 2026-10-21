import hashlib
from uuid import uuid4
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, UploadFile, File as FastAPIFile
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from sqlalchemy.exc import IntegrityError
from sqlalchemy import delete, update
from ..services.request_guard import recheck_actor
from sqlmodel import select

from ..database import get_session
from ..models.file import File
from ..models.file_version import FileDocument, FileUploadReceipt, FileVersion
from ..models.cloud_files import CloudFileImport
from ..models.file_experience import FileComment, OfficeEditLock, OfficeEditSession
from ..services.auth import get_current_user
from ..services.access import require_channel_member, lock_channel_writes
from ..services.storage import storage_service
from ..services.matrix_bridge import is_secure_channel
from ..services.workspaces import require_direct_message_writable
from ..services.remote_sync import is_file_deleted, restore_synced_file, tombstone_synced_file

router = APIRouter(prefix="/files", tags=["files"])

MAX_FILE_SIZE = 50 * 1024 * 1024
ALLOWED_TYPES = {
    "image/jpeg", "image/png", "image/gif", "image/webp",
    "application/pdf", "text/plain", "application/json",
    "text/csv", "text/markdown", "text/javascript", "application/javascript",
    "text/typescript", "application/typescript", "text/x-python", "text/x-java-source",
    "text/x-c", "text/x-c++src", "text/css", "text/xml", "application/xml", "text/html",
    "application/yaml", "text/yaml", "audio/mpeg", "audio/wav", "audio/ogg", "audio/webm",
    "audio/mp4", "video/mp4", "video/webm", "video/ogg",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.oasis.opendocument.text", "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.oasis.opendocument.presentation",
}


def file_metadata(file_record: File, version: FileVersion | None = None,
                  document: FileDocument | None = None) -> dict:
    """Expose immutable content identity, never internal storage paths."""
    return {
        "id": file_record.id,
        "channel_id": file_record.channel_id,
        "uploader_id": file_record.uploader_id,
        "filename": file_record.filename,
        "content_type": file_record.content_type,
        "size": file_record.size,
        # The native storage implementation writes plaintext, even for legacy
        # rows whose old encrypted flag incorrectly claimed otherwise.
        "encrypted": False,
        "created_at": file_record.created_at,
        "document_id": document.id if document else file_record.id,
        "version_number": version.number if version else 1,
        "version_count": document.version_count if document else 1,
        "document_owner_id": document.owner_id if document else file_record.uploader_id,
    }


def safe_filename(filename: str | None) -> str:
    name = (filename or "unnamed").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(character for character in name if ord(character) >= 32 and ord(character) != 127)
    return name if name not in {"", ".", ".."} else "unnamed"


def require_native_upload(session, channel_id: str):
    """Native storage may never accept plaintext for a secure channel."""
    if is_secure_channel(session, channel_id):
        raise HTTPException(409, "Use encrypted file transfer for this channel")


async def validated_content(file: UploadFile) -> bytes:
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(400, "File type not allowed")
    content = await file.read(MAX_FILE_SIZE + 1)
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(400, "File too large")
    return content


def find_file(session, file_id: str, user_id: str) -> File:
    record = session.get(File, file_id)
    if not record:
        raise HTTPException(404, "File not found")
    require_channel_member(session, record.channel_id, user_id)
    return record


def file_context(session, record: File):
    version = session.get(FileVersion, record.id)
    document = session.get(FileDocument, version.document_id) if version else None
    return version, document


def upload_fingerprint(channel_id: str, document_id: str | None, file: UploadFile, content: bytes):
    value = "\0".join([channel_id, document_id or "", safe_filename(file.filename), file.content_type or "",
                       hashlib.sha256(content).hexdigest()])
    return hashlib.sha256(value.encode()).hexdigest()


def acknowledged_upload(session, user_id: str, request_id, fingerprint: str):
    if not isinstance(request_id, str):
        return None
    receipt = session.get(FileUploadReceipt, (user_id, request_id))
    if not receipt:
        return None
    if receipt.fingerprint != fingerprint:
        raise HTTPException(409, "This upload request was already used for another file")
    record = session.get(File, receipt.file_id) if receipt.file_id else None
    if not record:
        raise HTTPException(409, "The previous upload is no longer available")
    version, document = file_context(session, record)
    return file_metadata(record, version, document)


@router.post("/channels/{channel_id}")
async def upload_file(channel_id: str, file: UploadFile = FastAPIFile(...),
                      user=Depends(get_current_user), session=Depends(get_session),
                      request_id: str | None = Header(None, alias="Idempotency-Key", pattern=r"^[A-Za-z0-9_-]{16,128}$")):
    actor_id = user.id
    require_channel_member(session, channel_id, actor_id)
    require_direct_message_writable(session, channel_id, actor_id)
    require_native_upload(session, channel_id)
    content = await validated_content(file)
    lock_channel_writes(session, channel_id)
    session.expire_all()
    recheck_actor(session, actor_id)
    require_channel_member(session, channel_id, actor_id)
    require_direct_message_writable(session, channel_id, actor_id)
    require_native_upload(session, channel_id)
    fingerprint = upload_fingerprint(channel_id, None, file, content)
    previous = acknowledged_upload(session, actor_id, request_id, fingerprint)
    if previous:
        return previous
    from ..services.enterprise_governance import enforce_native_file
    warnings = enforce_native_file(session, channel_id, actor_id, safe_filename(file.filename), file.content_type, content)
    file_id = uuid4().hex
    storage_path = await storage_service.save_file(file_id, content)
    record = File(id=file_id, channel_id=channel_id, uploader_id=actor_id,
                  filename=safe_filename(file.filename), content_type=file.content_type,
                  size=len(content), storage_path=storage_path, encrypted=False)
    document = FileDocument(id=file_id, channel_id=channel_id, owner_id=actor_id,
                            current_file_id=file_id)
    version = FileVersion(file_id=file_id, document_id=file_id, number=1)
    try:
        session.expire_all(); recheck_actor(session, actor_id)
        require_channel_member(session, channel_id, actor_id)
        require_direct_message_writable(session, channel_id, actor_id)
        require_native_upload(session, channel_id)
        session.add(record)
        session.flush()
        session.add(document)
        session.flush()
        session.add(version)
        if isinstance(request_id, str):
            session.add(FileUploadReceipt(user_id=actor_id, request_id=request_id, file_id=file_id, fingerprint=fingerprint))
        from ..services.webhooks import enqueue_channel_event
        enqueue_channel_event(session, channel_id, "file.created", record.id)
        session.commit()
    except IntegrityError:
        session.rollback()
        await storage_service.delete_file(file_id)
        previous = acknowledged_upload(session, actor_id, request_id, fingerprint)
        if previous:
            return previous
        raise HTTPException(409, "Another upload completed first. Refresh and try again.")
    except Exception:
        session.rollback()
        await storage_service.delete_file(file_id)
        raise
    return {**file_metadata(record, version, document), "policy_warnings": warnings}


@router.get("/channels/{channel_id}")
def list_files(channel_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    require_channel_member(session, channel_id, user.id)
    documents = {document.id: document for document in session.exec(
        select(FileDocument).where(FileDocument.channel_id == channel_id)).all()}
    records = session.exec(select(File).where(File.channel_id == channel_id)
                           .order_by(File.created_at.desc(), File.id.desc())).all()
    versions = {version.file_id: version for version in session.exec(
        select(FileVersion).join(File, File.id == FileVersion.file_id)
        .where(File.channel_id == channel_id)).all()}
    result = []
    for record in records:
        version = versions.get(record.id)
        document = documents.get(version.document_id) if version else None
        if document and document.current_file_id != record.id:
            continue
        if document and is_file_deleted(session, document.id):
            continue
        result.append(file_metadata(record, version, document))
    return result


@router.get("/{file_id}/versions")
def list_versions(file_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    record = find_file(session, file_id, user.id)
    _, document = file_context(session, record)
    if not document:
        return [file_metadata(record)]
    versions = session.exec(select(FileVersion).where(FileVersion.document_id == document.id)
                            .order_by(FileVersion.number.desc())).all()
    return [file_metadata(stored, version, document) for version in versions
            if (stored := session.get(File, version.file_id)) is not None]


@router.post("/{file_id}/versions")
async def upload_version(file_id: str, file: UploadFile = FastAPIFile(...),
                         user=Depends(get_current_user), session=Depends(get_session),
                         request_id: str | None = Header(None, alias="Idempotency-Key", pattern=r"^[A-Za-z0-9_-]{16,128}$")):
    actor_id = user.id
    record = find_file(session, file_id, actor_id)
    channel_id = record.channel_id
    require_direct_message_writable(session, channel_id, actor_id)
    require_native_upload(session, channel_id)
    _, document = file_context(session, record)
    owner_id = document.owner_id if document else record.uploader_id
    if owner_id != actor_id:
        raise HTTPException(403, "Only the document owner can upload a new version")
    content = await validated_content(file)
    lock_channel_writes(session, channel_id)
    session.expire_all()
    recheck_actor(session, actor_id)
    record = find_file(session, file_id, actor_id)
    require_native_upload(session, channel_id)
    require_direct_message_writable(session, channel_id, actor_id)
    _, document = file_context(session, record)
    if (document.owner_id if document else record.uploader_id) != actor_id:
        raise HTTPException(403, "Only the document owner can upload a new version")
    fingerprint = upload_fingerprint(record.channel_id, document.id if document else record.id, file, content)
    previous = acknowledged_upload(session, actor_id, request_id, fingerprint)
    if previous:
        return previous
    new_id = uuid4().hex
    from ..services.enterprise_governance import enforce_native_file
    warnings = enforce_native_file(session, channel_id, actor_id, safe_filename(file.filename), file.content_type, content)
    path = None
    try:
        if document:
            # PostgreSQL serializes allocation. Unique version numbers also
            # prevent SQLite/concurrent clients from overwriting history.
            document = session.exec(select(FileDocument).where(FileDocument.id == document.id)
                                    .with_for_update().execution_options(populate_existing=True)).one()
        else:
            document = FileDocument(id=record.id, channel_id=record.channel_id, owner_id=record.uploader_id,
                                    current_file_id=record.id, created_at=record.created_at)
            session.add(document)
            session.flush()
            session.add(FileVersion(file_id=record.id, document_id=document.id, number=1))
        path = await storage_service.save_file(new_id, content)
        session.expire_all(); recheck_actor(session, actor_id)
        require_channel_member(session, channel_id, actor_id)
        require_direct_message_writable(session, channel_id, actor_id)
        require_native_upload(session, channel_id)
        replacement = File(id=new_id, channel_id=record.channel_id, uploader_id=actor_id,
                           filename=safe_filename(file.filename), content_type=file.content_type,
                           size=len(content), storage_path=path, encrypted=False)
        version = FileVersion(file_id=new_id, document_id=document.id, number=document.version_count + 1)
        session.add(replacement)
        session.flush()
        session.add(version)
        if isinstance(request_id, str):
            session.add(FileUploadReceipt(user_id=actor_id, request_id=request_id, file_id=new_id, fingerprint=fingerprint))
        document.current_file_id = new_id
        document.version_count = version.number
        session.add(document)
        restore_synced_file(session, document.id)
        from ..services.webhooks import enqueue_channel_event
        enqueue_channel_event(session, record.channel_id, "file.version.created", replacement.id)
        session.commit()
    except IntegrityError:
        session.rollback()
        if path:
            await storage_service.delete_file(new_id)
        previous = acknowledged_upload(session, actor_id, request_id, fingerprint)
        if previous:
            return previous
        raise HTTPException(409, "Another version was just uploaded. Refresh and try again.")
    except Exception:
        session.rollback()
        if path:
            await storage_service.delete_file(new_id)
        raise
    return {**file_metadata(replacement, version, document), "policy_warnings": warnings}


@router.get("/{file_id}")
async def download_file(file_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    record = find_file(session, file_id, user.id)
    try:
        content = await run_in_threadpool(storage_service.read_file, record.id, MAX_FILE_SIZE, record.size)
    except (OSError, ValueError):
        raise HTTPException(404, "File not found")
    if content is None:
        raise HTTPException(404, "File not found")
    session.expire_all()
    recheck_actor(session, user.id)
    record = find_file(session, file_id, user.id)
    filename = safe_filename(record.filename)
    encoded = quote(filename)
    disposition = "attachment; filename*=UTF-8''" + encoded if encoded != filename else 'attachment; filename="' + filename + '"'
    return Response(content, media_type=record.content_type,
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store",
                 "Content-Disposition": disposition})


@router.delete("/{file_id}")
async def delete_file(file_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    record = find_file(session, file_id, user.id)
    channel_id = record.channel_id
    lock_channel_writes(session, channel_id)
    session.expire_all()
    recheck_actor(session, user.id)
    record = find_file(session, file_id, user.id)
    _, document = file_context(session, record)
    if (document.owner_id if document else record.uploader_id) != user.id:
        raise HTTPException(403, "Not the uploader")
    from ..services.enterprise_governance import assert_deletion_allowed
    preserved_authors = {record.uploader_id}
    if document:
        preserved_authors.add(document.owner_id)
        preserved_authors.update(session.exec(select(File.uploader_id).join(FileVersion, FileVersion.file_id == File.id)
            .where(FileVersion.document_id == document.id)).all())
    for author_id in sorted(preserved_authors):
        assert_deletion_allowed(session, channel_id, author_id=author_id)
    identity = document.id if document else record.id
    session.execute(delete(OfficeEditLock).where(OfficeEditLock.document_id == identity))
    session.execute(delete(OfficeEditSession).where(OfficeEditSession.channel_id == channel_id, OfficeEditSession.document_id == identity))
    if tombstone_synced_file(session, document):
        session.commit()
        return {"deleted": True, "retained_versions": True}
    if document:
        versions = session.exec(select(FileVersion).where(FileVersion.document_id == document.id)).all()
        records = [stored for version in versions if (stored := session.get(File, version.file_id))]
        for version in versions:
            session.delete(version)
        session.flush()
        session.delete(document)
        session.flush()
    else:
        records = [record]
    file_ids = [stored.id for stored in records]
    # Preserve cloud-import retry identities after erasure, and remove the
    # document discussion and its short-lived desktop edit capabilities.
    identity = document.id if document else record.id
    session.execute(update(CloudFileImport).where(CloudFileImport.file_id.in_(file_ids)).values(file_id=None))
    session.execute(delete(FileComment).where(FileComment.channel_id == channel_id, FileComment.document_id == identity))
    session.execute(delete(OfficeEditLock).where(OfficeEditLock.document_id == identity))
    session.execute(delete(OfficeEditSession).where(OfficeEditSession.channel_id == channel_id, OfficeEditSession.document_id == identity))
    receipts = session.exec(select(FileUploadReceipt).where(FileUploadReceipt.file_id.in_(file_ids))).all()
    for receipt in receipts:
        receipt.file_id = None
        session.add(receipt)
    session.flush()
    for stored in records:
        session.delete(stored)
    session.commit()
    for stored_id in file_ids:
        await storage_service.delete_file(stored_id)
    return {"deleted": True}
