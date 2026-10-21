"""Private notification consent, standard Web Push crypto and atomic offline replay."""
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
import secrets
import subprocess
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.main import app as _registered_models
from app.api import auth, push
from app.config import settings
from app.database import get_session
from app.models.account_security import AccountSecurity
from app.models.channel import Channel, ChannelMember
from app.models.collaboration import NotificationDelivery
from app.models.message import Message
from app.models.notification import Notification, NotificationPreference
from app.models.push import OfflineMessageReceipt, PushDelivery, PushSubscription
from app.models.secure_channel import SecureChannel
from app.models.session import ActiveSession
from app.models.user import User
from app.services import push as service
from app.services.auth import create_token, token_digest
from app.services.remote_transport import Response

REAL_DISPATCH = service.dispatch


def encoded(value):
    return base64.urlsafe_b64encode(value).decode().rstrip('=')


def subscription(endpoint='https://push.example.com/private-endpoint'):
    key = ec.generate_private_key(ec.SECP256R1())
    auth_secret = secrets.token_bytes(16)
    return {'endpoint': endpoint, 'keys': {'p256dh': encoded(key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)), 'auth': encoded(auth_secret)}}, key, auth_secret


@pytest.fixture
def mobile(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'environment', 'test')
    monkeypatch.setattr(settings, 'jwt_secret', secrets.token_urlsafe(48))
    monkeypatch.setattr(settings, 'matrix_enabled', False)
    monkeypatch.setattr(settings, 'message_encryption_keys', {'test': SecretStr(base64.b64encode(b'm' * 32).decode())})
    monkeypatch.setattr(settings, 'message_encryption_active_key_id', 'test')
    monkeypatch.setattr(settings, 'account_security_key', SecretStr(base64.b64encode(b'a' * 32).decode()))
    monkeypatch.setattr(settings, 'push_enabled', True)
    signing = ec.generate_private_key(ec.SECP256R1())
    monkeypatch.setattr(settings, 'push_vapid_private_key', SecretStr(signing.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()))
    monkeypatch.setattr(settings, 'push_vapid_subject', 'mailto:operator@example.com')
    from app.services import remote_transport
    monkeypatch.setattr(remote_transport.pinned, 'public_addresses', lambda host, timeout=5: ['8.8.8.8'])
    engine = create_engine(f"sqlite:///{tmp_path / 'mobile.db'}", connect_args={'check_same_thread': False, 'timeout': 20})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        for name in ['alice', 'bob', 'outsider']:
            session.add(User(id=name, name=name.title(), email=name + '@example.com', password_hash='unused'))
        session.add_all([Channel(id='shared', name='Private channel name'), Channel(id='elsewhere', name='Other')])
        session.commit()
        session.add_all([ChannelMember(channel_id='shared', user_id='alice', role='admin'), ChannelMember(channel_id='shared', user_id='bob'), ChannelMember(channel_id='elsewhere', user_id='outsider', role='admin')])
        session.commit()
    api = FastAPI(); api.include_router(push.router); api.include_router(auth.router)
    def sessions():
        with Session(engine) as session:
            yield session
    api.dependency_overrides[get_session] = sessions
    tokens = {name: create_token(name) for name in ['alice', 'bob', 'outsider']}
    deliveries = []
    monkeypatch.setattr(service, 'dispatch', lambda destination, payload: deliveries.append((destination, payload)))
    with TestClient(api) as client:
        yield client, engine, tokens, deliveries, signing
    engine.dispose()


def headers(token):
    return {'Authorization': 'Bearer ' + token}


def enable(mobile, name='alice', endpoint='https://push.example.com/private-endpoint'):
    client, _, tokens, calls, _ = mobile
    payload = subscription(endpoint)[0]
    response = client.post('/mobile/subscriptions', headers=headers(tokens[name]), json=payload)
    assert response.status_code == 202, response.text
    identifier = response.json()['id']; challenge = calls[-1][1]['challenge']
    confirmed = client.post('/mobile/subscriptions/' + identifier + '/confirm', headers=headers(tokens[name]), json={'challenge': challenge})
    assert confirmed.status_code == 200, confirmed.text
    return identifier, payload


def activity(mobile, subscription_id):
    with Session(mobile[1]) as session:
        notification = Notification(user_id='alice', type='mention', title='Sensitive sender and channel', body='Secret message content', created_at=datetime.utcnow() + timedelta(seconds=1))
        session.add(notification); session.commit()
        session.add(NotificationDelivery(event_id=notification.id, user_id='alice', channel_id='shared', notification_id=notification.id)); session.commit()
        return notification.id


def test_config_requires_valid_operator_key_and_is_private(mobile, monkeypatch):
    client, _, tokens, _, signing = mobile
    response = client.get('/mobile/config', headers=headers(tokens['alice']))
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    assert response.json()['application_server_key'] == encoded(signing.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
    assert client.get('/mobile/config').status_code == 401
    monkeypatch.setattr(settings, 'push_vapid_private_key', SecretStr('invalid-private-secret'))
    assert client.get('/mobile/config', headers=headers(tokens['alice'])).json()['enabled'] is False
    assert 'invalid-private-secret' not in client.get('/mobile/config', headers=headers(tokens['alice'])).text
    monkeypatch.setattr(settings, 'push_enabled', False)
    assert client.post('/mobile/subscriptions', headers=headers(tokens['alice']), json=subscription()[0]).status_code == 503


@pytest.mark.parametrize('endpoint', ['http://push.example.com/private', 'https://127.0.0.1/private', 'https://[::1]/private', 'https://10.0.0.1/private', 'https://push.example.com:8443/private', 'https://user:secret@push.example.com/private', 'https://push.example.com/private#secret'])
def test_unsafe_subscription_destinations_never_contact_network(mobile, endpoint):
    client, _, tokens, calls, _ = mobile
    response = client.post('/mobile/subscriptions', headers=headers(tokens['alice']), json=subscription(endpoint)[0])
    assert response.status_code == 422 and calls == []
    assert 'secret' not in response.text


def test_subscription_requires_delivered_browser_challenge_not_api_response(mobile):
    client, engine, tokens, calls, _ = mobile
    value = subscription()[0]
    response = client.post('/mobile/subscriptions', headers=headers(tokens['alice']), json=value)
    assert response.status_code == 202
    identifier = response.json()['id']; nonce = calls[-1][1]['challenge']
    assert nonce not in response.text and value['endpoint'] not in response.text and value['keys']['auth'] not in response.text
    with Session(engine) as session:
        row = session.get(PushSubscription, identifier)
        assert not row.active and row.confirmed_at is None
        assert value['endpoint'] not in row.encrypted_subscription and value['keys']['auth'] not in row.encrypted_subscription
        assert nonce != row.challenge_hash
    path = '/mobile/subscriptions/' + identifier + '/confirm'
    assert client.post(path, headers=headers(tokens['bob']), json={'challenge': nonce}).status_code == 404
    assert client.post(path, headers=headers(tokens['alice']), json={'challenge': 'x' * 43}).status_code == 400
    assert client.post(path, headers=headers(create_token('alice')), json={'challenge': nonce}).status_code == 400
    assert client.post(path, headers=headers(tokens['alice']), json={'challenge': nonce}).status_code == 200
    assert client.post(path, headers=headers(tokens['alice']), json={'challenge': nonce}).status_code == 400
    listing = client.get('/mobile/subscriptions', headers=headers(tokens['alice']))
    assert value['endpoint'] not in listing.text and value['keys']['p256dh'] not in listing.text and tokens['alice'] not in listing.text
    assert client.get('/mobile/subscriptions', headers=headers(tokens['bob'])).json() == {'items': []}


def test_expired_challenge_and_endpoint_transfer_are_denied(mobile):
    client, engine, tokens, calls, _ = mobile
    value = subscription()[0]
    response = client.post('/mobile/subscriptions', headers=headers(tokens['alice']), json=value)
    identifier, nonce = response.json()['id'], calls[-1][1]['challenge']
    with Session(engine) as session:
        row = session.get(PushSubscription, identifier); row.challenge_expires_at = datetime.utcnow() - timedelta(seconds=1); session.add(row); session.commit()
    assert client.post('/mobile/subscriptions/' + identifier + '/confirm', headers=headers(tokens['alice']), json={'challenge': nonce}).status_code == 400
    assert client.post('/mobile/subscriptions', headers=headers(tokens['bob']), json=value).status_code == 409
    assert client.delete('/mobile/subscriptions/' + identifier, headers=headers(tokens['bob'])).status_code == 404
    assert client.delete('/mobile/subscriptions/' + identifier, headers=headers(tokens['alice'])).status_code == 204
    assert client.post('/mobile/subscriptions', headers=headers(tokens['bob']), json=value).status_code == 202


def test_concurrent_confirmation_is_one_use(mobile):
    client, _, tokens, calls, _ = mobile
    response = client.post('/mobile/subscriptions', headers=headers(tokens['alice']), json=subscription()[0])
    identifier, nonce = response.json()['id'], calls[-1][1]['challenge']
    def confirm(_):
        return client.post('/mobile/subscriptions/' + identifier + '/confirm', headers=headers(tokens['alice']), json={'challenge': nonce}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(confirm, range(2))) == [200, 400]


def test_revoke_during_confirmation_delivery_prevents_success(mobile, monkeypatch):
    client, engine, tokens, _, _ = mobile
    # First request registers the real active session before dispatch pauses.
    def revoke(*_):
        with Session(engine) as session:
            tracked = session.exec(select(ActiveSession).where(ActiveSession.token_hash == token_digest(tokens['alice']))).one()
            tracked.revoked_at = datetime.utcnow(); session.add(tracked); session.commit()
    monkeypatch.setattr(service, 'dispatch', revoke)
    assert client.post('/mobile/subscriptions', headers=headers(tokens['alice']), json=subscription()[0]).status_code == 401
    with Session(engine) as session:
        assert session.exec(select(PushSubscription)).one().active is False


def test_worker_sends_only_generic_activity_and_deduplicates(mobile):
    identifier, _ = enable(mobile); activity(mobile, identifier); mobile[3].clear()
    with Session(mobile[1]) as session:
        assert service.run_pending(session) == 1
        assert service.run_pending(session) == 0
        assert session.exec(select(PushDelivery)).one().status == 'delivered'
    assert len(mobile[3]) == 1
    payload = mobile[3][0][1]
    assert set(payload) == {'type', 'id'} and payload['type'] == 'activity'
    assert all(secret not in json.dumps(payload) for secret in ['Sensitive', 'Secret', 'alice', 'shared', mobile[2]['alice']])


@pytest.mark.parametrize('change', ['member', 'blocked', 'revoked', 'expired', 'version', 'preference', 'read'])
def test_worker_rechecks_current_account_membership_session_and_unread_activity(mobile, change):
    identifier, _ = enable(mobile); notification_id = activity(mobile, identifier); mobile[3].clear()
    with Session(mobile[1]) as session:
        service.queue_activity(session)
        if change == 'member': session.delete(session.get(ChannelMember, ('shared', 'alice')))
        elif change == 'blocked':
            row = session.get(User, 'alice'); row.is_blocked = True; session.add(row)
        elif change in {'revoked', 'expired'}:
            row = session.exec(select(ActiveSession).where(ActiveSession.token_hash == token_digest(mobile[2]['alice']))).one()
            if change == 'revoked': row.revoked_at = datetime.utcnow()
            else: row.expires_at = datetime.utcnow() - timedelta(seconds=1)
            session.add(row)
        elif change == 'version':
            row = session.get(AccountSecurity, 'alice'); row.auth_version += 1; session.add(row)
        elif change == 'preference':
            row = session.exec(select(NotificationPreference).where(NotificationPreference.user_id == 'alice')).one(); row.push = False; session.add(row)
        elif change == 'read':
            row = session.get(Notification, notification_id); row.read = True; session.add(row)
        session.commit()
        assert service.run_pending(session) == 0
        assert session.exec(select(PushDelivery)).one().status == 'cancelled'
    assert mobile[3] == []


@pytest.mark.parametrize('code,status', [('subscription_expired', 'failed'), ('push_unavailable', 'pending')])
def test_worker_bounds_retry_and_disables_expired_endpoint(mobile, monkeypatch, code, status):
    identifier, _ = enable(mobile); activity(mobile, identifier)
    def fail(*_): raise service.ProviderError(code)
    monkeypatch.setattr(service, 'dispatch', fail)
    with Session(mobile[1]) as session:
        assert service.run_pending(session) == 0
        row = session.exec(select(PushDelivery)).one()
        assert row.status == status and row.attempts == 1 and row.next_attempt_at > datetime.utcnow()
        assert session.get(PushSubscription, identifier).active is (code != 'subscription_expired')
        assert service.run_pending(session) == 0


def send(mobile, client_id='client-id-123456789', content='Private offline message', channel='shared', actor='alice', **extra):
    return mobile[0].post('/mobile/offline/channels/' + channel + '/messages', headers=headers(mobile[2][actor]), json={'client_id': client_id, 'content': content, **extra})


def test_native_offline_replay_is_atomic_and_returns_one_message(mobile):
    first = send(mobile); replay = send(mobile)
    assert first.status_code == replay.status_code == 200
    assert first.json()['id'] == replay.json()['id'] and replay.json()['replayed'] is True
    assert send(mobile, content='Different content').status_code == 409
    with Session(mobile[1]) as session:
        rows = session.exec(select(Message)).all(); receipts = session.exec(select(OfflineMessageReceipt)).all()
        assert len(rows) == len(receipts) == 1 and rows[0].content != 'Private offline message'
        assert receipts[0].message_id == rows[0].id and 'Private offline message' not in receipts[0].payload_digest


def test_concurrent_native_retry_creates_one_message_and_receipt(mobile):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: send(mobile), range(2)))
    assert [result.status_code for result in results] == [200, 200]
    assert results[0].json()['id'] == results[1].json()['id']
    with Session(mobile[1]) as session:
        assert len(session.exec(select(Message)).all()) == len(session.exec(select(OfflineMessageReceipt)).all()) == 1


def test_offline_replay_still_requires_current_membership_and_encryption_mode(mobile):
    assert send(mobile, actor='outsider').status_code == 403
    assert send(mobile, mentions=['outsider']).status_code == 400
    result = send(mobile); assert result.status_code == 200
    with Session(mobile[1]) as session:
        session.delete(session.get(ChannelMember, ('shared', 'alice'))); session.commit()
    assert send(mobile).status_code == 403
    with Session(mobile[1]) as session:
        session.add(ChannelMember(channel_id='shared', user_id='alice', role='admin'))
        session.add(SecureChannel(channel_id='shared', created_by='alice', status='preparing')); session.commit()
    assert send(mobile).status_code == 409


def test_parent_validation_and_thread_count_survive_replay(mobile):
    parent = send(mobile, client_id='parent-message-12345').json()
    assert send(mobile, client_id='other-channel-12345', channel='elsewhere', actor='outsider', parent_id=parent['id']).status_code == 404
    reply = send(mobile, client_id='reply-message-12345', parent_id=parent['id'])
    assert reply.status_code == 200
    assert send(mobile, client_id='reply-message-12345', parent_id=parent['id']).status_code == 200
    with Session(mobile[1]) as session:
        assert session.get(Message, parent['id']).thread_count == 1


def test_real_webpush_payload_and_vapid_are_independently_verified(mobile, monkeypatch):
    import http_ece
    import jwt
    destination, browser_key, auth_secret = subscription()
    calls = []
    def transport(method, endpoint, **options):
        calls.append((method, endpoint, options)); return Response(201, {}, b'')
    monkeypatch.setattr(service, 'request', transport)
    REAL_DISPATCH(destination, {'type': 'activity', 'id': 'opaque-delivery-id'})
    method, endpoint, options = calls[0]
    assert method == 'POST' and endpoint == destination['endpoint']
    assert json.loads(http_ece.decrypt(options['body'], private_key=browser_key, auth_secret=auth_secret, version='aes128gcm')) == {'type': 'activity', 'id': 'opaque-delivery-id'}
    authorization = {key.lower(): value for key, value in options['headers'].items()}['authorization']
    assert authorization.startswith('vapid t=')
    token, public = (part.strip() for part in authorization[8:].split(',')); public = public.removeprefix('k=')
    claims = jwt.decode(token, mobile[4].public_key(), algorithms=['ES256'], audience='https://push.example.com')
    assert claims['sub'] == 'mailto:operator@example.com' and claims['exp'] > datetime.utcnow().timestamp()
    assert public == encoded(mobile[4].public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
    assert options['headers']['content-encoding'] == 'aes128gcm' and options['max_bytes'] == 4096 and options['timeout'] == 15


def test_operator_keygen_creates_private_independent_key_without_disclosure(tmp_path):
    command = [sys.executable, str(Path(__file__).resolve().parents[2] / 'scripts/push-keygen.py'), str(tmp_path / 'push.pem')]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0 and 'PRIVATE KEY' not in result.stdout + result.stderr
    target = tmp_path / 'push.pem'; original = target.read_bytes()
    assert target.stat().st_mode & 0o777 == 0o600
    assert isinstance(serialization.load_pem_private_key(original, password=None).curve, ec.SECP256R1)
    assert subprocess.run(command, capture_output=True).returncode != 0 and target.read_bytes() == original


def test_actual_tls_webpush_receiver_decrypts_and_verifies_vapid(mobile, tmp_path, monkeypatch):
    """The production pinned transport reaches only a private synthetic push service."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import http_ece
    import jwt
    import select as sockets_select
    import socket
    import socketserver
    import ssl
    import threading
    from app.services import remote_transport
    key_path, cert_path = tmp_path / 'receiver-key.pem', tmp_path / 'receiver-cert.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(key_path), '-out', str(cert_path), '-days', '1', '-subj', '/CN=push.example.com', '-addext', 'subjectAltName=DNS:push.example.com'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    destination, browser_key, auth_secret = subscription()
    observed = []
    class Receiver(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            clear = json.loads(http_ece.decrypt(body, private_key=browser_key, auth_secret=auth_secret, version='aes128gcm'))
            token = self.headers['Authorization'].removeprefix('vapid t=').split(',', 1)[0].strip()
            claims = jwt.decode(token, mobile[4].public_key(), algorithms=['ES256'], audience='https://push.example.com')
            observed.append({'host': self.headers['Host'], 'path': self.path, 'encoding': self.headers['Content-Encoding'], 'clear': clear, 'subject': claims['sub']})
            self.send_response(201); self.send_header('Content-Length', '0'); self.end_headers()
    receiver = ThreadingHTTPServer(('127.0.0.1', 0), Receiver)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(cert_path, key_path)
    receiver.socket = context.wrap_socket(receiver.socket, server_side=True)
    class Proxy(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(5); header = b''
            while b'\r\n\r\n' not in header:
                chunk = self.request.recv(4096)
                if not chunk or len(header) > 8192: return
                header += chunk
            assert header.split(b'\r\n', 1)[0] == b'CONNECT 8.8.8.8:443 HTTP/1.1'
            with socket.create_connection(('127.0.0.1', receiver.server_port), timeout=5) as upstream:
                self.request.sendall(b'HTTP/1.1 200 Connection established\r\n\r\n')
                try:
                    while True:
                        ready, _, _ = sockets_select.select([self.request, upstream], [], [], 5)
                        if not ready: return
                        for source in ready:
                            data = source.recv(16384)
                            if not data: return
                            (upstream if source is self.request else self.request).sendall(data)
                except (OSError, TimeoutError): pass
    proxy = socketserver.ThreadingTCPServer(('127.0.0.1', 0), Proxy); proxy.daemon_threads = True
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in [receiver, proxy]]
    for thread in threads: thread.start()
    trust = tmp_path / 'receiver-trust.pem'; trust.write_bytes(cert_path.read_bytes())
    monkeypatch.setenv('SSL_CERT_FILE', str(trust))
    monkeypatch.setattr(remote_transport.pinned, 'getproxies', lambda: {'https': 'http://127.0.0.1:' + str(proxy.server_address[1])})
    monkeypatch.setattr(service, 'request', remote_transport.request)
    try:
        REAL_DISPATCH(destination, {'type': 'activity', 'id': 'opaque-real-delivery'})
        assert observed == [{'host': 'push.example.com', 'path': '/private-endpoint', 'encoding': 'aes128gcm', 'clear': {'type': 'activity', 'id': 'opaque-real-delivery'}, 'subject': 'mailto:operator@example.com'}]
    finally:
        for server in [proxy, receiver]: server.shutdown(); server.server_close()
        for thread in threads: thread.join(timeout=3)


@pytest.mark.parametrize('change', ['outside_scope', 'provider', 'sidecar', 'workspace_member'])
def test_push_worker_preserves_signed_workspace_scope_after_http_request(mobile, change):
    from app.models.workspace import Workspace, WorkspaceChannel, WorkspaceMember
    from app.models.workspace_oidc import WorkspaceOIDCProvider, WorkspaceOIDCSession
    client, engine, tokens, calls, _ = mobile
    with Session(engine) as session:
        session.add_all([Workspace(id='scope', name='Organization'), Workspace(id='other', name='Other organization')]); session.commit()
        session.add_all([WorkspaceMember(workspace_id='scope', user_id='alice', role='owner'), WorkspaceChannel(channel_id='shared', workspace_id='scope'), WorkspaceChannel(channel_id='elsewhere', workspace_id='other'), ChannelMember(channel_id='elsewhere', user_id='alice')])
        session.add(WorkspaceOIDCProvider(workspace_id='scope', name='Customer SSO', issuer_url='https://idp.example.com', client_id='customer-client', created_by='alice', enabled=True, revision=1)); session.commit()
        scoped = create_token('alice', session, workspace_scope={'workspace_id': 'scope', 'provider_revision': 1})
        session.add(WorkspaceOIDCSession(token_hash=token_digest(scoped), workspace_id='scope', provider_revision=1)); session.commit()
    tokens['alice'] = scoped
    identifier, _ = enable(mobile); notification_id = activity(mobile, identifier); calls.clear()
    with Session(engine) as session:
        service.queue_activity(session)
        if change == 'outside_scope':
            row = session.exec(select(PushDelivery)).one(); row.channel_id = 'elsewhere'; session.add(row)
        elif change == 'provider':
            row = session.get(WorkspaceOIDCProvider, 'scope'); row.enabled = False; session.add(row)
        elif change == 'sidecar':
            session.delete(session.get(WorkspaceOIDCSession, token_digest(scoped)))
        else: session.delete(session.get(WorkspaceMember, ('scope', 'alice')))
        session.commit()
        assert service.run_pending(session) == 0
        assert session.exec(select(PushDelivery)).one().status == 'cancelled'
    assert calls == []


def test_purged_message_receipt_blocks_recreation_after_lost_ack(mobile):
    first = send(mobile); assert first.status_code == 200
    with Session(mobile[1]) as session:
        receipt = session.get(OfflineMessageReceipt, ('alice', 'client-id-123456789'))
        message = session.get(Message, receipt.message_id)
        receipt.message_id = None; session.add(receipt); session.flush()
        session.delete(message); session.commit()
    replay = send(mobile)
    assert replay.status_code == 409 and 'unavailable' in replay.text
    assert send(mobile, content='Changed after purge').status_code == 409
    with Session(mobile[1]) as session:
        assert session.exec(select(Message)).all() == []
        assert session.get(OfflineMessageReceipt, ('alice', 'client-id-123456789')).message_id is None
