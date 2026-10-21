"""Recordings are scoped, immutable, bounded and never server-decrypted for secure channels."""
from fastapi import HTTPException
from sqlmodel import Session
from app.api import clips
from app.models.clips import ChannelClip
from app.models.file import File
from app.models.secure_channel import SecureChannel
from app.services import shared_documents as content
from tests.test_shared_documents import documents, headers


def setup(d):
    d.client.app.include_router(clips.router)
    with Session(d.engine) as session:
        session.add_all([File(id='a' * 32, channel_id='room', uploader_id='member', filename='voice.webm', content_type='audio/webm', size=1024, storage_path='unused'), File(id='b' * 32, channel_id='other', uploader_id='outsider', filename='private.webm', content_type='audio/webm', size=1024, storage_path='unused')]); session.commit()


def test_native_clip_private_fields_and_file_acl(documents):
    d = documents; setup(d); path = '/channels/room/clips'
    data = {'id': 'clip', 'file_id': 'a' * 32, 'title': 'Confidential update', 'duration': 4, 'transcript': 'Secret transcript', 'captions': [{'start': 0, 'end': 3, 'text': 'Secret transcript'}]}
    for _ in range(2):
        response = d.client.post(path, json=data, headers=headers()); assert response.status_code == 201, response.text
    assert response.json()['fields']['transcript'] == 'Secret transcript'
    with Session(d.engine) as session:
        row = session.get(ChannelClip, 'clip'); assert 'Secret transcript' not in row.encrypted_payload and 'Confidential update' not in row.encrypted_payload
    assert d.client.post(path, json={**data, 'title': 'Overwrite'}, headers=headers()).status_code == 409
    assert d.client.post(path, json={**data, 'id': 'wrong', 'file_id': 'b' * 32}, headers=headers()).status_code == 404
    assert d.client.get(path, headers=headers('outsider')).status_code == 403
    assert d.client.delete(path + '/clip', headers=headers('peer')).status_code == 403
    assert d.client.delete(path + '/clip', headers=headers('owner')).json() == {'deleted': True, 'media_retained': True}


def test_secure_clip_only_accepts_verified_opaque_reference(documents, monkeypatch):
    d = documents; setup(d); path = '/channels/room/clips'
    monkeypatch.setattr(content, 'is_secure_channel', lambda session, channel: True)
    verified = []
    monkeypatch.setattr(content, 'validate_secure_event', lambda session, channel, event, user: verified.append((channel, event, user)))
    data = {'id': 'secure', 'matrix_event_id': '$encrypted'}
    assert d.client.post(path, json={**data, 'title': 'Leaked'}, headers=headers()).status_code == 422
    assert d.client.post(path, json={'id': 'plain', 'file_id': 'a' * 32, 'title': 'Leak', 'duration': 3}, headers=headers()).status_code == 409
    response = d.client.post(path, json=data, headers=headers()); assert response.status_code == 201, response.text
    assert 'fields' not in response.json() and 'file' not in response.json() and verified
    with Session(d.engine) as session:
        row = session.get(ChannelClip, 'secure'); assert row.encrypted_payload is None and row.file_id is None


def test_clip_bounds_reject_unsupported_and_malformed_captions(documents):
    d = documents; setup(d); path = '/channels/room/clips'
    data = {'id': 'clip', 'file_id': 'a' * 32, 'title': 'Clip', 'duration': 301}
    assert d.client.post(path, json=data, headers=headers()).status_code == 422
    assert d.client.post(path, json={**data, 'duration': 2, 'captions': [{'start': 2, 'end': 1, 'text': 'bad'}]}, headers=headers()).status_code == 422
    assert d.client.post(path, json={**data, 'duration': 2, 'captions': [{'start': 1, 'end': 99, 'text': 'bad'}]}, headers=headers()).status_code == 422
