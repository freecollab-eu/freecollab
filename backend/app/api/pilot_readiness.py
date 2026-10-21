"""Private operator-only pilot configuration summary; no live external probes."""
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from ..database import get_session
from ..services.auth import active_user_for_token, decode_token_claims, security, token_digest
from ..services.pilot_readiness import authorize_operator, snapshot
from ..services.request_guard import clear_request, remember_request
from ..services.security_audit import authenticated_actor

Status = Literal["ready", "attention", "unconfigured", "unknown"]
CheckId = Literal["production_environment", "public_https_origin", "cors_https_origins", "proxy_boundary", "host_boundary", "database",
                  "mail", "mail_worker", "integration_worker", "server_keys", "operator_mfa", "storage", "metrics", "backup_evidence"]


class StrictResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PilotCheck(StrictResponse):
    id: CheckId
    status: Status
    label: str
    detail: str
    action: str


class PilotWorker(StrictResponse):
    enabled: bool
    status: Status
    last_seen_at: datetime | None
    age_seconds: int | None = Field(ge=0)


class Workers(StrictResponse):
    mail: PilotWorker
    integrations: PilotWorker


class PilotFeature(StrictResponse):
    id: str
    enabled: bool
    worker_required: bool


class Database(StrictResponse):
    engine: Literal["postgresql", "sqlite", "other"]
    reachable: bool


class Mail(StrictResponse):
    mode: Literal["disabled", "file", "smtp"]
    configured: bool
    tls: Literal["implicit", "starttls", "none", "not_applicable"]
    issues: list[str]


class Metrics(StrictResponse):
    configured: bool


class Backup(StrictResponse):
    status: Literal["unknown"]
    evidence_source: Literal["not_recorded"]
    last_backup_at: None
    last_offsite_verified_at: None
    last_restore_verified_at: None
    freshness: Literal["unknown"]


class PilotReadiness(StrictResponse):
    checked_at: datetime
    environment: Literal["development", "test", "production"]
    scope: Literal["local_configuration"]
    status: Status
    summary: str
    public_origin: str | None
    checks: list[PilotCheck] = Field(min_length=14, max_length=14)
    database: Database
    mail: Mail
    workers: Workers
    features: list[PilotFeature] = Field(max_length=20)
    metrics: Metrics
    backup: Backup


def private(response: Response):
    response.headers.update({"Cache-Control": "private, no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer"})


router = APIRouter(prefix="/operations", tags=["operations"], dependencies=[Depends(private)])


def readonly_operator(request: Request, credentials=Depends(security), session=Depends(get_session)):
    # General get_current_user bootstraps legacy rows. A diagnostics read must
    # require an already tracked current credential and remain SELECT-only.
    clear_request(session)
    claims = decode_token_claims(credentials.credentials)
    actor = active_user_for_token(credentials.credentials, session) if claims else None
    if actor is None:
        raise HTTPException(401, "Sign in with your current operator account to read deployment readiness")
    remember_request(session, token_digest(credentials.credentials), claims)
    authorize_operator(session, actor.id)
    authenticated_actor(request, actor.id)
    return actor.id


@router.get("/pilot-readiness", response_model=PilotReadiness)
def pilot_readiness(actor_id=Depends(readonly_operator), session=Depends(get_session)):
    return snapshot(session, actor_id)
