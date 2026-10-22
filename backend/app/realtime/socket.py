import socketio
import asyncio
from contextlib import suppress
from datetime import datetime
from sqlmodel import Session
from fastapi import HTTPException
from ..database import engine
from ..config import settings
from ..models.channel import Channel, ChannelMember
from ..models.user import User
from ..services.auth import active_user_for_token, decode_token_claims, token_digest
from ..services.request_guard import remember_request, recheck_actor
from ..services.shared_runtime import runtime
from .broker import broker
from ..services.sso_scope import current_workspace_scope, require_channel_scope
from ..services.workspaces import workspace_allows_channel, visible_user_ids, require_direct_message_writable

sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins=settings.cors_origins)

# Per-process sessions retain JWTs only in Socket.IO's local session store.
# Optional Valkey leases contain digests/verified claims, never bearer tokens.
presence_store: dict[str, dict] = {}  # user_id -> {status, last_seen, sid}
_presence_task = None


def _presence_metadata(user_id, token, status="online"):
    claims = decode_token_claims(token) or {}
    allowed = {key: claims[key] for key in ("sub","exp","iat","av","amr","sso_workspace_id","sso_provider_revision","embed_application_id","native_client_id") if key in claims}
    now = datetime.utcnow()
    return {"user_id":user_id,"status":status,"last_seen":now.isoformat(),"changed_at":now.timestamp(),
        "session_hash":token_digest(token),"claims":allowed}


def _presence_valid(row):
    try:
        with Session(engine) as session:
            remember_request(session,row["session_hash"],row["claims"])
            recheck_actor(session,row["user_id"])
        return True
    except (HTTPException,KeyError,TypeError): return False


def _local_presence(user_id):
    row=presence_store.get(user_id,{"status":"offline","last_seen":None})
    if "sessions" not in row:return row
    valid=[value for value in list(row["sessions"].values()) if _presence_valid(value)]
    if not valid:return {"status":"offline","last_seen":row.get("last_seen")}
    latest=max(valid,key=lambda value:value["changed_at"])
    return {"status":latest["status"],"last_seen":latest["last_seen"]}


async def start_realtime_broker():
    global _presence_task
    shared=broker()
    if shared is None:return
    if shared.listener is not None:return
    async def deliver(envelope):
        if envelope["target"]=="channel":
            await broadcast_to_channel(envelope["id"],envelope["event"],envelope["data"],local_only=True,skip_sid=envelope.get("skip"))
        elif envelope["target"]=="user":
            await send_to_user(envelope["id"],envelope["event"],envelope["data"],local_only=True)
        else:await broadcast_presence(envelope["id"],envelope["data"],local_only=True)
    await shared.start(deliver)
    async def heartbeat():
        while True:
            await asyncio.sleep(max(10,shared.runtime.lease//3))
            for user_id,row in list(presence_store.items()):
                for sid,metadata in list(row.get("sessions",{}).items()):
                    try:
                        if not await _active_user(sid):
                            await shared.remove_presence(sid,user_id)
                            row["sessions"].pop(sid,None)
                            await sio.disconnect(sid)
                        else:
                            metadata["last_seen"]=datetime.utcnow().isoformat()
                            await shared.write_presence(sid,metadata)
                    except HTTPException:
                        # A broker outage never creates an unshared online
                        # lease; existing leases expire until recovery.
                        continue
    _presence_task=asyncio.create_task(heartbeat(),name="freecollab-presence-leases")


async def stop_realtime_broker():
    global _presence_task
    if _presence_task:
        _presence_task.cancel()
        with suppress(asyncio.CancelledError):await _presence_task
        _presence_task=None
    shared=broker()
    if shared:
        for user_id,row in list(presence_store.items()):
            for sid in list(row.get("sessions",{})):
                with suppress(HTTPException):await shared.remove_presence(sid,user_id)
        await shared.stop()
        shared.listener=None


def realtime_broker_ready():
    shared=broker()
    return shared is None or bool(shared.ready and shared.listener and not shared.listener.done())


async def _publish(shared,target,identifier,event,data,*,skip=None):
    try:await shared.publish(target,identifier,event,data,skip=skip)
    except HTTPException:
        # Native mutations have often already committed. Their authoritative
        # history/inbox and local authorized delivery remain available; a
        # transient fanout failure must not invite a non-idempotent retry.
        shared.ready=False


def _channel_id(data):
    # The browser SDK sends objects; retain the original string API for clients.
    value = data.get("channel_id") if isinstance(data, dict) else data
    if not isinstance(value, str) or not value or len(value) > 128:
        return None
    # Personal notification rooms must never be client-selectable.
    if value.startswith("user:"):
        return None
    return value


async def _active_user(sid):
    try:
        saved = await sio.get_session(sid)
    except KeyError:
        return None
    token = saved.get("token")
    if not isinstance(token, str) or not isinstance(saved.get("user_id"), str):
        return None
    return await asyncio.to_thread(_token_user_id, token, saved.get("user_id"))


def _token_user_id(token, expected_user_id=None):
    # A pool checkout may wait for a REST request to finish. Keep the complete
    # SQL unit in one worker, so that wait never prevents event-loop cleanup.
    with Session(engine) as session:
        user = active_user_for_token(token, session)
        return user.id if user and (expected_user_id is None or user.id == expected_user_id) else None


def _member_in_session(session, user_id, channel_id):
    if not isinstance(user_id,str) or not isinstance(channel_id,str) or not channel_id or len(channel_id)>128:return False
    return bool(
        session.get(Channel, channel_id)
        and session.get(ChannelMember, (channel_id, user_id))
        and workspace_allows_channel(session, channel_id, user_id)
    )


def _is_member(user_id, channel_id, token=None):
    if not isinstance(user_id,str) or not isinstance(channel_id,str) or not channel_id or len(channel_id)>128:return False
    with Session(engine) as session:
        if token is not None:
            actor = active_user_for_token(token, session)
            if actor is None or actor.id != user_id:
                return False
        return _member_in_session(session, user_id, channel_id)


def _channel_user_id(saved, channel_id):
    token, user_id = saved.get("token"), saved.get("user_id")
    if not isinstance(token, str) or not isinstance(user_id, str):return None
    # Bind the token's current source/workspace scope in the same session as
    # membership checks. No ORM object or session escapes this worker.
    return user_id if _is_member(user_id, channel_id, token) else None


async def _authorize_channel(sid, data):
    channel_id = _channel_id(data)
    if not channel_id:return None
    try:saved = await sio.get_session(sid)
    except KeyError:return None
    user_id = await asyncio.to_thread(_channel_user_id, saved, channel_id)
    if not user_id:return None
    return user_id, channel_id


@sio.event
async def connect(sid, environ, auth):
    token = auth.get("token") if isinstance(auth, dict) else None
    if not isinstance(token, str) or not token:
        raise socketio.exceptions.ConnectionRefusedError("Invalid token")
    user_id = await asyncio.to_thread(_token_user_id, token)
    if not user_id:
        raise socketio.exceptions.ConnectionRefusedError("Invalid user")

    await sio.save_session(sid, {"user_id": user_id, "token": token})
    metadata=_presence_metadata(user_id,token)
    shared=broker()
    if shared:
        try:await shared.write_presence(sid,metadata)
        except HTTPException:raise socketio.exceptions.ConnectionRefusedError("Presence coordination unavailable") from None
    current=presence_store.setdefault(user_id,{"sessions":{}})
    current.setdefault("sessions",{})[sid]=metadata
    current.update(status="online",last_seen=metadata["last_seen"],sid=sid)
    await sio.enter_room(sid, f"user:{user_id}")
    await broadcast_presence(user_id, {"user_id": user_id, "status": "online"})


@sio.event
async def disconnect(sid):
    try:
        saved = await sio.get_session(sid)
    except KeyError:
        return
    user_id = saved.get("user_id")
    if user_id:
        row=presence_store.get(user_id,{})
        row.get("sessions",{}).pop(sid,None)
        shared=broker()
        if shared:
            with suppress(HTTPException):await shared.remove_presence(sid,user_id)
            try:presence=await asyncio.to_thread(shared.runtime.presence,user_id,_presence_valid)
            except HTTPException:return
        else:presence=await asyncio.to_thread(_local_presence,user_id)
        row.update(status=presence["status"],last_seen=datetime.utcnow().isoformat(),sid=next(iter(row.get("sessions",{})),None))
        await broadcast_presence(user_id,{"user_id":user_id,**presence})


@sio.event
async def join(sid, data):
    authorized = await _authorize_channel(sid, data)
    if not authorized:
        return {"error": "Channel access denied"}
    await sio.enter_room(sid, authorized[1])
    return {"ok": True}


@sio.event
async def leave(sid, data):
    channel_id = _channel_id(data)
    if not channel_id or not await _active_user(sid):
        return {"error": "Channel access denied"}
    await sio.leave_room(sid, channel_id)
    return {"ok": True}


async def _typing_event(sid, data, event):
    authorized = await _authorize_channel(sid, data)
    if not authorized:
        return {"error": "Channel access denied"}
    user_id, channel_id = authorized
    try:
        await asyncio.to_thread(_require_writable, channel_id, user_id)
    except HTTPException:
        return {"error": "Messaging is unavailable for this conversation"}
    await broadcast_to_channel(channel_id,event,{"user_id":user_id,"channel_id":channel_id},skip_sid=sid)
    return {"ok": True}


def _require_writable(channel_id, user_id):
    with Session(engine) as session:
        require_direct_message_writable(session, channel_id, user_id)


@sio.event
async def typing(sid, data):
    return await _typing_event(sid, data, "typing")


@sio.event
async def stop_typing(sid, data):
    return await _typing_event(sid, data, "stop_typing")


@sio.event
async def set_status(sid, data):
    """Set custom status: online, away, busy, offline."""
    status = data.get("status") if isinstance(data, dict) else data
    if not isinstance(status, str) or status not in {"online", "away", "busy", "offline"}:
        return {"error": "Invalid status"}
    user_id = await _active_user(sid)
    if not user_id:
        return {"error": "Invalid user"}
    if user_id in presence_store:
        presence_store[user_id]["status"] = status
        presence_store[user_id]["last_seen"] = datetime.utcnow().isoformat()
        row=presence_store[user_id].get("sessions",{}).get(sid)
        if row:
            row.update(status=status,last_seen=datetime.utcnow().isoformat(),changed_at=datetime.utcnow().timestamp())
            shared=broker()
            if shared:await shared.write_presence(sid,row)
    await broadcast_presence(user_id, {"user_id": user_id, "status": status})
    return {"ok": True}


def get_user_presence(user_id: str) -> dict:
    """Get presence for a specific user."""
    shared=runtime()
    return shared.presence(user_id,_presence_valid) if shared else _local_presence(user_id)


def get_online_users(user_ids=None) -> list[str]:
    """Get list of online user IDs."""
    candidates=list(dict.fromkeys(user_ids if user_ids is not None else presence_store))
    if len(candidates)>1000:raise HTTPException(413,"Presence lookup is limited to 1,000 visible people")
    return [uid for uid in candidates if get_user_presence(uid)["status"]=="online"]


async def _prune_channel(channel_id):
    # Recheck recipients, including clients whose membership/account changed
    # after joining. A prior successful join is not a perpetual authorization.
    for sid, _ in list(sio.manager.get_participants("/", channel_id)):
        if not await _authorize_channel(sid, channel_id):
            await sio.leave_room(sid, channel_id)


async def broadcast_to_channel(channel_id: str, event: str, data: dict, *, local_only=False, skip_sid=None):
    shared=broker()
    if shared and not local_only:await _publish(shared,"channel",channel_id,event,data,skip=skip_sid)
    for sid,_ in list(sio.manager.get_participants("/",channel_id)):
        if sid==skip_sid:continue
        if not await _authorize_channel(sid,channel_id):
            await sio.leave_room(sid,channel_id)
        else:await sio.emit(event,data,room=sid)


def _scoped_delivery_allowed(session, user_id, event, data):
    if event == "presence":
        return data.get("user_id") in visible_user_ids(session, user_id)
    if event == "notification":
        # The payload contains retained notification text, so validate its actual
        # referenced record instead of trusting a caller-supplied action URL.
        from ..models.notification import Notification
        from ..api.notifications import notification_visible
        notification = session.get(Notification, data.get("id")) if data.get("id") else None
        return bool(notification and notification.user_id == user_id
                    and notification_visible(session, notification, user_id))
    if data.get("workspace_id"):
        return data["workspace_id"] == current_workspace_scope(session)
    if data.get("channel_id"):
        try:
            require_channel_scope(session, data["channel_id"])
            return True
        except HTTPException:
            return False
    return False


def _user_delivery_allowed(saved, user_id, event, data):
    with Session(engine) as session:
        actor = active_user_for_token(saved.get("token"), session)
        if actor is None or actor.id != user_id or saved.get("user_id") != user_id:
            return None
        scoped = current_workspace_scope(session) is not None
        if (event in {"notification","presence"} or scoped) and not _scoped_delivery_allowed(session,user_id,event,data):return False
        # Identifier-only revocation signals still reach an authenticated
        # account after membership removal. Content delivery needs membership.
        if event=="channel_removed" and set(data)-{"channel_id","workspace_id"}:return False
        if data.get("channel_id") and event!="channel_removed" and not _member_in_session(session,user_id,data["channel_id"]):return False
        return True


async def send_to_user(user_id: str, event: str, data: dict, *, local_only=False):
    """Authorize each browser; one account may have differently scoped logins."""
    room = f"user:{user_id}"
    shared=broker()
    if shared and not local_only:await _publish(shared,"user",user_id,event,data)
    for sid, _ in list(sio.manager.get_participants("/", room)):
        try:
            saved = await sio.get_session(sid)
        except KeyError:
            await sio.leave_room(sid, room)
            continue
        try:
            allowed = await asyncio.to_thread(_user_delivery_allowed, saved, user_id, event, data)
        except HTTPException:
            if event == "presence":
                # One denied browser does not suppress a sibling with a valid
                # workspace sign-in. Every socket still needs its own check.
                continue
            raise
        if allowed is None:
            await sio.leave_room(sid, room)
            continue
        if not allowed:continue
        # Never emit to the room as a fallback when there are zero recipients.
        await sio.emit(event,data,room=sid)


async def broadcast_presence(user_id: str, data: dict, *, local_only=False):
    """Recheck directional directory access before private-room delivery."""
    candidates = {identifier for identifier,row in presence_store.items() if row.get("sessions") or row.get("sid")} | {user_id}
    shared=broker()
    if shared and not local_only:await _publish(shared,"presence",user_id,"presence",data)
    # A candidate's directory cannot be checked in an unbound service session:
    # governed workspaces require that candidate's actual sign-in scope. The
    # delivery helper checks every current socket token and directional presence
    # visibility before emitting, including native and shared-channel sessions.
    for recipient in candidates:
        try:
            await send_to_user(recipient,"presence",data,local_only=True)
        except HTTPException:
            # A denied recipient must neither receive presence nor abort a
            # different participant's valid connection or disconnect.
            continue
