"""Explicit channel grants for metadata-only durable workflow automation.

Producer hooks participate in the caller's transaction. The worker never opens
message/document contents and never sends external messages. All DB effects for a
step, notification deduplication and its advancement commit together.
"""
from datetime import datetime, timedelta
from hashlib import sha256
import json
from typing import Literal

from fastapi import HTTPException
from nacl.exceptions import CryptoError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import and_, delete, func, or_, update
from sqlmodel import select

from ..config import settings
from ..models.channel import ChannelMember
from ..models.notification import Notification, NotificationPreference
from ..models.user import User
from ..models.app_platform import AppInstallation, AppJob, WorkflowAppReceipt
from ..models.workspace import WorkspaceChannel
from ..models.workflow import (WorkflowActivity, WorkflowDefinition, WorkflowNotice,
                              WorkflowRevision, WorkflowRun, WorkflowVote)
from .access import lock_channel_writes, require_channel_member
from .field_references import normalize_field_references
from .auth import active_user_for_token
from .crypto import decrypt_message, encrypt_message
from .sso_scope import require_unrestricted
from .workforce_access import is_managed
from .workspaces import lock_workspace, require_direct_message_writable, require_workspace_admin

TRIGGERS = {"manual", "file_uploaded", "document_created", "decision_updated", "task_updated"}
TERMINAL = {"completed", "rejected", "cancelled", "timed_out", "failed"}
LEASE_SECONDS = 60
MAX_DEFINITIONS = 20
MAX_RUNS = 2000


class Conditions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    actor: Literal["any", "creator"] = "any"
    status: Literal["todo", "doing", "done", "proposed", "accepted", "superseded"] | None = None


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["approval", "notify", "create_task", "app_command"]
    recipients: list[str] = Field(default_factory=list, max_length=50)
    quorum: int = Field(default=1, ge=1, le=20)
    reminder_minutes: int = Field(default=60, ge=1, le=10080)
    timeout_minutes: int = Field(default=1440, ge=1, le=43200)
    max_reminders: int = Field(default=2, ge=0, le=3)
    escalation_recipients: list[str] = Field(default_factory=list, max_length=20)
    project_id: str | None = Field(default=None, min_length=1, max_length=128)
    task_title: str | None = Field(default=None, min_length=1, max_length=200)
    installation_id: str | None = Field(default=None, min_length=1, max_length=128)
    command: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    expected_generation: int | None = Field(default=None, ge=1)
    arguments: dict[str, str] = Field(default_factory=dict)
    disclose_to_app: bool = False

    @field_validator("recipients", "escalation_recipients")
    @classmethod
    def unique_people(cls, values):
        if len(set(values)) != len(values) or any(not value or len(value) > 128 for value in values):
            raise ValueError("Select unique channel members")
        return values

    @model_validator(mode="after")
    def coherent(self):
        if self.kind in {"approval", "notify"} and not self.recipients:
            raise ValueError("Select at least one channel member")
        if self.kind == "approval" and self.quorum > len(self.recipients):
            raise ValueError("Approval quorum exceeds selected approvers")
        if self.kind == "create_task":
            if not self.project_id or not self.task_title or not self.task_title.strip():
                raise ValueError("Choose a project and task title")
            if self.recipients or self.escalation_recipients:
                raise ValueError("Task creation does not add approvers")
        elif self.project_id or self.task_title:
            raise ValueError("Task fields belong to a task action")
        if self.kind == "app_command":
            if not self.installation_id or not self.command or not self.expected_generation or not self.disclose_to_app:
                raise ValueError("Select a current installed command and explicitly approve its future arguments")
            if self.recipients or self.escalation_recipients:
                raise ValueError("App commands do not add approvers")
            if len(self.arguments) > 8 or sum(len(key) + len(value) for key, value in self.arguments.items()) > 64000:
                raise ValueError("App command arguments exceed their bound")
        elif self.installation_id or self.command or self.expected_generation or self.arguments or self.disclose_to_app:
            raise ValueError("App fields belong to an installed command action")
        if self.kind != "approval" and self.escalation_recipients:
            raise ValueError("Only approvals support escalation")
        return self


class DefinitionFields(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=100)
    trigger: Literal["manual", "file_uploaded", "document_created", "decision_updated", "task_updated"]
    conditions: Conditions = Field(default_factory=Conditions)
    steps: list[Step] = Field(min_length=1, max_length=5)

    @field_validator("name")
    @classmethod
    def name_without_controls(cls, value):
        if not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("Use a nonempty workflow name without control characters")
        return value.strip()

    @model_validator(mode="after")
    def status_matches_trigger(self):
        if self.conditions.status:
            allowed = {"task_updated": {"todo", "doing", "done"}, "decision_updated": {"proposed", "accepted", "superseded"}}
            if self.conditions.status not in allowed.get(self.trigger, set()):
                raise ValueError("Status conditions require the corresponding task or decision trigger")
        return self


def secure_channel(session, channel_id):
    from ..models.secure_channel import SecureChannel
    return session.get(SecureChannel, channel_id) is not None


def require_channel(session, workspace_id, channel_id, user_id, *, admin=False):
    mapping = session.get(WorkspaceChannel, channel_id)
    if not mapping or mapping.workspace_id != workspace_id:
        raise HTTPException(404, "Channel not found in workspace")
    member = require_channel_member(session, channel_id, user_id)
    user = session.get(User, user_id, populate_existing=True)
    if not user or user.is_blocked:
        raise HTTPException(403, "Active channel membership required")
    require_direct_message_writable(session, channel_id, user_id)
    if admin:
        require_unrestricted(session)
        require_workspace_admin(session, workspace_id, user_id)
        if member.role != "admin" or is_managed(session, user_id):
            raise HTTPException(403, "A personal workspace and channel administrator is required")
    return member


def lock_context(session, workspace_id, channel_id):
    lock_workspace(session, workspace_id)
    lock_channel_writes(session, channel_id)
    session.expire_all()


def lock_actor(session, workspace_id, channel_id, user_id, token, *, admin=False):
    session.execute(update(User).where(User.id == user_id).values(name=User.name))
    lock_context(session, workspace_id, channel_id)
    from .request_guard import recheck_actor
    recheck_actor(session, user_id)
    actor = active_user_for_token(token, session)
    if not actor or actor.id != user_id:
        raise HTTPException(401, "Your session changed. Sign in again.")
    require_channel(session, workspace_id, channel_id, user_id, admin=admin)
    return actor


def current_person(session, channel_id, user_id):
    try:
        require_channel_member(session, channel_id, user_id)
        user = session.get(User, user_id, populate_existing=True)
        return bool(user and not user.is_blocked)
    except HTTPException:
        return False


def validate_fields(session, workspace_id, channel_id, fields):
    if secure_channel(session, channel_id) and fields.trigger == "file_uploaded":
        raise HTTPException(409, "Encrypted attachment contents are not classified by the workflow server. Use manual, task, document or decision metadata.")
    if secure_channel(session, channel_id) and (fields.conditions.status or any(s.kind in {"create_task", "app_command"} for s in fields.steps)):
        raise HTTPException(409, "Encrypted channels support metadata triggers, generic notices and approvals; content rules and server-created tasks are unavailable")
    for step in fields.steps:
        for user_id in set(step.recipients + step.escalation_recipients):
            if not current_person(session, channel_id, user_id):
                raise HTTPException(400, "Workflow recipients must be current active channel members")
        if step.kind == "create_task":
            from ..models.project_tasks import ProjectBoard
            project = session.get(ProjectBoard, step.project_id)
            if not project or project.channel_id != channel_id or project.archived:
                raise HTTPException(404, "Choose an active project from this channel")
        if step.kind == "app_command":
            from . import app_platform
            row = session.get(AppInstallation, step.installation_id)
            if (not row or row.workspace_id != workspace_id or row.generation != step.expected_generation
                    or channel_id not in app_platform.channels(row) or not app_platform.allowed_installation(session, row)):
                raise HTTPException(409, "Choose a currently granted app installation and review its generation")
            command = next((value for value in app_platform.manifest_for(row).commands if value.name == step.command), None)
            if not command: raise HTTPException(404, "Workflow command is not installed")
            app_platform.arguments_for(command, step.arguments)


def definition_fields(session, definition, revision=None):
    row = session.exec(select(WorkflowRevision).where(WorkflowRevision.workflow_id == definition.id,
            WorkflowRevision.revision == (revision or definition.current_revision))).first()
    if not row:
        raise HTTPException(409, "Workflow revision is unavailable")
    try:
        payload = json.loads(decrypt_message(row.encrypted_definition, "workflow:" + definition.channel_id))
        if payload.pop("workflow_id") != definition.id or payload.pop("revision") != row.revision:
            raise ValueError("Context mismatch")
        return DefinitionFields.model_validate(payload)
    except (CryptoError, ValueError, TypeError, KeyError, AttributeError):
        raise HTTPException(409, "Workflow configuration could not be read") from None


def definition_data(session, definition):
    return {"id": definition.id, "workspace_id": definition.workspace_id, "channel_id": definition.channel_id,
            "revision": definition.current_revision, "enabled": definition.enabled, "created_by": definition.created_by,
            "granted_by": definition.granted_by, "created_at": definition.created_at, "updated_at": definition.updated_at,
            "fields": definition_fields(session, definition).model_dump(mode="json")}


def activity(session, run, kind, actor_id=None, now=None):
    session.add(WorkflowActivity(run_id=run.id, kind=kind, actor_id=actor_id, step_index=run.step_index,
                                 created_at=now or datetime.utcnow()))


def finish(session, run, status, *, reason=None, now=None):
    now = now or datetime.utcnow()
    run.status, run.failure = status, reason
    run.finished_at = run.updated_at = now
    run.lease_expires_at = None
    if status != "completed":
        receipts = session.exec(select(WorkflowAppReceipt).where(WorkflowAppReceipt.run_id == run.id)).all()
        for receipt in receipts:
            job = session.get(AppJob, receipt.app_job_id)
            if job and job.status in {"pending", "processing"}:
                job.status, job.failure, job.claim_token, job.encrypted_payload = "cancelled", "Workflow stopped", None, ""
                session.add(job)
    activity(session, run, reason or status, now=now)
    session.add(run)


def cancel_previous(session, definition):
    for run in session.exec(select(WorkflowRun).where(WorkflowRun.workflow_id == definition.id,
            WorkflowRun.status.not_in(TERMINAL))).all():
        finish(session, run, "cancelled", reason="configuration_changed")


def _trim_history(session, channel_id):
    count = session.exec(select(func.count()).select_from(WorkflowRun).where(WorkflowRun.channel_id == channel_id)).one()
    if count < MAX_RUNS:
        return
    obsolete = session.exec(select(WorkflowRun.id).where(WorkflowRun.channel_id == channel_id,
        WorkflowRun.status.in_(TERMINAL)).order_by(WorkflowRun.finished_at, WorkflowRun.id).limit(100)).all()
    if not obsolete:
        raise HTTPException(429, "Workflow queue is full. Complete or cancel pending runs before retrying.")
    for model in (WorkflowNotice, WorkflowVote, WorkflowActivity, WorkflowAppReceipt):
        session.exec(delete(model).where(model.run_id.in_(obsolete)))
    session.exec(delete(WorkflowRun).where(WorkflowRun.id.in_(obsolete)))


def queue_event(session, event_id, workspace_id, channel_id, kind, resource_id, *, actor_id=None, metadata=None, workflow_id=None):
    """Trusted producer hook: call after authorized mutation, before its commit."""
    if not getattr(settings, "workflows_enabled", True):
        return 0
    if kind not in TRIGGERS or not isinstance(event_id, str) or not 1 <= len(event_id) <= 256:
        raise ValueError("Unsupported workflow event")
    mapping = session.get(WorkspaceChannel, channel_id)
    if not mapping or mapping.workspace_id != workspace_id:
        raise ValueError("Workflow event workspace mismatch")
    if metadata is not None and (not isinstance(metadata, dict) or set(metadata) - {"status"}
                                or metadata.get("status") not in {"todo", "doing", "done", "proposed", "accepted", "superseded"}):
        raise ValueError("Workflow metadata accepts only a supported status")
    # Secure content status remains opaque even if a producer is misconfigured.
    if secure_channel(session, channel_id):
        metadata = None
    if actor_id is None and isinstance(resource_id, str):
        if kind == "file_uploaded":
            from ..models.file import File
            record = session.get(File, resource_id)
            actor_id = record.uploader_id if record and record.channel_id == channel_id else None
        elif kind == "decision_updated":
            from ..models.collaboration import Decision, DecisionRevision
            revision = session.get(DecisionRevision, resource_id)
            decision = session.get(Decision, revision.decision_id if revision else resource_id)
            if decision and decision.channel_id == channel_id:
                actor_id = revision.created_by if revision else decision.created_by
    definitions = session.exec(select(WorkflowDefinition).where(WorkflowDefinition.channel_id == channel_id,
        WorkflowDefinition.workspace_id == workspace_id, WorkflowDefinition.enabled == True,
        WorkflowDefinition.trigger == kind).limit(MAX_DEFINITIONS)).all()
    created = 0
    for definition in definitions:
        if workflow_id is not None and definition.id != workflow_id:
            continue
        fields = definition_fields(session, definition)
        if fields.conditions.actor == "creator" and actor_id != definition.granted_by:
            continue
        if fields.conditions.status and (metadata or {}).get("status") != fields.conditions.status:
            continue
        identifier = sha256((definition.id + "\0" + event_id).encode()).hexdigest()[:32]
        existing = session.get(WorkflowRun, identifier)
        if existing:
            continue
        _trim_history(session, channel_id)
        row = WorkflowRun(id=identifier, workflow_id=definition.id, workspace_id=workspace_id, channel_id=channel_id,
            workflow_revision=definition.current_revision, event_id=event_id, event_kind=kind,
            resource_id=resource_id[:128] if isinstance(resource_id, str) else None, actor_id=actor_id)
        dialect = session.get_bind().dialect.name
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        else:
            from sqlalchemy.dialects.sqlite import insert
        inserted = session.exec(insert(WorkflowRun).values(**row.model_dump()).on_conflict_do_nothing(
            index_elements=["workflow_id", "event_id"]))
        if inserted.rowcount != 1:
            continue
        activity(session, row, "queued", actor_id)
        created += 1
    return created


def grant_valid(session, definition):
    try:
        member = require_channel(session, definition.workspace_id, definition.channel_id, definition.granted_by)
        require_workspace_admin(session, definition.workspace_id, definition.granted_by)
        return bool(definition.enabled and member.role == "admin" and not is_managed(session, definition.granted_by))
    except HTTPException:
        return False


def eligible(session, run, step):
    ids = step.recipients + (step.escalation_recipients if run.escalated else [])
    return {user_id for user_id in ids if current_person(session, run.channel_id, user_id)}


def notice(session, run, user_id, kind, ordinal=0, now=None):
    key = (run.id, run.step_index, user_id, kind, ordinal)
    if session.get(WorkflowNotice, key) or not current_person(session, run.channel_id, user_id):
        return
    preference = session.exec(select(NotificationPreference).where(NotificationPreference.user_id == user_id)).first()
    if preference and (not preference.in_app or not preference.system):
        return
    identifier = sha256("\0".join(map(str, key)).encode()).hexdigest()[:32]
    titles = {"approval": "Workflow approval requested", "reminder": "Workflow approval reminder",
              "escalation": "Workflow approval needs attention", "notify": "Workflow activity", "finished": "Workflow finished"}
    if session.get(Notification, identifier) is None:
        session.add(Notification(id=identifier, user_id=user_id, type="system", actor_id=None,
            entity_type="workflow_run", entity_id=run.id, title=titles[kind], body=None,
            action_url="#channel=" + run.channel_id + "&workflow=" + run.id, created_at=now or datetime.utcnow()))
    session.add(WorkflowNotice(run_id=run.id, step_index=run.step_index, user_id=user_id, kind=kind,
        ordinal=ordinal, notification_id=identifier, created_at=now or datetime.utcnow()))


def _advance(run, now):
    run.step_index += 1
    run.status, run.due_at, run.deadline_at = "queued", now, None
    run.reminder_count, run.escalated, run.lease_expires_at = 0, False, None
    run.next_reminder_at = None
    run.updated_at = now


def _process_run(session, run, definition, fields, now):
    if definition.current_revision != run.workflow_revision:
        finish(session, run, "cancelled", reason="configuration_changed", now=now)
        return
    if not grant_valid(session, definition):
        finish(session, run, "cancelled", reason="grant_revoked", now=now)
        return
    if secure_channel(session, run.channel_id) and (fields.trigger == "file_uploaded" or fields.conditions.status or any(s.kind in {"create_task", "app_command"} for s in fields.steps)):
        finish(session, run, "cancelled", reason="encryption_mode_changed", now=now)
        return
    # Maximum five steps makes loops impossible; generated tasks never enqueue
    # new workflow events. A pass can finish simple steps without extra polling.
    for _ in range(6):
        if run.step_index >= len(fields.steps):
            finish(session, run, "completed", now=now)
            notice(session, run, definition.granted_by, "finished", now=now)
            return
        step = fields.steps[run.step_index]
        if step.kind == "notify":
            for recipient in step.recipients:
                notice(session, run, recipient, "notify", now=now)
            activity(session, run, "notification_sent", now=now)
            _advance(run, now)
            continue
        if step.kind == "create_task":
            from .project_tasks import create_workflow_task
            create_workflow_task(session, run.channel_id, definition.granted_by, step.project_id,
                                 step.task_title, "workflow:" + run.id + ":" + str(run.step_index))
            activity(session, run, "task_created", now=now)
            _advance(run, now)
            continue
        if step.kind == "app_command":
            from . import app_platform
            try:
                row = session.get(AppInstallation, step.installation_id, populate_existing=True)
                if not row or row.workspace_id != run.workspace_id:
                    raise HTTPException(404, "Workflow app is unavailable")
                job = app_platform.queue_invocation(session, row, definition.granted_by,
                    app_platform.Invocation(request_id="wf_" + sha256((run.id + ":" + str(run.step_index)).encode()).hexdigest(),
                        channel_id=run.channel_id, command=step.command, arguments=step.arguments,
                        expected_generation=step.expected_generation, confirm=True, disclose_to_app=step.disclose_to_app))
                receipt = session.get(WorkflowAppReceipt, (run.id, run.step_index))
                if not receipt:
                    session.flush(); session.add(WorkflowAppReceipt(run_id=run.id, step_index=run.step_index, app_job_id=job.id))
                    activity(session, run, "app_command_queued", now=now)
                if job.status == "delivered":
                    activity(session, run, "app_command_delivered", now=now); _advance(run, now); continue
                if job.status in {"failed", "uncertain", "cancelled"}:
                    finish(session, run, "failed", reason="app_delivery_" + job.status, now=now); return
                run.status, run.due_at, run.lease_expires_at, run.updated_at = "waiting_external", now + timedelta(seconds=5), None, now
                session.add(run); return
            except HTTPException:
                finish(session, run, "cancelled", reason="app_configuration_or_grant_changed", now=now); return
        people = eligible(session, run, step)
        if len(people) < step.quorum:
            finish(session, run, "cancelled", reason="approval_membership_changed", now=now)
            return
        votes = session.exec(select(WorkflowVote).where(WorkflowVote.run_id == run.id,
            WorkflowVote.step_index == run.step_index, WorkflowVote.user_id.in_(people))).all()
        if any(vote.choice == "reject" for vote in votes):
            finish(session, run, "rejected", now=now)
            return
        if sum(vote.choice == "approve" for vote in votes) >= step.quorum:
            activity(session, run, "approval_completed", now=now)
            _advance(run, now)
            continue
        if run.deadline_at and now >= run.deadline_at:
            finish(session, run, "timed_out", now=now)
            return
        if run.deadline_at is None:
            run.deadline_at = now + timedelta(minutes=step.timeout_minutes)
            run.next_reminder_at = now + timedelta(minutes=step.reminder_minutes)
            for recipient in people:
                notice(session, run, recipient, "approval", now=now)
            activity(session, run, "approval_requested", now=now)
        elif run.reminder_count < step.max_reminders and run.next_reminder_at and now >= run.next_reminder_at:
            run.reminder_count += 1
            run.next_reminder_at = now + timedelta(minutes=step.reminder_minutes)
            voted = {vote.user_id for vote in votes}
            for recipient in people - voted:
                notice(session, run, recipient, "reminder", run.reminder_count, now=now)
            activity(session, run, "reminder_sent", now=now)
        if not run.escalated and step.escalation_recipients and run.reminder_count >= step.max_reminders:
            run.escalated = True
            for recipient in step.escalation_recipients:
                notice(session, run, recipient, "escalation", now=now)
            activity(session, run, "escalated", now=now)
        run.status = "waiting_approval"
        run.due_at = min(run.deadline_at, run.next_reminder_at) if run.reminder_count < step.max_reminders and run.next_reminder_at else run.deadline_at
        run.lease_expires_at = None
        run.updated_at = now
        session.add(run)
        return


def run_pending(session, limit=10, *, now=None):
    if not getattr(settings, "workflows_enabled", True):
        return 0
    now = now or datetime.utcnow()
    available = or_(WorkflowRun.lease_expires_at.is_(None), WorkflowRun.lease_expires_at <= now)
    ids = session.exec(select(WorkflowRun.id).where(WorkflowRun.status.in_(["queued", "processing", "waiting_approval", "waiting_external"]),
        WorkflowRun.due_at <= now, available).order_by(WorkflowRun.due_at, WorkflowRun.id).limit(min(max(limit, 1), 25))).all()
    processed = 0
    for identifier in ids:
        claimed = session.exec(update(WorkflowRun).where(WorkflowRun.id == identifier,
            WorkflowRun.status.in_(["queued", "processing", "waiting_approval", "waiting_external"]), WorkflowRun.due_at <= now, available).values(
                status="processing", lease_expires_at=now + timedelta(seconds=LEASE_SECONDS), attempts=WorkflowRun.attempts + 1))
        session.commit()
        if claimed.rowcount != 1:
            continue
        try:
            run = session.get(WorkflowRun, identifier)
            initial_definition = session.get(WorkflowDefinition, run.workflow_id)
            locked_grant = initial_definition.granted_by if initial_definition else None
            if initial_definition:
                session.execute(update(User).where(User.id == locked_grant).values(name=User.name))
            lock_context(session, run.workspace_id, run.channel_id)
            owned = session.exec(update(WorkflowRun).where(WorkflowRun.id == identifier,
                WorkflowRun.status == "processing", WorkflowRun.lease_expires_at == now + timedelta(seconds=LEASE_SECONDS))
                .values(updated_at=WorkflowRun.updated_at))
            if owned.rowcount != 1:
                session.rollback()
                continue
            run = session.get(WorkflowRun, identifier, populate_existing=True)
            if not run or run.status != "processing" or run.lease_expires_at != now + timedelta(seconds=LEASE_SECONDS):
                session.rollback()
                continue
            definition = session.get(WorkflowDefinition, run.workflow_id, populate_existing=True)
            if definition and definition.granted_by != locked_grant:
                # A save may have replaced the grant while this claim waited.
                # Reclaim in the usual lock order using the new grant actor.
                run.status, run.lease_expires_at, run.due_at = "queued", None, now
                session.add(run)
                session.commit()
                continue
            if not definition:
                finish(session, run, "cancelled", reason="configuration_unavailable", now=now)
            else:
                fields = definition_fields(session, definition, run.workflow_revision)
                _process_run(session, run, definition, fields, now)
            run.attempts = 0
            if run.status not in TERMINAL:
                run.failure = None
            session.add(run)
            session.commit()
            processed += 1
        except Exception:
            session.rollback()
            row = session.get(WorkflowRun, identifier, populate_existing=True)
            if row and row.status == "processing" and row.lease_expires_at == now + timedelta(seconds=LEASE_SECONDS):
                if row.attempts >= 5:
                    finish(session, row, "failed", reason="action_failed", now=now)
                else:
                    row.status, row.failure = "queued", "action_retry"
                    row.due_at, row.lease_expires_at = now + timedelta(seconds=min(3600, 30 * 2 ** row.attempts)), None
                    session.add(row)
                session.commit()
    return processed


VOTE_REFERENCE_PREFIX = "field-references-v1:"


def native_vote_fields(vote, run):
    """Read legacy raw notes and context-bound referenced notes without guessing."""
    if not vote.encrypted_note:
        return {"note": None}
    try:
        if not vote.encrypted_note.startswith(VOTE_REFERENCE_PREFIX):
            return {"note": decrypt_message(vote.encrypted_note, "workflow-vote:" + run.id)}
        payload = json.loads(decrypt_message(vote.encrypted_note[len(VOTE_REFERENCE_PREFIX):], "workflow-vote:" + run.id))
        if (payload["version"], payload["run_id"], payload["step_index"], payload["user_id"]) != (1, run.id, vote.step_index, vote.user_id):
            raise ValueError("Vote context mismatch")
        note = payload["note"]
        if not isinstance(note, str) or len(note) > 2000:
            raise ValueError("Invalid approval note")
        refs = normalize_field_references({"note": note}, payload.get("field_references"))
        return {"note": note, **({"field_references": refs} if refs else {})}
    except (CryptoError, ValueError, TypeError, KeyError, AttributeError):
        raise HTTPException(409, "Stored approval note could not be verified") from None


def seal_vote_note(run, actor_id, note, references):
    if not note:
        return None
    if not references:
        return encrypt_message(note, "workflow-vote:" + run.id)
    payload = {"version": 1, "run_id": run.id, "step_index": run.step_index,
               "user_id": actor_id, "note": note, "field_references": references}
    return VOTE_REFERENCE_PREFIX + encrypt_message(json.dumps(payload, separators=(",", ":")), "workflow-vote:" + run.id)


def run_data(session, run, *, viewer_id=None):
    definition = session.get(WorkflowDefinition, run.workflow_id)
    fields = definition_fields(session, definition, run.workflow_revision)
    votes = session.exec(select(WorkflowVote).where(WorkflowVote.run_id == run.id).order_by(WorkflowVote.created_at)).all()
    current_step = fields.steps[run.step_index] if run.step_index < len(fields.steps) else None
    may_vote = bool(run.status == "waiting_approval" and run.deadline_at and run.deadline_at > datetime.utcnow()
        and definition.current_revision == run.workflow_revision and grant_valid(session, definition)
        and current_step and current_step.kind == "approval"
        and viewer_id in eligible(session, run, current_step) and not any(v.user_id == viewer_id and v.step_index == run.step_index for v in votes))
    return {"id": run.id, "workflow_id": run.workflow_id, "workflow_name": fields.name,
        "workflow_revision": run.workflow_revision, "channel_id": run.channel_id, "event_kind": run.event_kind,
        "resource_id": run.resource_id, "status": run.status, "step_index": run.step_index, "step_count": len(fields.steps),
        "due_at": run.due_at, "deadline_at": run.deadline_at, "reminder_count": run.reminder_count,
        "escalated": run.escalated, "failure": run.failure, "created_at": run.created_at,
        "finished_at": run.finished_at, "may_vote": may_vote,
        "external_actions": [{"step_index": receipt.step_index, "job_id": receipt.app_job_id,
            "status": job.status if (job := session.get(AppJob, receipt.app_job_id)) else "unavailable"}
            for receipt in session.exec(select(WorkflowAppReceipt).where(WorkflowAppReceipt.run_id == run.id)).all()],
        "votes": [{"user_id": v.user_id, "step_index": v.step_index, "choice": v.choice, "created_at": v.created_at,
                   "matrix_event_id": v.matrix_event_id,
                   **(native_vote_fields(v, run) if not secure_channel(session, run.channel_id) else {"note": None}),
                   "resolution_error": "An older native note is unavailable in this encrypted channel"
                       if v.encrypted_note and secure_channel(session, run.channel_id) else None} for v in votes]}
