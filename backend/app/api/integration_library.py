from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import update
from sqlmodel import select
from ..database import get_session
from ..models.integration_library import IntegrationRelease
from ..services import integration_library as service
from ..services.account_security import account_limit
from ..services.auth import get_current_user
from ..services.workspaces import require_workspace_member

router = APIRouter(tags=["integration-library"])


@router.get("/apps/library")
def catalog(q: str = Query(default="", max_length=100), session=Depends(get_session)):
    rows = session.exec(select(IntegrationRelease).where(IntegrationRelease.status == "approved")
                        .order_by(IntegrationRelease.created_at.desc()).limit(200)).all()
    releases = service.builtin_data() + [service.release_data(row) for row in rows]
    term = q.casefold().strip()
    return {"releases": [row for row in releases if not term or term in (row["app_id"] + " " + row["package"]["manifest"]["name"] + " " + row["package"]["manifest"]["description"]).casefold()],
            "community_policy": "instance-admin-reviewed-declarative-packages", "incomplete": len(rows) == 200}


@router.post("/workspaces/{workspace_id}/apps/library/submissions", status_code=201)
def submit(workspace_id: str, data: service.Package, user=Depends(get_current_user), session=Depends(get_session)):
    account_limit(session, "integration-submit", user.id, 20, 3600)
    result = service.submit(session, workspace_id, user.id, data); session.commit(); return result


@router.get("/workspaces/{workspace_id}/apps/library/submissions")
def own_submissions(workspace_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    service.require_unrestricted(session)
    require_workspace_member(session, workspace_id, user.id)
    rows = session.exec(select(IntegrationRelease).where(IntegrationRelease.publisher_id == user.id)
                       .order_by(IntegrationRelease.created_at.desc()).limit(100)).all()
    return {"releases": [service.release_data(row) for row in rows]}


@router.get("/admin/integration-library")
def review_queue(user=Depends(get_current_user), session=Depends(get_session)):
    service.operator(session, user.id)
    rows = session.exec(select(IntegrationRelease).order_by(IntegrationRelease.created_at.desc()).limit(100)).all()
    return {"releases": [service.release_data(row, review=True) for row in rows]}


class Review(service.apps.Strict):
    package_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    approve: bool


@router.post("/admin/integration-library/{identifier}/review")
def review(identifier: str, data: Review, user=Depends(get_current_user), session=Depends(get_session)):
    # Serialize review/withdraw/install against this exact immutable SQL row.
    session.execute(update(IntegrationRelease).where(IntegrationRelease.id == identifier).values(status=IntegrationRelease.status))
    session.expire_all(); service.operator(session, user.id)
    row = session.get(IntegrationRelease, identifier)
    if not row: raise HTTPException(404, "Integration version not found")
    if row.package_digest != data.package_digest: raise HTTPException(409, "Integration digest changed")
    service.validate_package(service.Package.model_validate_json(row.package_json))
    row.status = "approved" if data.approve else "withdrawn"
    row.reviewer_id, row.reviewed_at = user.id, datetime.utcnow()
    session.add(row); session.commit(); return service.release_data(row)


@router.post("/workspaces/{workspace_id}/apps/library/{identifier}/install", status_code=201)
def install(workspace_id: str, identifier: str, data: service.Install, user=Depends(get_current_user), session=Depends(get_session)):
    return service.install_release(session, workspace_id, user.id, identifier, data)
