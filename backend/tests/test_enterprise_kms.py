"""Vault Transit envelope encryption through verified, pinned HTTPS protocol."""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import ssl
import subprocess
from threading import Thread

from nacl import secret, utils
from nacl.exceptions import CryptoError
from pydantic import SecretStr
import pytest

from app.config import settings
from app.services import enterprise_kms, remote_transport
from app.services.crypto import decrypt_message, encrypt_message


@pytest.fixture
def vault(tmp_path, monkeypatch):
    key, certificate = tmp_path / "key.pem", tmp_path / "cert.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(certificate),
        "-days", "1", "-subj", "/CN=kms.example.com", "-addext", "subjectAltName=DNS:kms.example.com"],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    observed = {"requests": [], "version": 1, "keys": {1: utils.random(32)}, "credential": "fixture-private-vault-token", "status": 200}
    class Transit(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            document = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            observed["requests"].append({"path": self.path, "body": document, "host": self.headers.get("Host"), "namespace": self.headers.get("X-Vault-Namespace")})
            if self.headers.get("X-Vault-Token") != observed["credential"]:
                self.send_response(403); self.send_header("Content-Length", "0"); self.end_headers(); return
            if observed["status"] != 200:
                self.send_response(observed["status"]); self.send_header("Location", "https://127.0.0.1/secret"); self.send_header("Content-Length", "0"); self.end_headers(); return
            if self.path == "/v1/transit/encrypt/native-content":
                version = observed["version"]
                wrapped = secret.SecretBox(observed["keys"][version]).encrypt(base64.b64decode(document["plaintext"], validate=True))
                data = {"ciphertext": "vault:v" + str(version) + ":" + base64.b64encode(wrapped).decode()}
            elif self.path == "/v1/transit/decrypt/native-content":
                _, version, wrapped = document["ciphertext"].split(":", 2)
                plain = secret.SecretBox(observed["keys"][int(version[1:])]).decrypt(base64.b64decode(wrapped, validate=True))
                data = {"plaintext": base64.b64encode(plain).decode()}
            else:
                self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers(); return
            body = json.dumps({"data": data}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Transit)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); tls.load_cert_chain(certificate, key)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    worker = Thread(target=server.serve_forever, daemon=True); worker.start()
    monkeypatch.setenv("SSL_CERT_FILE", str(certificate))
    monkeypatch.setattr(remote_transport.pinned, "getproxies", lambda: {})
    monkeypatch.setattr(remote_transport.pinned, "public_addresses", lambda host, timeout=5: ["8.8.8.8"])
    original = remote_transport.pinned._PinnedBackend.connect_tcp
    def fixture_connection(self, host, port, timeout=None, local_address=None, socket_options=None):
        return original(self, "127.0.0.1", server.server_port, timeout, local_address, socket_options)
    monkeypatch.setattr(remote_transport.pinned._PinnedBackend, "connect_tcp", fixture_connection)
    monkeypatch.setattr(settings, "enterprise_vault_url", "https://kms.example.com")
    monkeypatch.setattr(settings, "enterprise_vault_token", SecretStr("fixture-private-vault-token"))
    monkeypatch.setattr(settings, "enterprise_vault_mount", "transit")
    monkeypatch.setattr(settings, "enterprise_vault_key", "native-content")
    monkeypatch.setattr(settings, "enterprise_vault_namespace", "customer-eu")
    monkeypatch.setattr(settings, "message_encryption_keys", {"retained": SecretStr(base64.b64encode(b"\x9c" * 32).decode())})
    monkeypatch.setattr(settings, "message_encryption_active_key_id", "retained")
    yield observed
    server.shutdown(); server.server_close(); worker.join(3)


def test_real_tls_vault_envelope_never_sends_message_plaintext_and_authenticates_channel(vault):
    encrypted = encrypt_message("Strictly private customer message", "native-a")
    assert encrypted.startswith("fc2:vault:") and "Strictly" not in encrypted
    assert decrypt_message(encrypted, "native-a") == "Strictly private customer message"
    with pytest.raises(CryptoError): decrypt_message(encrypted, "native-b")
    assert len(vault["requests"]) == 3
    assert all(call["host"] == "kms.example.com" and call["namespace"] == "customer-eu" for call in vault["requests"])
    assert "Strictly private customer message" not in json.dumps(vault["requests"])
    data_key = base64.b64decode(vault["requests"][0]["body"]["plaintext"])
    assert len(data_key) == 32


def test_vault_retains_old_provider_versions_and_uses_fresh_data_keys(vault):
    first = encrypt_message("First version", "native")
    vault["version"] = 2; vault["keys"][2] = utils.random(32)
    second = encrypt_message("Second version", "native")
    assert decrypt_message(first, "native") == "First version"
    assert decrypt_message(second, "native") == "Second version"
    assert vault["requests"][0]["body"]["plaintext"] != vault["requests"][1]["body"]["plaintext"]


def test_revoked_vault_credential_immediately_prevents_reads_no_key_cache_or_local_fallback(vault):
    encrypted = encrypt_message("Preserved private content", "native")
    assert decrypt_message(encrypted, "native") == "Preserved private content"
    vault["credential"] = "revoked-by-customer"
    for operation in (lambda: decrypt_message(encrypted, "native"), lambda: encrypt_message("new", "native")):
        with pytest.raises(CryptoError) as failure: operation()
        assert "private-vault-token" not in str(failure.value)


def test_switching_provider_reference_never_sends_wrapped_keys_to_another_provider(vault, monkeypatch):
    encrypted = encrypt_message("Private", "native")
    monkeypatch.setattr(settings, "enterprise_vault_key", "different-key")
    before = len(vault["requests"])
    with pytest.raises(CryptoError): decrypt_message(encrypted, "native")
    assert len(vault["requests"]) == before


@pytest.mark.parametrize("url", ["http://kms.example.com", "https://127.0.0.1", "https://kms.example.com:8443", "https://metadata.google.internal", "https://private:token@kms.example.com"])
def test_private_or_unsafe_vault_destinations_fail_closed_before_transport(vault, monkeypatch, url):
    monkeypatch.setattr(settings, "enterprise_vault_url", url)
    with pytest.raises(CryptoError): encrypt_message("Private", "native")
    assert vault["requests"] == []


def test_tls_hostname_is_verified_and_redirects_never_followed(vault, monkeypatch):
    monkeypatch.setattr(settings, "enterprise_vault_url", "https://wrong-name.example.com")
    with pytest.raises(CryptoError): encrypt_message("Private", "native")
    assert vault["requests"] == []
    monkeypatch.setattr(settings, "enterprise_vault_url", "https://kms.example.com")
    vault["status"] = 302
    with pytest.raises(CryptoError): encrypt_message("Private", "native")
    assert len(vault["requests"]) == 1


def test_unconfigured_vault_never_falls_back_and_older_local_envelopes_remain_readable(vault, monkeypatch):
    monkeypatch.setattr(settings, "enterprise_vault_url", "")
    legacy = encrypt_message("Retained local key", "native")
    assert legacy.startswith("fc1:retained:")
    monkeypatch.setattr(settings, "enterprise_vault_url", "https://kms.example.com")
    monkeypatch.setattr(settings, "enterprise_vault_token", None)
    with pytest.raises(CryptoError): encrypt_message("Must not fall back", "native")
    assert decrypt_message(legacy, "native") == "Retained local key"
