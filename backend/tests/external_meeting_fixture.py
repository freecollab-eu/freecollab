"""Private HTTPS provider protocol fixture, never imported by production code.

The real pinned transport still verifies the original provider hostname and TLS.
Only this test process maps fixed public names to a local CONNECT/TLS fixture.
No real provider account, credential, service or browser login is used.
"""
import base64
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import select
import socket
import socketserver
import ssl
import subprocess
import threading
from urllib.parse import parse_qs, urlsplit

from app.services import external_meeting_providers as protocols, remote_transport as transport

HOSTS = {"accounts.google.com", "oauth2.googleapis.com", "www.googleapis.com", "login.microsoftonline.com", "graph.microsoft.com", "zoom.us", "api.zoom.us"}
SECRET = "private-protocol-fixture-client-secret"


class ProviderFixture:
    def __init__(self, directory):
        self.directory = Path(directory); self.directory.mkdir(parents=True, exist_ok=True)
        self.codes, self.events, self.requests = {}, {}, []
        self.tokens, self.refreshes, self.posts, self.deletes = {}, {}, {}, {}
        self.uncertain_once, self.unauthorized_once = set(), set()
        self.servers, self.threads = [], []

    def issue_code(self, provider, challenge, client_id):
        code = secrets.token_urlsafe(32)
        self.codes[code] = (provider, challenge, client_id)
        return code

    def _send(self, handler, status, data=None):
        body = json.dumps(data or {}).encode() if status != 204 else b""
        handler.send_response(status); handler.send_header("Content-Type", "application/json"); handler.send_header("Content-Length", str(len(body))); handler.end_headers()
        if body: handler.wfile.write(body)

    def handle(self, handler):
        host = handler.headers.get("Host", ""); path = urlsplit(handler.path).path; method = handler.command
        self.requests.append((host, method, path))
        provider = "google_meet" if host in {"oauth2.googleapis.com", "www.googleapis.com"} else "microsoft_teams" if host in {"login.microsoftonline.com", "graph.microsoft.com"} else "zoom"
        body = handler.rfile.read(int(handler.headers.get("Content-Length", 0)))
        is_token = path.endswith("/token") and host in {"oauth2.googleapis.com", "login.microsoftonline.com", "zoom.us"}
        if is_token:
            fields = {key: values[0] for key, values in parse_qs(body.decode()).items()}
            if provider == "zoom":
                try: client_id, secret = base64.b64decode(handler.headers.get("Authorization", "").removeprefix("Basic ")).decode().split(":", 1)
                except (ValueError, UnicodeError): return self._send(handler, 401)
            else: client_id, secret = fields.get("client_id"), fields.get("client_secret")
            if secret != SECRET: return self._send(handler, 401)
            if fields.get("grant_type") == "authorization_code":
                expected = self.codes.pop(fields.get("code"), None)
                challenge = base64.urlsafe_b64encode(hashlib.sha256(fields.get("code_verifier", "").encode()).digest()).rstrip(b"=").decode()
                if not expected or expected != (provider, challenge, client_id): return self._send(handler, 400, {"error": "invalid_grant"})
            elif fields.get("grant_type") == "refresh_token":
                if fields.get("refresh_token") != "fixture-private-refresh-" + provider: return self._send(handler, 400, {"error": "invalid_grant"})
                self.refreshes[provider] = self.refreshes.get(provider, 0) + 1
            else: return self._send(handler, 400)
            token = "fixture-private-access-" + provider
            self.tokens[provider] = token
            scope = "https://www.googleapis.com/auth/calendar.events" if provider == "google_meet" else "Calendars.ReadWrite offline_access" if provider == "microsoft_teams" else " ".join(sorted(protocols.ZOOM_SCOPES))
            return self._send(handler, 200, {"access_token": token, "refresh_token": "fixture-private-refresh-" + provider, "scope": scope, "token_type": "Bearer", "expires_in": 3600})
        if host not in {"www.googleapis.com", "graph.microsoft.com", "api.zoom.us"}: return self._send(handler, 404)
        if handler.headers.get("Authorization") != "Bearer " + self.tokens.get(provider, "invalid"):
            return self._send(handler, 401)
        if provider in self.unauthorized_once:
            self.unauthorized_once.remove(provider); return self._send(handler, 401)
        if method == "GET" and provider == "microsoft_teams" and "/calendars/" in path and "/events" not in path:
            return self._send(handler, 200, {"id": "selected-calendar", "allowedOnlineMeetingProviders": ["teamsForBusiness"], "canEdit": True})
        collection = path.endswith("/events") if provider != "zoom" else path == "/v2/users/me/meetings"
        if collection and method == "GET":
            values = [value.copy() for (kind, _), value in self.events.items() if kind == provider]
            return self._send(handler, 200, {"items" if provider == "google_meet" else "value" if provider == "microsoft_teams" else "meetings": values})
        if collection and method == "POST":
            raw = json.loads(body)
            self.posts[provider] = self.posts.get(provider, 0) + 1
            if provider == "google_meet":
                if raw.get("attendees") or parse_qs(urlsplit(handler.path).query).get("sendUpdates") != ["none"]:
                    return self._send(handler, 422)
                identity = raw["id"]
                if (provider, identity) in self.events: return self._send(handler, 409)
                raw.update(etag='"fixture-etag-1"', status="confirmed", hangoutLink="https://meet.google.com/abc-defg-hij")
                raw["conferenceData"]["createRequest"]["status"] = {"statusCode": "success"}
            elif provider == "microsoft_teams":
                existing = next((value for (kind, _), value in self.events.items() if kind == provider and value.get("transactionId") == raw.get("transactionId")), None)
                if existing: return self._send(handler, 200, existing)
                if raw.get("attendees") or not raw.get("isOnlineMeeting") or raw.get("onlineMeetingProvider") != "teamsForBusiness": return self._send(handler, 422)
                identity = "fixture-graph-" + str(self.posts[provider])
                raw.update(id=identity, **{"@odata.etag": '"fixture-etag-1"'}, onlineMeeting={"joinUrl": "https://teams.microsoft.com/l/meetup-join/fixture?context=fixture"})
            else:
                if raw.get("settings", {}).get("auto_recording") != "none" or not raw.get("settings", {}).get("waiting_room"): return self._send(handler, 422)
                identity = str(12345678900 + self.posts[provider]); raw.update(id=int(identity), join_url="https://customer.zoom.us/j/" + identity + "?pwd=fixture-private-meeting-passcode")
            self.events[(provider, identity)] = raw
            if provider in self.uncertain_once:
                self.uncertain_once.remove(provider); return self._send(handler, 503, {"error": "Fixture writes then loses response"})
            return self._send(handler, 201, raw)
        identity = path.rsplit("/", 1)[-1]
        record = self.events.get((provider, identity))
        if not record: return self._send(handler, 404)
        if method == "GET": return self._send(handler, 200, record)
        if method == "DELETE":
            etag = record.get("etag") or record.get("@odata.etag")
            if etag and handler.headers.get("If-Match") != etag: return self._send(handler, 412)
            self.deletes[provider] = self.deletes.get(provider, 0) + 1; del self.events[(provider, identity)]
            return self._send(handler, 204)
        return self._send(handler, 405)

    def start(self):
        key, cert = self.directory / "provider.key", self.directory / "provider.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert), "-days", "1", "-subj", "/CN=www.googleapis.com", "-addext", "subjectAltName=" + ",".join("DNS:" + host for host in sorted(HOSTS))], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        key.chmod(0o600)
        fixture = self
        class Receiver(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def action(self):
                try: fixture.handle(self)
                except (ValueError, TypeError, KeyError): fixture._send(self, 422)
            do_GET = do_POST = do_DELETE = action
        https = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); tls.load_cert_chain(cert, key); https.socket = tls.wrap_socket(https.socket, server_side=True)
        class Proxy(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.settimeout(5); headers = b""
                while b"\r\n\r\n" not in headers:
                    chunk = self.request.recv(4096)
                    if not chunk or len(headers) > 16384: return
                    headers += chunk
                if headers.split(b"\r\n", 1)[0] != b"CONNECT 8.8.8.8:443 HTTP/1.1": return
                with socket.create_connection(("127.0.0.1", https.server_port), timeout=5) as upstream:
                    self.request.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                    try:
                        while True:
                            ready, _, _ = select.select([self.request, upstream], [], [], 5)
                            if not ready: return
                            for source in ready:
                                block = source.recv(65536)
                                if not block: return
                                (upstream if source is self.request else self.request).sendall(block)
                    except (OSError, TimeoutError): pass
        proxy = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Proxy); proxy.daemon_threads = True
        self.servers = [https, proxy]
        for server in self.servers:
            thread = threading.Thread(target=server.serve_forever, daemon=True); self.threads.append(thread); thread.start()
        self.proxy_url = "http://127.0.0.1:" + str(proxy.server_address[1])
        trust = self.directory / "trust.pem"; original = ssl.get_default_verify_paths().cafile
        trust.write_bytes((Path(original).read_bytes() if original else b"") + b"\n" + cert.read_bytes()); self.trust_file = str(trust)
        return self

    @contextmanager
    def transport(self):
        original_dns, original_proxies = transport.pinned.public_addresses, transport.pinned.getproxies
        original_trust = os.environ.get("SSL_CERT_FILE")
        def addresses(host, timeout=5):
            if host not in HOSTS: raise transport.pinned.TransportError("unsafe_destination")
            return ["8.8.8.8"]
        transport.pinned.public_addresses = addresses
        transport.pinned.getproxies = lambda: {"https": self.proxy_url}
        os.environ["SSL_CERT_FILE"] = self.trust_file
        try: yield self
        finally:
            transport.pinned.public_addresses, transport.pinned.getproxies = original_dns, original_proxies
            if original_trust is None: os.environ.pop("SSL_CERT_FILE", None)
            else: os.environ["SSL_CERT_FILE"] = original_trust

    def close(self):
        for server in reversed(self.servers): server.shutdown(); server.server_close()
        for thread in self.threads: thread.join(5)
