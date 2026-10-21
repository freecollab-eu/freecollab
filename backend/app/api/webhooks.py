"""Explicit channel-scoped webhook grants for workspace administrators."""
from datetime import datetime
import json
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from sqlalchemy import func, update
from sqlmodel import select

from ..database import get_session
from ..models.webhook import WebhookDelivery, WebhookEndpoint
from ..services.auth import active_user_for_token, get_current_user, security
from ..services.account_security import account_limit
from ..services.workspaces import lock_workspace, require_workspace_admin
from ..services import webhooks as service

router = APIRouter(tags=["webhooks"])
ShortID = Annotated[str, StringConstraints(min_length=1, max_length=128)]


class CreateWebhook(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=1, max_length=2048)
    channel_ids: list[ShortID] = Field(min_length=1, max_length=25)
    event_types: list[str] = Field(min_length=1, max_length=6)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value):
        if not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("Use a webhook name without control characters")
        return value.strip()

    @field_validator("channel_ids")
    @classmethod
    def unique_channels(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("Each channel may be selected once")
        return sorted(values)

    @field_validator("event_types")
    @classmethod
    def valid_events(cls, values):
        if not set(values) <= service.EVENT_TYPES or len(set(values)) != len(values):
            raise ValueError("Select supported, unique event types")
        return sorted(values)


class UpdateWebhook(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str | None = Field(default=None, min_length=1, max_length=80)
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    channel_ids: list[ShortID] | None = Field(default=None, min_length=1, max_length=25)
    event_types: list[str] | None = Field(default=None, min_length=1, max_length=6)
    enabled: bool | None = None

    @field_validator("name")
    @classmethod
    def valid_name(cls, value):
        return CreateWebhook.valid_name(value) if value is not None else value

    @field_validator("channel_ids")
    @classmethod
    def valid_channels(cls, value):
        return CreateWebhook.unique_channels(value) if value is not None else value

    @field_validator("event_types")
    @classmethod
    def valid_events(cls, value):
        return CreateWebhook.valid_events(value) if value is not None else value


def lock_actor(session, workspace_id, user_id, token):
    lock_workspace(session, workspace_id)
    session.expire_all()
    current = active_user_for_token(token, session)
    if not current or current.id != user_id:
        raise HTTPException(401, "Your session changed. Sign in again.")


def find_endpoint(session, workspace_id, identifier, user_id, *, lock=False, token=None):
    if lock:
        lock_actor(session, workspace_id, user_id, token)
    require_workspace_admin(session, workspace_id, user_id)
    row = session.get(WebhookEndpoint, identifier)
    if not row or row.workspace_id != workspace_id or row.deleted_at or not service.may_manage(session, row, user_id):
        raise HTTPException(404, "Webhook not found within your channel grants")
    return row


@router.get("/workspaces/{workspace_id}/webhooks")
def list_webhooks(workspace_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    require_workspace_admin(session, workspace_id, user.id)
    rows = session.exec(select(WebhookEndpoint).where(WebhookEndpoint.workspace_id == workspace_id, WebhookEndpoint.deleted_at == None).order_by(WebhookEndpoint.created_at)).all()
    return {"enabled": service.webhooks_enabled(), "event_types": sorted(service.EVENT_TYPES),
            "webhooks": [service.endpoint_data(row) for row in rows if service.may_manage(session, row, user.id)]}


@router.post("/workspaces/{workspace_id}/webhooks", status_code=201)
def create_webhook(workspace_id: str, data: CreateWebhook, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    service.require_enabled()
    service.require_grants(session, workspace_id, user.id, data.channel_ids)
    normalized, origin = service.validate_destination(data.url)
    lock_actor(session, workspace_id, user.id, credentials.credentials)
    service.require_grants(session, workspace_id, user.id, data.channel_ids)
    count = session.exec(select(func.count()).select_from(WebhookEndpoint).where(WebhookEndpoint.workspace_id == workspace_id, WebhookEndpoint.deleted_at == None)).one()
    if count >= 20:
        raise HTTPException(409, "This workspace already has the maximum of 20 webhooks")
    secret = secrets.token_urlsafe(32)
    row = WebhookEndpoint(workspace_id=workspace_id, created_by=user.id, granted_by=user.id,
                          name=data.name, origin=origin, encrypted_url="", encrypted_secret="",
                          channel_ids_json=json.dumps(data.channel_ids), event_types_json=json.dumps(data.event_types))
    service.seal_endpoint(row, normalized, secret)
    session.add(row)
    session.commit()
    return {"webhook": service.endpoint_data(row), "secret": secret}


@router.patch("/workspaces/{workspace_id}/webhooks/{identifier}")
def update_webhook(workspace_id: str, identifier: str, data: UpdateWebhook, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    # Pause/revoke still work when the operator disabled global delivery.
    if any(getattr(data, key) is not None for key in ("url", "channel_ids", "event_types")) or data.enabled is True:
        service.require_enabled()
    require_workspace_admin(session, workspace_id, user.id)
    destination = service.validate_destination(data.url) if data.url is not None else None
    row = find_endpoint(session, workspace_id, identifier, user.id, lock=True, token=credentials.credentials)
    channels = data.channel_ids if data.channel_ids is not None else service.channel_ids(row)
    service.require_grants(session, workspace_id, user.id, channels)
    if data.name is not None:
        row.name = data.name
    if destination:
        service.seal_endpoint(row, destination[0])
        row.origin = destination[1]
    if data.channel_ids is not None:
        row.channel_ids_json = json.dumps(channels)
    if data.event_types is not None:
        row.event_types_json = json.dumps(data.event_types)
    if data.enabled is not None:
        row.enabled = data.enabled
    if destination or data.channel_ids is not None or data.event_types is not None or data.enabled is not None:
        row.generation += 1
        service.cancel_pending(session, row)
    row.granted_by, row.updated_at = user.id, datetime.utcnow()
    session.add(row)
    session.commit()
    return service.endpoint_data(row)


@router.post("/workspaces/{workspace_id}/webhooks/{identifier}/rotate")
def rotate_secret(workspace_id: str, identifier: str, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    service.require_enabled()
    row = find_endpoint(session, workspace_id, identifier, user.id, lock=True, token=credentials.credentials)
    secret = secrets.token_urlsafe(32)
    service.seal_endpoint(row, secret=secret)
    row.generation += 1
    row.granted_by, row.updated_at = user.id, datetime.utcnow()
    service.cancel_pending(session, row)
    session.add(row)
    session.commit()
    return {"webhook": service.endpoint_data(row), "secret": secret}


@router.delete("/workspaces/{workspace_id}/webhooks/{identifier}")
def delete_webhook(workspace_id: str, identifier: str, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    row = find_endpoint(session, workspace_id, identifier, user.id, lock=True, token=credentials.credentials)
    row.enabled, row.deleted_at, row.updated_at = False, datetime.utcnow(), datetime.utcnow()
    row.generation += 1
    # Remove reusable URL credentials and signing material from a tombstone.
    row.encrypted_secret, row.encrypted_url = "", ""
    service.cancel_pending(session, row)
    session.add(row)
    session.commit()
    return {"deleted": True}


@router.post("/workspaces/{workspace_id}/webhooks/{identifier}/test")
def test_webhook(workspace_id: str, identifier: str, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    service.require_enabled()
    find_endpoint(session, workspace_id, identifier, user.id)
    account_limit(session, "webhook-test", user.id, 10, 60)
    row = find_endpoint(session, workspace_id, identifier, user.id, lock=True, token=credentials.credentials)
    if not row.enabled:
        raise HTTPException(409, "Enable this webhook before sending a test")
    delivery = service.enqueue_test(session, row)
    session.commit()
    return {"queued": True, "delivery_id": delivery.id}


@router.get("/workspaces/{workspace_id}/webhooks/{identifier}/deliveries")
def list_deliveries(workspace_id: str, identifier: str, limit: int = Query(50, ge=1, le=100), user=Depends(get_current_user), session=Depends(get_session)):
    row = find_endpoint(session, workspace_id, identifier, user.id)
    rows = session.exec(select(WebhookDelivery).where(WebhookDelivery.webhook_id == row.id).order_by(WebhookDelivery.created_at.desc()).limit(limit)).all()
    # Current configuration may intentionally remove a formerly authorized
    # channel. Its old activity does not remain exposed in delivery history.
    allowed = set(service.channel_ids(row))
    return {"deliveries": [service.delivery_data(item) for item in rows if item.channel_id is None or item.channel_id in allowed]}


@router.post("/workspaces/{workspace_id}/webhooks/{identifier}/deliveries/{delivery_id}/retry")
def retry_delivery(workspace_id: str, identifier: str, delivery_id: str, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    service.require_enabled()
    find_endpoint(session, workspace_id, identifier, user.id)
    account_limit(session, "webhook-retry", user.id, 20, 60)
    row = find_endpoint(session, workspace_id, identifier, user.id, lock=True, token=credentials.credentials)
    item = session.get(WebhookDelivery, delivery_id)
    if not item or item.webhook_id != row.id or (item.channel_id and item.channel_id not in service.channel_ids(row)):
        raise HTTPException(404, "Delivery not found")
    if not row.enabled or item.generation != row.generation:
        raise HTTPException(409, "This delivery belongs to an inactive configuration")
    if item.status not in {"failed", "pending"}:
        raise HTTPException(409, "Only pending or failed deliveries can be retried")
    item.status, item.attempts, item.next_attempt_at = "pending", 0, datetime.utcnow()
    item.failure, item.http_status, item.claim_token = None, None, None
    session.add(item)
    session.commit()
    return {"queued": True, "delivery_id": item.id}
