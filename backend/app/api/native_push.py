"""Native notification opt-in requires the linked mobile sign-in and device proof."""
from datetime import datetime, timedelta
import hmac
import json
import secrets
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from ..database import get_session
from ..models.account_security import AccountSecurity
from ..models.user import User
from ..models.native_clients import NativeClientSession
from ..models.native_push import NativePushDevice, NativePushDelivery
from ..models.notification import NotificationPreference
from ..services.connect import channel_view_workspace_id
from ..services import native_push as service
from ..services import native_calls
from ..services.account_security import account_limit, digest, seal_private
from ..services.auth import decode_token_claims, get_current_user, security, token_digest
from ..services.sso_scope import current_workspace_scope
from ..services.request_guard import recheck_actor


def private(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/mobile/native", tags=["native notifications"], dependencies=[Depends(private)])


class DeviceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform: Literal["android", "ios"]
    token: str = Field(min_length=20, max_length=4096)
    calls_enabled: bool = False
    consent: Literal[True]


class ConfirmInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    challenge: str = Field(min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]+$")


def lock_actor(session, user_id):
    # Match account closure's User -> AccountSecurity order. A registration
    # either commits before erasure (and is erased) or rechecks after closure.
    session.exec(update(User).where(User.id == user_id).values(name=User.name))
    if session.get(AccountSecurity, user_id) is None:
        session.add(AccountSecurity(user_id=user_id)); session.flush()
    session.exec(update(AccountSecurity).where(AccountSecurity.user_id == user_id).values(auth_version=AccountSecurity.auth_version))
    session.expire_all()
    recheck_actor(session, user_id)


def details(row, token_hash):
    return {"id": row.id, "platform": row.platform, "created_at": row.created_at,
            "confirmed": row.confirmed_at is not None, "active": row.active, "calls_enabled": row.calls_enabled,
            "current_session": hmac.compare_digest(row.auth_token_hash, token_hash)}


@router.get("/config")
def config(user=Depends(get_current_user)):
    return service.configuration()


@router.get("/devices")
def devices(user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    query = select(NativePushDevice).where(NativePushDevice.user_id == user.id)
    if current_workspace_scope(session) is not None:
        query = query.where(NativePushDevice.auth_token_hash == token_digest(credentials.credentials))
    rows = session.exec(query.order_by(NativePushDevice.created_at.desc()).limit(service.MAX_DEVICES)).all()
    return {"items": [details(row, token_digest(credentials.credentials)) for row in rows]}


@router.post("/devices", status_code=202)
def register(data: DeviceInput, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    service.require_platform(data.platform)
    account_limit(session, "native-push-register", user.id, 20, 3600)
    lock_actor(session, user.id)
    token_hash = token_digest(credentials.credentials)
    scope = current_workspace_scope(session)
    claims = decode_token_claims(credentials.credentials)
    native = session.get(NativeClientSession, token_hash)
    if not native or native.client_kind != "mobile" or not claims or claims.get("native_client_id") != native.id:
        raise HTTPException(403, "Connect the mobile app through browser sign-in before enabling native notifications")
    token = service.validate_token(data.platform, data.token)
    capability_hash = digest(data.platform + ":" + token)
    row = session.exec(select(NativePushDevice).where(NativePushDevice.token_hash == capability_hash)).first()
    if (row and row.user_id == user.id and scope is not None and not hmac.compare_digest(row.auth_token_hash, token_hash)
            and service.device_live(session, row.id, confirmed=False, preferences=False)):
        raise HTTPException(403, "Use the original sign-in to manage this device registration")
    if row and row.user_id != user.id:
        if service.device_live(session, row.id, confirmed=False, preferences=False):
            raise HTTPException(409, "This native registration belongs to another account. Remove it before switching notification ownership.")
        # A logged-out/expired owner cannot receive new pushes. The replacement
        # account must still prove possession through a fresh vendor delivery.
        native_calls.revoke_devices(session, push_device_id=row.id)
        session.exec(delete(NativePushDelivery).where(NativePushDelivery.device_id == row.id))
        session.delete(row); session.flush(); row = None
    if (row and row.active and row.confirmed_at and row.calls_enabled == data.calls_enabled
            and hmac.compare_digest(row.auth_token_hash, token_hash)
            and service.device_live(session, row.id)):
        return {"id": row.id, "status": "enabled", "expires_in": 0}
    if not row:
        count = session.exec(select(func.count()).select_from(NativePushDevice).where(NativePushDevice.user_id == user.id)).one()
        if count >= service.MAX_DEVICES:
            raise HTTPException(409, "Remove an old native notification device first")
        row = NativePushDevice(user_id=user.id, platform=data.platform, token_hash=capability_hash,
                               encrypted_token="", encrypted_claims="", auth_token_hash=token_hash)
    native_calls.revoke_devices(session, push_device_id=row.id)
    # Pending/re-registration immediately invalidates deliveries for the prior
    # sign-in. Old references are never rebound to a replacement token/account.
    session.exec(delete(NativePushDelivery).where(NativePushDelivery.device_id == row.id))
    challenge = secrets.token_urlsafe(32)
    row.auth_token_hash = token_hash
    row.encrypted_token = seal_private(token, "native-push-token", row.id)
    row.encrypted_claims = seal_private(json.dumps(claims, separators=(",", ":")), "native-push-authority", row.id)
    row.challenge_hash, row.challenge_expires_at = digest(challenge), datetime.utcnow() + timedelta(minutes=5)
    row.active, row.confirmed_at, row.calls_enabled = False, None, data.calls_enabled
    row.last_notification_at, row.last_notification_id = datetime.utcnow(), ""
    session.add(row)
    device_id = row.id
    proof_hash = digest(challenge)
    try:
        session.commit()
    except IntegrityError:
        session.rollback(); raise HTTPException(409, "Native registration changed; retry") from None
    try:
        if not service.device_live(session, device_id, confirmed=False, preferences=False):
            raise HTTPException(401, "The mobile sign-in changed")
        def authorized():
            current = service.device_live(session, device_id, confirmed=False, preferences=False)
            allowed = bool(current and hmac.compare_digest(current.auth_token_hash, token_hash)
                           and current.challenge_hash == proof_hash)
            session.commit()
            return allowed
        service.dispatch(data.platform, token, {"type": "subscription_confirmation", "device_id": device_id, "challenge": challenge}, authorize=authorized)
    except (service.ProviderError, HTTPException) as exc:
        if isinstance(exc, service.ProviderError) and exc.code == "authorization_changed":
            raise HTTPException(401, "The mobile sign-in changed") from None
        raise HTTPException(503, "The native notification confirmation could not be delivered") from None
    if not authorized():
        raise HTTPException(401, "The mobile sign-in changed")
    return {"id": device_id, "status": "confirmation_pending", "expires_in": 300}


@router.post("/devices/{device_id}/confirm")
def confirm(device_id: str, data: ConfirmInput, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    account_limit(session, "native-push-confirm", user.id, 30, 300)
    lock_actor(session, user.id)
    row = session.get(NativePushDevice, device_id)
    if not row or row.user_id != user.id:
        raise HTTPException(404, "Native notification device not found")
    if (not row.challenge_hash or not row.challenge_expires_at or row.challenge_expires_at <= datetime.utcnow()
            or not hmac.compare_digest(row.challenge_hash, digest(data.challenge))
            or not hmac.compare_digest(row.auth_token_hash, token_digest(credentials.credentials))
            or not service.device_live(session, row.id, confirmed=False, preferences=False)):
        raise HTTPException(400, "Native device confirmation is invalid or expired")
    row.active, row.confirmed_at = True, datetime.utcnow()
    row.challenge_hash, row.challenge_expires_at = None, None
    preference = session.exec(select(NotificationPreference).where(NotificationPreference.user_id == user.id)).first() or NotificationPreference(user_id=user.id)
    preference.push = True
    # Token rotation retains at most one confirmed registration per native
    # client session/platform. Other native sessions/devices remain independent.
    prior = session.exec(select(NativePushDevice).where(NativePushDevice.auth_token_hash == row.auth_token_hash,
        NativePushDevice.platform == row.platform, NativePushDevice.id != row.id)).all()
    for old in prior:
        native_calls.revoke_devices(session, push_device_id=old.id)
        session.exec(delete(NativePushDelivery).where(NativePushDelivery.device_id == old.id)); session.delete(old)
    session.add(preference); session.add(row); session.commit()
    return details(row, token_digest(credentials.credentials))


@router.delete("/devices/{device_id}", status_code=204)
def remove(device_id: str, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    lock_actor(session, user.id)
    row = session.get(NativePushDevice, device_id)
    if (not row or row.user_id != user.id or current_workspace_scope(session) is not None
            and not hmac.compare_digest(row.auth_token_hash, token_digest(credentials.credentials))):
        raise HTTPException(404, "Native notification device not found")
    native_calls.revoke_devices(session, push_device_id=row.id)
    session.exec(delete(NativePushDelivery).where(NativePushDelivery.device_id == row.id))
    session.delete(row); session.commit()


@router.get("/deliveries/{delivery_id}")
def resolve(delivery_id: str, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    row = session.get(NativePushDelivery, delivery_id)
    device = session.get(NativePushDevice, row.device_id) if row else None
    if (not row or not device or device.user_id != user.id
            or not hmac.compare_digest(device.auth_token_hash, token_digest(credentials.credentials))
            or not service.device_live(session, device.id, channel_id=row.channel_id)
            or not service.activity_live(session, row, device)):
        raise HTTPException(404, "The notification is no longer available")
    workspace_id = channel_view_workspace_id(session, row.channel_id, user.id)
    return {"type": row.kind, "channel_id": row.channel_id, "workspace_id": workspace_id,
            "meeting_id": row.meeting_id, "transport": row.transport}


from .native_calls import router as calling_router
router.include_router(calling_router)
