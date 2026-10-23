"""Real account photos obey identity, current directory scope and byte erasure."""
from datetime import datetime
from hashlib import sha256
import struct
from types import SimpleNamespace
import zlib

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import delete, event
from sqlmodel import Session, SQLModel, create_engine, select

from app import main as _register_product_models  # noqa: F401
from app.api import avatars, data_control
from app.config import settings
from app.database import get_session
from app.middleware import RequestBodyLimitMiddleware
from app.models.avatar import AvatarErasure, UserAvatar
from app.models.channel import Channel, ChannelMember
from app.models.direct_message import UserBlock
from app.models.session import ActiveSession
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceChannel, WorkspaceMember
from app.services import avatars as service, storage
from app.services.auth import create_token, hash_password, token_digest

PASSWORD = "Profile-photo-only!2026"


def chunk(kind, payload):
    return struct.pack('!I', len(payload)) + kind + payload + struct.pack('!I', zlib.crc32(kind + payload) & 0xffffffff)


def png(*, width=2, height=2, color=2, raw=None, metadata=False, interlace=0):
    header = struct.pack('!IIBBBBB', width, height, 8, color, 0, 0, interlace)
    raw = raw if raw is not None else b'\0' + bytes([255, 0, 0, 0, 255, 0]) + b'\0' + bytes([0, 0, 255, 50, 60, 70])
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header)
            + (chunk(b'tEXt', b'Owner\0PRIVATE LOCATION EXIF') if metadata else b'')
            + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


@pytest.fixture
def photos(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'demo_instance', False)
    monkeypatch.setattr(settings, 'data_controls_enabled', True)
    monkeypatch.setattr(settings, 'matrix_enabled', False)
    blob_store = storage.StorageService(tmp_path / 'photos')
    monkeypatch.setattr(storage, 'storage_service', blob_store)
    engine = create_engine('sqlite:///' + str(tmp_path / 'photos.sqlite'), connect_args={'check_same_thread': False})
    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, _): connection.execute('PRAGMA foreign_keys=ON')
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([User(id=name, name=name.title(), email=name + '@example.invalid', password_hash=hash_password(PASSWORD))
                         for name in ('alice', 'bob', 'guest', 'outsider')])
        session.add_all([Workspace(id='a', name='Shared'), Workspace(id='b', name='Private')])
        session.commit()
        session.add_all([WorkspaceMember(workspace_id='a', user_id='alice', role='owner'),
                         WorkspaceMember(workspace_id='a', user_id='bob'),
                         WorkspaceMember(workspace_id='a', user_id='guest', role='guest'),
                         WorkspaceMember(workspace_id='b', user_id='outsider', role='owner')])
        session.add_all([Channel(id='shared', name='Shared', type='group'), Channel(id='guest-room', name='Guest room', type='group')])
        session.commit()
        session.add_all([WorkspaceChannel(workspace_id='a', channel_id=name) for name in ('shared', 'guest-room')])
        session.add_all([ChannelMember(channel_id='shared', user_id='alice', role='admin'), ChannelMember(channel_id='shared', user_id='bob'),
                         ChannelMember(channel_id='guest-room', user_id='alice', role='admin'), ChannelMember(channel_id='guest-room', user_id='guest')])
        session.commit()
        tokens = {name: create_token(name, session) for name in ('alice', 'bob', 'guest', 'outsider')}
    app = FastAPI()
    app.add_middleware(RequestBodyLimitMiddleware)
    app.include_router(avatars.router)
    app.include_router(data_control.account_router)
    def sessions():
        with Session(engine) as session: yield session
    app.dependency_overrides[get_session] = sessions
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, engine=engine, storage=blob_store, tokens=tokens)
    engine.dispose()


def auth(photos, name='alice'): return {'Authorization': 'Bearer ' + photos.tokens[name]}


def upload(photos, name='alice', data=None):
    return photos.client.post('/account/avatar', headers=auth(photos, name),
                              files={'file': ('private-filename.png', data or png(metadata=True), 'image/png')})


def test_native_upload_current_peer_read_replace_and_delete_use_private_verified_bytes(photos):
    response = upload(photos)
    assert response.status_code == 200, response.text
    with Session(photos.engine) as session:
        first = session.get(UserAvatar, 'alice')
        old_blob = first.blob_id
        assert first.width == first.height == 2
    for who in ('alice', 'bob', 'guest'):
        result = photos.client.get('/users/alice/avatar?workspace_id=a', headers=auth(photos, who))
        assert result.status_code == 200
        assert result.headers['content-type'] == 'image/png'
        assert result.headers['cache-control'] == 'private, no-store'
        assert result.headers['x-content-type-options'] == 'nosniff'
        assert b'PRIVATE LOCATION' not in result.content and b'tEXt' not in result.content
        assert result.content == service.normalize_avatar_png(png())[0]
    assert photos.client.get('/users/alice/avatar').status_code == 401
    for target in ('alice', 'unknown'):
        denied = photos.client.get('/users/' + target + '/avatar', headers=auth(photos, 'outsider'))
        assert denied.status_code == 404 and denied.json() == {'detail': 'Profile photo not found'}
    assert upload(photos, data=png(raw=b'\0' + b'\x10\x20\x30' * 2 + b'\0' + b'\x40\x50\x60' * 2)).status_code == 200
    assert photos.storage.read_file(old_blob) is None
    with Session(photos.engine) as session:
        current_blob = session.get(UserAvatar, 'alice').blob_id
        assert current_blob != old_blob and not session.exec(select(AvatarErasure)).all()
    assert photos.client.delete('/account/avatar', headers=auth(photos)).status_code == 204
    assert photos.storage.read_file(current_blob) is None
    assert photos.client.get('/users/alice/avatar', headers=auth(photos)).status_code == 404
    assert photos.client.delete('/account/avatar', headers=auth(photos)).status_code == 204


def test_guest_directory_and_personal_blocks_are_enforced(photos):
    assert upload(photos, 'bob').status_code == 200
    assert photos.client.get('/users/bob/avatar?workspace_id=a', headers=auth(photos, 'guest')).status_code == 404
    assert photos.client.get('/users/bob/avatar?workspace_id=b', headers=auth(photos)).status_code == 404
    with Session(photos.engine) as session:
        session.add(UserBlock(blocker_id='bob', blocked_id='alice')); session.commit()
    assert photos.client.get('/users/bob/avatar', headers=auth(photos)).status_code == 404


def test_former_workspace_member_with_dangling_channel_membership_has_no_photo_access(photos):
    assert upload(photos).status_code == 200
    with Session(photos.engine) as session:
        session.delete(session.get(WorkspaceMember, ('a', 'bob'))); session.commit()
        assert session.get(ChannelMember, ('shared', 'bob'))
    assert photos.client.get('/users/alice/avatar?workspace_id=a', headers=auth(photos, 'bob')).status_code == 404
    assert photos.client.get('/users/alice/avatar', headers=auth(photos, 'bob')).status_code == 404


@pytest.mark.parametrize('change', ['membership', 'session', 'target', 'deleted_actor'])
def test_authority_is_rechecked_after_private_storage_io(photos, monkeypatch, change):
    assert upload(photos).status_code == 200
    original = photos.storage.read_file
    def delayed_read(*args, **kwargs):
        result = original(*args, **kwargs)
        with Session(photos.engine) as session:
            if change == 'membership': session.delete(session.get(WorkspaceMember, ('a', 'bob')))
            elif change == 'session':
                row = session.exec(select(ActiveSession).where(ActiveSession.token_hash == token_digest(photos.tokens['bob']))).one()
                row.revoked_at = datetime.utcnow(); session.add(row)
            elif change == 'target':
                row = session.get(User, 'alice'); row.is_blocked = True; session.add(row)
            else:
                # Disposable workspaces really remove their visitor's User row.
                # Keep the route's previously authenticated ORM identity alive
                # while removing every FK reference owned by this actor.
                from app.models.account_security import AccountSecurity
                session.execute(delete(ChannelMember).where(ChannelMember.user_id == 'bob'))
                session.execute(delete(WorkspaceMember).where(WorkspaceMember.user_id == 'bob'))
                session.execute(delete(ActiveSession).where(ActiveSession.user_id == 'bob'))
                session.execute(delete(AccountSecurity).where(AccountSecurity.user_id == 'bob'))
                session.execute(delete(User).where(User.id == 'bob'))
            session.commit()
        return result
    monkeypatch.setattr(photos.storage, 'read_file', delayed_read)
    result = photos.client.get('/users/alice/avatar?workspace_id=a', headers=auth(photos, 'bob'))
    assert result.status_code == (401 if change in ('session', 'deleted_actor') else 404)
    assert not result.headers.get('content-type', '').startswith('image/')


def test_failed_private_storage_erasure_retries_durably_without_serving_removed_photo(photos, monkeypatch):
    assert upload(photos).status_code == 200
    with Session(photos.engine) as session: blob = session.get(UserAvatar, 'alice').blob_id
    original = photos.storage.delete_file_sync
    monkeypatch.setattr(photos.storage, 'delete_file_sync', lambda *_: (_ for _ in ()).throw(OSError('private storage temporarily unavailable')))
    assert photos.client.delete('/account/avatar', headers=auth(photos)).status_code == 204
    assert photos.client.get('/users/alice/avatar', headers=auth(photos)).status_code == 404
    with Session(photos.engine) as session:
        assert session.get(UserAvatar, 'alice') is None and session.get(AvatarErasure, blob)
    monkeypatch.setattr(photos.storage, 'delete_file_sync', original)
    with Session(photos.engine) as session:
        assert service.erase_pending_avatar_blobs(session) == 1
        assert not session.exec(select(AvatarErasure)).all()
        assert service.erase_pending_avatar_blobs(session) == 0
    assert photos.storage.read_file(blob) is None


def test_erasure_worker_returns_completed_count_and_preserves_its_twenty_record_bound(photos):
    blobs = [f'{number:032x}' for number in range(21)]
    with Session(photos.engine) as session:
        for blob in blobs:
            photos.storage.save_file_sync(blob, b'old private photo bytes')
            session.add(AvatarErasure(blob_id=blob, user_id='alice'))
        session.commit()
        assert service.erase_pending_avatar_blobs(session, maximum=100) == 20
        assert len(session.exec(select(AvatarErasure)).all()) == 1
        assert sum(photos.storage.read_file(blob) is not None for blob in blobs) == 1
        assert service.erase_pending_avatar_blobs(session) == 1
        assert service.erase_pending_avatar_blobs(session) == 0
    assert not list(photos.storage.storage_path.iterdir())


def test_account_identity_erasure_includes_own_photo_and_keeps_peer_photo(photos):
    assert upload(photos, 'alice').status_code == upload(photos, 'bob').status_code == 200
    with Session(photos.engine) as session:
        own_blob, peer_blob = session.get(UserAvatar, 'bob').blob_id, session.get(UserAvatar, 'alice').blob_id
    preview = photos.client.get('/account/data-closure', headers=auth(photos, 'bob'))
    assert preview.status_code == 200, preview.text
    assert preview.json()['can_erase_identity']
    result = photos.client.post('/account/data-closure', headers=auth(photos, 'bob'), json={
        'password': PASSWORD, 'confirmation': 'ERASE MY ACCOUNT', 'preview_fingerprint': preview.json()['fingerprint'],
        'acknowledge_retained_shared_authorship_and_backups': True})
    assert result.status_code == 200, result.text
    assert photos.storage.read_file(own_blob) is None and photos.storage.read_file(peer_blob)
    with Session(photos.engine) as session:
        assert session.get(UserAvatar, 'bob') is None
        assert session.get(UserAvatar, 'alice') and session.get(User, 'bob').is_blocked


def test_public_demo_has_real_photo_reads_but_no_personal_photo_writes(photos, monkeypatch):
    assert upload(photos).status_code == 200
    monkeypatch.setattr(settings, 'demo_instance', True)
    assert photos.client.get('/users/alice/avatar', headers=auth(photos)).status_code == 200
    assert upload(photos).status_code == photos.client.delete('/account/avatar', headers=auth(photos)).status_code == 403
    from app.services.public_demo_boundary import allowed_public_demo_route
    assert allowed_public_demo_route('GET', '/users/alice/avatar')
    assert not allowed_public_demo_route('POST', '/account/avatar')
    assert not allowed_public_demo_route('DELETE', '/account/avatar')


@pytest.mark.parametrize('bad', [b'<svg xmlns="http://www.w3.org/2000/svg"/>', b'JPEG', png() + b'trailing',
    png(interlace=1), png(width=513), png(color=3), png(raw=b'\x05' + b'\0' * 6 + b'\0' * 7),
    png(raw=b'\0' * 1_000_000), png(raw=b'\0'), png()[:-1] + b'\1'])
def test_invalid_oversized_animated_or_corrupt_images_never_create_a_blob(photos, bad):
    before = set(photos.storage.storage_path.iterdir())
    response = upload(photos, data=bad)
    assert response.status_code == 422
    assert set(photos.storage.storage_path.iterdir()) == before
    with Session(photos.engine) as session: assert not session.exec(select(UserAvatar)).all()


def test_streaming_request_limit_applies_before_multipart_can_spool_a_large_photo(photos):
    response = upload(photos, data=png() + b'X' * (1024 * 1024))
    assert response.status_code == 413
    assert not list(photos.storage.storage_path.iterdir())
