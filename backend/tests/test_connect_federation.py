"""Two independent SQL installations, real Ed25519 wire envelopes, Matrix fixture.

The Matrix control-plane fixture is explicit: these tests do not assert public
homeserver federation or Rust SDK interoperability with a deployed operator.
"""
import base64
from contextvars import ContextVar
from datetime import datetime, timedelta
import json
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from nacl.signing import SigningKey
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api import connect_federation as api, channels, secure_messaging
from app.config import settings
from app.database import get_session
from app.models.channel import Channel, ChannelMember
from app.models.connect_federation import FederatedConnection, FederatedGrant, FederatedPeerMember, ConnectFederationIdentity
from app.models.secure_channel import MatrixAccount, SecureChannel
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from app.services import connect_federation as federation, matrix_bridge, remote_transport
from app.services.auth import create_token
from app.services.workspaces import workspace_allows_channel


@pytest.fixture
def pair(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'message_encryption_key_file', str(tmp_path / 'messages.json'))
    monkeypatch.setattr(settings, 'account_security_key_file', str(tmp_path / 'accounts.key'))
    monkeypatch.setattr(settings, 'matrix_enabled', True)
    active = ContextVar('federation-test-installation', default='a')
    keys = {side: SigningKey.generate() for side in 'ab'}
    origins = {side: f'https://{side}.example.com' for side in 'ab'}
    servers = {side: f'matrix.{side}.example.com' for side in 'ab'}
    class ContextSettings:
        def __getattr__(self, name):
            side = active.get(); other = 'b' if side == 'a' else 'a'
            local = {'connect_federation_enabled': True, 'matrix_enabled': True,
                'connect_federation_public_url': origins[side], 'matrix_server_name': servers[side],
                'connect_federation_peers': {origins[other]: {'public_key': base64.b64encode(bytes(keys[other].verify_key)).decode(),
                    'matrix_server_name': servers[other]}}}
            return local[name] if name in local else getattr(settings, name)
    config = ContextSettings()
    monkeypatch.setattr(federation, 'settings', config)
    monkeypatch.setattr(matrix_bridge, 'settings', config)
    operator = lambda side: '@freecollab_operator_' + ('a' if side == 'a' else 'b') * 16 + ':' + servers[side]
    rooms, clients, engines, tokens, observed = {}, {}, {}, {}, []
    def service(session, **_): return operator(active.get()), 'operator-' + active.get()
    def account(session, user_id):
        existing = session.get(MatrixAccount, user_id)
        if not existing:
            existing = MatrixAccount(user_id=user_id, matrix_user_id=f'@fc_{user_id}:' + servers[active.get()], encrypted_password='fixture-encrypted', provisioned=True)
            session.add(existing); session.commit()
        return existing
    def provision(session, binding):
        room = '!' + binding.channel_id + ':' + servers[active.get()]
        rooms[room] = {'members': {operator(active.get()): 'join'}, 'levels': {'users': {operator(active.get()): 100},
                'users_default': 0, 'events_default': 0, 'state_default': 100, 'invite': 100, 'kick': 100, 'ban': 100},
            'encryption': {'algorithm': 'm.megolm.v1.aes-sha2'}, 'history': {'history_visibility': 'joined'}}
        binding.matrix_room_id, binding.status = room, 'active'; session.add(binding)
    def matrix(method, path, *, token=None, data=None):
        path = unquote(path)
        if '/join/' in path:
            room = path.split('/join/', 1)[1]; rooms[room]['members'][operator(active.get())] = 'join'; return {'room_id': room}
        room, tail = path.split('/rooms/', 1)[1].split('/', 1)
        state = rooms[room]
        if method == 'GET' and tail == 'members': return {'chunk': [{'state_key': name, 'content': {'membership': status}} for name, status in state['members'].items()]}
        if tail == 'invite': state['members'][data['user_id']] = 'invite'; return {}
        if tail == 'kick': state['members'][data['user_id']] = 'leave'; return {}
        if tail == 'state/m.room.power_levels':
            if method == 'PUT': state['levels'] = data
            return state['levels']
        if tail == 'state/m.room.encryption': return state['encryption']
        if tail == 'state/m.room.history_visibility': return state['history']
        if tail == 'state/m.room.join_rules': return {'join_rule': 'invite'}
        if tail == 'state/m.room.guest_access': return {'guest_access': 'forbidden'}
        if tail == 'state/m.room.create': return {'room_version': '12'}
        raise AssertionError((method, path))
    for module in (matrix_bridge, api):
        monkeypatch.setattr(module, 'service_token', service)
        monkeypatch.setattr(module, 'matrix_request', matrix)
    monkeypatch.setattr(api, 'create_secure_room', provision)
    monkeypatch.setattr(matrix_bridge, 'ensure_account', account)
    for side in 'ab':
        engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        engines[side] = engine; SQLModel.metadata.create_all(engine)
        active_token = active.set(side)
        with Session(engine) as session:
            session.add(Workspace(id=side, name='Company ' + side))
            for name, role in [('admin', 'owner'), ('member', 'member'), ('outsider', 'member')]:
                session.add(User(id=name, name=side + '-' + name, email=name + '@' + side + '.example.com', password_hash='unused'))
                session.add(WorkspaceMember(workspace_id=side, user_id=name, role=role))
            session.add(ConnectFederationIdentity(encrypted_seed=federation.encrypt_message(base64.b64encode(bytes(keys[side])).decode(), 'connect-federation-identity')))
            session.commit(); tokens[side] = {name: create_token(name, session) for name in ['admin', 'member', 'outsider']}; session.commit()
        active.reset(active_token)
        app = FastAPI()
        for module in (api, channels, secure_messaging): app.include_router(module.router)
        def make_scope(identifier):
            @app.middleware('http')
            async def scope(request, call_next):
                saved = active.set(identifier)
                try: return await call_next(request)
                finally: active.reset(saved)
        make_scope(side)
        def make_session(target):
            def dependency():
                with Session(target) as session: yield session
            return dependency
        app.dependency_overrides[get_session] = make_session(engine)
        clients[side] = TestClient(app)
    def wire(method, url, *, headers=None, body=None, **_):
        parsed = urlsplit(url); side = parsed.hostname.split('.')[0]
        observed.append(json.loads(body))
        response = clients[side].request(method, parsed.path, headers=headers, content=body)
        return remote_transport.Response(response.status_code, dict(response.headers), response.content)
    monkeypatch.setattr(federation.remote_transport, 'request', wire)
    value = SimpleNamespace(clients=clients, engines=engines, tokens=tokens, rooms=rooms, observed=observed,
        active=active, origins=origins, keys=keys, servers=servers, config=config,
        headers=lambda side, name='admin': {'Authorization': 'Bearer ' + tokens[side][name]})
    yield value
    for client in clients.values(): client.close()
    for engine in engines.values(): engine.dispose()


def offer(pair):
    response = pair.clients['a'].post('/connect/federation/workspaces/a/connections', headers=pair.headers('a'),
        json={'name': 'Shared project', 'peer_origin': pair.origins['b'], 'peer_workspace_id': 'b'})
    assert response.status_code == 201, response.text
    return response.json()


def activate(pair):
    invitation = offer(pair)
    response = pair.clients['b'].post('/connect/federation/workspaces/b/accept', headers=pair.headers('b'),
        json={'peer_origin': pair.origins['a'], 'invitation_token': invitation['invitation_token']})
    assert response.status_code == 201, response.text
    return invitation, response.json()


def grant(pair, side, connection, user='member'):
    response = pair.clients[side].post(f'/connect/federation/workspaces/{side}/connections/{connection["id"]}/members',
        headers=pair.headers(side), json={'user_id': user})
    assert response.status_code == 201, response.text


def test_two_independent_installations_bind_same_encrypted_room_without_foreign_accounts(pair):
    host, guest = activate(pair)
    assert host['channel_id'] != guest['channel_id'] and host['id'] == guest['id']
    for side, connection in [('a', host), ('b', guest)]:
        with Session(pair.engines[side]) as session:
            assert len(session.exec(select(User)).all()) == 3
            assert session.get(ChannelMember, (connection['channel_id'], 'admin')) is None
            assert session.get(SecureChannel, connection['channel_id']).matrix_room_id in pair.rooms
            assert session.get(FederatedConnection, connection['id']).status == 'active'
            assert session.get(WorkspaceMember, ('b' if side == 'a' else 'a', 'member')) is None
    assert all('signature' in envelope for envelope in pair.observed)
    assert len({pair.rooms[room]['encryption']['algorithm'] for room in pair.rooms}) == 1


def test_only_own_explicit_grants_and_current_remote_roster_are_accepted(pair):
    host, guest = activate(pair)
    grant(pair, 'a', host); grant(pair, 'b', guest)
    response = pair.clients['a'].post(f'/connect/federation/workspaces/a/connections/{host["id"]}/sync', headers=pair.headers('a'))
    assert response.status_code == 200, response.text
    response = pair.clients['a'].get(f'/channels/{host["channel_id"]}/members', headers=pair.headers('a', 'member'))
    assert response.status_code == 200, response.text
    remote = [row for row in response.json() if row.get('remote')]
    assert remote == [{'user_id': '@fc_member:matrix.b.example.com', 'name': 'b-member', 'public_key': None,
        'role': 'member', 'remote': True, 'matrix_user_id': '@fc_member:matrix.b.example.com'}]
    assert pair.clients['a'].get(f'/channels/{host["channel_id"]}', headers=pair.headers('a', 'outsider')).status_code == 403
    with Session(pair.engines['a']) as session:
        assert len(session.exec(select(User)).all()) == 3
        assert session.exec(select(FederatedPeerMember)).first().matrix_user_id.endswith(':matrix.b.example.com')


def test_member_removal_immediately_denies_local_access_and_rotates_remote_epoch(pair):
    host, guest = activate(pair); grant(pair, 'a', host); grant(pair, 'b', guest)
    endpoint = f'/connect/federation/workspaces/a/connections/{host["id"]}/sync'
    assert pair.clients['a'].post(endpoint, headers=pair.headers('a')).status_code == 200
    with Session(pair.engines['a']) as session: old = session.get(FederatedConnection, host['id']).peer_roster_hash
    response = pair.clients['b'].delete(f'/connect/federation/workspaces/b/connections/{guest["id"]}/members/member', headers=pair.headers('b'))
    assert response.status_code == 200
    assert pair.clients['b'].get(f'/channels/{guest["channel_id"]}', headers=pair.headers('b', 'member')).status_code == 403
    assert pair.clients['a'].post(endpoint, headers=pair.headers('a')).status_code == 200
    with Session(pair.engines['a']) as session:
        assert session.get(FederatedConnection, host['id']).peer_roster_hash != old
        assert federation.peer_crypto_ids(session, host['channel_id']) == set()


def test_bilateral_revocation_is_durable_on_both_databases(pair):
    host, guest = activate(pair); grant(pair, 'a', host); grant(pair, 'b', guest)
    response = pair.clients['b'].delete(f'/connect/federation/workspaces/b/connections/{guest["id"]}', headers=pair.headers('b'))
    assert response.status_code == 200, response.text
    for side, connection in [('a', host), ('b', guest)]:
        assert pair.clients[side].get(f'/channels/{connection["channel_id"]}', headers=pair.headers(side, 'member')).status_code == 403
        with Session(pair.engines[side]) as session:
            assert session.get(FederatedConnection, connection['id']).status == 'revoked'
            assert session.get(FederatedGrant, (connection['id'], 'member')).revoked_at is not None


def test_original_admin_authority_ending_prevents_remote_sends(pair):
    host, guest = activate(pair); grant(pair, 'a', host); grant(pair, 'b', guest)
    with Session(pair.engines['b']) as session:
        member = session.get(WorkspaceMember, ('b', 'admin')); member.role = 'member'; session.add(member); session.commit()
    result = pair.clients['a'].post(f'/connect/federation/workspaces/a/connections/{host["id"]}/sync', headers=pair.headers('a'))
    assert result.status_code == 503, result.text


def test_peer_outage_fails_closed_and_local_revoke_remains_available(pair, monkeypatch):
    host, guest = activate(pair); grant(pair, 'a', host)
    def unavailable(*args, **kwargs): raise remote_transport.ProviderError('provider_unavailable')
    monkeypatch.setattr(federation.remote_transport, 'request', unavailable)
    endpoint = f'/connect/federation/workspaces/a/connections/{host["id"]}'
    assert pair.clients['a'].post(endpoint + '/sync', headers=pair.headers('a')).status_code == 503
    response = pair.clients['a'].delete(endpoint, headers=pair.headers('a'))
    assert response.status_code == 200 and response.json()['remote_cleanup_pending']
    assert pair.clients['a'].get(f'/channels/{host["channel_id"]}', headers=pair.headers('a', 'member')).status_code == 403


def test_pending_expired_wrong_workspace_and_member_invites_are_denied(pair):
    host = offer(pair)
    assert pair.clients['a'].post(f'/connect/federation/workspaces/a/connections/{host["id"]}/members', headers=pair.headers('a'), json={'user_id': 'member'}).status_code == 409
    assert pair.clients['a'].post('/connect/federation/workspaces/a/connections', headers=pair.headers('a', 'member'), json={'name': 'Denied', 'peer_origin': pair.origins['b'], 'peer_workspace_id': 'b'}).status_code == 403
    with Session(pair.engines['a']) as session:
        connection = session.get(FederatedConnection, host['id']); connection.invitation_expires_at = datetime.utcnow() - timedelta(seconds=1); session.add(connection); session.commit()
        assert connection.invitation_hash == federation.sha256(host['invitation_token'].encode()).hexdigest()
        assert host['invitation_token'] not in connection.model_dump_json()
    response = pair.clients['b'].post('/connect/federation/workspaces/b/accept', headers=pair.headers('b'), json={'peer_origin': pair.origins['a'], 'invitation_token': host['invitation_token']})
    assert response.status_code == 503  # Bounded peer errors never forward provider bodies.


@pytest.mark.parametrize('change', ['signature', 'audience', 'operation', 'timestamp', 'nonce', 'payload', 'origin'])
def test_signed_requests_reject_tampering_before_application_effects(pair, change):
    saved = pair.active.set('a')
    with Session(pair.engines['a']) as session:
        envelope = federation.signed(session, 'roster', {'connection_id': 'nonexistent'}, audience=pair.origins['b'])
    pair.active.reset(saved)
    replacements = {'signature': base64.b64encode(b'X' * 64).decode(), 'audience': 'https://evil.example.com',
        'operation': 'revoke', 'timestamp': 1, 'nonce': 'bad', 'payload': {'connection_id': 'changed'}, 'origin': 'https://evil.example.com'}
    envelope[change] = replacements[change]
    response = pair.clients['b'].post('/connect/federation/wire/roster', json=envelope)
    assert response.status_code == 403, response.text


def test_nonce_is_single_use_across_distinct_requests(pair):
    host, guest = activate(pair)
    saved = pair.active.set('a')
    with Session(pair.engines['a']) as session:
        envelope = federation.signed(session, 'roster', {'connection_id': host['id']}, audience=pair.origins['b'])
    pair.active.reset(saved)
    assert pair.clients['b'].post('/connect/federation/wire/roster', json=envelope).status_code == 200
    assert pair.clients['b'].post('/connect/federation/wire/roster', json=envelope).status_code == 409


def test_wrong_response_nonce_and_short_lived_epoch_are_denied(pair):
    host, guest = activate(pair)
    saved = pair.active.set('b')
    with Session(pair.engines['b']) as session:
        envelope = federation.signed(session, 'roster:response', {}, audience=pair.origins['a'])
    pair.active.reset(saved)
    saved = pair.active.set('a')
    with Session(pair.engines['a']) as session:
        with pytest.raises(HTTPException): federation.verify(session, envelope, 'roster:response', consume=False, expected_nonce='another-value')
        connection = session.get(FederatedConnection, host['id']); connection.roster_checked_at = datetime.utcnow() - timedelta(seconds=31)
        with pytest.raises(HTTPException): federation.epoch_material(session, connection)
    pair.active.reset(saved)


@pytest.mark.parametrize('url', ['http://a.example.com', 'https://127.0.0.1', 'https://a.example.com:8443', 'https://a.example.com/path', 'https://user:secret@a.example.com', 'https://a.example.com?key=1'])
def test_peer_origin_is_exact_https_without_paths_credentials_or_private_hosts(url):
    with pytest.raises(HTTPException): federation.origin(url)


def test_workspace_offboarding_permanently_revokes_grants_before_reinvitation(pair):
    from app.services.connect import revoke_workspace_grants, workspace_grant_channel_ids
    host, guest = activate(pair); grant(pair, 'a', host)
    saved = pair.active.set('a')
    with Session(pair.engines['a']) as session:
        assert host['channel_id'] in workspace_grant_channel_ids(session, 'a', 'member')
        assert host['channel_id'] in revoke_workspace_grants(session, 'a', 'member')
        session.delete(session.get(WorkspaceMember, ('a', 'member'))); session.commit()
        session.add(WorkspaceMember(workspace_id='a', user_id='member', role='member')); session.commit()
        assert session.get(FederatedGrant, (host['id'], 'member')).revoked_at is not None
        assert session.get(ChannelMember, (host['channel_id'], 'member')) is None
        assert not federation.grant_valid(session, session.get(FederatedConnection, host['id']), 'member')
    pair.active.reset(saved)


def test_sync_rechecks_local_grants_after_remote_io_before_matrix_invites(pair, monkeypatch):
    host, guest = activate(pair); grant(pair, 'a', host); grant(pair, 'b', guest)
    endpoint = '/connect/federation/workspaces/a/connections/' + host['id'] + '/sync'
    assert pair.clients['a'].post(endpoint, headers=pair.headers('a')).status_code == 200
    original = federation.remote_snapshot
    def revoke_during_wait(session, connection):
        value = original(session, connection)
        with Session(pair.engines['a']) as separate:
            grant_row = separate.get(FederatedGrant, (host['id'], 'member'))
            grant_row.revoked_at = datetime.utcnow(); separate.add(grant_row)
            separate.delete(separate.get(ChannelMember, (host['channel_id'], 'member')))
            separate.commit()
        return value
    monkeypatch.setattr(federation, 'remote_snapshot', revoke_during_wait)
    assert pair.clients['a'].post(endpoint, headers=pair.headers('a')).status_code == 200
    with Session(pair.engines['a']) as session:
        room = session.get(SecureChannel, host['channel_id']).matrix_room_id
        assert pair.rooms[room]['members']['@fc_member:matrix.a.example.com'] == 'leave'
        assert session.get(ChannelMember, (host['channel_id'], 'member')) is None


@pytest.mark.parametrize('event,content', [('m.room.join_rules', {'join_rule': 'public'}),
    ('m.room.guest_access', {'guest_access': 'can_join'}), ('m.room.history_visibility', {'history_visibility': 'world_readable'})])
def test_guest_refuses_peer_room_with_weaker_join_guest_or_history_policy(pair, monkeypatch, event, content):
    invitation = offer(pair)
    original = api.matrix_request
    def weak(method, path, **kwargs):
        if method == 'GET' and path.endswith('/state/' + event): return content
        return original(method, path, **kwargs)
    monkeypatch.setattr(api, 'matrix_request', weak)
    result = pair.clients['b'].post('/connect/federation/workspaces/b/accept', headers=pair.headers('b'),
        json={'peer_origin': pair.origins['a'], 'invitation_token': invitation['invitation_token']})
    assert result.status_code == 409, result.text
    for side in 'ab':
        with Session(pair.engines[side]) as session:
            assert session.get(FederatedConnection, invitation['id']).status == 'revoked'


def test_server_refuses_unapproved_matrix_room_occupants_even_with_current_peer_roster(pair):
    host, guest = activate(pair); grant(pair, 'a', host); grant(pair, 'b', guest)
    assert pair.clients['a'].post('/connect/federation/workspaces/a/connections/' + host['id'] + '/sync', headers=pair.headers('a')).status_code == 200
    saved = pair.active.set('a')
    with Session(pair.engines['a']) as session:
        connection = session.get(FederatedConnection, host['id'])
        room = session.get(SecureChannel, host['channel_id']).matrix_room_id
        pair.rooms[room]['members']['@fc_unapproved:matrix.b.example.com'] = 'join'
        with pytest.raises(HTTPException) as error: federation.validate_room_roster(session, connection)
        assert error.value.status_code == 409
    pair.active.reset(saved)


def test_authorized_secure_binding_exposes_only_its_explicitly_pinned_peer_server(pair):
    host, guest = activate(pair); grant(pair, 'a', host)
    bindings = pair.clients['a'].get('/secure/channels', headers=pair.headers('a', 'member'))
    assert bindings.status_code == 200, bindings.text
    assert bindings.json()[0]['federated_peer_server'] == 'matrix.b.example.com'
    hidden = pair.clients['a'].get('/secure/channels', headers=pair.headers('a', 'outsider'))
    assert hidden.status_code == 200 and hidden.json() == []
