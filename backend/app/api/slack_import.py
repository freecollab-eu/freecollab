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
    row.encrypted_archive, row.status = "", "discarded"
    session.add(row); session.commit()
    return {"discarded": True}
