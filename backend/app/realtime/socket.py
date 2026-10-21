import socketio
from datetime import datetime
from sqlmodel import Session
from fastapi import HTTPException
from ..database import engine
from ..config import settings
from ..models.channel import Channel, ChannelMember
from ..models.user import User
from ..services.auth import active_user_for_token
from ..services.sso_scope import current_workspace_scope, require_channel_scope
from ..services.workspaces import workspace_allows_channel, visible_user_ids, require_direct_message_writable

sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins=settings.cors_origins)

# In-memory presence store (use Redis in production)
presence_store: dict[str, dict] = {}  # user_id -> {status, last_seen, sid}


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
    if not isinstance(token, str):
        return None
    with Session(engine) as session:
        user = active_user_for_token(token, session)
        return user.id if user and user.id == saved.get("user_id") else None


def _is_member(user_id, channel_id, token=None):
    with Session(engine) as session:
        if token is not None:
            actor = active_user_for_token(token, session)
            if actor is None or actor.id != user_id:
                return False
        return bool(
            session.get(Channel, channel_id)
            and session.get(ChannelMember, (channel_id, user_id))
            and workspace_allows_channel(session, channel_id, user_id)
        )


async def _authorize_channel(sid, data):
    channel_id = _channel_id(data)
    user_id = await _active_user(sid)
    saved = await sio.get_session(sid) if user_id else {}
    if not channel_id or not user_id or not _is_member(user_id, channel_id, saved.get("token")):
        return None
    return user_id, channel_id


@sio.event
async def connect(sid, environ, auth):
    token = auth.get("token") if isinstance(auth, dict) else None
    if not isinstance(token, str) or not token:
        raise socketio.exceptions.ConnectionRefusedError("Invalid token")
    with Session(engine) as session:
        user = active_user_for_token(token, session)
        if not user:
            raise socketio.exceptions.ConnectionRefusedError("Invalid user")
        user_id = user.id

    await sio.save_session(sid, {"user_id": user_id, "token": token})
    presence_store[user_id] = {
        "status": "online",
        "last_seen": datetime.utcnow().isoformat(),
        "sid": sid,
    }
    await sio.enter_room(sid, f"user:{user_id}")
    await broadcast_presence(user_id, {"user_id": user_id, "status": "online"})


@sio.event
async def disconnect(sid):
    try:
        saved = await sio.get_session(sid)
    except KeyError:
        return
    user_id = saved.get("user_id")
    if user_id and presence_store.get(user_id, {}).get("sid") == sid:
        presence_store[user_id] = {
            "status": "offline",
            "last_seen": datetime.utcnow().isoformat(),
            "sid": None,
        }
        await broadcast_presence(user_id, {"user_id": user_id, "status": "offline", "last_seen": presence_store[user_id]["last_seen"]})


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
        with Session(engine) as session:
            require_direct_message_writable(session, channel_id, user_id)
    except HTTPException:
        return {"error": "Messaging is unavailable for this conversation"}
    await _prune_channel(channel_id)
    await sio.emit(event, {"user_id": user_id, "channel_id": channel_id}, room=channel_id, skip_sid=sid)
    return {"ok": True}


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
    await broadcast_presence(user_id, {"user_id": user_id, "status": status})
    return {"ok": True}


def get_user_presence(user_id: str) -> dict:
    """Get presence for a specific user."""
    return presence_store.get(user_id, {"status": "offline", "last_seen": None})


def get_online_users() -> list[str]:
    """Get list of online user IDs."""
    return [uid for uid, data in presence_store.items() if data["status"] == "online"]


async def _prune_channel(channel_id):
    # Recheck recipients, including clients whose membership/account changed
    # after joining. A prior successful join is not a perpetual authorization.
    for sid, _ in list(sio.manager.get_participants("/", channel_id)):
        user_id = await _active_user(sid)
        saved = await sio.get_session(sid) if user_id else {}
        if not user_id or not _is_member(user_id, channel_id, saved.get("token")):
            await sio.leave_room(sid, channel_id)


async def broadcast_to_channel(channel_id: str, event: str, data: dict):
    await _prune_channel(channel_id)
    await sio.emit(event, data, room=channel_id)


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


async def send_to_user(user_id: str, event: str, data: dict):
    """Authorize each browser; one account may have differently scoped logins."""
    room = f"user:{user_id}"
    recipients = []
    has_scoped_recipient = False
    for sid, _ in list(sio.manager.get_participants("/", room)):
        try:
            saved = await sio.get_session(sid)
        except KeyError:
            await sio.leave_room(sid, room)
            continue
        with Session(engine) as session:
            actor = active_user_for_token(saved.get("token"), session)
            if actor is None or actor.id != user_id or saved.get("user_id") != user_id:
                await sio.leave_room(sid, room)
                continue
            scoped = current_workspace_scope(session) is not None
            has_scoped_recipient |= scoped
            if not scoped or _scoped_delivery_allowed(session, user_id, event, data):
                recipients.append(sid)
    if has_scoped_recipient:
        for sid in recipients:
            await sio.emit(event, data, room=sid)
    else:
        await sio.emit(event, data, room=room)


async def broadcast_presence(user_id: str, data: dict):
    """Recheck directional directory access before private-room delivery."""
    candidates = set(presence_store) | {user_id}
    with Session(engine) as session:
        recipients = [candidate for candidate in candidates if user_id in visible_user_ids(session, candidate)]
    for recipient in recipients:
        await send_to_user(recipient, "presence", data)
