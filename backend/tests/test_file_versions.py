"""Immutable file versions, retry receipts and encrypted-channel boundaries."""
import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from sqlmodel import Session, select

from app.api import files
from app.models.file import File
from app.models.file_version import FileDocument, FileUploadReceipt, FileVersion
from app.models.secure_channel import SecureChannel
from app.models.channel import Channel, ChannelMember
from app.models.direct_message import UserBlock
from app.models.user import User
from tests.test_file_security import file_api as version_api  # Reuse the isolated fixture.


def upload(api, content=b'first', filename='draft.txt', key=None, version=None, content_type='text/plain'):
    path = f'/files/{version}/versions' if version else '/files/channels/channel'
    return api.client.post(path, files={'file': (filename, content, content_type)},
                           headers={'Idempotency-Key': key} if key else {})


def test_versions_keep_original_bytes_and_list_only_latest_document(version_api):
    first = upload(version_api).json()
    response = upload(version_api, b'second', 'review.txt', version=first['id'])
    assert response.status_code == 200, response.text
    second = response.json()
    assert second['document_id'] == first['id']
    assert second['version_number'] == second['version_count'] == 2
    assert first['id'] != second['id']
    assert version_api.client.get('/files/' + first['id']).content == b'first'
    assert version_api.client.get('/files/' + second['id']).content == b'second'
    assert [record['id'] for record in version_api.client.get('/files/channels/channel').json()] == [second['id']]
    versions = version_api.client.get(f"/files/{first['id']}/versions").json()
    assert [record['version_number'] for record in versions] == [2, 1]
    assert all(record['version_count'] == 2 and record['encrypted'] is False for record in versions)
    assert all('storage_path' not in record for record in versions)


def test_members_read_versions_but_only_owner_can_change_document(version_api):
    first = upload(version_api).json()
    second = upload(version_api, b'v2', version=first['id']).json()
    version_api.app.state.user_id = 'member'
    assert version_api.client.get(f"/files/{second['id']}/versions").status_code == 200
    assert version_api.client.get('/files/' + first['id']).content == b'first'
    assert upload(version_api, b'changed', version=first['id']).status_code == 403
    assert version_api.client.delete('/files/' + second['id']).status_code == 403
    version_api.app.state.user_id = 'outsider'
    assert version_api.client.get(f"/files/{second['id']}/versions").status_code == 403
    assert version_api.client.get('/files/' + first['id']).status_code == 403
    assert upload(version_api, b'changed', version=first['id']).status_code == 403


def test_removed_owner_cannot_read_or_change_old_versions(version_api):
    first = upload(version_api).json()
    with Session(version_api.engine) as session:
        session.delete(session.get(ChannelMember, ('channel', 'owner')))
        session.commit()
    assert version_api.client.get(f"/files/{first['id']}/versions").status_code == 403
    assert upload(version_api, b'v2', version=first['id']).status_code == 403
    assert version_api.storage.get_file_path(first['id']).read_bytes() == b'first'


def test_retry_after_lost_acknowledgement_does_not_duplicate_upload(version_api):
    key = uuid4().hex
    first = upload(version_api, key=key).json()
    repeated = upload(version_api, key=key)
    assert repeated.status_code == 200
    assert repeated.json()['id'] == first['id']
    assert upload(version_api, b'different', key=key).status_code == 409
    assert len(version_api.client.get('/files/channels/channel').json()) == 1
    assert len(list(version_api.storage.storage_path.iterdir())) == 1


def test_version_retry_returns_same_version_via_any_document_version(version_api):
    first = upload(version_api).json()
    key = uuid4().hex
    second = upload(version_api, b'v2', key=key, version=first['id']).json()
    repeated = upload(version_api, b'v2', key=key, version=second['id'])
    assert repeated.status_code == 200
    assert repeated.json()['id'] == second['id']
    assert len(version_api.client.get(f"/files/{first['id']}/versions").json()) == 2
    assert len(list(version_api.storage.storage_path.iterdir())) == 2


def test_upload_request_keys_are_scoped_to_account(version_api):
    key = uuid4().hex
    first = upload(version_api, key=key).json()
    version_api.app.state.user_id = 'member'
    second = upload(version_api, key=key).json()
    assert first['id'] != second['id']
    assert second['document_owner_id'] == 'member'


def test_rejected_version_leaves_current_bytes_and_version_count(version_api, monkeypatch):
    first = upload(version_api).json()
    assert upload(version_api, b'no', version=first['id'], content_type='application/x-unsupported').status_code == 400
    monkeypatch.setattr(files, 'MAX_FILE_SIZE', 4)
    assert upload(version_api, b'12345', version=first['id']).status_code == 400
    records = version_api.client.get(f"/files/{first['id']}/versions").json()
    assert len(records) == 1 and records[0]['version_count'] == 1
    assert version_api.storage.get_file_path(first['id']).read_bytes() == b'first'


def test_first_version_of_legacy_file_backfills_only_its_document(version_api):
    file_id = uuid4().hex
    path = asyncio.run(version_api.storage.save_file(file_id, b'legacy'))
    with Session(version_api.engine) as session:
        session.add(File(id=file_id, channel_id='channel', uploader_id='owner', filename='old.txt',
                         content_type='text/plain', size=6, storage_path=path, encrypted=True))
        session.commit()
    before = version_api.client.get(f'/files/{file_id}/versions').json()
    assert before[0]['version_number'] == 1 and before[0]['encrypted'] is False
    result = upload(version_api, b'new', version=file_id)
    assert result.status_code == 200, result.text
    assert result.json()['version_number'] == 2
    assert version_api.storage.get_file_path(file_id).read_bytes() == b'legacy'


@pytest.mark.parametrize('status', ['preparing', 'active'])
def test_native_upload_cannot_leak_plaintext_into_secure_channel(version_api, status):
    first = upload(version_api).json()
    with Session(version_api.engine) as session:
        session.add(SecureChannel(channel_id='channel', created_by='owner', status=status))
        session.commit()
    assert upload(version_api, b'secret').status_code == 409
    assert upload(version_api, b'secret', version=first['id']).status_code == 409
    assert len(list(version_api.storage.storage_path.iterdir())) == 1


def test_delete_all_versions_and_retry_cannot_resurrect_document(version_api):
    key = uuid4().hex
    first = upload(version_api, key=key).json()
    second = upload(version_api, b'v2', version=first['id']).json()
    assert version_api.client.delete('/files/' + second['id']).status_code == 200
    assert version_api.client.get('/files/channels/channel').json() == []
    assert version_api.client.get('/files/' + first['id']).status_code == 404
    assert version_api.client.get('/files/' + second['id']).status_code == 404
    assert not list(version_api.storage.storage_path.iterdir())
    assert upload(version_api, key=key).status_code == 409
    with Session(version_api.engine) as session:
        assert session.exec(select(FileVersion)).all() == []
        assert session.exec(select(FileDocument)).all() == []
        assert session.get(FileUploadReceipt, ('owner', key)).file_id is None


def test_database_failure_cleans_orphaned_blob(version_api, monkeypatch):
    def fail_commit(self):
        raise RuntimeError('simulated transaction failure')
    monkeypatch.setattr(Session, 'commit', fail_commit)
    with pytest.raises(RuntimeError, match='simulated transaction failure'):
        upload(version_api)
    assert not list(version_api.storage.storage_path.iterdir())
    with Session(version_api.engine) as session:
        assert session.exec(select(File)).all() == []
        assert session.exec(select(FileDocument)).all() == []


def test_blocked_direct_pair_cannot_send_files_or_versions_but_retains_old_downloads(version_api):
    first = upload(version_api).json()
    with Session(version_api.engine) as session:
        session.get(Channel, 'channel').type = 'direct'
        session.add(UserBlock(blocker_id='member', blocked_id='owner'))
        session.commit()
    assert upload(version_api, b'blocked filename communication').status_code == 403
    assert upload(version_api, b'blocked replacement', version=first['id']).status_code == 403
    assert version_api.client.get('/files/' + first['id']).content == b'first'
    version_api.app.state.user_id = 'member'
    assert upload(version_api, b'blocked opposite direction').status_code == 403
    assert version_api.client.get('/files/' + first['id']).content == b'first'
    assert len(list(version_api.storage.storage_path.iterdir())) == 1


def test_block_created_while_body_is_read_is_checked_again_before_file_write(version_api):
    with Session(version_api.engine) as session:
        session.get(Channel, 'channel').type = 'direct'
        session.commit()

    class BlockDuringRead:
        content_type = 'text/plain'
        filename = 'blocked.txt'

        async def read(self, size):
            with Session(version_api.engine) as other:
                other.add(UserBlock(blocker_id='member', blocked_id='owner'))
                other.commit()
            return b'blocked'

    from fastapi import HTTPException
    with Session(version_api.engine) as session:
        with pytest.raises(HTTPException) as failure:
            asyncio.run(files.upload_file('channel', BlockDuringRead(), session.get(User, 'owner'), session))
        assert failure.value.status_code == 403
    assert not list(version_api.storage.storage_path.iterdir())


def test_failed_storage_write_removes_partial_new_blob_without_replacing_existing(version_api, monkeypatch):
    file_id = uuid4().hex
    path = version_api.storage.get_file_path(file_id)
    original_open = Path.open

    class FailedWrite:
        def __enter__(self):
            self.output = original_open(path, 'xb')
            return self

        def __exit__(self, *args):
            self.output.close()
            return False

        def write(self, content):
            self.output.write(b'partial')
            self.output.flush()
            raise OSError('simulated full disk')

    def open_with_failed_write(candidate, *args, **kwargs):
        if candidate == path and args == ('xb',) and not kwargs:
            return FailedWrite()
        return original_open(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', open_with_failed_write)
    with pytest.raises(OSError, match='simulated full disk'):
        asyncio.run(version_api.storage.save_file(file_id, b'complete'))
    assert not path.exists()
