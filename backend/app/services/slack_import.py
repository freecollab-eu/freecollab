"""Slack ZIP parser. Never extracts a path or fetches a remote attachment URL."""
import base64
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import logging
import re
import stat
from pathlib import PurePosixPath
from uuid import uuid4
import zipfile

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import select
from sqlalchemy import delete

from ..models.channel import Channel, ChannelMember
from ..models.file import File
from ..models.file_version import FileDocument, FileVersion
from ..models.message import Message
from ..models.slack_import import SlackImport, SlackImportMessage
from ..models.user import User
from ..models.workspace import WorkspaceChannel, WorkspaceMember
from .account_security import seal_private, unseal_private
from .crypto import encrypt_message
from .enterprise_governance import enforce_native_content, enforce_native_file
from .request_guard import recheck_actor
from .sso_scope import require_unrestricted
from .storage import storage_service
from .workspaces import lock_workspace, require_workspace_admin, require_workspace_member

MAX_ARCHIVE = 10 * 1024 * 1024
MAX_EXPANDED = 50 * 1024 * 1024
MAX_MESSAGES = 10000
MAX_ENTRIES = 2500
MAX_CHANNELS = 100
MAX_JSON = 8 * 1024 * 1024
ID = re.compile(r"^[A-Z][A-Z0-9]{0,31}$")
TIMESTAMP = re.compile(r"^[0-9]{1,12}\.[0-9]{1,6}$")
logger = logging.getLogger(__name__)


class Confirm(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    channel_ids: list[str] = Field(min_length=1, max_length=25)
    user_mapping: dict[str, str] = Field(default_factory=dict, max_length=250)
    channel_prefix: str = Field(default="slack-", min_length=1, max_length=30, pattern=r"^[\w -]+$")
    acknowledge_native_archive: bool


def pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def bad(detail="Invalid or unsupported Slack ZIP archive"):
    raise HTTPException(422, detail)


def stamp(value):
    if not isinstance(value, str) or not TIMESTAMP.fullmatch(value):
        bad("Each Slack message needs a unique valid timestamp")
    try:
        return datetime.utcfromtimestamp(float(Decimal(value)))
    except (ValueError, OverflowError, OSError, InvalidOperation):
        bad("Slack message timestamp is outside the supported range")


class Archive:
    def __init__(self, raw):
        if not raw or len(raw) > MAX_ARCHIVE:
            raise HTTPException(413, "Slack ZIP exceeds 10 MB")
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(raw))
            entries = self.zip.infolist()
            if not entries or len(entries) > MAX_ENTRIES:
                bad("Slack archive exceeds the entry bound")
            self.entries = {}
            total = 0
            for info in entries:
                path = PurePosixPath(info.filename)
                mode = info.external_attr >> 16
                if (not info.filename or "\\" in info.filename or path.is_absolute()
                        or any(part in {".", ".."} for part in info.filename.split("/"))
                        or len(info.filename) > 512 or len(path.parts) > 5
                        or info.flag_bits & 1 or stat.S_ISLNK(mode)
                        or (stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR})):
                    bad("Unsafe ZIP entry")
                if info.filename in self.entries:
                    bad("Duplicate ZIP path")
                total += info.file_size
                if info.file_size < 0 or total > MAX_EXPANDED or info.file_size > MAX_JSON and info.filename.endswith('.json'):
                    bad("Slack archive exceeds the expanded size bound")
                if info.file_size > max(1024 * 1024, info.compress_size * 200):
                    bad("Excessive ZIP compression ratio")
                self.entries[info.filename] = info
            self.users = self.read_json("users.json")
            channels = self.read_json("channels.json")
            if not isinstance(self.users, list) or len(self.users) > 5000 or not isinstance(channels, list) or len(channels) > MAX_CHANNELS:
                bad("Slack export requires bounded users.json and channels.json lists")
            self.labels = {}
            for user in self.users:
                if not isinstance(user, dict) or not ID.fullmatch(str(user.get("id", ""))) or user["id"] in self.labels:
                    bad("Invalid or duplicate Slack user")
                profile = user.get("profile") or {}
                if not isinstance(profile, dict): bad()
                label = profile.get("display_name") or profile.get("real_name") or user.get("real_name") or user.get("name") or user["id"]
                if not isinstance(label, str) or len(label) > 200: bad()
                self.labels[user["id"]] = label
            self.channels = {}
            self.messages = {}
            self.file_count = 0
            self.missing_files = 0
            referenced_bytes = 0
            count = 0
            for channel in channels:
                if not isinstance(channel, dict): bad()
                identifier, name = channel.get("id"), channel.get("name")
                if (not isinstance(identifier, str) or not ID.fullmatch(identifier) or identifier in self.channels
                        or not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", name)):
                    bad("Invalid or duplicate Slack channel")
                members = channel.get("members", [])
                if not isinstance(members, list) or len(members) > 5000 or any(not isinstance(u, str) or u not in self.labels for u in members):
                    bad("Invalid Slack channel roster")
                self.channels[identifier] = {"id": identifier, "name": name, "members": members}
                records = []
                seen = set()
                for path in sorted(self.entries):
                    if not re.fullmatch(re.escape(name) + r"/\d{4}-\d{2}-\d{2}\.json", path): continue
                    rows = self.read_json(path)
                    if not isinstance(rows, list): bad()
                    for row in rows:
                        if not isinstance(row, dict): bad()
                        # Membership/edit system events cannot become impersonated messages.
                        if row.get("type") != "message" or row.get("subtype") not in {None, "bot_message", "file_share", "thread_broadcast"}: continue
                        ts = row.get("ts"); stamp(ts)
                        if ts in seen: bad("Duplicate Slack message timestamp")
                        seen.add(ts)
                        text = row.get("text", "")
                        if not isinstance(text, str) or len(text) > 18000: bad("Slack message is too large")
                        author = row.get("user") or row.get("bot_id") or "unknown"
                        if not isinstance(author, str) or len(author) > 80: bad()
                        parent = row.get("thread_ts")
                        if parent is not None: stamp(parent)
                        files = row.get("files") or []
                        if not isinstance(files, list) or len(files) > 20: bad()
                        bundled = []
                        missing = 0
                        for file in files:
                            if not isinstance(file, dict): bad()
                            fid = file.get("id", "")
                            if not isinstance(fid, str) or not ID.fullmatch(fid): bad("Invalid Slack file identifier")
                            # Optional portable-export convention, exact immutable Slack ID.
                            path = "files/" + fid
                            if path in self.entries and not self.entries[path].is_dir():
                                mime = file.get("mimetype", "application/octet-stream")
                                from ..api.files import ALLOWED_TYPES
                                if mime not in ALLOWED_TYPES: bad("Bundled file type is not supported")
                                filename = file.get("name", fid)
                                if not isinstance(filename, str) or len(filename) > 255: bad()
                                bundled.append({"path": path, "name": filename, "mime": mime})
                                self.file_count += 1
                                referenced_bytes += self.entries[path].file_size
                                if self.file_count > 200 or referenced_bytes > MAX_EXPANDED:
                                    bad("Archive exceeds 200 file references or 50 MB of referenced files")
                            else: missing += 1; self.missing_files += 1
                        if not text.strip() and not files: continue
                        records.append({"ts": ts, "text": text, "author": author, "parent": parent,
                                        "files": bundled, "missing": missing})
                        count += 1
                        if count > MAX_MESSAGES: bad("Slack archive exceeds 10,000 messages")
                by_timestamp = {entry["ts"]: entry for entry in records}
                for entry in records:
                    parent = entry["parent"]
                    if parent and parent != entry["ts"] and parent in by_timestamp:
                        # Slack roots may carry their own thread_ts. Replies
                        # cannot become parents of nested or cyclic threads.
                        root = by_timestamp[parent]
                        if root["parent"] not in {None, parent}:
                            bad("Slack thread replies must refer to a root message")
                self.messages[identifier] = sorted(records, key=lambda r: Decimal(r["ts"]))
            # Two Slack channels must not alias the same message directory.
            if len({c["name"] for c in self.channels.values()}) != len(self.channels): bad("Duplicate Slack channel directory")
        except HTTPException:
            raise
        except (zipfile.BadZipFile, RuntimeError, ValueError, KeyError, TypeError, UnicodeError, RecursionError, NotImplementedError):
            bad()

    def read(self, path):
        info = self.entries.get(path)
        if not info: bad("Slack archive is missing required files")
        try:
            with self.zip.open(info) as source:
                raw = source.read(info.file_size + 1)
            if len(raw) != info.file_size: bad("ZIP entry changed")
            return raw
        except (zipfile.BadZipFile, RuntimeError, ValueError, EOFError): bad()

    def read_json(self, path):
        return json.loads(self.read(path), object_pairs_hook=pairs,
                          parse_constant=lambda _: bad("Invalid JSON number"))

    def preview(self):
        return {"channels": [{**{k: c[k] for k in ("id", "name")}, "member_ids": c["members"],
                             "message_count": len(self.messages[c["id"]])} for c in self.channels.values()],
                "users": [{"id": uid, "name": name} for uid, name in self.labels.items()],
                "bundled_files": self.file_count, "missing_files": self.missing_files,
                "native_archive": True, "remote_fetches": False}


def authorize(session, workspace_id, user_id, *, locked=False):
    if locked:
        lock_workspace(session, workspace_id)
        session.expire_all()
    recheck_actor(session, user_id)
    require_unrestricted(session)
    require_workspace_admin(session, workspace_id, user_id)


def create_preview(session, workspace_id, actor_id, raw):
    authorize(session, workspace_id, actor_id, locked=True)
    archive = Archive(raw)
    digest = hashlib.sha256(raw).hexdigest()
    old = session.exec(select(SlackImport).where(SlackImport.workspace_id == workspace_id, SlackImport.archive_digest == digest)).first()
    if old:
        if old.actor_id != actor_id: raise HTTPException(409, "This archive already belongs to another import")
        if old.status == "completed": return {"id": old.id, "status": old.status, "result": json.loads(old.result_json)}
        old.status = "preview"
        old.encrypted_archive = seal_private(base64.b64encode(raw).decode(), "slack-archive", old.id)
        old.expires_at = datetime.utcnow() + timedelta(hours=1)
        session.add(old)
        return {"id": old.id, "status": old.status, **archive.preview()}
    row = SlackImport(workspace_id=workspace_id, actor_id=actor_id, archive_digest=digest,
                      encrypted_archive="", expires_at=datetime.utcnow()+timedelta(hours=1))
    row.encrypted_archive = seal_private(base64.b64encode(raw).decode(), "slack-archive", row.id)
    session.add(row)
    return {"id": row.id, "status": row.status, **archive.preview()}


def expire_previews(session, limit=10):
    rows = session.exec(select(SlackImport).where(SlackImport.status == "preview", SlackImport.expires_at <= datetime.utcnow())
                        .limit(min(max(limit, 1), 25)).with_for_update()).all()
    for row in rows:
        row.encrypted_archive, row.status = "", "expired"
        session.add(row)
    session.commit()
    return len(rows)


def close_account_resources(session, user_id):
    """Erase private preview copies; keep only provenance used by shared content."""
    referenced = select(SlackImportMessage.import_id)
    rows = session.exec(select(SlackImport).where(SlackImport.actor_id == user_id)).all()
    for row in rows:
        row.encrypted_archive = ""
        if row.status != "completed": row.status = "discarded"
        session.add(row)
    session.flush()
    session.execute(delete(SlackImport).where(SlackImport.actor_id == user_id,
        SlackImport.id.not_in(referenced)))


def import_archive(session, workspace_id, actor_id, identifier, data):
    authorize(session, workspace_id, actor_id, locked=True)
    row = session.get(SlackImport, identifier, populate_existing=True)
    if not row or row.workspace_id != workspace_id or row.actor_id != actor_id:
        raise HTTPException(404, "Slack import not found")
    fingerprint = hashlib.sha256(json.dumps(data.model_dump(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if row.status == "completed":
        if row.request_digest != fingerprint: raise HTTPException(409, "This import already completed with different choices")
        return json.loads(row.result_json)
    if row.expires_at <= datetime.utcnow() or row.status != "preview":
        raise HTTPException(409, "Upload this archive again to refresh the preview")
    if not data.acknowledge_native_archive or len(set(data.channel_ids)) != len(data.channel_ids):
        raise HTTPException(422, "Review native storage and choose unique channels")
    archive = Archive(base64.b64decode(unseal_private(row.encrypted_archive, "slack-archive", row.id), validate=True))
    if not set(data.channel_ids) <= archive.channels.keys() or not data.user_mapping.keys() <= archive.labels.keys():
        raise HTTPException(422, "Choose channels and users from this archive")
    def validate_mapping():
        for uid in set(data.user_mapping.values()):
            member = require_workspace_member(session, workspace_id, uid)
            person = session.get(User, uid, populate_existing=True)
            if member.role == "guest" or not person or person.is_blocked:
                raise HTTPException(422, "Map only active non-guest workspace members")
    validate_mapping()
    # Files are not transactionally stored in SQL. Track every newly owned blob
    # and erase them on rollback, including a failure of the final SQL commit.
    blobs = []
    result = {"channels": [], "messages": 0, "files": 0, "missing_files": 0}
    try:
        for cid in data.channel_ids:
            source = archive.channels[cid]
            channel_id = uuid4().hex
            channel = Channel(id=channel_id, name=(data.channel_prefix + source["name"])[:100], type="group")
            session.add(channel); session.flush()
            session.add(WorkspaceChannel(channel_id=channel_id, workspace_id=workspace_id)); session.flush()
            members = {actor_id} | {data.user_mapping[uid] for uid in source["members"] if uid in data.user_mapping}
            for uid in members:
                session.add(ChannelMember(channel_id=channel_id, user_id=uid, role="admin" if uid == actor_id else "member"))
            session.flush()
            messages = archive.messages[cid]
            identities = {entry["ts"]: uuid4().hex for entry in messages}
            rows = {}
            # Insert parent rows first so foreign keys remain valid on PostgreSQL.
            for entry in messages:
                label = archive.labels.get(entry["author"], entry["author"])
                text = "[Imported from Slack · " + label + " · " + entry["ts"] + "]\n" + entry["text"]
                if entry["missing"]: text += "\n[" + str(entry["missing"]) + " original attachment(s) unavailable in this archive]"
                enforce_native_content(session, channel_id, actor_id, text)
                message = Message(id=identities[entry["ts"]], channel_id=channel_id, author_id=actor_id,
                    content=encrypt_message(text, channel_id), encrypted=True, created_at=stamp(entry["ts"]))
                session.add(message); session.flush(); rows[entry["ts"]] = message
                session.add(SlackImportMessage(message_id=message.id, import_id=row.id, source_channel=cid, source_timestamp=entry["ts"]))
                result["messages"] += 1; result["missing_files"] += entry["missing"]
                for file in entry["files"]:
                    from ..api.files import safe_filename
                    content = archive.read(file["path"])
                    filename = safe_filename(file["name"])
                    enforce_native_file(session, channel_id, actor_id, filename, file["mime"], content)
                    file_id = uuid4().hex
                    # An object store may accept bytes but lose its response.
                    # Track this fresh ID before I/O so rollback can erase that
                    # uncertain write. A conditional collision is not our blob.
                    blobs.append(file_id)
                    try:
                        location = storage_service.save_file_sync(file_id, content)
                    except FileExistsError:
                        blobs.pop()
                        raise
                    record = File(id=file_id, channel_id=channel_id, uploader_id=actor_id, filename=filename,
                        content_type=file["mime"], size=len(content), storage_path=location, encrypted=False, created_at=stamp(entry["ts"]))
                    session.add(record); session.flush()
                    session.add(FileDocument(id=file_id, channel_id=channel_id, owner_id=actor_id, current_file_id=file_id)); session.flush()
                    session.add(FileVersion(file_id=file_id, document_id=file_id, number=1))
                    result["files"] += 1
            for entry in messages:
                parent = entry["parent"]
                if parent and parent != entry["ts"] and parent in rows:
                    rows[entry["ts"]].parent_id = rows[parent].id
                    rows[parent].thread_count += 1
            result["channels"].append({"id": channel_id, "name": channel.name, "source_id": cid})
        session.flush()
        # Repeat signed authority after file I/O. No webhook, mention, workflow
        # or email events are generated for historical content.
        session.expire_all(); authorize(session, workspace_id, actor_id)
        validate_mapping()
        row = session.get(SlackImport, identifier)
        row.status, row.encrypted_archive, row.request_digest = "completed", "", fingerprint
        row.result_json = json.dumps(result, separators=(",", ":")); session.add(row)
        session.commit()
        return result
    except BaseException:
        try:
            session.rollback()
        finally:
            for file_id in blobs:
                try:
                    storage_service.delete_file_sync(file_id)
                except Exception:
                    # Continue every independent erasure and preserve the
                    # original import failure. IDs are opaque; never log names,
                    # bytes, provider response bodies or archive credentials.
                    logger.error("Slack import rollback could not erase owned blob %s; operator reconciliation required", file_id)
        raise
