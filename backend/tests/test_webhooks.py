"""Signed durable deliveries preserve channel grants and encrypted content."""
import hashlib
import hmac
import json
import socket
import socketserver
import select as socket_select
import ssl
import subprocess
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api import webhooks as api
from app.api import messages, files, decisions, notifications
from app.config import settings
from app.database import get_session
from app.models.channel import Channel, ChannelMember
from app.models.account_security import AccountSecurity
from app.models.secure_channel import SecureChannel
from app.models.user import User
from app.models.webhook import WebhookDelivery, WebhookEndpoint
from app.models.workspace import Workspace, WorkspaceChannel, WorkspaceMember
from app.services import webhooks as service
from app.services.auth import create_token


@pytest.fixture
def team(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "settings", SimpleNamespace(webhooks_enabled=True))
    monkeypatch.setattr(settings, "account_security_key_file", str(tmp_path / "account.key"))
    monkeypatch.setattr(settings, "message_encryption_key_file", str(tmp_path / "message-keys.json"))
    monkeypatch.setattr(settings, "jwt_secret", "webhook-test-signing-key-at-least-32-bytes")
    monkeypatch.setattr(service, "public_addresses", lambda host: ["8.8.8.8"])
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([User(id=name, name=name, email=name + "@example.com", password_hash="unused") for name in ("owner", "admin", "member", "outsider")])
        session.add_all([Workspace(id="team", name="Team"), Workspace(id="other", name="Other")])
        session.flush()
        session.add_all([WorkspaceMember(workspace_id="team", user_id=name, role=role) for name, role in (("owner", "owner"), ("admin", "admin"), ("member", "member"))])
        session.add_all([Channel(id=name, name=name, type="direct" if name == "direct" else "group") for name in ("room", "hidden", "direct", "secure")])
        session.flush()
        session.add_all([WorkspaceChannel(channel_id=name, workspace_id="team") for name in ("room", "hidden", "direct", "secure")])
        session.add_all([ChannelMember(channel_id=channel, user_id=user, role=role) for channel, user, role in (
            ("room", "owner", "admin"), ("room", "admin", "member"), ("room", "member", "member"),
            ("hidden", "admin", "admin"), ("secure", "owner", "admin"), ("direct", "owner", "admin"),
        )])
        session.add(SecureChannel(channel_id="secure", created_by="owner"))
        session.commit()
    app = FastAPI()
    for module in (api, messages, files, decisions, notifications):
        app.include_router(module.router)
    def sessions():
        with Session(engine) as session:
            yield session
    app.dependency_overrides[get_session] = sessions
    headers = {name: {"Authorization": "Bearer " + create_token(name)} for name in ("owner", "admin", "member", "outsider")}
    with TestClient(app) as client:
        yield client, engine, headers
    engine.dispose()


def create(team, **values):
    client, _, headers = team
    data = {"name": "n8n", "url": "https://hooks.example.com/very-private-trigger", "channel_ids": ["room"], "event_types": ["message.created"]}
    data.update(values)
    response = client.post("/workspaces/team/webhooks", headers=headers["owner"], json=data)
    assert response.status_code == 201, response.text
    return response.json()


def test_channel_admin_grants_and_private_logs_are_not_workspace_admin_directory(team):
    client, _, headers = team
    made = create(team)
    identifier = made["webhook"]["id"]
    assert client.get("/workspaces/team/webhooks", headers=headers["owner"]).json()["webhooks"][0]["id"] == identifier
    assert client.get("/workspaces/team/webhooks", headers=headers["admin"]).json()["webhooks"] == []
    assert client.get(f"/workspaces/team/webhooks/{identifier}/deliveries", headers=headers["admin"]).status_code == 404
    assert client.get("/workspaces/team/webhooks", headers=headers["member"]).status_code == 403
    assert client.get("/workspaces/team/webhooks", headers=headers["outsider"]).status_code == 403
    assert client.get("/workspaces/other/webhooks", headers=headers["owner"]).status_code == 403
    for channel in ("hidden", "direct"):
        assert client.post("/workspaces/team/webhooks", headers=headers["owner"], json={"name": "No", "url": "https://hooks.example.com/hook", "channel_ids": [channel], "event_types": ["message.created"]}).status_code == 403


def test_one_time_secret_and_target_are_encrypted_then_rotation_cancels_old_work(team):
    client, engine, headers = team
    made = create(team)
    identifier, secret = made["webhook"]["id"], made["secret"]
    assert len(secret) >= 40 and made["webhook"]["origin"] == "https://hooks.example.com"
    listing = client.get("/workspaces/team/webhooks", headers=headers["owner"]).text
    assert secret not in listing and "very-private-trigger" not in listing and "encrypted_" not in listing
    queued = client.post(f"/workspaces/team/webhooks/{identifier}/test", headers=headers["owner"])
    assert queued.status_code == 200
    with Session(engine) as session:
        row = session.get(WebhookEndpoint, identifier)
        assert secret not in repr(row) and "very-private-trigger" not in repr(row)
        assert row.encrypted_secret.startswith("fcas1:") and row.encrypted_url.startswith("fcas1:")
    rotated = client.post(f"/workspaces/team/webhooks/{identifier}/rotate", headers=headers["owner"])
    assert rotated.status_code == 200 and rotated.json()["secret"] != secret
    with Session(engine) as session:
        assert session.get(WebhookDelivery, queued.json()["delivery_id"]).status == "cancelled"


def test_native_message_and_secure_activity_payloads_are_opaque_and_deduplicated(team, monkeypatch):
    client, engine, headers = team
    made = create(team, channel_ids=["room", "secure"], event_types=["message.created", "channel.activity"])
    async def no_broadcast(*args, **kwargs): pass
    monkeypatch.setattr(messages, "broadcast_to_channel", no_broadcast)
    monkeypatch.setattr(messages, "process_message_mentions", no_broadcast)
    marker = "Do not send this confidential body to n8n"
    sent = client.post("/messages/channels/room", headers=headers["owner"], json={"content": marker})
    assert sent.status_code == 200, sent.text
    with Session(engine) as session:
        service.enqueue_channel_event(session, "room", "message.created", sent.json()["id"])
        service.enqueue_channel_event(session, "secure", "channel.activity", "$opaque-matrix-event")
        service.enqueue_channel_event(session, "secure", "channel.activity", "$opaque-matrix-event")
        session.commit()
        rows = session.exec(select(WebhookDelivery).where(WebhookDelivery.webhook_id == made["webhook"]["id"])).all()
        assert len(rows) == 2
        assert all(marker not in row.payload and "filename" not in row.payload and "secret" not in row.payload for row in rows)
        for row in rows:
            body = json.loads(row.payload)
            assert set(body) == {"schema_version", "id", "type", "occurred_at", "data"}
            assert set(body["data"]) == {"workspace_id", "channel_id", "resource_id"}


def test_real_receiver_checks_exact_body_hmac_and_worker_acknowledges_once(team):
    client, engine, headers = team
    made = create(team)
    received = []
    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            stamp = self.headers["X-FreeCollab-Timestamp"]
            expected = "v1=" + hmac.new(made["secret"].encode(), stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
            assert hmac.compare_digest(self.headers["X-FreeCollab-Signature"], expected)
            parsed = json.loads(body)
            assert parsed["id"] == self.headers["X-FreeCollab-Event-ID"]
            assert parsed["type"] == self.headers["X-FreeCollab-Event"] == "webhook.test"
            assert parsed["data"] == {"workspace_id": "team"}
            received.append(body)
            self.send_response(204)
            self.end_headers()
        def log_message(self, *args): pass
    receiver = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    thread = threading.Thread(target=receiver.serve_forever, daemon=True)
    thread.start()
    try:
        queued = client.post(f'/workspaces/team/webhooks/{made["webhook"]["id"]}/test', headers=headers["owner"]).json()
        def local_test_receiver(_url, body, signed_headers):
            # Deliberate test-only injection; production transport has no local
            # host exceptions and cannot be configured through the API.
            with httpx.Client(trust_env=False) as transport:
                result = transport.post(f"http://127.0.0.1:{receiver.server_port}/", content=body, headers=signed_headers)
                return result.status_code, None
        with Session(engine) as session:
            assert service.deliver_pending(session, send=local_test_receiver) == 1
            assert service.deliver_pending(session, send=local_test_receiver) == 0
            assert session.get(WebhookDelivery, queued["delivery_id"]).status == "delivered"
        assert len(received) == 1
    finally:
        receiver.shutdown()
        receiver.server_close()
        thread.join(3)


def test_retry_backoff_attempt_limit_and_explicit_retry_keep_event_identity(team):
    client, engine, headers = team
    made = create(team)
    path = f'/workspaces/team/webhooks/{made["webhook"]["id"]}'
    delivery_id = client.post(path + "/test", headers=headers["owner"]).json()["delivery_id"]
    seen = []
    def busy(_url, body, signed):
        seen.append((body, signed["X-FreeCollab-Event-ID"]))
        return 429, "600"
    with Session(engine) as session:
        for attempt in range(8):
            assert service.deliver_pending(session, send=busy) == 0
            row = session.get(WebhookDelivery, delivery_id)
            assert row.attempts == attempt + 1
            assert row.next_attempt_at > datetime.utcnow() + timedelta(seconds=590)
            if attempt < 7:
                assert row.status == "pending"
                row.next_attempt_at = datetime.utcnow() - timedelta(seconds=1)
                session.add(row)
                session.commit()
        assert row.status == "failed"
    assert len({item[0] for item in seen}) == len({item[1] for item in seen}) == 1
    retried = client.post(path + f"/deliveries/{delivery_id}/retry", headers=headers["owner"])
    assert retried.status_code == 200
    with Session(engine) as session:
        row = session.get(WebhookDelivery, delivery_id)
        assert row.attempts == 0 and row.status == "pending"


def test_membership_revocation_pause_and_endpoint_deletion_cancel_delivery(team):
    client, engine, headers = team
    made = create(team)
    path = f'/workspaces/team/webhooks/{made["webhook"]["id"]}'
    first = client.post(path + "/test", headers=headers["owner"]).json()["delivery_id"]
    with Session(engine) as session:
        membership = session.get(ChannelMember, ("room", "owner"))
        membership.role = "member"
        session.add(membership)
        session.commit()
        def unexpected(*args): raise AssertionError("Removed grant reached receiver")
        assert service.deliver_pending(session, send=unexpected) == 0
        assert session.get(WebhookDelivery, first).status == "cancelled"
        membership.role = "admin"
        session.add(membership)
        session.commit()
    second = client.post(path + "/test", headers=headers["owner"]).json()["delivery_id"]
    assert client.patch(path, headers=headers["owner"], json={"enabled": False}).status_code == 200
    with Session(engine) as session:
        assert session.get(WebhookDelivery, second).status == "cancelled"
    assert client.post(path + "/test", headers=headers["owner"]).status_code == 409
    assert client.delete(path, headers=headers["owner"]).status_code == 200
    with Session(engine) as session:
        row = session.get(WebhookEndpoint, made["webhook"]["id"])
        assert row.deleted_at and row.encrypted_url == row.encrypted_secret == ""


@pytest.mark.parametrize("url", [
    "http://hooks.example.com/", "https://localhost/", "https://127.0.0.1/", "https://169.254.169.254/",
    "https://[::1]/", "https://10.1.2.3/", "https://user:secret@hooks.example.com/", "https://hooks.example.com:8443/",
    "https://hooks.example.com/#fragment", "https://hooks.example.com/\r\nHeader:evil", "https://metadata.google.internal/",
    "https://hooks.example.com./", "https://2130706433/", "https://0x7f000001/", "https://[64:ff9b::a00:1]/",
])
def test_ssrf_url_validation_rejects_private_credentials_controls_and_alt_ports(url):
    with pytest.raises(Exception):
        service.normalize_url(url)


def test_dns_rebinding_and_mixed_dns_are_denied_before_any_network(monkeypatch):
    monkeypatch.setattr(service.socket, "getaddrinfo", lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)), (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(Exception): service.public_addresses("hooks.example.com")
    monkeypatch.setattr(service, "public_addresses", lambda host: (_ for _ in ()).throw(ValueError("rebound")))
    monkeypatch.setattr(service.httpcore, "ConnectionPool", lambda **kwargs: pytest.fail("Private rebinding reached network"))
    with pytest.raises(ValueError): service.post_webhook("https://hooks.example.com/", b"{}", {})


def test_pinned_tls_name_changes_only_destination_not_https_proxy():
    names = []
    class Stream:
        def start_tls(self, context, name, timeout): names.append(name); return self
    stream = service._TLSNameStream(Stream(), "8.8.8.8", "hooks.example.com")
    stream = stream.start_tls(None, "proxy.example.com")
    stream.start_tls(None, "8.8.8.8")
    assert names == ["proxy.example.com", "hooks.example.com"]


def test_stream_deadline_cannot_be_reset_by_a_slow_drip_receiver(monkeypatch):
    clock = {"time": 100.0}
    calls = []
    class Stream:
        def read(self, maximum, timeout): calls.append(timeout); return b"x"
    monkeypatch.setattr(service.time, "monotonic", lambda: clock["time"])
    stream = service._TLSNameStream(Stream(), "8.8.8.8", "hooks.example.com")
    clock["time"] = 110
    assert stream.read(1, 15) == b"x"
    clock["time"] = 129
    assert stream.read(1, 15) == b"x"
    clock["time"] = 131
    with pytest.raises(service.httpcore.ReadTimeout): stream.read(1, 15)
    assert calls == [15, 1]


def test_session_version_is_rechecked_after_waiting_for_workspace_lock(team, monkeypatch):
    client, engine, headers = team
    made = create(team)
    original = api.lock_workspace
    def security_change(session, workspace):
        original(session, workspace)
        state = session.get(AccountSecurity, "owner")
        if state is None: state = AccountSecurity(user_id="owner")
        state.auth_version += 1
        session.add(state)
        session.flush()
    monkeypatch.setattr(api, "lock_workspace", security_change)
    response = client.post(f'/workspaces/team/webhooks/{made["webhook"]["id"]}/rotate', headers=headers["owner"])
    assert response.status_code == 401
    with Session(engine) as session:
        assert session.get(WebhookEndpoint, made["webhook"]["id"]).generation == 1


def test_redirect_is_a_failed_delivery_and_expired_processing_claim_recovers(team):
    client, engine, headers = team
    made = create(team)
    identifier = client.post(f'/workspaces/team/webhooks/{made["webhook"]["id"]}/test', headers=headers["owner"]).json()["delivery_id"]
    with Session(engine) as session:
        row = session.get(WebhookDelivery, identifier)
        row.status, row.claimed_at, row.claim_token = "processing", datetime.utcnow() - timedelta(minutes=5), "old-worker"
        session.add(row)
        session.commit()
        calls = []
        def redirect(*args): calls.append(1); return 302, None
        assert service.deliver_pending(session, send=redirect) == 0
        assert session.get(WebhookDelivery, identifier).status == "failed"
        assert len(calls) == 1


def test_post_pins_destination_and_preserves_proxy_ca_and_no_redirects(monkeypatch):
    monkeypatch.setattr(service, "public_addresses", lambda host: ["8.8.8.8"])
    monkeypatch.setattr(service, "getproxies", lambda: {"https": "http://user:private@proxy.example.com:8080"})
    observed = {}
    class Response:
        status = 302
        headers = [(b"location", b"https://127.0.0.1/private")]
        def __enter__(self): return self
        def __exit__(self, *args): pass
    class Pool:
        def __init__(self, proxy, **kwargs): observed.update(proxy=proxy, options=kwargs)
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def stream(self, method, url, **kwargs): observed.update(method=method, url=url, request=kwargs); return Response()
    monkeypatch.setattr(service.httpcore, "HTTPProxy", Pool)
    assert service.post_webhook("https://hooks.example.com/private-path?a=1", b"{}", {}) == (302, None)
    assert observed["url"] == "https://8.8.8.8/private-path?a=1"
    assert observed["request"]["headers"]["Host"] == "hooks.example.com"
    assert observed["options"]["proxy_auth"] == (b"user", b"private")
    assert observed["options"]["ssl_context"].check_hostname
    assert observed["options"]["ssl_context"].verify_mode == 2


def test_real_connect_proxy_pins_ip_and_verifies_original_tls_hostname(tmp_path, monkeypatch):
    key, certificate = tmp_path / "fixture.key", tmp_path / "fixture.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(certificate),
                    "-days", "1", "-subj", "/CN=hooks.example.com", "-addext", "subjectAltName=DNS:hooks.example.com"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    observed = {"connect": [], "sni": [], "requests": 0}
    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            assert body == b'{"fixture":true}'
            assert self.headers["Host"] == "hooks.example.com"
            observed["requests"] += 1
            self.send_response(204)
            self.end_headers()
        def log_message(self, *args): pass
    https = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, key)
    context.set_servername_callback(lambda _socket, name, _context: observed["sni"].append(name))
    https.socket = context.wrap_socket(https.socket, server_side=True)
    class Proxy(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(3)
            headers = b""
            while b"\r\n\r\n" not in headers:
                data = self.request.recv(4096)
                if not data: return
                headers += data
                if len(headers) > 8192: return
            first = headers.split(b"\r\n", 1)[0]
            observed["connect"].append(first)
            assert first == b"CONNECT 8.8.8.8:443 HTTP/1.1"
            # Deliberately route this isolated test proxy to its local TLS
            # fixture. Production code still CONNECTs only the validated IP.
            with socket.create_connection(("127.0.0.1", https.server_port), timeout=3) as upstream:
                self.request.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                try:
                    while True:
                        ready, _, _ = socket_select.select([self.request, upstream], [], [], 3)
                        if not ready: return
                        for source in ready:
                            data = source.recv(16384)
                            if not data: return
                            (upstream if source is self.request else self.request).sendall(data)
                except (OSError, TimeoutError): pass
    proxy = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Proxy)
    proxy.daemon_threads = True
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (https, proxy)]
    for thread in threads: thread.start()
    monkeypatch.setenv("SSL_CERT_FILE", str(certificate))
    monkeypatch.setattr(service, "getproxies", lambda: {"https": f"http://127.0.0.1:{proxy.server_address[1]}"})
    monkeypatch.setattr(service, "public_addresses", lambda host: ["8.8.8.8"])
    try:
        body = b'{"fixture":true}'
        assert service.post_webhook("https://hooks.example.com/", body, {"Content-Type": "application/json"}) == (204, None)
        with pytest.raises(Exception):
            service.post_webhook("https://wrong-name.example.com/", body, {"Content-Type": "application/json"})
        assert observed["requests"] == 1
        assert observed["sni"] == ["hooks.example.com", "wrong-name.example.com"]
        assert len(observed["connect"]) == 2
    finally:
        for server in (proxy, https): server.shutdown(); server.server_close()
        for thread in threads: thread.join(3)


def test_disabled_server_queues_nothing_but_can_revoke_existing_endpoint(team, monkeypatch):
    client, engine, headers = team
    made = create(team)
    monkeypatch.setattr(service, "settings", SimpleNamespace(webhooks_enabled=False))
    listing = client.get("/workspaces/team/webhooks", headers=headers["owner"])
    assert listing.status_code == 200 and listing.json()["enabled"] is False
    assert client.post("/workspaces/team/webhooks", headers=headers["owner"], json={"name": "Disabled", "url": "https://hooks.example.com/", "channel_ids": ["room"], "event_types": ["message.created"]}).status_code == 503
    with Session(engine) as session:
        assert service.enqueue_channel_event(session, "room", "message.created", "never-queued") == 0
        assert service.deliver_pending(session, send=lambda *args: pytest.fail("Disabled worker sent data")) == 0
    assert client.delete(f'/workspaces/team/webhooks/{made["webhook"]["id"]}', headers=headers["owner"]).status_code == 200
