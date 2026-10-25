"""Authenticated opaque discovery for local and federated device-owned work."""
from hashlib import sha256

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import select

from ..database import get_session
from ..models.channel import ChannelMember
from ..models.encrypted_work import EncryptedWorkPointer
from ..models.secure_channel import MatrixAccount, SecureChannel
from ..models.user import User
from ..services.auth import get_current_user
from ..services.access import lock_channel_writes, require_channel_member
from ..services.request_guard import recheck_actor
from ..services.workspaces import channel_workspace_id, require_direct_message_writable, workspace_allows_channel

PROTOCOL = "freecollab-encrypted-work/2"
KINDS = ["collection", "feed", "decision", "linked-file", "comment", "workflow", "workflow-run", "task", "project", "organization"]
MAX_POINTERS = 5000
PAGE_SIZE = 50


def no_store(response: Response):
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(prefix="/channels/{channel_id}/encrypted-work", tags=["encrypted work"], dependencies=[Depends(no_store)])


class PointerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    matrix_event_id: str = Field(min_length=2, max_length=1024, pattern=r"^\$")


def binding_for(session, channel_id, actor):
    require_channel_member(session, channel_id, actor)
    from ..services.matrix_bridge import require_matrix
    require_matrix()
    binding = session.get(SecureChannel, channel_id)
    if not binding or binding.status != "active" or not binding.matrix_room_id:
        raise HTTPException(409, "A current active encrypted channel is required")
    account = session.get(MatrixAccount, actor)
    if not account or not account.provisioned:
        raise HTTPException(409, "Unlock this device's encryption before opening encrypted work")
    return binding, account


@router.get("/context")
def context(channel_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    binding, account = binding_for(session, channel_id, user.id)
    from ..services.connect_federation import connection_for_channel
    connection = connection_for_channel(session, channel_id)
    if connection:
        from .connect_federation import work_context
        view = work_context(channel_id, user, session)
        return {**view, "mode": "federated", "encrypted_work_protocol": PROTOCOL,
            "encrypted_work_supported": PROTOCOL in view.get("work_protocols", []), "resource_kinds": KINDS}
    rows = session.exec(select(ChannelMember).where(ChannelMember.channel_id == channel_id)).all()
    members = []
    for member in rows:
        person = session.get(User, member.user_id)
        identity = session.get(MatrixAccount, member.user_id)
        if person and not person.is_blocked and identity and identity.provisioned and workspace_allows_channel(session, channel_id, member.user_id):
            members.append({"matrix_user_id": identity.matrix_user_id, "name": person.name})
    recheck_actor(session, user.id)
    binding, account = binding_for(session, channel_id, user.id)
    server = account.matrix_user_id.split(":", 1)[1]
    return {"protocol": "freecollab-federated-work/1", "encrypted_work_protocol": PROTOCOL,
        "encrypted_work_supported": True, "work_protocols": [PROTOCOL, "freecollab-work-authority/1"], "resource_kinds": KINDS,
        "mode": "local", "room_protocols": ["freecollab-matrix-meeting/1"],
        "connection_id": sha256(("encrypted-local:" + binding.matrix_room_id).encode()).hexdigest()[:32],
        "channel_id": channel_id, "workspace_id": channel_workspace_id(session, channel_id),
        "room_id": binding.matrix_room_id, "matrix_user_id": account.matrix_user_id,
        "local_server": server, "peer_server": server, "members": members,
        "limits": {"index_events_per_installation": MAX_POINTERS, "page_size": PAGE_SIZE,
            "max_document_state_bytes": 512 * 1024}}


@router.get("/index")
def index(channel_id: str, after: int = Query(default=0, ge=0, le=2**63 - 1),
          user=Depends(get_current_user), session=Depends(get_session)):
    binding_for(session, channel_id, user.id)
    from ..services.connect_federation import connection_for_channel
    if connection_for_channel(session, channel_id):
        raise HTTPException(409, "Use the bilateral encrypted work index for a federated room")
    rows = session.exec(select(EncryptedWorkPointer).where(EncryptedWorkPointer.channel_id == channel_id,
        EncryptedWorkPointer.sequence > after).order_by(EncryptedWorkPointer.sequence).limit(PAGE_SIZE + 1)).all()
    selected = rows[:PAGE_SIZE]
    return {"items": [{"sequence": row.sequence, "matrix_event_id": row.matrix_event_id,
        "matrix_user_id": row.matrix_user_id} for row in selected],
        "next_after": selected[-1].sequence if selected else after, "has_more": len(rows) > PAGE_SIZE}


@router.post("/index", status_code=201)
def publish(channel_id: str, body: PointerInput, user=Depends(get_current_user), session=Depends(get_session)):
    binding, _ = binding_for(session, channel_id, user.id)
    room = binding.matrix_room_id
    from ..services.connect_federation import connection_for_channel
    if connection_for_channel(session, channel_id):
        raise HTTPException(409, "Use the bilateral encrypted work index for a federated room")
    from ..services.matrix_bridge import validate_secure_event
    event = validate_secure_event(session, channel_id, body.matrix_event_id, user.id)
    lock_channel_writes(session, channel_id)
    session.expire_all()
    recheck_actor(session, user.id)
    binding, account = binding_for(session, channel_id, user.id)
    require_direct_message_writable(session, channel_id, user.id)
    if binding.matrix_room_id != room or event.get("sender") != account.matrix_user_id:
        raise HTTPException(409, "Encrypted room or sender identity changed")
    previous = session.exec(select(EncryptedWorkPointer).where(EncryptedWorkPointer.channel_id == channel_id,
        EncryptedWorkPointer.matrix_event_id == body.matrix_event_id)).first()
    if previous:
        if previous.published_by != user.id or previous.matrix_user_id != event.get("sender"):
            raise HTTPException(403, "Only the encrypted event's sender may retry its pointer")
        return {"sequence": previous.sequence, "matrix_event_id": previous.matrix_event_id}
    retained = session.exec(select(EncryptedWorkPointer.sequence).where(EncryptedWorkPointer.channel_id == channel_id).limit(MAX_POINTERS)).all()
    if len(retained) >= MAX_POINTERS:
        raise HTTPException(409, "This encrypted work index reached its retained event limit")
    row = EncryptedWorkPointer(channel_id=channel_id, matrix_event_id=body.matrix_event_id,
        matrix_user_id=account.matrix_user_id, published_by=user.id)
    from ..services.encrypted_work import record_authority
    record_authority(session, channel_id, {**event, "event_id": body.matrix_event_id}, user.id)
    session.add(row); session.flush()
    from ..services.encrypted_work import notify_local
    notify_local(session, channel_id, row.sequence, user.id)
    session.commit()
    return {"sequence": row.sequence, "matrix_event_id": row.matrix_event_id}


@router.get("/authority")
def authority(channel_id: str, event_id: str = Query(min_length=2, max_length=1024, pattern=r"^\$"),
              user=Depends(get_current_user), session=Depends(get_session)):
    binding, identity = binding_for(session, channel_id, user.id)
    from ..services.encrypted_work import authority_payload, AUTHORITY_PROTOCOL
    from ..services import connect_federation as federation, federated_work
    try:
        result = authority_payload(session, channel_id, event_id, issuer=identity.matrix_user_id.split(":", 1)[1])
        return result
    except HTTPException as unavailable:
        if unavailable.status_code != 404:
            raise
    connection = federation.connection_for_channel(session, channel_id)
    if not connection:
        raise HTTPException(404, "No publication-time authority exists for this event")
    connection, binding = federated_work.require_connection(session, channel_id, user.id)
    room, peer = binding.matrix_room_id, connection.peer_origin
    snapshot = federation.remote_snapshot(session, connection)
    if AUTHORITY_PROTOCOL not in snapshot.get("work_protocols", []):
        raise HTTPException(409, "This peer has not negotiated publication-time authority receipts")
    result = federation.call_peer(session, peer, "work-authority", {"connection_id": connection.id, "event_id": event_id})
    session.expire_all(); recheck_actor(session, user.id)
    current, binding = federated_work.require_connection(session, channel_id, user.id)
    domain = federation.peer_server_for_channel(session, channel_id)
    if (current.id != connection.id or current.peer_origin != peer or binding.matrix_room_id != room
            or not isinstance(result, dict) or set(result) != {"protocol", "event_id", "room_id", "matrix_user_id", "role", "issuer", "published_at"}
            or result.get("protocol") != AUTHORITY_PROTOCOL or result.get("event_id") != event_id
            or result.get("room_id") != room or result.get("issuer") != domain
            or result.get("role") not in {"admin", "member"} or not isinstance(result.get("matrix_user_id"), str)
            or not result["matrix_user_id"].endswith(":" + str(domain)) or not isinstance(result.get("published_at"), str)):
        raise HTTPException(503, "The peer did not confirm matching encrypted event authority")
    return result
