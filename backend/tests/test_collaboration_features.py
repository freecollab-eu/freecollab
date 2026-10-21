"""Collaboration workflows preserve membership, encrypted storage and delivery rules."""
import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

from app.api import decisions, mentions, messages, notifications, reactions, saved_items, threads
from app.database import get_session
from app.models.channel import Channel, ChannelMember
from app.models.collaboration import ChannelNotificationSetting, DecisionRevision, EncryptedMessageActivity, EncryptedThreadSubscription, NotificationDelivery
from app.models.message import Message
from app.models.direct_message import UserBlock
from app.models.notification import Notification, NotificationPreference
from app.models.secure_channel import SecureChannel, MatrixAccount
from app.models.user import User
from app.services.auth import create_token
from app.services.chat_notifications import notify_chat_message, queue_due_digests


@pytest.fixture
def collaboration(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([User(id=name, name=name.title(), email=f"{name}@example.com", password_hash="unused")
                         for name in ("owner", "member", "watcher", "outsider")])
        session.commit()
        session.add_all([Channel(id="private", name="Private"), Channel(id="other", name="Other")])
        session.commit()
        session.add_all([ChannelMember(channel_id="private", user_id=name, role="admin" if name == "owner" else "member")
                         for name in ("owner", "member", "watcher")])
        session.add(ChannelMember(channel_id="other", user_id="outsider", role="admin"))
        session.commit()
    app = FastAPI()
    for module in (messages, threads, mentions, notifications, reactions, saved_items, decisions):
        app.include_router(module.router)
    def sessions():
        with Session(engine) as session:
            yield session
    app.dependency_overrides[get_session] = sessions
    delivery = AsyncMock()
    monkeypatch.setattr(notifications, "send_to_user", delivery)
    monkeypatch.setattr(messages, "broadcast_to_channel", AsyncMock())
    monkeypatch.setattr(threads, "broadcast_to_channel", AsyncMock())
    monkeypatch.setattr(reactions, "broadcast_to_channel", AsyncMock())
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, engine=engine, delivery=delivery)
    engine.dispose()


def bearer(name="owner"):
    return {"Authorization": "Bearer " + create_token(name)}


def send(api, content="A private message", user="owner", **fields):
    response = api.client.post("/messages/channels/private", json={"content": content, **fields}, headers=bearer(user))
    assert response.status_code == 200, response.text
    return response.json()


def test_member_id_mentions_validate_and_deliver_once_without_content_copy(collaboration):
    api = collaboration
    bad = api.client.post("/messages/channels/private", json={"content": "Invalid", "mentions": ["outsider"]}, headers=bearer())
    assert bad.status_code == 400
    message = send(api, "Sensitive exact words @Member", mentions=["member", "member", "owner"])
    assert message["mentions"] == ["member", "owner"]
    assert api.client.get("/mentions/me", headers=bearer("member")).json()[0]["id"] == message["id"]
    assert api.client.get("/mentions/me", headers=bearer("watcher")).json() == []
    members = api.client.get("/mentions/channels/private/members?query=MEM", headers=bearer()).json()
    assert [person["id"] for person in members] == ["member"]
    assert api.client.get("/mentions/channels/private/members", headers=bearer("outsider")).status_code == 403
    assert api.client.put(f'/messages/{message["id"]}', json={"content": "Edited private words"}, headers=bearer()).status_code == 200
    with Session(api.engine) as session:
        stored = session.get(Message, message["id"])
        assert "private words" not in stored.content
        asyncio.run(notify_chat_message(session, stored))
        inbox = session.exec(select(Notification)).all()
        assert len(inbox) == 1 and inbox[0].user_id == "member"
        notification_id = inbox[0].id
        assert "Sensitive" not in inbox[0].body and "Edited" not in inbox[0].body
        assert len(session.exec(select(NotificationDelivery)).all()) == 1
    assert api.delivery.await_count == 1
    assert api.client.get("/notifications?before=" + notification_id, headers=bearer()).status_code == 404


def test_channel_and_global_preferences_thread_watch_and_self_ignore(collaboration):
    api = collaboration
    parent = send(api)
    assert api.client.put("/notifications/channels/private/settings", json={"level": "all"}, headers=bearer("member")).status_code == 200
    assert api.client.put("/notifications/channels/private/settings", json={"level": "muted"}, headers=bearer("watcher")).status_code == 200
    send(api, mentions=["watcher"])
    assert len(api.client.get("/notifications", headers=bearer("member")).json()) == 1
    assert api.client.get("/notifications", headers=bearer("watcher")).json() == []
    path = f'/threads/messages/{parent["id"]}/subscription'
    assert api.client.get(path, headers=bearer()).json()["watching"] is True
    assert api.client.put(path, json={"watching": False}, headers=bearer()).status_code == 200
    assert api.client.put(path, json={"watching": True}, headers=bearer("watcher")).status_code == 200
    # Muting still wins over a watched thread and an explicit mention.
    reply = api.client.post(f'/threads/messages/{parent["id"]}/reply', json={"content": "Reply", "mentions": ["owner", "watcher"]}, headers=bearer("member"))
    assert reply.status_code == 200
    owner_inbox = api.client.get("/notifications", headers=bearer()).json()
    assert len(owner_inbox) == 1 and owner_inbox[0]["type"] == "mention"
    assert api.client.get("/notifications", headers=bearer("watcher")).json() == []
    assert api.client.put("/notifications/preferences", json={"mentions": False}, headers=bearer()).status_code == 200
    send(api, user="member", mentions=["owner", "member"])
    assert len(api.client.get("/notifications", headers=bearer()).json()) == 1
    assert api.client.put(path, json={"watching": True}, headers=bearer("outsider")).status_code == 403


def test_sync_replays_edits_tombstones_and_thread_counts_with_scoped_cursor(collaboration):
    api = collaboration
    first = send(api, "Original")
    initial = api.client.get("/messages/channels/private/sync?limit=1", headers=bearer("member")).json()
    assert initial["messages"][0]["content"] == "Original"
    assert not initial["has_more"]
    second = send(api, "Second")
    api.client.put(f'/messages/{first["id"]}', json={"content": "Changed"}, headers=bearer())
    api.client.delete(f'/messages/{second["id"]}', headers=bearer())
    after = initial["next_cursor"]
    accumulated = {}
    while True:
        page = api.client.get(f"/messages/channels/private/sync?after={after}&limit=1", headers=bearer("member")).json()
        accumulated.update({message["id"]: message for message in page["messages"]})
        after = page["next_cursor"]
        if not page["has_more"]:
            break
    assert accumulated[first["id"]]["content"] == "Changed"
    assert accumulated[second["id"]]["deleted"] is True
    assert accumulated[second["id"]]["content"] == "[Message deleted]"
    send(api, "Thread reply", parent_id=first["id"])
    delta = api.client.get(f"/messages/channels/private/sync?after={after}", headers=bearer()).json()
    assert next(message for message in delta["messages"] if message["id"] == first["id"])["thread_count"] == 1
    assert api.client.get("/messages/channels/private/sync", headers=bearer("outsider")).status_code == 403
    assert api.client.get("/messages/channels/other/sync?after=9999", headers=bearer("outsider")).status_code == 400


def test_pins_are_shared_admin_managed_bookmarks_private_and_revocation_hides_content(collaboration):
    api = collaboration
    message = send(api)
    pin_path = "/channels/private/pins"
    assert api.client.post(pin_path, json={"message_id": message["id"]}, headers=bearer("member")).status_code == 403
    assert api.client.post(pin_path, json={"message_id": message["id"]}, headers=bearer()).status_code == 200
    assert api.client.get(pin_path, headers=bearer("member")).json()[0]["message"]["content"] == "A private message"
    body = {"channel_id": "private", "message_id": message["id"]}
    assert api.client.post("/bookmarks", json=body, headers=bearer("member")).status_code == 200
    assert api.client.get("/bookmarks", headers=bearer()).json() == []
    assert api.client.post("/bookmarks", json=body, headers=bearer("outsider")).status_code == 403
    # Cross-channel pointers cannot turn a shared pin into a disclosure.
    assert api.client.post("/channels/other/pins", json={"message_id": message["id"]}, headers=bearer("outsider")).status_code == 404
    with Session(api.engine) as session:
        session.delete(session.get(ChannelMember, ("private", "member")))
        session.commit()
    assert api.client.get("/bookmarks", headers=bearer("member")).json() == []
    assert api.client.get(pin_path, headers=bearer("member")).status_code == 403
    assert api.client.delete(f'/bookmarks/private/{message["id"]}', headers=bearer("member")).status_code == 200
    api.client.delete(f'/messages/{message["id"]}', headers=bearer())
    assert api.client.get(pin_path, headers=bearer()).json()[0]["available"] is False


def test_decisions_have_encrypted_immutable_revisions_and_optimistic_conflicts(collaboration):
    api = collaboration
    source = send(api)
    body = {"title": "Private decision title", "content": "Private decision details", "status": "accepted",
            "responsible_user_id": "member", "decision_date": "2026-10-20", "source_message_id": source["id"]}
    response = api.client.post("/channels/private/decisions", json=body, headers=bearer("member"))
    assert response.status_code == 200, response.text
    decision = response.json()
    path = f'/decisions/{decision["id"]}'
    assert api.client.get(path, headers=bearer("outsider")).status_code == 403
    assert api.client.post(path + "/revisions", json={**body, "expected_revision": 1}, headers=bearer("watcher")).status_code == 403
    revision = api.client.post(path + "/revisions", json={**body, "content": "Revised details", "expected_revision": 1}, headers=bearer())
    assert revision.status_code == 200 and revision.json()["revision"] == 2
    assert api.client.post(path + "/revisions", json={**body, "expected_revision": 1}, headers=bearer()).status_code == 409
    history = api.client.get(path + "/revisions", headers=bearer("watcher")).json()
    assert [item["content"] for item in history] == ["Revised details", "Private decision details"]
    with Session(api.engine) as session:
        rows = session.exec(select(DecisionRevision)).all()
        assert len(rows) == 2 and all("Private" not in row.encrypted_payload for row in rows)
    assert api.client.post("/channels/private/decisions", json={**body, "responsible_user_id": "outsider"}, headers=bearer()).status_code == 400
    with Session(api.engine) as session:
        session.delete(session.get(ChannelMember, ("private", "member")))
        session.commit()
    assert api.client.get(path + "/revisions", headers=bearer("member")).status_code == 403


def test_secure_binding_refuses_native_content_and_uses_only_opaque_channel_references(collaboration, monkeypatch):
    api = collaboration
    legacy = send(api, "Legacy secret", mentions=["member"])
    api.client.post("/channels/private/pins", json={"message_id": legacy["id"]}, headers=bearer())
    old_decision = api.client.post("/channels/private/decisions", json={"title": "Legacy title", "content": "Legacy details"}, headers=bearer()).json()
    with Session(api.engine) as session:
        session.add(SecureChannel(channel_id="private", created_by="owner", status="preparing"))
        session.commit()
    assert api.client.post("/messages/channels/private", json={"content": "Must never persist"}, headers=bearer()).status_code == 409
    assert api.client.get("/messages/channels/private", headers=bearer()).status_code == 409
    assert api.client.get("/messages/channels/private/sync", headers=bearer()).status_code == 409
    assert api.client.get("/mentions/me", headers=bearer("member")).json() == []
    assert api.client.post(f'/threads/messages/{legacy["id"]}/reply', json={"content": "No plaintext reply"}, headers=bearer()).status_code == 409
    assert api.client.get(f'/decisions/{old_decision["id"]}', headers=bearer()).status_code == 409
    assert api.client.get("/channels/private/pins", headers=bearer()).json()[0]["available"] is False
    assert api.client.post("/channels/private/decisions", json={"title": "No title", "content": "No plaintext"}, headers=bearer()).status_code == 409
    monkeypatch.setattr("app.services.matrix_bridge.validate_secure_event", lambda *args, **kwargs: {"type": "m.room.encrypted", "sender": "@fc_owner:example", "room_id": "!private:example"})
    secure = api.client.post("/channels/private/decisions", json={"matrix_event_id": "$encrypted-event"}, headers=bearer()).json()
    assert secure["content_mode"] == "matrix" and "content" not in secure
    assert api.client.post("/channels/private/decisions", json={"matrix_event_id": "$event", "title": "Leaky title"}, headers=bearer()).status_code == 422
    pin = api.client.post("/channels/private/pins", json={"matrix_event_id": "$encrypted-event"}, headers=bearer())
    assert pin.status_code == 200 and "message" not in pin.json()
    subscription = "/threads/channels/private/events/$encrypted-thread/subscription"
    assert api.client.get(subscription, headers=bearer("member")).json()["watching"] is False
    assert api.client.put(subscription, json={"watching": True}, headers=bearer("member")).json()["watching"] is True
    assert api.client.get(subscription, headers=bearer()).json()["watching"] is False
    assert api.client.put(subscription, json={"watching": True}, headers=bearer("outsider")).status_code == 403
    assert api.client.get("/threads/channels/other/events/$encrypted-thread/subscription", headers=bearer("outsider")).status_code == 400
    assert api.client.post("/channels/other/pins", json={"matrix_event_id": "$encrypted-event"}, headers=bearer("outsider")).status_code == 400
    with Session(api.engine) as session:
        stored = session.get(DecisionRevision, secure["revision_id"])
        assert stored.encrypted_payload is None
        assert len(session.exec(select(Message)).all()) == 1


def test_removed_people_receive_no_mentions_notifications_or_sync(collaboration):
    api = collaboration
    send(api, "Private activity title must disappear", mentions=["member"])
    assert len(api.client.get("/notifications", headers=bearer("member")).json()) == 1
    with Session(api.engine) as session:
        session.delete(session.get(ChannelMember, ("private", "member")))
        session.commit()
    assert api.client.put("/notifications/channels/private/settings", json={"level": "all"}, headers=bearer("member")).status_code == 403
    send(api)
    assert api.client.get("/notifications", headers=bearer("member")).json() == []
    assert api.client.get("/notifications/unread-count", headers=bearer("member")).json()["unread_count"] == 0
    assert api.client.get("/messages/channels/private/sync", headers=bearer("member")).status_code == 403


def test_digest_is_opt_in_generic_idempotent_and_member_scoped(collaboration, monkeypatch):
    api = collaboration
    monkeypatch.setattr("app.services.mail.mail_available", lambda: True)
    now = datetime.utcnow()
    with Session(api.engine) as session:
        session.add(ChannelNotificationSetting(user_id="member", channel_id="private", email_digest="daily", digest_since=now - timedelta(days=2)))
        session.add(ChannelNotificationSetting(user_id="outsider", channel_id="private", email_digest="daily", digest_since=now - timedelta(days=2)))
        session.commit()
    message = send(api, "Never include these private words")
    queued = []
    def enqueue(session, recipient, subject, body):
        queued.append((recipient, subject, body))
    with Session(api.engine) as session:
        assert queue_due_digests(session, enqueue, "https://freecollab.example", now=now + timedelta(seconds=30)) == 1
        assert queue_due_digests(session, enqueue, "https://freecollab.example", now=now + timedelta(seconds=60)) == 0
    assert len(queued) == 1 and queued[0][0] == "member@example.com"
    assert "https://freecollab.example" in queued[0][2] and "1 new messages" in queued[0][2]
    assert message["content"] not in queued[0][2]
    with Session(api.engine) as session:
        session.add(NotificationPreference(user_id="member", email=False))
        session.commit()
        assert queue_due_digests(session, enqueue, "https://freecollab.example", now=now + timedelta(days=4)) == 0
    monkeypatch.setattr("app.services.mail.mail_available", lambda: False)
    with Session(api.engine) as session:
        assert queue_due_digests(session, enqueue, "https://freecollab.example", now=now + timedelta(days=4)) == 0


def test_failed_digest_enqueue_does_not_advance_cursor(collaboration, monkeypatch):
    api = collaboration
    monkeypatch.setattr("app.services.mail.mail_available", lambda: True)
    since = datetime.utcnow() - timedelta(days=2)
    with Session(api.engine) as session:
        session.add(ChannelNotificationSetting(user_id="member", channel_id="private", email_digest="daily", digest_since=since))
        session.commit()
    send(api)
    def fail(*_):
        raise RuntimeError("Queue unavailable")
    with Session(api.engine) as session:
        with pytest.raises(RuntimeError):
            queue_due_digests(session, fail, "https://freecollab.example")
    with Session(api.engine) as session:
        assert session.get(ChannelNotificationSetting, ("member", "private")).digest_last_queued_at is None


def test_encrypted_activity_verifies_envelope_honors_watches_preferences_and_deduplicates(collaboration, monkeypatch):
    api = collaboration
    matrix_events = {}
    with Session(api.engine) as session:
        session.add(SecureChannel(channel_id="private", matrix_room_id="!private:example", created_by="owner", status="active"))
        for user_id in ("owner", "member", "watcher"):
            session.add(MatrixAccount(user_id=user_id, matrix_user_id=f"@fc_{user_id}:example", encrypted_password="unused"))
        session.add(ChannelNotificationSetting(user_id="watcher", channel_id="private", level="muted"))
        session.commit()
    def validate(session, channel_id, event_id, user_id, *, require_sender=True):
        event = matrix_events.get(event_id)
        if not event or event["room_id"] != "!" + channel_id + ":example" or (require_sender and event["sender"] != f"@fc_{user_id}:example"):
            raise HTTPException(400, "Envelope proof rejected")
        return event
    monkeypatch.setattr("app.services.matrix_bridge.validate_secure_event", validate)
    def acknowledge(event_id, user="owner", mentions=None, parent=None):
        matrix_events.setdefault(event_id, {"type": "m.room.encrypted", "room_id": "!private:example", "sender": f"@fc_{user}:example", "content": {"ciphertext": "opaque encrypted bytes"}})
        return api.client.post("/notifications/channels/private/encrypted-events", json={"event_id": event_id, "mentions": mentions or [], "parent_event_id": parent}, headers=bearer(user))
    first = acknowledge("$root", mentions=["owner", "member", "watcher"])
    assert first.status_code == 200 and first.json()["created"] is True
    assert acknowledge("$root", mentions=["member", "watcher"]).json()["created"] is False
    inbox = api.client.get("/notifications", headers=bearer("member")).json()
    assert len(inbox) == 1 and inbox[0]["entity_type"] == "encrypted_message"
    assert api.client.get("/notifications", headers=bearer("watcher")).json() == []
    assert acknowledge("$root", user="member").status_code == 400  # Cannot ACK someone else's envelope.
    assert acknowledge("$outsider", user="outsider").status_code == 403
    assert acknowledge("$reply", user="member", parent="$root").status_code == 200
    owner_inbox = api.client.get("/notifications", headers=bearer()).json()
    assert len(owner_inbox) == 1 and owner_inbox[0]["type"] == "reply"
    with Session(api.engine) as session:
        assert session.get(EncryptedThreadSubscription, ("member", "private", "$root")).watching is True
    # Participating subscribes, while an explicit opt-out is preserved.
    watch = "/threads/channels/private/events/$root/subscription"
    assert api.client.put(watch, json={"watching": False}, headers=bearer("member")).status_code == 200
    assert acknowledge("$reply2", user="owner", parent="$root").status_code == 200
    assert len(api.client.get("/notifications", headers=bearer("member")).json()) == 1
    assert api.client.put("/notifications/preferences", json={"mentions": False}, headers=bearer("member")).status_code == 200
    assert acknowledge("$mentioned", mentions=["member"]).status_code == 200
    assert len(api.client.get("/notifications", headers=bearer("member")).json()) == 1
    with Session(api.engine) as session:
        assert len(session.exec(select(EncryptedMessageActivity)).all()) == 4
        assert all(notification.body == "Open FreeCollab to view this activity." for notification in session.exec(select(Notification)).all())
        session.delete(session.get(ChannelMember, ("private", "member")))
        session.commit()
    assert api.client.get("/notifications", headers=bearer("member")).json() == []
    assert api.client.get("/notifications/unread-count", headers=bearer("member")).json()["unread_count"] == 0


def test_secure_activity_summary_contains_only_generic_count_and_unavailable_proof_fails_closed(collaboration, monkeypatch):
    api = collaboration
    with Session(api.engine) as session:
        session.add(SecureChannel(channel_id="private", matrix_room_id="!private:example", created_by="owner", status="active"))
        session.add(EncryptedMessageActivity(channel_id="private", event_id="$opaque", actor_id="owner"))
        session.add(ChannelNotificationSetting(user_id="member", channel_id="private", email_digest="daily", digest_since=datetime.utcnow() - timedelta(days=2)))
        session.commit()
    monkeypatch.setattr("app.services.mail.mail_available", lambda: True)
    queued = []
    with Session(api.engine) as session:
        assert queue_due_digests(session, lambda *args: queued.append(args[1:]), "https://freecollab.example") == 1
    assert queued[0][0] == "member@example.com" and "1 new messages" in queued[0][2]
    assert "$opaque" not in queued[0][2]
    def unavailable(*_args, **_kwargs):
        raise HTTPException(503, "Encrypted service unavailable")
    monkeypatch.setattr("app.services.matrix_bridge.validate_secure_event", unavailable)
    response = api.client.post("/notifications/channels/private/encrypted-events", json={"event_id": "$unknown"}, headers=bearer())
    assert response.status_code == 503
    with Session(api.engine) as session:
        assert session.get(EncryptedMessageActivity, ("private", "$unknown")) is None


def test_personal_direct_block_fences_edits_decisions_and_reactions(collaboration):
    api = collaboration
    message = send(api)
    decision = api.client.post("/channels/private/decisions", json={"title": "Before block", "content": "Original agreement"}, headers=bearer()).json()
    assert api.client.post(f'/reactions/messages/{message["id"]}', json={"emoji": "👍"}, headers=bearer()).status_code == 200
    with Session(api.engine) as session:
        channel = session.get(Channel, "private")
        channel.type = "direct"
        session.add(channel)
        session.delete(session.get(ChannelMember, ("private", "watcher")))
        session.add(UserBlock(blocker_id="member", blocked_id="owner"))
        session.commit()
    assert api.client.put(f'/messages/{message["id"]}', json={"content": "Bypass a block"}, headers=bearer()).status_code == 403
    assert api.client.post("/channels/private/decisions", json={"title": "Bypass", "content": "New agreement"}, headers=bearer()).status_code == 403
    assert api.client.post(f'/decisions/{decision["id"]}/revisions', json={"title": "Bypass", "content": "Changed agreement", "expected_revision": 1}, headers=bearer()).status_code == 403
    assert api.client.post(f'/reactions/messages/{message["id"]}', json={"emoji": "❤️"}, headers=bearer()).status_code == 403
    assert api.client.delete(f'/reactions/messages/{message["id"]}/👍', headers=bearer()).status_code == 403
    assert api.client.get("/messages/channels/private", headers=bearer()).status_code == 200
    assert api.client.delete(f'/messages/{message["id"]}', headers=bearer()).status_code == 200
