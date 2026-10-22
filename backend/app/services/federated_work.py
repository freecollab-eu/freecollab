"""Authorized pointer pages, never task/calendar/document plaintext."""
from datetime import datetime
import re

from fastapi import HTTPException
from sqlmodel import select

from ..models.federated_work import FederatedWorkPointer
from ..models.secure_channel import SecureChannel
from . import connect_federation as federation

MAX_POINTERS = 2000
PAGE_SIZE = 50


def require_connection(session, channel_id, user_id):
    from .access import require_channel_member
    require_channel_member(session, channel_id, user_id)
    connection = federation.connection_for_channel(session, channel_id)
    if not connection:
        raise HTTPException(404, "This channel has no cross-installation work space")
    federation.configuration()
    if not federation.grant_valid(session, connection, user_id):
        raise HTTPException(403, "Current bilateral shared-channel access is required")
    binding = session.get(SecureChannel, channel_id)
    if not binding or not binding.matrix_room_id or binding.status != "active":
        raise HTTPException(409, "The shared encrypted room is not ready")
    return connection, binding


def page(session, connection, after=0, limit=PAGE_SIZE):
    if type(after) is not int or not 0 <= after <= 2**63 - 1 or type(limit) is not int or not 1 <= limit <= PAGE_SIZE:
        raise HTTPException(422, "Invalid encrypted work index cursor")
    active = connection.status == "active" and federation.authority_valid(session, connection)
    if not active:
        return {"connection_id": connection.id, "status": "revoked", "items": [], "next_after": after, "has_more": False}
    rows = session.exec(select(FederatedWorkPointer).where(
        FederatedWorkPointer.connection_id == connection.id, FederatedWorkPointer.sequence > after,
    ).order_by(FederatedWorkPointer.sequence).limit(limit + 1)).all()
    selected = rows[:limit]
    return {"connection_id": connection.id, "status": "active", "items": [
        {"sequence": row.sequence, "matrix_event_id": row.matrix_event_id,
         "matrix_user_id": row.matrix_user_id, "created_at": row.created_at.isoformat()}
        for row in selected], "next_after": selected[-1].sequence if selected else after,
        "has_more": len(rows) > limit}


def validate_remote_page(value, connection, after):
    try:
        if (not isinstance(value, dict) or set(value) != {"connection_id", "status", "items", "next_after", "has_more"}
                or value.get("connection_id") != connection.id
                or value.get("status") != "active" or type(value.get("has_more")) is not bool
                or not isinstance(value.get("items"), list) or len(value["items"]) > PAGE_SIZE
                or type(value.get("next_after")) is not int):
            raise ValueError()
        _, peers = federation.configuration()
        domain = peers[connection.peer_origin][1]
        cursor = after
        seen = set()
        for row in value["items"]:
            if (not isinstance(row, dict) or set(row) != {"sequence", "matrix_event_id", "matrix_user_id", "created_at"}
                    or type(row.get("sequence")) is not int or not cursor < row["sequence"] <= 2**63 - 1
                    or not isinstance(row.get("matrix_event_id"), str) or not row["matrix_event_id"].startswith("$")
                    or not 2 <= len(row["matrix_event_id"]) <= 1024 or row["matrix_event_id"] in seen
                    or not isinstance(row.get("matrix_user_id"), str)
                    or not re.fullmatch(r"@fc_[A-Za-z0-9_-]{1,128}:" + re.escape(domain), row["matrix_user_id"])
                    or not isinstance(row.get("created_at"), str) or len(row["created_at"]) > 64):
                raise ValueError()
            datetime.fromisoformat(row["created_at"])
            cursor = row["sequence"]
            seen.add(row["matrix_event_id"])
        if value["next_after"] != cursor or value["has_more"] and not value["items"]:
            raise ValueError()
    except (KeyError, ValueError, TypeError, AttributeError):
        raise HTTPException(503, "The peer did not confirm a valid current encrypted work index") from None
    return value
