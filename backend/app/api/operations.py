"""Installation-operator mail controls and redacted monitoring endpoints."""
from datetime import datetime, timezone
import hmac

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.security import HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text, update
from sqlmodel import select

from ..database import get_session
from ..config import settings
from ..models.account_security import AccountSecurity, MailOutbox
from ..models.operations import MailOperation
from ..services.account_security import account_limit, ensure_reauthentication, state_for
from ..services.auth import active_user_for_token, security
from ..services.sso_scope import require_unrestricted
from ..services.mail import enqueue_mail
from ..services.maildiagnostics import integration_queue_status, integration_worker_required, mail_configuration, queue_status, safe_delivery, worker_status
from .admin import require_admin


def no_store(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/operations", tags=["operations"], dependencies=[Depends(no_store)])
metrics_security = HTTPBearer(auto_error=False)


class Reauthenticate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=256)
    code: str | None = Field(default=None, max_length=128)


def require_metrics_access(credentials=Depends(metrics_security), session=Depends(get_session)):
    if credentials is None:
        raise HTTPException(401, "Metrics authentication required", headers={"WWW-Authenticate": "Bearer"})
    secret = getattr(settings, "operations_metrics_token", None)
    configured = secret.get_secret_value() if secret else None
    if configured and hmac.compare_digest(credentials.credentials.encode(), configured.encode()):
        return None
    user = active_user_for_token(credentials.credentials, session)
    if not user:
        raise HTTPException(401, "Metrics authentication required", headers={"WWW-Authenticate": "Bearer"})
    require_unrestricted(session)
    if user.role != "admin":
        raise HTTPException(403, "Installation operator access required")
    return user


def reauthenticate_operator(session, user, credentials, data, scope):
    account_limit(session, "operations-" + scope, user.id, 10, 3600)
    state_for(session, user.id)
    session.execute(update(AccountSecurity).where(AccountSecurity.user_id == user.id).values(auth_version=AccountSecurity.auth_version))
    session.expire_all()
    current = active_user_for_token(credentials.credentials, session)
    if not current or current.id != user.id or current.role != "admin":
        raise HTTPException(401, "Operator account changed. Please sign in again.")
    require_unrestricted(session)
    state = ensure_reauthentication(session, current, data.password, data.code)
    if not state.email_verified_at:
        raise HTTPException(403, "Verify your operator email before sending or retrying mail")
    return current


@router.get("/status")
def status(user=Depends(require_admin), session=Depends(get_session)):
    session.exec(text("SELECT 1"))
    integration_queues = integration_queue_status(session)
    return {
        "database": {"reachable": True},
        "mail": mail_configuration(),
        "queue": queue_status(session),
        "worker": worker_status(session),
        "integration_worker": {
            **worker_status(session, name="integrations"),
            "enabled": integration_worker_required(integration_queues),
        },
        "integration_queues": integration_queues,
        "ai_runtime_worker": {**worker_status(session, name="ai-runtime"),
            "enabled": bool(settings.copilot_enabled and not settings.demo_instance)},
    }


@router.get("/mail/deliveries")
def deliveries(limit: int = Query(default=50, ge=1, le=100), before: datetime | None = None,
               user=Depends(require_admin), session=Depends(get_session)):
    query = select(MailOutbox)
    if before:
        if before.tzinfo is not None:
            before = before.astimezone(timezone.utc).replace(tzinfo=None)
        query = query.where(MailOutbox.created_at < before)
    rows = session.exec(query.order_by(MailOutbox.created_at.desc(), MailOutbox.id).limit(limit)).all()
    return {"items": [safe_delivery(row) for row in rows]}


@router.post("/mail/test", status_code=202)
def test_mail(data: Reauthenticate, user=Depends(require_admin), credentials=Depends(security), session=Depends(get_session)):
    current = reauthenticate_operator(session, user, credentials, data, "test")
    if not mail_configuration()["configured"]:
        raise HTTPException(503, "Email delivery is not configured correctly on this server")
    row = enqueue_mail(session, current.email, "FreeCollab delivery test",
        "This is the delivery test you requested in FreeCollab's operator settings.\n\n"
        "This diagnostic email contains no account link, recovery key, message or document content.\n"
        "A queued status means the worker still needs to deliver it; it is not a delivery confirmation.")
    session.add(MailOperation(outbox_id=row.id, requested_by=current.id))
    session.commit()
    return {"id": row.id, "status": "queued"}


@router.post("/mail/deliveries/{identifier}/retry")
def retry_mail(identifier: str, data: Reauthenticate, user=Depends(require_admin),
               credentials=Depends(security), session=Depends(get_session)):
    current = reauthenticate_operator(session, user, credentials, data, "retry")
    if not mail_configuration()["configured"]:
        raise HTTPException(503, "Email delivery is not configured correctly on this server")
    row = session.get(MailOutbox, identifier)
    if not row:
        raise HTTPException(404, "Delivery not found")
    # Conditional update prevents duplicate manual retries and races with claims.
    changed = session.exec(update(MailOutbox).where(MailOutbox.id == identifier, MailOutbox.status == "failed").values(
        status="pending", attempts=0, next_attempt_at=datetime.utcnow(), claimed_at=None, failure=None,
    ))
    if changed.rowcount != 1:
        raise HTTPException(409, "Only failed deliveries can be retried")
    # Keep original encrypted message/recipient and outbox ID for crash replay.
    operation = session.get(MailOperation, identifier)
    if operation:
        operation.kind = "retry"
        operation.requested_by = current.id
        operation.created_at = datetime.utcnow()
        session.add(operation)
    else:
        session.add(MailOperation(outbox_id=identifier, requested_by=current.id, kind="retry"))
    session.commit()
    session.refresh(row)
    return safe_delivery(row)


@router.get("/metrics")
def metrics(user=Depends(require_metrics_access), session=Depends(get_session)):
    queue = queue_status(session)
    worker = worker_status(session)
    config = mail_configuration()
    integration_queues = integration_queue_status(session)
    integration_worker = worker_status(session, name="integrations")
    integrations_enabled = integration_worker_required(integration_queues)
    ai_worker = worker_status(session, name="ai-runtime")
    ai_enabled = bool(settings.copilot_enabled and not settings.demo_instance)
    lines = [
        "# HELP freecollab_mail_configured Whether configured mail delivery is usable.",
        "# TYPE freecollab_mail_configured gauge",
        f"freecollab_mail_configured {int(config['configured'])}",
        "# HELP freecollab_mail_worker_healthy Whether a worker polled in the last 180 seconds.",
        "# TYPE freecollab_mail_worker_healthy gauge",
        f"freecollab_mail_worker_healthy {int(worker['status'] == 'healthy')}",
        "# HELP freecollab_mail_outbox_messages Current retained outbox records by status.",
        "# TYPE freecollab_mail_outbox_messages gauge",
        *[f'freecollab_mail_outbox_messages{{status="{key}"}} {queue[key]}' for key in ("pending", "processing", "failed", "delivered")],
        "# HELP freecollab_mail_oldest_pending_seconds Age of the oldest pending or processing record.",
        "# TYPE freecollab_mail_oldest_pending_seconds gauge",
        f"freecollab_mail_oldest_pending_seconds {queue['oldest_pending_seconds']}",
        "# HELP freecollab_integrations_enabled Whether an integration-worker feature is enabled or persisted always-on work requires it.",
        "# TYPE freecollab_integrations_enabled gauge",
        f"freecollab_integrations_enabled {int(integrations_enabled)}",
        "# HELP freecollab_integrations_worker_healthy Whether the integration worker polled in the last 180 seconds.",
        "# TYPE freecollab_integrations_worker_healthy gauge",
        f"freecollab_integrations_worker_healthy {int(integration_worker['status'] == 'healthy')}",
        "# HELP freecollab_ai_runtime_enabled Whether the independent AI lane is enabled for this installation.",
        "# TYPE freecollab_ai_runtime_enabled gauge",
        f"freecollab_ai_runtime_enabled {int(ai_enabled)}",
        "# HELP freecollab_ai_runtime_worker_healthy Whether the actual AI worker polled in the last 180 seconds.",
        "# TYPE freecollab_ai_runtime_worker_healthy gauge",
        f"freecollab_ai_runtime_worker_healthy {int(ai_worker['status'] == 'healthy')}",
        "# HELP freecollab_integration_queue_records Retained jobs, connections, schedules or maintenance records by worker group and state.",
        "# TYPE freecollab_integration_queue_records gauge",
        *[f'freecollab_integration_queue_records{{queue="{name}",status="{status}"}} {count}'
          for name, counters in integration_queues.items() for status, count in counters.items() if status != "oldest_pending_seconds"],
        "# HELP freecollab_integration_oldest_pending_seconds Age of the oldest unsent/processing job; proof anchoring wait is excluded.",
        "# TYPE freecollab_integration_oldest_pending_seconds gauge",
        *[f'freecollab_integration_oldest_pending_seconds{{queue="{name}"}} {counters["oldest_pending_seconds"]}'
          for name, counters in integration_queues.items()],
    ]
    return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4", headers={"Cache-Control": "no-store"})
