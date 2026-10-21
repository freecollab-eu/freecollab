"""File authorization and storage boundaries, using isolated local state."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy.pool import StaticPool

from app.api import files
from app.database import get_session
from app.models.channel import Channel, ChannelMember
from app.models.file import File
from app.models.user import User
from app.services.auth import get_current_user
from app.services.storage import StorageService


@pytest.fixture
def file_api(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    app = FastAPI()
    app.include_router(files.router)
    app.state.user_id = "owner"

    def session_override():
        with Session(engine) as session:
            yield session

    def user_override():
        with Session(engine) as session:
            return session.get(User, app.state.user_id)

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_current_user] = user_override
    monkeypatch.setattr("app.services.storage.settings.storage_path", str(tmp_path / "files"))
    storage = StorageService()
    monkeypatch.setattr(files, "storage_service", storage)
    with Session(engine) as session:
        for user_id in ("owner", "member", "outsider"):
            session.add(User(id=user_id, name=user_id, email=f"{user_id}@example.com", password_hash="unused"))
        session.add(Channel(id="channel", name="Private"))
        session.add(ChannelMember(channel_id="channel", user_id="owner"))
        session.add(ChannelMember(channel_id="channel", user_id="member"))
        session.commit()
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, app=app, engine=engine, storage=storage, tmp_path=tmp_path)
    engine.dispose()


def upload(api, content=b"private file"):
    response = api.client.post(
        "/files/channels/channel", files={"file": ("note.txt", content, "text/plain")}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_member_can_share_file_without_exposing_internal_paths(file_api):
    record = upload(file_api)
    assert record["encrypted"] is False
    assert "storage_path" not in record
    # Current storage is plaintext; the API must not claim otherwise.
    assert file_api.storage.get_file_path(record["id"]).read_bytes() == b"private file"
    file_api.app.state.user_id = "member"
    listing = file_api.client.get("/files/channels/channel")
    assert listing.status_code == 200
    assert listing.json()[0]["encrypted"] is False
    assert "storage_path" not in listing.json()[0]
    response = file_api.client.get(f"/files/{record['id']}")
    assert response.status_code == 200
    assert response.content == b"private file"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert file_api.client.delete(f"/files/{record['id']}").status_code == 403


def test_outsider_cannot_upload_list_download_or_delete(file_api):
    record = upload(file_api)
    file_api.app.state.user_id = "outsider"
    assert file_api.client.post(
        "/files/channels/channel", files={"file": ("no.txt", b"no", "text/plain")}
    ).status_code == 403
    assert file_api.client.get("/files/channels/channel").status_code == 403
    assert file_api.client.get(f"/files/{record['id']}").status_code == 403
    assert file_api.client.delete(f"/files/{record['id']}").status_code == 403


def test_removed_uploader_loses_access_and_delete_permission(file_api):
    record = upload(file_api)
    with Session(file_api.engine) as session:
        session.delete(session.get(ChannelMember, ("channel", "owner")))
        session.commit()
    assert file_api.client.get(f"/files/{record['id']}").status_code == 403
    assert file_api.client.delete(f"/files/{record['id']}").status_code == 403
    assert file_api.storage.get_file_path(record["id"]).is_file()


def test_download_does_not_trust_persisted_filesystem_path(file_api):
    record = upload(file_api)
    outside = file_api.tmp_path / "outside.txt"
    outside.write_text("must not be returned")
    with Session(file_api.engine) as session:
        stored = session.get(File, record["id"])
        stored.storage_path = str(outside)
        # Legacy rows incorrectly claimed that plaintext files were encrypted.
        stored.encrypted = True
        session.add(stored)
        session.commit()
    assert file_api.client.get(f"/files/{record['id']}").content == b"private file"
    assert file_api.client.get("/files/channels/channel").json()[0]["encrypted"] is False


def test_missing_file_returns_404(file_api):
    record = upload(file_api)
    file_api.storage.get_file_path(record["id"]).unlink()
    assert file_api.client.get(f"/files/{record['id']}").status_code == 404


def test_symlink_cannot_escape_storage_root(file_api):
    record = upload(file_api)
    path = file_api.storage.get_file_path(record["id"])
    path.unlink()
    outside = file_api.tmp_path / "outside.txt"
    outside.write_text("private outside storage")
    path.symlink_to(outside)
    assert file_api.client.get(f"/files/{record['id']}").status_code == 404


@pytest.mark.parametrize("file_id", ["../outside", "/tmp/outside", "a/../outside", "..\\outside", ""])
def test_storage_rejects_path_components(file_api, file_id):
    with pytest.raises(ValueError):
        file_api.storage.get_file_path(file_id)


def test_upload_reads_only_limit_plus_one_bytes(file_api, monkeypatch):
    monkeypatch.setattr(files, "MAX_FILE_SIZE", 4)

    class OversizedUpload:
        content_type = "text/plain"
        filename = "large.txt"

        async def read(self, size=-1):
            assert size == 5, "Uploads must not be read into memory without a bound"
            return b"12345"

    with Session(file_api.engine) as session:
        with pytest.raises(HTTPException) as error:
            asyncio.run(files.upload_file(
                "channel", OversizedUpload(), session.get(User, "owner"), session
            ))
        assert error.value.status_code == 400
        assert "large" in error.value.detail
    assert not list(file_api.storage.storage_path.iterdir())


def test_owner_delete_removes_record_and_file(file_api):
    record = upload(file_api)
    response = file_api.client.delete(f"/files/{record['id']}")
    assert response.status_code == 200
    assert not file_api.storage.get_file_path(record["id"]).exists()
    assert file_api.client.get("/files/channels/channel").json() == []


def test_storage_never_overwrites_an_existing_file(file_api):
    file_id = uuid4().hex
    asyncio.run(file_api.storage.save_file(file_id, b"original"))
    with pytest.raises(FileExistsError):
        asyncio.run(file_api.storage.save_file(file_id, b"replacement"))
    assert asyncio.run(file_api.storage.get_file(file_id)) == b"original"


def test_download_strips_filename_path_components(file_api):
    record = upload(file_api)
    with Session(file_api.engine) as session:
        stored = session.get(File, record["id"])
        stored.filename = "..\\..\\unsafe\r\n.txt"
        session.add(stored)
        session.commit()
    response = file_api.client.get(f"/files/{record['id']}")
    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="unsafe.txt"'
