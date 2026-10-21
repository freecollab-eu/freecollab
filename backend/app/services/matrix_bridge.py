"""Private Matrix control plane. No message plaintext or content keys enter here."""
from datetime import datetime
from hashlib import sha1
import hmac
import secrets
from urllib.parse import quote

import httpx
from fastapi import HTTPException
from sqlmodel import select
from ..config import settings
from ..models.channel import ChannelMember
from ..models.secure_channel import SecureChannel, MatrixAccount, MatrixServiceAccount
from .crypto import encrypt_message, decrypt_message
from .workspaces import workspace_allows_channel, users_blocked
from ..models.user import User
from ..models.channel import Channel

_service_tokens = {}


def is_secure_channel(session, channel_id: str) -> bool:
    """Preparing rooms also reject native plaintext writes (fail closed)."""
    return session.get(SecureChannel, channel_id) is not None


def require_matrix():
    if not getattr(settings, "matrix_enabled", False):
        raise HTTPException(503, "Encrypted messaging is not configured")


def matrix_request(method, path, *, token=None, data=None):
    require_matrix()
    headers = {"Authorization": "Bearer " + token} if token else {}
    try:
        # The private service URL is configured by the operator, never by clients.
        with httpx.Client(timeout=40, trust_env=False) as client:
            response = client.request(method, settings.matrix_internal_url.rstrip("/") + path, json=data, headers=headers)
        if response.status_code >= 400:
            raise HTTPException(503, "Encrypted messaging service rejected the operation")
        return response.json() if response.content else {}
    except httpx.HTTPError:
        raise HTTPException(503, "Encrypted messaging service is unavailable") from None


def _register(localpart, password):
    nonce = matrix_request("GET", "/_synapse/admin/v1/register")["nonce"]
    secret = settings.matrix_registration_shared_secret.get_secret_value()
    mac = hmac.new(secret.encode(), "\0".join((nonce, localpart, password, "notadmin")).encode(), sha1).hexdigest()
    return matrix_request("POST", "/_synapse/admin/v1/register", data={
        "nonce": nonce, "username": localpart, "password": password,
        "admin": False, "mac": mac, "inhibit_login": True,
    })


def ensure_account(session, user_id):
    require_matrix()
    row = session.get(MatrixAccount, user_id)
    if row is None:
        password = secrets.token_urlsafe(48)
        localpart = "fc_" + user_id
        row = MatrixAccount(user_id=user_id, matrix_user_id="@" + localpart + ":" + settings.matrix_server_name,
                            encrypted_password=encrypt_message(password, "matrix-auth:" + user_id))
        session.add(row)
        # Persist random credentials before remote registration. A timeout or later
        # rollback can then be retried without orphaning an inaccessible identity.
        session.commit()
    if not row.provisioned:
        password = decrypt_message(row.encrypted_password, "matrix-auth:" + user_id)
        try:
            _register(row.matrix_user_id.split(":", 1)[0][1:], password)
        except HTTPException:
            login_account(row)  # A previous remote success may have timed out.
        row.provisioned = True
        session.add(row)
        session.commit()
    return row


def login_account(row, device_id=None, display_name="FreeCollab browser"):
    context = "matrix-auth:" + row.user_id if isinstance(row, MatrixAccount) else "matrix-service"
    data = {"type": "m.login.password", "identifier": {"type": "m.id.user", "user": row.matrix_user_id},
            "password": decrypt_message(row.encrypted_password, context), "initial_device_display_name": display_name}
    if device_id:
        data["device_id"] = device_id
    return matrix_request("POST", "/_matrix/client/v3/login", data=data)


def service_token(session, *, allow_provision=True):
    row = session.get(MatrixServiceAccount, "operator")
    if (row is None or not row.provisioned) and not allow_provision:
        raise HTTPException(503, "Encrypted membership control is not ready")
    if row is None:
        password = secrets.token_urlsafe(48)
        matrix_id = "@freecollab_operator_" + secrets.token_hex(8) + ":" + settings.matrix_server_name
        row = MatrixServiceAccount(matrix_user_id=matrix_id, encrypted_password=encrypt_message(password, "matrix-service"))
        session.add(row)
        session.commit()
    if not row.provisioned:
        try:
            _register(row.matrix_user_id.split(":", 1)[0][1:], decrypt_message(row.encrypted_password, "matrix-service"))
        except HTTPException:
            login_account(row, device_id="FREECOLLAB_CONTROL")
        row.provisioned = True
        session.add(row)
        session.commit()
    cached = _service_tokens.get(row.matrix_user_id)
    if cached and (datetime.utcnow() - cached[0]).total_seconds() < 3600:
        return row.matrix_user_id, cached[1]
    auth = login_account(row, device_id="FREECOLLAB_CONTROL")
    _service_tokens[row.matrix_user_id] = (datetime.utcnow(), auth["access_token"])
    return row.matrix_user_id, auth["access_token"]


def create_secure_room(session, binding):
    members = session.exec(select(ChannelMember).where(ChannelMember.channel_id == binding.channel_id)).all()
    accounts = [ensure_account(session, m.user_id) for m in members if workspace_allows_channel(session, binding.channel_id, m.user_id) and (u := session.get(User, m.user_id)) and not u.is_blocked]
    operator, token = service_token(session)
    room = matrix_request("POST", "/_matrix/client/v3/createRoom", token=token, data={
        "visibility": "private", "preset": "private_chat", "room_version": "12",
        "invite": [a.matrix_user_id for a in accounts],
        "initial_state": [
            {"type": "m.room.encryption", "state_key": "", "content": {
                "algorithm": "m.megolm.v1.aes-sha2", "rotation_period_msgs": 1, "rotation_period_ms": 3600000}},
            {"type": "m.room.history_visibility", "state_key": "", "content": {"history_visibility": "joined"}},
            {"type": "m.room.guest_access", "state_key": "", "content": {"guest_access": "forbidden"}},
            {"type": "m.room.join_rules", "state_key": "", "content": {"join_rule": "invite"}},
            {"type": "m.room.power_levels", "state_key": "", "content": {
                "users": {}, "users_default": 0, "events_default": 0,
                "state_default": 100, "invite": 100, "kick": 100, "ban": 100, "redact": 100,
                "events": {"m.room.encryption": 100, "m.room.history_visibility": 100}}},
        ],
    })
    binding.matrix_room_id = room["room_id"]
    binding.status = "active"
    session.add(binding)
    session.flush()
    return binding


def sync_secure_membership(session, channel_id):
    binding = session.get(SecureChannel, channel_id)
    if not binding or not binding.matrix_room_id:
        return
    operator, token = service_token(session)
    members = session.exec(select(ChannelMember).where(ChannelMember.channel_id == channel_id)).all()
    for member in members:
        if workspace_allows_channel(session, channel_id, member.user_id) and (u := session.get(User, member.user_id)) and not u.is_blocked:
            ensure_account(session, member.user_id)
    from .workspaces import channel_workspace_id, lock_workspace
    from .access import lock_channel_writes
    workspace_id = channel_workspace_id(session, channel_id)
    if workspace_id:
        lock_workspace(session, workspace_id)
    lock_channel_writes(session, channel_id)
    session.expire_all()
    members = session.exec(select(ChannelMember).where(ChannelMember.channel_id == channel_id)).all()
    if any(not session.get(MatrixAccount, member.user_id) or not session.get(MatrixAccount, member.user_id).provisioned for member in members if workspace_allows_channel(session, channel_id, member.user_id) and (u := session.get(User, member.user_id)) and not u.is_blocked):
        raise HTTPException(503, "New encrypted membership is pending; retry synchronization")
    wanted = {session.get(MatrixAccount, m.user_id).matrix_user_id for m in members
              if workspace_allows_channel(session, channel_id, m.user_id)
              and (u := session.get(User, m.user_id)) and not u.is_blocked}
    channel = session.get(Channel, channel_id)
    if channel and channel.type == "direct" and len(members) == 2 and users_blocked(session, members[0].user_id, members[1].user_id):
        wanted = set()
    room = quote(binding.matrix_room_id, safe="")
    state = matrix_request("GET", f"/_matrix/client/v3/rooms/{room}/members", token=token)
    existing = {e["state_key"]: e.get("content", {}).get("membership") for e in state.get("chunk", [])}
    for matrix_id in wanted:
        if existing.get(matrix_id) not in ("join", "invite"):
            matrix_request("POST", f"/_matrix/client/v3/rooms/{room}/invite", token=token, data={"user_id": matrix_id})
    for matrix_id, membership in existing.items():
        if matrix_id != operator and matrix_id not in wanted and membership in ("join", "invite"):
            matrix_request("POST", f"/_matrix/client/v3/rooms/{room}/kick", token=token, data={"user_id": matrix_id})


def remove_secure_member(session, channel_id, user_id):
    binding = session.get(SecureChannel, channel_id)
    account = session.get(MatrixAccount, user_id)
    if not binding or not binding.matrix_room_id or not account:
        return
    _, token = service_token(session, allow_provision=False)
    room = quote(binding.matrix_room_id, safe='')
    current = matrix_request("GET", f"/_matrix/client/v3/rooms/{room}/members", token=token)
    if not any(event.get("state_key") == account.matrix_user_id and event.get("content", {}).get("membership") in ("join", "invite") for event in current.get("chunk", [])):
        return
    matrix_request("POST", f"/_matrix/client/v3/rooms/{room}/kick",
                   token=token, data={"user_id": account.matrix_user_id, "reason": "Channel membership ended"})


def validate_secure_event(session, channel_id, event_id, user_id, *, require_sender=True):
    """Validate an opaque event pointer without decrypting its content."""
    from .access import require_channel_member
    require_channel_member(session, channel_id, user_id)
    binding = session.get(SecureChannel, channel_id)
    account = session.get(MatrixAccount, user_id)
    if not binding or not binding.matrix_room_id or binding.status != "active" or not account:
        raise HTTPException(409, "Encrypted channel is not ready")
    _, token = service_token(session, allow_provision=False)
    event = matrix_request("GET", f"/_matrix/client/v3/rooms/{quote(binding.matrix_room_id, safe='')}/event/{quote(event_id, safe='')}", token=token)
    if event.get("room_id") != binding.matrix_room_id or event.get("type") != "m.room.encrypted" or (require_sender and event.get("sender") != account.matrix_user_id) or event.get("unsigned", {}).get("redacted_because") or not event.get("content", {}).get("ciphertext"):
        raise HTTPException(400, "Encrypted event does not belong to this sender and channel")
    return event


def membership_epoch(session, channel_id):
    """Opaque membership snapshot used to reject sends prepared before removal."""
    from hashlib import sha256
    members = session.exec(select(ChannelMember).where(ChannelMember.channel_id == channel_id)).all()
    active = sorted(member.user_id for member in members if workspace_allows_channel(session, channel_id, member.user_id)
                    and (user := session.get(User, member.user_id)) and not user.is_blocked)
    revocations = [row["user_id"] + ":" + row["device_id"] for row in revoked_devices(session, active)]
    return sha256((channel_id + "\0" + "\0".join(active) + "\0revoked\0" + "\0".join(revocations)).encode()).hexdigest()


def lock_device_accounts(session, user_ids):
    """Serialize key-recipient snapshots with device removal, in a fixed order."""
    from sqlalchemy import update
    for user_id in sorted(set(user_ids)):
        result = session.execute(update(MatrixAccount).where(MatrixAccount.user_id == user_id)
                                 .values(encrypted_password=MatrixAccount.encrypted_password))
        if result.rowcount != 1:
            raise HTTPException(409, "Encrypted device identity is not ready")


def revoked_devices(session, user_ids):
    from ..models.secure_channel import MatrixDeviceRevocation
    rows = session.exec(select(MatrixDeviceRevocation, MatrixAccount.matrix_user_id).join(
        MatrixAccount, MatrixAccount.user_id == MatrixDeviceRevocation.user_id,
    ).where(MatrixDeviceRevocation.user_id.in_(set(user_ids))).order_by(
        MatrixDeviceRevocation.user_id, MatrixDeviceRevocation.device_id,
    )).all()
    return [{"user_id": row.user_id, "matrix_user_id": matrix_id, "device_id": row.device_id} for row, matrix_id in rows]


def delete_owned_device(account, access_token, device_id):
    """Use Matrix UIA to delete one device, including uncertain-success retries.

    A retry after deleting the acting device may have an invalid old token. A
    temporary keyless management login can confirm absence; only that newly
    created management device is cleaned up afterward.
    """
    require_matrix()
    password = decrypt_message(account.encrypted_password, "matrix-auth:" + account.user_id)
    auth = {"type": "m.login.password", "identifier": {"type": "m.id.user", "user": account.matrix_user_id}, "password": password}
    base = settings.matrix_internal_url.rstrip("/") + "/_matrix/client/v3/devices/"
    temporary = None
    try:
        with httpx.Client(timeout=40, trust_env=False) as client:
            def delete(token, target):
                headers = {"Authorization": "Bearer " + token}
                response = client.get(base + quote(target, safe=""), headers=headers)
                if response.status_code == 404:
                    return response
                if response.status_code != 200:
                    return response
                response = client.request("DELETE", base + quote(target, safe=""), headers=headers, json={})
                if response.status_code == 401 and response.json().get("session"):
                    response = client.request("DELETE", base + quote(target, safe=""), headers=headers,
                                              json={"auth": {**auth, "session": response.json()["session"]}})
                return response
            response = delete(access_token, device_id)
            if response.status_code == 401 and response.json().get("errcode") == "M_UNKNOWN_TOKEN":
                temporary = login_account(account, display_name="Temporary device removal")
                response = delete(temporary["access_token"], device_id)
            if response.status_code not in (200, 204, 404):
                raise HTTPException(503, "Encrypted device removal was not confirmed; retry safely")
            if temporary:
                # This device has no encryption keys. Do not touch retained devices.
                # The target deletion is already confirmed. Cleanup failure must
                # not leave its linked native login active or delay its tombstone.
                try:
                    delete(temporary["access_token"], temporary["device_id"])
                except httpx.HTTPError:
                    pass
    except httpx.HTTPError:
        raise HTTPException(503, "Encrypted device removal was not confirmed; retry safely") from None
