"""Channel-admin metadata with a native/Matrix fence and exact revision checks."""
import json
import unicodedata

from fastapi import HTTPException
from nacl.exceptions import CryptoError

from ..models.channel import Channel
from ..models.channel_metadata import ChannelMetadata
from .access import lock_channel_writes, require_channel_member
from .connect import connection_for_channel
from .crypto import decrypt_message, encrypt_message
from .matrix_bridge import is_secure_channel
from .request_guard import recheck_actor
from .workspaces import channel_workspace_id, lock_workspace


def shared_channel(session, channel_id: str) -> bool:
    from .connect_federation import connection_for_channel as federated_channel
    return bool(connection_for_channel(session, channel_id) or federated_channel(session, channel_id))


def locked_admin(session, channel_id: str, user_id: str):
    workspace_id = channel_workspace_id(session, channel_id)
    if workspace_id and not shared_channel(session, channel_id):
        lock_workspace(session, workspace_id)
    lock_channel_writes(session, channel_id)
    # The dependency's ORM identities may predate waiting for either writer lock.
    session.expire_all()
    recheck_actor(session, user_id)
    member = require_channel_member(session, channel_id, user_id)
    if member.role != "admin":
        raise HTTPException(403, "Channel admin access required")
    channel = session.get(Channel, channel_id)
    if channel.type in {"direct", "group_direct"}:
        raise HTTPException(409, "Direct conversation details use their conversation controls")
    if shared_channel(session, channel_id):
        raise HTTPException(409, "Shared-channel details are managed by their connection")
    return channel


def seal_payload(channel_id: str, version: int, fields: dict) -> str:
    return encrypt_message(json.dumps({"kind": "channel_metadata", "channel_id": channel_id,
        "version": version, "fields": fields}, ensure_ascii=False, separators=(",", ":")), channel_id)


def open_payload(session, row: ChannelMetadata) -> dict:
    if row.content_mode != "server" or is_secure_channel(session, row.channel_id):
        raise HTTPException(409, "Native channel details are unavailable for an encrypted channel")
    try:
        payload = json.loads(decrypt_message(row.encrypted_payload, row.channel_id))
        if (payload.get("kind"), payload.get("channel_id"), payload.get("version")) != ("channel_metadata", row.channel_id, row.version):
            raise ValueError
        fields = payload["fields"]
        if (not isinstance(fields, dict) or set(fields) != {"name", "description"}
                or not isinstance(fields["name"], str) or not 1 <= len(fields["name"]) <= 100
                or fields["name"] != fields["name"].strip()
                or any(unicodedata.category(char) in {"Cc", "Cs"} for char in fields["name"])
                or not isinstance(fields["description"], str) or len(fields["description"]) > 1000
                or fields["description"] != fields["description"].strip()
                or any(unicodedata.category(char) in {"Cc", "Cs"} and char not in "\n\t" for char in fields["description"])):
            raise ValueError
        return fields
    except (CryptoError, ValueError, TypeError, AttributeError, KeyError):
        raise HTTPException(409, "Stored channel details could not be verified") from None


def view(session, channel_id: str, user_id: str) -> dict:
    recheck_actor(session, user_id)
    member = require_channel_member(session, channel_id, user_id)
    channel = session.get(Channel, channel_id)
    secure = is_secure_channel(session, channel_id)
    row = session.get(ChannelMetadata, channel_id)
    if row and ((row.content_mode == "matrix") != secure):
        raise HTTPException(409, "Channel encryption changed; reload channel details")
    fields = open_payload(session, row) if row and not secure else None
    if fields and fields["name"] != channel.name:
        raise HTTPException(409, "Stored channel details could not be verified")
    return {"channel_id": channel_id, "version": row.version if row else 0,
        "secure": secure, "authoritative": row is not None,
        "name": None if secure else fields["name"] if fields else channel.name,
        "description": None if secure else fields["description"] if fields else "",
        "matrix_event_id": row.matrix_event_id if row and secure else None,
        "updated_at": row.updated_at if row else None, "updated_by": row.updated_by if row else None,
        "management": "conversation" if channel.type in {"direct", "group_direct"} else "connection" if shared_channel(session, channel_id) else "channel",
        "can_manage": member.role == "admin" and channel.type not in {"direct", "group_direct"} and not shared_channel(session, channel_id)}
