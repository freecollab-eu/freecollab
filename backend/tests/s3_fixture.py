"""Isolated HTTPS S3 protocol server that verifies actual SigV4 requests."""
import base64
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import re
import ssl
import threading
from types import SimpleNamespace
from urllib.parse import parse_qsl, quote, unquote, urlsplit
from xml.sax.saxutils import escape

import pytest
from pydantic import SecretStr

from app.config import Settings
from app.services.storage import S3StorageService
from tests.oidc_mock import tls_fixture

ACCESS = "isolated-fixture-access"
SECRET = "isolated-fixture-secret-never-production"


@pytest.fixture
def s3(tmp_path, monkeypatch):
    certificate, key = tls_fixture(tmp_path / "s3-tls")
    state = SimpleNamespace(objects={}, requests=[], locked=False, corrupt=False)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *args):
            pass

        def reply(self, status=200, body=b"", headers=None):
            self.send_response(status)
            for name, value in (headers or {}).items():
                self.send_header(name, str(value))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def error(self, code, status):
            self.reply(status, ("<Error><Code>" + code + "</Code><Message>Isolated protocol fixture</Message></Error>").encode(),
                {"Content-Type": "application/xml"})

        def signed(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            value = self.headers.get("Authorization", "")
            match = re.fullmatch(r"AWS4-HMAC-SHA256 Credential=([^/]+)/([^,]+), SignedHeaders=([^,]+), Signature=([0-9a-f]{64})", value)
            if not match or match[1] != ACCESS:
                self.error("SignatureDoesNotMatch", 403); return None
            credential, scope, names, signature = match.groups()
            date, region, service, end = scope.split("/")
            if region != "us-east-1" or service != "s3" or end != "aws4_request":
                self.error("SignatureDoesNotMatch", 403); return None
            parsed = urlsplit(self.path)
            headers = "".join(name + ":" + " ".join(self.headers[name].split()) + "\n" for name in names.split(";"))
            query = "&".join(quote(name, safe="-_.~") + "=" + quote(value, safe="-_.~")
                             for name, value in sorted(parse_qsl(parsed.query, keep_blank_values=True)))
            payload = self.headers.get("x-amz-content-sha256", hashlib.sha256(body).hexdigest())
            canonical = "\n".join((self.command, quote(unquote(parsed.path), safe="/-_.~"), query,
                                    headers, names, payload))
            to_sign = "\n".join(("AWS4-HMAC-SHA256", self.headers["x-amz-date"], scope,
                                  hashlib.sha256(canonical.encode()).hexdigest()))
            signing = ("AWS4" + SECRET).encode()
            for part in (date, region, service, end):
                signing = hmac.new(signing, part.encode(), hashlib.sha256).digest()
            actual = hmac.new(signing, to_sign.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(actual, signature):
                self.error("SignatureDoesNotMatch", 403); return None
            if payload not in {"UNSIGNED-PAYLOAD", hashlib.sha256(body).hexdigest()}:
                self.error("XAmzContentSHA256Mismatch", 400); return None
            state.requests.append((self.command, parsed.path, dict(parse_qsl(parsed.query, keep_blank_values=True))))
            return parsed, body

        def do_PUT(self):
            signed = self.signed()
            if signed is None: return
            parsed, body = signed
            key = unquote(parsed.path)
            if self.headers.get("If-None-Match") != "*":
                return self.error("InvalidRequest", 400)
            versions = state.objects.setdefault(key, [])
            if versions and not versions[-1].get("deleted"):
                return self.error("PreconditionFailed", 412)
            checksum = base64.b64encode(hashlib.sha256(body).digest()).decode()
            if self.headers.get("x-amz-checksum-sha256") != checksum:
                return self.error("BadDigest", 400)
            version = {"id": "v" + str(len(versions) + 1), "body": body,
                       "digest": self.headers.get("x-amz-meta-sha256", "")}
            versions.append(version)
            self.reply(200, headers={"ETag": '"' + hashlib.md5(body).hexdigest() + '"',
                "x-amz-checksum-sha256": checksum, "x-amz-version-id": version["id"]})

        def do_GET(self):
            signed = self.signed()
            if signed is None: return
            parsed, _ = signed
            query = dict(parse_qsl(parsed.query, keep_blank_values=True))
            if "versions" in query:
                prefix = query.get("prefix", "")
                bucket_path = parsed.path.rstrip("/") + "/"
                items = []
                for path, versions in state.objects.items():
                    key = path.removeprefix(bucket_path)
                    if not key.startswith(prefix): continue
                    for row in versions:
                        tag = "DeleteMarker" if row.get("deleted") else "Version"
                        items.append("<" + tag + "><Key>" + escape(key) + "</Key><VersionId>" + row["id"]
                            + "</VersionId><IsLatest>true</IsLatest></" + tag + ">")
                return self.reply(200, ('<ListVersionsResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><IsTruncated>false</IsTruncated>'
                    + "".join(items) + "</ListVersionsResult>").encode(), {"Content-Type": "application/xml"})
            versions = state.objects.get(unquote(parsed.path), [])
            if not versions or versions[-1].get("deleted"):
                return self.error("NoSuchKey", 404)
            row = versions[-1]
            body = row["body"] + b"changed" if state.corrupt else row["body"]
            self.reply(200, body, {"x-amz-meta-sha256": row["digest"],
                "x-amz-checksum-sha256": base64.b64encode(hashlib.sha256(body).digest()).decode()})

        def do_HEAD(self):
            signed = self.signed()
            if signed is None: return
            parsed, _ = signed
            versions = state.objects.get(unquote(parsed.path), [])
            if not versions or versions[-1].get("deleted"):
                return self.reply(404)
            self.reply(200, versions[-1]["body"])

        def do_DELETE(self):
            signed = self.signed()
            if signed is None: return
            parsed, _ = signed
            if state.locked:
                return self.error("AccessDenied", 403)
            key = unquote(parsed.path)
            versions = state.objects.get(key, [])
            version = dict(parse_qsl(parsed.query)).get("versionId")
            if version:
                state.objects[key] = [row for row in versions if row["id"] != version]
            else:
                versions.append({"id": "v" + str(len(versions) + 1), "deleted": True})
            self.reply(204)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = Settings(_env_file=None, environment="test", storage_backend="s3",
        s3_endpoint_url="https://localhost:" + str(server.server_port), s3_bucket="freecollab-fixture",
        s3_access_key_id=SecretStr(ACCESS), s3_secret_access_key=SecretStr(SECRET), s3_ca_bundle=certificate)
    store = S3StorageService(config)
    # Route only this isolated localhost endpoint directly; all other inherited
    # network/proxy settings remain intact.
    existing_no_proxy = __import__("os").environ.get("NO_PROXY", "")
    monkeypatch.setenv("NO_PROXY", existing_no_proxy + ",localhost,127.0.0.1")
    try:
        yield SimpleNamespace(store=store, state=state, config=config)
    finally:
        if store._client is not None: store._client.close()
        server.shutdown(); server.server_close(); thread.join(timeout=5)
