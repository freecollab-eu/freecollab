"""Validated project/task snapshots; secure channels retain opaque pointers."""
from datetime import date
from hashlib import sha256
import json
from typing import Literal

from fastapi import HTTPException
from nacl.exceptions import CryptoError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlmodel import select

from ..models.collaboration import Decision
from ..models.file import File
from ..models.file_version import FileVersion
from ..models.project_tasks import ProjectBoard, ProjectTaskRevision, TaskCard
from ..models.remote_sync import RemoteSyncFileState
from ..models.user import User
from .access import require_channel_member
from .auth import active_user_for_token
from .chat_notifications import require_native_channel
from .crypto import decrypt_message, encrypt_message
from .matrix_bridge import is_secure_channel, validate_secure_event


class ProjectFields(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)
    color: Literal["sage", "blue", "sand", "rose"] = "sage"


class ChecklistItem(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    title: str = Field(min_length=1, max_length=200)
    done: bool = False


class TaskFields(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=12000)
    status: Literal["todo", "doing", "done"] = "todo"
    priority: Literal["low", "normal", "high"] = "normal"
    assignee_user_id: str | None = Field(default=None, min_length=1, max_length=128)
    due_date: date | None = None
    checklist: list[ChecklistItem] = Field(default_factory=list, max_length=40)
    file_ids: list[str] = Field(default_factory=list, max_length=20)
    decision_ids: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("file_ids", "decision_ids")
    @classmethod
    def references(cls, value):
        if any(not isinstance(item, str) or not 1 <= len(item) <= 128 for item in value) or len(set(value)) != len(value):
            raise ValueError("References must be unique bounded identifiers")
        return value

    @model_validator(mode="after")
    def unique_checks(self):
        if len({item.id for item in self.checklist}) != len(self.checklist):
            raise ValueError("Checklist identifiers must be unique")
        return self


class SnapshotWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    expected_revision: int = Field(default=0, ge=0, le=1000)
    archived: bool = False
    fields: dict | None = None
    matrix_event_id: str | None = Field(default=None, min_length=1, max_length=1024)

    @model_validator(mode="after")
    def exclusive_payload(self):
        if (self.fields is None) == (self.matrix_event_id is None):
            raise ValueError("Provide fields or an encrypted event reference")
        return self


def fresh_actor(session, token, expected_id):
    session.expire_all()
    from .request_guard import recheck_actor
    recheck_actor(session, expected_id)
    actor = active_user_for_token(token, session)
    if not actor or actor.id != expected_id:
        raise HTTPException(401, "Your account or session changed; sign in again")
    return actor


def validate_snapshot(session, kind, channel_id, data, actor_id):
    secure = is_secure_channel(session, channel_id)
    if secure:
        if data.fields is not None or not data.matrix_event_id:
            raise HTTPException(409, "This channel requires an encrypted project/task event")
        validate_secure_event(session, channel_id, data.matrix_event_id, actor_id)
        return None
    if data.matrix_event_id:
        raise HTTPException(400, "Encrypted project/task events require an encrypted channel")
    try:
        fields = (ProjectFields if kind == "project" else TaskFields).model_validate(data.fields)
    except ValueError:
        raise HTTPException(422, "Invalid project/task fields") from None
    if kind == "task":
        if fields.assignee_user_id:
            target = session.get(User, fields.assignee_user_id)
            if not target or target.is_blocked:
                raise HTTPException(400, "Choose an active channel member")
            try:
                require_channel_member(session, channel_id, target.id)
            except HTTPException:
                raise HTTPException(400, "Choose an active channel member") from None
        for identifier in fields.file_ids:
            file = session.get(File, identifier)
            version = session.get(FileVersion, identifier)
            deleted = session.get(RemoteSyncFileState, version.document_id) if version else None
            if not file or file.channel_id != channel_id or deleted and deleted.deleted:
                raise HTTPException(400, "Linked files must be available in this channel")
        for identifier in fields.decision_ids:
            decision = session.get(Decision, identifier)
            if not decision or decision.channel_id != channel_id:
                raise HTTPException(400, "Linked decisions must belong to this channel")
    payload = fields.model_dump(mode="json")
    from .enterprise_governance import enforce_native_document
    enforce_native_document(session, channel_id, actor_id, payload)
    return payload


def revision_for(session, kind, item_id, number):
    return session.exec(select(ProjectTaskRevision).where(ProjectTaskRevision.kind == kind,
        ProjectTaskRevision.item_id == item_id, ProjectTaskRevision.revision == number)).first()


def new_revision(kind, item, data, fields, actor_id):
    number = item.revision + 1
    payload = {"id": item.id, "kind": kind, "revision": number, "archived": data.archived,
               "project_id": item.project_id if kind == "task" else None, "fields": fields}
    return ProjectTaskRevision(kind=kind, item_id=item.id, revision=number, created_by=actor_id,
        content_mode="matrix" if data.matrix_event_id else "server", matrix_event_id=data.matrix_event_id,
        archived=data.archived, encrypted_payload=None if data.matrix_event_id else encrypt_message(json.dumps(payload, ensure_ascii=False), item.channel_id))


def serialize(session, kind, item, revision):
    record = {"id": item.id, "kind": kind, "channel_id": item.channel_id,
        "project_id": item.project_id if kind == "task" else None, "created_by": item.created_by,
        "created_at": item.created_at, "revision": revision.revision, "revised_by": revision.created_by,
        "revised_at": revision.created_at, "archived": revision.archived,
        "content_mode": revision.content_mode, "matrix_event_id": revision.matrix_event_id}
    if revision.content_mode == "server":
        require_native_channel(session, item.channel_id)
        try:
            payload = json.loads(decrypt_message(revision.encrypted_payload, item.channel_id))
            if any(payload.get(key) != record[key] for key in ("id", "kind", "project_id", "revision", "archived")):
                raise ValueError()
            record["fields"] = (ProjectFields if kind == "project" else TaskFields).model_validate(payload["fields"]).model_dump(mode="json")
        except (CryptoError, ValueError, TypeError, KeyError, AttributeError):
            raise HTTPException(409, "Stored project/task revision is unavailable") from None
    return record


def create_workflow_task(session, channel_id, actor_id, project_id, title, idempotency_key, description=""):
    """One native task per approved workflow action, without committing or loops."""
    from ..models.workspace import WorkspaceChannel
    from .access import lock_channel_writes
    from .workforce_access import require_personal_account
    from .workspaces import require_direct_message_writable, require_workspace_admin
    if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 256:
        raise HTTPException(400, "Invalid workflow task identifier")
    lock_channel_writes(session, channel_id)
    session.expire_all()
    from .request_guard import recheck_actor
    recheck_actor(session, actor_id)
    actor = session.get(User, actor_id)
    if not actor or actor.is_blocked:
        raise HTTPException(403, "Workflow authorizer is no longer active")
    require_personal_account(session, actor_id)
    member = require_channel_member(session, channel_id, actor_id)
    mapping = session.get(WorkspaceChannel, channel_id)
    if member.role != "admin" or not mapping:
        raise HTTPException(403, "Workflow authorizer needs current channel administration")
    require_workspace_admin(session, mapping.workspace_id, actor_id)
    require_direct_message_writable(session, channel_id, actor_id)
    require_native_channel(session, channel_id)
    project = session.get(ProjectBoard, project_id)
    if not project or project.channel_id != channel_id or project.archived:
        raise HTTPException(409, "Choose an active project in the workflow channel")
    key = sha256((channel_id + "\0" + project_id + "\0" + actor_id + "\0" + idempotency_key).encode()).hexdigest()
    identifier = "wf_" + key
    existing = session.get(TaskCard, identifier)
    if existing:
        if existing.automation_key != key or existing.created_by != actor_id or existing.project_id != project_id:
            raise HTTPException(409, "Workflow task identifier is already in use")
        return existing
    from sqlalchemy import func
    count = session.exec(select(func.count()).select_from(TaskCard).where(TaskCard.channel_id == channel_id)).one()
    if count >= 1000:
        raise HTTPException(409, "This channel has reached its task limit")
    try:
        fields = TaskFields(title=title, description=description).model_dump(mode="json")
    except ValueError:
        raise HTTPException(422, "Invalid workflow task fields") from None
    task = TaskCard(id=identifier, project_id=project_id, channel_id=channel_id, created_by=actor_id, automation_key=key)
    data = SnapshotWrite(id=identifier, fields=fields)
    revision = new_revision("task", task, data, fields, actor_id)
    task.revision = revision.revision
    session.add(task)
    session.flush()
    session.add(revision)
    session.flush()
    return task
