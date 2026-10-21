"""Real local TLS/process/file proofs for deployment helpers, never a deployment."""
import base64
from contextlib import contextmanager
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import sqlite3
import ssl
import subprocess
import sys
from threading import Thread
import time
from types import SimpleNamespace
from urllib.request import HTTPSHandler, ProxyHandler, build_opener

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(name, relative):
    spec = spec_from_file_location(name, ROOT / relative)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


preflight = load("release_preflight", "scripts/pilot-preflight.py")
runtime = load("release_runtime", "backend/railway/runtime.py")
backup = load("release_backup", "deploy/pilot/backup.py")


@pytest.fixture
def tls_site(tmp_path):
    key, cert = tmp_path / "tls.key", tmp_path / "tls.crt"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-keyout", str(key), "-out", str(cert),
                    "-subj", "/CN=release.freecollab.example", "-addext", "subjectAltName=DNS:release.freecollab.example"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    routes = {
        "/api/health": (200, {"status": "ok", "version": "0.9.0"}),
        "/api/ready": (200, {"status": "ready"}),
        "/api/capabilities": (200, {"version": "0.9.0", "integrations": {"connection_setup": True}}),
        "/api/operations/metrics": (200, "".join("freecollab_" + name + " 1\n" for name in ("mail_configured", "mail_worker_healthy", "integrations_enabled", "integrations_worker_healthy"))),
    }
    seen, connected = [], []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.command, self.path, self.headers.get("Authorization")))
            status, body = routes[self.path]
            self.send_response(status)
            self.send_header("Content-Type", "application/json" if isinstance(body, dict) else "text/plain")
            self.send_header("Cache-Control", "private, no-store")
            if status == 302:
                self.send_header("Location", "https://other.example/never-follow")
            self.end_headers()
            payload = json.dumps(body).encode() if isinstance(body, dict) else body.encode()
            try:
                if body == "slow":
                    for _ in range(20):
                        self.wfile.write(b" "); self.wfile.flush(); time.sleep(.05)
                else:
                    self.wfile.write(payload)
            except OSError:
                pass

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert, key)
    server.socket = server_context.wrap_socket(server.socket, server_side=True)
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()

    def opener(context):
        class MappedConnection(http.client.HTTPSConnection):
            def connect(self):
                connected.append(self.host)
                plain = socket.create_connection(("127.0.0.1", server.server_port), timeout=self.timeout)
                self.sock = self._context.wrap_socket(plain, server_hostname=self.host)

        class MappedHandler(HTTPSHandler):
            def https_open(self, request):
                return self.do_open(MappedConnection, request, context=context)

        return build_opener(ProxyHandler({}), preflight.NoRedirects(), MappedHandler())

    context = ssl.create_default_context(cafile=str(cert))
    site = SimpleNamespace(origin="https://release.freecollab.example", context=context, opener=opener(context),
                           make_opener=opener, routes=routes, seen=seen, connected=connected)
    try:
        yield site
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)


def check(site, **kwargs):
    return preflight.check_site(site.origin, context=site.context, opener=site.opener, resolver=lambda *_: None, **kwargs)


def test_passive_real_tls_exact_gets_scoped_header_and_safe_summary(tls_site):
    token = "private-metrics-" + secrets.token_hex(24)
    result = check(tls_site, token=token, expect_version="0.9.0", required=["integrations.connection_setup"])
    assert result["ok"] and len(result["checks"]) == 4
    assert [row[:2] for row in tls_site.seen] == [("GET", path) for path in tls_site.routes]
    assert [row[2] for row in tls_site.seen] == [None, None, None, "Bearer " + token]
    assert token not in json.dumps(result) and "freecollab_mail" not in json.dumps(result)


def test_redirect_never_forwards_metrics_credential(tls_site):
    tls_site.routes["/api/operations/metrics"] = (302, "private remote error should not escape")
    with pytest.raises(preflight.CheckError, match="redirect_refused"):
        check(tls_site, token="secret-" + "a" * 40)
    assert set(tls_site.connected) == {"release.freecollab.example"}


def test_untrusted_certificate_rejected_before_http_or_credential(tls_site):
    tls_site.opener = tls_site.make_opener(ssl.create_default_context())
    with pytest.raises(preflight.CheckError, match="verified_https_request_failed"):
        check(tls_site, token="secret-" + "a" * 40)
    assert tls_site.seen == []


def test_slow_trickled_body_has_absolute_deadline(tls_site):
    tls_site.routes["/api/health"] = (200, "slow")
    started = time.monotonic()
    with pytest.raises(preflight.CheckError, match="https_deadline_exceeded"):
        check(tls_site, timeout=.2)
    assert time.monotonic() - started < .8


def test_oversized_body_and_false_capability_are_rejected(tls_site):
    tls_site.routes["/api/capabilities"] = (200, "x" * (preflight.MAX_RESPONSE + 1))
    with pytest.raises(preflight.CheckError, match="response_too_large"):
        check(tls_site)
    tls_site.routes["/api/capabilities"] = (200, {"version": "0.9.0", "secure_messaging": False})
    with pytest.raises(preflight.CheckError, match="required_capability_unavailable"):
        check(tls_site, required=["secure_messaging"])


@pytest.mark.parametrize("origin", ["http://release.example", "https://127.0.0.1", "https://[::1]", "https://localhost", "https://app.internal", "https://u:p@release.example", "https://release.example/api", "https://release.example?token=private", "https://release.example:8443"])
def test_nonpublic_or_credentialed_origin_rejected(origin):
    with pytest.raises(preflight.CheckError):
        preflight.public_origin(origin)


def test_mixed_private_dns_and_unverified_context_fail_closed(monkeypatch):
    monkeypatch.setattr(preflight.subprocess, "run", lambda *_, **__: SimpleNamespace(stdout=b'["8.8.8.8","127.0.0.1"]'))
    with pytest.raises(preflight.CheckError, match="public_dns_check_failed"):
        preflight.resolve_public("release.example", 1)
    with pytest.raises(preflight.CheckError, match="certificate_and_hostname_verification_required"):
        preflight.check_site("https://release.example", resolver=lambda *_: None, context=ssl._create_unverified_context())


def test_metrics_token_requires_private_regular_owned_nonsymlink_file(tmp_path):
    token = tmp_path / "token"; token.write_text("a" * 64 + "\n"); token.chmod(0o600)
    assert preflight.private_token(token) == "a" * 64
    token.chmod(0o644)
    with pytest.raises(preflight.CheckError): preflight.private_token(token)
    token.chmod(0o600)
    link = tmp_path / "link"; link.symlink_to(token)
    with pytest.raises(preflight.CheckError): preflight.private_token(link)
    fifo = tmp_path / "fifo"; os.mkfifo(fifo, 0o600)
    with pytest.raises(preflight.CheckError): preflight.private_token(fifo)


def unused_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); return sock.getsockname()[1]


def wait_for(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate(): return
        time.sleep(.05)
    raise AssertionError("Private process fixture did not reach expected state")


def alive(pid):
    try: os.kill(pid, 0); return True
    except ProcessLookupError: return False


def supervisor_fixture(tmp_path, *, exit_worker=False, startup_timeout=5):
    port = unused_port()
    api_pid, worker_pid = tmp_path / "api.pid", tmp_path / "worker.pid"
    api_code = "from http.server import BaseHTTPRequestHandler,HTTPServer;import os;from pathlib import Path;Path(" + repr(str(api_pid)) + ").write_text(str(os.getpid()))\nclass H(BaseHTTPRequestHandler):\n def do_GET(self):\n  self.send_response(200);self.end_headers();self.wfile.write(b'{\"status\":\"ready\"}')\n def log_message(self,*a):pass\nHTTPServer(('127.0.0.1'," + str(port) + "),H).serve_forever()"
    worker_code = "import os,time;from pathlib import Path;Path(" + repr(str(worker_pid)) + ").write_text(str(os.getpid()));time.sleep(" + (".3" if exit_worker else "60") + ")"
    code = "import importlib.util;spec=importlib.util.spec_from_file_location('runtime'," + repr(str(ROOT / "backend/railway/runtime.py")) + ");m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);raise SystemExit(m.supervise(" + repr([sys.executable, "-c", api_code]) + ", [" + repr([sys.executable, "-c", worker_code]) + "],ready_url='http://127.0.0.1:" + str(port) + "/ready',startup_timeout=" + str(startup_timeout) + ",stop_timeout=.5))"
    process = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return process, api_pid, worker_pid


def test_supervisor_reaps_api_and_worker_when_worker_exits(tmp_path):
    process, api_pid, worker_pid = supervisor_fixture(tmp_path, exit_worker=True)
    try:
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 1 and b"runtime unavailable" in stderr
        assert not alive(int(api_pid.read_text())) and not alive(int(worker_pid.read_text()))
    finally:
        if process.poll() is None: process.terminate(); process.wait(timeout=5)


def test_supervisor_sigterm_drains_and_reaps_all_children(tmp_path):
    process, api_pid, worker_pid = supervisor_fixture(tmp_path)
    try:
        wait_for(worker_pid.exists)
        process.terminate(); process.communicate(timeout=10)
        assert process.returncode == 0
        assert not alive(int(api_pid.read_text())) and not alive(int(worker_pid.read_text()))
    finally:
        if process.poll() is None: process.terminate(); process.wait(timeout=5)


def test_api_startup_deadline_never_starts_workers_and_reaps_api(tmp_path):
    marker = tmp_path / "unexpected-worker"
    started = time.monotonic()
    result = runtime.supervise([sys.executable, "-c", "import time;time.sleep(60)"],
        [[sys.executable, "-c", "from pathlib import Path;Path(" + repr(str(marker)) + ").touch()"]],
        ready_url=f"http://127.0.0.1:{unused_port()}/ready", startup_timeout=.3, stop_timeout=.2)
    assert result == 1 and not marker.exists() and time.monotonic() - started < 2


def test_storage_initialization_touches_only_four_known_directory_inodes(tmp_path, monkeypatch):
    data = tmp_path / "data"; data.mkdir()
    nested = data / "existing-private-file"; nested.write_bytes(b"preserve original bytes"); nested.chmod(0o640)
    original = os.fstat
    changed, dropped = [], []
    monkeypatch.setattr(runtime.os, "geteuid", lambda: 0)
    monkeypatch.setattr(runtime.os, "fstat", lambda fd: SimpleNamespace(st_mode=original(fd).st_mode, st_uid=0))
    monkeypatch.setattr(runtime.os, "fchown", lambda fd, uid, gid: changed.append((original(fd).st_ino, uid, gid)))
    monkeypatch.setattr(runtime.os, "setgroups", lambda groups: dropped.append(("groups", groups)))
    monkeypatch.setattr(runtime.os, "setgid", lambda gid: dropped.append(("gid", gid)))
    monkeypatch.setattr(runtime.os, "setuid", lambda uid: dropped.append(("uid", uid)))
    runtime.prepare_storage(data)
    assert {row[0] for row in changed} == {path.stat().st_ino for path in (data, data / "files", data / "mail", data / "security")}
    assert dropped == [("groups", []), ("gid", 10001), ("uid", 10001)]
    assert nested.read_bytes() == b"preserve original bytes" and nested.stat().st_mode & 0o777 == 0o640


def test_storage_refuses_symlink_and_unknown_existing_owner(tmp_path, monkeypatch):
    data = tmp_path / "data"; data.mkdir(); outside = tmp_path / "outside"; outside.mkdir()
    (data / "files").symlink_to(outside, target_is_directory=True)
    original = os.fstat
    monkeypatch.setattr(runtime.os, "geteuid", lambda: 0)
    monkeypatch.setattr(runtime.os, "fstat", lambda fd: SimpleNamespace(st_mode=original(fd).st_mode, st_uid=10001))
    with pytest.raises(OSError): runtime.prepare_storage(data)
    assert list(outside.iterdir()) == []
    monkeypatch.setattr(runtime.os, "fstat", lambda fd: SimpleNamespace(st_mode=original(fd).st_mode, st_uid=20002))
    with pytest.raises(ValueError, match="ownership"): runtime.prepare_storage(data)


def test_real_railway_runtime_starts_existing_workers_on_private_database(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("Real runtime root init intentionally targets only its mounted /data")
    port = unused_port()
    env = os.environ.copy()
    env.update(ENVIRONMENT="test", DATABASE_URL="sqlite:///" + str(tmp_path / "runtime.db"), STORAGE_PATH=str(tmp_path / "files"),
               JWT_SECRET=secrets.token_hex(32), ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(),
               ACCOUNT_SECURITY_KEY_FILE=str(tmp_path / "account.key"), MESSAGE_ENCRYPTION_KEYS=json.dumps({"test": base64.b64encode(secrets.token_bytes(32)).decode()}),
               MESSAGE_ENCRYPTION_ACTIVE_KEY_ID="test", MESSAGE_ENCRYPTION_KEY_FILE=str(tmp_path / "content.key"),
               MAIL_DELIVERY_MODE="disabled", MAIL_FILE_PATH=str(tmp_path / "mail"), MATRIX_ENABLED="false", OIDC_ENABLED="false",
               WEBHOOKS_ENABLED="false", SYNC_ENABLED="false", OTRUST_ENABLED="false", PUSH_ENABLED="false", CLOUD_FILES_ENABLED="false",
               EXTERNAL_MEETINGS_ENABLED="false", COPILOT_ENABLED="false", PORT=str(port), FORWARDED_ALLOW_IPS="127.0.0.1,::1", PYTHONPATH=str(ROOT / "backend"))
    log = (tmp_path / "runtime.log").open("w")
    (tmp_path / "runtime.log").chmod(0o600)
    process = subprocess.Popen([sys.executable, str(ROOT / "backend/railway/runtime.py")], env=env, cwd=ROOT / "backend", stdout=log, stderr=subprocess.STDOUT)
    children = []
    try:
        def both_heartbeats():
            assert process.poll() is None, "Private runtime exited"
            try:
                with sqlite3.connect(tmp_path / "runtime.db") as db:
                    return {row[0] for row in db.execute("select name from workerheartbeat")} >= {"mail", "integrations"}
            except sqlite3.OperationalError: return False
        wait_for(both_heartbeats, timeout=25)
        assert process.poll() is None, "Private runtime lost a child; inspect its owned fixture log"
        # Some managed kernels omit /proc/.../children (CHECKPOINT_RESTORE).
        children = [int(value) for value in subprocess.run(["ps", "--ppid", str(process.pid), "-o", "pid="], check=True, capture_output=True, text=True).stdout.split()]
        assert len(children) == 3
        process.terminate(); process.wait(timeout=35)
        assert process.returncode == 0 and all(not alive(pid) for pid in children)
    finally:
        if process.poll() is None: process.terminate(); process.wait(timeout=35)
        log.close()


@pytest.mark.parametrize("field,value", [("PORT", "80"), ("PORT", "8080\nreturn 200"), ("FREECOLLAB_PUBLIC_HOST", "valid.example\ninclude private;"), ("FREECOLLAB_BACKEND_HOST", "public.example"), ("FREECOLLAB_TRUSTED_EDGE_CIDR", "0.0.0.0/0"), ("FREECOLLAB_TRUSTED_EDGE_CIDR", "::/0"), ("FREECOLLAB_TRUSTED_EDGE_CIDR", "0:0:0:0:0:0:0:0/0"), ("FREECOLLAB_TRUSTED_EDGE_CIDR", "0.0.0.0/00"), ("FREECOLLAB_TRUSTED_EDGE_CIDR", "127.0.0.1/32\ninclude x;")])
def test_frontend_template_injection_and_unsafe_trust_rejected(field, value):
    env = {**os.environ, "PORT": "8080", "FREECOLLAB_PUBLIC_HOST": "release.example", "FREECOLLAB_BACKEND_HOST": "backend.railway.internal", "FREECOLLAB_TRUSTED_EDGE_CIDR": "127.0.0.1/32", field: value}
    result = subprocess.run(["sh", str(ROOT / "frontend/railway/19-validate-routing.sh")], env=env, capture_output=True)
    assert result.returncode == 1 and value not in result.stderr.decode()


def test_valid_frontend_private_routing_configuration():
    env = {**os.environ, "PORT": "8080", "FREECOLLAB_PUBLIC_HOST": "release.example", "FREECOLLAB_BACKEND_HOST": "backend.railway.internal", "FREECOLLAB_TRUSTED_EDGE_CIDR": "fd12:3456::/64"}
    subprocess.run(["sh", str(ROOT / "frontend/railway/19-validate-routing.sh")], env=env, check=True, capture_output=True)
