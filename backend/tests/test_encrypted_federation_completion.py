"""Real signed peer control plane for negotiated capabilities and opaque notices."""
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlmodel import Session, select

from app.api import encrypted_work
from app.models.channel import ChannelMember
from app.models.connect_federation import FederatedGrant, FederatedConnection
from app.models.encrypted_work import EncryptedWorkAuthority, EncryptedWorkNotice, EncryptedWorkRemoteCursor
from app.models.notification import Notification
from app.services import connect_federation as federation, encrypted_work as work, matrix_bridge
from tests.test_connect_federation import pair
from tests.test_federated_work import setup, path


def configured(pair, monkeypatch):
    host, guest = setup(pair, monkeypatch)
    for client in pair.clients.values():
        client.app.include_router(encrypted_work.router)
    return host, guest


def test_signed_roster_negotiates_v2_and_preserves_legacy_peer_readers(pair, monkeypatch):
    host, guest = configured(pair, monkeypatch)
    context = pair.clients['a'].get(path(host, 'work-context'), headers=pair.headers('a', 'member'))
    assert context.status_code == 200, context.text
    assert context.json()['work_protocols'] == ['freecollab-federated-work/1', 'freecollab-federated-work/2', 'freecollab-encrypted-work/2', 'freecollab-work-authority/1']
    assert context.json()['room_protocols'] == ['freecollab-matrix-meeting/1']
    original = federation.local_roster
    def old_peer(*args, **kwargs):
        value = original(*args, **kwargs)
        if pair.active.get() == 'b':
            value.pop('work_protocols', None); value.pop('room_protocols', None)
        return value
    monkeypatch.setattr(federation, 'local_roster', old_peer)
    legacy = pair.clients['a'].get(path(host, 'work-context'), headers=pair.headers('a', 'member'))
    assert legacy.status_code == 200 and legacy.json()['work_protocols'] == ['freecollab-federated-work/1']
    assert legacy.json()['room_protocols'] == []
    unsupported = pair.clients['a'].get('/channels/' + host['channel_id'] + '/encrypted-work/context', headers=pair.headers('a', 'member'))
    assert unsupported.status_code == 200 and unsupported.json()['encrypted_work_supported'] is False


def test_signed_peer_cannot_advertise_unbounded_or_malformed_capabilities(pair, monkeypatch):
    host, _ = configured(pair, monkeypatch); original = federation.local_roster
    def malformed(*args, **kwargs):
        value = original(*args, **kwargs)
        if pair.active.get() == 'b': value['work_protocols'] = ['freecollab-encrypted-work/2\nignored']
        return value
    monkeypatch.setattr(federation, 'local_roster', malformed)
    response = pair.clients['a'].get(path(host, 'work-context'), headers=pair.headers('a', 'member'))
    assert response.status_code == 503, response.text


def test_publication_time_peer_admin_receipts_are_signed_and_exactly_bound(pair, monkeypatch):
    host, guest = configured(pair, monkeypatch)
    assert pair.clients['a'].post(path(host), headers=pair.headers('a', 'member'), json={'matrix_event_id': '$owned-a'}).status_code == 201
    target = '/channels/' + guest['channel_id'] + '/encrypted-work/authority?event_id=$owned-a'
    proof = pair.clients['b'].get(target, headers=pair.headers('b', 'member'))
    assert proof.status_code == 200, proof.text
    assert proof.json()['role'] == 'member' and proof.json()['matrix_user_id'] == '@fc_member:matrix.a.example.com'
    assert proof.json()['issuer'] == 'matrix.a.example.com'
    assert any(envelope['operation'] == 'work-authority' for envelope in pair.observed)
    with Session(pair.engines['a']) as session:
        member = session.get(ChannelMember, (host['channel_id'], 'member')); member.role = 'admin'; session.add(member); session.commit()
    assert pair.clients['b'].get(target, headers=pair.headers('b', 'member')).json()['role'] == 'member'
    original = matrix_bridge.validate_secure_event
    def another(session, channel, event, actor):
        return original(session, channel, '$owned-a' if event == '$later-admin' else event, actor)
    monkeypatch.setattr(matrix_bridge, 'validate_secure_event', another)
    assert pair.clients['a'].post(path(host), headers=pair.headers('a', 'member'), json={'matrix_event_id': '$later-admin'}).status_code == 201
    later = pair.clients['b'].get(target.replace('$owned-a', '$later-admin'), headers=pair.headers('b', 'member'))
    assert later.status_code == 200 and later.json()['role'] == 'admin', later.text
    with Session(pair.engines['a']) as session:
        receipts = session.exec(select(EncryptedWorkAuthority)).all()
        assert len(receipts) == 2 and {row.role for row in receipts} == {'member', 'admin'}
    with Session(pair.engines['b']) as session:
        assert session.exec(select(EncryptedWorkAuthority)).all() == []


def test_opaque_peer_progress_delivers_one_coalesced_generic_notice(pair, monkeypatch):
    host, guest = configured(pair, monkeypatch)
    assert pair.clients['a'].post(path(host), headers=pair.headers('a', 'member'), json={'matrix_event_id': '$owned-a'}).status_code == 201
    scope = pair.active.set('b')
    try:
        with Session(pair.engines['b']) as session:
            assert work.run_pending(session, limit=1) == 1
            notifications = session.exec(select(Notification)).all()
            assert len(notifications) == 1
            notification = notifications[0]
            assert notification.user_id == 'member' and notification.title == 'Encrypted work updated' and notification.body is None
            notification.read = True; session.add(notification); session.commit()
            assert work.run_pending(session, limit=1) == 0
            cursor = session.exec(select(EncryptedWorkRemoteCursor)).one()
            cursor.checked_at -= timedelta(seconds=16); session.add(cursor); session.commit()
            assert work.run_pending(session, limit=1) == 1
            assert session.exec(select(Notification)).one().read is True
            assert session.exec(select(EncryptedWorkNotice)).one().sequence > 0
            assert session.exec(select(EncryptedWorkRemoteCursor)).one().last_error is None
            status = work.queue_status(session)
            assert status['scheduled'] == 1 and status['failed'] == 0
    finally: pair.active.reset(scope)


def test_recipient_removed_during_peer_lookup_gets_no_retained_notice(pair, monkeypatch):
    host, guest = configured(pair, monkeypatch)
    assert pair.clients['a'].post(path(host), headers=pair.headers('a', 'member'), json={'matrix_event_id': '$owned-a'}).status_code == 201
    original = federation.call_peer
    def removed(session, *args, **kwargs):
        result = original(session, *args, **kwargs)
        if pair.active.get() == 'b' and args[1] == 'work-index':
            with Session(pair.engines['b']) as other:
                other.delete(other.get(ChannelMember, (guest['channel_id'], 'member')))
                grant = other.get(FederatedGrant, (guest['id'], 'member')); grant.revoked_at = datetime.utcnow(); other.add(grant); other.commit()
        return result
    monkeypatch.setattr(federation, 'call_peer', removed)
    scope = pair.active.set('b')
    try:
        with Session(pair.engines['b']) as session:
            work.run_pending(session, limit=1)
            assert session.exec(select(Notification)).all() == []
    finally: pair.active.reset(scope)


def test_peer_failure_diagnostics_never_retain_response_plaintext(pair, monkeypatch):
    configured(pair, monkeypatch)
    def failed(*_args, **_kwargs): raise HTTPException(503, 'sensitive response text must not persist')
    monkeypatch.setattr(federation, 'call_peer', failed)
    with Session(pair.engines['a']) as session:
        work.run_pending(session, limit=1)
        cursor = session.exec(select(EncryptedWorkRemoteCursor)).one()
        assert cursor.last_error == 'peer_unavailable'
        assert 'sensitive' not in str(cursor.model_dump())
        assert work.queue_status(session)['failed'] == 1


def test_peer_failure_after_connection_revocation_cannot_recreate_progress(pair, monkeypatch):
    host, _guest = configured(pair, monkeypatch)
    def failed(*_args, **_kwargs):
        with Session(pair.engines['a']) as other:
            row = other.get(FederatedConnection, host['id']); row.status = 'revoked'
            other.add(row); other.commit()
        raise HTTPException(503, 'untrusted remote failure')
    monkeypatch.setattr(federation, 'call_peer', failed)
    with Session(pair.engines['a']) as session:
        assert work.run_pending(session, limit=1) == 0
        assert session.exec(select(EncryptedWorkRemoteCursor)).all() == []
