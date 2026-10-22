"""Real bounded-pool regressions for socket auth and committed chat fanout."""
import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.security import HTTPAuthorizationCredentials
from sqlmodel import SQLModel, Session, create_engine, select

from app.api import messages, push
from app.models.channel import Channel, ChannelMember
from app.models.message import Message
from app.models.notification import Notification
from app.models.user import User
from app.realtime import socket
from app.services.auth import create_token
from app.services.crypto import encrypt_message


@pytest.fixture
def realtime_pool(tmp_path, monkeypatch):
    # One connection exposes both event-loop blocking and nested checkouts.
    # Production pool limits are unchanged.
    engine = create_engine(f"sqlite:///{tmp_path / 'realtime-pool.db'}",
                           pool_size=1, max_overflow=0, pool_timeout=.5)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([
            User(id=uid, name=uid, email=f"{uid}@example.com", password_hash="unused")
            for uid in ("author", "recipient")
        ])
        session.add(Channel(id="channel", name="Private"))
        session.add_all([ChannelMember(channel_id="channel", user_id=uid)
                         for uid in ("author", "recipient")])
        session.commit()
    saved = {uid: {"user_id": uid, "token": create_token(uid)} for uid in ("author", "recipient")}
    sio = Mock()
    sio.get_session = AsyncMock(side_effect=lambda sid: saved[sid])
    sio.emit = AsyncMock()
    sio.leave_room = AsyncMock()
    sio.manager.get_participants.side_effect = lambda namespace, room: (
        [("recipient", "transport")] if room in {"channel", "user:recipient"} else [])
    monkeypatch.setattr(socket, "engine", engine)
    monkeypatch.setattr(socket, "sio", sio)
    monkeypatch.setattr(socket, "broker", lambda: None)
    yield engine, sio, saved
    engine.dispose()


@pytest.mark.parametrize("operation", ["active_user", "channel", "personal_channel"])
def test_pool_wait_leaves_event_loop_free_to_return_a_connection(realtime_pool, operation):
    engine, sio, _ = realtime_pool

    async def exercise():
        held = engine.connect()
        released = asyncio.Event()

        async def release_on_event_loop():
            await asyncio.sleep(.02)
            held.close()
            released.set()

        release = asyncio.create_task(release_on_event_loop())
        try:
            if operation == "active_user":
                assert await socket._active_user("recipient") == "recipient"
            elif operation == "channel":
                await socket.broadcast_to_channel("channel", "message", {"content": "permitted"})
            else:
                await socket.send_to_user("recipient", "message", {"channel_id": "channel"})
            assert released.is_set()
        finally:
            held.close()
            await release

    asyncio.run(exercise())
    if operation != "active_user":
        sio.emit.assert_awaited_once()
        assert sio.emit.await_args.kwargs == {"room": "recipient"}


@pytest.mark.parametrize("operation,event", [
    ("send", "message"), ("edit", "message_edited"),
    ("delete", "message_deleted"), ("offline", "message"),
])
def test_committed_chat_releases_connection_before_current_recipient_checks(realtime_pool, operation, event):
    engine, sio, saved = realtime_pool
    with Session(engine) as session:
        session.add(Message(id="original", channel_id="channel", author_id="author",
                            content=encrypt_message("original", "channel"), encrypted=True))
        session.commit()
        actor = session.get(User, "author")
        if operation == "send":
            result = asyncio.run(messages.send_message(
                "channel", messages.SendMessage(content="new message", mentions=["recipient"]), actor, session))
        elif operation == "edit":
            result = asyncio.run(messages.edit_message(
                "original", messages.EditMessage(content="edited message", mentions=["recipient"]), actor, session))
        elif operation == "delete":
            result = asyncio.run(messages.delete_message("original", actor, session))
        else:
            credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=saved["author"]["token"])
            result = asyncio.run(push.offline_message(
                "channel", push.OfflineMessageInput(client_id="one-queued-message", content="offline message",
                                                    mentions=["recipient"]), actor, credentials, session))
        # Both durable return values and fanout must survive commit expiration.
        assert result["id"]
        assert not session.in_transaction()
    delivered = [args.args[0] for args in sio.emit.await_args_list]
    assert delivered.count(event) == 1
    assert delivered.count("notification") == (0 if operation == "delete" else 1)
    with Session(engine) as session:
        rows = session.exec(select(Notification)).all()
        assert len(rows) == (0 if operation == "delete" else 1)
        assert session.get(Message, result["id"]) is not None
