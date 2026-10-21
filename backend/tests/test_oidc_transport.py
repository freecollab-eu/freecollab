"""Customer OIDC destinations cannot turn discovery into private network access."""
import base64
from concurrent.futures import Future
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import select
import socket
import socketserver
import ssl
import subprocess
import threading
from urllib.parse import parse_qs

import httpcore
import pytest

from app.services import oidc_transport as transport


@pytest.mark.parametrize("url", [
    "http://idp.example.com/realm", "https://127.0.0.1/", "https://localhost/", "https://idp.local/",
    "https://idp.internal/", "https://metadata.google.internal/", "https://169.254.169.254/token",
    "https://10.0.0.1/", "https://100.64.0.1/", "https://224.0.0.1/", "https://[::1]/",
    "https://[fd00::1]/", "https://[64:ff9b::a00:1]/", "https://[2002:7f00:1::]/",
    "https://[::ffff:127.0.0.1]/", "https://[fe80::1%25eth0]/", "https://2130706433/",
    "https://0x7f000001/", "https://user:private-secret@idp.example.com/",
    "https://@idp.example.com/", "https://idp.example.com:8443/", "https://idp.example.com/#fragment",
    "https://idp.example.com./", "https://idp.example.com/\r\nAuthorization:evil",
    "https://idp.example.com\\@127.0.0.1/", "https://idp.example.com/\x00",
])
def test_customer_url_rejects_private_or_ambiguous_destinations(url):
    with pytest.raises(transport.TransportError) as failure:
        transport.normalize_url(url)
    assert url not in str(failure.value)
    assert "private-secret" not in str(failure.value)


def test_issuer_identifier_is_preserved_but_issuer_query_is_rejected(monkeypatch):
    looked_up = []
    monkeypatch.setattr(transport, "public_addresses", lambda host: looked_up.append(host) or ["8.8.8.8"])
    issuer = "https://IdP.EXAMPLE.com/Realm/"
    assert transport.validate_public_url(issuer, issuer=True) == issuer
    assert looked_up == ["idp.example.com"]
    with pytest.raises(transport.TransportError):
        transport.validate_public_url(issuer + "?tenant=secret", issuer=True)


@pytest.mark.parametrize("addresses", [["127.0.0.1"], ["8.8.8.8", "10.0.0.1"], ["8.8.8.8", "::1"], []])
def test_every_dns_answer_must_be_public(monkeypatch, addresses):
    monkeypatch.setattr(transport.socket, "getaddrinfo", lambda *_, **__: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443)) for address in addresses
    ])
    with pytest.raises(transport.TransportError):
        transport.public_addresses("idp.example.com")


def test_dns_timeout_releases_queued_slot_without_waiting_for_an_unbounded_lookup(monkeypatch):
    future = Future()
    monkeypatch.setattr(transport._DNS_POOL, "submit", lambda *_, **__: future)
    with pytest.raises(transport.TransportError):
        transport.public_addresses("idp.example.com", timeout=.01)
    assert future.cancelled()
    # The cancellation callback must release the bounded admission slot.
    assert transport._DNS_SLOTS.acquire(blocking=False)
    transport._DNS_SLOTS.release()


def test_proxy_dns_is_bounded_and_connects_to_its_resolved_address(monkeypatch):
    observed = []
    monkeypatch.setattr(transport, "_resolve", lambda host, port, timeout: observed.append((host, port, timeout)) or ["10.0.0.2"])
    backend = transport._PinnedBackend("8.8.8.8", "idp.example.com", transport.time.monotonic() + 12)
    monkeypatch.setattr(backend.backend, "connect_tcp", lambda host, port, *args: observed.append((host, port)) or object())
    backend.connect_tcp("trusted-proxy.internal", 8080, timeout=5)
    assert observed[0][:2] == ("trusted-proxy.internal", 8080)
    assert 0 < observed[0][2] <= 5
    assert observed[1] == ("10.0.0.2", 8080)


def test_proxy_dns_failure_never_opens_a_socket(monkeypatch):
    backend = transport._PinnedBackend("8.8.8.8", "idp.example.com", transport.time.monotonic() + 12)
    monkeypatch.setattr(transport, "_resolve", lambda *args: (_ for _ in ()).throw(transport.TransportError("DNS timeout")))
    monkeypatch.setattr(backend.backend, "connect_tcp", lambda *args: pytest.fail("Timed-out proxy DNS reached a socket"))
    with pytest.raises(transport.TransportError):
        backend.connect_tcp("trusted-proxy.internal", 8080, timeout=5)


def test_proxy_selection_honors_original_host_no_proxy_and_all_proxy(monkeypatch):
    monkeypatch.setattr(transport, "getproxies", lambda: {"https": "https://user:private@proxy.example.net:8443", "no": ".example.com"})
    assert transport._proxy("idp.example.com", 443) is None
    assert transport._proxy("idp.example.net", 443) == ("https://proxy.example.net:8443", (b"user", b"private"), True)
    monkeypatch.setattr(transport, "getproxies", lambda: {"all": "http://proxy.example.net:8080", "no": "different.example.net:443"})
    assert transport._proxy("different.example.net", 443) is None
    assert transport._proxy("idp.example.net", 443) == ("http://proxy.example.net:8080", None, False)


@contextmanager
def fake_pool(monkeypatch, *, status=200, headers=(), chunks=(b'{"valid":true}',)):
    calls = []
    class Response:
        def __init__(self): self.status, self.headers = status, headers
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def iter_stream(self):
            yield from chunks() if callable(chunks) else chunks
    class Pool:
        def __init__(self, *args, **kwargs): calls.append({"pool_args": args, "options": kwargs})
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def stream(self, method, url, **kwargs):
            calls[-1].update(method=method, url=url, request=kwargs)
            return Response()
    monkeypatch.setattr(transport, "public_addresses", lambda host, timeout=5: ["8.8.8.8"])
    monkeypatch.setattr(transport, "getproxies", lambda: {"https": "http://user:private@proxy.example.net:8080"})
    monkeypatch.setattr(transport.httpcore, "HTTPProxy", Pool)
    monkeypatch.setattr(transport.httpcore, "ConnectionPool", lambda **_: pytest.fail("Proxy failure used direct fallback"))
    yield calls


def test_every_cross_host_endpoint_is_pinned_with_original_tls_host_and_form_auth(monkeypatch):
    with fake_pool(monkeypatch) as calls:
        assert transport.request_json("GET", "https://metadata.example.com/realm/.well-known/openid-configuration") == {"valid": True}
        assert transport.request_json("POST", "https://tokens.example.net/token?tenant=team", data={"code": "one time", "grant_type": "authorization_code"}, auth=("client%3Aid", "secret%3Avalue")) == {"valid": True}
        assert transport.request_json("GET", "https://keys.example.org/jwks") == {"valid": True}
    assert [call["request"]["headers"]["Host"] for call in calls] == ["metadata.example.com", "tokens.example.net", "keys.example.org"]
    assert calls[1]["url"] == "https://8.8.8.8/token?tenant=team"
    assert parse_qs(calls[1]["request"]["content"].decode()) == {"code": ["one time"], "grant_type": ["authorization_code"]}
    assert calls[1]["request"]["headers"]["Authorization"] == "Basic " + base64.b64encode(b"client%3Aid:secret%3Avalue").decode()
    for call in calls:
        assert call["options"]["proxy_auth"] == (b"user", b"private")
        assert call["options"]["ssl_context"].check_hostname
        assert call["options"]["ssl_context"].verify_mode == ssl.CERT_REQUIRED


@pytest.mark.parametrize("status,headers,chunks", [
    (302, [(b"location", b"https://127.0.0.1/private")], (b"",)),
    (500, [], (b"provider secret in error",)),
    (200, [(b"content-length", b"99999999")], (b"{}",)),
    (200, [], (b"x" * (transport.MAX_RESPONSE_BYTES + 1),)),
    (200, [], (b"[]",)),
    (200, [], (b"not json",)),
    (200, [], (b'{"issuer":"a","issuer":"b"}',)),
    (200, [], (b'{"value":NaN}',)),
])
def test_redirects_and_bad_or_oversized_responses_do_not_leak_details(monkeypatch, status, headers, chunks):
    with fake_pool(monkeypatch, status=status, headers=headers, chunks=chunks) as calls:
        with pytest.raises(transport.TransportError) as failure:
            transport.request_json("POST", "https://idp.example.com/private-secret", data={"client_secret": "client-secret"})
    assert len(calls) == 1
    assert str(failure.value) == "Organization sign-in provider request failed"
    assert failure.value.__suppress_context__


def test_dns_rebinding_is_rechecked_before_any_new_token_connection(monkeypatch):
    monkeypatch.setattr(transport, "public_addresses", lambda host, timeout=5: (_ for _ in ()).throw(transport.TransportError("private answer")))
    monkeypatch.setattr(transport.httpcore, "HTTPProxy", lambda *_, **__: pytest.fail("Private DNS reached proxy"))
    monkeypatch.setattr(transport.httpcore, "ConnectionPool", lambda *_, **__: pytest.fail("Private DNS reached socket"))
    with pytest.raises(transport.TransportError):
        transport.request_json("POST", "https://idp.example.com/token", data={"client_secret": "not sent"})


def test_stream_and_json_body_share_an_absolute_deadline(monkeypatch):
    clock = {"now": 100.0}
    monkeypatch.setattr(transport.time, "monotonic", lambda: clock["now"])
    def chunks():
        yield b'{"value":"'
        clock["now"] = 113.0
        yield b'late"}'
    with fake_pool(monkeypatch, chunks=chunks):
        with pytest.raises(transport.TransportError):
            transport.request_json("GET", "https://idp.example.com/jwks", timeout=12)


def test_tls_proxy_handshake_keeps_proxy_identity_even_when_its_ip_equals_target():
    names = []
    class Stream:
        def start_tls(self, context, name, timeout): names.append(name); return self
    stream = transport._TLSNameStream(Stream(), "8.8.8.8", "idp.example.com", transport.time.monotonic() + 10, True)
    stream = stream.start_tls(None, "8.8.8.8")
    stream.start_tls(None, "8.8.8.8")
    assert names == ["8.8.8.8", "idp.example.com"]


@pytest.mark.parametrize("proxy_tls", [False, True])
def test_real_proxy_tunnel_validates_original_host_and_transfers_json_form_and_basic_auth(tmp_path, monkeypatch, proxy_tls):
    key, certificate = tmp_path / "fixture.key", tmp_path / "fixture.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(certificate),
                    "-days", "1", "-subj", "/CN=idp.example.com", "-addext", "subjectAltName=DNS:idp.example.com,IP:127.0.0.1"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    observed = {"connect": [], "sni": [], "requests": []}
    class Receiver(BaseHTTPRequestHandler):
        def reply(self, body):
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        def do_GET(self):
            observed["requests"].append(("GET", self.path))
            assert self.headers["Host"] == "idp.example.com"
            self.reply(b'{"keys":[{"kid":"test-key"}]}')
        def do_POST(self):
            observed["requests"].append(("POST", self.path))
            assert self.headers["Host"] == "idp.example.com"
            assert parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode()) == {"code": ["local-only"], "grant_type": ["authorization_code"]}
            assert self.headers["Authorization"] == "Basic " + base64.b64encode(b"local-client:local-secret").decode()
            self.reply(b'{"id_token":"synthetic-local-response"}')
        def log_message(self, *_): pass
    https = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, key)
    context.set_servername_callback(lambda _socket, name, _context: observed["sni"].append(name))
    https.socket = context.wrap_socket(https.socket, server_side=True)
    class Proxy(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(3)
            header = b""
            while b"\r\n\r\n" not in header:
                block = self.request.recv(4096)
                if not block: return
                header += block
                if len(header) > 8192: return
            first = header.split(b"\r\n", 1)[0]
            observed["connect"].append(first)
            assert first == b"CONNECT 8.8.8.8:443 HTTP/1.1"
            # Only this isolated fixture proxy routes a test public IP to local
            # TLS. Runtime policy still emits a pinned public CONNECT target.
            with socket.create_connection(("127.0.0.1", https.server_port), timeout=3) as upstream:
                self.request.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                try:
                    while True:
                        ready, _, _ = select.select([self.request, upstream], [], [], 3)
                        if not ready: return
                        for source in ready:
                            block = source.recv(16384)
                            if not block: return
                            (upstream if source is self.request else self.request).sendall(block)
                except (OSError, TimeoutError): pass
    proxy = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Proxy)
    proxy.daemon_threads = True
    if proxy_tls:
        proxy.socket = context.wrap_socket(proxy.socket, server_side=True)
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (https, proxy)]
    for thread in threads: thread.start()
    trust = tmp_path / "combined-trust.pem"
    original = ssl.get_default_verify_paths().cafile
    trust.write_bytes((Path(original).read_bytes() if original else b"") + b"\n" + certificate.read_bytes())
    monkeypatch.setenv("SSL_CERT_FILE", str(trust))
    monkeypatch.setattr(transport, "getproxies", lambda: {"https": ("https" if proxy_tls else "http") + f"://127.0.0.1:{proxy.server_address[1]}"})
    monkeypatch.setattr(transport, "public_addresses", lambda host, timeout=5: ["8.8.8.8"])
    try:
        assert transport.request_json("GET", "https://idp.example.com/jwks") == {"keys": [{"kid": "test-key"}]}
        assert transport.request_json("POST", "https://idp.example.com/token", data={"code": "local-only", "grant_type": "authorization_code"}, auth=("local-client", "local-secret")) == {"id_token": "synthetic-local-response"}
        with pytest.raises(transport.TransportError):
            transport.request_json("GET", "https://wrong-name.example.com/jwks")
        assert observed["requests"] == [("GET", "/jwks"), ("POST", "/token")]
        assert len(observed["connect"]) == 3
        assert [value for value in observed["sni"] if value] == ["idp.example.com", "idp.example.com", "wrong-name.example.com"]
    finally:
        for server in (proxy, https): server.shutdown(); server.server_close()
        for thread in threads: thread.join(3)
