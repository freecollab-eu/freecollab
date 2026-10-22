"""Per-device notification consent and exactly-once native offline message replay."""
from datetime import datetime, timedelta
import hashlib
import hmac
import json
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import delete, func, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from ..database import get_session
from ..models.channel import Channel
from ..models.account_security import AccountSecurity
from ..models.message import Message
from ..models.notification import NotificationPreference
from ..models.push import OfflineMessageReceipt, PushDelivery, PushSubscription
from ..schemas import MessageText
from ..services import push as service
from ..services.access import lock_channel_writes, require_channel_member
from ..services.account_security import _secret_key, account_limit, digest, seal_private
from ..services.auth import active_user_for_token, decode_token_claims, get_current_user, security, token_digest
from ..services.chat_notifications import (record_message_change, require_native_channel,
    subscribe_participant, validate_mentions)
from ..services.crypto import encrypt_message
from ..services.direct_messages import restore_direct_inbox
from ..services.workspaces import require_direct_message_writable
from ..services.request_guard import recheck_actor


def private(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/mobile", tags=["mobile and offline"], dependencies=[Depends(private)])


class SubscriptionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: str = Field(min_length=1, max_length=4096)
    keys: dict[str, str]


class ConfirmInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    challenge: str = Field(min_length=32, max_length=128)


class OfflineMessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str = Field(pattern=r"^[A-Za-z0-9_-]{16,80}$")
    content: MessageText
    mentions: list[Annotated[str, StringConstraints(min_length=1, max_length=128)]] = Field(default_factory=list, max_length=50)
    parent_id: str | None = Field(default=None, min_length=1, max_length=128)


def details(row, token_hash):
    return {"id": row.id, "created_at": row.created_at, "confirmed": row.confirmed_at is not None,
            "active": row.active, "current_session": hmac.compare_digest(row.auth_token_hash, token_hash)}


def lock_actor(session, user_id):
    session.exec(update(AccountSecurity).where(AccountSecurity.user_id == user_id).values(auth_version=AccountSecurity.auth_version))
    session.expire_all()
    recheck_actor(session, user_id)


@router.get("/config")
def config(user=Depends(get_current_user)):
    return service.configuration()


@router.get("/subscriptions")
def subscriptions(user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    rows = session.exec(select(PushSubscription).where(PushSubscription.user_id == user.id)
                        .order_by(PushSubscription.created_at.desc()).limit(service.MAX_SUBSCRIPTIONS)).all()
    return {"items": [details(row, token_digest(credentials.credentials)) for row in rows]}


@router.post("/subscriptions", status_code=202)
def subscribe(data: SubscriptionInput, request: Request, user=Depends(get_current_user),
              credentials=Depends(security), session=Depends(get_session)):
    service.require_configured()
    account_limit(session, "push-subscribe", user.id, 20, 3600)
    normalized = service.validate_subscription(data.model_dump())
    lock_actor(session, user.id)
    endpoint_hash = digest(normalized["endpoint"])
    row = session.exec(select(PushSubscription).where(PushSubscription.endpoint_hash == endpoint_hash)).first()
    if row and row.user_id != user.id:
        raise HTTPException(409, "This browser subscription belongs to another account. Unsubscribe it in the browser first.")
    if not row:
        count = session.exec(select(func.count()).select_from(PushSubscription).where(PushSubscription.user_id == user.id)).one()
        if count >= service.MAX_SUBSCRIPTIONS:
            raise HTTPException(409, "Remove an old notification device before adding another")
        row = PushSubscription(user_id=user.id, endpoint_hash=endpoint_hash, encrypted_subscription="",
            auth_token_hash=token_digest(credentials.credentials), auth_version=0)
    claims = decode_token_claims(credentials.credentials)
    challenge = secrets.token_urlsafe(32)
    row.encrypted_subscription = seal_private(json.dumps(normalized, separators=(",", ":")), "push-subscription", row.id)
    row.auth_token_hash, row.auth_version = token_digest(credentials.credentials), claims.get("av", 0)
    row.scope_workspace_id, row.scope_provider_revision = claims.get("sso_workspace_id"), claims.get("sso_provider_revision")
    row.active, row.confirmed_at = False, None
    row.challenge_hash, row.challenge_expires_at = digest(challenge), datetime.utcnow() + timedelta(minutes=5)
    row.last_notification_at, row.last_notification_id = datetime.utcnow(), ""
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "Notification subscription changed; retry") from None
    try:
        service.dispatch(normalized, {"type": "subscription_confirmation", "subscription_id": row.id, "challenge": challenge})
    except (service.ProviderError, HTTPException):
        raise HTTPException(503, "The notification confirmation could not be delivered") from None
    recheck_actor(session, user.id)
    return {"id": row.id, "status": "confirmation_pending", "expires_in": 300}


@router.post("/subscriptions/{subscription_id}/confirm")
def confirm(subscription_id: str, data: ConfirmInput, user=Depends(get_current_user),
            credentials=Depends(security), session=Depends(get_session)):
    account_limit(session, "push-confirm", user.id, 30, 300)
    lock_actor(session, user.id)
    row = session.get(PushSubscription, subscription_id)
    if not row or row.user_id != user.id:
        raise HTTPException(404, "Notification device not found")
    if (not row.challenge_hash or not row.challenge_expires_at or row.challenge_expires_at <= datetime.utcnow()
            or not hmac.compare_digest(row.challenge_hash, digest(data.challenge))
            or not hmac.compare_digest(row.auth_token_hash, token_digest(credentials.credentials))):
        raise HTTPException(400, "Notification confirmation is invalid or expired")
    row.active, row.confirmed_at = True, datetime.utcnow()
    row.challenge_hash, row.challenge_expires_at = None, None
    preference = session.exec(select(NotificationPreference).where(NotificationPreference.user_id == user.id)).first()
    if preference is None:
        preference = NotificationPreference(user_id=user.id)
    preference.push = True
    session.add(preference); session.add(row); session.commit()
    return details(row, token_digest(credentials.credentials))


@router.delete("/subscriptions/{subscription_id}", status_code=204)
def unsubscribe(subscription_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    lock_actor(session, user.id)
    row = session.get(PushSubscription, subscription_id)
    if not row or row.user_id != user.id:
        raise HTTPException(404, "Notification device not found")
    session.exec(delete(PushDelivery).where(PushDelivery.subscription_id == row.id))
    session.delete(row); session.commit()


def offline_digest(user_id, channel_id, data):
    body = json.dumps({"user": user_id, "channel": channel_id, "content": data.content,
                       "mentions": data.mentions, "parent": data.parent_id}, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(_secret_key(), body, hashlib.sha256).hexdigest()


@router.post("/offline/channels/{channel_id}/messages")
async def offline_message(channel_id: str, data: OfflineMessageInput, user=Depends(get_current_user),
                          credentials=Depends(security), session=Depends(get_session)):
    from .messages import message_data
    from .mentions import process_message_mentions
    from ..realtime.socket import broadcast_to_channel
    lock_channel_writes(session, channel_id)
    session.expire_all()
    recheck_actor(session, user.id)
    current = active_user_for_token(credentials.credentials, session)
    if current is None or current.id != user.id:
        raise HTTPException(401, "Sign in again before sending queued messages")
    require_channel_member(session, channel_id, current.id)
    require_direct_message_writable(session, channel_id, current.id)
    require_native_channel(session, channel_id)
    expected = offline_digest(current.id, channel_id, data)
    existing = session.get(OfflineMessageReceipt, (current.id, data.client_id))
    if existing:
        if existing.channel_id != channel_id or not hmac.compare_digest(existing.payload_digest, expected):
            raise HTTPException(409, "This queued message identifier already has different content")
        message = session.get(Message, existing.message_id) if existing.message_id else None
        if not message:
            raise HTTPException(409, "The queued message receipt is unavailable")
        return {**message_data(message), "client_id": data.client_id, "replayed": True}
    mention_ids = validate_mentions(session, channel_id, data.mentions)
    parent = session.get(Message, data.parent_id) if data.parent_id else None
    if data.parent_id and (not parent or parent.deleted or parent.channel_id != channel_id):
        raise HTTPException(404, "Parent message not found in channel")
    from ..services.enterprise_governance import enforce_native_content
    warnings = enforce_native_content(session, channel_id, current.id, data.content)
    message = Message(id=secrets.token_hex(16), channel_id=channel_id, author_id=current.id,
        content=encrypt_message(data.content, channel_id), encrypted=True,
        parent_id=data.parent_id, mentions=json.dumps(mention_ids))
    session.add(message)
    if parent:
        parent.thread_count = (parent.thread_count or 0) + 1
        subscribe_participant(session, current.id, parent)
    record_message_change(session, message, "created")
    if parent:
        record_message_change(session, parent, "thread")
    restore_direct_inbox(session, channel_id)
    from ..services.webhooks import enqueue_channel_event
    enqueue_channel_event(session, channel_id, "message.created", message.id)
    session.add(OfflineMessageReceipt(user_id=current.id, client_id=data.client_id, channel_id=channel_id,
        message_id=message.id, payload_digest=expected))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "Queued message changed concurrently; retry the same identifier") from None
    await process_message_mentions(message, session.get(Channel, channel_id), current, session, mention_ids=mention_ids)
    result = {**message_data(message), "client_id": data.client_id, "replayed": False, "policy_warnings": warnings}
    payload = {**result, "created_at": message.created_at.isoformat()}
    session.commit()
    await broadcast_to_channel(channel_id, "message", payload)
    return result
