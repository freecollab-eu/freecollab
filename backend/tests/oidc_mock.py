"""Local synthetic OIDC provider for HTTP unit and HTTPS browser proofs only."""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import secrets
import ssl
import threading
from urllib.parse import parse_qs, urlencode, urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import jwt
from jwt.algorithms import RSAAlgorithm


def tls_fixture(directory):
    """Generate an isolated localhost CA/server certificate; never used in production."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "FreeCollab isolated OIDC test")])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .sign(key, hashes.SHA256()))
    cert_path, key_path = directory / "oidc-test-ca.pem", directory / "oidc-test-key.pem"
    with os.fdopen(os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
        stream.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    return str(cert_path), str(key_path)


class LocalProvider:
    def __init__(self, *, cert_path=None, key_path=None,
                 client_id="freecollab-test", client_secret="client-secret"):
        self.client_id, self.client_secret = client_id, client_secret
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.subject, self.email, self.name = "external-subject", "oidc-person@example.com", "OIDC Person"
        self.claim_overrides = {}
        self.signing_key = self.key
        self.header = {"kid": "test-rsa"}
        self.codes = {}
        self.exchange_count = 0
        self.discovery_issuer = None
        self.redirect_endpoint = None
        self.jwks_extra = []
        self.token_signer = None
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, data, status=200):
                body = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                parsed = urlsplit(self.path)
                if parsed.path == "/.well-known/openid-configuration":
                    return self.reply({
                        "issuer": provider.discovery_issuer or provider.issuer,
                        "authorization_endpoint": provider.issuer + "/authorize",
                        "token_endpoint": provider.redirect_endpoint or provider.issuer + "/token",
                        "jwks_uri": provider.issuer + "/jwks",
                        "response_types_supported": ["code"],
                        "code_challenge_methods_supported": ["S256"],
                        "token_endpoint_auth_methods_supported": ["client_secret_basic"],
                    })
                if parsed.path == "/jwks":
                    key = json.loads(RSAAlgorithm.to_jwk(provider.key.public_key()))
                    key.update(kid="test-rsa", use="sig", alg="RS256")
                    return self.reply({"keys": [key, *provider.jwks_extra]})
                if parsed.path == "/authorize":
                    params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                    if params.get("code_challenge_method") != "S256" or params.get("response_type") != "code":
                        return self.reply({"error": "invalid_request"}, 400)
                    code = secrets.token_urlsafe(32)
                    provider.codes[code] = params
                    self.send_response(302)
                    self.send_header("Location", params["redirect_uri"] + "?" + urlencode({"code": code, "state": params["state"]}))
                    self.end_headers()
                    return
                self.reply({"error": "not_found"}, 404)

            def do_POST(self):
                if self.path != "/token":
                    return self.reply({"error": "not_found"}, 404)
                params = {k: v[0] for k, v in parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode()).items()}
                expected = "Basic " + base64.b64encode(f"{provider.client_id}:{provider.client_secret}".encode()).decode()
                if self.headers.get("Authorization") != expected:
                    return self.reply({"error": "invalid_client"}, 401)
                code = provider.codes.pop(params.get("code"), None)
                challenge = base64.urlsafe_b64encode(hashlib.sha256(params.get("code_verifier", "").encode()).digest()).rstrip(b"=").decode()
                if not code or params.get("grant_type") != "authorization_code" or params.get("redirect_uri") != code["redirect_uri"] or challenge != code["code_challenge"]:
                    return self.reply({"error": "invalid_grant"}, 400)
                provider.exchange_count += 1
                now = int(datetime.now(timezone.utc).timestamp())
                claims = {"iss": provider.issuer, "aud": provider.client_id, "sub": provider.subject,
                          "iat": now, "exp": now + 300, "nonce": code["nonce"],
                          "email": provider.email, "email_verified": True, "name": provider.name}
                claims.update(provider.claim_overrides)
                token = provider.token_signer(claims) if provider.token_signer else jwt.encode(claims, provider.signing_key, algorithm="RS256", headers=provider.header)
                self.reply({"id_token": token, "access_token": "unused-provider-access-token", "token_type": "Bearer"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        scheme = "http"
        if cert_path is not None:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert_path, key_path)
            self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
            scheme = "https"
        self.issuer = f"{scheme}://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


