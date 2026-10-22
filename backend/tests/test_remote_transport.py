"""Sync HTTP transport keeps credentials bounded, pinned and TLS verified."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import select
import socket
import socketserver
import ssl
import subprocess
import threading

import pytest

from app.services import remote_transport as transport


@contextmanager
def pool(monkeypatch, status=200, headers=(), chunks=(b'{}',)):
    calls = []
    class Response:
        def __init__(self): self.status, self.headers = status, headers
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def iter_stream(self): yield from chunks() if callable(chunks) else chunks
    class Pool:
        def __init__(self, *args, **kwargs): calls.append({'options': kwargs, 'args': args})
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def stream(self, method, url, **kwargs): calls[-1].update(method=method, url=url, request=kwargs); return Response()
    monkeypatch.setattr(transport.pinned, 'public_addresses', lambda host, timeout=5: ['8.8.8.8'])
    monkeypatch.setattr(transport.pinned, 'getproxies', lambda: {'https': 'http://proxy.example.net:8080'})
    monkeypatch.setattr(transport.httpcore, 'HTTPProxy', Pool)
    monkeypatch.setattr(transport.httpcore, 'ConnectionPool', lambda **_: pytest.fail('Unexpected direct connection'))
    yield calls


@pytest.mark.parametrize('url', ['http://files.example.com/', 'https://127.0.0.1/', 'https://10.0.0.1/', 'https://[::1]/', 'https://metadata.google.internal/', 'https://files.example.com:8443/', 'https://private:secret@files.example.com/', 'https://files.example.com/#fragment'])
def test_runtime_private_destinations_are_rejected_before_network(monkeypatch, url):
    monkeypatch.setattr(transport.httpcore, 'HTTPProxy', lambda *_, **__: pytest.fail('Unsafe destination reached proxy'))
    with pytest.raises(transport.ProviderError) as error: transport.request('GET', url)
    assert error.value.code == 'unsafe_destination' and 'secret' not in str(error.value)


@pytest.mark.parametrize('method', ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'PROPFIND', 'REPORT', 'OPTIONS'])
def test_supported_methods_keep_original_host_pinning_and_credentials(monkeypatch, method):
    with pool(monkeypatch) as calls:
        response = transport.request(method, 'https://files.example.com/selected/item?view=one', headers={'Authorization': 'Bearer private-token', 'If-Match': '"known-etag"'}, body=b'payload' if method not in {'GET', 'DELETE', 'OPTIONS'} else None)
    call = calls[0]
    assert response.status == 200 and call['url'] == 'https://8.8.8.8/selected/item?view=one'
    assert call['request']['headers']['Host'] == 'files.example.com'
    assert call['request']['headers']['Authorization'] == 'Bearer private-token'
    assert call['options']['ssl_context'].check_hostname and call['options']['ssl_context'].verify_mode == ssl.CERT_REQUIRED


@pytest.mark.parametrize('status,headers,chunks,code', [
    (302, [(b'location', b'https://private:secret@127.0.0.1/')], (b'',), 'redirect_not_allowed'),
    (200, [(b'content-length', b'99999999')], (b'',), 'response_too_large'),
    (200, [], (b'x' * 101,), 'response_too_large'),
    (200, [(b'content-encoding', b'gzip')], (b'compressed',), 'unsupported_encoding'),
])
def test_redirect_encoding_and_body_bounds_are_fail_closed(monkeypatch, status, headers, chunks, code):
    with pool(monkeypatch, status=status, headers=headers, chunks=chunks):
        with pytest.raises(transport.ProviderError) as error: transport.request('GET', 'https://files.example.com/private-token', max_bytes=100)
    assert error.value.code == code and 'private-token' not in str(error.value) and 'secret' not in str(error.value)


def test_http_error_bodies_return_only_for_typed_protocol_handling(monkeypatch):
    with pool(monkeypatch, status=429, headers=[(b'retry-after', b'999999')], chunks=(b'secret provider error body',)):
        response = transport.request('GET', 'https://files.example.com/item')
    with pytest.raises(transport.ProviderError) as error: transport.check_response(response)
    assert error.value.code == 'rate_limited' and error.value.retry_after == 3600
    assert 'secret' not in str(error.value)


@pytest.mark.parametrize('headers', [{'Host': 'attacker.example.net'}, {'Authorization': 'Bearer token\r\nHost:evil'}, {'Content-Length': '999'}, {'If-Match': '\x00'}])
def test_caller_headers_cannot_override_transport_or_inject_headers(monkeypatch, headers):
    with pool(monkeypatch) as calls:
        with pytest.raises(transport.ProviderError): transport.request('PATCH', 'https://files.example.com/item', headers=headers, body=b'content')
    assert calls == []


def test_absolute_deadline_covers_streaming(monkeypatch):
    clock = {'time': 100.0}
    monkeypatch.setattr(transport.time, 'monotonic', lambda: clock['time'])
    def chunks():
        yield b'{'
        clock['time'] = 131
        yield b'}'
    with pool(monkeypatch, chunks=chunks):
        with pytest.raises(transport.ProviderError) as failure: transport.request('GET', 'https://files.example.com/item', timeout=30)
    assert failure.value.code == 'provider_timeout'


def test_ambiguous_or_nonfinite_json_is_not_accepted():
    for body in (b'{"key":1,"key":2}', b'{"key":NaN}', b'[]', b'not json'):
        with pytest.raises(transport.ProviderError): transport.Response(200, {}, body).json()


def test_excessively_nested_provider_json_remains_a_typed_invalid_response():
    body = b'{"nested":' + b'[' * 1500 + b'0' + b']' * 1500 + b'}'
    assert len(body) < transport.MAX_JSON_BYTES
    with pytest.raises(transport.ProviderError) as failure:
        transport.Response(200, {}, body).json()
    assert failure.value.code == 'invalid_response'


@pytest.mark.parametrize('proxy_tls', [False, True])
def test_real_connect_tunnel_verifies_tls_hostname_and_transfers_binary_dav_methods(tmp_path, monkeypatch, proxy_tls):
    key, certificate = tmp_path / 'key.pem', tmp_path / 'cert.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(key), '-out', str(certificate), '-days', '1', '-subj', '/CN=files.example.com', '-addext', 'subjectAltName=DNS:files.example.com,IP:127.0.0.1'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    observed = {'connect': [], 'sni': [], 'requests': []}
    class Receiver(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def handle_request(self):
            assert self.headers.get('Host') == 'files.example.com'
            assert self.headers.get('Authorization') == 'Bearer local-only-token'
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            observed['requests'].append((self.command, body))
            data = b'\x00\xffreal binary file' if self.command == 'GET' else b'<multistatus xmlns="DAV:"/>'
            self.send_response(207 if self.command == 'PROPFIND' else 200)
            self.send_header('ETag', '"fixture-etag"'); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
        do_GET = do_PATCH = do_PROPFIND = handle_request
    https = ThreadingHTTPServer(('127.0.0.1', 0), Receiver)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(certificate, key)
    context.set_servername_callback(lambda _socket, name, _context: observed['sni'].append(name))
    https.socket = context.wrap_socket(https.socket, server_side=True)
    class Proxy(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(3); header = b''
            while b'\r\n\r\n' not in header:
                chunk = self.request.recv(4096)
                if not chunk: return
                header += chunk
                if len(header) > 8192: return
            first = header.split(b'\r\n', 1)[0]; observed['connect'].append(first)
            assert first == b'CONNECT 8.8.8.8:443 HTTP/1.1'
            with socket.create_connection(('127.0.0.1', https.server_port), timeout=3) as upstream:
                self.request.sendall(b'HTTP/1.1 200 Connection established\r\n\r\n')
                try:
                    while True:
                        ready, _, _ = select.select([self.request, upstream], [], [], 3)
                        if not ready: return
                        for source in ready:
                            block = source.recv(16384)
                            if not block: return
                            (upstream if source is self.request else self.request).sendall(block)
                except (OSError, TimeoutError): pass
    proxy = socketserver.ThreadingTCPServer(('127.0.0.1', 0), Proxy); proxy.daemon_threads = True
    if proxy_tls: proxy.socket = context.wrap_socket(proxy.socket, server_side=True)
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (https, proxy)]
    for thread in threads: thread.start()
    trust = tmp_path / 'combined.pem'; original = ssl.get_default_verify_paths().cafile
    trust.write_bytes((Path(original).read_bytes() if original else b'') + b'\n' + certificate.read_bytes())
    monkeypatch.setenv('SSL_CERT_FILE', str(trust))
    monkeypatch.setattr(transport.pinned, 'getproxies', lambda: {'https': ('https' if proxy_tls else 'http') + '://127.0.0.1:' + str(proxy.server_address[1])})
    monkeypatch.setattr(transport.pinned, 'public_addresses', lambda host, timeout=5: ['8.8.8.8'])
    try:
        headers = {'Authorization': 'Bearer local-only-token'}
        assert transport.request('GET', 'https://files.example.com/item', headers=headers).body == b'\x00\xffreal binary file'
        assert transport.request('PATCH', 'https://files.example.com/item', headers=headers, body=b'\x00\xffupload').status == 200
        assert transport.request('PROPFIND', 'https://files.example.com/selected/', headers={**headers, 'Depth': '1'}, body=b'<propfind xmlns="DAV:"/>').status == 207
        with pytest.raises(transport.ProviderError): transport.request('GET', 'https://wrong-name.example.com/item', headers=headers)
        assert observed['requests'] == [('GET', b''), ('PATCH', b'\x00\xffupload'), ('PROPFIND', b'<propfind xmlns="DAV:"/>')]
        assert len(observed['connect']) == 4
        assert [name for name in observed['sni'] if name] == ['files.example.com'] * 3 + ['wrong-name.example.com']
    finally:
        for server in (proxy, https): server.shutdown(); server.server_close()
        for thread in threads: thread.join(3)


def test_one_operation_budget_bounds_multiple_independent_http_requests(monkeypatch):
    clock = {'time': 100.0}
    monkeypatch.setattr(transport.time, 'monotonic', lambda: clock['time'])
    budget = transport.begin_budget(45)
    try:
        with pool(monkeypatch) as calls:
            assert transport.request('GET', 'https://files.example.com/first').status == 200
            clock['time'] = 130.0
            nested = transport.begin_budget(45)
            try:
                assert transport.request('GET', 'https://files.example.com/second').status == 200
                assert calls[-1]['request']['extensions']['timeout']['read'] == 15.0
                clock['time'] = 146.0
                with pytest.raises(transport.ProviderError) as failure: transport.request('GET', 'https://files.example.com/third')
                assert failure.value.code == 'provider_timeout'
            finally: transport.end_budget(nested)
    finally: transport.end_budget(budget)
    # The next independent operation starts with a clean budget.
    with pool(monkeypatch): assert transport.request('GET', 'https://files.example.com/new').status == 200
