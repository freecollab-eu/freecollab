"""Real verified HTTPS through pinned transport for every picker provider."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import select
import socket
import socketserver
import ssl
import subprocess
import threading
from urllib.parse import parse_qs, urlsplit

import pytest
from app.services import cloud_file_providers as providers, remote_transport as transport

CREDENTIALS = {"access_token": "private-fixture-bearer"}
CONFIGS = {"google": {"client_id": "customer", "folder_id": "selected-folder"},
    "microsoft": {"client_id": "customer", "tenant_id": "12345678-1234-1234-1234-123456789012", "drive_id": "drive", "folder_id": "selected-folder"},
    "dropbox": {"client_id": "customer", "folder_id": "/chosen"}, "box": {"client_id": "customer", "folder_id": "10"}}


@pytest.fixture
def https(tmp_path, monkeypatch):
    hosts = {"www.googleapis.com", "graph.microsoft.com", "api.dropboxapi.com", "content.dropboxapi.com", "api.box.com", "dl.fixture.boxcloud.com", "files.fixture.1drv.com"}
    key, cert = tmp_path / "key.pem", tmp_path / "cert.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert), "-days", "1", "-subj", "/CN=www.googleapis.com", "-addext", "subjectAltName=" + ",".join("DNS:" + host for host in sorted(hosts))], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    state = {"mode": "stable", "revision": 1, "requests": [], "connects": [], "bytes": b"actual four-provider HTTPS bytes"}
    class Receiver(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def reply(self, status, value, extra=None):
            data = value if isinstance(value, bytes) else json.dumps(value).encode()
            self.send_response(status); self.send_header("Content-Length", str(len(data)))
            for name, value in (extra or {}).items(): self.send_header(name, value)
            self.end_headers()
            try: self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError): pass
        def receive(self):
            host = self.headers.get("Host"); assert host in hosts
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            state["requests"].append({"host": host, "method": self.command, "path": self.path, "headers": dict(self.headers), "body": raw})
            token_request = urlsplit(self.path).path == "/oauth2/token"
            if not token_request and host not in {"files.fixture.1drv.com", "dl.fixture.boxcloud.com"}:
                assert self.headers.get("Authorization") == "Bearer private-fixture-bearer"
            else: assert self.headers.get("Authorization") is None
            return host, urlsplit(self.path).path, parse_qs(raw.decode()) if token_request else json.loads(raw) if raw else None
        def do_POST(self): self.route()
        def do_GET(self): self.route()
        def route(self):
            host, path, payload = self.receive(); revision = state["revision"]
            if state["mode"] == "revoked": return self.reply(401, {"error": "private-provider-secret-description"})
            if path == "/oauth2/token":
                assert payload["client_id"] == ["customer-app"] and payload["client_secret"] == ["private-customer-secret"]
                if payload["grant_type"] == ["authorization_code"]:
                    assert payload["code_verifier"] == ["private-verifier"] and payload["code"] == ["fixture-code"]
                    assert payload["redirect_uri"] == ["https://collab.example.com/api/cloud-files/oauth/callback"]
                else: assert payload["refresh_token"] == ["private-refresh"]
                return self.reply(200, {"access_token":"private-new-access","refresh_token":"private-new-refresh","token_type":"bearer","expires_in":3600})
            if path == "/redirect-test":
                return self.reply(302, b"must not be followed", {"Location": "https://dl.fixture.boxcloud.com/content" if state["mode"] != "oversized" else "https://dl.fixture.boxcloud.com/" + "x" * 16400})
            if host == "www.googleapis.com":
                file = {"id": "remote-file", "name": "notes.txt", "mimeType": "text/plain", "size": str(len(state["bytes"])), "version": str(revision), "parents": ["outside" if state["mode"] == "outside" else "selected-folder"], "webViewLink": "https://docs.google.com/document/d/remote-file"}
                if path == "/drive/v3/files/selected-folder": return self.reply(200, {"id": "selected-folder", "name": "Root", "mimeType": "application/vnd.google-apps.folder"})
                if path == "/drive/v3/files": return self.reply(200, {"files": [file]})
                if urlsplit(self.path).query == "alt=media": return self.content()
                return self.reply(200, file, {"ETag": '"g' + str(revision) + '"'})
            if host == "graph.microsoft.com":
                file = {"id": "remote-file", "name": "notes.txt", "size": len(state["bytes"]), "eTag": '"m' + str(revision) + '"', "file": {"mimeType": "text/plain"}, "parentReference": {"id": "outside" if state["mode"] == "outside" else "selected-folder", "driveId": "drive"}, "@microsoft.graph.downloadUrl": "https://files.fixture.1drv.com/content", "webUrl": "https://fixture.sharepoint.com/notes.txt"}
                if path.endswith("/selected-folder"): return self.reply(200, {"id": "selected-folder", "folder": {}, "name": "Root"})
                if path.endswith("/children"): return self.reply(200, {"value": [file]})
                if "/search(q=" in path: return self.reply(200, {"value": [file]})
                return self.reply(200, file)
            if host == "api.dropboxapi.com":
                file = {".tag": "file", "id": "id:file", "name": "notes.txt", "path_lower": "/outside/notes.txt" if state["mode"] == "outside" else "/chosen/notes.txt", "rev": "r" + str(revision), "size": len(state["bytes"])}
                if path.endswith("/list_folder"): return self.reply(200, {"entries": [file], "has_more": False, "cursor": "private-cursor"})
                return self.reply(200, file)
            if host == "api.box.com":
                file = {"id": "20", "type": "file", "name": "notes.txt", "size": len(state["bytes"]), "etag": str(revision), "parent": {"id": "10"}, "path_collection": {"entries": [{"id": "0"}] if state["mode"] == "outside" else [{"id": "0"}, {"id": "10"}]}}
                if path == "/2.0/folders/10": return self.reply(200, {"id": "10", "type": "folder", "name": "Root", "path_collection": {"entries": [{"id": "0"}]}})
                if path.endswith("/items"): return self.reply(200, {"entries": [file], "total_count": 1})
                if path.endswith("/content"):
                    target = "https://evil.example/private" if state["mode"] == "redirect_escape" else "https://dl.fixture.boxcloud.com/content"
                    return self.reply(302, b"", {"Location": target})
                return self.reply(200, file)
            if host in {"content.dropboxapi.com", "files.fixture.1drv.com", "dl.fixture.boxcloud.com"}: return self.content()
            return self.reply(404, {})
        def content(self):
            if state["mode"] == "content_redirect": return self.reply(302, b"", {"Location": "https://evil.example/collect"})
            self.reply(200, state["bytes"], {"Content-Type": "text/plain"})
            if state["mode"] == "race": state["revision"] += 1
    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    class Proxy(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(4); header = b""
            while b"\r\n\r\n" not in header:
                block = self.request.recv(4096)
                if not block: return
                header += block
                if len(header) > 8192: return
            assert header.split(b"\r\n", 1)[0] == b"CONNECT 8.8.8.8:443 HTTP/1.1"
            state["connects"].append(header.split(b"\r\n", 1)[0].decode())
            with socket.create_connection(("127.0.0.1", server.server_port), timeout=4) as upstream:
                self.request.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                try:
                    while True:
                        ready, _, _ = select.select([self.request, upstream], [], [], 4)
                        if not ready: return
                        for source in ready:
                            block = source.recv(16384)
                            if not block: return
                            (upstream if source is self.request else self.request).sendall(block)
                except (OSError, TimeoutError): pass
    proxy = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Proxy); proxy.daemon_threads = True
    threads = [threading.Thread(target=srv.serve_forever, daemon=True) for srv in (server, proxy)]
    for thread in threads: thread.start()
    trust = tmp_path / "trust.pem"; original = ssl.get_default_verify_paths().cafile
    trust.write_bytes((Path(original).read_bytes() if original else b"") + b"\n" + cert.read_bytes())
    monkeypatch.setenv("SSL_CERT_FILE", str(trust))
    monkeypatch.setattr(transport.pinned, "getproxies", lambda: {"https": "http://127.0.0.1:" + str(proxy.server_address[1])})
    monkeypatch.setattr(transport.pinned, "public_addresses", lambda host, timeout=5: ["8.8.8.8"])
    try: yield state
    finally:
        for srv in (proxy, server): srv.shutdown(); srv.server_close()
        for thread in threads: thread.join(4)


@pytest.mark.parametrize("provider", CONFIGS)
def test_four_providers_browse_and_download_actual_pinned_verified_https(https, provider):
    config = CONFIGS[provider]
    page = providers.browse(provider, config, CREDENTIALS, None)
    assert len(page["items"]) == 1 and page["cursor"] is None
    selected = page["items"][0]
    content = providers.read_file(provider, config, CREDENTIALS, selected["id"], selected["parent_id"], selected["etag"])
    assert content["content"] == https["bytes"] and content["name"] == "notes.txt"
    assert https["connects"] and all(row == "CONNECT 8.8.8.8:443 HTTP/1.1" for row in https["connects"])
    for row in https["requests"]:
        if row["host"] in {"files.fixture.1drv.com", "dl.fixture.boxcloud.com"}: assert "Authorization" not in row["headers"]


@pytest.mark.parametrize("provider", CONFIGS)
@pytest.mark.parametrize("mode", ["race", "outside", "revoked", "content_redirect"])
def test_four_providers_refuse_changed_moved_revoked_and_redirecting_bytes(https, provider, mode):
    selected = providers.browse(provider, CONFIGS[provider], CREDENTIALS, None)["items"][0]
    https["mode"] = mode
    with pytest.raises(providers.ProviderError) as error:
        providers.read_file(provider, CONFIGS[provider], CREDENTIALS, selected["id"], selected["parent_id"], selected["etag"])
    assert error.value.code in {"conflict", "outside_collection", "reauthorization_required", "redirect_not_allowed"}
    assert "private-provider-secret" not in str(error.value)


def test_box_content_redirect_never_sends_credentials_or_visits_unapproved_host(https):
    selected = providers.browse("box", CONFIGS["box"], CREDENTIALS, None)["items"][0]
    https["mode"] = "redirect_escape"
    with pytest.raises(providers.ProviderError) as error:
        providers.read_file("box", CONFIGS["box"], CREDENTIALS, selected["id"], selected["parent_id"], selected["etag"])
    assert error.value.code == "unsafe_destination"
    assert not any(row["host"] not in {"api.box.com"} for row in https["requests"])


def test_transport_redirect_default_rejection_explicit_bounded_return_and_no_follow(https):
    target = "https://api.box.com/redirect-test"
    with pytest.raises(transport.ProviderError) as error:
        transport.request("GET", target, headers={"Authorization": "Bearer private-fixture-bearer"})
    assert error.value.code == "redirect_not_allowed"
    response = transport.request("GET", target, headers={"Authorization": "Bearer private-fixture-bearer"}, allow_redirect_location=True)
    assert response.status == 302 and response.body == b"" and response.headers["location"] == "https://dl.fixture.boxcloud.com/content"
    assert len(https["requests"]) == 2 and not any(row["host"] == "dl.fixture.boxcloud.com" for row in https["requests"])
    https["mode"] = "oversized"
    with pytest.raises(transport.ProviderError) as error:
        transport.request("GET", target, headers={"Authorization": "Bearer private-fixture-bearer"}, allow_redirect_location=True)
    assert error.value.code == "invalid_response"


@pytest.mark.parametrize("provider", ["dropbox", "box"])
def test_customer_oauth_exchange_and_refresh_actual_pinned_tls_with_rotating_secrets(https, provider):
    config={"client_id":"customer-app","client_secret":"private-customer-secret","folder_id":"10" if provider=="box" else "/chosen"}
    token=providers.exchange_code(provider,config,"fixture-code","private-verifier","https://collab.example.com/api/cloud-files/oauth/callback")
    assert token["access_token"]=="private-new-access" and token["refresh_token"]=="private-new-refresh"
    refreshed=providers.refresh_tokens(provider,config,"private-refresh")
    assert refreshed["access_token"]=="private-new-access"
    assert all("private-" not in row["path"] for row in https["requests"])
    assert all("Authorization" not in row["headers"] for row in https["requests"])
