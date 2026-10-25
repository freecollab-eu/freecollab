"""One current, permission-checked run/step read model for existing engines.

This is a projection of actual persisted execution states, not synthetic jobs.
Each engine retains its transactional claim and reviewed-write implementation.
"""
from fastapi import HTTPException
from sqlmodel import select

from ..models.ai_runtime import AIRun, AIRunStep, AIUsage
from ..models.app_platform import AppAIDraft, AppJob, ChannelRecapSchedule
from ..models.copilot import CopilotConversation, CopilotTurn
from ..models.workflow import WorkflowRun
from . import copilot
from .workspaces import require_workspace_member


def timeline(session, workspace_id, actor_id, *, limit=100):
    require_workspace_member(session, workspace_id, actor_id)
    limit = max(1, min(limit, 100))
    items = []
    truncated = False

    def window(query):
        nonlocal truncated
        rows = session.exec(query.limit(limit + 1)).all()
        truncated = truncated or len(rows) > limit
        return rows

    def add(family, row, status, *, channel_id, created_at, source=None, index=0, attempts=1, profile_revision=None, execution_id=None, timestamp_kind="created_at"):
        try:
            copilot.grant(session, workspace_id, channel_id, actor_id)
        except HTTPException:
            return
        usage = session.exec(select(AIUsage).where(AIUsage.workspace_id == workspace_id, AIUsage.user_id == actor_id,
            AIUsage.execution_id == (execution_id or family + ":" + row.id))).all()
        items.append({"id": family + ":" + row.id, "source_id": row.id, "family": family, "channel_id": channel_id,
            "status": status, "created_at": created_at.isoformat() if timestamp_kind == "created_at" else None,
            "timestamp": created_at.isoformat(), "timestamp_kind": timestamp_kind,
            "updated_at": getattr(row, "updated_at", None).isoformat() if getattr(row, "updated_at", None) else created_at.isoformat(),
            "profile_revision": profile_revision, "steps": [{"index": index, "status": status, "attempts": attempts}],
            "usage": {"input_tokens": sum(item.input_tokens for item in usage), "output_tokens": sum(item.output_tokens for item in usage),
                "charged_tokens": sum(item.charged_tokens for item in usage), "estimated_cost_micros": sum(item.estimated_cost_micros for item in usage),
                "unknown_attempts": sum(item.status == "unknown" for item in usage), "cost_is_estimate": True},
            "source": source or {"channel_id": channel_id}, "content_included": False})

    for row in window(select(AIRun).where(AIRun.workspace_id == workspace_id, AIRun.user_id == actor_id).order_by(AIRun.created_at.desc())):
        add("run", row, row.status, channel_id=row.channel_id, created_at=row.created_at, source={"run_id": row.id, "channel_id": row.channel_id}, profile_revision=row.profile_revision, execution_id=row.id)
        if items and items[-1]["id"] == "run:" + row.id:
            items[-1]["operation"] = row.operation
            items[-1]["steps"] = [{"index": step.index, "status": step.status, "attempts": step.attempts}
                for step in session.exec(select(AIRunStep).where(AIRunStep.run_id == row.id).order_by(AIRunStep.index)).all()]
    conversations = session.exec(select(CopilotConversation).where(CopilotConversation.workspace_id == workspace_id, CopilotConversation.user_id == actor_id)).all()
    by_id = {row.id: row for row in conversations}
    if by_id:
        for row in window(select(CopilotTurn).where(CopilotTurn.conversation_id.in_(by_id)).order_by(CopilotTurn.created_at.desc())):
            conversation = by_id[row.conversation_id]
            status = {"processing": "running", "complete": "completed", "failed": "failed"}.get(row.status, row.status)
            add("copilot", row, status, channel_id=conversation.channel_id, created_at=row.created_at,
                source={"conversation_id": conversation.id, "turn_id": row.id, "channel_id": conversation.channel_id}, profile_revision=row.config_revision, execution_id="turn:" + row.id)
    for row in window(select(AppAIDraft).where(AppAIDraft.workspace_id == workspace_id, AppAIDraft.owner_id == actor_id).order_by(AppAIDraft.created_at.desc())):
        status = {"draft": "awaiting_review", "approved": "completed", "rejected": "cancelled"}.get(row.status, row.status)
        add("app-draft", row, status, channel_id=row.channel_id, created_at=row.created_at, profile_revision=row.provider_revision,
            source={"tab": "apps", "draft_id": row.id, "channel_id": row.channel_id})
    for row in window(select(ChannelRecapSchedule).where(ChannelRecapSchedule.workspace_id == workspace_id, ChannelRecapSchedule.owner_id == actor_id).order_by(ChannelRecapSchedule.next_run_at.desc())):
        status = "queued" if row.enabled else "failed" if row.last_status and row.last_status.startswith("paused") else "cancelled"
        add("recap", row, status, channel_id=row.channel_id, created_at=row.last_run_at or row.next_run_at,
            source={"tab": "apps", "schedule_id": row.id, "channel_id": row.channel_id}, profile_revision=row.provider_revision,
            timestamp_kind="last_run_at" if row.last_run_at else "next_run_at")
    for row in window(select(WorkflowRun).where(WorkflowRun.workspace_id == workspace_id).order_by(WorkflowRun.created_at.desc())):
        status = {"waiting_approval": "awaiting_review", "rejected": "cancelled", "timed_out": "failed"}.get(row.status, row.status)
        add("workflow", row, status, channel_id=row.channel_id, created_at=row.created_at, index=row.step_index, attempts=row.attempts,
            source={"tab": "workflows", "run_id": row.id, "workflow_id": row.workflow_id, "channel_id": row.channel_id})
    for row in window(select(AppJob).where(AppJob.actor_id == actor_id).order_by(AppJob.created_at.desc())):
        from ..models.app_platform import AppInstallation
        installation = session.get(AppInstallation, row.installation_id)
        if not installation or installation.workspace_id != workspace_id:
            continue
        status = {"pending": "queued", "processing": "running", "delivered": "completed", "needs_review": "awaiting_review"}.get(row.status, row.status)
        add("app-command", row, status, channel_id=row.channel_id, created_at=row.created_at, attempts=row.attempts,
            source={"tab": "apps", "job_id": row.id, "channel_id": row.channel_id})
    items.sort(key=lambda item: (item["updated_at"], item["id"]), reverse=True)
    return {"items": items[:limit], "complete": not truncated and len(items) <= limit, "source_engines": ["run", "copilot", "app-draft", "recap", "workflow", "app-command"],
        "writes_performed": False, "content_included": False}
