"""Pilot admin reads work and incomplete hard deletion never orphans data."""
import pytest
from unittest.mock import AsyncMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api import admin, messages
from app.database import get_session
from app.models.channel import Channel, ChannelMember
from app.models.file import File
from app.models.message import Message
from app.models.user import User
from app.services.auth import create_token


@pytest.fixture
def administration(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([
            User(id="admin", name="Admin", email="admin@example.com", password_hash="unused", role="admin"),
            User(id="member", name="Member", email="member@example.com", password_hash="unused"),
            Channel(id="channel", name="Retained channel"),
        ])
        session.commit()
        session.add_all([
            ChannelMember(channel_id="channel", user_id="member"),
            Message(id="message", channel_id="channel", author_id="member", content="retained message", encrypted=False),
            File(id="file", channel_id="channel", uploader_id="member", filename="retained.txt", content_type="text/plain", size=4, storage_path="unused"),
        ])
        session.commit()
    app = FastAPI()
    app.include_router(admin.router)
    app.include_router(messages.router)
    monkeypatch.setattr(admin, "broadcast_to_channel", AsyncMock())
    def override_session():
        with Session(engine) as session:
            yield session
    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as client:
        yield client, engine
    engine.dispose()


def header(user="admin"):
    return {"Authorization": "Bearer " + create_token(user)}


def test_admin_lists_channels_without_nonexistent_created_by_field(administration):
    client, _ = administration
    response = client.get("/admin/channels", headers=header())
    assert response.status_code == 200
    channel = response.json()[0]
    assert channel["id"] == "channel" and channel["name"] == "Retained channel"
    assert channel["created_at"] and "created_by" not in channel
    assert client.get("/admin/users", headers=header()).status_code == 200
    assert client.get("/admin/stats", headers=header()).status_code == 200


@pytest.mark.parametrize("path", ["/admin/channels/channel", "/admin/users/member"])
def test_incomplete_deletion_is_refused_and_all_content_remains(administration, path):
    client, engine = administration
    response = client.delete(path, headers=header())
    assert response.status_code == 409
    assert "retention workflow" in response.json()["detail"]
    with Session(engine) as session:
        assert session.get(User, "member")
        assert session.get(Channel, "channel")
        assert session.get(ChannelMember, ("channel", "member"))
        assert session.get(Message, "message").content == "retained message"
        assert session.get(File, "file").filename == "retained.txt"


def test_nonstaff_cannot_read_or_attempt_retention_actions(administration):
    client, _ = administration
    assert client.get("/admin/channels", headers=header("member")).status_code == 403
    assert client.delete("/admin/channels/channel", headers=header("member")).status_code == 403
    assert client.delete("/admin/users/admin", headers=header("member")).status_code == 403


def test_missing_objects_and_own_account_keep_existing_error_contract(administration):
    client, _ = administration
    assert client.delete("/admin/channels/missing", headers=header()).status_code == 404
    assert client.delete("/admin/users/missing", headers=header()).status_code == 404
    assert client.delete("/admin/users/admin", headers=header()).status_code == 400


def test_message_moderation_preserves_thread_references_and_hides_history(administration):
    client, engine = administration
    with Session(engine) as session:
        parent = session.get(Message, "message")
        parent.thread_count = 1
        session.add(parent)
        session.add(Message(id="reply", channel_id="channel", author_id="member", content="reply text", encrypted=False, parent_id="message"))
        session.commit()
    response = client.delete("/admin/messages/message", headers=header())
    assert response.status_code == 200
    with Session(engine) as session:
        parent = session.get(Message, "message")
        assert parent and parent.deleted and parent.content == "[Message deleted]"
        assert parent.thread_count == 1
        assert session.get(Message, "reply").parent_id == "message"
    history = client.get("/messages/channels/channel", headers=header("member"))
    assert history.status_code == 200
    assert all(message["id"] != "message" for message in history.json())
    admin.broadcast_to_channel.assert_awaited_once_with("channel", "message_deleted", {"id": "message"})


def test_nonstaff_cannot_moderate_message(administration):
    client, engine = administration
    assert client.delete("/admin/messages/message", headers=header("member")).status_code == 403
    with Session(engine) as session:
        assert not session.get(Message, "message").deleted
