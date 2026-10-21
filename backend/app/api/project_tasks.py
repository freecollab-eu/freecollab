"""Project boards and collaborative tasks use channel and workspace membership."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from ..database import get_session
from ..models.project_tasks import ProjectBoard, ProjectTaskRevision, TaskCard
from ..services.access import lock_channel_writes, require_channel_member
from ..services.auth import get_current_user, security
from ..services.project_tasks import SnapshotWrite, fresh_actor, new_revision, revision_for, serialize, validate_snapshot
from ..services.workspaces import require_direct_message_writable


def no_store(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/channels/{channel_id}/projects", tags=["projects and tasks"], dependencies=[Depends(no_store)])


def project_for(session, channel_id, project_id):
    project = session.get(ProjectBoard, project_id)
    if not project or project.channel_id != channel_id:
        raise HTTPException(404, "Project not found in this channel")
    return project


def prepare_write(session, channel_id, actor_id, token):
    lock_channel_writes(session, channel_id)
    actor = fresh_actor(session, token, actor_id)
    member = require_channel_member(session, channel_id, actor.id)
    require_direct_message_writable(session, channel_id, actor.id)
    return actor, member


def save_item(session, kind, item, data, actor, member, fields):
    if item.revision != data.expected_revision:
        raise HTTPException(409, "This item changed; reload its current revision before saving")
    if item.revision >= 1000:
        raise HTTPException(409, "This item reached its revision limit; create a new item")
    if item.revision and item.archived and data.archived:
        raise HTTPException(409, "Reopen this item before changing its content")
    if item.archived != data.archived and item.created_by != actor.id and member.role != "admin":
        raise HTTPException(403, "Only the creator or channel administrator can archive or reopen this item")
    revision = new_revision(kind, item, data, fields, actor.id)
    item.revision, item.archived, item.updated_at = revision.revision, data.archived, datetime.utcnow()
    session.add(item)
    session.add(revision)
    from ..services.webhooks import enqueue_channel_event
    enqueue_channel_event(session, item.channel_id, "channel.activity", revision.id)
    if kind == "task":
        from ..models.workspace import WorkspaceChannel
        from ..services.workflow import queue_event
        mapping = session.get(WorkspaceChannel, item.channel_id)
        if mapping:
            queue_event(session, "task:" + item.id + ":" + str(revision.revision), mapping.workspace_id,
                item.channel_id, "task_updated", item.id, actor_id=actor.id,
                metadata={"status": fields["status"]} if fields is not None else None)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "This item changed; reload its current revision") from None
    return serialize(session, kind, item, revision)


@router.get("")
def projects(channel_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    require_channel_member(session, channel_id, user.id)
    items = session.exec(select(ProjectBoard).where(ProjectBoard.channel_id == channel_id).order_by(ProjectBoard.created_at, ProjectBoard.id).limit(20)).all()
    return [serialize(session, "project", item, revision_for(session, "project", item.id, item.revision)) for item in items]


@router.post("")
def save_project(channel_id: str, data: SnapshotWrite, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    actor, member = prepare_write(session, channel_id, user.id, credentials.credentials)
    item = session.get(ProjectBoard, data.id)
    if item and item.channel_id != channel_id:
        raise HTTPException(404, "Project not found in this channel")
    if not item:
        if data.expected_revision or data.archived:
            raise HTTPException(404, "Project not found")
        count = session.exec(select(func.count()).select_from(ProjectBoard).where(ProjectBoard.channel_id == channel_id)).one()
        if count >= 20:
            raise HTTPException(409, "This channel has reached its 20-project limit")
        item = ProjectBoard(id=data.id, channel_id=channel_id, created_by=actor.id)
        session.add(item)
        session.flush()
    fields = validate_snapshot(session, "project", channel_id, data, actor.id)
    actor, member = prepare_write(session, channel_id, actor.id, credentials.credentials)
    return save_item(session, "project", item, data, actor, member, fields)


@router.get("/{project_id}/tasks")
def tasks(channel_id: str, project_id: str, offset: int = Query(default=0, ge=0, le=1000),
          limit: int = Query(default=200, ge=1, le=200), user=Depends(get_current_user), session=Depends(get_session)):
    require_channel_member(session, channel_id, user.id)
    project_for(session, channel_id, project_id)
    items = session.exec(select(TaskCard).where(TaskCard.project_id == project_id, TaskCard.channel_id == channel_id)
        .order_by(TaskCard.created_at, TaskCard.id).offset(offset).limit(limit + 1)).all()
    return {"items": [serialize(session, "task", item, revision_for(session, "task", item.id, item.revision)) for item in items[:limit]],
            "next_offset": offset + limit if len(items) > limit else None}


@router.post("/{project_id}/tasks")
def save_task(channel_id: str, project_id: str, data: SnapshotWrite, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    actor, member = prepare_write(session, channel_id, user.id, credentials.credentials)
    project = project_for(session, channel_id, project_id)
    if project.archived:
        raise HTTPException(409, "Reopen the project before changing its tasks")
    item = session.get(TaskCard, data.id)
    if item and (item.channel_id != channel_id or item.project_id != project_id):
        raise HTTPException(404, "Task not found in this project")
    if not item:
        if data.expected_revision or data.archived:
            raise HTTPException(404, "Task not found")
        count = session.exec(select(func.count()).select_from(TaskCard).where(TaskCard.channel_id == channel_id)).one()
        if count >= 1000:
            raise HTTPException(409, "This channel has reached its 1,000-task limit")
        item = TaskCard(id=data.id, project_id=project_id, channel_id=channel_id, created_by=actor.id)
        session.add(item)
        session.flush()
    fields = validate_snapshot(session, "task", channel_id, data, actor.id)
    actor, member = prepare_write(session, channel_id, actor.id, credentials.credentials)
    return save_item(session, "task", item, data, actor, member, fields)


@router.get("/{project_id}/revisions")
def project_history(channel_id: str, project_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    require_channel_member(session, channel_id, user.id)
    item = project_for(session, channel_id, project_id)
    revisions = session.exec(select(ProjectTaskRevision).where(ProjectTaskRevision.kind == "project", ProjectTaskRevision.item_id == item.id)
                            .order_by(ProjectTaskRevision.revision.desc()).limit(1000)).all()
    return [serialize(session, "project", item, revision) for revision in revisions]


@router.get("/{project_id}/tasks/{task_id}/revisions")
def task_history(channel_id: str, project_id: str, task_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    require_channel_member(session, channel_id, user.id)
    project_for(session, channel_id, project_id)
    item = session.get(TaskCard, task_id)
    if not item or item.project_id != project_id or item.channel_id != channel_id:
        raise HTTPException(404, "Task not found in this project")
    revisions = session.exec(select(ProjectTaskRevision).where(ProjectTaskRevision.kind == "task", ProjectTaskRevision.item_id == item.id)
                            .order_by(ProjectTaskRevision.revision.desc()).limit(1000)).all()
    return [serialize(session, "task", item, revision) for revision in revisions]
