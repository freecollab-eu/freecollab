"""No observed progress: participants deliberately create their own self-reports."""
from fastapi import APIRouter, Depends, Query, Response
from ..database import get_session
from ..services.account_security import account_limit
from ..services import pilot_feedback as service
from .connection_setup import readonly_actor


def private(response: Response):
    response.headers.update({"Cache-Control": "private, no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer"})


router = APIRouter(prefix="/workspaces/{workspace_id}/pilot-feedback", tags=["workspace feedback"], dependencies=[Depends(private)])


@router.get("")
def own_feedback(workspace_id: str, user=Depends(readonly_actor), session=Depends(get_session)):
    return service.own_feedback(session, workspace_id, user.id)


@router.get("/summary")
def summary(workspace_id: str, user=Depends(readonly_actor), session=Depends(get_session)):
    return service.summary(session, workspace_id, user.id)


@router.put("/{task}")
def save_feedback(workspace_id: str, task: service.Task, data: service.FeedbackWrite, user=Depends(readonly_actor), session=Depends(get_session)):
    # account_limit may commit; it must run before all authority/write locks.
    account_limit(session, "pilot-feedback-write", user.id, 60, 300)
    return service.save_feedback(session, workspace_id, user.id, task, data)


@router.delete("/{task}")
def remove_feedback(workspace_id: str, task: service.Task, expected_revision: int = Query(ge=0, le=service.MAX_REVISION),
                    user=Depends(readonly_actor), session=Depends(get_session)):
    account_limit(session, "pilot-feedback-write", user.id, 60, 300)
    return service.delete_feedback(session, workspace_id, user.id, task, expected_revision)
