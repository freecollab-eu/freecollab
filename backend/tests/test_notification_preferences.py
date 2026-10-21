"""Recipient preferences must govern persisted and realtime notifications."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api import comments, notifications
from app.database import get_session
from app.models.channel import Channel, ChannelMember
from app.models.notification import Notification, NotificationPreference
from app.models.post import Comment, Post
from app.models.user import User
from app.services.auth import create_token


CATEGORIES = [
    ("mention", "mentions"), ("like", "likes"), ("comment", "comments"),
    ("reply", "replies"), ("follow", "follows"), ("system", "system"),
]


@pytest.fixture
def notification_api(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([
            User(id=user_id, name=user_id, email=f"{user_id}@example.com", password_hash="unused")
            for user_id in ("owner", "member", "outsider")
        ])
        session.add(Channel(id="private", name="Private"))
        session.add_all([
            ChannelMember(channel_id="private", user_id=user_id)
            for user_id in ("owner", "member")
        ])
        session.add(Post(id="post", author_id="owner", flow_id="private", content="Post"))
        session.commit()
    delivery = AsyncMock()
    monkeypatch.setattr(notifications, "send_to_user", delivery)
    monkeypatch.setattr(comments, "broadcast_to_channel", AsyncMock())
    app = FastAPI()
    app.include_router(notifications.router)
    # This isolated app explicitly enables the otherwise experimental comments API.
    app.include_router(comments.router)

    def session_override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, engine=engine, delivery=delivery)
    engine.dispose()


def bearer(user_id="owner"):
    return {"Authorization": "Bearer " + create_token(user_id)}


def set_preferences(api, **changes):
    response = api.client.put("/notifications/preferences", json=changes, headers=bearer())
    assert response.status_code == 200


def create(api, notification_type="mention"):
    with Session(api.engine) as session:
        return asyncio.run(notifications.create_notification(
            session, "owner", notification_type, "A notification", body="Details",
            actor_id="member", entity_type="post", entity_id="post", action_url="/posts/post",
        ))


@pytest.mark.parametrize("notification_type,field", CATEGORIES)
def test_singular_type_obeys_its_plural_category_and_can_be_reenabled(notification_api, notification_type, field):
    api = notification_api
    set_preferences(api, **{field: False})
    assert create(api, notification_type) is None
    api.delivery.assert_not_awaited()
    with Session(api.engine) as session:
        assert session.exec(select(Notification)).all() == []
    set_preferences(api, **{field: True})
    notification = create(api, notification_type)
    assert notification is not None
    api.delivery.assert_awaited_once()
    recipient, event, payload = api.delivery.await_args.args
    assert (recipient, event) == ("owner", "notification")
    assert payload["type"] == notification_type
    assert payload["actor_id"] == "member"
    with Session(api.engine) as session:
        stored = session.get(Notification, payload["id"])
        assert stored.user_id == "owner" and stored.type == notification_type
        assert stored.body == "Details" and stored.entity_id == "post"


@pytest.mark.parametrize("notification_type,field", CATEGORIES)
def test_in_app_disabled_blocks_every_category_even_with_other_delivery_flags(notification_api, notification_type, field):
    api = notification_api
    set_preferences(api, in_app=False, email=True, push=True, **{field: True})
    assert create(api, notification_type) is None
    api.delivery.assert_not_awaited()
    with Session(api.engine) as session:
        assert session.exec(select(Notification)).all() == []


def test_default_preferences_allow_in_app_without_creating_a_preference_row(notification_api):
    api = notification_api
    notification = create(api)
    assert notification is not None
    api.delivery.assert_awaited_once()
    with Session(api.engine) as session:
        assert session.exec(select(NotificationPreference)).all() == []
        assert len(session.exec(select(Notification)).all()) == 1


@pytest.mark.parametrize("notification_type", ["unknown", "mentions", "in_app", "email", "push", "quiet_start"])
@pytest.mark.parametrize("has_preferences", [False, True])
def test_unknown_types_cannot_use_other_preference_attributes_as_categories(notification_api, notification_type, has_preferences):
    api = notification_api
    if has_preferences:
        set_preferences(api, in_app=True, email=True, push=True)
    assert create(api, notification_type) is None
    api.delivery.assert_not_awaited()
    with Session(api.engine) as session:
        assert session.exec(select(Notification)).all() == []


@pytest.mark.parametrize("settings,expected_count", [
    ({"comments": False}, 0),
    ({"in_app": False}, 0),
    ({"comments": True, "in_app": True}, 1),
])
def test_actual_comment_generation_obeys_recipient_preferences(notification_api, settings, expected_count):
    api = notification_api
    set_preferences(api, **settings)
    # An actor's disabled settings must not suppress another person's enabled inbox.
    assert api.client.put("/notifications/preferences", json={"in_app": False}, headers=bearer("member")).status_code == 200
    response = api.client.post("/comments/post/post", json={"content": "A real comment"}, headers=bearer("member"))
    assert response.status_code == 200
    with Session(api.engine) as session:
        assert session.get(Comment, response.json()["id"]).content == "A real comment"
        assert session.get(Post, "post").comment_count == 1
        rows = session.exec(select(Notification)).all()
        assert len(rows) == expected_count
        if rows:
            assert rows[0].user_id == "owner" and rows[0].actor_id == "member"
            assert rows[0].type == "comment" and rows[0].entity_id == "post"
    assert api.delivery.await_count == expected_count
    inbox = api.client.get("/notifications", headers=bearer()).json()
    assert len(inbox) == expected_count
    assert api.client.get("/notifications", headers=bearer("member")).json() == []


def test_comment_access_checks_and_self_notification_rule_remain_intact(notification_api):
    api = notification_api
    assert api.client.post("/comments/post/post", json={"content": "No authentication"}).status_code in (401, 403)
    assert api.client.post("/comments/post/post", json={"content": "Intrusion"}, headers=bearer("outsider")).status_code == 403
    assert api.client.post("/comments/post/post", json={"content": "My own comment"}, headers=bearer()).status_code == 200
    api.delivery.assert_not_awaited()
    with Session(api.engine) as session:
        assert len(session.exec(select(Comment)).all()) == 1
        assert session.exec(select(Notification)).all() == []
