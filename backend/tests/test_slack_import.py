"""Portable Slack archives: review, authority, transaction and ZIP adversaries."""
from datetime import datetime, timedelta
import io
import json
import stat
import zipfile

import pytest
from sqlmodel import Session, select
from app.api import slack_import as routes
from app.models.channel import Channel, ChannelMember
from app.models.file import File
from app.models.message import Message
from app.models.slack_import import SlackImport
from app.models.user import User
from app.services import slack_import as service
from app.services.crypto import decrypt_message
from app.services.storage import StorageService
from tests.test_webhooks import team

BASE = '/workspaces/team/slack-imports'


def archive(*, changes=None, extra=None):
    data = {
        'users.json': [{'id': 'U1', 'name': 'Alice'}, {'id': 'U2', 'profile': {'display_name': 'Bob'}}],
        'channels.json': [{'id': 'C1', 'name': 'general', 'members': ['U1', 'U2']}, {'id': 'C2', 'name': 'private', 'members': ['U2']}],
        'general/2024-01-01.json': [
            {'type': 'message', 'user': 'U1', 'ts': '1704067200.000001', 'text': 'Private original Slack content'},
            {'type': 'message', 'user': 'U2', 'ts': '1704067201.000001', 'thread_ts': '1704067200.000001', 'text': 'A threaded reply', 'files': [{'id': 'F1', 'name': '../report.txt', 'mimetype': 'text/plain'}]},
            {'type': 'message', 'user': 'U1', 'ts': '1704067202.000001', 'text': 'Unavailable remote file', 'files': [{'id': 'F2', 'url_private_download': 'http://169.254.169.254/never-fetch'}]},
            {'type': 'message', 'subtype': 'channel_join', 'ts': '1704067203.000001', 'text': 'Not historical conversation text'}],
        'private/2024-01-01.json': [{'type': 'message', 'user': 'U2', 'ts': '1704067200.000001', 'text': 'Do not import unselected channel'}],
    }
    if changes: changes(data)
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as writer:
        for path, value in data.items(): writer.writestr(path, json.dumps(value))
        writer.writestr('files/F1', b'Bundled attachment original bytes')
        if extra: extra(writer)
    return output.getvalue()


@pytest.fixture
def importer(team, tmp_path, monkeypatch):
    team[0].app.include_router(routes.router)
    monkeypatch.setattr(service, 'storage_service', StorageService(tmp_path / 'imports'))
    return team


def preview(importer, raw=None, actor='owner'):
    response = importer[0].post(BASE + '/preview', headers=importer[2][actor], files={'file': ('slack.zip', raw or archive(), 'application/zip')})
    assert response.status_code == 200, response.text
    return response.json()


def confirm(importer, job, **changes):
    fields = {'channel_ids': ['C1'], 'user_mapping': {'U1': 'owner', 'U2': 'member'}, 'channel_prefix': 'archive-', 'acknowledge_native_archive': True}
    fields.update(changes)
    return importer[0].post(BASE + '/' + job['id'] + '/confirm', headers=importer[2]['owner'], json=fields)


def test_real_archive_preview_then_transactional_threads_files_labels_and_retry(importer):
    client, engine, headers = importer
    raw = archive()
    job = preview(importer, raw)
    assert [row['message_count'] for row in job['channels']] == [3, 1]
    assert job['bundled_files'] == job['missing_files'] == 1
    assert 'Private original Slack content' not in json.dumps(job)
    with Session(engine) as session:
        stored = session.get(SlackImport, job['id'])
        assert stored.encrypted_archive.startswith('fcas1:') and 'Alice' not in stored.encrypted_archive
        assert len(session.exec(select(Channel)).all()) == 4
    response = confirm(importer, job)
    assert response.status_code == 200, response.text
    result = response.json(); cid = result['channels'][0]['id']
    assert result['messages'] == 3 and result['files'] == 1 and result['missing_files'] == 1
    assert confirm(importer, job).json() == result
    assert confirm(importer, job, channel_prefix='other-').status_code == 409
    with Session(engine) as session:
        stored = session.get(SlackImport, job['id']); assert stored.encrypted_archive == '' and stored.status == 'completed'
        rows = session.exec(select(Message).where(Message.channel_id == cid).order_by(Message.created_at)).all()
        assert len(rows) == 3 and all(row.author_id == 'owner' for row in rows)
        assert rows[1].parent_id == rows[0].id and rows[0].thread_count == 1
        content = decrypt_message(rows[1].content, cid)
        assert '[Imported from Slack · Bob' in content and 'A threaded reply' in content
        assert 'A threaded reply' not in rows[1].content
        assert {row.user_id for row in session.exec(select(ChannelMember).where(ChannelMember.channel_id == cid))} == {'owner', 'member'}
        record = session.exec(select(File).where(File.channel_id == cid)).one()
        assert record.filename == 'report.txt'
        assert service.storage_service.read_file(record.id) == b'Bundled attachment original bytes'
    # Normal history authorization remains authoritative after the import.
    assert client.get('/messages/channels/' + cid, headers=headers['member']).status_code == 200
    assert client.get('/messages/channels/' + cid, headers=headers['admin']).status_code == 403
    assert preview(importer, raw)['result'] == result


def test_mapping_and_import_are_explicit_and_current_administrator_only(importer):
    client, engine, headers = importer
    for actor in ['member', 'outsider']:
        assert client.post(BASE + '/preview', headers=headers[actor], files={'file': ('s.zip', archive())}).status_code == 403
    job = preview(importer)
    assert confirm(importer, job, acknowledge_native_archive=False).status_code == 422
    assert confirm(importer, job, channel_ids=['C1', 'C1']).status_code == 422
    assert confirm(importer, job, channel_ids=['C999']).status_code == 422
    assert confirm(importer, job, user_mapping={'U99': 'member'}).status_code == 422
    assert confirm(importer, job, user_mapping={'U2': 'outsider'}).status_code == 403
    assert client.post(BASE + '/' + job['id'] + '/confirm', headers=headers['admin'], json={'channel_ids': ['C1'], 'acknowledge_native_archive': True}).status_code == 404
    result = confirm(importer, job, user_mapping={}).json()
    with Session(engine) as session:
        assert {row.user_id for row in session.exec(select(ChannelMember).where(ChannelMember.channel_id == result['channels'][0]['id']))} == {'owner'}


@pytest.mark.parametrize('path', ['../outside.json', '/absolute.json', 'general/../../escape.json', 'general\\escape.json'])
def test_zip_paths_are_rejected_without_extraction(importer, path):
    response = importer[0].post(BASE + '/preview', headers=importer[2]['owner'], files={'file': ('s.zip', archive(extra=lambda writer: writer.writestr(path, '{}')))})
    assert response.status_code == 422, response.text


def test_symlinks_duplicate_json_duplicate_timestamps_and_expansion_are_rejected(importer):
    def symlink(writer):
        info = zipfile.ZipInfo('link'); info.create_system = 3; info.external_attr = (stat.S_IFLNK | 0o777) << 16
        writer.writestr(info, '/etc/passwd')
    variants = [archive(extra=symlink), archive(extra=lambda writer: writer.writestr('huge.txt', b'A' * (2 * 1024 * 1024))),
                archive(changes=lambda data: data['general/2024-01-01.json'].append(data['general/2024-01-01.json'][0]))]
    invalid = io.BytesIO()
    with zipfile.ZipFile(invalid, 'w') as writer:
        writer.writestr('users.json', '[{"id":"U1","id":"U2"}]'); writer.writestr('channels.json', '[]')
    variants.append(invalid.getvalue())
    for raw in variants:
        response = importer[0].post(BASE + '/preview', headers=importer[2]['owner'], files={'file': ('s.zip', raw)})
        assert response.status_code == 422, response.text


def test_expiry_discard_and_reupload_do_not_retain_archive(importer):
    raw = archive()
    job = preview(importer, raw)
    with Session(importer[1]) as session:
        row = session.get(SlackImport, job['id']); row.expires_at = datetime.utcnow() - timedelta(seconds=1)
        session.add(row); session.commit(); assert service.expire_previews(session) == 1
        assert session.get(SlackImport, row.id).encrypted_archive == ''
    assert confirm(importer, job).status_code == 409
    renewed = preview(importer, raw); assert renewed['id'] == job['id'] and renewed['status'] == 'preview'
    assert importer[0].delete(BASE + '/' + job['id'], headers=importer[2]['owner']).status_code == 200
    assert confirm(importer, job).status_code == 409
    assert preview(importer)['status'] == 'preview'


def test_import_file_failure_rolls_back_channels_messages_and_owned_blobs(importer, monkeypatch):
    job = preview(importer)
    original = service.storage_service.save_file_sync
    def fail(file_id, content):
        raise OSError('Synthetic storage outage')
    monkeypatch.setattr(service.storage_service, 'save_file_sync', fail)
    with pytest.raises(OSError): confirm(importer, job)
    with Session(importer[1]) as session:
        assert len(session.exec(select(Channel)).all()) == 4
        assert session.exec(select(Message)).all() == []
        assert session.get(SlackImport, job['id']).status == 'preview'
    monkeypatch.setattr(service.storage_service, 'save_file_sync', original)
    assert confirm(importer, job).status_code == 200


@pytest.mark.parametrize('parents', [
    {'1704067200.000001': '1704067201.000001', '1704067201.000001': '1704067200.000001'},
    {'1704067201.000001': '1704067200.000001', '1704067202.000001': '1704067201.000001'},
])
def test_nested_and_cyclic_slack_threads_rejected_before_preview(importer, parents):
    def modify(data):
        for row in data['general/2024-01-01.json']:
            if row['ts'] in parents: row['thread_ts'] = parents[row['ts']]
    response = importer[0].post(BASE + '/preview', headers=importer[2]['owner'], files={'file': ('s.zip', archive(changes=modify))})
    assert response.status_code == 422, response.text
    with Session(importer[1]) as session:
        assert session.exec(select(SlackImport)).all() == []


def test_uncertain_storage_write_is_tracked_before_save_returns(importer, monkeypatch):
    job = preview(importer)
    original = service.storage_service.save_file_sync
    written = []
    def uncertain(file_id, content):
        original(file_id, content); written.append(file_id)
        raise OSError('Synthetic acknowledgement lost after durable storage write')
    monkeypatch.setattr(service.storage_service, 'save_file_sync', uncertain)
    with pytest.raises(OSError, match='acknowledgement lost'): confirm(importer, job)
    assert len(written) == 1 and service.storage_service.read_file(written[0]) is None
    with Session(importer[1]) as session:
        assert session.exec(select(File)).all() == []
        assert session.exec(select(Message)).all() == []
        assert len(session.exec(select(Channel)).all()) == 4


def test_rollback_attempts_all_owned_blob_erasures_despite_one_failure(importer, monkeypatch, caplog):
    def modify(data):
        data['general/2024-01-01.json'][2]['files'] = [{'id': 'F3', 'name': 'second.txt', 'mimetype': 'text/plain'}]
    job = preview(importer, archive(changes=modify, extra=lambda writer: writer.writestr('files/F3', b'second owned blob')))
    original_save = service.storage_service.save_file_sync
    original_erase = service.storage_service.delete_file_sync
    written, attempted = [], []
    def uncertain_second(file_id, content):
        location = original_save(file_id, content); written.append(file_id)
        if len(written) == 2: raise OSError('Original synthetic import write failure')
        return location
    def erase(file_id):
        attempted.append(file_id)
        if len(attempted) == 1: raise RuntimeError('Synthetic cleanup outage')
        return original_erase(file_id)
    monkeypatch.setattr(service.storage_service, 'save_file_sync', uncertain_second)
    monkeypatch.setattr(service.storage_service, 'delete_file_sync', erase)
    with pytest.raises(OSError, match='Original synthetic import write failure'): confirm(importer, job)
    assert attempted == written and len(written) == 2
    assert service.storage_service.read_file(written[1]) is None
    assert 'operator reconciliation required' in caplog.text
    assert 'second.txt' not in caplog.text and 'second owned blob' not in caplog.text
    # The remaining fixture blob is explicitly owned by this test.
    original_erase(written[0])
    with Session(importer[1]) as session:
        assert session.exec(select(File)).all() == []
        assert session.exec(select(Message)).all() == []
        assert session.get(SlackImport, job['id']).status == 'preview'


def test_storage_collision_never_erases_a_blob_the_import_did_not_create(importer, monkeypatch):
    job = preview(importer); attempted = []
    def collision(*_): raise FileExistsError('Synthetic immutable collision')
    monkeypatch.setattr(service.storage_service, 'save_file_sync', collision)
    monkeypatch.setattr(service.storage_service, 'delete_file_sync', lambda file_id: attempted.append(file_id))
    with pytest.raises(FileExistsError): confirm(importer, job)
    assert attempted == []


@pytest.mark.parametrize('change', ['blocked', 'guest'])
def test_mapping_active_non_guest_authority_rechecked_after_storage_io(importer, monkeypatch, change):
    from app.models.workspace import WorkspaceMember
    job = preview(importer); original = service.authorize; written = []
    original_save = service.storage_service.save_file_sync
    def save(file_id, content): written.append(file_id); return original_save(file_id, content)
    def authorize(session, workspace_id, actor_id, *, locked=False):
        if not locked:
            if change == 'blocked':
                member = session.get(User, 'member'); member.is_blocked = True
            else:
                member = session.get(WorkspaceMember, (workspace_id, 'member')); member.role = 'guest'
            session.add(member); session.flush()
        return original(session, workspace_id, actor_id, locked=locked)
    monkeypatch.setattr(service.storage_service, 'save_file_sync', save)
    monkeypatch.setattr(service, 'authorize', authorize)
    response = confirm(importer, job)
    assert response.status_code == 422, response.text
    assert written and all(service.storage_service.read_file(file_id) is None for file_id in written)
    with Session(importer[1]) as session:
        assert session.exec(select(Message)).all() == []
        assert session.exec(select(File)).all() == []


def test_importing_admin_workspace_authority_rechecked_after_storage_io(importer, monkeypatch):
    from app.models.workspace import WorkspaceMember
    job = preview(importer); original = service.authorize; written = []
    original_save = service.storage_service.save_file_sync
    def save(file_id, content): written.append(file_id); return original_save(file_id, content)
    def authorize(session, workspace_id, actor_id, *, locked=False):
        if not locked:
            member = session.get(WorkspaceMember, (workspace_id, actor_id)); member.role = 'member'
            session.add(member); session.flush()
        return original(session, workspace_id, actor_id, locked=locked)
    monkeypatch.setattr(service.storage_service, 'save_file_sync', save)
    monkeypatch.setattr(service, 'authorize', authorize)
    response = confirm(importer, job)
    assert response.status_code == 403, response.text
    assert written and all(service.storage_service.read_file(file_id) is None for file_id in written)
    with Session(importer[1]) as session:
        assert session.exec(select(Message)).all() == []
        assert session.exec(select(File)).all() == []
