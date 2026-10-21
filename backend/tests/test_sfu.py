"""LiveKit JWT/Twirp protocol fixture and source-session revocation (no media simulation)."""
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace
import json
import jwt
import pytest
from pydantic import SecretStr
from sqlmodel import Session, select
from app.api import sfu
from app.config import settings
from app.models.sfu import SFURoom, SFUParticipant, SFUWorkerLease
from app.models.channel import ChannelMember
from app.services import sfu as service
from tests.test_meetings import team


@pytest.fixture
def livekit(team, monkeypatch):
    client, engine, tokens, scoped = team
    team = SimpleNamespace(client=client, engine=engine, tokens=tokens, scoped=scoped, headers=lambda user='owner': {'Authorization': 'Bearer ' + tokens[user]})
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            claims = jwt.decode(self.headers['Authorization'].removeprefix('Bearer '), 'f' * 32, algorithms=['HS256'], issuer='fixture-key')
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append((self.path, payload, claims)); self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(b'{}')
        def log_message(self, *_): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler); thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    for name, value in {'meetings_sfu_provider': 'livekit', 'meetings_sfu_url': 'ws://127.0.0.1:7880', 'meetings_sfu_api_url': f'http://127.0.0.1:{server.server_port}', 'meetings_sfu_api_key': 'fixture-key', 'meetings_sfu_api_secret': SecretStr('f' * 32), 'meetings_sfu_max_participants': 100}.items(): monkeypatch.setattr(settings, name, value)
    team.client.app.include_router(sfu.router)
    with Session(team.engine) as session: service.reconcile(session)
    team.calls = calls
    yield team
    server.shutdown(); server.server_close(); thread.join(timeout=2)


def create(d): return d.client.post('/channels/shared/large-meetings', json={}, headers=d.headers('owner'))
def join(d, room, user='member'): return d.client.post(f'/large-meetings/{room}/join', json={}, headers=d.headers(user))


def test_livekit_protocol_is_room_scoped_expiring_and_honest(livekit):
    d = livekit; created = create(d); assert created.status_code == 201, created.text
    room = created.json(); assert room['max_participants'] == 100 and room['encryption'] == 'sfu-dtls-srtp'
    access = join(d, room['id']); assert access.status_code == 200, access.text
    token = access.json()['token']; claims = jwt.decode(token, 'f' * 32, algorithms=['HS256'])
    assert claims['exp'] - claims['iat'] == 60 and claims['sub'] == access.json()['participant_id']
    assert claims['video'] == {'roomJoin': True, 'room': 'freecollab_' + room['id'], 'canPublish': True, 'canSubscribe': True, 'canPublishData': False}
    creation = next(call for call in d.calls if call[0].endswith('/CreateRoom'))
    assert creation[1]['max_participants'] == 100 and creation[2]['video'] == {'roomCreate': True}
    assert join(d, room['id'], 'outsider').status_code == 403
    assert join(d, room['id']).status_code == 409
    status = d.client.get('/channels/shared/large-meetings', headers=d.headers()).json()['status']
    assert status['ready'] and 'token' not in status and 'secret' not in json.dumps(status)


def test_revocation_worker_removes_device_without_client_poll(livekit):
    d = livekit; room = create(d).json()['id']; access = join(d, room).json(); participant = access['participant_id']
    with Session(d.engine) as session:
        session.delete(session.get(ChannelMember, ('shared', 'member'))); session.commit()
        result = service.reconcile(session); assert result['removed'] == 1
        row = session.get(SFUParticipant, participant); assert row.state == 'left' and row.disconnected
    assert d.calls[-1][0].endswith('/RemoveParticipant') and d.calls[-1][1] == {'room': 'freecollab_' + room, 'identity': participant}
    assert d.calls[-1][2]['video'] == {'roomAdmin': True, 'room': 'freecollab_' + room}


def test_sfu_host_leave_keeps_room_and_expired_heartbeat_fails_closed(livekit):
    d = livekit; room = create(d).json()['id']; access = join(d, room, 'owner').json()
    path = f"/large-meetings/{room}/participants/{access['participant_id']}"
    assert d.client.post(path + '/leave', json={}, headers=d.headers('owner')).status_code == 200
    with Session(d.engine) as session:
        assert session.get(SFURoom, room).ended_at is None
        lease = session.get(SFUWorkerLease, 'livekit'); lease.seen_at = datetime.utcnow() - timedelta(minutes=1); session.add(lease); session.commit()
    assert join(d, room).status_code == 503
    with Session(d.engine) as session: service.reconcile(session)
    assert join(d, room).status_code == 200
    assert d.client.post(f'/large-meetings/{room}/end', json={}, headers=d.headers('member')).status_code == 403
    assert d.client.post(f'/large-meetings/{room}/end', json={}, headers=d.headers('owner')).status_code == 200
    assert join(d, room).status_code == 410


def test_tls_and_missing_credentials_report_not_configured(livekit, monkeypatch):
    monkeypatch.setattr(settings, 'environment', 'production')
    with Session(livekit.engine) as session:
        assert not service.status(session)['configured']
    assert create(livekit).status_code == 503


def participant(identifier, room, *, checked=None, state='admitted'):
    return SFUParticipant(id=identifier, meeting_id=room, user_id='member', name=identifier, state=state,
        auth_token_hash='fixture-source', auth_version=0, expires_at=datetime.utcnow() + timedelta(hours=1),
        authorization_checked_at=checked or datetime.utcnow(), last_seen_at=datetime.utcnow())


def test_more_than_full_batch_rotates_to_tail_and_stale_backlog_never_looks_ready(livekit, monkeypatch):
    """Actual 2,001-row SQL queue; isolated validator tests scheduling, not JWTs."""
    d = livekit; room = create(d).json()['id']; old = datetime.utcnow() - timedelta(minutes=1)
    with Session(d.engine) as session:
        session.add_all([participant(f'first-{index:04}', room, checked=old) for index in range(2000)] + [participant('z-revoked-tail', room, checked=old)])
        session.commit()
    monkeypatch.setattr(service, 'participant_valid', lambda session, row, room: row.id != 'z-revoked-tail')
    # Keep the test focused on the real batch bound independently of CI speed.
    monkeypatch.setattr(service, 'RECONCILE_BUDGET_SECONDS', 60)
    with Session(d.engine) as session:
        first = service.reconcile(session); assert first['checked'] == 2000 and first['removed'] == 0
        assert session.get(SFUWorkerLease, 'livekit').seen_at > datetime.utcnow() - timedelta(seconds=10)
        assert not service.status(session)['ready']  # Fresh worker cannot mask skipped stale tail.
    assert join(d, room).status_code == 503
    with Session(d.engine) as session:
        second = service.reconcile(session); assert second['checked'] == 2000 and second['removed'] == 1
        assert session.get(SFUParticipant, 'z-revoked-tail').disconnected
        assert service.status(session)['ready']
    # Exactly 2,000 current devices can heartbeat, but another admission is fenced.
    assert join(d, room).status_code == 503
    assert any(call[0].endswith('/RemoveParticipant') and call[1]['identity'] == 'z-revoked-tail' for call in d.calls)


def test_failed_removal_rotates_fairly_preserves_host_ban_and_fences_readiness(livekit, monkeypatch):
    from fastapi import HTTPException
    d = livekit; room = create(d).json()['id']; old = datetime.utcnow() - timedelta(minutes=1)
    with Session(d.engine) as session:
        session.add_all([participant('a-failed-removed', room, checked=old, state='removed'), participant('z-next-revoked', room, checked=old)]); session.commit()
    monkeypatch.setattr(service, 'RECONCILE_BATCH_SIZE', 1)
    monkeypatch.setattr(service, 'participant_valid', lambda *_: False)
    normal = service.remove
    def removal(room, row):
        if row.id == 'a-failed-removed': raise HTTPException(503, 'Private fixture removal failure')
        return normal(room, row)
    monkeypatch.setattr(service, 'remove', removal)
    with Session(d.engine) as session:
        assert service.reconcile(session)['checked'] == 1
        assert session.get(SFUParticipant, 'a-failed-removed').state == 'removed'
        assert not service.status(session)['ready']
        assert service.reconcile(session)['removed'] == 1
        assert session.get(SFUParticipant, 'z-next-revoked').disconnected
        assert not service.status(session)['ready']  # Pending failed RPC remains a fence.


def test_exhausted_scan_budget_cannot_refresh_worker_readiness(livekit, monkeypatch):
    d = livekit; room = create(d).json()['id']
    with Session(d.engine) as session:
        session.add(participant('not-yet-checked', room)); session.commit()
        original = session.get(SFUWorkerLease, 'livekit').seen_at
        monkeypatch.setattr(service, 'RECONCILE_BUDGET_SECONDS', 0)
        assert service.reconcile(session) == {'checked': 0, 'removed': 0, 'budget_exhausted': True}
        assert session.get(SFUWorkerLease, 'livekit').seen_at == original
        assert not service.status(session)['ready']
