"""Ephemeral, explicitly reviewed disclosure from a secure device to an app.

This is deliberately outside the background outbox. Only content-free outcome
authority survives the HTTP request; neither arguments nor provider output are
stored, logged, retried, or published into an encrypted room by this service.
"""
from datetime import datetime, timedelta
import hashlib
import hmac
import json

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from ..models.app_platform import AppForegroundReceipt, AppInstallation
from ..models.secure_channel import MatrixAccount, MatrixDeviceRevocation, MatrixSession, SecureChannel
from . import app_platform as apps, matrix_bridge, remote_transport
from .access import lock_channel_writes
from .account_security import unseal_private
from .request_guard import recheck_actor
from .workspaces import lock_workspace


def secure_context(session, workspace_id, channel_id, actor_id, device_id=None):
    recheck_actor(session, actor_id)
    apps.native_channel(session, workspace_id, channel_id, actor_id, allow_secure=True)
    binding = session.get(SecureChannel, channel_id, populate_existing=True)
    account = session.get(MatrixAccount, actor_id, populate_existing=True)
    if not binding or binding.status != "active" or not binding.matrix_room_id or not account or not account.provisioned:
        raise HTTPException(409, "Open the active encrypted channel on your trusted device before reviewing this app")
    if device_id is not None:
        # The assertion is useful only when the Matrix device is bound to the
        # *verified native request token*. A caller-supplied device ID is no proof.
        context = session.info.get("freecollab_verified_request")
        if not context:
            raise HTTPException(401, "A current authenticated secure device session is required")
        bound = session.exec(select(MatrixSession).where(MatrixSession.user_id == actor_id,
            MatrixSession.auth_token_hash == context["token_hash"], MatrixSession.device_id == device_id,
            MatrixSession.auth_expires_at > datetime.utcnow(),
            MatrixSession.auth_version == context["claims"].get("av", 0))).first()
        if not bound or session.get(MatrixDeviceRevocation, (actor_id, device_id)):
            raise HTTPException(403, "This secure device session changed; open the encrypted channel again")
    return (binding.matrix_room_id, account.matrix_user_id, matrix_bridge.membership_epoch(session, channel_id))


def authorized(session, installation_id, channel_id, actor_id, data):
    recheck_actor(session, actor_id)
    row = session.get(AppInstallation, installation_id, populate_existing=True)
    if not row or row.generation != data.expected_generation:
        raise HTTPException(409, "The installed app changed; review its current recipient again")
    manifest = apps.manifest_for(row)
    if (not manifest.secure_foreground or "commands:invoke" not in manifest.scopes
            or channel_id not in apps.channels(row) or not apps.allowed_installation(session, row)):
        raise HTTPException(403, "This app is not granted for secure foreground disclosure")
    command = next((value for value in manifest.commands if value.name == data.command), None)
    if not command:
        raise HTTPException(404, "This command is not installed")
    context = secure_context(session, row.workspace_id, channel_id, actor_id, data.matrix_device_id)
    return row, command, apps.arguments_for(command, data.arguments), context


def receipt_data(row):
    return {"id": row.id, "command": row.command, "status": row.status, "attempts": 1,
        "http_status": row.http_status, "failure": row.failure, "created_at": row.created_at,
        "foreground": True, "preview": None}


def invoke(session, row, channel_id, actor_id, data, send=None):
    if not data.confirm or not data.disclose_to_app or not data.disclose_decrypted_content:
        raise HTTPException(422, "Explicitly approve these exact arguments leaving end-to-end encryption for this app")
    row, command, args, context = authorized(session, row.id, channel_id, actor_id, data)
    installation_id, workspace_id = row.id, row.workspace_id
    config = json.loads(unseal_private(row.encrypted_config, "app-config", row.id))
    secret = unseal_private(row.encrypted_secret, "app-signing", row.id)
    provider_grant_id = None
    authorization = config.get("authorization", "")
    if command.protocol in {"google.calendar-event", "microsoft.calendar-event"}:
        from . import personal_oauth
        grant = personal_oauth.grant_for_target(session, "calendar-action", row.id, actor_id)
        if not grant:
            raise HTTPException(409, "Connect your personal provider account for this installed app first")
        provider_grant_id = grant.id
        authorization = "Bearer " + personal_oauth.access_token(session, grant.id, user_id=actor_id)
        # Refresh may release locks. Fence again before approving any disclosure.
        lock_workspace(session, workspace_id); lock_channel_writes(session, channel_id); session.expire_all()
        row, command, args, refreshed = authorized(session, installation_id, channel_id, actor_id, data)
        if refreshed != context:
            raise HTTPException(409, "Encrypted membership changed; review this disclosure again")
    digest = hmac.new(secret.encode(), b"freecollab-secure-app-receipt-v1\x00" + apps.dump([
        installation_id, data.expected_generation, actor_id, channel_id, command.name, args,
        context, data.matrix_device_id, provider_grant_id]).encode(), hashlib.sha256).hexdigest()
    previous = session.exec(select(AppForegroundReceipt).where(AppForegroundReceipt.installation_id == row.id,
        AppForegroundReceipt.request_id == data.request_id)).first()
    if previous:
        if previous.request_digest != digest:
            raise HTTPException(409, "Request identifier already belongs to another reviewed disclosure")
        if previous.status == "processing" and previous.created_at < datetime.utcnow() - timedelta(seconds=90):
            previous.status, previous.failure, previous.finished_at = "uncertain", "Delivery outcome is unknown; inspect the recipient before another action", datetime.utcnow()
            session.add(previous); session.commit()
        return receipt_data(previous)
    receipt = AppForegroundReceipt(installation_id=row.id, generation=row.generation, channel_id=channel_id,
        actor_id=actor_id, command=command.name, request_id=data.request_id, request_digest=digest)
    try:
        with session.begin_nested():
            session.add(receipt); session.flush()
    except IntegrityError:
        raise HTTPException(409, "This disclosure is already in progress; inspect its receipt before retrying") from None
    payload = {"schema_version": 1, "id": receipt.id, "type": "app.command", "installation_id": row.id,
        "generation": row.generation, "workspace_id": row.workspace_id, "channel_id": channel_id,
        "actor_id": actor_id, "bot_id": row.bot_id, "command": command.name, "arguments": args,
        "secure_foreground": True}
    body = apps.dump(apps.provider_payload(command.protocol, payload)).encode()
    headers = apps.webhooks.signature_headers(secret, body, receipt.id, "app.command")
    headers["Idempotency-Key"] = receipt.id
    if authorization: headers["Authorization"] = authorization
    receipt_id = receipt.id
    session.commit()  # Persist the content-free authority BEFORE any external I/O.
    preview, code, status, failure = None, None, "uncertain", "Delivery outcome is unknown; inspect the recipient before another action"
    try:
        response = (send or remote_transport.request)("POST", config["endpoint"], headers=headers,
            body=body, max_bytes=8192, timeout=25)
        code = response.status
        if 200 <= code < 300:
            status, failure = "delivered", None
            preview = response.body.decode("utf-8", errors="replace").replace("\x00", "")[:8192]
        else:
            # An app may commit a write and then return an error. Do not infer a
            # safe retry from arbitrary provider HTTP semantics or retain body.
            failure = "The recipient returned an error; inspect its outcome before another action"
    except Exception:
        # Never retain exception strings: provider libraries can include content
        # or credentials. Ambiguous delivery is permanent for this request ID.
        pass
    lock_workspace(session, workspace_id); lock_channel_writes(session, channel_id); session.expire_all()
    changed = False
    try:
        _, _, _, after = authorized(session, installation_id, channel_id, actor_id, data)
        if after != context: raise HTTPException(409, "Encrypted membership changed")
        if provider_grant_id:
            from . import personal_oauth
            personal_oauth.current_grant(session, provider_grant_id, user_id=actor_id)
    except HTTPException:
        changed = True; preview = None
        failure = "Access changed after disclosure; the recipient may already have received it. No preview is retained"
    receipt = session.get(AppForegroundReceipt, receipt_id, populate_existing=True)
    receipt.status, receipt.http_status, receipt.failure, receipt.finished_at = status, code, failure, datetime.utcnow()
    session.add(receipt); session.commit()
    if changed:
        # Do not return even receipt details to a token that was revoked while
        # the external request was in flight. The administrator can inspect the
        # content-free delivery authority through a current session.
        raise HTTPException(409, failure)
    return {**receipt_data(receipt), "preview": preview}
