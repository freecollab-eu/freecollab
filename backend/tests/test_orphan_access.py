"""Legacy dangling memberships must not keep a deleted channel accessible."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api import files, messages, reactions, read_receipts, threads
from app.database import get_session
from app.models.channel import Channel, ChannelMember
from app.models.file import File
from app.models.message import Message
from app.models.reaction import Reaction
from app.models.read_receipt import ReadReceipt
from app.models.user import User
from app.services.auth import get_current_user
from app.services.storage import StorageService


FILE_ID = "f" * 32


@pytest.fixture
def resources(tmp_path, monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    app = FastAPI()
    for module in (files, messages, reactions, read_receipts, threads):
        app.include_router(module.router)
    app.state.user_id = "owner"

    def sessions():
        with Session(engine) as session:
            yield session

    def current_user():
        with Session(engine) as session:
            return session.get(User, app.state.user_id)

    app.dependency_overrides[get_session] = sessions
    app.dependency_overrides[get_current_user] = current_user
    monkeypatch.setattr("app.services.storage.settings.storage_path", str(tmp_path / "files"))
    storage = StorageService()
    monkeypatch.setattr(files, "storage_service", storage)
    path = storage.get_file_path(FILE_ID)
    path.write_bytes(b"synthetic private file")
    with Session(engine) as session:
        for user_id in ("owner", "outsider"):
            session.add(User(id=user_id, name=user_id, email=f"{user_id}@example.com", password_hash="unused"))
        session.add(Channel(id="channel", name="Private"))
        session.add(ChannelMember(channel_id="channel", user_id="owner", role="admin"))
        session.add(Message(id="message", channel_id="channel", author_id="owner", content="private history", encrypted=False))
        session.add(File(id=FILE_ID, channel_id="channel", uploader_id="owner", filename="private.txt", content_type="text/plain", size=22, storage_path=str(path)))
        session.add(Reaction(id="reaction", message_id="message", user_id="owner", emoji="👍"))
        session.add(ReadReceipt(channel_id="channel", user_id="owner", last_read_message_id="message"))
        session.commit()
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, app=app, engine=engine, path=path)
    engine.dispose()


CASES = [
    ("GET", "/messages/channels/channel", {}),
    ("POST", "/messages/channels/channel", {"json": {"content": "new text"}}),
    ("PUT", "/messages/message", {"json": {"content": "changed text"}}),
    ("DELETE", "/messages/message", {}),
    ("GET", "/threads/messages/message", {}),
    ("POST", "/threads/messages/message/reply", {"json": {"content": "reply"}}),
    ("GET", "/files/channels/channel", {}),
    ("POST", "/files/channels/channel", {"files": {"file": ("new.txt", b"new", "text/plain")}}),
    ("GET", f"/files/{FILE_ID}", {}),
    ("DELETE", f"/files/{FILE_ID}", {}),
    ("GET", "/reactions/messages/message", {}),
    ("POST", "/reactions/messages/message", {"json": {"emoji": "❤️"}}),
    ("DELETE", "/reactions/messages/message/👍", {}),
    ("GET", "/read-receipts/channels/channel", {}),
    ("POST", "/read-receipts/channels/channel", {"json": {"message_id": "message"}}),
    ("GET", "/read-receipts/channels/channel/unread-count", {}),
]


@pytest.mark.parametrize("method,path,kwargs", CASES)
def test_deleted_channel_denies_reads_and_mutations(resources, method, path, kwargs):
    with Session(resources.engine) as session:
        session.delete(session.get(Channel, "channel"))
        session.commit()
        # Older SQLite deployments may retain these dangling records.
        assert session.get(ChannelMember, ("channel", "owner"))
    response = resources.client.request(method, path, **kwargs)
    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Channel not found"
    with Session(resources.engine) as session:
        assert session.get(Message, "message").content == "private history"
        assert not session.get(Message, "message").deleted
        assert len(session.exec(select(Message)).all()) == 1
        assert session.get(Reaction, "reaction")
        assert len(session.exec(select(Reaction)).all()) == 1
        assert session.get(File, FILE_ID)
    assert resources.path.read_bytes() == b"synthetic private file"


def test_existing_channel_still_distinguishes_member_and_nonmember(resources):
    response = resources.client.get("/messages/channels/channel")
    assert response.status_code == 200 and response.json()[0]["content"] == "private history"
    resources.app.state.user_id = "outsider"
    response = resources.client.get("/messages/channels/channel")
    assert response.status_code == 403


def test_removed_member_cannot_remove_preexisting_reaction(resources):
    with Session(resources.engine) as session:
        session.delete(session.get(ChannelMember, ("channel", "owner")))
        session.commit()
    assert resources.client.delete("/reactions/messages/message/👍").status_code == 403
    with Session(resources.engine) as session:
        assert session.get(Reaction, "reaction")
