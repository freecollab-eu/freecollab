"""Channel isolation and encryption regression tests for the HTTP chat workflow."""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine
from sqlalchemy.pool import StaticPool

from app.database import get_session
from app.main import app
from app.models.channel import ChannelMember
from app.models.message import Message
from app.services.crypto import encrypt_message
from app.services.rate_limit import AuthRateLimiter


@pytest.fixture
def chat(monkeypatch):
    # Each fixture owns a fresh DB and users; give that isolated application
    # state its own limits too, preserving production limits and other tests.
    monkeypatch.setattr(app.state, "auth_rate_limiter", AuthRateLimiter(), raising=False)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    def session_override():
        with Session(engine) as session:
            yield session
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_session] = session_override
    client = TestClient(app)
    users = []
    for name in ("alice", "bob", "outsider"):
        r = client.post("/auth/register", json={"name": name, "email": f"{name}@example.com", "password": "local-test-pass"})
        assert r.status_code == 200
        users.append(r.json())
    headers = [{"Authorization": "Bearer " + u["access_token"]} for u in users]
    r = client.post("/channels", headers=headers[0], json={"name": "private", "member_ids": [users[1]["user_id"]]})
    assert r.status_code == 200
    yield client, engine, users, headers, r.json()["id"]
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)
    engine.dispose()


def test_two_members_exchange_encrypted_messages_and_thread_replies(chat):
    client, engine, _, headers, channel = chat
    sent = client.post(f"/messages/channels/{channel}", headers=headers[0], json={"content": "private content"})
    assert sent.status_code == 200
    message = sent.json()
    assert message["content"] == "private content"
    reply = client.post(f'/threads/messages/{message["id"]}/reply', headers=headers[1], json={"content": "private reply"})
    assert reply.status_code == 200
    with Session(engine) as session:
        assert session.get(Message, message["id"]).content != "private content"
        stored = session.get(Message, reply.json()["id"])
        assert stored.encrypted and stored.content != "private reply"
    history = client.get(f"/messages/channels/{channel}", headers=headers[1])
    assert {m["content"] for m in history.json()} == {"private content", "private reply"}
    thread = client.get(f'/threads/messages/{message["id"]}', headers=headers[0])
    assert thread.json()["replies"][0]["content"] == "private reply"
    assert client.get(f"/messages/channels/{channel}", headers=headers[2]).status_code == 403
    assert client.get(f'/threads/messages/{message["id"]}', headers=headers[2]).status_code == 403


def test_reply_cannot_reference_a_different_channel(chat):
    client, engine, _, headers, channel = chat
    parent = client.post(f"/messages/channels/{channel}", headers=headers[0], json={"content": "original"}).json()
    other = client.post("/channels", headers=headers[2], json={"name": "other", "member_ids": []}).json()["id"]
    response = client.post(f"/messages/channels/{other}", headers=headers[2], json={"content": "intrusion", "parent_id": parent["id"]})
    assert response.status_code == 404
    with Session(engine) as session:
        assert session.get(Message, parent["id"]).thread_count == 0


def test_removed_author_cannot_edit_or_delete(chat):
    client, engine, users, headers, channel = chat
    message = client.post(f"/messages/channels/{channel}", headers=headers[1], json={"content": "original"}).json()
    with Session(engine) as session:
        session.delete(session.get(ChannelMember, (channel, users[1]["user_id"])))
        session.commit()
    assert client.put(f'/messages/{message["id"]}', headers=headers[1], json={"content": "changed"}).status_code == 403
    assert client.delete(f'/messages/{message["id"]}', headers=headers[1]).status_code == 403


def test_unknown_channel_member_rejected(chat):
    client, _, _, headers, _ = chat
    response = client.post("/channels", headers=headers[0], json={"name": "invalid", "member_ids": ["missing-user"]})
    assert response.status_code == 400


def test_legacy_cross_channel_reply_is_not_disclosed(chat):
    client, engine, users, headers, channel = chat
    parent = client.post(f"/messages/channels/{channel}", headers=headers[0], json={"content": "original"}).json()
    other = client.post("/channels", headers=headers[2], json={"name": "other", "member_ids": []}).json()["id"]
    # Older versions accepted a parent ID from a different channel.
    with Session(engine) as session:
        session.add(Message(
            id="legacy-cross-channel-reply", channel_id=other,
            author_id=users[2]["user_id"], parent_id=parent["id"],
            content=encrypt_message("other channel secret", other), encrypted=True,
        ))
        session.commit()
    response = client.get(f'/threads/messages/{parent["id"]}', headers=headers[0])
    assert response.status_code == 200
    assert response.json()["replies"] == []
    assert "other channel secret" not in response.text


def test_legacy_invalid_encrypted_thread_reply_returns_controlled_error(chat):
    client, engine, users, headers, channel = chat
    parent = client.post(f"/messages/channels/{channel}", headers=headers[0], json={"content": "original"}).json()
    with Session(engine) as session:
        session.add(Message(
            id="legacy-invalid-encrypted-reply", channel_id=channel,
            author_id=users[0]["user_id"], parent_id=parent["id"],
            content="plaintext incorrectly marked encrypted", encrypted=True,
        ))
        session.commit()
    response = client.get(f'/threads/messages/{parent["id"]}', headers=headers[0])
    assert response.status_code == 409
    assert response.json()["detail"] == "Stored thread reply could not be decrypted"
    assert "plaintext incorrectly marked encrypted" not in response.text
