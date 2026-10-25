"""Customer-owned Slack app configuration and signed Events API ingress."""
from datetime import datetime
import hashlib
import json
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field
from sqlmodel import select
from ..config import settings
from ..database import get_session
from ..models.slack_livebridge import SlackBridge, SlackBridgeJob
from ..services import slack_livebridge as service
from ..services import app_platform
from ..services.account_security import account_limit
from ..services.access import lock_channel_writes
from ..services.auth import get_current_user
from ..services.request_guard import recheck_actor
from ..services.workspaces import lock_workspace, require_workspace_admin, require_workspace_member

router = APIRouter(tags=["slack-livebridge"])


def find(session, workspace_id, bridge_id):
    row = session.get(SlackBridge, bridge_id, populate_existing=True)
    if not row or row.workspace_id != workspace_id: raise HTTPException(404, "Bridge not found")
    return row


def permitted(session, row, actor_id):
    require_workspace_admin(session, row.workspace_id, actor_id)
    app_platform.native_channel(session, row.workspace_id, row.channel_id, actor_id)


def private_instance():
    if getattr(settings, "demo_instance", False):
        raise HTTPException(403, "Slack is not connected in this public example")


@router.get("/workspaces/{workspace_id}/channels/{channel_id}/slack-bridges")
def channel_status(workspace_id: str, channel_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    app_platform.native_channel(session, workspace_id, channel_id, user.id)
    rows = session.exec(select(SlackBridge).where(SlackBridge.workspace_id == workspace_id,
        SlackBridge.channel_id == channel_id, SlackBridge.revoked_at == None)).all()
    try:
        app_platform.install_grants(session, workspace_id, user.id, [channel_id]); can_manage = True
    except HTTPException:
        can_manage = False
    return {"bridges": [service.public(row) for row in rows], "can_manage": can_manage,
        "public_example": bool(getattr(settings, "demo_instance", False))}


@router.post("/workspaces/{workspace_id}/slack-bridges/{bridge_id}/test")
def test_connection(workspace_id: str, bridge_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    private_instance(); account_limit(session, "slack-bridge-test", user.id, 20, 3600)
    row = find(session, workspace_id, bridge_id); permitted(session, row, user.id)
    result = service.slack_request("auth.test", {}, service.config(row)["bot_token"])
    if result.get("ok") is not True or result.get("team_id") != row.slack_team_id or result.get("user_id") != row.slack_bot_user_id:
        raise HTTPException(409, "Slack credentials or workspace/bot mapping could not be verified")
    return {"verified": True, "team_id": row.slack_team_id, "bot_user_id": row.slack_bot_user_id,
        "scope": "Account identity only; subscribe your Slack app to message events and invite the bot to the mapped channel"}


@router.post("/workspaces/{workspace_id}/slack-bridges", status_code=201)
def create(workspace_id: str, data: service.BridgeInput, user=Depends(get_current_user), session=Depends(get_session)):
    private_instance(); account_limit(session, "slack-bridge-config", user.id, 20, 3600)
    recheck_actor(session, user.id)
    row = service.save(session, workspace_id, user.id, data); session.commit()
    return {"bridge": service.public(row), "events_path": "/slack/bridges/" + row.id + "/events"}


@router.put("/workspaces/{workspace_id}/slack-bridges/{bridge_id}")
def configure(workspace_id: str, bridge_id: str, data: service.BridgeInput, user=Depends(get_current_user), session=Depends(get_session)):
    private_instance(); account_limit(session, "slack-bridge-config", user.id, 20, 3600)
    row = find(session, workspace_id, bridge_id); permitted(session, row, user.id)
    recheck_actor(session, user.id)
    row = service.save(session, workspace_id, user.id, data, row); session.commit()
    return {"bridge": service.public(row), "events_path": "/slack/bridges/" + row.id + "/events"}


@router.post("/workspaces/{workspace_id}/slack-bridges/{bridge_id}/pause")
def pause(workspace_id: str, bridge_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    lock_workspace(session, workspace_id); session.expire_all(); recheck_actor(session, user.id)
    require_workspace_admin(session, workspace_id, user.id)
    row = find(session, workspace_id, bridge_id)
    row.enabled = False; row.generation += 1; row.last_status = "paused"; row.updated_at = datetime.utcnow()
    service.cancel_pending(session, row); session.add(row); session.commit()
    return service.public(row)


@router.delete("/workspaces/{workspace_id}/slack-bridges/{bridge_id}")
def revoke(workspace_id: str, bridge_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    lock_workspace(session, workspace_id); session.expire_all(); recheck_actor(session, user.id)
    require_workspace_admin(session, workspace_id, user.id)
    row = find(session, workspace_id, bridge_id)
    row.enabled = False; row.generation += 1; row.revoked_at = datetime.utcnow(); row.encrypted_config = ""; row.last_status = "revoked"
    service.cancel_pending(session, row); session.add(row); session.commit()
    return {"revoked": True}


@router.get("/workspaces/{workspace_id}/slack-bridges/{bridge_id}/jobs")
def jobs(workspace_id: str, bridge_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    row = find(session, workspace_id, bridge_id); permitted(session, row, user.id)
    rows = session.exec(select(SlackBridgeJob).where(SlackBridgeJob.bridge_id == row.id).order_by(SlackBridgeJob.created_at.desc()).limit(100)).all()
    return {"jobs": [service.job_data(job) for job in rows]}


class Retry(app_platform.Strict):
    confirm_slack_has_not_processed: bool


@router.post("/workspaces/{workspace_id}/slack-bridges/{bridge_id}/jobs/{job_id}/retry")
def retry(workspace_id: str, bridge_id: str, job_id: str, data: Retry, user=Depends(get_current_user), session=Depends(get_session)):
    private_instance(); lock_workspace(session, workspace_id); session.expire_all(); recheck_actor(session, user.id)
    row = find(session, workspace_id, bridge_id); permitted(session, row, user.id)
    job = session.get(SlackBridgeJob, job_id)
    if not job or job.bridge_id != row.id: raise HTTPException(404, "Bridge job not found")
    if (not data.confirm_slack_has_not_processed or not service.grant(session, row) or row.generation != job.generation
            or job.status not in {"uncertain", "failed"} or not job.encrypted_payload):
        raise HTTPException(409, "Inspect Slack and confirm this unchanged request was not processed before retrying")
    job.status = "pending"; job.attempts = 0; job.claim_token = None; job.failure = None; job.finished_at = None; job.next_attempt_at = datetime.utcnow()
    session.add(job); session.commit(); return service.job_data(job)


@router.post("/slack/bridges/{bridge_id}/events")
async def events(bridge_id: str, request: Request, session=Depends(get_session)):
    private_instance()
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > 65536: raise HTTPException(413, "Slack event exceeds64 KB")
        chunks.append(chunk)
    body = b"".join(chunks)
    row = session.get(SlackBridge, bridge_id)
    if not row or not service.grant(session, row): raise HTTPException(401, "Slack bridge unavailable")
    original_generation = row.generation
    signing_digest = service.verify_signature(row, body, request.headers)
    try:
        packet = json.loads(body, object_pairs_hook=app_platform.copilot.transport._pairs, parse_constant=app_platform.copilot.transport._constant)
        if not isinstance(packet, dict): raise ValueError()
    except Exception: raise HTTPException(422, "Invalid Slack event") from None
    lock_workspace(session, row.workspace_id); lock_channel_writes(session, row.channel_id); session.expire_all()
    row = session.get(SlackBridge, bridge_id)
    if not service.grant(session, row) or original_generation != row.generation:
        raise HTTPException(403, "Slack bridge authorization changed")
    if signing_digest != hashlib.sha256(service.config(row)["signing_secret"].encode()).hexdigest():
        raise HTTPException(403, "Slack signing secret changed")
    if packet.get("type") == "url_verification":
        challenge = packet.get("challenge")
        if not isinstance(challenge, str) or not 1 <= len(challenge) <= 256: raise HTTPException(422, "Invalid Slack challenge")
        return {"challenge": challenge}
    result = service.incoming(session, row, packet); session.commit(); return result
