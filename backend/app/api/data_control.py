"""Owner-controlled private exports, dry runs and explicitly confirmed erasure."""
from datetime import datetime, timedelta
import hashlib
import json
import secrets
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select as sqlselect, update
from sqlmodel import select

from ..database import get_session
from ..models.account_security import AccountSecurity
from ..models.channel import Channel, ChannelMember
from ..models.data_control import AccountIdentityErasure, DataControlJob, DataControlPreview, WorkspaceRetentionPolicy
from ..models.session import ActiveSession
from ..models.user import User
from ..models.workspace import PersonalWorkspace, Workspace, WorkspaceChannel, WorkspaceMember
from ..services import data_control as service
from ..services.access import lock_channel_writes
from ..services.account_security import account_limit, ensure_reauthentication, revoke_all_sessions, seal_private, state_for, unseal_private
from ..services.auth import active_user_for_token, get_current_user, hash_password, security, token_digest
from ..services.request_guard import recheck_actor
from ..services.sso_scope import require_unrestricted
from ..services.workforce_access import require_personal_account
from ..services.workspaces import lock_workspace


def private(response: Response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"


router = APIRouter(prefix="/workspaces/{workspace_id}/data-controls", tags=["data controls"], dependencies=[Depends(private)])
account_router = APIRouter(prefix="/account/data-closure", tags=["account data controls"], dependencies=[Depends(private)])


class EphemeralExportResponse(FileResponse):
    """Remove decrypted download material even on a disconnected client."""
    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            from pathlib import Path
            Path(self.path).unlink(missing_ok=True)


class Reauthenticate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=72)
    code: str | None = Field(default=None, max_length=64)


class PolicyInput(Reauthenticate):
    enabled: bool
    hold: bool
    retention_days: int = Field(ge=7, le=3650)
    expected_revision: int = Field(ge=1)


class Selection(Reauthenticate):
    channel_ids: list[str] = Field(min_length=1, max_length=20)


class ExportInput(Selection):
    acknowledge_plaintext_export: Literal[True]


class PurgeInput(Reauthenticate):
    confirmation: str = Field(max_length=200)
    acknowledge_backups_and_provider_copies: Literal[True]


class ClosureInput(Reauthenticate):
    confirmation: Literal["ERASE MY ACCOUNT"]
    preview_fingerprint: str = Field(min_length=64, max_length=64)
    acknowledge_retained_shared_authorship_and_backups: Literal[True]


def authorize(session, workspace_id, user, credentials, data):
    service.require_owner(session, workspace_id, user.id)
    account_limit(session, "data-controls", user.id, 30, 3600)
    state_for(session, user.id)
    session.execute(update(User).where(User.id == user.id).values(name=User.name))
    session.execute(update(AccountSecurity).where(AccountSecurity.user_id == user.id).values(auth_version=AccountSecurity.auth_version))
    lock_workspace(session, workspace_id)
    session.expire_all()
    actor = active_user_for_token(credentials.credentials, session)
    if actor is None: raise HTTPException(401, "Account access changed. Please sign in again.")
    service.require_owner(session, workspace_id, actor.id)
    state = ensure_reauthentication(session, actor, data.password, data.code)
    recheck_actor(session, actor.id)
    return actor, state


def policy_payload(row):
    return {"enabled": row.enabled, "hold": row.hold, "retention_days": row.retention_days,
            "revision": row.revision, "automatic_purge": False}


def job_payload(row):
    return {"id": row.id, "kind": row.kind, "status": row.status, "counts": json.loads(row.counts_json),
            "created_at": row.created_at, "finished_at": row.finished_at, "expires_at": row.expires_at,
            "artifact_size": row.artifact_size, "sha256": row.artifact_sha256,
            "failure": row.failure, "download_available": row.kind == "export" and row.status == "ready" and row.expires_at > datetime.utcnow()}


@router.get("")
def status(workspace_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    service.require_owner(session, workspace_id, user.id)
    pairs = session.exec(select(Channel, ChannelMember).join(ChannelMember, ChannelMember.channel_id == Channel.id)
        .join(WorkspaceChannel, WorkspaceChannel.channel_id == Channel.id).where(
            WorkspaceChannel.workspace_id == workspace_id, ChannelMember.user_id == user.id)).all()
    from ..models.secure_channel import SecureChannel
    channels = [{"id": channel.id, "name": None if session.get(SecureChannel, channel.id) else channel.name,
                 "secure": bool(session.get(SecureChannel, channel.id)), "is_admin": member.role == "admin"} for channel, member in pairs]
    jobs = session.exec(select(DataControlJob).where(DataControlJob.workspace_id == workspace_id,
            DataControlJob.requested_by == user.id).order_by(DataControlJob.created_at.desc()).limit(50)).all()
    return {"policy": policy_payload(service.policy_for(session, workspace_id)), "channels": channels,
            "jobs": [job_payload(row) for row in jobs], "limits": {"channels": 20, "records": service.MAX_ROWS, "archive_bytes": service.MAX_BYTES}}


@router.put("/policy")
def save_policy(workspace_id: str, data: PolicyInput, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    actor, _ = authorize(session, workspace_id, user, credentials, data)
    row = service.policy_for(session, workspace_id)
    if row.revision != data.expected_revision: raise HTTPException(409, "Retention policy changed; reload it")
    row.enabled, row.hold, row.retention_days = data.enabled, data.hold, data.retention_days
    row.revision += 1; row.updated_at = datetime.utcnow(); row.updated_by = actor.id
    session.add(row); session.commit()
    return policy_payload(row)


@router.post("/previews")
def preview(workspace_id: str, data: Selection, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    actor, state = authorize(session, workspace_id, user, credentials, data)
    service.selected_channels(session, workspace_id, actor.id, data.channel_ids, purge=True)
    for identifier in sorted(data.channel_ids): lock_channel_writes(session, identifier)
    recheck_actor(session, actor.id)
    policy = service.policy_for(session, workspace_id)
    cutoff = datetime.utcnow() - timedelta(days=policy.retention_days)
    plan = service.build_plan(session, workspace_id, actor.id, data.channel_ids, cutoff)
    identifier = secrets.token_hex(16)
    request = {"channel_ids": sorted(data.channel_ids), "cutoff": cutoff.isoformat(), "counts": service.plan_counts(plan)}
    channel_counts = [{"channel_id": identifier, "counts": {name: len([item for item in rows if item.get("channel_id") == identifier])
        for name, rows in plan.items() if any(item.get("channel_id") == identifier for item in rows)}} for identifier in sorted(data.channel_ids)]
    row = DataControlPreview(id=identifier, workspace_id=workspace_id, requested_by=actor.id,
        token_hash=token_digest(credentials.credentials), auth_version=state.auth_version, policy_revision=policy.revision,
        encrypted_plan=seal_private(service._json(request), "data-preview", identifier), fingerprint=service.plan_digest(plan),
        expires_at=datetime.utcnow() + timedelta(minutes=10))
    session.add(row); session.commit()
    return {"id": row.id, "counts": request["counts"], "channels": channel_counts, "cutoff": cutoff, "expires_at": row.expires_at,
            "policy": policy_payload(policy), "confirmation": "PURGE " + workspace_id,
            "scope": "Selected native-channel content only; external providers, Matrix history, device caches and backups are separate."}


def queue_job(session, actor, state, credentials, workspace_id, kind, request):
    pending = session.exec(select(func.count()).select_from(DataControlJob).where(DataControlJob.requested_by == actor.id,
        DataControlJob.status.in_(["queued", "processing", "erasing_files"]))).one()
    if pending >= 2: raise HTTPException(429, "Wait for an existing data-control job to complete")
    identifier = secrets.token_hex(16)
    row = DataControlJob(id=identifier, workspace_id=workspace_id, requested_by=actor.id,
        token_hash=token_digest(credentials.credentials), auth_version=state.auth_version, kind=kind,
        encrypted_request=seal_private(service._json(request), "data-job", identifier), expires_at=datetime.utcnow() + timedelta(hours=1))
    recheck_actor(session, actor.id)
    session.add(row); session.commit(); return job_payload(row)


@router.post("/exports", status_code=202)
def export(workspace_id: str, data: ExportInput, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    actor, state = authorize(session, workspace_id, user, credentials, data)
    service.selected_channels(session, workspace_id, actor.id, data.channel_ids)
    return queue_job(session, actor, state, credentials, workspace_id, "export", {"channel_ids": sorted(data.channel_ids)})


@router.post("/purges/{preview_id}", status_code=202)
def purge(workspace_id: str, preview_id: str, data: PurgeInput, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    actor, state = authorize(session, workspace_id, user, credentials, data)
    row = session.get(DataControlPreview, preview_id)
    policy = service.policy_for(session, workspace_id)
    if (not row or row.workspace_id != workspace_id or row.requested_by != actor.id or row.used_at
            or row.expires_at <= datetime.utcnow() or row.token_hash != token_digest(credentials.credentials)
            or row.auth_version != state.auth_version):
        raise HTTPException(409, "Preview expired or changed; create a fresh preview")
    if not policy.enabled or policy.hold or policy.revision != row.policy_revision:
        raise HTTPException(409, "Retention is disabled, held or changed; save the intended policy and create another preview")
    if data.confirmation != "PURGE " + workspace_id: raise HTTPException(400, "Type the exact preview confirmation")
    request = json.loads(unseal_private(row.encrypted_plan, "data-preview", row.id))
    if not request["counts"]: raise HTTPException(409, "No eligible content in this preview")
    request.update(fingerprint=row.fingerprint, policy_revision=row.policy_revision)
    row.used_at = datetime.utcnow(); session.add(row)
    return queue_job(session, actor, state, credentials, workspace_id, "purge", request)


def owned_job(session, workspace_id, user, identifier):
    service.require_owner(session, workspace_id, user.id)
    row = session.get(DataControlJob, identifier)
    if not row or row.workspace_id != workspace_id or row.requested_by != user.id:
        raise HTTPException(404, "Data-control job not found")
    return row


@router.post("/jobs/{job_id}/run")
def run_job(workspace_id: str, job_id: str, data: Reauthenticate, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    actor, _ = authorize(session, workspace_id, user, credentials, data)
    row = owned_job(session, workspace_id, actor, job_id)
    if row.status not in {"queued", "processing", "erasing_files"}: return job_payload(row)
    session.commit()
    service.run_pending(session, limit=1, job_id=job_id)
    return job_payload(session.get(DataControlJob, job_id, populate_existing=True))


@router.post("/jobs/{job_id}/retry")
def retry_job(workspace_id: str, job_id: str, data: Reauthenticate, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    actor, state = authorize(session, workspace_id, user, credentials, data)
    row = owned_job(session, workspace_id, actor, job_id)
    if row.status != "failed": raise HTTPException(409, "Only failed jobs can be retried")
    request = json.loads(unseal_private(row.encrypted_request, "data-job", row.id))
    service.selected_channels(session, workspace_id, actor.id, request["channel_ids"], purge=row.kind == "purge")
    from ..models.data_control import DataFileErasure
    pending_files = session.exec(select(DataFileErasure).where(DataFileErasure.job_id == row.id, DataFileErasure.erased_at.is_(None))).first()
    if row.kind == "purge" and not pending_files:
        raise HTTPException(409, "Create a new preview before retrying a metadata purge")
    row.status = "erasing_files" if pending_files else "queued"
    row.lease_token = None; row.lease_expires_at = None; row.attempts = 0; row.failure = None
    row.expires_at = datetime.utcnow() + timedelta(hours=1)
    row.token_hash = token_digest(credentials.credentials); row.auth_version = state.auth_version
    recheck_actor(session, actor.id)
    session.add(row); session.commit()
    return job_payload(row)


@router.post("/exports/{job_id}/download")
def download(workspace_id: str, job_id: str, data: Reauthenticate, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    actor, _ = authorize(session, workspace_id, user, credentials, data)
    row = owned_job(session, workspace_id, actor, job_id)
    if row.kind != "export" or row.status != "ready" or row.expires_at <= datetime.utcnow():
        raise HTTPException(410, "This private export is unavailable or expired")
    request = json.loads(unseal_private(row.encrypted_request, "data-job", row.id))
    if row.token_hash != token_digest(credentials.credentials): raise HTTPException(403, "Use the session that requested this export")
    service._job_actor(session, row, request["channel_ids"])
    path = service.open_archive(row.id)
    try:
        recheck_actor(session, actor.id)
        service._job_actor(session, row, request["channel_ids"])
    except Exception:
        path.unlink(missing_ok=True); raise
    session.commit()
    return EphemeralExportResponse(path, media_type="application/zip", filename="freecollab-export-" + row.id + ".zip",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"})


def closure_state(session, user):
    require_unrestricted(session)
    require_personal_account(session, user.id)
    memberships = session.exec(select(WorkspaceMember).where(WorkspaceMember.user_id == user.id)).all()
    channels = session.exec(select(ChannelMember).where(ChannelMember.user_id == user.id)).all()
    home = session.get(PersonalWorkspace, user.id)
    empty_homes, blockers = [], []
    for member in memberships:
        others = session.exec(select(WorkspaceMember, User).join(User, User.id == WorkspaceMember.user_id).where(
            WorkspaceMember.workspace_id == member.workspace_id, WorkspaceMember.user_id != user.id)).all()
        if member.role != "owner" or any(row.role == "owner" and not actor.is_blocked for row, actor in others): continue
        empty = bool(home and home.workspace_id == member.workspace_id and not others and not session.exec(
            select(WorkspaceChannel).where(WorkspaceChannel.workspace_id == member.workspace_id)).first())
        if empty:
            permitted = {"workspacemember", "personalworkspace", "workspaceretentionpolicy", "workspaceidentitypolicy"}
            for table in service.SQLModel.metadata.sorted_tables:
                for fk in table.foreign_keys:
                    if fk.column.table.name == "workspace" and table.name not in permitted:
                        if table.name == "pilotfeedback":
                            # Own optional feedback is erased in this closure;
                            # another participant's private notes still protect
                            # the workspace from implicit account-only deletion.
                            if service._records(session, table.name, (fk.parent == member.workspace_id) & (table.c.user_id != user.id)): empty = False
                        elif service._records(session, table.name, fk.parent == member.workspace_id): empty = False
        if empty: empty_homes.append(member.workspace_id)
        else: blockers.append(member.workspace_id)
    channel_blockers = []
    for member in channels:
        if member.role != "admin": continue
        other_admins = session.exec(select(ChannelMember, User).join(User, User.id == ChannelMember.user_id).where(
            ChannelMember.channel_id == member.channel_id, ChannelMember.user_id != user.id, ChannelMember.role == "admin")).all()
        if not any(not actor.is_blocked for _, actor in other_admins): channel_blockers.append(member.channel_id)
    installation_blocked = user.role == "admin" and not session.exec(select(User).where(
        User.id != user.id, User.role == "admin", User.is_blocked == False)).first()
    active_jobs = session.exec(select(DataControlJob.id).where(DataControlJob.requested_by == user.id,
        DataControlJob.status.in_(["queued", "processing", "erasing_files"]))).all()
    from ..models.data_control import DataFileErasure
    pending_erasure = session.exec(select(DataFileErasure.job_id).join(DataControlJob, DataControlJob.id == DataFileErasure.job_id).where(
        DataControlJob.requested_by == user.id, DataFileErasure.erased_at.is_(None))).all()
    active_jobs = sorted(set(active_jobs + pending_erasure))
    result = {"user_id": user.id, "memberships": sorted([[row.workspace_id, row.role] for row in memberships]),
              "channels": sorted([[row.channel_id, row.role] for row in channels]), "empty_personal_workspaces": sorted(empty_homes),
              "blocking_workspaces": sorted(blockers), "blocking_channels": sorted(channel_blockers),
              "blocking_installation_admin": bool(installation_blocked), "active_data_jobs": sorted(active_jobs)}
    result["fingerprint"] = hashlib.sha256(service._json(result).encode()).hexdigest()
    result["can_erase_identity"] = not (blockers or channel_blockers or installation_blocked or active_jobs)
    return result


@account_router.get("")
def closure_preview(user=Depends(get_current_user), session=Depends(get_session)):
    result = closure_state(session, user)
    return {**result, "confirmation": "ERASE MY ACCOUNT", "effect":
        "Permanently erase your login identity, profile, private preferences and stored account credentials. Shared authored content keeps an opaque disabled author stub. Matrix history, other devices and backups are separate."}


def _delete_rows(session, name, column, values):
    table = service._table(name)
    if table is not None and values:
        session.execute(delete(table).where(table.c[column].in_(values)))


@account_router.post("")
def close_account(data: ClosureInput, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    require_unrestricted(session); require_personal_account(session, user.id)
    account_limit(session, "account-identity-erasure", user.id, 5, 3600)
    state_for(session, user.id)
    # Two administrators may have disjoint workspaces. Serialize their
    # closures on the same deterministic administrator row set so neither
    # can rely on a successor who is simultaneously erasing their identity.
    current_role = session.exec(select(User.role).where(User.id == user.id)).first()
    lock_ids = session.exec(select(User.id).where(User.role == "admin")).all() if current_role == "admin" else []
    for identifier in sorted(set(lock_ids + [user.id])):
        session.execute(update(User).where(User.id == identifier).values(name=User.name))
    session.execute(update(AccountSecurity).where(AccountSecurity.user_id == user.id).values(auth_version=AccountSecurity.auth_version))
    session.expire_all()
    actor = active_user_for_token(credentials.credentials, session)
    if actor is None: raise HTTPException(401, "Account access changed. Please sign in again.")
    state = ensure_reauthentication(session, actor, data.password, data.code)
    # Lock the resources whose last-owner/admin protection is being assessed.
    workspaces = session.exec(select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == actor.id)).all()
    for identifier in sorted(workspaces): lock_workspace(session, identifier)
    channel_ids = session.exec(select(ChannelMember.channel_id).where(ChannelMember.user_id == actor.id)).all()
    for identifier in sorted(channel_ids): lock_channel_writes(session, identifier)
    recheck_actor(session, actor.id)
    snapshot = closure_state(session, actor)
    if snapshot["fingerprint"] != data.preview_fingerprint: raise HTTPException(409, "Account dependencies changed; refresh the erasure preview")
    if not snapshot["can_erase_identity"]:
        raise HTTPException(409, "Transfer workspace/channel ownership and finish data jobs before erasing this identity")
    from ..services.matrix_bridge import remove_secure_member
    for identifier in sorted(channel_ids): remove_secure_member(session, identifier, actor.id)
    recheck_actor(session, actor.id)
    if closure_state(session, actor)["fingerprint"] != snapshot["fingerprint"]:
        raise HTTPException(409, "Account dependencies changed during remote access revocation")
    original_email = actor.email
    # Delete the user's private archive copies and preview/request metadata;
    # shared workspace content is intentionally not swept by identity closure.
    data_jobs = session.exec(select(DataControlJob).where(DataControlJob.requested_by == actor.id)).all()
    for job in data_jobs: service._artifact_path(job.id).unlink(missing_ok=True)
    _delete_rows(session, "datafileerasure", "job_id", [job.id for job in data_jobs])
    _delete_rows(session, "datacontroljob", "requested_by", [actor.id])
    _delete_rows(session, "datacontrolpreview", "requested_by", [actor.id])
    service.erase_pilot_feedback_for_account(session, actor.id)
    # Remove account/device credentials and private profile data explicitly.
    subscriptions = service._table("pushsubscription")
    subscription_ids = service._records(session, subscriptions.name, subscriptions.c.user_id == actor.id) if subscriptions is not None else []
    _delete_rows(session, "pushdelivery", "subscription_id", service._ids(subscription_ids))
    _delete_rows(session, "pushsubscription", "user_id", [actor.id])
    notifications = service._table("notification")
    notification_rows = service._records(session, notifications.name, notifications.c.user_id == actor.id) if notifications is not None else []
    _delete_rows(session, "pushdelivery", "notification_id", service._ids(notification_rows))
    _delete_rows(session, "notificationdelivery", "notification_id", service._ids(notification_rows))
    _delete_rows(session, "notification", "user_id", [actor.id])
    for name in ("userappearance", "userlanguage", "notificationpreference", "bookmark", "threadsubscription", "encryptedthreadsubscription",
                 "channelnotificationsetting", "notificationdelivery", "directmessagestate", "offlinemessagereceipt"):
        _delete_rows(session, name, "user_id", [actor.id])
    conversations = service._table("copilotconversation")
    own_conversations = service._records(session, conversations.name, conversations.c.user_id == actor.id) if conversations is not None else []
    turns = service._children(session, "copilotturn", "conversation_id", service._ids(own_conversations))
    _delete_rows(session, "copilottoolapproval", "turn_id", service._ids(turns))
    _delete_rows(session, "copilotturn", "conversation_id", service._ids(own_conversations))
    _delete_rows(session, "copilotconversation", "user_id", [actor.id])
    for name in ("copilotconsent", "copilotactivity", "cloudfileoauthattempt", "externalmeetingoauthattempt", "cloudfileimport", "activityfeedfollow", "activityfeedread"):
        _delete_rows(session, name, "user_id", [actor.id])
    _delete_rows(session, "activityfeedfollow", "target_id", [actor.id])
    office = service._table("officeeditsession")
    sessions = service._records(session, office.name, office.c.user_id == actor.id) if office is not None else []
    _delete_rows(session, "officeeditlock", "session_id", service._ids(sessions))
    _delete_rows(session, "officeeditsession", "user_id", [actor.id])
    table = service._table("userblock")
    if table is not None: session.execute(delete(table).where((table.c.blocker_id == actor.id) | (table.c.blocked_id == actor.id)))
    attempts_table = service._table("oidcloginattempt")
    attempts = service._records(session, "oidcloginattempt", attempts_table.c.user_id == actor.id) if attempts_table is not None else []
    _delete_rows(session, "workspaceoidcattempt", "state_hash", [row["state_hash"] for row in attempts])
    _delete_rows(session, "oidcloginattempt", "user_id", [actor.id])
    identities_table = service._table("oidcidentity")
    identities = service._records(session, "oidcidentity", identities_table.c.user_id == actor.id) if identities_table is not None else []
    _delete_rows(session, "workspaceoidcidentity", "identity_id", service._ids(identities))
    _delete_rows(session, "oidcidentity", "user_id", [actor.id])
    challenges_table = service._table("mfachallenge")
    challenges = service._records(session, "mfachallenge", challenges_table.c.user_id == actor.id) if challenges_table is not None else []
    for name in ("workspaceoidcmfachallenge", "oidcmfachallenge"):
        _delete_rows(session, name, "token_hash", [row["token_hash"] for row in challenges])
    _delete_rows(session, "mfachallenge", "user_id", [actor.id])
    for name in ("accounttoken", "accountrecoverycode", "matrixsession", "matrixaccount"):
        _delete_rows(session, name, "user_id", [actor.id])
    tracked = session.exec(select(ActiveSession).where(ActiveSession.user_id == actor.id)).all()
    for name in ("workspaceencryptionunlock", "workspaceoidcsession"):
        _delete_rows(session, name, "token_hash", [row.token_hash for row in tracked])
    _delete_rows(session, "activesession", "user_id", [actor.id])
    mail_table = service._table("mailoutbox")
    mail_rows = service._records(session, "mailoutbox", mail_table.c.recipient == original_email) if mail_table is not None else []
    _delete_rows(session, "mailoperation", "outbox_id", service._ids(mail_rows))
    _delete_rows(session, "mailoutbox", "recipient", [original_email])
    # A former user's provider authority must not continue in background jobs.
    table = service._table("remotesyncconnection")
    if table is not None: session.execute(update(table).where(table.c.grant_user_id == actor.id).values(enabled=False, encrypted_credentials=None,
        encrypted_cursor=None, status="disconnected", revision=table.c.revision + 1))
    table = service._table("webhookendpoint")
    if table is not None: session.execute(update(table).where(table.c.granted_by == actor.id).values(enabled=False, generation=table.c.generation + 1))
    table = service._table("cloudfileconnection")
    if table is not None: session.execute(update(table).where(table.c.grant_user_id == actor.id).values(encrypted_credentials=None,
        status="disconnected", revision=table.c.revision + 1))
    table = service._table("externalmeetingconnection")
    if table is not None: session.execute(update(table).where(table.c.grant_user_id == actor.id).values(enabled=False, encrypted_credentials=None,
        status="disconnected", revision=table.c.revision + 1))
    table = service._table("workflowdefinition")
    if table is not None: session.execute(update(table).where(table.c.granted_by == actor.id).values(enabled=False))
    table = service._table("meetingparticipant")
    if table is not None:
        participants = service._records(session, table.name, table.c.user_id == actor.id)
        signals = service._table("meetingsignal")
        if signals is not None and participants:
            ids = service._ids(participants)
            session.execute(delete(signals).where((signals.c.sender_id.in_(ids)) | (signals.c.recipient_id.in_(ids))))
        session.execute(update(table).where(table.c.user_id == actor.id).values(name="Deleted account", state="removed", expires_at=datetime.utcnow()))
    table = service._table("meeting")
    if table is not None: session.execute(update(table).where(table.c.host_user_id == actor.id).values(ended_at=datetime.utcnow()))
    # Invalidate every prior JWT through the retained monotonically increasing
    # security version, while clearing all factor/recovery/profile values.
    session.execute(update(AccountSecurity).where(AccountSecurity.user_id == actor.id).values(auth_version=AccountSecurity.auth_version + 1,
        email_verified_at=None, totp_secret=None, totp_pending_secret=None, totp_pending_expires_at=None, totp_last_counter=-1))
    _delete_rows(session, "channelmember", "user_id", [actor.id])
    _delete_rows(session, "workspacemember", "user_id", [actor.id])
    _delete_rows(session, "personalworkspace", "user_id", [actor.id])
    for workspace_id in snapshot["empty_personal_workspaces"]:
        for name in ("workspaceretentionpolicy", "workspaceidentitypolicy"):
            _delete_rows(session, name, "workspace_id", [workspace_id])
        _delete_rows(session, "workspace", "id", [workspace_id])
    actor.name = "Deleted account"; actor.email = "erased-" + secrets.token_hex(16) + "@identity.invalid"
    actor.password_hash = hash_password(secrets.token_hex(32)); actor.public_key = None; actor.encrypted_private_key = None
    actor.role = "user"; actor.is_blocked = True
    session.add(actor); session.add(AccountIdentityErasure(user_id=actor.id)); session.commit()
    return {"identity_erased": True, "signed_out": True, "retained_authorship_stub": True,
            "shared_content_and_backups_erased": False}
