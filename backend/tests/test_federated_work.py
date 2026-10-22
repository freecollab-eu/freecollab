"""Opaque work discovery on two independent installations; no content relay."""
from datetime import datetime

from fastapi import HTTPException
from sqlmodel import Session, select

from tests.test_connect_federation import pair, activate, grant
from app.models.channel import ChannelMember
from app.models.connect_federation import FederatedConnection, FederatedGrant
from app.models.federated_work import FederatedWorkPointer
from app.services import federated_work, matrix_bridge


def setup(pair, monkeypatch):
    host, guest = activate(pair)
    grant(pair, 'a', host); grant(pair, 'b', guest)
    def event(session, channel_id, event_id, user_id):
        side = 'a' if channel_id == host['channel_id'] else 'b'
        if event_id != '$owned-' + side:
            raise HTTPException(400, 'Encrypted event does not belong to this sender and channel')
        return {'room_id': '!owned', 'sender': '@fc_' + user_id + ':matrix.' + side + '.example.com',
            'type': 'm.room.encrypted', 'content': {'ciphertext': 'opaque'}}
    monkeypatch.setattr(matrix_bridge, 'validate_secure_event', event)
    return host, guest


def path(connection, suffix='work-index'):
    return '/connect/federation/channels/' + connection['channel_id'] + '/' + suffix


def test_both_installations_discover_same_encrypted_events_without_plaintext_indexes(pair, monkeypatch):
    host, guest = setup(pair, monkeypatch)
    for side, connection in [('a', host), ('b', guest)]:
        result = pair.clients[side].post(path(connection), headers=pair.headers(side, 'member'), json={'matrix_event_id': '$owned-' + side})
        assert result.status_code == 201, result.text
    for side, connection in [('a', host), ('b', guest)]:
        result = pair.clients[side].get(path(connection), headers=pair.headers(side, 'member'))
        assert result.status_code == 200, result.text
        body = result.json()
        assert {row['matrix_event_id'] for row in body['local']['items'] + body['remote']['items']} == {'$owned-a', '$owned-b'}
        assert body['connection_id'] == host['id']
        with Session(pair.engines[side]) as session:
            pointers = session.exec(select(FederatedWorkPointer)).all()
            assert len(pointers) == 1
            assert set(pointers[0].model_dump()) == {'sequence', 'connection_id', 'matrix_event_id', 'matrix_user_id', 'published_by', 'created_at'}
            assert pointers[0].published_by == 'member'


def test_work_context_uses_current_global_members_and_never_enrolls_administrators(pair, monkeypatch):
    host, guest = setup(pair, monkeypatch)
    result = pair.clients['a'].get(path(host, 'work-context'), headers=pair.headers('a', 'member'))
    assert result.status_code == 200, result.text
    value = result.json()
    assert value['protocol'] == 'freecollab-federated-work/1'
    assert value['connection_id'] == host['id'] and value['channel_id'] == host['channel_id']
    assert value['matrix_user_id'] == '@fc_member:matrix.a.example.com'
    assert value['local_server'] == 'matrix.a.example.com' and value['peer_server'] == 'matrix.b.example.com'
    assert {row['matrix_user_id'] for row in value['members']} == {'@fc_member:matrix.a.example.com', '@fc_member:matrix.b.example.com'}
    assert pair.clients['a'].get(path(host, 'work-context'), headers=pair.headers('a', 'admin')).status_code == 403
    assert pair.clients['a'].get(path(host), headers=pair.headers('a', 'outsider')).status_code == 403
    assert pair.clients['a'].get(path(host)).status_code == 401


def test_plaintext_fields_and_nonowned_event_ids_cannot_enter_work_index(pair, monkeypatch):
    host, _ = setup(pair, monkeypatch)
    assert pair.clients['a'].post(path(host), headers=pair.headers('a', 'member'), json={'matrix_event_id': '$owned-a', 'title': 'Private title'}).status_code == 422
    assert pair.clients['a'].post(path(host), headers=pair.headers('a', 'member'), json={'matrix_event_id': '$different-author'}).status_code == 400
    with Session(pair.engines['a']) as session: assert session.exec(select(FederatedWorkPointer)).all() == []


def test_pointer_receipts_are_idempotent_and_page_cursors_do_not_replay_entries(pair, monkeypatch):
    host, _ = setup(pair, monkeypatch)
    results = [pair.clients['a'].post(path(host), headers=pair.headers('a', 'member'), json={'matrix_event_id': '$owned-a'}) for _ in range(2)]
    assert all(result.status_code == 201 for result in results)
    assert results[0].json()['sequence'] == results[1].json()['sequence']
    result = pair.clients['a'].get(path(host) + '?local_after=' + str(results[0].json()['sequence']), headers=pair.headers('a', 'member'))
    assert result.status_code == 200 and result.json()['local']['items'] == []
    assert pair.clients['a'].get(path(host) + '?local_after=-1', headers=pair.headers('a', 'member')).status_code == 422


def test_current_bilateral_revocation_blocks_new_publication_and_remote_index_reads(pair, monkeypatch):
    host, guest = setup(pair, monkeypatch)
    revoked = pair.clients['b'].delete('/connect/federation/workspaces/b/connections/' + guest['id'], headers=pair.headers('b'))
    assert revoked.status_code == 200
    for side, connection in [('a', host), ('b', guest)]:
        assert pair.clients[side].get(path(connection), headers=pair.headers(side, 'member')).status_code == 403
        assert pair.clients[side].post(path(connection), headers=pair.headers(side, 'member'), json={'matrix_event_id': '$owned-' + side}).status_code == 403


def test_remote_authority_ending_prevents_cached_index_from_authorizing_shared_work(pair, monkeypatch):
    host, guest = setup(pair, monkeypatch)
    with Session(pair.engines['b']) as session:
        connection = session.get(FederatedConnection, guest['id']); connection.status = 'revoked'; session.add(connection); session.commit()
    result = pair.clients['a'].get(path(host), headers=pair.headers('a', 'member'))
    assert result.status_code == 503, result.text


def test_local_revoke_during_matrix_validation_does_not_publish_stale_pointer(pair, monkeypatch):
    host, _ = setup(pair, monkeypatch)
    original = matrix_bridge.validate_secure_event
    def revoke(session, *args, **kwargs):
        event = original(session, *args, **kwargs)
        with Session(pair.engines['a']) as other:
            grant = other.get(FederatedGrant, (host['id'], 'member')); grant.revoked_at = datetime.utcnow(); other.add(grant)
            other.delete(other.get(ChannelMember, (host['channel_id'], 'member'))); other.commit()
        return event
    monkeypatch.setattr(matrix_bridge, 'validate_secure_event', revoke)
    result = pair.clients['a'].post(path(host), headers=pair.headers('a', 'member'), json={'matrix_event_id': '$owned-a'})
    assert result.status_code == 403, result.text
    with Session(pair.engines['a']) as session: assert session.exec(select(FederatedWorkPointer)).all() == []


def test_bounded_index_quota_does_not_block_existing_idempotent_receipt(pair, monkeypatch):
    host, _ = setup(pair, monkeypatch)
    monkeypatch.setattr(federated_work, 'MAX_POINTERS', 1)
    assert pair.clients['a'].post(path(host), headers=pair.headers('a', 'member'), json={'matrix_event_id': '$owned-a'}).status_code == 201
    assert pair.clients['a'].post(path(host), headers=pair.headers('a', 'member'), json={'matrix_event_id': '$owned-a'}).status_code == 201
    monkeypatch.setattr(matrix_bridge, 'validate_secure_event', lambda *args, **kwargs: {'sender': '@fc_member:matrix.a.example.com'})
    result = pair.clients['a'].post(path(host), headers=pair.headers('a', 'member'), json={'matrix_event_id': '$another-owned'})
    assert result.status_code == 409
