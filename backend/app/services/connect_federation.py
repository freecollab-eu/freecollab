"""Pinned, signed control plane for separate installations' Matrix E2EE rooms.

Only membership metadata travels here. Each homeserver handles Matrix federation;
content encryption and device verification remain in the existing Rust client.
"""
import base64
from datetime import datetime, timedelta
from hashlib import sha256
import json
import re
import secrets
import time
from urllib.parse import quote, urlsplit

from fastapi import HTTPException
from nacl.signing import SigningKey, VerifyKey
from nacl.exceptions import BadSignatureError
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from ..config import settings
from ..models.channel import ChannelMember
from ..models.connect_federation import (ConnectFederationIdentity, ConnectFederationNonce,
    FederatedConnection, FederatedGrant, FederatedPeerMember)
from ..models.secure_channel import MatrixAccount, SecureChannel
from ..models.user import User
from ..models.workspace import WorkspaceMember
from .crypto import encrypt_message, decrypt_message
from . import remote_transport

PROTOCOL = "freecollab-connect/1"
PATH = "/connect/federation/wire/"
MAX_MEMBERS = 100
MAX_WIRE_BYTES = 128 * 1024


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def origin(value):
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.port not in (None, 443) or parsed.query or parsed.fragment
                or parsed.path not in ("", "/") or any(c.isspace() for c in value)
                or len(value) > 255):
            raise ValueError()
        normalized = "https://" + parsed.hostname.lower()
        remote_transport.syntax_url(normalized)
        if value.rstrip("/") != normalized:
            raise ValueError()
        return normalized
    except (ValueError, remote_transport.ProviderError):
        raise HTTPException(422, "Federation requires an exact public HTTPS origin") from None


def configuration():
    if not settings.connect_federation_enabled or not settings.matrix_enabled:
        raise HTTPException(503, "Cross-installation Connect is not configured")
    local = origin(settings.connect_federation_public_url)
    peers = {}
    for url, value in settings.connect_federation_peers.items():
        remote = origin(url)
        try:
            key = base64.b64decode(value["public_key"], validate=True)
            server = value["matrix_server_name"]
            if (len(key) != 32 or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,251}[a-z0-9]", server)
                    or remote == local or server == settings.matrix_server_name):
                raise ValueError()
        except (KeyError, ValueError, TypeError):
            raise HTTPException(503, "Federation peer trust configuration is invalid") from None
        peers[remote] = (key, server)
    return local, peers


def identity(session):
    row = session.get(ConnectFederationIdentity, "installation")
    if row is None:
        key = SigningKey.generate()
        row = ConnectFederationIdentity(encrypted_seed=encrypt_message(
            base64.b64encode(bytes(key)).decode(), "connect-federation-identity"))
        session.add(row)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            row = session.get(ConnectFederationIdentity, "installation")
    return SigningKey(base64.b64decode(decrypt_message(row.encrypted_seed, "connect-federation-identity")))


def public_identity(session):
    local, peers = configuration()
    return {"protocol": PROTOCOL, "origin": local,
        "public_key": base64.b64encode(bytes(identity(session).verify_key)).decode(),
        "matrix_server_name": settings.matrix_server_name,
        "trusted_origins": sorted(peers)}


def signed(session, operation, payload, *, nonce=None, audience):
    local, peers = configuration()
    if audience not in peers:
        raise HTTPException(403, "This installation is not an explicitly trusted peer")
    envelope = {"protocol": PROTOCOL, "origin": local, "audience": audience,
        "operation": operation, "timestamp": int(time.time()),
        "nonce": nonce or secrets.token_urlsafe(24), "payload": payload}
    envelope["signature"] = base64.b64encode(identity(session).sign(canonical(envelope)).signature).decode()
    return envelope


def verify(session, envelope, operation, *, consume=True, expected_origin=None, expected_nonce=None):
    local, peers = configuration()
    try:
        if (not isinstance(envelope, dict) or set(envelope) != {
                "protocol", "origin", "audience", "operation", "timestamp", "nonce", "payload", "signature"}
                or len(canonical(envelope)) > MAX_WIRE_BYTES
                or envelope["protocol"] != PROTOCOL or envelope["audience"] != local
                or envelope["operation"] != operation or type(envelope["timestamp"]) is not int
                or abs(int(time.time()) - envelope["timestamp"]) > 60
                or not isinstance(envelope["nonce"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{24,80}", envelope["nonce"])
                or not isinstance(envelope["payload"], dict)):
            raise ValueError()
        sender = envelope["origin"]
        if sender not in peers or (expected_origin and sender != expected_origin) or (expected_nonce and envelope["nonce"] != expected_nonce):
            raise ValueError()
        signature = base64.b64decode(envelope["signature"], validate=True)
        unsigned = {key: value for key, value in envelope.items() if key != "signature"}
        VerifyKey(peers[sender][0]).verify(canonical(unsigned), signature)
    except (KeyError, ValueError, TypeError, RecursionError, BadSignatureError):
        raise HTTPException(403, "The federation signature, peer or request lifetime is invalid") from None
    if consume:
        session.execute(delete(ConnectFederationNonce).where(
            ConnectFederationNonce.created_at < datetime.utcnow() - timedelta(minutes=3)))
        session.add(ConnectFederationNonce(origin=sender, nonce=envelope["nonce"]))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            raise HTTPException(409, "This signed federation request was already consumed") from None
    return sender, envelope["payload"]


def call_peer(session, peer, operation, payload):
    envelope = signed(session, operation, payload, audience=peer)
    try:
        response = remote_transport.request("POST", peer + PATH + operation,
            headers={"Content-Type": "application/json"}, body=canonical(envelope), max_bytes=MAX_WIRE_BYTES,
            timeout=45 if operation == "accept" else 15)
        remote_transport.check_response(response)
        answer = response.json()
        _, result = verify(session, answer, operation + ":response", consume=False,
            expected_origin=peer, expected_nonce=envelope["nonce"])
        return result
    except remote_transport.ProviderError:
        raise HTTPException(503, "The trusted peer could not confirm current shared-channel access") from None


def connection_for_channel(session, channel_id):
    return session.exec(select(FederatedConnection).where(FederatedConnection.channel_id == channel_id)
        .execution_options(populate_existing=True)).first()


def authority_valid(session, connection):
    from .workforce_access import principal_snapshot
    member = session.get(WorkspaceMember, (connection.workspace_id, connection.authority_user_id))
    user = session.get(User, connection.authority_user_id)
    principal = principal_snapshot(session, connection.authority_user_id)
    return bool(member and member.role in {"owner", "admin"} and user and not user.is_blocked
        and (principal is None or (principal[1] and principal[0] == connection.workspace_id)))


def grant_valid(session, connection, user_id):
    from .workforce_access import principal_snapshot
    grant = session.get(FederatedGrant, (connection.id, user_id))
    member = session.get(WorkspaceMember, (connection.workspace_id, user_id))
    user = session.get(User, user_id)
    principal = principal_snapshot(session, user_id)
    return bool(settings.connect_federation_enabled and settings.matrix_enabled
        and connection.peer_origin in settings.connect_federation_peers
        and connection.status == "active" and authority_valid(session, connection)
        and grant and grant.revoked_at is None and member and member.role in {"owner", "admin", "member", "guest"}
        and user and not user.is_blocked and (principal is None or (principal[1] and principal[0] == connection.workspace_id)))


def require_admin(session, workspace_id, connection_id, user_id):
    from .workspaces import require_workspace_admin
    require_workspace_admin(session, workspace_id, user_id)
    connection = session.get(FederatedConnection, connection_id)
    if not connection or connection.workspace_id != workspace_id:
        raise HTTPException(404, "Federated connection not found")
    return connection


def local_roster(session, connection, *, provision=False):
    from .matrix_bridge import ensure_account, service_token, revoked_devices
    members = session.exec(select(ChannelMember.user_id).where(ChannelMember.channel_id == connection.channel_id)).all()
    roster = []
    for user_id in members:
        if not grant_valid(session, connection, user_id):
            continue
        account = ensure_account(session, user_id) if provision else session.get(MatrixAccount, user_id)
        if not account or not account.provisioned:
            raise HTTPException(503, "A local encryption identity is not ready; retry synchronization")
        roster.append({"matrix_user_id": account.matrix_user_id, "name": session.get(User, user_id).name,
            "revoked_devices": revoked_devices(session, [user_id])})
    if len(roster) > MAX_MEMBERS:
        raise HTTPException(409, "Federated channel participant limit reached")
    operator, _ = service_token(session)
    return {"status": connection.status if authority_valid(session, connection) else "revoked",
        "connection_id": connection.id, "operator": operator, "members": sorted(roster, key=lambda m: m["matrix_user_id"])}


def remote_snapshot(session, connection):
    if connection.status != "active" or not authority_valid(session, connection):
        raise HTTPException(403, "Federated connection access has ended")
    result = call_peer(session, connection.peer_origin, "roster", {"connection_id": connection.id})
    _, peers = configuration()
    domain = peers[connection.peer_origin][1]
    try:
        members = result["members"]
        if (result.get("status") != "active" or result.get("connection_id") != connection.id
                or result.get("operator") != connection.peer_operator or not isinstance(members, list)
                or len(members) > MAX_MEMBERS):
            raise ValueError()
        seen = set()
        for member in members:
            matrix_id, name = member["matrix_user_id"], member["name"]
            if (not isinstance(matrix_id, str) or not re.fullmatch(r"@fc_[A-Za-z0-9_-]{1,128}:" + re.escape(domain), matrix_id)
                    or matrix_id in seen or not isinstance(name, str) or not 1 <= len(name) <= 200
                    or not isinstance(member["revoked_devices"], list) or len(member["revoked_devices"]) > 100):
                raise ValueError()
            seen.add(matrix_id)
            for device in member["revoked_devices"]:
                if (device.get("matrix_user_id") != matrix_id or not isinstance(device.get("device_id"), str)
                        or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", device["device_id"])):
                    raise ValueError()
        if not members and result.get("status") != "active":
            raise ValueError()
    except (KeyError, ValueError, TypeError, AttributeError):
        raise HTTPException(503, "The trusted peer did not confirm a valid current roster") from None
    for old in session.exec(select(FederatedPeerMember).where(FederatedPeerMember.connection_id == connection.id)).all():
        old.active = old.matrix_user_id in seen
        session.add(old)
    for member in members:
        row = session.get(FederatedPeerMember, (connection.id, member["matrix_user_id"])) or FederatedPeerMember(
            connection_id=connection.id, matrix_user_id=member["matrix_user_id"], name=member["name"])
        row.name, row.active = member["name"], True
        row.revoked_devices_json = json.dumps(member["revoked_devices"])
        session.add(row)
    connection.peer_roster_hash = sha256(canonical(result)).hexdigest()
    connection.roster_checked_at = datetime.utcnow()
    session.add(connection)
    session.commit()
    return result


def cached_peer_members(session, channel_id):
    connection = connection_for_channel(session, channel_id)
    if not connection or connection.status != "active" or not authority_valid(session, connection):
        return []
    return session.exec(select(FederatedPeerMember).where(FederatedPeerMember.connection_id == connection.id,
        FederatedPeerMember.active == True)).all()


def peer_crypto_ids(session, channel_id):
    return {row.matrix_user_id for row in cached_peer_members(session, channel_id)}


def peer_server_for_channel(session, channel_id):
    connection = connection_for_channel(session, channel_id)
    if not connection or connection.status != "active" or not authority_valid(session, connection):
        return None
    try:
        _, peers = configuration()
    except HTTPException:
        return None
    return peers.get(connection.peer_origin, (None, None))[1]


def peer_revocations(session, channel_id):
    return [device for row in cached_peer_members(session, channel_id) for device in json.loads(row.revoked_devices_json)]


def control_ids(session, connection):
    from .matrix_bridge import service_token
    operator, _ = service_token(session, allow_provision=False)
    return [operator, connection.peer_operator]


def validate_room_roster(session, connection):
    """Reject unexpected room occupants before forwarding an encrypted send."""
    from .matrix_bridge import matrix_request, service_token
    binding = session.get(SecureChannel, connection.channel_id)
    _, token = service_token(session, allow_provision=False)
    wanted = {member["matrix_user_id"] for member in local_roster(session, connection)["members"]}
    wanted.update(peer_crypto_ids(session, connection.channel_id))
    wanted.update(control_ids(session, connection))
    state = matrix_request("GET", "/_matrix/client/v3/rooms/" + quote(binding.matrix_room_id, safe="") + "/members", token=token)
    if any(event.get("state_key") not in wanted and event.get("content", {}).get("membership") in {"join", "invite"}
           for event in state.get("chunk", [])):
        raise HTTPException(409, "Unexpected encrypted room occupants; synchronize the approved roster again")


def sync_membership(session, connection):
    from .matrix_bridge import service_token, matrix_request
    from .access import lock_channel_writes
    local_roster(session, connection, provision=True)
    remote = remote_snapshot(session, connection)
    binding = session.get(SecureChannel, connection.channel_id)
    if not binding or not binding.matrix_room_id:
        raise HTTPException(409, "Federated encrypted room is not ready")
    operator, token = service_token(session)
    room = quote(binding.matrix_room_id, safe="")
    lock_channel_writes(session, connection.channel_id)
    session.expire_all()
    if connection.status != "active" or not authority_valid(session, connection):
        raise HTTPException(403, "Federated connection access has ended")
    epoch_material(session, connection)
    # Peer I/O may have overlapped a local offboarding. Derive seats again under
    # the same channel lock used by local grant/revoke operations.
    local = local_roster(session, connection)
    wanted = {m["matrix_user_id"] for m in local["members"] + remote["members"]}
    wanted.update((operator, connection.peer_operator))
    state = matrix_request("GET", f"/_matrix/client/v3/rooms/{room}/members", token=token)
    existing = {e["state_key"]: e.get("content", {}).get("membership") for e in state.get("chunk", [])}
    for matrix_id in wanted:
        if existing.get(matrix_id) not in {"join", "invite"}:
            matrix_request("POST", f"/_matrix/client/v3/rooms/{room}/invite", token=token, data={"user_id": matrix_id})
    for matrix_id, membership in existing.items():
        if matrix_id not in wanted and membership in {"join", "invite"}:
            matrix_request("POST", f"/_matrix/client/v3/rooms/{room}/kick", token=token, data={"user_id": matrix_id})


def epoch_material(session, connection):
    # A send snapshot is short-lived; an unavailable peer cannot authorize writes.
    checked = connection.roster_checked_at
    if (not checked or checked < datetime.utcnow() - timedelta(seconds=30)
            or connection.status != "active" or not authority_valid(session, connection)):
        raise HTTPException(409, "Refresh the current remote encrypted membership before sending")
    return connection.id + ":" + connection.peer_roster_hash


def revoke_local(session, connection):
    from ..models.channel import Channel
    from sqlalchemy import update
    session.execute(update(Channel).where(Channel.id == connection.channel_id).values(name=Channel.name))
    connection.status = "revoked"
    connection.revoked_at = connection.revoked_at or datetime.utcnow()
    session.add(connection)
    grants = session.exec(select(FederatedGrant).where(FederatedGrant.connection_id == connection.id)).all()
    for grant in grants:
        grant.revoked_at = grant.revoked_at or datetime.utcnow()
        session.add(grant)
        member = session.get(ChannelMember, (connection.channel_id, grant.user_id))
        if member:
            session.delete(member)
    session.commit()


def cleanup_room(session, connection):
    """Best effort remote removal follows a durable local fail-closed fence."""
    from .matrix_bridge import service_token, matrix_request
    binding = session.get(SecureChannel, connection.channel_id)
    if not binding or not binding.matrix_room_id:
        return
    operator, token = service_token(session, allow_provision=False)
    room = quote(binding.matrix_room_id, safe="")
    if connection.role == "host" and connection.peer_operator:
        levels = matrix_request("GET", f"/_matrix/client/v3/rooms/{room}/state/m.room.power_levels", token=token)
        levels.setdefault("users", {}).pop(connection.peer_operator, None)
        matrix_request("PUT", f"/_matrix/client/v3/rooms/{room}/state/m.room.power_levels", token=token, data=levels)
    state = matrix_request("GET", f"/_matrix/client/v3/rooms/{room}/members", token=token)
    pending = False
    for event in state.get("chunk", []):
        matrix_id = event.get("state_key")
        protected = {operator} if connection.role == "host" else {operator, connection.peer_operator}
        if matrix_id not in protected and event.get("content", {}).get("membership") in {"join", "invite"}:
            try:
                matrix_request("POST", f"/_matrix/client/v3/rooms/{room}/kick", token=token,
                    data={"user_id": matrix_id, "reason": "Shared connection ended"})
            except HTTPException:
                pending = True
    if pending:
        raise HTTPException(503, "Some encrypted participants still need remote cleanup")
