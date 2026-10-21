from datetime import datetime

from sqlmodel import Session

from app.models.message import Message
from app.services.crypto import encrypt_message
from .test_message_security import chat


def test_history_cursor_uses_chronology_and_deterministic_tie_breaker(chat):
    client, engine, users, headers, channel = chat
    with Session(engine) as session:
        for message_id, date in (("z-old", datetime(2000, 1, 1)), ("a-new", datetime(2001, 1, 1)), ("m-new", datetime(2001, 1, 1))):
            session.add(Message(id=message_id, channel_id=channel, author_id=users[0]["user_id"],
                                content=encrypt_message(message_id, channel), created_at=date))
        session.commit()
    cursor = None
    seen = []
    for _ in range(3):
        params = {"limit": 1}
        if cursor:
            params["before"] = cursor
        response = client.get(f"/messages/channels/{channel}", headers=headers[1], params=params)
        assert response.status_code == 200
        cursor = response.json()[0]["id"]
        seen.append(cursor)
    assert seen == ["m-new", "a-new", "z-old"]


def test_chat_rejects_empty_oversize_messages_and_unbounded_history(chat):
    client, _, _, headers, channel = chat
    for content in ("", "   ", "x" * 20001):
        assert client.post(f"/messages/channels/{channel}", headers=headers[0], json={"content": content}).status_code == 422
    for limit in (0, -1, 101):
        assert client.get(f"/messages/channels/{channel}?limit={limit}", headers=headers[0]).status_code == 422
