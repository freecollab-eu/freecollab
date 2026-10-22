"""Purpose-limited background calling; never a general account bearer or media."""
from datetime import datetime
from uuid import uuid4
from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel
from .native_push import NativePushDevice  # noqa: F401
from .sfu import SFUParticipant  # noqa: F401


class NativeCallDevice(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    push_device_id: str = Field(foreign_key="nativepushdevice.id", unique=True, index=True)
    source_hash: str = Field(index=True)
    push_confirmed_at: datetime
    encrypted_claims: str
    credential_hash: str = Field(unique=True, index=True)
    platform: str
    active: bool = True
    encrypted_voip_token: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime


class NativeCallSession(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("device_id", "client_call_id"),)
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    device_id: str = Field(foreign_key="nativecalldevice.id", index=True)
    participant_id: str = Field(foreign_key="sfuparticipant.id", unique=True)
    room_id: str = Field(index=True)
    channel_id: str = Field(foreign_key="channel.id", index=True)
    client_call_id: str
    delivery_id: str | None = None
    muted: bool = False
    held: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    heartbeat_at: datetime = Field(default_factory=datetime.utcnow)
    ended_at: datetime | None = None
    expires_at: datetime


class NativeCallConsent(SQLModel, table=True):
    device_id: str = Field(foreign_key="nativecalldevice.id", primary_key=True)
    channel_id: str = Field(foreign_key="channel.id", primary_key=True)
    matrix_room_id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime
