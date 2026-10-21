import hashlib
import io
import zipfile

from fastapi import HTTPException
from pydantic import SecretStr, ValidationError
import pytest
from sqlmodel import Session

from app.api import files
from app.config import Settings
from app.services import storage
from app.services.storage import S3StorageService, StorageLimitError, StorageService
from tests.s3_fixture import s3
from tests.test_file_security import file_api, upload
from tests.test_data_controls import controls, headers, post, FILE_ID, service

IDENTITY = "a" * 32


def test_actual_tls_sigv4_immutable_put_bounded_checksum_get_and_version_erasure(s3):
    store = s3.store
    assert store.save_file_sync(IDENTITY, b"private immutable bytes").startswith("s3://freecollab-fixture/")
    with pytest.raises(FileExistsError): store.save_file_sync(IDENTITY, b"replacement")
    assert store.read_file(IDENTITY, max_bytes=64, expected_size=23) == b"private immutable bytes"
    with pytest.raises(StorageLimitError): store.read_file(IDENTITY, max_bytes=4)
    with pytest.raises(ValueError): store.read_file(IDENTITY, max_bytes=64, expected_size=22)
    assert store.delete_file_sync(IDENTITY)
    assert store.read_file(IDENTITY, max_bytes=64) is None
    assert not store.delete_file_sync(IDENTITY)
    assert {method for method, _, _ in s3.state.requests} >= {"PUT", "GET", "DELETE", "HEAD"}


def test_checksum_change_and_missing_remote_object_never_fall_back_to_local(s3, tmp_path):
    store = s3.store
    local = StorageService(tmp_path)
    local.save_file_sync(IDENTITY, b"misleading local bytes")
    assert store.read_file(IDENTITY, max_bytes=64) is None
    store.save_file_sync(IDENTITY, b"remote bytes")
    s3.state.corrupt = True
    with pytest.raises(ValueError, match="checksum"): store.read_file(IDENTITY, max_bytes=64)
    with pytest.raises(ValueError): store.get_file_path(IDENTITY)


def test_versioning_delete_markers_and_object_lock_are_not_false_erasure(s3):
    store = s3.store
    store.save_file_sync(IDENTITY, b"one")
    store.client.delete_object(Bucket=store.bucket, Key=store.key(IDENTITY))
    store.save_file_sync(IDENTITY, b"two")
    path = "/" + store.bucket + "/" + store.key(IDENTITY)
    assert len(s3.state.objects[path]) == 3
    s3.state.locked = True
    with pytest.raises(HTTPException) as error: store.delete_file_sync(IDENTITY)
    assert error.value.status_code == 503 and len(s3.state.objects[path]) == 3
    s3.state.locked = False
    assert store.delete_file_sync(IDENTITY) and s3.state.objects[path] == []


def test_tls_trust_and_credentials_are_verified_without_secret_errors(s3):
    wrong = s3.config.model_copy(update={"s3_secret_access_key": SecretStr("incorrect-private-key")})
    with pytest.raises(HTTPException) as error: S3StorageService(wrong).save_file_sync(IDENTITY, b"secret")
    assert error.value.status_code == 503 and "incorrect-private-key" not in str(error.value)
    untrusted = s3.config.model_copy(update={"s3_ca_bundle": ""})
    with pytest.raises(HTTPException) as error: S3StorageService(untrusted).read_file(IDENTITY, max_bytes=10)
    assert error.value.status_code == 503


@pytest.mark.parametrize("changes", [
    {"s3_endpoint_url": "https://user:password@storage.example.com"},
    {"s3_endpoint_url": "https://storage.example.com/other-path"},
    {"s3_endpoint_url": "http://storage.example.com"},
    {"s3_key_prefix": "../"}, {"s3_key_prefix": "safe/../../"},
    {"s3_secret_access_key": None}, {"s3_secret_access_key": ""}, {"s3_access_key_id": " "},
    {"s3_bucket": "../bucket"},
])
def test_object_storage_configuration_rejects_unsafe_or_incomplete_boundaries(changes):
    values = dict(storage_backend="s3", s3_bucket="customer-files", s3_access_key_id="synthetic-access",
        s3_secret_access_key="synthetic-secret", s3_endpoint_url="https://storage.example.com")
    with pytest.raises(ValidationError): Settings(_env_file=None, **(values | changes))


def test_real_s3_file_api_download_authorization_and_delete(file_api, s3, monkeypatch):
    monkeypatch.setattr(files, "storage_service", s3.store)
    result = upload(file_api, b"remote version")
    response = file_api.client.get("/files/" + result["id"])
    assert response.status_code == 200 and response.content == b"remote version"
    assert response.headers["cache-control"] == "private, no-store"
    file_api.app.state.user_id = "outsider"
    assert file_api.client.get("/files/" + result["id"]).status_code == 403
    file_api.app.state.user_id = "owner"
    assert file_api.client.delete("/files/" + result["id"]).status_code == 200
    assert s3.store.read_file(result["id"], max_bytes=64) is None


def test_actual_data_export_reads_s3_and_keeps_encrypted_archive_local(controls, s3, monkeypatch):
    monkeypatch.setattr(storage, "storage_service", s3.store)
    s3.store.save_file_sync(FILE_ID, b"PRIVATE FILE")
    queued = post(controls, "/exports", {"channel_ids": ["native"], "acknowledge_plaintext_export": True})
    assert queued.status_code == 202, queued.text
    with Session(controls[1]) as session: assert service.run_pending(session) == 1
    response = post(controls, "/exports/" + queued.json()["id"] + "/download")
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.read("files/" + FILE_ID) == b"PRIVATE FILE"
    encrypted = service._artifact_path(queued.json()["id"]).read_bytes()
    assert b"PRIVATE FILE" not in encrypted


def test_office_reader_uses_bounded_remote_bytes(s3, monkeypatch):
    from app.services.file_experience import bounded_file
    from types import SimpleNamespace
    monkeypatch.setattr(storage, "storage_service", s3.store)
    s3.store.save_file_sync(IDENTITY, b"office XML")
    assert bounded_file(SimpleNamespace(id=IDENTITY, size=10)) == b"office XML"
    s3.state.corrupt = True
    with pytest.raises(HTTPException) as error: bounded_file(SimpleNamespace(id=IDENTITY, size=10))
    assert error.value.status_code == 404


def test_explicit_migration_verifies_checksum_and_keeps_local_source(controls, s3):
    from app.storage_migrate import migrate
    from app.models.file import File
    source = StorageService(storage.settings.storage_path)
    with Session(controls[1]) as session:
        with pytest.raises(ValueError, match="not copied"):
            migrate(session, source, s3.store)
        result = migrate(session, source, s3.store, copy=True)
        assert result == {"copied": 1, "verified": 1, "source_deleted": False, "metadata_updated": True}
        assert session.get(File, FILE_ID).storage_path.startswith("s3://")
        assert migrate(session, source, s3.store)["verified"] == 1
    assert source.read_file(FILE_ID, max_bytes=64) == b"PRIVATE FILE"
    s3.state.corrupt = True
    with Session(controls[1]) as session:
        with pytest.raises(ValueError): migrate(session, source, s3.store, copy=True)


def test_operator_probe_uses_real_transport_and_reports_only_verified_metadata(s3):
    from app.storage_probe import probe
    result = probe(s3.store)
    assert result == {"backend": "s3", "write": True, "read": True, "checksum": True, "erased": True}
    assert not any(s3.state.objects.values())
