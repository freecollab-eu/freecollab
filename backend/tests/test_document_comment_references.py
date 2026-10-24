"""Document comment references remain bound, authorized and encrypted at rest."""
from sqlmodel import Session
from app.models.shared_documents import SharedDocumentComment
from app.models.secure_channel import SecureChannel
from app.services import shared_documents as service
from tests.test_shared_documents import documents, create, headers


def test_comment_reference_roundtrip_trims_utf16_and_retains_identity(documents):
    d = documents
    assert create(d).status_code == 201
    path = '/channels/room/shared-documents/doc/comments'
    source = {'id': 'chosen', 'text': '  🌿 Ask @Peer  ', 'references': [{'kind': 'person', 'id': 'peer', 'start': 9, 'end': 14}]}
    response = d.client.post(path, json=source, headers=headers())
    assert response.status_code == 201, response.text
    expected = {'text': '🌿 Ask @Peer', 'references': [{'kind': 'person', 'id': 'peer', 'start': 7, 'end': 12}]}
    assert response.json()['fields'] == expected
    assert d.client.get(path, headers=headers('peer')).json()[0]['fields'] == expected
    assert d.client.post(path, json=source, headers=headers()).status_code == 201
    changed = {**source, 'references': [{**source['references'][0], 'id': 'owner'}]}
    assert d.client.post(path, json=changed, headers=headers()).status_code == 409
    with Session(d.engine) as session:
        raw = session.get(SharedDocumentComment, 'chosen').encrypted_payload
        assert '@Peer' not in raw and '"references"' not in raw and '"peer"' not in raw


def test_comment_rejects_fabricated_cross_channel_and_split_unicode_references(documents):
    d = documents
    assert create(d).status_code == 201
    path = '/channels/room/shared-documents/doc/comments'
    for index, reference in enumerate([
        {'kind': 'person', 'id': 'outsider', 'start': 3, 'end': 8},
        {'kind': 'document', 'id': 'missing', 'start': 3, 'end': 8},
        {'kind': 'person', 'id': 'peer', 'start': 1, 'end': 8},
        {'kind': 'person', 'id': 'peer', 'start': 3, 'end': 99},
    ]):
        response = d.client.post(path, json={'id': 'invalid-' + str(index), 'text': '🌿 @Peer', 'references': [reference]}, headers=headers())
        assert response.status_code in (404, 422), response.text
    assert d.client.get(path, headers=headers()).json() == []


def test_secure_comments_never_accept_reference_metadata_beside_opaque_event(documents, monkeypatch):
    d = documents
    with Session(d.engine) as session:
        session.add(SecureChannel(channel_id='room', created_by='owner', status='active'))
        session.commit()
    monkeypatch.setattr(service, 'validate_secure_event', lambda *args: None)
    response = d.client.post('/channels/room/shared-documents', json={'id': 'secure', 'update_id': 'first', 'matrix_event_id': '$created'}, headers=headers())
    assert response.status_code == 201
    path = '/channels/room/shared-documents/secure/comments'
    for refs in [[], [{'kind': 'person', 'id': 'peer', 'start': 0, 'end': 5}]]:
        response = d.client.post(path, json={'id': 'leak', 'matrix_event_id': '$opaque', 'references': refs}, headers=headers())
        assert response.status_code == 422
    response = d.client.post(path, json={'id': 'opaque', 'matrix_event_id': '$opaque'}, headers=headers())
    assert response.status_code == 201 and 'fields' not in response.json()
    with Session(d.engine) as session:
        row = session.get(SharedDocumentComment, 'opaque')
        assert row.encrypted_payload is None and row.matrix_event_id == '$opaque'


def test_document_change_hints_contain_only_scoped_identifiers_after_commit(documents, monkeypatch):
    from app.realtime import socket
    events = []
    async def broadcast(channel, event, payload):
        with Session(documents.engine) as session:
            from app.models.shared_documents import SharedDocument
            assert session.get(SharedDocument, payload['document_id']) is not None
        events.append((channel, event, payload))
    monkeypatch.setattr(socket, 'broadcast_to_channel', broadcast)
    assert create(documents).status_code == 201
    response = documents.client.post('/channels/room/shared-documents/doc/comments', json={'id': 'hint', 'text': 'Sensitive @Peer', 'references': [{'kind': 'person', 'id': 'peer', 'start': 10, 'end': 15}]}, headers=headers())
    assert response.status_code == 201, response.text
    assert len(events) == 2
    for channel, event, payload in events:
        assert channel == 'room' and event == 'shared_document_changed'
        assert set(payload) == {'channel_id', 'document_id', 'part'}
        assert payload['channel_id'] == 'room' and payload['document_id'] == 'doc'
    assert events[-1][2]['part'] == 'comments'


import pytest

@pytest.mark.parametrize('change', ['revoked', 'removed', 'blocked', 'archived', 'encrypted'])
def test_reference_lookup_cannot_retain_stale_comment_write_authority(documents, monkeypatch, change):
    from app.api import shared_documents
    from app.models.channel import ChannelMember
    from app.models.shared_documents import SharedDocument
    from app.models.user import User
    from app.services.auth import create_token, revoke_token
    d = documents
    assert create(d).status_code == 201
    original = shared_documents.validate_references
    token = create_token('member')
    def lookup(session, *args, **kwargs):
        result = original(session, *args, **kwargs)
        if change == 'revoked':
            revoke_token(token, session)
        elif change == 'removed':
            session.delete(session.get(ChannelMember, ('room', 'member')))
            session.commit()
        elif change == 'blocked':
            actor = session.get(User, 'member'); actor.is_blocked = True
            session.add(actor); session.commit()
        elif change == 'archived':
            document = session.get(SharedDocument, 'doc'); document.archived = True
            session.add(document); session.commit()
        else:
            session.add(SecureChannel(channel_id='room', created_by='owner', status='active')); session.commit()
        return result
    monkeypatch.setattr(shared_documents, 'validate_references', lookup)
    response = d.client.post('/channels/room/shared-documents/doc/comments', json={'id': 'late', 'text': 'Ask @Peer', 'references': [{'kind': 'person', 'id': 'peer', 'start': 4, 'end': 9}]}, headers={'Authorization': 'Bearer ' + token})
    assert response.status_code == {'revoked': 401, 'blocked': 401, 'removed': 403, 'archived': 409, 'encrypted': 409}[change], response.text
    with Session(d.engine) as session:
        assert session.get(SharedDocumentComment, 'late') is None
