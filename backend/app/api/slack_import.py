from datetime import datetime
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlmodel import select
from ..database import get_session
from ..models.slack_import import SlackImport
from ..services import slack_import as service
from ..services.account_security import account_limit
from ..services.auth import get_current_user

router = APIRouter(tags=["slack-import"])


@router.post("/workspaces/{workspace_id}/slack-imports/preview")
async def preview(workspace_id: str, file: UploadFile = File(...), user=Depends(get_current_user), session=Depends(get_session)):
    service.authorize(session, workspace_id, user.id)
    account_limit(session, "slack-import", user.id, 10, 3600)
    raw = await file.read(service.MAX_ARCHIVE + 1)
    result = service.create_preview(session, workspace_id, user.id, raw)
    session.commit()
    return result


@router.post("/workspaces/{workspace_id}/slack-imports/{identifier}/confirm")
def confirm(workspace_id: str, identifier: str, data: service.Confirm, user=Depends(get_current_user), session=Depends(get_session)):
    return service.import_archive(session, workspace_id, user.id, identifier, data)


@router.delete("/workspaces/{workspace_id}/slack-imports/{identifier}")
def discard(workspace_id: str, identifier: str, user=Depends(get_current_user), session=Depends(get_session)):
    service.authorize(session, workspace_id, user.id, locked=True)
    row = session.get(SlackImport, identifier)
    if not row or row.workspace_id != workspace_id or row.actor_id != user.id: raise HTTPException(404, "Slack import not found")
    if row.status == "completed": raise HTTPException(409, "Completed imports use normal channel retention controls")
    row.encrypted_archive, row.status, row.result_json = "", "discarded", None
    session.add(row); session.commit()
    return {"discarded": True}


@router.post("/workspaces/{workspace_id}/slack-imports/{identifier}/begin")
def begin(workspace_id: str, identifier: str, data: service.Confirm, user=Depends(get_current_user), session=Depends(get_session)):
    return service.begin_resume(session, workspace_id, user.id, identifier, data)


@router.get("/workspaces/{workspace_id}/slack-imports/{identifier}/progress")
def progress(workspace_id: str, identifier: str, user=Depends(get_current_user), session=Depends(get_session)):
    return service.resume_status(service._job(session, workspace_id, user.id, identifier))


@router.post("/workspaces/{workspace_id}/slack-imports/{identifier}/step")
def step(workspace_id: str, identifier: str, data: service.ResumeStep, user=Depends(get_current_user), session=Depends(get_session)):
    return service.resume_batch(session, workspace_id, user.id, identifier, data.expected_sequence)


@router.post("/workspaces/{workspace_id}/slack-imports/secure-reserve")
def secure_reserve(workspace_id: str, data: service.SecureReserve, user=Depends(get_current_user), session=Depends(get_session)):
    account_limit(session, "slack-secure-import", user.id, 200, 3600)
    return service.reserve_secure(session, workspace_id, user.id, data)


@router.get("/channels/{channel_id}/messages/{message_id}/slack-history")
def historical_message(channel_id: str, message_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    return service.historical_message(session, channel_id, message_id, user.id)


@router.post("/workspaces/{workspace_id}/slack-imports/{identifier}/complete-secure")
def complete_secure(workspace_id: str, identifier: str, data: service.SecureComplete, user=Depends(get_current_user), session=Depends(get_session)):
    return service.complete_secure(session, workspace_id, user.id, identifier, data)
