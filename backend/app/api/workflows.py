"""No-code channel workflow configuration and current-member approvals."""
from datetime import datetime
import json
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, update
from sqlmodel import select

from ..database import get_session
from ..models.workflow import WorkflowActivity, WorkflowDefinition, WorkflowRevision, WorkflowRun, WorkflowVote
from ..services import workflow as service
from ..services.account_security import account_limit, ensure_reauthentication
from ..services.auth import get_current_user, security
from ..services.crypto import encrypt_message
from ..services.field_references import ReferencedFields, require_field_reference_targets


def no_store(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/workspaces/{workspace_id}/channels/{channel_id}/workflows", tags=["workflows"],
                   dependencies=[Depends(no_store)])


class Confirmation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    password: str = Field(min_length=1, max_length=256)
    code: str | None = Field(default=None, max_length=128)


class SaveWorkflow(Confirmation):
    fields: service.DefinitionFields
    expected_revision: int = Field(default=0, ge=0)
    enabled: bool = True


class Vote(ReferencedFields):
    model_config = ConfigDict(extra="forbid", strict=True)
    choice: str = Field(pattern="^(approve|reject)$")
    note: str | None = Field(default=None, max_length=2000)
    matrix_event_id: str | None = Field(default=None, min_length=1, max_length=1024)

    def reference_texts(self):
        return {"note": self.note or ""}


def definition_for(session, workspace_id, channel_id, identifier):
    row = session.get(WorkflowDefinition, identifier, populate_existing=True)
    if not row or row.workspace_id != workspace_id or row.channel_id != channel_id:
        raise HTTPException(404, "Workflow not found in this channel")
    return row


def run_for(session, workspace_id, channel_id, identifier):
    row = session.get(WorkflowRun, identifier, populate_existing=True)
    if not row or row.workspace_id != workspace_id or row.channel_id != channel_id:
        raise HTTPException(404, "Workflow run not found in this channel")
    return row


def confirm(session, workspace_id, channel_id, user, token, data):
    account_limit(session, "workflow-config", user.id, 30, 3600)
    actor = service.lock_actor(session, workspace_id, channel_id, user.id, token, admin=True)
    ensure_reauthentication(session, actor, data.password, data.code)
    return actor


@router.get("")
def list_definitions(workspace_id: str, channel_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    service.require_channel(session, workspace_id, channel_id, user.id)
    rows = session.exec(select(WorkflowDefinition).where(WorkflowDefinition.workspace_id == workspace_id,
        WorkflowDefinition.channel_id == channel_id).order_by(WorkflowDefinition.created_at, WorkflowDefinition.id)
        .limit(service.MAX_DEFINITIONS)).all()
    try:
        service.require_channel(session, workspace_id, channel_id, user.id, admin=True)
        can_manage = True
    except HTTPException:
        can_manage = False
    return {"enabled": bool(getattr(service.settings, "workflows_enabled", True)), "can_manage": can_manage,
            "secure_channel": service.secure_channel(session, channel_id),
            "definitions": [service.definition_data(session, row) for row in rows]}


def save_definition(workspace_id, channel_id, identifier, data, user, token, session):
    account_limit(session, "workflow-config", user.id, 30, 3600)
    actor = service.lock_actor(session, workspace_id, channel_id, user.id, token, admin=True)
    row = definition_for(session, workspace_id, channel_id, identifier) if identifier else None
    expected = row.current_revision if row else 0
    if data.expected_revision != expected:
        raise HTTPException(409, "Workflow changed. Refresh before saving.")
    service.validate_fields(session, workspace_id, channel_id, data.fields)
    if not row:
        count = session.exec(select(func.count()).select_from(WorkflowDefinition).where(
            WorkflowDefinition.channel_id == channel_id)).one()
        if count >= service.MAX_DEFINITIONS:
            raise HTTPException(409, "This channel has the maximum of 20 workflows")
    ensure_reauthentication(session, actor, data.password, data.code)
    if row is None:
        row = WorkflowDefinition(workspace_id=workspace_id, channel_id=channel_id,
            created_by=actor.id, granted_by=actor.id, trigger=data.fields.trigger, enabled=data.enabled)
        session.add(row)
        session.flush()
    else:
        service.cancel_previous(session, row)
        row.current_revision += 1
        row.granted_by, row.trigger, row.enabled = actor.id, data.fields.trigger, data.enabled
        row.updated_at = datetime.utcnow()
    payload = {**data.fields.model_dump(mode="json"), "workflow_id": row.id, "revision": row.current_revision}
    session.add(WorkflowRevision(workflow_id=row.id, revision=row.current_revision, created_by=actor.id,
        encrypted_definition=encrypt_message(json.dumps(payload, separators=(",", ":")), "workflow:" + channel_id)))
    session.add(row)
    session.commit()
    return service.definition_data(session, row)


@router.post("", status_code=201)
def create(workspace_id: str, channel_id: str, data: SaveWorkflow, user=Depends(get_current_user),
           credentials=Depends(security), session=Depends(get_session)):
    return save_definition(workspace_id, channel_id, None, data, user, credentials.credentials, session)


@router.put("/{workflow_id}")
def save(workspace_id: str, channel_id: str, workflow_id: str, data: SaveWorkflow, user=Depends(get_current_user),
         credentials=Depends(security), session=Depends(get_session)):
    return save_definition(workspace_id, channel_id, workflow_id, data, user, credentials.credentials, session)


@router.post("/{workflow_id}/disable")
def disable(workspace_id: str, channel_id: str, workflow_id: str, data: Confirmation, user=Depends(get_current_user),
            credentials=Depends(security), session=Depends(get_session)):
    confirm(session, workspace_id, channel_id, user, credentials.credentials, data)
    row = definition_for(session, workspace_id, channel_id, workflow_id)
    row.enabled, row.updated_at = False, datetime.utcnow()
    service.cancel_previous(session, row)
    session.add(row)
    session.commit()
    return service.definition_data(session, row)


@router.get("/{workflow_id}/revisions")
def revisions(workspace_id: str, channel_id: str, workflow_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    service.require_channel(session, workspace_id, channel_id, user.id)
    row = definition_for(session, workspace_id, channel_id, workflow_id)
    entries = session.exec(select(WorkflowRevision).where(WorkflowRevision.workflow_id == row.id)
        .order_by(WorkflowRevision.revision.desc()).limit(100)).all()
    return {"revisions": [{"revision": revision.revision, "created_by": revision.created_by,
        "created_at": revision.created_at, "fields": service.definition_fields(session, row, revision.revision).model_dump(mode="json")} for revision in entries]}


@router.post("/{workflow_id}/run", status_code=202)
def run_manual(workspace_id: str, channel_id: str, workflow_id: str, user=Depends(get_current_user),
               credentials=Depends(security), session=Depends(get_session)):
    account_limit(session, "workflow-manual", user.id, 30, 3600)
    service.lock_actor(session, workspace_id, channel_id, user.id, credentials.credentials)
    if not getattr(service.settings, "workflows_enabled", True):
        raise HTTPException(503, "Workflow processing is disabled on this installation")
    row = definition_for(session, workspace_id, channel_id, workflow_id)
    if not row.enabled or row.trigger != "manual" or not service.grant_valid(session, row):
        raise HTTPException(409, "This workflow is not available for manual runs")
    event = "manual:" + secrets.token_hex(16)
    service.queue_event(session, event, workspace_id, channel_id, "manual", None, actor_id=user.id, workflow_id=row.id)
    session.commit()
    run = session.exec(select(WorkflowRun).where(WorkflowRun.workflow_id == row.id, WorkflowRun.event_id == event)).first()
    if not run:
        raise HTTPException(409, "This workflow's actor condition did not match")
    return {"id": run.id, "status": run.status}


@router.get("/runs/history")
def runs(workspace_id: str, channel_id: str, limit: int = Query(default=50, ge=1, le=100),
         before: datetime | None = None, user=Depends(get_current_user), session=Depends(get_session)):
    service.require_channel(session, workspace_id, channel_id, user.id)
    query = select(WorkflowRun).where(WorkflowRun.workspace_id == workspace_id, WorkflowRun.channel_id == channel_id)
    if before:
        if before.tzinfo:
            from datetime import timezone
            before = before.astimezone(timezone.utc).replace(tzinfo=None)
        query = query.where(WorkflowRun.created_at < before)
    rows = session.exec(query.order_by(WorkflowRun.created_at.desc(), WorkflowRun.id).limit(limit)).all()
    return {"runs": [service.run_data(session, row, viewer_id=user.id) for row in rows]}


@router.get("/runs/{run_id}")
def read_run(workspace_id: str, channel_id: str, run_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    service.require_channel(session, workspace_id, channel_id, user.id)
    row = run_for(session, workspace_id, channel_id, run_id)
    events = session.exec(select(WorkflowActivity).where(WorkflowActivity.run_id == row.id)
        .order_by(WorkflowActivity.created_at, WorkflowActivity.id).limit(200)).all()
    return {**service.run_data(session, row, viewer_id=user.id), "activity": [
        {"kind": entry.kind, "step_index": entry.step_index, "actor_id": entry.actor_id, "created_at": entry.created_at} for entry in events]}


def active_approval(session, workspace_id, channel_id, run_id, actor, token):
    service.lock_actor(session, workspace_id, channel_id, actor, token)
    row = run_for(session, workspace_id, channel_id, run_id)
    definition = definition_for(session, workspace_id, channel_id, row.workflow_id)
    if row.status != "waiting_approval" or row.workflow_revision != definition.current_revision or not service.grant_valid(session, definition):
        raise HTTPException(409, "This approval is no longer active")
    if not row.deadline_at or row.deadline_at <= datetime.utcnow():
        raise HTTPException(409, "This approval has expired")
    fields = service.definition_fields(session, definition, row.workflow_revision)
    step = fields.steps[row.step_index]
    if step.kind != "approval" or actor not in service.eligible(session, row, step):
        raise HTTPException(403, "You are not a current approver for this step")
    return row


@router.post("/runs/{run_id}/vote")
def vote(workspace_id: str, channel_id: str, run_id: str, data: Vote, user=Depends(get_current_user),
         credentials=Depends(security), session=Depends(get_session)):
    account_limit(session, "workflow-vote", user.id, 60, 3600)
    actor = user.id
    row = active_approval(session, workspace_id, channel_id, run_id, actor, credentials.credentials)
    expected_step, expected_revision = row.step_index, row.workflow_revision
    secure = service.secure_channel(session, channel_id)
    if secure and (data.note is not None or "field_references" in data.model_fields_set):
        raise HTTPException(409, "Approval notes in this channel require an encrypted event")
    previous = session.get(WorkflowVote, (row.id, row.step_index, user.id))
    if previous:
        if previous.choice != data.choice or previous.matrix_event_id != data.matrix_event_id:
            raise HTTPException(409, "Your approval choice was already recorded")
        if data.note is not None and not service.secure_channel(session, channel_id):
            stored = service.native_vote_fields(previous, row)
            if stored.get("note") != data.note or stored.get("field_references", {}) != data.model_dump(mode="json").get("field_references", {}):
                raise HTTPException(409, "Your approval note was already recorded")
        return service.run_data(session, row, viewer_id=user.id)
    if secure:
        if data.matrix_event_id:
            from ..services.matrix_bridge import validate_secure_event
            validate_secure_event(session, channel_id, data.matrix_event_id, user.id)
    elif data.matrix_event_id:
        raise HTTPException(400, "Encrypted note references require an encrypted channel")
    refs = data.model_dump(mode="json").get("field_references", {})
    if not secure:
        require_field_reference_targets(session, channel_id, actor, {"note": data.note or "", "field_references": refs})
    row = active_approval(session, workspace_id, channel_id, run_id, actor, credentials.credentials)
    if (row.step_index, row.workflow_revision) != (expected_step, expected_revision):
        raise HTTPException(409, "This approval step changed; review the current step before approving")
    if service.secure_channel(session, channel_id) != secure:
        raise HTTPException(409, "Channel encryption changed; reload before approving")
    record = WorkflowVote(run_id=row.id, step_index=row.step_index, user_id=actor, choice=data.choice,
        matrix_event_id=data.matrix_event_id,
        encrypted_note=service.seal_vote_note(row, actor, data.note, refs))
    session.add(record)
    service.activity(session, row, "approval_" + data.choice, user.id)
    row.due_at = datetime.utcnow()
    session.add(row)
    session.commit()
    return service.run_data(session, row, viewer_id=user.id)


@router.post("/runs/{run_id}/cancel")
def cancel(workspace_id: str, channel_id: str, run_id: str, data: Confirmation, user=Depends(get_current_user),
           credentials=Depends(security), session=Depends(get_session)):
    confirm(session, workspace_id, channel_id, user, credentials.credentials, data)
    row = run_for(session, workspace_id, channel_id, run_id)
    if row.status not in service.TERMINAL:
        service.finish(session, row, "cancelled", reason="cancelled_by_administrator")
        service.activity(session, row, "administrator_cancelled", user.id)
        session.commit()
    return service.run_data(session, row, viewer_id=user.id)
