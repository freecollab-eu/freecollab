"""Customer-owned app installation, bounded bot callbacks and AI drafts."""
from datetime import datetime
import hashlib
import hmac
import json
import re
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import Field
from sqlalchemy import func, update
from sqlmodel import select

from ..database import get_session
from ..models.app_platform import (AppAIDraft, AppCallback, AppCard, AppForegroundReceipt, AppInstallation, AppJob,
                                   AppRevision, ChannelRecapSchedule)
from ..models.operations import WorkerHeartbeat
from ..services import app_platform as service
from ..services.account_security import account_limit, unseal_private
from ..services.access import lock_channel_writes
from ..services.auth import get_current_user, security
from ..services.request_guard import recheck_actor
from ..services.workspaces import lock_workspace, require_workspace_admin, require_workspace_member


def no_store(response: Response):
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(tags=["apps"], dependencies=[Depends(no_store)])


def installation(session, workspace_id, identifier):
    row = session.get(AppInstallation, identifier, populate_existing=True)
    if not row or row.workspace_id != workspace_id:
        raise HTTPException(404, "App installation not found")
    return row


def manage(session, row, user_id):
    service.install_grants(session, row.workspace_id, user_id, service.channels(row), allow_secure=service.manifest_for(row).secure_foreground)


def lock_actor(session, workspace_id, user_id, channel_id=None):
    service.lock_invoking_account(session, user_id)
    lock_workspace(session, workspace_id)
    if channel_id:
        lock_channel_writes(session, channel_id)
    session.expire_all()
    recheck_actor(session, user_id)


@router.get("/workspaces/{workspace_id}/apps/catalog")
def catalog(workspace_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    require_workspace_member(session, workspace_id, user.id)
    return {"recipes": service.catalog(), "scopes": sorted(service.SCOPES), "content_policy": "explicit_native_arguments_only"}


@router.get("/workspaces/{workspace_id}/apps")
def installed(workspace_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    require_workspace_member(session, workspace_id, user.id)
    rows = session.exec(select(AppInstallation).where(AppInstallation.workspace_id == workspace_id).order_by(AppInstallation.created_at.desc()).limit(100)).all()
    visible = []
    for row in rows:
        accessible = []
        for channel_id in service.channels(row):
            try:
                service.native_channel(session, workspace_id, channel_id, user.id, allow_secure=service.manifest_for(row).secure_foreground)
                accessible.append(channel_id)
            except HTTPException:
                pass
        if accessible:
            data = service.installation_data(row)
            data["channel_ids"] = accessible
            visible.append(data)
    heartbeat = session.get(WorkerHeartbeat, "integrations")
    from datetime import timedelta
    ready = bool(heartbeat and heartbeat.last_seen_at > datetime.utcnow()-timedelta(seconds=90))
    return {"installations": visible, "worker_ready": ready, "delivery": "queued_until_integration_worker_runs"}


@router.get("/workspaces/{workspace_id}/channels/{channel_id}/apps/commands")
def composer_commands(workspace_id: str, channel_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    from ..services import app_foreground
    secure = service.is_secure_channel(session, channel_id)
    if secure:
        app_foreground.secure_context(session, workspace_id, channel_id, user.id)
    else:
        service.native_channel(session, workspace_id, channel_id, user.id)
    commands = []
    rows = session.exec(select(AppInstallation).where(AppInstallation.workspace_id == workspace_id,
        AppInstallation.enabled == True, AppInstallation.revoked_at == None).order_by(AppInstallation.id).limit(100)).all()
    for row in rows:
        if channel_id not in service.channels(row) or not service.allowed_installation(session, row):
            continue
        manifest = service.manifest_for(row)
        if "commands:invoke" not in manifest.scopes or secure and not manifest.secure_foreground:
            continue
        for command in manifest.commands:
            commands.append({"key": row.id + ":" + command.name, "installation_id": row.id,
                "app_id": row.app_id, "app_name": manifest.name, "generation": row.generation,
                "origin": row.origin, "supports_secure_foreground": manifest.secure_foreground,
                "delivery": "foreground" if secure else "queued", **command.model_dump()})
    return {"commands": commands}


class ComposerInvocation(service.Strict):
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,80}$")
    expected_generation: int = Field(ge=1)
    command: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    arguments: dict[str, service.ReviewedArgument] = Field(default_factory=dict)
    confirm: bool
    disclose_to_app: bool


class ForegroundInvocation(ComposerInvocation):
    disclose_decrypted_content: bool
    matrix_device_id: str = Field(min_length=1, max_length=255)


@router.post("/workspaces/{workspace_id}/channels/{channel_id}/apps/foreground/{installation_id}")
def foreground_invoke(workspace_id: str, channel_id: str, installation_id: str, data: ForegroundInvocation,
                      user=Depends(get_current_user), session=Depends(get_session)):
    from ..services import app_foreground
    account_limit(session, "app-command", user.id, 60, 3600)
    lock_actor(session, workspace_id, user.id, channel_id)
    row = installation(session, workspace_id, installation_id)
    return app_foreground.invoke(session, row, channel_id, user.id, data)


@router.post("/workspaces/{workspace_id}/channels/{channel_id}/apps/commands/{installation_id}", status_code=202)
def composer_invoke(workspace_id: str, channel_id: str, installation_id: str, data: ComposerInvocation,
                    user=Depends(get_current_user), session=Depends(get_session)):
    account_limit(session, "app-command", user.id, 60, 3600)
    lock_actor(session, workspace_id, user.id, channel_id)
    row = installation(session, workspace_id, installation_id)
    request = service.Invocation(channel_id=channel_id, **data.model_dump())
    job = service.queue_invocation(session, row, user.id, request)
    session.commit()
    return service.job_data(job)


@router.post("/workspaces/{workspace_id}/apps", status_code=201)
def install(workspace_id: str, data: service.InstallInput, user=Depends(get_current_user), session=Depends(get_session)):
    account_limit(session, "app-install", user.id, 30, 3600)
    row, secret = service.save_installation(session, workspace_id, user.id, data)
    session.commit()
    return {"installation": service.installation_data(row), "signing_secret": secret,
            "callback_path": "/apps/"+row.id+"/callback", "secret_shown_once": True}


@router.put("/workspaces/{workspace_id}/apps/{installation_id}")
def update_install(workspace_id: str, installation_id: str, data: service.InstallInput,
                   user=Depends(get_current_user), session=Depends(get_session)):
    account_limit(session, "app-install", user.id, 30, 3600)
    old = installation(session, workspace_id, installation_id)
    row, secret = service.save_installation(session, workspace_id, user.id, data, row=old)
    session.commit()
    return {"installation": service.installation_data(row), "signing_secret": secret,
            "callback_path": "/apps/"+row.id+"/callback", "secret_shown_once": True}


@router.delete("/workspaces/{workspace_id}/apps/{installation_id}")
def revoke(workspace_id: str, installation_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    lock_actor(session, workspace_id, user.id)
    row = installation(session, workspace_id, installation_id)
    # Revocation remains available after a channel grant or membership was lost.
    require_workspace_admin(session, workspace_id, user.id)
    service.require_unrestricted(session)
    row.enabled, row.revoked_at = False, datetime.utcnow()
    row.generation += 1
    row.encrypted_secret = row.encrypted_config = ""
    service.cancel_pending(session, row)
    session.add(row)
    session.commit()
    return {"revoked": True}


@router.get("/workspaces/{workspace_id}/apps/{installation_id}/revisions")
def revisions(workspace_id: str, installation_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    row = installation(session, workspace_id, installation_id)
    manage(session, row, user.id)
    rows = session.exec(select(AppRevision).where(AppRevision.installation_id == row.id).order_by(AppRevision.generation.desc()).limit(100)).all()
    return {"revisions": [{"generation": entry.generation, "manifest": json.loads(entry.manifest_json),
        "channel_ids": json.loads(entry.channel_ids_json), "granted_by": entry.granted_by, "created_at": entry.created_at} for entry in rows]}


@router.post("/workspaces/{workspace_id}/apps/{installation_id}/commands", status_code=202)
def invoke(workspace_id: str, installation_id: str, data: service.Invocation,
           user=Depends(get_current_user), session=Depends(get_session)):
    account_limit(session, "app-command", user.id, 60, 3600)
    lock_actor(session, workspace_id, user.id, data.channel_id)
    row = installation(session, workspace_id, installation_id)
    job = service.queue_invocation(session, row, user.id, data)
    session.commit()
    return service.job_data(job)


@router.get("/workspaces/{workspace_id}/apps/{installation_id}/jobs")
def jobs(workspace_id: str, installation_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    from datetime import timedelta
    from ..services.app_foreground import receipt_data
    row = installation(session, workspace_id, installation_id)
    manage(session, row, user.id)
    rows = session.exec(select(AppJob).where(AppJob.installation_id == row.id).order_by(AppJob.created_at.desc()).limit(100)).all()
    foreground = session.exec(select(AppForegroundReceipt).where(AppForegroundReceipt.installation_id == row.id)
        .order_by(AppForegroundReceipt.created_at.desc()).limit(100)).all()
    for entry in foreground:
        if entry.status == "processing" and entry.created_at < datetime.utcnow()-timedelta(seconds=90):
            entry.status, entry.failure, entry.finished_at = "uncertain", "Delivery outcome is unknown; inspect the recipient before another action", datetime.utcnow()
            session.add(entry)
    session.commit()
    return {"jobs": sorted([service.job_data(entry) for entry in rows] + [receipt_data(entry) for entry in foreground],
        key=lambda entry: entry["created_at"], reverse=True)[:100]}


class Retry(service.Strict):
    confirm_receiver_has_not_processed: bool


@router.post("/workspaces/{workspace_id}/apps/{installation_id}/jobs/{job_id}/retry")
def retry(workspace_id: str, installation_id: str, job_id: str, data: Retry,
          user=Depends(get_current_user), session=Depends(get_session)):
    lock_actor(session, workspace_id, user.id)
    row = installation(session, workspace_id, installation_id)
    manage(session, row, user.id)
    job = session.get(AppJob, job_id, populate_existing=True)
    if not job or job.installation_id != row.id:
        raise HTTPException(404, "App job not found")
    if not data.confirm_receiver_has_not_processed or job.status not in {"failed", "uncertain"} or not job.encrypted_payload or row.generation != job.generation or not service.allowed_installation(session, row):
        raise HTTPException(409, "Inspect the receiver and confirm this unchanged request was not processed before retrying")
    job.status, job.failure, job.attempts, job.next_attempt_at = "pending", None, 0, datetime.utcnow()
    session.add(job)
    session.commit()
    return service.job_data(job)


class Callback(service.Strict):
    event_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,80}$")
    generation: int = Field(ge=1)
    channel_id: str = Field(min_length=1, max_length=128)
    card: service.Card


@router.post("/apps/{installation_id}/callback", status_code=201)
async def callback(installation_id: str, request: Request, session=Depends(get_session)):
    # Bound streamed input before decoding; no JWT or OAuth token is accepted.
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > 16384:
            raise HTTPException(413, "App callback exceeds 16 KB")
        chunks.append(chunk)
    body = b"".join(chunks)
    row = session.get(AppInstallation, installation_id, populate_existing=True)
    if not row or not row.enabled or row.revoked_at:
        raise HTTPException(401, "App signature unavailable")
    try:
        stamp = request.headers.get("X-FreeCollab-Timestamp", "")
        if not re.fullmatch(r"\d{10,11}", stamp) or abs(int(time.time())-int(stamp)) > 300:
            raise ValueError("Expired signature")
        secret = unseal_private(row.encrypted_secret, "app-signing", row.id)
        expected = "v1="+hmac.new(secret.encode(), stamp.encode()+b"."+body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(request.headers.get("X-FreeCollab-Signature", ""), expected):
            raise ValueError("Invalid signature")
        packet = json.loads(body, object_pairs_hook=service.copilot.transport._pairs, parse_constant=service.copilot.transport._constant)
        data = Callback.model_validate(packet)
    except Exception:
        raise HTTPException(401, "Invalid, expired or malformed app signature") from None
    lock_workspace(session, row.workspace_id)
    lock_channel_writes(session, data.channel_id)
    session.expire_all()
    row = session.get(AppInstallation, installation_id)
    # Recheck both the signing generation and installer's current grant after
    # acquiring the same lock used by revocation and delivery.
    current_secret = unseal_private(row.encrypted_secret, "app-signing", row.id) if row.encrypted_secret else ""
    if row.generation != data.generation or not hmac.compare_digest(secret, current_secret) or not service.allowed_installation(session, row):
        raise HTTPException(403, "App grant or signing generation changed")
    if data.channel_id not in service.channels(row) or "cards:write" not in service.manifest_for(row).scopes:
        raise HTTPException(403, "App cannot publish a card to this channel")
    digest = hashlib.sha256(body).hexdigest()
    previous = session.get(AppCallback, (row.id, data.event_id))
    if previous:
        if previous.body_digest != digest:
            raise HTTPException(409, "Callback identifier already belongs to different content")
        return {"card_id": previous.card_id, "duplicate": True}
    count = session.exec(select(func.count()).select_from(AppCallback).where(AppCallback.installation_id == row.id)).one()
    if count >= 10000:
        raise HTTPException(429, "App callback history reached its safety bound; rotate this installation")
    card = service.add_card(session, row.workspace_id, data.channel_id, row.bot_id, service.manifest_for(row).name, data.card, row.granted_by, row.id)
    session.flush()
    session.add(AppCallback(installation_id=row.id, event_id=data.event_id, body_digest=digest, card_id=card.id))
    session.commit()
    return {"card_id": card.id, "duplicate": False}


@router.get("/workspaces/{workspace_id}/channels/{channel_id}/apps/cards")
def cards(workspace_id: str, channel_id: str, before: datetime | None = None,
          limit: int = Query(default=30, ge=1, le=100), user=Depends(get_current_user), session=Depends(get_session)):
    service.native_channel(session, workspace_id, channel_id, user.id)
    query = select(AppCard).where(AppCard.workspace_id == workspace_id, AppCard.channel_id == channel_id)
    if before:
        if before.tzinfo:
            from datetime import timezone
            before = before.astimezone(timezone.utc).replace(tzinfo=None)
        query = query.where(AppCard.created_at < before)
    rows = session.exec(query.order_by(AppCard.created_at.desc(), AppCard.id).limit(limit)).all()
    return {"cards": [service.card_data(session, row, user.id) for row in rows]}


@router.post("/workspaces/{workspace_id}/channels/{channel_id}/apps/ai/drafts", status_code=201)
def create_draft(workspace_id: str, channel_id: str, data: service.AIInput,
                 user=Depends(get_current_user), session=Depends(get_session)):
    account_limit(session, "app-ai", user.id, 30, 3600)
    row = service.generate_draft(session, workspace_id, channel_id, user.id, data)
    session.commit()
    return service.draft_result(session, row, user.id)


@router.get("/workspaces/{workspace_id}/channels/{channel_id}/apps/ai/drafts")
def drafts(workspace_id: str, channel_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    service.native_channel(session, workspace_id, channel_id, user.id)
    rows = session.exec(select(AppAIDraft).where(AppAIDraft.workspace_id == workspace_id, AppAIDraft.channel_id == channel_id,
        AppAIDraft.owner_id == user.id).order_by(AppAIDraft.created_at.desc()).limit(30)).all()
    result = []
    for row in rows:
        try:
            result.append(service.draft_result(session, row, user.id))
        except HTTPException:
            result.append({"id": row.id, "kind": row.kind, "status": "source_or_consent_changed", "created_at": row.created_at})
    return {"drafts": result}


class ApproveDraft(service.Strict):
    confirm: bool
    password: str | None = Field(default=None, max_length=256)
    code: str | None = Field(default=None, max_length=128)


@router.post("/workspaces/{workspace_id}/channels/{channel_id}/apps/ai/drafts/{draft_id}/approve")
def approve_draft(workspace_id: str, channel_id: str, draft_id: str, data: ApproveDraft,
                  user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    if not data.confirm:
        raise HTTPException(422, "Review the exact draft and explicitly approve it")
    lock_actor(session, workspace_id, user.id, channel_id)
    row = session.get(AppAIDraft, draft_id, populate_existing=True)
    if not row or row.workspace_id != workspace_id or row.channel_id != channel_id or row.owner_id != user.id:
        raise HTTPException(404, "Private AI draft not found")
    result = service.draft_result(session, row, user.id)
    if row.status == "approved":
        return {"status": "approved", "resource_id": row.resource_id}
    if row.kind == "summary":
        card = service.add_card(session, workspace_id, channel_id, "freecollab_copilot", "Workspace copilot",
                                service.Card(title="Reviewed channel recap", text=result["text"]), user.id, citations=result["citations"])
        session.flush()
        resource = card.id
    elif row.kind == "actions":
        resources = []
        # Validate every destination generation before adding any queued writes.
        for action in result["actions"]:
            installed = installation(session, workspace_id, action["installation_id"])
            if installed.generation != action["generation"]:
                raise HTTPException(409, "An app changed after this proposal; generate and review a new draft")
            job = service.queue_invocation(session, installed, user.id, service.Invocation(request_id=row.id+"_"+str(len(resources)),
                channel_id=channel_id, command=action["command"], arguments=action["arguments"], expected_generation=action["generation"], confirm=True, disclose_to_app=True))
            resources.append(job.id)
        resource = ",".join(resources)
    else:
        if not data.password:
            raise HTTPException(422, "Use your normal password/MFA confirmation before installing a generated workflow")
        from ..models.workflow import WorkflowDefinition, WorkflowRevision
        from ..services.account_security import ensure_reauthentication
        from ..services.crypto import encrypt_message
        fields = service.workflow.DefinitionFields.model_validate(result["fields"])
        actor = service.workflow.lock_actor(session, workspace_id, channel_id, user.id, credentials.credentials, admin=True)
        service.workflow.validate_fields(session, workspace_id, channel_id, fields)
        ensure_reauthentication(session, actor, data.password, data.code)
        count = session.exec(select(func.count()).select_from(WorkflowDefinition).where(WorkflowDefinition.channel_id == channel_id)).one()
        if count >= service.workflow.MAX_DEFINITIONS:
            raise HTTPException(409, "This channel has the maximum of twenty workflows")
        definition = WorkflowDefinition(workspace_id=workspace_id, channel_id=channel_id, created_by=actor.id,
            granted_by=actor.id, trigger=fields.trigger, enabled=True)
        session.add(definition)
        session.flush()
        payload = {**fields.model_dump(mode="json"), "workflow_id": definition.id, "revision": definition.current_revision}
        session.add(WorkflowRevision(workflow_id=definition.id, revision=definition.current_revision, created_by=actor.id,
            encrypted_definition=encrypt_message(service.dump(payload), "workflow:" + channel_id)))
        resource = definition.id
    row.status, row.resource_id = "approved", resource
    session.add(row)
    session.commit()
    return {"status": "approved", "resource_id": resource}


@router.delete("/workspaces/{workspace_id}/channels/{channel_id}/apps/ai/drafts/{draft_id}", status_code=204)
def delete_draft(workspace_id: str, channel_id: str, draft_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    # Private cleanup remains possible after source membership or AI consent was
    # removed; only the current owner may destroy their private draft.
    lock_actor(session, workspace_id, user.id, channel_id)
    require_workspace_member(session, workspace_id, user.id)
    row = session.get(AppAIDraft, draft_id, populate_existing=True)
    if not row or row.workspace_id != workspace_id or row.channel_id != channel_id or row.owner_id != user.id:
        raise HTTPException(404, "Private AI draft not found")
    from ..services.enterprise_governance import assert_deletion_allowed
    assert_deletion_allowed(session, channel_id, author_id=user.id)
    session.delete(row)
    session.commit()


class ScheduleInput(service.Strict):
    expected_config_revision: int = Field(ge=1, strict=True)
    interval_hours: int = Field(default=24, ge=1, le=168)
    enabled: bool = True
    disclose_future_native_messages: bool


@router.get("/workspaces/{workspace_id}/channels/{channel_id}/apps/ai/recaps")
def recaps(workspace_id: str, channel_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    service.native_channel(session, workspace_id, channel_id, user.id)
    rows = session.exec(select(ChannelRecapSchedule).where(ChannelRecapSchedule.workspace_id == workspace_id,
        ChannelRecapSchedule.channel_id == channel_id, ChannelRecapSchedule.owner_id == user.id)).all()
    return {"schedules": [row.model_dump() for row in rows]}


@router.post("/workspaces/{workspace_id}/channels/{channel_id}/apps/ai/recaps", status_code=201)
def schedule(workspace_id: str, channel_id: str, data: ScheduleInput, user=Depends(get_current_user), session=Depends(get_session)):
    lock_actor(session, workspace_id, user.id, channel_id)
    service.install_grants(session, workspace_id, user.id, [channel_id])
    config = service.ai_context(session, workspace_id, channel_id, user.id)
    if data.expected_config_revision != config.revision:
        raise HTTPException(409, "Provider configuration changed; review the current destination before scheduling")
    if data.enabled and not data.disclose_future_native_messages:
        raise HTTPException(422, "Explicitly consent to sharing up to eight new native messages per recap with this provider")
    row = session.exec(select(ChannelRecapSchedule).where(ChannelRecapSchedule.workspace_id == workspace_id,
        ChannelRecapSchedule.channel_id == channel_id, ChannelRecapSchedule.owner_id == user.id)).first()
    if not row:
        row = ChannelRecapSchedule(workspace_id=workspace_id, channel_id=channel_id, owner_id=user.id,
            provider_revision=config.revision, interval_hours=data.interval_hours)
    else:
        row.generation += 1
    row.interval_hours, row.enabled, row.provider_revision = data.interval_hours, data.enabled, config.revision
    # This grant is for future messages only, including when re-enabling a
    # paused schedule or changing its provider. Never backfill unseen history.
    from datetime import timedelta
    consented_at = datetime.utcnow()
    if data.enabled:
        row.last_run_at = consented_at
        row.last_status = None
    row.next_run_at = consented_at + timedelta(hours=data.interval_hours)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row.model_dump()


@router.delete("/workspaces/{workspace_id}/channels/{channel_id}/apps/ai/recaps/{schedule_id}")
def stop_schedule(workspace_id: str, channel_id: str, schedule_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    lock_actor(session, workspace_id, user.id)
    row = session.get(ChannelRecapSchedule, schedule_id, populate_existing=True)
    if not row or row.workspace_id != workspace_id or row.channel_id != channel_id or row.owner_id != user.id:
        raise HTTPException(404, "Recap schedule not found")
    row.enabled, row.generation = False, row.generation+1
    session.add(row)
    session.commit()
    return {"enabled": False}
