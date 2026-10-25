"""Current native sources, without a persistent plaintext search index.

Clients match these records locally. No query text is needed to load them.
Matrix channels are deliberately excluded; their verified records are searched
by the existing decryption client. Every citation is actor-bound and rechecked.
"""
import base64
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import time

from fastapi import HTTPException
from sqlmodel import select

from ..models.activity_feed import ActivityFeedPost
from ..models.file import File
from ..models.file_version import FileDocument, FileVersion
from ..models.project_tasks import ProjectBoard, TaskCard
from ..models.shared_documents import SharedDocument, SharedDocumentUpdate
from ..models.message import Message
from ..models.collaboration import Decision, DecisionRevision
from ..models.clips import ChannelClip
from ..models.integration_content import IntegrationContent, IntegrationContentRevision
from ..models.workflow import WorkflowDefinition
from . import activity_feed, project_tasks, shared_documents
from .access import require_channel_member
from .account_security import seal_private, unseal_private
from .file_experience import OFFICE_TYPES, office_preview
from .matrix_bridge import is_secure_channel
from .remote_sync import is_file_deleted
from .request_guard import recheck_actor
from .storage import storage_service

MAX_SOURCES = 200
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_CHARS = 1500000
KINDS = {"document": SharedDocument, "project": ProjectBoard, "task": TaskCard,
         "feed": ActivityFeedPost, "file-content": File, "message": Message,
         "decision": Decision, "clip": ChannelClip, "linked-document": IntegrationContent,
         "calendar": IntegrationContent, "workflow": WorkflowDefinition}


def authorize(session, channel_id, actor_id):
    recheck_actor(session, actor_id)
    require_channel_member(session, channel_id, actor_id)
    if is_secure_channel(session, channel_id):
        raise HTTPException(409, "Search encrypted content on this device")


def isolated_extract(payload):
    try:
        process = subprocess.run([sys.executable, str(Path(__file__).with_name("search_extract.py"))],
            input=json.dumps(payload).encode(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=4, check=True, cwd="/tmp")
        if len(process.stdout) > 500000: raise ValueError()
        result = json.loads(process.stdout)
        if not isinstance(result.get("text"), str): raise ValueError()
        return result
    except (OSError, subprocess.SubprocessError, ValueError):
        raise HTTPException(422, "Text extraction is unavailable for this source") from None


def fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def reference(actor_id, item):
    return seal_private(json.dumps({"actor": actor_id, "channel": item["channel_id"], "kind": item["kind"],
        "id": item["id"], "version": item["version"], "expires": datetime.utcnow().timestamp() + 600}),
        "enterprise-search-source", actor_id)


def message_visible(session, row):
    seen = {row.id}
    parent_id = row.parent_id
    for _ in range(32):
        if not parent_id:
            return True
        if parent_id in seen:
            return False
        seen.add(parent_id)
        parent = session.get(Message, parent_id, populate_existing=True)
        if not parent or parent.deleted or parent.channel_id != row.channel_id:
            return False
        parent_id = parent.parent_id
    return False


def source(session, channel_id, kind, row):
    title, text, truncated, parent = "", "", False, None
    if kind == 'decision':
        from ..api.decisions import revision_data
        revision = session.exec(select(DecisionRevision).where(DecisionRevision.decision_id == row.id).order_by(DecisionRevision.revision.desc()).limit(1)).first()
        if not revision:
            raise HTTPException(404, 'Decision has no current content')
        if revision.content_mode != 'server':
            raise HTTPException(409, 'Decision content is device-only')
        fields = revision_data(session, row, revision)
        title, text = fields['title'], fields['content']
    elif kind == 'clip':
        if row.content_mode != 'server':
            raise HTTPException(409, 'Clip content is device-only')
        fields = shared_documents.open_payload(session, channel_id, 'clip', row.id, row.id, row.encrypted_payload)
        title, text = fields['title'], fields.get('transcript', '')
    elif kind in ('linked-document', 'calendar'):
        expected = 'documents' if kind == 'linked-document' else 'calendar'
        if row.kind != expected:
            raise HTTPException(404, 'Content kind does not match')
        from ..api.integrations_content import latest_revision, serialize
        revision = latest_revision(session, row.id)
        if not revision or revision.deleted:
            raise HTTPException(404, 'Content is no longer current')
        if revision.content_mode != 'server':
            raise HTTPException(409, 'Integration content is device-only')
        fields = serialize(session, row, revision)['fields']
        title = fields['title']
        text = fields.get('notes', '') if kind == 'linked-document' else '\n'.join(fields.get(key, '') for key in ('description', 'location', 'starts_at', 'ends_at'))
    elif kind == 'workflow':
        from .workflow import definition_fields
        fields = definition_fields(session, row)
        title, text = fields.name, fields.trigger + '\n' + '\n'.join((step.task_title or '') for step in fields.steps)
    elif kind == "message":
        if row.deleted or not message_visible(session, row):
            raise HTTPException(404, "Message or its parent is unavailable")
        from .crypto import decrypt_message
        try:
            text = decrypt_message(row.content, channel_id) if row.encrypted else row.content
        except Exception:
            raise HTTPException(409, "Message could not be read") from None
        title, parent = text.splitlines()[0][:160] if text else "Message", row.parent_id
    elif kind == "document":
        if row.archived: raise HTTPException(404, "Document archived")
        updates = session.exec(select(SharedDocumentUpdate).where(SharedDocumentUpdate.document_id == row.id)
            .order_by(SharedDocumentUpdate.sequence).limit(1001)).all()
        if len(updates) > 1000 or sum(item.byte_size for item in updates) > MAX_SOURCE_BYTES:
            raise HTTPException(413, "Document exceeds the search extraction limit")
        if len(updates) != row.update_count or any(item.sequence != index + 1 or item.content_mode != "server" for index, item in enumerate(updates)):
            raise HTTPException(409, "Document history cannot be verified")
        encoded = [shared_documents.open_payload(session, channel_id, "update", row.id, item.update_id,
            item.encrypted_payload)["update_base64"] for item in updates]
        fields = isolated_extract({"kind": "yjs", "updates": encoded})
        title, text = fields.get("title") or "Untitled document", fields["text"]
    elif kind in {"task", "project"}:
        if row.archived: raise HTTPException(404, "Item archived")
        if kind == "task":
            board = session.get(ProjectBoard, row.project_id)
            if not board or board.archived: raise HTTPException(404, "Project archived")
            parent = row.project_id
        revision = project_tasks.revision_for(session, kind, row.id, row.revision)
        if not revision or revision.content_mode != "server": raise HTTPException(409, "Native revision unavailable")
        fields = project_tasks.serialize(session, kind, row, revision)["fields"]
        title = fields["title"]
        text = fields["description"] + "\n" + "\n".join(item["title"] for item in fields.get("checklist", []))
    elif kind == "feed":
        if row.archived: raise HTTPException(404, "Post archived")
        revision = activity_feed.revision_for(session, "post", row.id, row.revision)
        if not revision or revision.content_mode != "server": raise HTTPException(409, "Native post unavailable")
        fields = activity_feed.content_record(session, channel_id, "post", row, revision)["fields"]
        title, text = fields["title"], fields["body"]
        if fields.get("poll"):
            text += "\n" + fields["poll"]["question"] + "\n" + "\n".join(item["label"] for item in fields["poll"]["options"])
    else:
        if row.encrypted: raise HTTPException(409, "Encrypted file content is device-only")
        version = session.get(FileVersion, row.id)
        document = session.get(FileDocument, version.document_id) if version else None
        if document and (document.current_file_id != row.id or is_file_deleted(session, document.id)):
            raise HTTPException(404, "File is no longer current")
        if row.size > MAX_SOURCE_BYTES: raise HTTPException(413, "File exceeds 2 MiB content search limit")
        try:
            content = storage_service.read_file(row.id, max_bytes=MAX_SOURCE_BYTES, expected_size=row.size)
        except (OSError, ValueError): raise HTTPException(404, "File content unavailable") from None
        if content is None: raise HTTPException(404, "File content unavailable")
        if len(content) > MAX_SOURCE_BYTES: raise HTTPException(413, "File exceeds search extraction limit")
        title = row.filename
        if row.content_type in OFFICE_TYPES:
            fields = office_preview(content, row.content_type)
            text, truncated = "\n".join(fields["blocks"]), fields["truncated"]
        elif row.content_type == "application/pdf":
            fields = isolated_extract({"kind": "pdf", "content": base64.b64encode(content).decode()})
            text, truncated = fields["text"], fields["truncated"]
        elif row.content_type.startswith("text/") or row.content_type in {"application/json", "application/xml", "application/yaml"}:
            try: text = content.decode("utf-8-sig")
            except UnicodeDecodeError: raise HTTPException(422, "Only UTF-8 text can be searched") from None
            if "\x00" in text: raise HTTPException(422, "Binary content cannot be searched")
            truncated = len(text) > 100000; text = text[:100000]
        else: raise HTTPException(422, "File has no supported passive text extractor")
    result = {"id": row.id, "channel_id": channel_id, "kind": kind, "title": title, "text": text,
        "created_by": getattr(row, "created_by", getattr(row, "uploader_id", getattr(row, "author_id", ""))),
        "created_at": row.created_at.isoformat(), "parent_id": parent, "truncated": truncated}
    result["source_revision"] = row_revision(row, session)
    result["version"] = fingerprint({**result, "revision": getattr(row, "revision", None),
        "updates": getattr(row, "update_count", None)})
    return result


def row_revision(row, session=None):
    extra = None
    if session is not None and isinstance(row, Decision):
        latest = session.exec(select(DecisionRevision).where(DecisionRevision.decision_id == row.id).order_by(DecisionRevision.revision.desc()).limit(1)).first()
        extra = [latest.revision, latest.content_mode] if latest else None
    if session is not None and isinstance(row, IntegrationContent):
        latest = session.exec(select(IntegrationContentRevision).where(IntegrationContentRevision.item_id == row.id).order_by(IntegrationContentRevision.revision.desc()).limit(1)).first()
        extra = [latest.revision, latest.deleted, latest.content_mode] if latest else None
    return fingerprint({"revision": getattr(row, "revision", None), "updates": getattr(row, "update_count", None),
        "archived": getattr(row, "archived", False), "updated_at": getattr(row, "updated_at", None),
        "edited_at": getattr(row, "edited_at", None), "deleted": getattr(row, "deleted", False),
        'current_revision': getattr(row, 'current_revision', None), 'extra': extra, 'parent_id': getattr(row, 'parent_id', None), 'message_content': row.content if isinstance(row, Message) else None, "id": row.id})


def still_current(session, item):
    row = session.get(KINDS[item["kind"]], item["id"], populate_existing=True)
    if not row or row.channel_id != item["channel_id"] or row_revision(row, session) != item["source_revision"]: return False
    if item["kind"] == "message" and not message_visible(session, row): return False
    if item["kind"] == "file-content":
        version = session.get(FileVersion, row.id)
        document = session.get(FileDocument, version.document_id, populate_existing=True) if version else None
        if document and (document.current_file_id != row.id or is_file_deleted(session, document.id)): return False
    if item["kind"] == "task":
        board = session.get(ProjectBoard, row.project_id, populate_existing=True)
        if not board or board.archived: return False
    return True


def allowed(session, channel_id, item):
    from .enterprise_governance import native_content_allowed
    return native_content_allowed(session, channel_id, item["title"] + "\n" + item["text"], surface="search")


def sources(session, channel_id, actor_id):
    authorize(session, channel_id, actor_id)
    items, skipped, chars = [], [], 0
    deadline = time.monotonic() + 15
    complete = True
    for kind, model in KINDS.items():
        records = session.exec(select(model).where(model.channel_id == channel_id)
            .order_by(model.created_at.desc(), model.id.desc()).limit(MAX_SOURCES + 1)).all()
        if len(records) > MAX_SOURCES: complete = False
        for row in records[:MAX_SOURCES]:
            if time.monotonic() > deadline:
                complete = False; break
            try:
                item = source(session, channel_id, kind, row)
                if not allowed(session, channel_id, item): continue
                if item["truncated"]: complete = False
            except HTTPException as error:
                if error.status_code not in {404, 409, 413, 422}: raise
                if error.status_code != 404: skipped.append({"kind": kind, "id": row.id, "status": error.status_code})
                continue
            chars += len(item["text"]) + len(item["title"])
            if chars > MAX_RESPONSE_CHARS:
                complete = False; break
            item["reference"] = reference(actor_id, item); items.append(item)
        if chars > MAX_RESPONSE_CHARS or time.monotonic() > deadline: break
    # Parsing may take time. Fresh ORM identities and current request/session
    # checks prevent returning content after revocation or a channel conversion.
    session.commit(); session.expire_all()
    authorize(session, channel_id, actor_id)
    fresh = [item for item in items if still_current(session, item) and allowed(session, channel_id, item)]
    if len(fresh) != len(items): complete = False
    items = fresh
    return {"items": items, "complete": complete and not skipped, "skipped": skipped,
        "engine": "current-sources", "persistent_plaintext_index": False}


def resolve(session, actor_id, encrypted_reference, *, commit=True):
    try:
        claim = json.loads(unseal_private(encrypted_reference, "enterprise-search-source", actor_id))
        if claim["actor"] != actor_id or claim["expires"] < datetime.utcnow().timestamp(): raise ValueError()
        KINDS[claim["kind"]]
    except Exception: raise HTTPException(404, "Search citation expired or unavailable") from None
    return resolve_canonical(session, actor_id, claim['kind'], claim['id'], claim['channel'], claim['version'], commit=commit)


def resolve_canonical(session, actor_id, kind, identifier, channel_id, version, *, commit=False):
    """Internal durable-job descriptor resolver; no public expiry bypass route.

    Queue owners must derive this descriptor from a fresh actor-bound citation,
    seal it in the authorized job, and retain its exact version commitment.
    Current membership, native mode, source version and governance remain live.
    """
    model = KINDS.get(kind)
    if model is None or not isinstance(identifier, str) or not isinstance(version, str):
        raise HTTPException(404, 'Search source unavailable')
    authorize(session, channel_id, actor_id)
    row = session.get(model, identifier, populate_existing=True)
    if not row or row.channel_id != channel_id: raise HTTPException(404, "Search source unavailable")
    item = source(session, channel_id, kind, row)
    if item["version"] != version: raise HTTPException(409, "This source changed; refresh search")
    if not allowed(session, channel_id, item): raise HTTPException(403, "Source is restricted by policy")
    if commit:
        session.commit()
    session.expire_all(); authorize(session, channel_id, actor_id)
    if not still_current(session, item): raise HTTPException(409, "This source changed; refresh search")
    if not allowed(session, channel_id, item): raise HTTPException(403, "Source is restricted by policy")
    item["reference"] = reference(actor_id, item)
    return item
