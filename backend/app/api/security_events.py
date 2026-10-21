"""Read-only bounded installation-operator security event history."""
import base64
from datetime import datetime, timedelta, timezone
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, or_
from sqlmodel import select

from ..config import settings
from ..database import get_session
from ..models.security_event import SecurityEvent
from .admin import require_admin
from .operations import no_store

router = APIRouter(prefix="/operations", tags=["operations"], dependencies=[Depends(no_store)])


def encode_cursor(row):
    raw = json.dumps([row.created_at.isoformat(timespec="microseconds"), row.id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(value):
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        parts = json.loads(raw)
        if not isinstance(parts, list) or len(parts) != 2 or not all(isinstance(p, str) for p in parts):
            raise ValueError
        when = datetime.fromisoformat(parts[0])
        if when.tzinfo:
            when = when.astimezone(timezone.utc).replace(tzinfo=None)
        if len(parts[1]) != 32 or any(c not in "0123456789abcdef" for c in parts[1]):
            raise ValueError
        return when, parts[1]
    except (ValueError, TypeError, UnicodeError, OverflowError):
        raise HTTPException(400, "Invalid activity cursor") from None


@router.get("/security-events")
def security_events(limit: int = Query(default=50, ge=1, le=100),
                    cursor: str | None = Query(default=None, max_length=256),
                    outcome: str | None = Query(default=None, pattern="^(success|denied|error)$"),
                    user=Depends(require_admin), session=Depends(get_session)):
    query = select(SecurityEvent).where(SecurityEvent.created_at >= datetime.utcnow() - timedelta(days=settings.security_audit_retention_days))
    if outcome:
        query = query.where(SecurityEvent.outcome == outcome)
    if cursor:
        when, identifier = decode_cursor(cursor)
        query = query.where(or_(SecurityEvent.created_at < when,
                               and_(SecurityEvent.created_at == when, SecurityEvent.id < identifier)))
    rows = session.exec(query.order_by(SecurityEvent.created_at.desc(), SecurityEvent.id.desc()).limit(limit + 1)).all()
    items = rows[:limit]
    return {"items": [{field: getattr(row, field) for field in (
        "id", "created_at", "actor_kind", "actor_id", "workspace_id", "target_id", "action", "outcome", "status_code")} for row in items],
        "next_cursor": encode_cursor(items[-1]) if len(rows) > limit else None,
        "retention_days": settings.security_audit_retention_days, "max_records": settings.security_audit_max_records}
