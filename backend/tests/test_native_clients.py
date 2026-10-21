"""Real native PKCE exchange, source revocation, workspace fences and Matrix marker."""
import asyncio
import base64
from datetime import datetime, timedelta
from hashlib import sha256
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from app.api import auth, native_clients, secure_messaging
from app.database import get_session
from app.models.native_clients import NativeClientGrant, NativeClientMatrixBinding, NativeClientSession
from app.models.secure_channel import MatrixSession
from app.models.session import ActiveSession
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from app.models.workspace_oidc import WorkspaceOIDCProvider, WorkspaceOIDCSession
from app.realtime import socket
from app.services.auth import create_token, decode_token_claims, revoke_token, token_digest
from app.services.request_guard import remember_request, recheck_actor

VERIFIER = 'v' * 43
CHALLENGE = base64.urlsafe_b64encode(sha256(VERIFIER.encode()).digest()).rstrip(b'=').decode()
CALLBACK = 'http://127.0.0.1:43127/freecollab-native/callback'


@pytest.fixture
def native(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "native.db"}', connect_args={'check_same_thread': False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(User(id='own', name='Own Person', email='own@example.test', password_hash='unused'))
        session.add(Workspace(id='team', name='Team'))
        session.flush()
        session.add(WorkspaceMember(workspace_id='team', user_id='own', role='member'))
        session.add(WorkspaceOIDCProvider(workspace_id='team', name='Customer IdP', issuer_url='https://idp.example.test', client_id='customer', enabled=True, revision=4, created_by='own'))
        session.commit()
        source = create_token('own', session=session)
    app = FastAPI()
    app.include_router(auth.router); app.include_router(native_clients.router)
    def sessions():
        with Session(engine) as session: yield session
    app.dependency_overrides[get_session] = sessions
    monkeypatch.setattr(socket, 'engine', engine)
    with TestClient(app) as client: yield client, engine, source
    engine.dispose()


def bearer(token): return {'Authorization': 'Bearer ' + token}
def connect(client, source, **overrides):
    return client.post('/native-clients/connect', headers=bearer(source), json={
        'code_challenge': CHALLENGE, 'redirect_uri': CALLBACK, 'client_kind': 'desktop', **overrides})
def exchange(client, code, **overrides):
    return client.post('/native-clients/exchange', json={'code': code, 'code_verifier': VERIFIER, 'redirect_uri': CALLBACK, **overrides})


def test_native_handoff_stores_digests_and_preserves_identity_freshness(native):
    client, engine, source = native
    grant = connect(client, source)
    assert grant.status_code == 200, grant.text
    assert grant.headers['cache-control'] == 'no-store'
    code = grant.json()['code']
    with Session(engine) as session:
        stored = session.get(NativeClientGrant, token_digest(code))
        assert code not in repr(stored) and source not in repr(stored)
    result = exchange(client, code)
    assert result.status_code == 200, result.text
    token = result.json()['access_token']
    original, child = decode_token_claims(source), decode_token_claims(token)
    assert all(child[key] == original[key] for key in ('sub', 'iat', 'exp', 'amr', 'av'))
    assert child['native_client_id'] and child['jti'] != original['jti']
    assert client.get('/auth/me', headers=bearer(token)).status_code == 200
    assert exchange(client, code).status_code == 401
    assert connect(client, token).status_code == 403
    with Session(engine) as session:
        assert token not in repr(session.get(NativeClientSession, token_digest(token)))
        tracked = session.exec(select(ActiveSession).where(ActiveSession.token_hash == token_digest(token))).first()
        assert tracked.expires_at == datetime.utcfromtimestamp(original['exp'])


@pytest.mark.parametrize('callback', ['http://localhost:43127/freecollab-native/callback', 'https://evil.test', 'http://127.0.0.1:80/freecollab-native/callback', 'http://127.0.0.1:43127/other', 'http://127.0.0.1:43127/freecollab-native/callback?x=y', 'freecollab://native/complete'])
def test_native_redirect_does_not_accept_arbitrary_browser_destinations(native, callback):
    client, _, source = native
    assert connect(client, source, redirect_uri=callback).status_code == 422


def test_wrong_pkce_or_callback_cannot_consume_valid_grant(native):
    client, _, source = native
    code = connect(client, source).json()['code']
    assert exchange(client, code, code_verifier='x' * 43).status_code == 401
    assert exchange(client, code, redirect_uri='http://127.0.0.1:43128/freecollab-native/callback').status_code == 401
    assert exchange(client, code).status_code == 200


def test_browser_logout_invalidates_rest_socket_and_locked_request(native, monkeypatch):
    client, engine, source = native
    token = exchange(client, connect(client, source).json()['code']).json()['access_token']
    monkeypatch.setattr(socket.sio, 'get_session', AsyncMock(return_value={'user_id': 'own', 'token': token}))
    assert asyncio.run(socket._active_user('sid')) == 'own'
    with Session(engine) as session:
        remember_request(session, token_digest(token), decode_token_claims(token))
        recheck_actor(session, 'own')
        with Session(engine) as revoker: revoke_token(source, revoker)
        with pytest.raises(HTTPException): recheck_actor(session, 'own')
    assert client.get('/auth/me', headers=bearer(token)).status_code == 401
    assert asyncio.run(socket._active_user('sid')) is None


def test_workspace_sso_scope_survives_exchange_and_source_revision_changes(native):
    client, engine, _ = native
    with Session(engine) as session:
        source = create_token('own', session=session, auth_methods=['sso'], workspace_scope={'workspace_id': 'team', 'provider_revision': 4})
        session.add(WorkspaceOIDCSession(token_hash=token_digest(source), workspace_id='team', provider_revision=4)); session.commit()
    token = exchange(client, connect(client, source).json()['code']).json()['access_token']
    me = client.get('/auth/me', headers=bearer(token))
    assert me.status_code == 200 and me.json()['session_scope'] == 'workspace' and me.json()['workspace_id'] == 'team'
    with Session(engine) as session:
        provider = session.get(WorkspaceOIDCProvider, 'team'); provider.revision = 5; session.add(provider); session.commit()
    assert client.get('/auth/me', headers=bearer(token)).status_code == 401


def test_missing_native_sidecar_fails_closed(native):
    client, engine, source = native
    token = exchange(client, connect(client, source).json()['code']).json()['access_token']
    with Session(engine) as session:
        session.delete(session.get(NativeClientSession, token_digest(token))); session.commit()
    assert client.get('/auth/me', headers=bearer(token)).status_code == 401


@pytest.mark.parametrize('mutation', ['parent_logout', 'native_sidecar', 'matrix_marker'])
def test_matrix_gateway_rechecks_native_parent_and_independent_marker(native, mutation):
    from starlette.requests import Request
    client, engine, source = native
    token = exchange(client, connect(client, source).json()['code']).json()['access_token']
    marker = decode_token_claims(token)['native_client_id']
    with Session(engine) as session:
        session.add(MatrixSession(matrix_token_hash=token_digest('matrix-native'), user_id='own',
            auth_token_hash=token_digest(token), auth_expires_at=datetime.utcnow()+timedelta(days=1),
            device_id='NATIVE', encrypted_access_token='unused', auth_version=0)); session.flush()
        session.add(NativeClientMatrixBinding(matrix_token_hash=token_digest('matrix-native'), native_client_id=marker)); session.commit()
    request = Request({'type':'http', 'headers': [(b'authorization', b'Bearer matrix-native')]})
    with Session(engine) as session:
        assert secure_messaging._matrix_authorize(request, session)[0].user_id == 'own'
        if mutation == 'parent_logout':
            revoke_token(source, session)
        elif mutation == 'native_sidecar':
            session.delete(session.get(NativeClientSession, token_digest(token))); session.commit()
        else:
            session.delete(session.get(NativeClientMatrixBinding, token_digest('matrix-native'))); session.commit()
        with pytest.raises(HTTPException): secure_messaging._matrix_authorize(request, session)


def test_native_logout_does_not_revoke_the_browser(native):
    client, _, source = native
    token = exchange(client, connect(client, source).json()['code']).json()['access_token']
    assert client.post('/auth/logout', headers=bearer(token)).status_code == 200
    assert client.get('/auth/me', headers=bearer(token)).status_code == 401
    assert client.get('/auth/me', headers=bearer(source)).status_code == 200


def test_two_exchanges_cannot_create_two_sessions_from_one_grant(native):
    from concurrent.futures import ThreadPoolExecutor
    client, engine, source = native
    code = connect(client, source).json()['code']
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: exchange(client, code).status_code, range(2)))
    assert sorted(results) == [200, 401]
    with Session(engine) as session:
        assert len(session.exec(select(NativeClientSession)).all()) == 1


def test_native_source_expiry_does_not_depend_on_operator_timezone(native, monkeypatch):
    import os
    import time
    if not hasattr(time, 'tzset'): pytest.skip('Process timezone control is unavailable')
    from app.services.native_clients import _source_valid
    client, engine, source = native
    previous = os.environ.get('TZ')
    try:
        monkeypatch.setenv('TZ', 'America/Los_Angeles'); time.tzset()
        claims = decode_token_claims(source); claims['exp'] = time.time()+600
        with Session(engine) as session: assert _source_valid(session, token_digest(source), claims)
    finally:
        if previous is None: os.environ.pop('TZ', None)
        else: os.environ['TZ'] = previous
        time.tzset()


def test_native_preserves_verified_mfa_and_does_not_refresh_authentication(native):
    client, engine, _ = native
    with Session(engine) as session: source = create_token('own', session=session, mfa_verified=True)
    token = exchange(client, connect(client, source).json()['code']).json()['access_token']
    original, child = decode_token_claims(source), decode_token_claims(token)
    assert child['amr'] == original['amr'] == ['pwd', 'otp']
    assert child['iat'] == original['iat'] and child['exp'] == original['exp']


def test_expired_code_cannot_create_native_session(native):
    client, engine, source = native
    code = connect(client, source).json()['code']
    with Session(engine) as session:
        grant = session.get(NativeClientGrant, token_digest(code)); grant.expires_at = datetime.utcnow()-timedelta(seconds=1)
        session.add(grant); session.commit()
    assert exchange(client, code).status_code == 401
    with Session(engine) as session: assert not session.exec(select(NativeClientSession)).all()


def test_missing_native_child_workspace_scope_cannot_upgrade_digest_only_authority(native):
    from app.services.sso_scope import bind_scope_for_hash
    client, engine, _ = native
    with Session(engine) as session:
        source = create_token('own', session=session, auth_methods=['sso'], workspace_scope={'workspace_id':'team','provider_revision':4})
        session.add(WorkspaceOIDCSession(token_hash=token_digest(source), workspace_id='team', provider_revision=4)); session.commit()
    token = exchange(client, connect(client, source).json()['code']).json()['access_token']
    with Session(engine) as session:
        assert bind_scope_for_hash(session, token_digest(token), 'own')
        session.delete(session.get(WorkspaceOIDCSession, token_digest(token))); session.commit()
        assert not bind_scope_for_hash(session, token_digest(token), 'own')
    assert client.get('/auth/me', headers=bearer(token)).status_code == 401


def test_mobile_callbacks_are_static_and_source_logout_cancels_pending_grant(native):
    client, engine, source = native
    result = connect(client, source, client_kind='mobile', redirect_uri='freecollab://native/complete')
    assert result.status_code == 200
    with Session(engine) as session: revoke_token(source, session)
    assert exchange(client, result.json()['code'], redirect_uri='freecollab://native/complete').status_code == 401

