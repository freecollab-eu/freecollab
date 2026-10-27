"""Committed deletion keeps a retry identity until private bytes are gone."""
import importlib
import pkgutil

from fastapi import HTTPException
import pytest
from sqlmodel import Session, select

import app.models
for module in pkgutil.iter_modules(app.models.__path__):
    importlib.import_module("app.models." + module.name)

from app.models.file import File
from app.models.file_version import FileDocument, FileErasure, FileUploadReceipt, FileVersion
from app.services import file_erasure, storage
from app.services.integration_worker import run_once
from tests.test_file_security import file_api as erasure_api
from tests.test_file_versions import upload


def test_storage_failure_keeps_durable_erasure_for_a_fresh_worker_session(erasure_api, monkeypatch):
    state = erasure_api
    record = upload(state, key="erasure-upload-key").json()
    original = state.storage.delete_file_sync
    def unavailable(_):
        raise HTTPException(503, "Object storage erasure is unavailable")
    monkeypatch.setattr(state.storage, "delete_file_sync", unavailable)
    response = state.client.delete("/files/" + record["id"])
    assert response.status_code == 200, response.text
    assert response.json()["erasure_pending"] is True
    assert state.storage.get_file_path(record["id"]).exists()
    with Session(state.engine) as session:
        assert session.get(File, record["id"]) is None
        assert session.get(FileErasure, record["id"]) is not None
        assert session.get(FileUploadReceipt, ("owner", "erasure-upload-key")).file_id is None
    monkeypatch.setattr(state.storage, "delete_file_sync", original)
    monkeypatch.setattr(storage, "storage_service", state.storage)
    with Session(state.engine) as restarted_worker:
        assert file_erasure.erase_pending_file_blobs(restarted_worker) == 1
    assert not state.storage.get_file_path(record["id"]).exists()
    with Session(state.engine) as session:
        assert session.get(FileErasure, record["id"]) is None
        assert file_erasure.erase_pending_file_blobs(session) == 0


def test_partial_multiversion_erasure_is_idempotent_and_does_not_forget_failed_blob(erasure_api, monkeypatch):
    state = erasure_api
    first = upload(state).json()
    second = upload(state, b"second", version=first["id"]).json()
    original = state.storage.delete_file_sync
    def partial(file_id):
        if file_id == second["id"]:
            raise OSError("Temporary private-storage failure")
        original(file_id)
        # A lost acknowledgement may follow successful object erasure.
        raise OSError("Erasure acknowledgement lost")
    monkeypatch.setattr(state.storage, "delete_file_sync", partial)
    response = state.client.delete("/files/" + second["id"])
    assert response.status_code == 200, response.text
    assert response.json()["erasure_pending"] is True
    with Session(state.engine) as session:
        assert len(session.exec(select(FileErasure)).all()) == 2
        assert session.get(FileDocument, first["id"]) is None
        assert session.get(FileVersion, first["id"]) is None
    assert not state.storage.get_file_path(first["id"]).exists()
    assert state.storage.get_file_path(second["id"]).exists()
    monkeypatch.setattr(state.storage, "delete_file_sync", original)
    with Session(state.engine) as session:
        assert file_erasure.erase_pending_file_blobs(session, store=state.storage) == 2
        assert session.exec(select(FileErasure)).all() == []
    assert not state.storage.get_file_path(second["id"]).exists()


def test_cleanup_never_erases_from_a_different_configured_storage_root(erasure_api):
    state = erasure_api
    identifier = "a" * 32
    state.storage.save_file_sync(identifier, b"original")
    replacement = storage.StorageService(state.tmp_path / "replacement")
    replacement.save_file_sync(identifier, b"different storage")
    with Session(state.engine) as session:
        session.add(FileErasure(file_id=identifier, storage_scope=file_erasure.storage_scope(state.storage)))
        session.commit()
        assert file_erasure.erase_pending_file_blobs(session, store=replacement) == 0
        assert session.get(FileErasure, identifier) is not None
        assert file_erasure.erase_pending_file_blobs(session, store=state.storage) == 1
    assert replacement.read_file(identifier) == b"different storage"


def test_local_migration_requires_draining_the_original_erasure_queue(erasure_api):
    from app.storage_migrate import migrate
    state = erasure_api
    destination = storage.StorageService(state.tmp_path / "destination")
    with Session(state.engine) as session:
        session.add(FileErasure(file_id="b" * 32, storage_scope=file_erasure.storage_scope(state.storage)))
        session.commit()
        with pytest.raises(ValueError, match="erasure"):
            migrate(session, state.storage, destination, copy=True)


def test_erasure_queue_commits_atomically_with_metadata_removal(erasure_api, monkeypatch):
    state = erasure_api
    record = upload(state).json()
    original = Session.commit
    def fail_deletion_commit(session):
        if any(isinstance(row, FileErasure) for row in session.new):
            raise RuntimeError("Metadata commit unavailable")
        original(session)
    monkeypatch.setattr(Session, "commit", fail_deletion_commit)
    with pytest.raises(RuntimeError, match="Metadata commit"):
        state.client.delete("/files/" + record["id"])
    with Session(state.engine) as session:
        assert session.get(File, record["id"]) is not None
        assert session.get(FileErasure, record["id"]) is None
    assert state.storage.read_file(record["id"]) == b"first"


def test_always_on_worker_finishes_erasure_when_optional_features_are_disabled(erasure_api, monkeypatch):
    from app.config import settings
    state = erasure_api
    identifier = "c" * 32
    state.storage.save_file_sync(identifier, b"removed private bytes")
    monkeypatch.setattr(storage, "storage_service", state.storage)
    for flag in ("webhooks_enabled", "otrust_enabled", "sync_enabled", "workflows_enabled",
                 "data_controls_enabled", "push_enabled", "native_push_enabled", "copilot_enabled", "connect_federation_enabled"):
        monkeypatch.setattr(settings, flag, False)
    with Session(state.engine) as session:
        session.add(FileErasure(file_id=identifier, storage_scope=file_erasure.storage_scope(state.storage)))
        session.commit()
    assert run_once(state.engine)["file-cleanup"] == 1
    assert state.storage.read_file(identifier) is None
    with Session(state.engine) as session:
        assert session.get(FileErasure, identifier) is None
