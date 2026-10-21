"""Bounded passive OOXML extraction and scoped native Office edit authority."""
from datetime import datetime
from hashlib import sha256
import io
import json
import re
import secrets
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from fastapi import HTTPException
from sqlmodel import select

from ..models.file import File
from ..models.file_version import FileDocument, FileVersion
from ..models.file_experience import OfficeEditSession
from .access import lock_channel_writes, require_channel_member
from .auth import decode_token_claims, token_digest
from .matrix_bridge import is_secure_channel
from .remote_sync import is_file_deleted
from .request_guard import recheck_actor, remember_request
from .workspaces import require_direct_message_writable

OFFICE_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "ms-word",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "ms-excel",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "ms-powerpoint",
}
MAX_PREVIEW_FILE = 10 * 1024 * 1024
MAX_XML = 1024 * 1024
MAX_UNPACKED = 8 * 1024 * 1024
MAX_TEXT = 100000


def native_file(session, channel_id, file_id, actor_id, *, writing=False):
    require_channel_member(session, channel_id, actor_id)
    if is_secure_channel(session, channel_id):
        raise HTTPException(409, "Native file previews and Office editing are unavailable in encrypted channels")
    record = session.get(File, file_id, populate_existing=True)
    if record is None or record.channel_id != channel_id:
        raise HTTPException(404, "File not found")
    version = session.get(FileVersion, record.id)
    document = session.get(FileDocument, version.document_id, populate_existing=True) if version else None
    if document and is_file_deleted(session, document.id):
        raise HTTPException(404, "File no longer available")
    if writing:
        require_direct_message_writable(session, channel_id, actor_id)
        if (document.owner_id if document else record.uploader_id) != actor_id:
            raise HTTPException(403, "Only the document owner can edit it in Office")
    return record, document


def digest(value):
    return sha256(value.encode()).hexdigest()


def capability(session, identifier, secret, *, writing=False):
    if not re.fullmatch(r"[0-9a-f]{32}", identifier) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", secret):
        raise HTTPException(404, "Office session unavailable")
    row = session.get(OfficeEditSession, identifier, populate_existing=True)
    if row is None or not secrets.compare_digest(row.secret_hash, digest(secret)) or row.revoked_at or row.expires_at <= datetime.utcnow():
        raise HTTPException(404, "Office session unavailable or expired")
    claims = json.loads(row.claims_json)
    remember_request(session, row.native_session_hash, claims)
    recheck_actor(session, row.user_id)
    require_channel_member(session, row.channel_id, row.user_id)
    if writing:
        lock_channel_writes(session, row.channel_id)
        session.expire_all()
        row = session.get(OfficeEditSession, identifier)
        if row is None or row.revoked_at or row.expires_at <= datetime.utcnow():
            raise HTTPException(404, "Office session unavailable or expired")
        recheck_actor(session, row.user_id)
    record, document = native_file(session, row.channel_id, row.last_file_id, row.user_id, writing=True)
    if document is None or document.id != row.document_id:
        raise HTTPException(404, "Office document no longer available")
    head = session.get(File, document.current_file_id, populate_existing=True)
    if head is None or head.content_type != row.content_type:
        raise HTTPException(409, "The Office document type changed; reopen it from FreeCollab")
    return row, head, document


def saved_claims(token):
    claims = decode_token_claims(token)
    if not claims:
        raise HTTPException(401, "Sign in again before editing in Office")
    return token_digest(token), json.dumps({key: claims[key] for key in ("sub", "iat", "exp", "av", "amr", "sso_workspace_id", "sso_provider_revision", "native_client_id", "embed_application_id") if key in claims}, separators=(",", ":"))


def etag(record):
    return '"' + record.id + '"'


def bounded_file(record):
    from .storage import storage_service, StorageLimitError
    try:
        content = storage_service.read_file(record.id, max_bytes=MAX_PREVIEW_FILE, expected_size=record.size)
    except StorageLimitError:
        raise HTTPException(413, "Office preview and edit limit is 10 MiB") from None
    except (OSError, ValueError):
        raise HTTPException(404, "File content unavailable") from None
    if content is None:
        raise HTTPException(404, "File content unavailable")
    return content


def safe_xml(data):
    try:
        decoded = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(422, "Only UTF-8 Office XML is supported") from None
    if len(data) > MAX_XML or "<!DOCTYPE" in decoded.upper() or "<!ENTITY" in decoded.upper():
        raise HTTPException(422, "This XML is not safe for a bounded preview")
    try:
        return ET.fromstring(data)
    except (ET.ParseError, ValueError):
        raise HTTPException(422, "This Office document could not be parsed") from None


def office_preview(content, content_type):
    """No HTML, scripts, formulas, images, embedded files or relationships execute."""
    if content_type not in OFFICE_TYPES or len(content) > MAX_PREVIEW_FILE:
        raise HTTPException(422, "Use a supported OOXML document no larger than 10 MiB")
    try:
        with ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > 1000 or len({entry.filename for entry in entries}) != len(entries):
                raise HTTPException(422, "Office archive has too many or duplicate entries")
            total = 0
            for entry in entries:
                name = entry.filename.lower(); total += entry.file_size
                if (entry.flag_bits & 1 or name.startswith(("/", "\\")) or ".." in name.replace("\\", "/").split("/")
                        or "vbaproject" in name or name.endswith((".vba", ".bin")) or total > MAX_UNPACKED
                        or entry.file_size > MAX_XML or entry.file_size > max(entry.compress_size, 1) * 200):
                    raise HTTPException(422, "This Office archive exceeds safe passive preview limits")
            if "[Content_Types].xml" not in archive.namelist():
                raise HTTPException(422, "This is not an OOXML document")
            types = safe_xml(archive.read("[Content_Types].xml"))
            if any("macroenabled" in str(value).lower() or "vbaproject" in str(value).lower() for node in types.iter() for value in node.attrib.values()):
                raise HTTPException(422, "Macro-enabled Office documents cannot be previewed")
            blocks, truncated = [], False
            def add(text):
                nonlocal truncated
                if sum(len(value) for value in blocks) + len(text) > MAX_TEXT or len(blocks) >= 2000:
                    truncated = True; return
                if text: blocks.append(text)
            if content_type == next(iter(OFFICE_TYPES)):
                root = safe_xml(archive.read("word/document.xml"))
                for paragraph in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
                    add("".join(node.text or "" for node in paragraph.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")))
            elif OFFICE_TYPES[content_type] == "ms-powerpoint":
                names = sorted((name for name in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)), key=lambda name: int(re.search(r"(\d+)\.xml", name)[1]))
                for index, name in enumerate(names[:30]):
                    add("Slide " + str(index + 1))
                    root = safe_xml(archive.read(name))
                    for node in root.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}t"): add(node.text or "")
                truncated |= len(names) > 30
            else:
                ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
                strings = []
                if "xl/sharedStrings.xml" in archive.namelist():
                    for node in safe_xml(archive.read("xl/sharedStrings.xml")).iter(ns + "si"):
                        strings.append("".join(part.text or "" for part in node.iter(ns + "t")))
                if len(strings) > 10000:
                    raise HTTPException(422, "Too many spreadsheet strings")
                names = sorted(name for name in archive.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name))
                for name in names[:10]:
                    add(name.rsplit("/", 1)[-1]); root = safe_xml(archive.read(name))
                    rows = list(root.iter(ns + "row")); truncated |= len(rows) > 200
                    for row in rows[:200]:
                        cells = []
                        for cell in list(row)[:30]:
                            value = cell.find(ns + "v"); text = value.text or "" if value is not None else ""
                            if cell.attrib.get("t") == "s":
                                if not text.isdigit() or int(text) >= len(strings): raise HTTPException(422, "Invalid spreadsheet string index")
                                text = strings[int(text)]
                            elif cell.attrib.get("t") == "inlineStr": text = "".join(node.text or "" for node in cell.iter(ns + "t"))
                            cells.append(text)
                        add("\t".join(cells))
                truncated |= len(names) > 10
            return {"kind": "office-text", "blocks": blocks, "truncated": bool(truncated), "passive": True}
    except (BadZipFile, KeyError, RuntimeError, OSError):
        raise HTTPException(422, "This Office document is invalid or unsupported") from None
