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
from ..models.slack_import import SlackImport, SlackImportMessage, SlackImportDetail
from ..models.user import User
from ..models.workspace import WorkspaceChannel, WorkspaceMember
from .account_security import seal_private, unseal_private
from .archive_limits import bounded_zip_directory as checked_zip_directory
from .crypto import encrypt_message, decrypt_message
from .enterprise_governance import enforce_native_content, enforce_native_file
from .request_guard import recheck_actor
from .sso_scope import require_unrestricted
from .storage import storage_service
from .workspaces import lock_workspace, require_workspace_admin, require_workspace_member

MAX_ARCHIVE = 50 * 1024 * 1024
MAX_EXPANDED = 100 * 1024 * 1024
MAX_MESSAGES = 100000
MAX_ENTRIES = 10000
MAX_CHANNELS = 1000
MAX_JSON = 8 * 1024 * 1024
ID = re.compile(r"^[A-Z][A-Z0-9]{0,31}$")
TIMESTAMP = re.compile(r"^[0-9]{1,12}\.[0-9]{1,6}$")
logger = logging.getLogger(__name__)


class Confirm(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    channel_ids: list[str] = Field(min_length=1, max_length=100)
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


def historical_details(row, labels):
    """Retain export statements without manufacturing current-user actions."""
    def person(identifier):
        if not isinstance(identifier, str) or not identifier or len(identifier) > 80: bad("Invalid historical author")
        return {"id": identifier, "name": labels.get(identifier, identifier)}
    author = person(row.get("user") or row.get("bot_id") or "unknown")
    edited = row.get("edited")
    edited_at = None
    if edited is not None:
        if not isinstance(edited, dict): bad("Invalid Slack edit metadata")
        edited_at = edited.get("ts"); stamp(edited_at)
    history = row.get("edit_history", row.get("history", []))
    if not isinstance(history, list) or len(history) > 25: bad("Historical edits exceed the per-message bound")
    if row.get("previous_message") is not None:
        history = [*history, row["previous_message"]]
    revisions, seen = [], set()
    for previous in history:
        if not isinstance(previous, dict): bad("Invalid historical revision")
        text = previous.get("text", "")
        ts = previous.get("edited", {}).get("ts") if isinstance(previous.get("edited"), dict) else None
        ts = ts or previous.get("ts")
        stamp(ts)
        if not isinstance(text, str) or len(text) > 18000: bad("Historical revision is too large")
        identity = (ts, text)
        if identity in seen: continue
        seen.add(identity)
        revisions.append({"timestamp": ts, "text": text, "author": person(previous.get("user") or author["id"])})
    if len(revisions) > 25: bad("Historical edits exceed the per-message bound")
    reactions = row.get("reactions", [])
    if not isinstance(reactions, list) or len(reactions) > 100: bad("Historical reactions exceed the per-message bound")
    parsed, names = [], set()
    for reaction in reactions:
        if not isinstance(reaction, dict): bad("Invalid historical reaction")
        name, users, count = reaction.get("name"), reaction.get("users", []), reaction.get("count")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_+\-]{1,100}", name) or name in names: bad("Invalid or duplicate historical reaction")
        if not isinstance(users, list) or len(users) > 5000 or len(set(users)) != len(users): bad("Invalid historical reactors")
        people = [person(identifier) for identifier in users]
        if not isinstance(count, int) or isinstance(count, bool) or count < len(people) or count > 1000000: bad("Invalid historical reaction count")
        names.add(name); parsed.append({"name": name, "count": count, "users": people})
    result = {"version": 1, "source": "slack", "source_timestamp": row["ts"], "current_text": row.get("text", ""), "author": author,
              "edited_at": edited_at, "history": sorted(revisions, key=lambda item: Decimal(item["timestamp"])), "reactions": parsed}
    if len(json.dumps(result, ensure_ascii=False).encode()) > 128 * 1024: bad("Historical message metadata exceeds 128 KB")
    return result


def bounded_zip_directory(raw):
    """Scan actual directory records before ZipFile can allocate ZipInfo objects."""
    try:
        return checked_zip_directory(raw, max_entries=MAX_ENTRIES, label="Slack archive")
    except ValueError as error:
        bad(str(error))


class Archive:
    def __init__(self, raw):
        if not raw or len(raw) > MAX_ARCHIVE:
            raise HTTPException(413, "Slack ZIP exceeds 50 MB")
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(bounded_zip_directory(raw)))
            entries = self.zip.infolist()
            if not entries or len(entries) > MAX_ENTRIES:
                bad("Slack archive exceeds the entry bound")
            self.entries = {}
            total = 0
            for info in entries:
                path = PurePosixPath(info.filename)
                mode = info.external_attr >> 16
                if (not info.filename or re.search(r"[\x00-\x1f\x7f]", info.orig_filename) or "\\" in info.filename or path.is_absolute()
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
                seen = {}
                for path in sorted(self.entries):
                    if not re.fullmatch(re.escape(name) + r"/\d{4}-\d{2}-\d{2}\.json", path): continue
                    rows = self.read_json(path)
                    if not isinstance(rows, list): bad()
                    for row in rows:
                        if not isinstance(row, dict): bad()
                        # Edit envelopes retain historical snapshots, never impersonated live edits.
                        changed = row.get("type") == "message" and row.get("subtype") == "message_changed"
                        if changed:
                            envelope = row
                            row = envelope.get("message")
                            if not isinstance(row, dict): bad("Invalid Slack edit envelope")
                            row = {**row, "type": "message"}
                            previous = envelope.get("previous_message")
                            if previous is not None:
                                if not isinstance(previous, dict) or previous.get("ts") != row.get("ts"): bad("Slack edit targets another message")
                                row["previous_message"] = previous
                        if row.get("type") != "message" or row.get("subtype") not in {None, "bot_message", "file_share", "thread_broadcast"}: continue
                        ts = row.get("ts"); stamp(ts)
                        old = seen.get(ts)
                        if old:
                            if not changed: bad("Duplicate Slack message timestamp")
                            old_history = [{"ts": item["timestamp"], "text": item["text"], "user": item["author"]["id"]} for item in old["details"]["history"]]
                            old_history.append({"ts": old["details"]["edited_at"] or ts, "text": old["text"], "user": old["author"]})
                            row["edit_history"] = [*old_history, *row.get("edit_history", [])]
                            row.setdefault("thread_ts", old["parent"])
                            records.remove(old)
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
                                if self.file_count > 2000 or referenced_bytes > MAX_EXPANDED:
                                    bad("Archive exceeds 2,000 file references or 100 MB of referenced files")
                            else: missing += 1; self.missing_files += 1
                        if not text.strip() and not files: continue
                        record = {"ts": ts, "text": text, "author": author, "parent": parent,
                                  "files": bundled, "missing": missing, "details": historical_details(row, self.labels)}
                        records.append(record); seen[ts] = record
                        count += 0 if old else 1
                        if count > MAX_MESSAGES: bad("Slack archive exceeds 100,000 messages")
                by_timestamp = {entry["ts"]: entry for entry in records}
                for entry in records:
                    parent = entry["parent"]
                    if parent and parent != entry["ts"] and parent in by_timestamp:
                        # Slack roots may carry their own thread_ts. Replies
                        # cannot become parents of nested or cyclic threads.
                        root = by_timestamp[parent]
                        if root["parent"] not in {None, parent} or Decimal(parent) > Decimal(entry["ts"]):
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
                "native_archive": True, "remote_fetches": False, "resumable": True, "limits": {"archive_bytes": MAX_ARCHIVE, "expanded_bytes": MAX_EXPANDED, "messages": MAX_MESSAGES, "batch_messages": 250}}


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
    if old and old.status != "completed":
        pending = session.exec(select(SlackImport.id).where(SlackImport.actor_id == actor_id, SlackImport.id != old.id, SlackImport.encrypted_archive != "", SlackImport.expires_at > datetime.utcnow(), SlackImport.status.in_(["preview", "in_progress"]))).all()
        if len(pending) >= 3: raise HTTPException(429, "Finish or discard an existing archive before another upload")
    if old:
        if old.actor_id != actor_id: raise HTTPException(409, "This archive already belongs to another import")
        if old.status == "completed": return {"id": old.id, "status": old.status, "result": json.loads(old.result_json)}
        if old.status == "paused": old.status = "in_progress"
        elif old.status != "in_progress": old.status = "preview"
        old.encrypted_archive = seal_private(base64.b64encode(raw).decode(), "slack-archive", old.id)
        old.expires_at = datetime.utcnow() + timedelta(hours=1)
        session.add(old)
        return {"id": old.id, "status": old.status, **archive.preview(), **({"progress": resume_status(old)} if old.status == "in_progress" else {})}
    pending = session.exec(select(SlackImport.id).where(SlackImport.actor_id == actor_id, SlackImport.encrypted_archive != "", SlackImport.expires_at > datetime.utcnow(), SlackImport.status.in_(["preview", "in_progress"]))).all()
    if len(pending) >= 3: raise HTTPException(429, "Finish or discard an existing archive before another upload")
    row = SlackImport(workspace_id=workspace_id, actor_id=actor_id, archive_digest=digest,
                      encrypted_archive="", expires_at=datetime.utcnow()+timedelta(hours=1))
    row.encrypted_archive = seal_private(base64.b64encode(raw).decode(), "slack-archive", row.id)
    session.add(row)
    return {"id": row.id, "status": row.status, **archive.preview()}


def expire_previews(session, limit=10):
    rows = session.exec(select(SlackImport).where(SlackImport.status.in_(["preview", "in_progress"]), SlackImport.expires_at <= datetime.utcnow())
                        .limit(min(max(limit, 1), 25)).with_for_update()).all()
    for row in rows:
        was_started = row.status == "in_progress"
        row.encrypted_archive, row.status = "", "paused" if was_started else "expired"
        if not was_started: row.result_json = None
        session.add(row)
    session.commit()
    return len(rows)


def close_account_resources(session, user_id):
    """Erase private preview copies; keep only provenance used by shared content."""
    referenced = select(SlackImportMessage.import_id)
    rows = session.exec(select(SlackImport).where(SlackImport.actor_id == user_id)).all()
    for row in rows:
        row.encrypted_archive = ""
        if row.status != "completed":
            row.status = "discarded"
            row.result_json = None
        session.add(row)
    session.flush()
    session.execute(delete(SlackImport).where(SlackImport.actor_id == user_id,
        SlackImport.id.not_in(referenced)))


def write_native_batch(session, archive, cid, actor_id, channel_id, import_id, messages, result, blobs):
    identities = {entry["ts"]: uuid4().hex for entry in messages}
    rows = {}
    # Insert parent rows first so foreign keys remain valid on PostgreSQL.
    for entry in messages:
        label = archive.labels.get(entry["author"], entry["author"])
        text = "[Imported from Slack · " + label + " · " + entry["ts"] + "]\n" + entry["text"]
        if entry["missing"]: text += "\n[" + str(entry["missing"]) + " original attachment(s) unavailable in this archive]"
        enforce_native_content(session, channel_id, actor_id, text + "\n" + json.dumps(entry["details"], ensure_ascii=False))
        message = Message(id=identities[entry["ts"]], channel_id=channel_id, author_id=actor_id,
            content=encrypt_message(text, channel_id), encrypted=True, created_at=stamp(entry["ts"]),
            edited_at=stamp(entry["details"]["edited_at"]) if entry["details"]["edited_at"] else None)
        session.add(message); session.flush(); rows[entry["ts"]] = message
        session.add(SlackImportMessage(message_id=message.id, import_id=import_id, source_channel=cid, source_timestamp=entry["ts"]))
        session.add(SlackImportDetail(message_id=message.id, encrypted_details=encrypt_message(json.dumps(entry["details"], ensure_ascii=False), channel_id)))
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
        if parent and parent != entry["ts"]:
            root = rows.get(parent)
            if root is None:
                link = session.exec(select(SlackImportMessage).join(Message, Message.id == SlackImportMessage.message_id).where(SlackImportMessage.import_id == import_id,
                    SlackImportMessage.source_channel == cid, SlackImportMessage.source_timestamp == parent, Message.channel_id == channel_id)).first()
                root = session.get(Message, link.message_id) if link else None
            if root:
                rows[entry["ts"]].parent_id = root.id
                root.thread_count += 1


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
            write_native_batch(session, archive, cid, actor_id, channel_id, row.id, archive.messages[cid], result, blobs)
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


class ResumeStep(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_sequence: int = Field(ge=0, le=1000000)


class SecureReserve(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    archive_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    options_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    member_ids: list[str] = Field(default_factory=list, max_length=100)


def _job(session, workspace_id, actor_id, identifier):
    authorize(session, workspace_id, actor_id, locked=True)
    row = session.get(SlackImport, identifier, populate_existing=True)
    if not row or row.workspace_id != workspace_id or row.actor_id != actor_id:
        raise HTTPException(404, "Slack import not found")
    return row


def _mapping(session, workspace_id, user_ids):
    for uid in set(user_ids):
        member = require_workspace_member(session, workspace_id, uid)
        person = session.get(User, uid, populate_existing=True)
        if member.role == "guest" or not person or person.is_blocked:
            raise HTTPException(422, "Map only active non-guest workspace members")


def _state(row):
    value = json.loads(row.result_json)
    return json.loads(unseal_private(value["private_state"], "slack-resume", row.id)), value["result"]


def _save_state(row, state, result):
    row.result_json = json.dumps({"private_state": seal_private(json.dumps(state, separators=(",", ":")), "slack-resume", row.id),
                                 "result": result}, separators=(",", ":"))


def resume_status(row):
    if row.status == "completed":
        result = json.loads(row.result_json)
        return {"id": row.id, "status": row.status, "sequence": None, "result": result}
    if row.status not in {"in_progress", "paused"}:
        return {"id": row.id, "status": row.status, "sequence": 0}
    state, result = _state(row)
    return {"id": row.id, "status": row.status, "sequence": state["sequence"], "result": result,
            "processed": result["messages"], "total": state["total"], "channel_index": state["channel_index"],
            "source_digest": row.archive_digest, "request_digest": row.request_digest}


def begin_resume(session, workspace_id, actor_id, identifier, data):
    row = _job(session, workspace_id, actor_id, identifier)
    digest = hashlib.sha256(json.dumps(data.model_dump(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if row.status in {"in_progress", "paused", "completed"}:
        if row.request_digest != digest: raise HTTPException(409, "This migration already has different reviewed choices")
        return resume_status(row)
    if row.status != "preview" or row.expires_at <= datetime.utcnow():
        raise HTTPException(409, "Upload this exact archive again to continue")
    if not data.acknowledge_native_archive or len(set(data.channel_ids)) != len(data.channel_ids):
        raise HTTPException(422, "Review native storage and choose unique channels")
    archive = Archive(base64.b64decode(unseal_private(row.encrypted_archive, "slack-archive", row.id), validate=True))
    if not set(data.channel_ids) <= archive.channels.keys() or not data.user_mapping.keys() <= archive.labels.keys():
        raise HTTPException(422, "Choose channels and users from this archive")
    _mapping(session, workspace_id, data.user_mapping.values())
    state = {"options": data.model_dump(), "sequence": 0, "channel_index": 0, "message_offset": 0,
             "total": sum(len(archive.messages[cid]) for cid in data.channel_ids)}
    result = {"channels": [], "messages": 0, "files": 0, "missing_files": 0}
    row.status, row.request_digest = "in_progress", digest
    _save_state(row, state, result); session.add(row); session.commit()
    return resume_status(row)


def resume_batch(session, workspace_id, actor_id, identifier, expected_sequence):
    row = _job(session, workspace_id, actor_id, identifier)
    if row.status == "completed": return resume_status(row)
    if row.status != "in_progress" or row.expires_at <= datetime.utcnow():
        raise HTTPException(409, "Upload this exact archive again to continue")
    state, result = _state(row)
    if expected_sequence < state["sequence"]: return resume_status(row)  # lost acknowledgement: never replay a committed batch
    if expected_sequence != state["sequence"]: raise HTTPException(409, "Refresh migration progress before continuing")
    options = Confirm.model_validate(state["options"])
    _mapping(session, workspace_id, options.user_mapping.values())
    archive = Archive(base64.b64decode(unseal_private(row.encrypted_archive, "slack-archive", row.id), validate=True))
    cid = options.channel_ids[state["channel_index"]]
    source = archive.channels[cid]
    channel = None
    prior = next((item for item in result["channels"] if item["source_id"] == cid), None)
    if prior:
        from .access import lock_channel_writes
        lock_channel_writes(session, prior["id"])
        channel = session.get(Channel, prior["id"], populate_existing=True)
        member = session.get(ChannelMember, (prior["id"], actor_id), populate_existing=True)
        from ..models.secure_channel import SecureChannel
        mapping = session.get(WorkspaceChannel, prior["id"])
        if not channel or not mapping or mapping.workspace_id != workspace_id or not member or member.role != "admin" or session.get(SecureChannel, prior["id"]):
            raise HTTPException(409, "The destination channel changed; this migration is paused")
        expected_members = {actor_id} | {options.user_mapping[uid] for uid in source["members"] if uid in options.user_mapping}
        current_members = set(session.exec(select(ChannelMember.user_id).where(ChannelMember.channel_id == channel.id)).all())
        if current_members != expected_members: raise HTTPException(409, "Review the changed destination membership before continuing")
    else:
        channel = Channel(id=uuid4().hex, name=(options.channel_prefix + source["name"])[:100], type="group")
        session.add(channel); session.flush()
        session.add(WorkspaceChannel(channel_id=channel.id, workspace_id=workspace_id)); session.flush()
        members = {actor_id} | {options.user_mapping[uid] for uid in source["members"] if uid in options.user_mapping}
        for uid in members: session.add(ChannelMember(channel_id=channel.id, user_id=uid, role="admin" if uid == actor_id else "member"))
        session.flush(); result["channels"].append({"id": channel.id, "name": channel.name, "source_id": cid})
    blobs = []
    try:
        messages = archive.messages[cid][state["message_offset"]:state["message_offset"]+250]
        write_native_batch(session, archive, cid, actor_id, channel.id, row.id, messages, result, blobs)
        state["message_offset"] += len(messages)
        if state["message_offset"] >= len(archive.messages[cid]):
            state["channel_index"] += 1; state["message_offset"] = 0
        state["sequence"] += 1
        session.flush(); session.expire_all(); authorize(session, workspace_id, actor_id)
        _mapping(session, workspace_id, options.user_mapping.values())
        row = session.get(SlackImport, identifier)
        if state["channel_index"] == len(options.channel_ids):
            row.status, row.encrypted_archive = "completed", ""
            row.result_json = json.dumps(result, separators=(",", ":"))
        else:
            row.expires_at = datetime.utcnow() + timedelta(hours=1)
            _save_state(row, state, result)
        session.add(row); session.commit()
        return resume_status(row)
    except BaseException:
        try: session.rollback()
        finally:
            for file_id in blobs:
                try: storage_service.delete_file_sync(file_id)
                except Exception: logger.error("Slack import rollback could not erase owned blob %s; operator reconciliation required", file_id)
        raise


def _secure_origin(session, actor_id, auth_digest):
    """Bind opaque reservations to the real original sign-in and Matrix device."""
    from ..models.account_security import AccountSecurity
    from ..models.secure_channel import MatrixSession, MatrixDeviceRevocation
    from ..models.session import ActiveSession, RevokedToken
    from .matrix_bridge import lock_device_accounts
    lock_device_accounts(session, [actor_id])
    session.expire_all()
    recheck_actor(session, actor_id)
    sources = session.exec(select(MatrixSession).where(MatrixSession.user_id == actor_id, MatrixSession.auth_token_hash == auth_digest).limit(2)).all()
    current = sources[0] if len(sources) == 1 else None
    native = session.exec(select(ActiveSession).where(ActiveSession.token_hash == auth_digest)).first()
    security = session.get(AccountSecurity, actor_id)
    if (not current or current.auth_expires_at <= datetime.utcnow() or not native or native.revoked_at or native.expires_at <= datetime.utcnow()
            or (security.auth_version if security else 0) != current.auth_version
            or session.get(RevokedToken, auth_digest) or session.get(MatrixDeviceRevocation, (actor_id, current.device_id))):
        raise HTTPException(409, "Initialize the original current encryption session before continuing this migration")
    return hashlib.sha256(json.dumps([auth_digest, current.matrix_token_hash, current.device_id], separators=(",", ":")).encode()).hexdigest()


def reserve_secure(session, workspace_id, actor_id, data, auth_digest):
    """Only opaque archive/source commitments reach this secure-import API."""
    authorize(session, workspace_id, actor_id)
    from ..config import settings
    if not settings.matrix_enabled: raise HTTPException(409, "Enable encrypted messaging before a secure migration")
    origin = _secure_origin(session, actor_id, auth_digest)
    # Match encrypted sends: device/account fence before workspace/channel fence.
    authorize(session, workspace_id, actor_id, locked=True)
    if len(set(data.member_ids)) != len(data.member_ids): raise HTTPException(422, "Choose unique current members")
    _mapping(session, workspace_id, data.member_ids)
    digest = "secure:" + data.archive_digest
    row = session.exec(select(SlackImport).where(SlackImport.workspace_id == workspace_id, SlackImport.archive_digest == digest)).first()
    if row:
        if row.actor_id != actor_id: raise HTTPException(409, "This secure migration belongs to another administrator")
        if row.request_digest != data.options_digest or row.status != "secure": raise HTTPException(409, "Secure migration choices changed; use the original reviewed archive and mapping")
        state = json.loads(row.result_json)
        if state.get("origin") != origin: raise HTTPException(409, "Resume this migration in its original sign-in and encryption device")
    else:
        pending = session.exec(select(SlackImport.id).where(SlackImport.actor_id == actor_id, SlackImport.status == "secure")).all()
        if len(pending) >= 10: raise HTTPException(429, "Finish or discard an existing secure migration first")
        row = SlackImport(workspace_id=workspace_id, actor_id=actor_id, archive_digest=digest, encrypted_archive="", status="secure",
                          request_digest=data.options_digest, expires_at=datetime.utcnow()+timedelta(days=7))
        state = {"origin": origin, "channels": []}
        session.add(row); session.flush()
    current = next((item for item in state["channels"] if item["source_digest"] == data.source_digest), None)
    if current:
        from .access import lock_channel_writes
        lock_channel_writes(session, current["id"])
        channel = session.get(Channel, current["id"])
        member = session.get(ChannelMember, (current["id"], actor_id), populate_existing=True)
        mapping = session.get(WorkspaceChannel, current["id"])
        members = set(session.exec(select(ChannelMember.user_id).where(ChannelMember.channel_id == current["id"])).all())
        if not channel or not mapping or mapping.workspace_id != workspace_id or not member or member.role != "admin" or members != set(data.member_ids) | {actor_id}:
            raise HTTPException(409, "The secure migration destination or membership changed")
        return {"id": row.id, "channel_id": channel.id}
    if len(state["channels"]) >= 100: raise HTTPException(422, "A secure migration supports at most 100 selected channels")
    channel = Channel(id=uuid4().hex, name="Encrypted channel", type="group")
    session.add(channel); session.flush(); session.add(WorkspaceChannel(channel_id=channel.id, workspace_id=workspace_id)); session.flush()
    for uid in set(data.member_ids) | {actor_id}: session.add(ChannelMember(channel_id=channel.id, user_id=uid, role="admin" if uid == actor_id else "member"))
    state["channels"].append({"source_digest": data.source_digest, "id": channel.id})
    row.result_json = json.dumps(state, separators=(",", ":")); session.add(row); session.commit()
    return {"id": row.id, "channel_id": channel.id}


def historical_message(session, channel_id, message_id, user_id):
    from .access import require_channel_member
    from .chat_notifications import require_native_channel
    require_channel_member(session, channel_id, user_id)
    require_native_channel(session, channel_id)
    message = session.get(Message, message_id)
    if not message or message.channel_id != channel_id or message.deleted: raise HTTPException(404, "Historical message not found")
    detail = session.get(SlackImportDetail, message_id)
    return json.loads(decrypt_message(detail.encrypted_details, channel_id)) if detail else None


class SecureComplete(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    archive_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    options_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


def complete_secure(session, workspace_id, actor_id, identifier, data, auth_digest):
    origin = _secure_origin(session, actor_id, auth_digest)
    row = _job(session, workspace_id, actor_id, identifier)
    if not row.result_json or json.loads(row.result_json).get("origin") != origin:
        raise HTTPException(409, "Resume this migration in its original sign-in and encryption device")
    if row.status not in {"secure", "secure_completed"} or row.request_digest != data.options_digest or row.archive_digest != "secure:" + data.archive_digest:
        raise HTTPException(409, "Secure migration confirmation does not match its reviewed choices")
    row.status = "secure_completed"
    session.add(row); session.commit()
    return {"completed": True}
