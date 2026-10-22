"""Bilateral owner-controlled channels across separately operated installations."""
from datetime import datetime, timedelta
from hashlib import sha256
import re
import secrets
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import select
from sqlalchemy import update

from ..config import settings
from ..database import get_session
from ..models.channel import Channel, ChannelMember
from ..models.connect_federation import FederatedConnection, FederatedGrant
from ..models.secure_channel import SecureChannel
from ..models.user import User
from ..models.workspace import WorkspaceChannel, WorkspaceMember
from ..services import connect_federation as federation
from ..services import federated_work
from ..models.federated_work import FederatedWorkPointer
from ..services.auth import get_current_user
from ..services.workspaces import require_workspace_admin, lock_workspace
from ..services.request_guard import recheck_actor
from ..services.workforce_access import require_principal_workspace
from ..services.matrix_bridge import (create_secure_room, service_token, matrix_request,
    remove_secure_member)

router = APIRouter(prefix="/connect/federation", tags=["cross-installation shared channels"])


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Offer(Strict):
    name: str = Field(min_length=1, max_length=100)
    peer_origin: str = Field(min_length=8, max_length=255)
    peer_workspace_id: str = Field(min_length=1, max_length=100)


class Acceptance(Strict):
    peer_origin: str = Field(min_length=8, max_length=255)
    invitation_token: str = Field(min_length=32, max_length=128)


class Member(Strict):
    user_id: str = Field(min_length=1, max_length=128)


class WorkPointer(Strict):
    matrix_event_id: str = Field(min_length=2, max_length=1024)


def data(session, connection):
    channel = session.get(Channel, connection.channel_id)
    binding = session.get(SecureChannel, connection.channel_id)
    return {"id": connection.id, "channel_id": connection.channel_id, "workspace_id": connection.workspace_id,
        "name": channel.name if channel else "Shared channel", "peer_origin": connection.peer_origin,
        "peer_workspace_id": connection.peer_workspace_id, "role": connection.role,
        "status": connection.status, "created_at": connection.created_at,
        "expires_at": connection.invitation_expires_at, "revoked_at": connection.revoked_at,
        "secure": True, "transport": "federated-matrix", "room_ready": bool(binding and binding.status == "active")}


def lock_admin(session, workspace_id, actor_id):
    require_workspace_admin(session, workspace_id, actor_id)
    lock_workspace(session, workspace_id)
    session.expire_all()
    recheck_actor(session, actor_id)
    require_workspace_admin(session, workspace_id, actor_id)


@router.get("/workspaces/{workspace_id}/configuration")
def configuration(workspace_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    require_workspace_admin(session, workspace_id, user.id)
    try:
        identity = federation.public_identity(session)
        return {"enabled": True, **identity}
    except HTTPException:
        return {"enabled": False, "protocol": federation.PROTOCOL, "origin": "", "public_key": "",
            "matrix_server_name": "", "trusted_origins": [],
            "requirement": "The operator must configure HTTPS origins, pinned installation keys and federated Matrix homeservers."}


@router.get("/workspaces/{workspace_id}/connections")
def listing(workspace_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    require_workspace_admin(session, workspace_id, user.id)
    return [data(session, connection) for connection in session.exec(select(FederatedConnection)
        .where(FederatedConnection.workspace_id == workspace_id)
        .order_by(FederatedConnection.created_at.desc()).limit(100)).all()]


@router.post("/workspaces/{workspace_id}/connections", status_code=201)
def offer(workspace_id: str, body: Offer, response: Response, user=Depends(get_current_user), session=Depends(get_session)):
    _, peers = federation.configuration()
    peer = federation.origin(body.peer_origin)
    if peer not in peers:
        raise HTTPException(403, "An operator must explicitly pin this peer installation first")
    if not body.name.strip() or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", body.peer_workspace_id):
        raise HTTPException(422, "A channel name and exact partner workspace identifier are required")
    # Provision the installation identity before entering the authority transaction.
    federation.identity(session)
    lock_admin(session, workspace_id, user.id)
    active = session.exec(select(FederatedConnection.id).where(FederatedConnection.workspace_id == workspace_id,
        FederatedConnection.status.in_(["pending", "active"])).limit(100)).all()
    if len(active) >= 100:
        raise HTTPException(409, "Revoke an existing federated connection before adding another")
    channel = Channel(id=uuid4().hex, name=body.name.strip(), type="group")
    session.add(channel)
    session.flush()
    session.add(WorkspaceChannel(channel_id=channel.id, workspace_id=workspace_id))
    binding = SecureChannel(channel_id=channel.id, created_by=user.id)
    session.add(binding)
    token = secrets.token_urlsafe(32)
    connection = FederatedConnection(id=uuid4().hex, channel_id=channel.id, workspace_id=workspace_id,
        peer_origin=peer, peer_workspace_id=body.peer_workspace_id, role="host", authority_user_id=user.id,
        invitation_hash=sha256(token.encode()).hexdigest(), invitation_expires_at=datetime.utcnow() + timedelta(hours=24))
    session.add(connection)
    session.commit()  # The plaintext fence is durable before provisioning.
    try:
        create_secure_room(session, binding)
        session.commit()
    except HTTPException:
        # There is no usable invitation until the host's E2EE room exists.
        federation.revoke_local(session, connection)
        raise
    response.headers["Cache-Control"] = "no-store"
    return {**data(session, connection), "invitation_token": token}


def operator_valid(value, server):
    return isinstance(value, str) and re.fullmatch(r"@freecollab_operator_[a-f0-9]{16}:" + re.escape(server), value)


def handle_wire(session, operation, peer, payload):
    _, peers = federation.configuration()
    if operation == "accept":
        if set(payload) != {"invitation_token", "workspace_id", "operator"}:
            raise HTTPException(422, "Invalid federation acceptance")
        token = payload.get("invitation_token")
        if not isinstance(token, str) or not 32 <= len(token) <= 128:
            raise HTTPException(422, "Invalid federation acceptance")
        connection = session.exec(select(FederatedConnection).where(
            FederatedConnection.invitation_hash == sha256(token.encode()).hexdigest(),
            FederatedConnection.peer_origin == peer, FederatedConnection.role == "host")).first()
        if (not connection or connection.status not in {"pending", "active"}
                or connection.invitation_expires_at <= datetime.utcnow()
                or connection.peer_workspace_id != payload["workspace_id"]
                or not federation.authority_valid(session, connection)
                or not operator_valid(payload["operator"], peers[peer][1])
                or (connection.peer_operator and connection.peer_operator != payload["operator"])):
            raise HTTPException(404, "Invitation is unavailable or expired")
        binding = session.get(SecureChannel, connection.channel_id)
        if not binding or binding.status != "active" or not binding.matrix_room_id:
            raise HTTPException(503, "The source encrypted room is not ready")
        operator, token = service_token(session)
        room = quote(binding.matrix_room_id, safe="")
        state = matrix_request("GET", f"/_matrix/client/v3/rooms/{room}/members", token=token)
        if not any(e.get("state_key") == payload["operator"] and e.get("content", {}).get("membership") in {"invite", "join"}
                for e in state.get("chunk", [])):
            matrix_request("POST", f"/_matrix/client/v3/rooms/{room}/invite", token=token,
                data={"user_id": payload["operator"]})
        # Both customer operators control their own seats. Ordinary participants
        # retain zero power to invite or change room security.
        levels = matrix_request("GET", f"/_matrix/client/v3/rooms/{room}/state/m.room.power_levels", token=token)
        # Room v12 creators have implicit infinite power and MUST NOT appear in
        # content.users. The invited control account receives explicit power100.
        levels["users"] = {payload["operator"]: 100}
        matrix_request("PUT", f"/_matrix/client/v3/rooms/{room}/state/m.room.power_levels", token=token, data=levels)
        lock_workspace(session, connection.workspace_id)
        session.expire_all()
        session.refresh(connection)
        if connection.status not in {"pending", "active"} or not federation.authority_valid(session, connection):
            federation.revoke_local(session, connection)
            try:
                federation.cleanup_room(session, connection)
            except HTTPException:
                pass
            raise HTTPException(403, "The source connection authority ended")
        accepted = session.execute(update(FederatedConnection).where(
            FederatedConnection.id == connection.id, FederatedConnection.status.in_(["pending", "active"]),
            FederatedConnection.invitation_expires_at > datetime.utcnow(),
        ).values(status="active", peer_operator=payload["operator"]))
        if accepted.rowcount != 1:
            session.rollback()
            federation.revoke_local(session, connection)
            try:
                federation.cleanup_room(session, connection)
            except HTTPException:
                pass
            raise HTTPException(403, "The source connection authority ended")
        session.commit()
        return {"connection_id": connection.id, "workspace_id": connection.workspace_id,
            "name": session.get(Channel, connection.channel_id).name,
            "room_id": binding.matrix_room_id, "operator": operator,
            "expires_at": connection.invitation_expires_at.isoformat()}
    if operation == "work-index":
        if set(payload) != {"connection_id", "after", "limit"} or not isinstance(payload["connection_id"], str):
            raise HTTPException(422, "Invalid encrypted work index request")
        connection = session.get(FederatedConnection, payload["connection_id"])
        if not connection or connection.peer_origin != peer:
            raise HTTPException(404, "Federated connection not found")
        return federated_work.page(session, connection, payload["after"], payload["limit"])
    if set(payload) != {"connection_id"} or not isinstance(payload["connection_id"], str):
        raise HTTPException(422, "Invalid federation connection request")
    connection = session.get(FederatedConnection, payload["connection_id"])
    if not connection or connection.peer_origin != peer:
        raise HTTPException(404, "Federated connection not found")
    if operation == "roster":
        return federation.local_roster(session, connection)
    if operation == "revoke":
        federation.revoke_local(session, connection)
        try:
            federation.cleanup_room(session, connection)
        except HTTPException:
            pass
        return {"status": "revoked", "connection_id": connection.id}
    raise HTTPException(404, "Unsupported federation operation")


@router.post("/wire/{operation}")
async def wire(operation: str, request: Request, response: Response, session=Depends(get_session)):
    if operation not in {"accept", "roster", "revoke", "work-index"}:
        raise HTTPException(404, "Unsupported federation operation")
    raw = bytearray()
    async for block in request.stream():
        raw.extend(block)
        if len(raw) > federation.MAX_WIRE_BYTES:
            raise HTTPException(413, "Federation envelope is too large")
    import json
    from ..services.oidc_transport import _pairs, _constant
    try:
        envelope = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)
    except (ValueError, TypeError, RecursionError):
        raise HTTPException(422, "Invalid federation envelope") from None
    peer, payload = federation.verify(session, envelope, operation)
    # Blocking transport/Matrix calls belong in the worker pool, not the event loop.
    from starlette.concurrency import run_in_threadpool
    result = await run_in_threadpool(handle_wire, session, operation, peer, payload)
    response.headers["Cache-Control"] = "no-store"
    return federation.signed(session, operation + ":response", result, audience=peer, nonce=envelope["nonce"])


@router.post("/workspaces/{workspace_id}/accept", status_code=201)
def accept(workspace_id: str, body: Acceptance, user=Depends(get_current_user), session=Depends(get_session)):
    require_workspace_admin(session, workspace_id, user.id)
    _, peers = federation.configuration()
    peer = federation.origin(body.peer_origin)
    if peer not in peers:
        raise HTTPException(403, "An operator must explicitly pin this peer installation first")
    operator, token = service_token(session)
    result = federation.call_peer(session, peer, "accept", {
        "invitation_token": body.invitation_token, "workspace_id": workspace_id, "operator": operator})
    try:
        room_id = result["room_id"]
        if (not re.fullmatch(r"[a-f0-9]{32}", result["connection_id"])
                or not isinstance(room_id, str) or not re.fullmatch(r"![A-Za-z0-9._~-]{1,255}(?::" + re.escape(peers[peer][1]) + r")?", room_id)
                or not operator_valid(result["operator"], peers[peer][1])
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", result["workspace_id"])
                or not isinstance(result["name"], str) or not 1 <= len(result["name"].strip()) <= 100):
            raise ValueError()
        expiry = datetime.fromisoformat(result["expires_at"])
    except (ValueError, TypeError, KeyError):
        raise HTTPException(503, "The peer returned invalid encrypted-room metadata") from None
    lock_admin(session, workspace_id, user.id)
    existing = session.get(FederatedConnection, result["connection_id"])
    if existing:
        if existing.workspace_id != workspace_id or existing.peer_origin != peer or existing.status == "revoked":
            raise HTTPException(409, "This connection cannot be accepted again")
        connection = existing
        binding = session.get(SecureChannel, connection.channel_id)
    else:
        channel = Channel(id=uuid4().hex, name=result["name"].strip(), type="group")
        session.add(channel)
        session.flush()
        session.add(WorkspaceChannel(channel_id=channel.id, workspace_id=workspace_id))
        binding = SecureChannel(channel_id=channel.id, created_by=user.id, matrix_room_id=room_id, status="preparing")
        session.add(binding)
        connection = FederatedConnection(id=result["connection_id"], channel_id=channel.id,
            workspace_id=workspace_id, peer_origin=peer, peer_workspace_id=result["workspace_id"], role="guest",
            authority_user_id=user.id, status="active", invitation_expires_at=expiry, peer_operator=result["operator"])
        session.add(connection)
        session.commit()
    room = quote(room_id, safe="")
    matrix_request("POST", f"/_matrix/client/v3/join/{room}", token=token, data={})
    encryption = matrix_request("GET", f"/_matrix/client/v3/rooms/{room}/state/m.room.encryption", token=token)
    history = matrix_request("GET", f"/_matrix/client/v3/rooms/{room}/state/m.room.history_visibility", token=token)
    rules = matrix_request("GET", f"/_matrix/client/v3/rooms/{room}/state/m.room.join_rules", token=token)
    guests = matrix_request("GET", f"/_matrix/client/v3/rooms/{room}/state/m.room.guest_access", token=token)
    levels = matrix_request("GET", f"/_matrix/client/v3/rooms/{room}/state/m.room.power_levels", token=token)
    creation = matrix_request("GET", f"/_matrix/client/v3/rooms/{room}/state/m.room.create", token=token)
    users = levels.get("users", {})
    if (encryption.get("algorithm") != "m.megolm.v1.aes-sha2" or history.get("history_visibility") != "joined"
            or rules.get("join_rule") != "invite" or guests.get("guest_access") != "forbidden"
            or levels.get("users_default", 0) != 0 or levels.get("events_default", 0) != 0
            or any(levels.get(field) != 100 for field in ("state_default", "invite", "kick", "ban"))
            or creation.get("room_version") != "12" or creation.get("additional_creators", []) != []
            or users.get(operator) != 100 or result["operator"] in users
            or any(value != 0 for identifier, value in users.items() if identifier not in {operator, result["operator"]})):
        federation.revoke_local(session, connection)
        try:
            federation.call_peer(session, connection.peer_origin, "revoke", {"connection_id": connection.id})
        except HTTPException:
            pass
        raise HTTPException(409, "The peer room does not satisfy the required encryption policy")
    binding.status = "active"
    session.add(binding)
    session.commit()
    return data(session, connection)


@router.get("/workspaces/{workspace_id}/connections/{connection_id}/members")
def members(workspace_id: str, connection_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    federation.require_admin(session, workspace_id, connection_id, user.id)
    rows = session.exec(select(FederatedGrant, User.name).join(User, User.id == FederatedGrant.user_id)
        .where(FederatedGrant.connection_id == connection_id)).all()
    return [{"user_id": grant.user_id, "name": name, "active": grant.revoked_at is None} for grant, name in rows]


@router.post("/workspaces/{workspace_id}/connections/{connection_id}/members", status_code=201)
def grant(workspace_id: str, connection_id: str, body: Member, user=Depends(get_current_user), session=Depends(get_session)):
    federation.configuration()
    lock_admin(session, workspace_id, user.id)
    connection = federation.require_admin(session, workspace_id, connection_id, user.id)
    from ..services.access import lock_channel_writes
    lock_channel_writes(session, connection.channel_id)
    session.expire_all()
    if connection.status != "active" or not federation.authority_valid(session, connection):
        raise HTTPException(409, "Both installations must accept the connection before granting access")
    require_principal_workspace(session, body.user_id, workspace_id)
    member, target = session.get(WorkspaceMember, (workspace_id, body.user_id)), session.get(User, body.user_id)
    if not member or member.role not in {"owner", "admin", "member", "guest"} or not target or target.is_blocked:
        raise HTTPException(404, "An active member of your own organization is required")
    count = session.exec(select(FederatedGrant.user_id).where(FederatedGrant.connection_id == connection_id,
        FederatedGrant.revoked_at == None).limit(federation.MAX_MEMBERS)).all()
    if body.user_id not in count and len(count) >= federation.MAX_MEMBERS:
        raise HTTPException(409, "Federated channel participant limit reached")
    item = session.get(FederatedGrant, (connection_id, body.user_id)) or FederatedGrant(
        connection_id=connection_id, user_id=body.user_id, created_by=user.id)
    item.revoked_at = None
    session.add(item)
    if not session.get(ChannelMember, (connection.channel_id, body.user_id)):
        session.add(ChannelMember(channel_id=connection.channel_id, user_id=body.user_id, role="member"))
    session.commit()
    # Account provisioning occurs on this customer's side, never on the peer.
    from ..services.matrix_bridge import ensure_account
    ensure_account(session, body.user_id)
    return {"user_id": body.user_id, "name": target.name, "active": True}


@router.delete("/workspaces/{workspace_id}/connections/{connection_id}/members/{member_id}")
def remove(workspace_id: str, connection_id: str, member_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    lock_admin(session, workspace_id, user.id)
    connection = federation.require_admin(session, workspace_id, connection_id, user.id)
    from ..services.access import lock_channel_writes
    lock_channel_writes(session, connection.channel_id)
    session.expire_all()
    grant = session.get(FederatedGrant, (connection_id, member_id))
    if not grant:
        raise HTTPException(404, "Participant not found")
    grant.revoked_at = datetime.utcnow()
    session.add(grant)
    member = session.get(ChannelMember, (connection.channel_id, member_id))
    if member:
        session.delete(member)
    session.commit()
    pending = False
    try:
        remove_secure_member(session, connection.channel_id, member_id)
    except HTTPException:
        pending = True
    return {"active": False, "secure_sync_pending": pending}


@router.post("/workspaces/{workspace_id}/connections/{connection_id}/sync")
def synchronize(workspace_id: str, connection_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    connection = federation.require_admin(session, workspace_id, connection_id, user.id)
    federation.sync_membership(session, connection)
    session.commit()
    return data(session, connection)


@router.delete("/workspaces/{workspace_id}/connections/{connection_id}")
def revoke(workspace_id: str, connection_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    lock_admin(session, workspace_id, user.id)
    connection = federation.require_admin(session, workspace_id, connection_id, user.id)
    federation.revoke_local(session, connection)
    pending = False
    try:
        federation.call_peer(session, connection.peer_origin, "revoke", {"connection_id": connection.id})
    except HTTPException:
        pending = True
    try:
        federation.cleanup_room(session, connection)
    except HTTPException:
        pending = True
    return {**data(session, connection), "remote_cleanup_pending": pending}


@router.get("/channels/{channel_id}/work-context")
def work_context(channel_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    connection, binding = federated_work.require_connection(session, channel_id, user.id)
    federation.local_roster(session, connection, provision=True)
    remote = federation.remote_snapshot(session, connection)
    session.expire_all()
    recheck_actor(session, user.id)
    connection, binding = federated_work.require_connection(session, channel_id, user.id)
    local = federation.local_roster(session, connection)
    from ..models.secure_channel import MatrixAccount
    account = session.get(MatrixAccount, user.id)
    if not account or not account.provisioned:
        raise HTTPException(409, "Start this device's encrypted messaging before opening shared work")
    return {"protocol": "freecollab-federated-work/1", "connection_id": connection.id,
        "channel_id": channel_id, "workspace_id": connection.workspace_id, "room_id": binding.matrix_room_id,
        "matrix_user_id": account.matrix_user_id, "local_server": account.matrix_user_id.split(":", 1)[1],
        "peer_server": federation.peer_server_for_channel(session, channel_id),
        "members": [{"matrix_user_id": member["matrix_user_id"], "name": member["name"]}
            for member in local["members"] + remote["members"]],
        "limits": {"index_events_per_installation": federated_work.MAX_POINTERS,
            "page_size": federated_work.PAGE_SIZE, "max_document_state_bytes": 512 * 1024}}


@router.get("/channels/{channel_id}/work-index")
def work_index(channel_id: str, local_after: int = Query(default=0, ge=0, le=2**63 - 1),
               remote_after: int = Query(default=0, ge=0, le=2**63 - 1),
               user=Depends(get_current_user), session=Depends(get_session)):
    connection, _ = federated_work.require_connection(session, channel_id, user.id)
    remote = federation.call_peer(session, connection.peer_origin, "work-index", {
        "connection_id": connection.id, "after": remote_after, "limit": federated_work.PAGE_SIZE})
    remote = federated_work.validate_remote_page(remote, connection, remote_after)
    session.expire_all()
    recheck_actor(session, user.id)
    connection, _ = federated_work.require_connection(session, channel_id, user.id)
    return {"connection_id": connection.id,
        "local": federated_work.page(session, connection, local_after), "remote": remote}


@router.post("/channels/{channel_id}/work-index", status_code=201)
def publish_work_pointer(channel_id: str, body: WorkPointer,
                         user=Depends(get_current_user), session=Depends(get_session)):
    connection, _ = federated_work.require_connection(session, channel_id, user.id)
    from ..services.matrix_bridge import validate_secure_event
    event = validate_secure_event(session, channel_id, body.matrix_event_id, user.id)
    from ..services.access import lock_channel_writes
    lock_channel_writes(session, channel_id)
    session.expire_all()
    recheck_actor(session, user.id)
    connection, _ = federated_work.require_connection(session, channel_id, user.id)
    existing = session.exec(select(FederatedWorkPointer).where(
        FederatedWorkPointer.connection_id == connection.id,
        FederatedWorkPointer.matrix_event_id == body.matrix_event_id)).first()
    if existing:
        if existing.published_by != user.id or existing.matrix_user_id != event.get("sender"):
            raise HTTPException(403, "Only the original event sender may retry its work pointer")
        return {"sequence": existing.sequence, "matrix_event_id": existing.matrix_event_id}
    quota = session.exec(select(FederatedWorkPointer.sequence).where(
        FederatedWorkPointer.connection_id == connection.id).limit(federated_work.MAX_POINTERS)).all()
    if len(quota) >= federated_work.MAX_POINTERS:
        raise HTTPException(409, "This installation's encrypted work index reached its supported event limit")
    row = FederatedWorkPointer(connection_id=connection.id, matrix_event_id=body.matrix_event_id,
        matrix_user_id=event["sender"], published_by=user.id)
    session.add(row)
    session.commit()
    return {"sequence": row.sequence, "matrix_event_id": row.matrix_event_id}
