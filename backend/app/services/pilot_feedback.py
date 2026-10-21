"""Explicit self-reports, private author-only notes and SQL-only admin counts.

Nothing observes chat/file/invitation/provider actions. A 'completed' outcome is
only what the participant deliberately reports; it can never verify completed actions.
"""
from datetime import datetime
from hashlib import sha256
import json
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import and_, case, func, or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from ..models.pilot_feedback import PilotFeedback
from ..models.user import User
from ..models.workforce import WorkforcePrincipal
from ..models.workspace import WorkspaceMember
from .account_security import seal_private, unseal_private
from .request_guard import recheck_actor
from .workspaces import WORKSPACE_ROLES, lock_workspace, require_workspace_admin, require_workspace_member

Task = Literal["first_message", "file_share", "invite_team", "connect_service", "find_content"]
TASKS = (
    {"id": "first_message", "title": "Send a first message", "instructions": "Send a message in a channel you can access. Return here to report whether it worked."},
    {"id": "file_share", "title": "Share and open a file", "instructions": "Choose a file you intend to share with the channel's members, upload it, and check that you can open the copy."},
    {"id": "invite_team", "title": "Invite a teammate", "instructions": "If you have permission, create an invitation for an intended teammate. Record any blocker without naming the person."},
    {"id": "connect_service", "title": "Prepare or connect a service", "instructions": "Open the connection center and review a customer-owned service's requirements. Authorize or test a provider only when you intend to connect it."},
    {"id": "find_content", "title": "Find shared content", "instructions": "Use workspace search or channel history to find a message or file you can already access. Report whether the result was useful."},
)
MAX_REVISION = 1000000


class FeedbackWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0, le=MAX_REVISION, strict=True)
    outcome: Literal["completed", "blocked"]
    rating: int | None = Field(default=None, ge=1, le=5, strict=True)
    note: str | None = Field(default=None, max_length=1000)
    consent: bool = Field(default=False, strict=True)

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value):
        return value.strip() or None if value is not None else None


def authorize(session, workspace_id, user_id, *, lock=False, admin=False):
    if lock:
        lock_workspace(session, workspace_id)
        session.expire_all()
    recheck_actor(session, user_id)
    return (require_workspace_admin if admin else require_workspace_member)(session, workspace_id, user_id)


def note_subject(row):
    # Revision and outcome/rating binding prevents swapping notes between authors,
    # tenants, tasks or revisions, or attaching a note to altered public metadata.
    return sha256(json.dumps([row.workspace_id, row.user_id, row.task, row.revision, row.outcome, row.rating],
        ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def private_note(row):
    if not row.encrypted_note:
        return None
    try:
        value = unseal_private(row.encrypted_note, "pilot-feedback-note", note_subject(row))
        if len(value) > 1000: raise ValueError()
        return value
    except (HTTPException, ValueError, TypeError):
        raise HTTPException(503, "Your private feedback note could not be opened") from None


def own_record(row):
    if row.deleted: raise HTTPException(404, "Workspace feedback was removed")
    return {"task": row.task, "outcome": row.outcome, "rating": row.rating, "note": private_note(row), "revision": row.revision,
        "created_at": row.created_at.isoformat() + "Z", "updated_at": row.updated_at.isoformat() + "Z"}


def own_feedback(session, workspace_id, user_id):
    authorize(session, workspace_id, user_id)
    rows = session.exec(select(PilotFeedback).where(PilotFeedback.workspace_id == workspace_id, PilotFeedback.user_id == user_id).limit(5)).all()
    feedback = [own_record(row) for row in rows if not row.deleted]
    revisions = {task["id"]: 0 for task in TASKS}
    revisions.update({row.task: row.revision for row in rows})
    session.expire_all()
    member = authorize(session, workspace_id, user_id)
    order = {task["id"]: index for index, task in enumerate(TASKS)}
    return {"workspace_id": workspace_id, "can_manage": member.role in {"owner", "admin"}, "self_reported": True,
        "tasks": [dict(task) for task in TASKS], "revisions": revisions,
        "feedback": sorted(feedback, key=lambda row: order[row["task"]])}


def save_feedback(session, workspace_id, user_id, task, data):
    if not data.consent: raise HTTPException(422, "Explicit consent is required to submit workspace feedback")
    authorize(session, workspace_id, user_id, lock=True)
    row = session.get(PilotFeedback, (workspace_id, user_id, task), populate_existing=True)
    revision = row.revision if row else 0
    if revision != data.expected_revision: raise HTTPException(409, "Your workspace feedback changed; reload before saving")
    if revision >= MAX_REVISION: raise HTTPException(409, "This feedback task reached its revision limit")
    now = datetime.utcnow()
    if row is None:
        row = PilotFeedback(workspace_id=workspace_id, user_id=user_id, task=task, outcome=data.outcome)
    if row.deleted or revision == 0: row.created_at = now
    row.revision, row.deleted, row.outcome, row.rating, row.updated_at = revision + 1, False, data.outcome, data.rating, now
    row.encrypted_note = seal_private(data.note, "pilot-feedback-note", note_subject(row)) if data.note is not None else None
    # Local encryption/key I/O can outlive a session or scope update.
    authorize(session, workspace_id, user_id)
    session.add(row)
    try: session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "Your workspace feedback changed; reload before saving") from None
    return own_record(row)


def delete_feedback(session, workspace_id, user_id, task, expected_revision):
    authorize(session, workspace_id, user_id, lock=True)
    row = session.get(PilotFeedback, (workspace_id, user_id, task), populate_existing=True)
    if row is None:
        if expected_revision != 0: raise HTTPException(409, "Your workspace feedback changed; reload before removing it")
        return {"task": task, "revision": 0, "deleted": True}
    if row.deleted and expected_revision in {row.revision, row.revision - 1}:
        return {"task": task, "revision": row.revision, "deleted": True}
    if row.revision != expected_revision: raise HTTPException(409, "Your workspace feedback changed; reload before removing it")
    # Erasure remains possible even at the save limit. Stale PUTs at that
    # revision remain rejected by save_feedback's unconditional limit fence.
    row.revision = min(row.revision + 1, MAX_REVISION)
    row.deleted, row.outcome, row.rating, row.encrypted_note, row.created_at, row.updated_at = True, None, None, None, None, None
    authorize(session, workspace_id, user_id)
    session.add(row); session.commit()
    return {"task": task, "revision": row.revision, "deleted": True}


def summary(session, workspace_id, user_id):
    authorize(session, workspace_id, user_id, admin=True)
    # Join current active members, not stored profile text or decrypted notes.
    # Removed, blocked and deactivated organization identities never count as
    # active feedback participants. Each task has one current report per author.
    eligibility = [PilotFeedback.workspace_id == workspace_id, PilotFeedback.deleted.is_(False), User.is_blocked.is_(False),
        WorkspaceMember.role.in_(WORKSPACE_ROLES), or_(WorkforcePrincipal.user_id.is_(None),
            and_(WorkforcePrincipal.workspace_id == workspace_id, WorkforcePrincipal.active.is_(True)))]
    def current_members(query):
        return query.join(User, User.id == PilotFeedback.user_id).join(WorkspaceMember,
            and_(WorkspaceMember.workspace_id == PilotFeedback.workspace_id, WorkspaceMember.user_id == PilotFeedback.user_id)).outerjoin(
                WorkforcePrincipal, WorkforcePrincipal.user_id == PilotFeedback.user_id).where(*eligibility)
    counts = session.exec(current_members(select(PilotFeedback.task,
        func.sum(case((PilotFeedback.outcome == "completed", 1), else_=0)),
        func.sum(case((PilotFeedback.outcome == "blocked", 1), else_=0)),
        func.count(PilotFeedback.rating), func.coalesce(func.sum(PilotFeedback.rating), 0))).group_by(PilotFeedback.task)).all()
    participants = session.exec(current_members(select(func.count(func.distinct(PilotFeedback.user_id)))).select_from(PilotFeedback)).one()
    by_task = {task: {"task": task, "completed": int(completed), "blocked": int(blocked), "rating_count": int(rating_count), "rating_total": int(rating_total)}
        for task, completed, blocked, rating_count, rating_total in counts}
    tasks = [by_task.get(task["id"], {"task": task["id"], "completed": 0, "blocked": 0, "rating_count": 0, "rating_total": 0}) for task in TASKS]
    session.expire_all(); authorize(session, workspace_id, user_id, admin=True)
    return {"workspace_id": workspace_id, "self_reported": True, "pilot_verdict": None, "distinct_participants": int(participants),
        "feedback_count": sum(item["completed"] + item["blocked"] for item in tasks), "tasks": tasks, "notes_included": False}
