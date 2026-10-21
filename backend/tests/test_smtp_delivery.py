"""Real loopback SMTP/TLS delivery using an isolated ephemeral test CA.

No outside network connections or external email delivery occur in this suite.
"""
import base64
from contextlib import contextmanager
from datetime import datetime
from email import policy
from email.parser import BytesParser
import socketserver
import ssl
import subprocess
import threading

from pydantic import SecretStr
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from app.config import settings
from app.models.account_security import MailOutbox
from app.models.user import User  # noqa: F401 -- register referenced table
from app.services import mail


@pytest.fixture
def smtp_cert(tmp_path):
    cert, key = tmp_path / "smtp-cert.pem", tmp_path / "smtp-key.pem"
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
        "-keyout", str(key), "-out", str(cert), "-subj", "/CN=localhost",
        "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
    ], check=True, capture_output=True)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    return context, cert


@contextmanager
def smtp_receiver(server_context, *, implicit=False):
    """Minimal actual SMTP server: TLS, AUTH PLAIN, envelope and DATA."""
    messages, auths, tls_connections = [], [], []

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    class Handler(socketserver.StreamRequestHandler):
        def setup(self):
            if implicit:
                try:
                    self.request = server_context.wrap_socket(self.request, server_side=True)
                except ssl.SSLError:
                    self.request.close()
                    return
            super().setup()

        def finish(self):
            if hasattr(self, "wfile"):
                super().finish()

        def send(self, value):
            self.wfile.write(value)
            self.wfile.flush()

        def handle(self):
            if not hasattr(self, "wfile"):
                return
            secure = implicit
            self.send(b"220 localhost isolated test receiver\r\n")
            while True:
                command = self.rfile.readline(16384)
                if not command:
                    return
                verb = command.split(b" ", 1)[0].upper().strip()
                if verb in (b"EHLO", b"HELO"):
                    self.send(b"250-localhost\r\n250-STARTTLS\r\n250 AUTH PLAIN\r\n")
                elif verb == b"STARTTLS":
                    self.send(b"220 Ready for TLS\r\n")
                    self.rfile.close()
                    self.wfile.close()
                    try:
                        self.connection = self.request = server_context.wrap_socket(self.connection, server_side=True)
                    except ssl.SSLError:
                        return
                    self.rfile = self.connection.makefile("rb")
                    self.wfile = self.connection.makefile("wb")
                    secure = True
                elif verb == b"AUTH":
                    if not secure:
                        self.send(b"538 Encryption required\r\n")
                        continue
                    auths.append(base64.b64decode(command.strip().split(b" ")[2]))
                    self.send(b"235 Authentication successful\r\n")
                elif verb in (b"MAIL", b"RCPT", b"RSET", b"NOOP"):
                    self.send(b"250 OK\r\n")
                elif verb == b"DATA":
                    self.send(b"354 Send data\r\n")
                    data = bytearray()
                    while True:
                        line = self.rfile.readline(65536)
                        if line == b".\r\n":
                            break
                        if not line or len(data) > 100000:
                            return
                        data.extend(line[1:] if line.startswith(b"..") else line)
                    messages.append(bytes(data))
                    tls_connections.append(secure)
                    self.send(b"250 Accepted\r\n")
                elif verb == b"QUIT":
                    self.send(b"221 Bye\r\n")
                    return
                else:
                    self.send(b"502 Unsupported\r\n")

    server = Server(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], messages, auths, tls_connections
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def smtp_outbox(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "environment", "test")
    monkeypatch.setattr(settings, "account_security_key", SecretStr(base64.b64encode(b"\xb4" * 32).decode()))
    monkeypatch.setattr(settings, "mail_delivery_mode", "smtp")
    monkeypatch.setattr(settings, "smtp_host", "127.0.0.1")
    monkeypatch.setattr(settings, "smtp_from", "FreeCollab <sender@example.com>")
    monkeypatch.setattr(settings, "smtp_username", "fixture-user")
    monkeypatch.setattr(settings, "smtp_password", SecretStr("fixture-password"))
    monkeypatch.setattr(settings, "smtp_timeout", 2.0)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        row = mail.enqueue_mail(session, "recipient@example.com", "SMTP integration check", "Exact private diagnostic body\n")
        session.commit()
        identifier = row.id
    yield engine, identifier
    engine.dispose()


@pytest.mark.parametrize("implicit", [False, True], ids=["STARTTLS", "implicit-TLS"])
def test_real_smtp_delivery_verifies_ca_uses_tls_auth_and_commits_ack(smtp_outbox, smtp_cert, monkeypatch, implicit):
    engine, identifier = smtp_outbox
    server_context, cert = smtp_cert
    real_context = ssl.create_default_context
    monkeypatch.setattr(mail.ssl, "create_default_context", lambda: real_context(cafile=str(cert)))
    monkeypatch.setattr(settings, "smtp_implicit_tls", implicit)
    monkeypatch.setattr(settings, "smtp_starttls", not implicit)
    with smtp_receiver(server_context, implicit=implicit) as (port, received, auths, tls):
        monkeypatch.setattr(settings, "smtp_port", port)
        with Session(engine) as session:
            assert mail.deliver_pending(session) == 1
            stored = session.get(MailOutbox, identifier)
            assert stored.status == "delivered" and stored.delivered_at is not None
            assert stored.attempts == 1 and stored.failure is None
            assert mail.deliver_pending(session) == 0
        assert tls == [True] and auths == [b"\x00fixture-user\x00fixture-password"]
        assert len(received) == 1
        message = BytesParser(policy=policy.default).parsebytes(received[0])
        assert message["To"] == "recipient@example.com"
        assert message["From"] == "FreeCollab <sender@example.com>"
        assert message["X-FreeCollab-Outbox-ID"] == identifier
        assert message.get_content().replace("\r\n", "\n") == "Exact private diagnostic body\n"


def test_untrusted_tls_certificate_is_rejected_without_sending_message(smtp_outbox, smtp_cert, monkeypatch):
    engine, identifier = smtp_outbox
    server_context, _ = smtp_cert
    monkeypatch.setattr(settings, "smtp_implicit_tls", False)
    monkeypatch.setattr(settings, "smtp_starttls", True)
    with smtp_receiver(server_context) as (port, received, auths, tls):
        monkeypatch.setattr(settings, "smtp_port", port)
        with Session(engine) as session:
            assert mail.deliver_pending(session) == 0
            row = session.get(MailOutbox, identifier)
            assert row.status == "pending" and row.attempts == 1
            assert row.next_attempt_at > datetime.utcnow()
            assert row.failure == "Delivery failed; inspect server delivery configuration"
        assert received == auths == tls == []


def test_production_smtp_requires_tls_before_connecting(smtp_outbox, monkeypatch):
    engine, identifier = smtp_outbox
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "smtp_implicit_tls", False)
    monkeypatch.setattr(settings, "smtp_starttls", False)
    monkeypatch.setattr(mail.smtplib, "SMTP", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Must not connect without TLS")))
    with Session(engine) as session:
        assert mail.deliver_pending(session) == 0
        assert session.get(MailOutbox, identifier).status == "pending"

