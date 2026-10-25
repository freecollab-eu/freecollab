"""Session-bound proxy to a private Matrix homeserver, with fail-closed channel bindings."""
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, unquote
import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, ConfigDict, field_validator
from sqlmodel import select
from sqlalchemy import delete, update
import httpx
from ..config import settings
from ..database import get_session
from ..models.channel import Channel, ChannelMember
from ..models.secure_channel import SecureChannel, MatrixSession, MatrixAccount, MatrixDeviceRevocation
from ..models.session import ActiveSession, RevokedToken
from ..models.account_security import AccountSecurity
from ..models.user import User
from ..models.native_clients import NativeClientMatrixBinding, NativeClientSession
from ..models.embed import EmbedMatrixBinding, EmbedSession
from ..models.workspace_oidc import WorkspaceOIDCSession
from ..models.workspace_crypto_grant import WorkspaceEncryptionUnlock
from ..models.activity_feed import ActivityFeedPost
from ..models.copilot import CopilotConversation
from ..models.cloud_files import CloudFileLink
from ..models.external_meetings import ExternalMeeting
from ..services.access import require_channel_member, lock_channel_writes
from ..services.workspaces import workspace_allows_channel, require_direct_message_writable, visible_user_ids
from ..services.auth import active_user_for_token, get_current_user, token_digest, decode_token_claims, security, validate_password, token_is_revoked
from ..services.sso_scope import bind_scope_for_hash, current_workspace_scope, clear_scope, require_unrestricted
from ..services.workforce_access import is_managed, managed_scope_valid
from ..services.crypto import encrypt_message, decrypt_message
from ..services.account_security import account_limit, ensure_reauthentication, state_for
from ..services.rate_limit import request_address
from ..services.matrix_bridge import (require_matrix, ensure_account, login_account, create_secure_room,
                                      sync_secure_membership, matrix_request, membership_epoch, lock_device_accounts, revoked_devices, delete_owned_device)

router = APIRouter(tags=["secure messaging"])


class StartSession(BaseModel):
    device_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,80}$")


@router.get("/secure/status")
def status(user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    needs_unlock = False
    try:
        require_encryption_unlock(session, token_digest(credentials.credentials), user.id)
    except HTTPException as exc:
        if exc.status_code != 403:
            raise
        needs_unlock = True
    state = state_for(session, user.id)
    return {"enabled": getattr(settings, "matrix_enabled", False), "protocol": "matrix-megolm-v1",
            "metadata": "encrypted-client-events", "history": "joined", "verified_devices_only": True,
            "requires_encryption_unlock": needs_unlock, "mfa_required": bool(state.totp_secret)}


class EncryptionUnlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str
    code: str | None = Field(default=None, max_length=64)
    _password = field_validator("password")(validate_password)



def _crypto_account_data(event_type: str) -> bool:
    if not isinstance(event_type, str):
        return False
    return bool(event_type in {"m.secret_storage.default_key", "m.cross_signing.master",
                              "m.cross_signing.self_signing", "m.cross_signing.user_signing", "m.megolm_backup.v1"}
                or re.fullmatch(r"m\.secret_storage\.key\.[A-Za-z0-9_+/-]+", event_type))

def require_encryption_unlock(session, token_hash: str, user_id: str):
    """An IdP alone cannot obtain shared account secrets or encryption keys."""
    scope = current_workspace_scope(session)
    if is_managed(session, user_id):
        if scope is None or not managed_scope_valid(session, user_id, scope):
            raise HTTPException(401, "Organization sign-in is no longer valid")
        # This identity and its crypto account belong to exactly one workspace.
        # Provider authentication grants access, never recovery of device keys.
        return
    if scope is None:
        return
    grant = session.exec(select(WorkspaceEncryptionUnlock.workspace_id,
                               WorkspaceEncryptionUnlock.provider_revision,
                               WorkspaceEncryptionUnlock.auth_version,
                               WorkspaceEncryptionUnlock.expires_at).where(
        WorkspaceEncryptionUnlock.token_hash == token_hash,
    )).first()
    scoped = session.exec(select(WorkspaceOIDCSession.workspace_id,
                                WorkspaceOIDCSession.provider_revision).where(
        WorkspaceOIDCSession.token_hash == token_hash,
    )).first()
    version = session.exec(select(AccountSecurity.auth_version).where(AccountSecurity.user_id == user_id)).first()
    if (grant is None or scoped is None or grant[0] != scope or grant[0:2] != scoped[0:2]
            or grant[2] != (version or 0) or grant[3] <= datetime.utcnow()):
        raise HTTPException(403, {"code": "requires_encryption_unlock",
                                  "message": "Unlock encryption with your FreeCollab password and authenticator"})


@router.post("/secure/unlock")
def unlock_encryption(data: EncryptionUnlock, request: Request, user=Depends(get_current_user),
                      credentials=Depends(security), session=Depends(get_session)):
    digest = token_digest(credentials.credentials)
    account_limit(session, "encryption-unlock-address", request_address(request), 30, 300)
    account_limit(session, "encryption-unlock-account", user.id, 10, 300)
    state_for(session, user.id)
    session.execute(update(AccountSecurity).where(AccountSecurity.user_id == user.id)
                    .values(auth_version=AccountSecurity.auth_version))
    session.expire_all()
    actor = active_user_for_token(credentials.credentials, session)
    if actor is None:
        raise HTTPException(401, "Session expired")
    state = ensure_reauthentication(session, actor, data.password, data.code)
    # Password/MFA verification never broadens this token's workspace access.
    scope = current_workspace_scope(session)
    claims = decode_token_claims(credentials.credentials)
    expires = min(datetime.utcfromtimestamp(claims["exp"]), datetime.utcnow() + timedelta(minutes=30))
    if scope is not None:
        scoped = session.get(WorkspaceOIDCSession, digest)
        grant = session.get(WorkspaceEncryptionUnlock, digest)
        if grant is None:
            grant = WorkspaceEncryptionUnlock(token_hash=digest, workspace_id=scope,
                provider_revision=scoped.provider_revision, auth_version=state.auth_version, expires_at=expires)
        else:
            grant.workspace_id, grant.provider_revision = scope, scoped.provider_revision
            grant.auth_version, grant.expires_at = state.auth_version, expires
        session.add(grant)
    session.commit()
    return {"unlocked": True, "expires_at": expires.replace(tzinfo=timezone.utc)}


@router.post("/secure/session")
def start_session(data: StartSession, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    require_encryption_unlock(session, token_digest(credentials.credentials), user.id)
    require_matrix()
    account = ensure_account(session, user.id)
    # Reuse only devices already bound to this FreeCollab session. A new auth session
    # cannot inherit or overwrite a previous browser's cryptographic device identity.
    digest = token_digest(credentials.credentials)
    old = session.exec(select(MatrixSession).where(MatrixSession.auth_token_hash == digest)).first()
    if old:
        access_token = decrypt_message(old.encrypted_access_token, "matrix-session:" + old.matrix_token_hash)
        auth = {"user_id": account.matrix_user_id, "device_id": old.device_id, "access_token": access_token}
    else:
        auth = login_account(account)
        claims = decode_token_claims(credentials.credentials)
        row = MatrixSession(matrix_token_hash=token_digest(auth["access_token"]), user_id=user.id,
                            auth_token_hash=digest, auth_expires_at=datetime.utcfromtimestamp(claims["exp"]),
                            device_id=auth["device_id"], auth_version=claims.get("av", 0),
                            encrypted_access_token=encrypt_message(auth["access_token"], "matrix-session:" + token_digest(auth["access_token"])))
        session.add(row)
    # Provisioning/login can wait on the homeserver. Do not expose a credential
    # after a concurrent provider disable, membership removal or native logout.
    claims = decode_token_claims(credentials.credentials)
    if claims.get("native_client_id"):
        matrix_hash = token_digest(auth["access_token"])
        marker = session.get(NativeClientMatrixBinding, matrix_hash)
        if marker is None:
            session.add(NativeClientMatrixBinding(matrix_token_hash=matrix_hash,
                native_client_id=claims["native_client_id"]))
    if claims.get("embed_application_id"):
        matrix_hash = token_digest(auth["access_token"])
        marker = session.get(EmbedMatrixBinding, matrix_hash)
        if marker is None:
            session.add(EmbedMatrixBinding(matrix_token_hash=matrix_hash,
                application_id=claims["embed_application_id"]))
        elif marker.application_id != claims["embed_application_id"]:
            raise HTTPException(401, "Embedded client sign-in expired")
    session.flush()
    session.expire_all()
    if active_user_for_token(credentials.credentials, session) is None:
        raise HTTPException(401, "Session expired")
    require_encryption_unlock(session, digest, user.id)
    session.commit()
    return {**auth, "base_url": "", "server_name": settings.matrix_server_name}


@router.get("/secure/channels")
def channels(user=Depends(get_current_user), session=Depends(get_session)):
    rows = session.exec(select(SecureChannel).join(ChannelMember, ChannelMember.channel_id == SecureChannel.channel_id)
                        .where(ChannelMember.user_id == user.id)).all()
    from ..services.connect_federation import peer_server_for_channel
    return [{"channel_id": r.channel_id, "room_id": r.matrix_room_id, "status": r.status,
        **({"federated_peer_server": peer_server_for_channel(session, r.channel_id)}
           if peer_server_for_channel(session, r.channel_id) else {})}
        for r in rows if workspace_allows_channel(session, r.channel_id, user.id)]


def has_native_product_history(session, channel_id: str) -> bool:
    # A channel gains client encryption only while empty. Archived projects,
    # immutable document history and workflow definitions still count as history.
    from ..models.project_tasks import ProjectBoard, TaskCard
    from ..models.shared_documents import SharedDocument
    from ..models.document_organization import SharedDocumentStack, SharedDocumentPlacement, SharedDocumentRelation, SharedDocumentStackReference
    from ..models.channel_metadata import ChannelMetadata
    from ..models.workflow import WorkflowDefinition, WorkflowRun
    return (any(session.exec(select(model.id).where(model.channel_id == channel_id)).first()
               for model in (ProjectBoard, TaskCard, SharedDocument, SharedDocumentStack, SharedDocumentRelation, SharedDocumentStackReference, WorkflowDefinition, WorkflowRun, ActivityFeedPost, CopilotConversation, CloudFileLink, ExternalMeeting))
            or session.exec(select(SharedDocumentPlacement.document_id).where(SharedDocumentPlacement.channel_id == channel_id)).first() is not None
            or session.exec(select(ChannelMetadata.channel_id).where(ChannelMetadata.channel_id == channel_id,
                                                                    ChannelMetadata.content_mode == "server")).first() is not None)


@router.post("/secure/channels/{channel_id}/enable")
def enable_channel(channel_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    require_matrix()
    lock_channel_writes(session, channel_id)
    member = require_channel_member(session, channel_id, user.id)
    if member.role != "admin":
        raise HTTPException(403, "Channel admin access required")
    channel = session.get(Channel, channel_id)
    if channel.type == "public":
        raise HTTPException(400, "Encrypted channels must be private")
    binding = session.get(SecureChannel, channel_id)
    if binding is None:
        # Existing server-side history cannot be presented as client encrypted.
        from ..models.message import Message
        from ..models.file import File
        from ..models.collaboration import Decision
        from ..models.integration_content import IntegrationContent
        from ..models.post import Post
        if (session.exec(select(Message.id).where(Message.channel_id == channel_id)).first() or session.exec(select(File.id).where(File.channel_id == channel_id)).first() or session.exec(select(Decision.id).where(Decision.channel_id == channel_id)).first() or session.exec(select(Post.id).where(Post.flow_type == "channel", Post.flow_id == channel_id)).first()
                or session.exec(select(IntegrationContent.id).where(IntegrationContent.channel_id == channel_id)).first()
                or has_native_product_history(session, channel_id)):
            raise HTTPException(409, "Create a new empty channel for end-to-end encryption")
        binding = SecureChannel(channel_id=channel_id, created_by=user.id)
        # Native semantic caches must not outlive the switch to device-only
        # encrypted work, including an empty-channel index built before enable.
        from ..models.content_completion import ChannelSemanticIndex
        from ..services.semantic_opensearch import purge_channel as purge_semantic_channel
        purge_semantic_channel(session, channel_id)
        session.execute(delete(ChannelSemanticIndex).where(ChannelSemanticIndex.channel_id == channel_id))
        from ..services.media_capture import invalidate_channel_captures
        invalidate_channel_captures(session, channel_id)
        channel.name = "Encrypted channel"
        session.add(channel)
        session.add(binding)
        session.commit()  # Preparing is durable and blocks every plaintext write.
    if not binding.matrix_room_id:
        create_secure_room(session, binding)
        session.commit()
    sync_secure_membership(session, channel_id)
    session.commit()
    return {"channel_id": channel_id, "room_id": binding.matrix_room_id, "status": binding.status}


@router.post("/secure/channels/{channel_id}/sync")
def sync_channel(channel_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    require_channel_member(session, channel_id, user.id)
    require_direct_message_writable(session, channel_id, user.id)
    sync_secure_membership(session, channel_id)
    session.commit()
    binding = session.get(SecureChannel, channel_id)
    if not binding or not binding.matrix_room_id:
        raise HTTPException(409, "Encrypted channel is not ready")
    members = session.exec(select(ChannelMember.user_id).where(ChannelMember.channel_id == channel_id)).all()
    from ..services.connect_federation import peer_revocations, cached_peer_members, connection_for_channel as federated_channel, control_ids
    federated = federated_channel(session, channel_id)
    return {"room_id": binding.matrix_room_id, "membership_epoch": membership_epoch(session, channel_id),
        "revoked_devices": revoked_devices(session, members) + peer_revocations(session, channel_id),
        "federated_operators": control_ids(session, federated) if federated else [],
        "federated_peers": [{"user_id": row.matrix_user_id, "name": row.name,
            "matrix_user_id": row.matrix_user_id} for row in cached_peer_members(session, channel_id)]}


class RevokeDevice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=72)
    code: str | None = Field(default=None, max_length=64)
    _password = field_validator("password")(validate_password)


class RevokeManagedDevice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: bool


@router.get("/secure/devices/revocations")
def device_revocations(channel_id: str | None = None, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    users = [user.id]
    if not channel_id:
        require_encryption_unlock(session, token_digest(credentials.credentials), user.id)
    if channel_id:
        require_channel_member(session, channel_id, user.id)
        users = session.exec(select(ChannelMember.user_id).where(ChannelMember.channel_id == channel_id)).all()
    from ..services.connect_federation import peer_revocations
    return {"revoked_devices": revoked_devices(session, users) + (peer_revocations(session, channel_id) if channel_id else [])}


@router.post("/secure/devices/{device_id}/revoke")
def revoke_device(device_id: str, data: RevokeDevice, request: Request, user=Depends(get_current_user),
                  credentials=Depends(security), session=Depends(get_session)):
    require_unrestricted(session)
    require_matrix()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", device_id):
        raise HTTPException(400, "Invalid encryption device")
    # Rate buckets and missing account state commit before any recipient lock.
    account_limit(session, "device-revoke-address", request_address(request), 30, 300)
    account_limit(session, "device-revoke-account", user.id, 15, 300)
    state_for(session, user.id)
    session.execute(update(AccountSecurity).where(AccountSecurity.user_id == user.id).values(auth_version=AccountSecurity.auth_version))
    # Dependencies may have cached security state before waiting for this lock.
    # Re-read the factor policy and this login's validity under the same lock.
    session.expire_all()
    session.refresh(user)
    claims = decode_token_claims(credentials.credentials)
    if not claims or user.is_blocked or token_is_revoked(credentials.credentials, session, claims):
        raise HTTPException(401, "Account security changed. Please sign in again.")
    ensure_reauthentication(session, user, data.password, data.code)
    return _revoke_owned_device(session, user, credentials.credentials, device_id)


@router.post("/secure/devices/{device_id}/revoke-managed")
def revoke_managed_device(device_id: str, data: RevokeManagedDevice, request: Request,
                          user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    require_matrix()
    if not data.confirmed or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", device_id):
        raise HTTPException(400, "Confirm removal of a valid encryption device")
    account_limit(session, "device-revoke-address", request_address(request), 30, 300)
    account_limit(session, "device-revoke-account", user.id, 15, 300)
    state_for(session, user.id)
    session.execute(update(AccountSecurity).where(AccountSecurity.user_id == user.id)
                    .values(auth_version=AccountSecurity.auth_version))
    session.expire_all()
    actor = active_user_for_token(credentials.credentials, session)
    scope = current_workspace_scope(session)
    if actor is None:
        raise HTTPException(401, "Organization sign-in is no longer valid")
    if not is_managed(session, user.id) or not scope or not managed_scope_valid(session, user.id, scope):
        raise HTTPException(403, "This control is only for an organization-managed encryption identity")
    claims = decode_token_claims(credentials.credentials)
    issued = claims.get("iat") if claims else None
    now = datetime.now(timezone.utc).timestamp()
    if (type(issued) not in (int, float) or issued < now - 300 or issued > now + 30
            or "sso" not in claims.get("amr", [])):
        raise HTTPException(403, {"code": "requires_recent_organization_signin",
            "message": "Sign in with your organization again, then remove the device within five minutes."})
    return _revoke_owned_device(session, actor, credentials.credentials, device_id)


def _revoke_owned_device(session, user, native_token, device_id):
    rows = session.exec(select(MatrixSession).where(MatrixSession.user_id == user.id, MatrixSession.device_id == device_id)).all()
    existing = session.get(MatrixDeviceRevocation, (user.id, device_id))
    if not rows and not existing:
        raise HTTPException(404, "Own encryption device not found")
    current_digest = token_digest(native_token)
    current = any(row.auth_token_hash == current_digest for row in rows)
    if existing:
        session.commit()
        return {"revoked": True, "current": current}
    account = session.get(MatrixAccount, user.id)
    controller = session.exec(select(MatrixSession).where(MatrixSession.user_id == user.id, MatrixSession.auth_token_hash == current_digest)).first()
    if not account or not controller:
        raise HTTPException(409, "Initialize encryption on this browser before removing a device")
    lock_device_accounts(session, [user.id])
    # Synapse confirms deletion before native sessions or the tombstone change.
    access = decrypt_message(controller.encrypted_access_token, "matrix-session:" + controller.matrix_token_hash)
    delete_owned_device(account, access, device_id)
    now = datetime.utcnow()
    session.add(MatrixDeviceRevocation(user_id=user.id, device_id=device_id, revoked_at=now))
    for target in rows:
        native = session.exec(select(ActiveSession).where(ActiveSession.token_hash == target.auth_token_hash)).first()
        if native:
            native.revoked_at = now
            session.add(native)
        if target.auth_expires_at > now and not session.get(RevokedToken, target.auth_token_hash):
            session.add(RevokedToken(token_hash=target.auth_token_hash, expires_at=target.auth_expires_at))
    session.commit()
    return {"revoked": True, "current": current}


@router.post("/secure/cross-signing-auth")
def cross_signing_auth(data: dict, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    """Complete UIA server-side; the random Matrix login secret never enters the UI."""
    require_encryption_unlock(session, token_digest(credentials.credentials), user.id)
    if set(data) - {"keys", "session", "matrix_device_id"} or not isinstance(data.get("keys"), dict):
        raise HTTPException(400, "Invalid signing-key request")
    account = ensure_account(session, user.id)
    # Use the current device token so UIA session belongs to the initiating device.
    row = session.exec(select(MatrixSession).where(MatrixSession.user_id == user.id, MatrixSession.device_id == data.get("matrix_device_id"))).first()
    if not row:
        raise HTTPException(403, "Unknown device")
    token = decrypt_message(row.encrypted_access_token, "matrix-session:" + row.matrix_token_hash)
    password = decrypt_message(account.encrypted_password, "matrix-auth:" + user.id)
    auth = {"type": "m.login.password", "identifier": {"type": "m.id.user", "user": account.matrix_user_id}, "password": password}
    if data.get("session"):
        auth["session"] = data["session"]
    return matrix_request("POST", "/_matrix/client/v3/keys/device_signing/upload", token=token, data={**data["keys"], "auth": auth})


def _matrix_authorize(request, session):
    clear_scope(session)
    session.expire_all()
    bearer = request.headers.get("authorization", "")
    if not bearer.startswith("Bearer "):
        raise HTTPException(401, "Matrix session required")
    row = session.get(MatrixSession, token_digest(bearer[7:]))
    if not row or row.auth_expires_at <= datetime.utcnow() or session.get(RevokedToken, row.auth_token_hash) or session.get(MatrixDeviceRevocation, (row.user_id, row.device_id)):
        raise HTTPException(401, "Session expired")
    tracked = session.exec(select(ActiveSession).where(ActiveSession.token_hash == row.auth_token_hash)).first()
    state = session.get(AccountSecurity, row.user_id)
    user = session.get(User, row.user_id)
    if not user or user.is_blocked or not tracked or (tracked.revoked_at or tracked.expires_at <= datetime.utcnow()) or (state.auth_version if state else 0) != row.auth_version:
        raise HTTPException(401, "Session expired")
    marker = session.get(NativeClientMatrixBinding, row.matrix_token_hash)
    native = session.get(NativeClientSession, row.auth_token_hash)
    if (marker is not None or native is not None) and (marker is None or native is None or marker.native_client_id != native.id):
        raise HTTPException(401, "Native client sign-in expired")
    embed_marker = session.get(EmbedMatrixBinding, row.matrix_token_hash)
    embedded = session.get(EmbedSession, row.auth_token_hash)
    if (embed_marker is not None or embedded is not None) and (
            embed_marker is None or embedded is None or embed_marker.application_id != embedded.application_id):
        raise HTTPException(401, "Embedded client sign-in expired")
    if not bind_scope_for_hash(session, row.auth_token_hash, row.user_id):
        raise HTTPException(401, "Workspace sign-in expired")
    grant_scope = session.exec(select(WorkspaceEncryptionUnlock.workspace_id).where(
        WorkspaceEncryptionUnlock.token_hash == row.auth_token_hash,
    )).first()
    if grant_scope is not None and grant_scope != current_workspace_scope(session):
        raise HTTPException(401, "Workspace sign-in expired")
    require_encryption_unlock(session, row.auth_token_hash, row.user_id)
    return row, bearer


@router.api_route("/_matrix/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def matrix_proxy(path: str, request: Request, session=Depends(get_session)):
    require_matrix()
    # Do not expose admin, registration, password-login or arbitrary external media.
    if path == "client/versions" and request.method == "GET":
        return matrix_request("GET", "/_matrix/" + path)
    row, bearer = _matrix_authorize(request, session)
    decoded = unquote(path)
    if decoded in ("client/v3/pushrules/", "client/r0/pushrules/"):
        decoded = decoded.rstrip("/")
    if any(ord(ch) < 32 for ch in decoded) or any(ch in decoded for ch in ("?", "#")) or "%" in decoded or "\\" in decoded or any(part in ("", ".", "..") for part in decoded.split("/")):
        raise HTTPException(403, "Ambiguous Matrix path")
    normalized = re.sub(r"^client/(?:r0|v1)/", "client/v3/", decoded)
    allowed = (
        r"client/v3/(?:sync|capabilities|pushrules|devices|keys/changes)",
        r"client/v3/keys/(?:upload|query|claim|device_signing/upload|signatures/upload)",
        r"client/v3/room_keys/(?:version(?:/[^/]+)?|keys(?:/[^/]+(?:/[^/]+)?)?)",
        r"client/v3/user/@[^/]+/(?:account_data/[^/]+|filter(?:/[^/]+)?)",
        r"client/v3/sendToDevice/(?:m\.room\.encrypted|m\.room_key_request|m\.room_key\.withheld|m\.key\.verification\.[a-z_]+|m\.secret\.request)/[^/]+",
        r"client/v3/join/![^/]+",
        r"client/v3/rooms/![^/]+/(?:join|state(?:/[^/]+(?:/[^/]+)?)?|members|joined_members|messages|threads|context/[^/]+|event/[^/]+|relations/[^/]+(?:/[^/]+(?:/[^/]+)?)?|send/m\.room\.encrypted/[^/]+|redact/[^/]+/[^/]+)",
        r"media/(?:v3|r0)/(?:config|upload|download/[^/]+/[^/]+)",
        r"client/v3/media/(?:config|upload|download/[^/]+/[^/]+)",
    )
    if not any(re.fullmatch(pattern, normalized) for pattern in allowed):
        raise HTTPException(403, "Unsupported Matrix endpoint")
    account = session.get(MatrixAccount, row.user_id)
    user_path = re.fullmatch(r"client/v3/user/(@[^/]+)/.+", normalized)
    if user_path and (not account or user_path[1] != account.matrix_user_id):
        raise HTTPException(403, "Own Matrix account required")
    account_data_path = re.fullmatch(r"client/v3/user/@[^/]+/account_data/([^/]+)", normalized)
    if (current_workspace_scope(session) is not None and account_data_path
            and not _crypto_account_data(account_data_path[1])):
        raise HTTPException(403, "This sign-in only grants access to workspace content and unlocked encryption keys")
    if "/rooms/" in decoded and "/state" in decoded and request.method != "GET":
        raise HTTPException(403, "Room security state is managed by FreeCollab")
    match = re.search(r"/(?:rooms|join)/(![^/]+)", decoded)
    if match:
        binding = session.exec(select(SecureChannel).where(SecureChannel.matrix_room_id == match[1])).first()
        if not binding:
            raise HTTPException(403, "Unknown encrypted room")
        require_channel_member(session, binding.channel_id, row.user_id)
        if request.method != "GET":
            require_direct_message_writable(session, binding.channel_id, row.user_id)
    if request.method == "POST" and re.search(r"^(?:media/(?:v3|r0)/|client/v1/media/)upload$", decoded):
        if request.headers.get("content-type", "").split(";", 1)[0] != "application/octet-stream" or "filename" in request.query_params:
            raise HTTPException(400, "Encrypted uploads require anonymous ciphertext")
    media = re.search(r"/media/(?:v3|r0)/download/([^/]+)/|^media/(?:v3|r0)/download/([^/]+)/|^client/v1/media/download/([^/]+)/", decoded)
    remote_media = False
    if media and next(group for group in media.groups() if group) != settings.matrix_server_name:
        server = next(group for group in media.groups() if group)
        from ..services.connect_federation import peer_server_for_channel
        mappings = session.exec(select(SecureChannel).join(ChannelMember,
            ChannelMember.channel_id == SecureChannel.channel_id).where(ChannelMember.user_id == row.user_id)).all()
        remote_media = any(workspace_allows_channel(session, mapped.channel_id, row.user_id)
            and peer_server_for_channel(session, mapped.channel_id) == server
            for mapped in mappings)
        if not remote_media:
            raise HTTPException(403, "Encrypted media requires a current explicitly shared peer")
    body = await request.body()
    if len(body) > 52 * 1024 * 1024:
        raise HTTPException(413, "Encrypted upload is too large")
    if '/sendToDevice/m.room_key_request/' in decoded or '/sendToDevice/m.room_key.withheld/' in decoded:
        import json
        try:
            messages = json.loads(body)["messages"]
            for targets in messages.values():
                for content in targets.values():
                    room_id = content.get("body", content).get("room_id")
                    if room_id:
                        target = session.exec(select(SecureChannel).where(SecureChannel.matrix_room_id == room_id)).first()
                        if not target:
                            raise HTTPException(403, "Unknown encrypted key-request room")
                        require_channel_member(session, target.channel_id, row.user_id)
                    elif content.get("action") != "request_cancellation":
                        raise HTTPException(400, "Key requests require an authorized room")
        except (ValueError, KeyError, TypeError, AttributeError):
            raise HTTPException(400, "Invalid room-key request") from None
    if '/send/m.room.encrypted/' in decoded:
        import json
        try:
            content = json.loads(body)
        except (ValueError, TypeError):
            raise HTTPException(400, "Invalid encrypted event") from None
        if (not isinstance(content, dict) or content.get("algorithm") != "m.megolm.v1.aes-sha2"
                or not isinstance(content.get("ciphertext"), str) or not content["ciphertext"]
                or set(content) - {"algorithm", "ciphertext", "device_id", "sender_key", "session_id", "m.relates_to"}):
            raise HTTPException(400, "Only encrypted message envelopes are accepted")
    # Only opaque encrypted events may carry message or channel/file metadata.
    if '/send/' in decoded and not re.search(r'/send/m\.room\.encrypted/', decoded):
        raise HTTPException(400, "Encrypted channels require encrypted events")
    sending = '/send/m.room.encrypted/' in decoded
    parameters = [(key, value) for key, value in request.query_params.multi_items() if key not in ('access_token', 'allow_remote')]
    if media:
        parameters.append(('allow_remote', 'true' if remote_media else 'false'))
    try:
        if sending:
            # The DB lock and upstream send run in one worker. A concurrent native
            # removal cannot interleave between epoch validation and event ACK.
            from starlette.concurrency import run_in_threadpool
            from ..services.workspaces import channel_workspace_id, lock_workspace
            from ..services.connect_federation import connection_for_channel as federated_channel, remote_snapshot
            federated = federated_channel(session, binding.channel_id)
            if federated:
                # A second fresh remote check precedes the local send lock. Never
                # hold opposing SQL write locks across an inter-installation call.
                await run_in_threadpool(remote_snapshot, session, federated)
            def send_bound_event():
                recipients = session.exec(select(ChannelMember.user_id).where(ChannelMember.channel_id == binding.channel_id)).all()
                lock_device_accounts(session, recipients)
                workspace_id = channel_workspace_id(session, binding.channel_id)
                from ..services.connect import connection_for_channel
                if workspace_id and not connection_for_channel(session, binding.channel_id):
                    lock_workspace(session, workspace_id)
                lock_channel_writes(session, binding.channel_id)
                session.expire_all()
                _matrix_authorize(request, session)
                current_room = session.exec(select(SecureChannel.matrix_room_id).where(
                    SecureChannel.channel_id == binding.channel_id,
                )).first()
                if current_room != match[1]:
                    raise HTTPException(403, "Encrypted room binding changed")
                current_recipients = session.exec(select(ChannelMember.user_id).where(ChannelMember.channel_id == binding.channel_id)).all()
                if not set(current_recipients).issubset(recipients):
                    raise HTTPException(409, "Channel recipients changed; prepare encryption again before sending")
                require_channel_member(session, binding.channel_id, row.user_id)
                require_direct_message_writable(session, binding.channel_id, row.user_id)
                if request.headers.get('x-freecollab-membership-epoch') != membership_epoch(session, binding.channel_id):
                    raise HTTPException(409, "Channel membership changed; prepare encryption again before sending")
                if federated:
                    from ..services.connect_federation import validate_room_roster
                    validate_room_roster(session, federated)
                try:
                    with httpx.Client(timeout=40, trust_env=False, follow_redirects=False) as upstream:
                        return upstream.request(request.method, settings.matrix_internal_url.rstrip('/')+'/_matrix/'+path,
                            params=parameters, content=body, headers={"Authorization": bearer, "Content-Type": "application/json"})
                finally:
                    session.rollback()
            response = await run_in_threadpool(send_bound_event)
        else:
            async with httpx.AsyncClient(timeout=65, trust_env=False, follow_redirects=False) as client:
                response = await client.request(request.method, settings.matrix_internal_url.rstrip('/')+'/_matrix/'+path,
                params=parameters, content=body,
                headers={"Authorization": bearer, "Content-Type": request.headers.get("content-type", "application/json")})
    except httpx.HTTPError:
        raise HTTPException(503, "Encrypted messaging service is unavailable") from None
    _matrix_authorize(request, session)  # Recheck provider/member changes during any upstream wait.
    # Synapse UI authentication is completed only for signing-key upload. Its
    # random login password authenticates the account; it cannot decrypt content.
    if response.status_code == 401 and decoded.endswith('/keys/device_signing/upload') and request.method == 'POST':
        import json
        payload = json.loads(body)
        challenge = response.json()
        account = session.get(MatrixAccount, row.user_id)
        payload["auth"] = {"type": "m.login.password", "identifier": {"type": "m.id.user", "user": account.matrix_user_id},
                           "password": decrypt_message(account.encrypted_password, "matrix-auth:" + row.user_id), "session": challenge.get("session")}
        async with httpx.AsyncClient(timeout=40, trust_env=False) as client:
            response = await client.post(settings.matrix_internal_url.rstrip('/')+'/_matrix/'+path, json=payload, headers={"Authorization": bearer})
    session.expire_all()
    _matrix_authorize(request, session)
    if match:
        require_channel_member(session, binding.channel_id, row.user_id)
        if request.method != "GET":
            require_direct_message_writable(session, binding.channel_id, row.user_id)
    if normalized == "client/v3/sync" and response.status_code == 200:
        import json
        payload = response.json()
        mappings = session.exec(select(SecureChannel).join(ChannelMember, ChannelMember.channel_id == SecureChannel.channel_id)
                                .where(ChannelMember.user_id == row.user_id)).all()
        allowed_rooms = {b.matrix_room_id for b in mappings if workspace_allows_channel(session, b.channel_id, row.user_id)}
        for section in ("join", "invite", "leave", "knock"):
            if section in payload.get("rooms", {}):
                payload["rooms"][section] = {room_id: data for room_id, data in payload["rooms"][section].items() if room_id in allowed_rooms}
        if current_workspace_scope(session) is not None:
            # SSSS writes need their sync acknowledgement for the Rust SDK to
            # complete recovery setup. Only encryption account data is permitted
            # following native unlock; m.direct and global preferences stay hidden.
            account_data = payload.get("account_data")
            if isinstance(account_data, dict):
                account_data["events"] = [event for event in account_data.get("events", [])
                    if isinstance(event, dict) and _crypto_account_data(event.get("type", ""))]
        visible_ids = visible_user_ids(session, row.user_id)
        visible_matrix = set(session.exec(select(MatrixAccount.matrix_user_id).where(MatrixAccount.user_id.in_(visible_ids))).all())
        if "presence" in payload:
            payload["presence"]["events"] = [event for event in payload["presence"].get("events", []) if event.get("sender") in visible_matrix]
        # Crypto traffic still works between current group peers after a personal
        # DM block; that block hides presence and direct chat, not shared groups.
        crypto_user_ids = {row.user_id}
        for mapped in mappings:
            if mapped.matrix_room_id not in allowed_rooms:
                continue
            try:
                require_direct_message_writable(session, mapped.channel_id, row.user_id)
            except HTTPException:
                continue
            peers = session.exec(select(ChannelMember.user_id).where(ChannelMember.channel_id == mapped.channel_id)).all()
            crypto_user_ids.update(peer for peer in peers if workspace_allows_channel(session, mapped.channel_id, peer))
        crypto_matrix = set(session.exec(select(MatrixAccount.matrix_user_id).where(MatrixAccount.user_id.in_(crypto_user_ids))).all())
        from ..services.connect_federation import peer_crypto_ids
        for mapped in mappings:
            if mapped.matrix_room_id in allowed_rooms:
                crypto_matrix.update(peer_crypto_ids(session, mapped.channel_id))
        for kind in ("changed", "left"):
            if kind in payload.get("device_lists", {}):
                payload["device_lists"][kind] = [user_id for user_id in payload["device_lists"][kind] if user_id in crypto_matrix]
        if "to_device" in payload:
            payload["to_device"]["events"] = [event for event in payload["to_device"].get("events", []) if event.get("sender") in crypto_matrix]
        response = httpx.Response(200, json=payload)
    _matrix_authorize(request, session)  # Logout also cuts off outstanding sync responses.
    return Response(response.content, status_code=response.status_code, headers={
        "Content-Type": response.headers.get("content-type", "application/json"), "Cache-Control": "no-store"})
