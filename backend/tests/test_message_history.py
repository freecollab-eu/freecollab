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


def test_thread_pages_expose_all_older_replies_with_equal_timestamp_without_skips(chat):
    client, engine, users, headers, channel = chat
    parent = client.post('/messages/channels/' + channel, headers=headers[0], json={'content': 'Thread root'}).json()
    expected = [f'reply-{index:03}' for index in range(105)]
    with Session(engine) as session:
        for identifier in expected:
            session.add(Message(id=identifier, channel_id=channel, author_id=users[0]['user_id'],
                parent_id=parent['id'], created_at=datetime(2026, 10, 23, 10), encrypted=True,
                content=encrypt_message(identifier, channel)))
        row = session.get(Message, parent['id']); row.thread_count = len(expected); session.add(row); session.commit()
    before, seen, sizes = None, [], []
    while True:
        page = client.get('/threads/messages/' + parent['id'], headers=headers[1], params={'limit': 50, **({'before': before} if before else {})})
        assert page.status_code == 200
        body = page.json(); identifiers = [reply['id'] for reply in body['replies']]
        assert identifiers == sorted(identifiers) and all(reply['parent_id'] == parent['id'] for reply in body['replies'])
        seen = identifiers + seen; sizes.append(len(identifiers))
        if not body['has_more']: break
        before = identifiers[0]
    assert sizes == [50, 50, 5] and seen == expected
    assert client.get('/threads/messages/' + parent['id'], headers=headers[1], params={'before': parent['id']}).status_code == 404


def test_busy_thread_replies_cannot_crowd_roots_out_of_opted_in_main_history(chat):
    client, engine, users, headers, channel = chat
    with Session(engine) as session:
        root = Message(id='root', channel_id=channel, author_id=users[0]['user_id'], content=encrypt_message('Visible root', channel), encrypted=True, created_at=datetime(2020, 1, 1), thread_count=105)
        session.add(root)
        for index in range(105):
            session.add(Message(id=f'reply-{index:03}', channel_id=channel, author_id=users[0]['user_id'], parent_id='root', encrypted=True, content=encrypt_message('Reply', channel), created_at=datetime(2026, 1, 1)))
        session.commit()
    all_history = client.get('/messages/channels/' + channel, headers=headers[1], params={'limit': 50}).json()
    assert len(all_history) == 50 and all(message['parent_id'] == 'root' for message in all_history)
    roots = client.get('/messages/channels/' + channel, headers=headers[1], params={'limit': 50, 'roots_only': 'true'}).json()
    assert len(roots) == 1 and roots[0]['id'] == 'root' and roots[0]['thread_count'] == 105
