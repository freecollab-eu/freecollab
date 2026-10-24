"""Explicit, compare-and-swap updates to native or client-encrypted channel labels."""
from datetime import datetime
import unicodedata

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..database import get_session
from ..models.channel_metadata import ChannelMetadata
from ..realtime.socket import broadcast_to_channel
from ..services import channel_metadata as service
from ..services.auth import get_current_user
from ..services.matrix_bridge import is_secure_channel, validate_secure_event


def no_store(response: Response):
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(prefix="/channels/{channel_id}/metadata", tags=["channel metadata"], dependencies=[Depends(no_store)])


class MetadataUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_version: int = Field(ge=0)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    matrix_event_id: str | None = Field(default=None, min_length=1, max_length=1024)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        if value is None:
            return value
        if not value.strip() or any(unicodedata.category(char) in {"Cc", "Cs"} for char in value):
            raise ValueError("Use a nonempty channel name without control characters")
        return value.strip()

    @field_validator("description")
    @classmethod
    def clean_description(cls, value):
        if value is None:
            return value
        value = value.replace("\r\n", "\n").replace("\r", "\n")
        if any(unicodedata.category(char) in {"Cc", "Cs"} and char not in "\n\t" for char in value):
            raise ValueError("Use a channel description without control characters")
        return value.strip()

    @model_validator(mode="after")
    def content_mode(self):
        if self.matrix_event_id is not None:
            if {"name", "description"} & self.model_fields_set:
                raise ValueError("Encrypted channel details accept only an event reference")
        elif self.name is None or self.description is None:
            raise ValueError("Supply a channel name and description")
        return self


@router.get("")
def get_metadata(channel_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    return service.view(session, channel_id, user.id)


@router.patch("")
async def update_metadata(data: MetadataUpdate, channel_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    channel = service.locked_admin(session, channel_id, user.id)
    row = session.get(ChannelMetadata, channel_id)
    version = row.version if row else 0
    if version != data.expected_version:
        raise HTTPException(409, "Channel details changed; reload before saving")
    secure = is_secure_channel(session, channel_id)
    if secure != (data.matrix_event_id is not None):
        raise HTTPException(409, "Encrypted channels require encrypted channel details")
    if secure:
        validate_secure_event(session, channel_id, data.matrix_event_id, user.id)
        # Matrix verification does remote I/O. Recheck the live scope and role,
        # then the exact version while retaining the same channel writer lock.
        channel = service.locked_admin(session, channel_id, user.id)
        row = session.get(ChannelMetadata, channel_id)
        if (row.version if row else 0) != data.expected_version or not is_secure_channel(session, channel_id):
            raise HTTPException(409, "Channel details changed; reload before saving")
    if row and ((row.content_mode == "matrix") != secure):
        raise HTTPException(409, "Channel encryption changed; reload channel details")
    if secure and row and row.matrix_event_id == data.matrix_event_id:
        return service.view(session, channel_id, user.id)
    if not secure and row:
        current = service.open_payload(session, row)
        if current == {"name": data.name, "description": data.description}:
            return service.view(session, channel_id, user.id)
    now = datetime.utcnow()
    if row is None:
        row = ChannelMetadata(channel_id=channel_id, created_by=user.id, updated_by=user.id)
    row.version, row.updated_by, row.updated_at = version + 1, user.id, now
    row.content_mode = "matrix" if secure else "server"
    row.matrix_event_id = data.matrix_event_id if secure else None
    row.encrypted_payload = None if secure else service.seal_payload(channel_id, row.version, {"name": data.name, "description": data.description})
    if not secure:
        channel.name = data.name
        session.add(channel)
    session.add(row)
    session.commit()
    result = service.view(session, channel_id, user.id)
    # Only a revision hint crosses realtime; recipients reauthorize their fetch.
    await broadcast_to_channel(channel_id, "channel_metadata_updated", {"channel_id": channel_id, "version": row.version})
    return result
