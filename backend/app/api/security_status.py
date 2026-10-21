"""Private installation security configuration and durable queue diagnostics."""
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field

from ..database import get_session
from ..services.operational_security import security_status
from .admin import require_admin

Status = Literal["pass", "attention", "disabled", "unknown"]
CheckId = Literal["public_https_origin", "cors_https_origins", "experimental_features", "demo_ai",
                  "independent_server_keys", "operator_mfa", "storage_capacity", "mail_leases",
                  "webhook_leases", "proof_leases", "sync_leases", "workflow_leases", "data_leases", "push_leases"]


class StrictResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SecurityCheck(StrictResponse):
    id: CheckId
    status: Status


class OperatorCoverage(StrictResponse):
    active: int = Field(ge=0)
    mfa_enabled: int = Field(ge=0)
    without_mfa: int = Field(ge=0)
    email_verified: int = Field(ge=0)


class StorageStatus(StrictResponse):
    status: Status
    free_bytes: int | None = Field(ge=0)
    total_bytes: int | None = Field(gt=0)
    free_percent: float | None = Field(ge=0, le=100)


class QueueStatus(StrictResponse):
    enabled: bool
    due: int = Field(ge=0)
    expired_leases: int = Field(ge=0)
    failed: int = Field(ge=0)


class QueueDiagnostics(StrictResponse):
    mail: QueueStatus
    webhooks: QueueStatus
    otrust: QueueStatus
    sync: QueueStatus
    workflows: QueueStatus
    data: QueueStatus
    push: QueueStatus


class SecurityStatus(StrictResponse):
    checked_at: datetime
    environment: Literal["development", "test", "production"]
    checks: list[SecurityCheck] = Field(max_length=14)
    operators: OperatorCoverage
    storage: StorageStatus
    queues: QueueDiagnostics


def private_cache(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/operations", tags=["operations"], dependencies=[Depends(private_cache)])


@router.get("/security", response_model=SecurityStatus)
def read_security_status(user=Depends(require_admin), session=Depends(get_session)):
    return security_status(session)
