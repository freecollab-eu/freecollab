"""Purpose-limited native call capability, never accepted by account APIs."""
from datetime import datetime
import hmac
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import select

from ..database import get_session
from ..models.native_calls import NativeCallDevice
from ..services import native_calls as service
from ..services.account_security import account_limit, seal_private
from ..services.auth import get_current_user, security, token_digest

router = APIRouter(prefix="/calling", tags=["native calling"])
Identifier = str

class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")

class Register(Input):
    push_device_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    consent: Literal[True]

class Answer(Input):
    explicit_answer: Literal[True]
    client_call_id: str = Field(pattern=r"^[a-f0-9]{32}$")

class Join(Answer):
    room_id: str = Field(pattern=r"^[a-f0-9]{32}$")

class VoIP(Input):
    token: str = Field(pattern=r"^[a-fA-F0-9]{64,200}$")

class Control(Input):
    muted: bool | None = None
    held: bool | None = None

class Consent(Input):
    acknowledge_customer_sfu_audio_not_e2ee: Literal[True]


def capability(device_id: str = Header(alias="X-FreeCollab-Call-Device"), credentials=Depends(security), session=Depends(get_session)):
    return service.authenticate(session, device_id, credentials.credentials)


def possession(device_id: str = Header(alias="X-FreeCollab-Call-Device"), credentials=Depends(security), session=Depends(get_session)):
    return service.authenticate(session, device_id, credentials.credentials, live=False)


@router.get("/config")
def config(user=Depends(get_current_user)):
    return service.configured()


@router.get("/device")
def current(user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    rows = session.exec(select(NativeCallDevice).where(NativeCallDevice.user_id == user.id,
        NativeCallDevice.source_hash == token_digest(credentials.credentials), NativeCallDevice.active == True)).all()
    return {"items": [{"id": row.id, "push_device_id": row.push_device_id, "platform": row.platform,
        "expires_at": row.expires_at, "voip_registered": bool(row.encrypted_voip_token)} for row in rows]}


@router.post("/devices", status_code=201)
def register(data: Register, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    account_limit(session, "native-call-register", user.id, 20, 3600)
    return service.register(session, user.id, credentials.credentials, data.push_device_id)


@router.delete("/device", status_code=204)
def remove(device=Depends(possession), session=Depends(get_session)):
    service.revoke_devices(session, device_id=device.id); session.commit()


@router.put("/device/voip")
def voip(data: VoIP, device=Depends(capability), session=Depends(get_session)):
    if device.platform != "ios": raise HTTPException(400, "VoIP tokens are only accepted from this installation's iOS device")
    device.encrypted_voip_token = seal_private(data.token.lower(), "native-call-voip", device.id)
    session.add(device); session.commit()
    return {"registered": True}


@router.post("/devices/{device_id}/channels/{channel_id}/media-consent")
def consent(device_id: str, channel_id: str, data: Consent, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    device = service.live_device(session, device_id, channel_id=channel_id)
    if device.user_id != user.id or not hmac.compare_digest(device.source_hash, token_digest(credentials.credentials)):
        raise HTTPException(404, "This mobile calling device was not found")
    return service.acknowledge_media(session, device_id, channel_id)


@router.get("/offers/{delivery_id}")
def offer(delivery_id: str, device=Depends(capability), session=Depends(get_session)):
    return service.offer(session, device.id, delivery_id)


@router.post("/offers/{delivery_id}/answer")
def answer(delivery_id: str, data: Answer, device=Depends(capability), session=Depends(get_session)):
    return service.join(session, device.id, delivery_id=delivery_id, client_call_id=data.client_call_id)


@router.post("/join")
def join(data: Join, device=Depends(capability), session=Depends(get_session)):
    return service.join(session, device.id, room_id=data.room_id, client_call_id=data.client_call_id)


@router.post("/sessions/{identifier}/heartbeat")
def heartbeat(identifier: str, data: Control, device=Depends(capability), session=Depends(get_session)):
    return service.heartbeat(session, device.id, identifier, muted=data.muted, held=data.held)


@router.post("/sessions/{identifier}/end")
def end(identifier: str, data: Input, device=Depends(possession), session=Depends(get_session)):
    return service.end(session, device.id, identifier)
