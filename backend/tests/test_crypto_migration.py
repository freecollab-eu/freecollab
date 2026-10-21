"""Authenticate context and migrate historical content without reusing JWT keys."""
import base64
import json
import os

import nacl.secret
from nacl.encoding import Base64Encoder
from nacl.exceptions import CryptoError
import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.config import Settings
from app import crypto_migration
from app.models.channel import Channel
from app.models.user import User
from app.models.message import Message
from app.services import crypto


def encoded(key):
    return base64.b64encode(key).decode()


@pytest.fixture
def crypto_settings(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None, environment="test", jwt_secret="signing-secret-before-rotation",
        message_encryption_keys={"old": encoded(b"o" * 32), "current": encoded(b"c" * 32)},
        message_encryption_active_key_id="current",
        message_encryption_key_file=str(tmp_path / "message-keys.json"),
        legacy_message_encryption_key=encoded(b"l" * 32),
    )
    monkeypatch.setattr(crypto, "settings", settings)
    monkeypatch.setattr(crypto_migration, "settings", settings)
    return settings


@pytest.fixture
def database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(User(id="owner", name="Owner", email="owner@example.com", password_hash="unused"))
        session.add(Channel(id="channel", name="Private"))
        session.commit()
    yield engine
    engine.dispose()


def legacy_encrypt(content):
    return nacl.secret.SecretBox(b"l" * 32).encrypt(content.encode(), encoder=Base64Encoder).decode()


def seed(engine, identifier, content, **kwargs):
    with Session(engine) as session:
        session.add(Message(id=identifier, channel_id="channel", author_id="owner", content=content, **kwargs))
        session.commit()


def stored(engine, identifier):
    with Session(engine) as session:
        return session.get(Message, identifier).content


def test_envelope_roundtrip_uses_active_versioned_key(crypto_settings):
    ciphertext = crypto.encrypt_message("private text", "channel")
    assert ciphertext.startswith("fc1:current:")
    assert crypto.decrypt_message(ciphertext, "channel") == "private text"
    assert "private text" not in ciphertext


def test_message_encryption_is_independent_of_jwt_rotation(crypto_settings):
    ciphertext = crypto.encrypt_message("history", "channel")
    crypto_settings.jwt_secret = "completely-independent-signing-secret"
    assert crypto.decrypt_message(ciphertext, "channel") == "history"


def test_wrong_channel_context_is_rejected(crypto_settings):
    ciphertext = crypto.encrypt_message("private", "channel")
    with pytest.raises(CryptoError, match="context"):
        crypto.decrypt_message(ciphertext, "other-channel")


@pytest.mark.parametrize("mutation", ["fc1:unknown:AAAA", "fc2:current:AAAA", "fc1:current:not-base64", "fc1:current:AAAA"])
def test_unknown_or_tampered_envelope_is_rejected(crypto_settings, mutation):
    with pytest.raises((CryptoError, ValueError)):
        crypto.decrypt_message(mutation, "channel")


def test_legacy_decryption_requires_explicit_key(crypto_settings):
    ciphertext = legacy_encrypt("historical")
    assert crypto.decrypt_message(ciphertext, "channel") == "historical"
    crypto_settings.legacy_message_encryption_key = None
    with pytest.raises(CryptoError, match="Legacy"):
        crypto.decrypt_message(ciphertext, "channel")


def test_retained_old_key_decrypts_after_active_key_rotation(crypto_settings):
    crypto_settings.message_encryption_active_key_id = "old"
    ciphertext = crypto.encrypt_message("history", "channel")
    crypto_settings.message_encryption_active_key_id = "current"
    assert crypto.decrypt_message(ciphertext, "channel") == "history"
    del crypto_settings.message_encryption_keys["old"]
    with pytest.raises(CryptoError, match="Unknown"):
        crypto.decrypt_message(ciphertext, "channel")


def test_development_key_persists_independently_and_is_private(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, environment="test", message_encryption_key_file=str(tmp_path / "private" / "keys.json"))
    monkeypatch.setattr(crypto, "settings", settings)
    ciphertext = crypto.encrypt_message("restart history", "channel")
    path = tmp_path / "private" / "keys.json"
    assert path.is_file() and path.stat().st_mode & 0o077 == 0
    crypto._development_keys.cache_clear()
    settings.jwt_secret = "rotated-jwt"
    assert crypto.decrypt_message(ciphertext, "channel") == "restart history"


def test_development_keyfile_symlink_is_rejected(tmp_path, monkeypatch):
    target = tmp_path / "target.json"
    target.write_text(json.dumps({"local-dev": encoded(b"x" * 32)}))
    os.chmod(target, 0o600)
    link = tmp_path / "link.json"
    link.symlink_to(target)
    settings = Settings(_env_file=None, environment="test", message_encryption_key_file=str(link))
    monkeypatch.setattr(crypto, "settings", settings)
    with pytest.raises(ValueError, match="symlink"):
        crypto.encrypt_message("secret", "channel")


def test_dry_run_audits_without_changing_rows(database, crypto_settings):
    ciphertext = legacy_encrypt("history")
    seed(database, "legacy", ciphertext)
    with Session(database) as session:
        report = crypto_migration.migrate_messages(session)
    assert report["dry_run"] and report["legacy_encrypted"] == 1
    assert report["would_update"] == 1 and report["updated"] == 0
    assert stored(database, "legacy") == ciphertext


def test_apply_migrates_legacy_and_explicit_plaintext_idempotently(database, crypto_settings):
    seed(database, "legacy", legacy_encrypt("history"))
    seed(database, "plaintext", "old reply", encrypted=False)
    seed(database, "deleted", "[Message deleted]", deleted=True)
    with Session(database) as session:
        report = crypto_migration.migrate_messages(session, apply=True)
    assert report["updated"] == 2 and report["deleted_skipped"] == 1
    assert crypto.decrypt_message(stored(database, "legacy"), "channel") == "history"
    assert crypto.decrypt_message(stored(database, "plaintext"), "channel") == "old reply"
    assert stored(database, "deleted") == "[Message deleted]"
    with Session(database) as session:
        second = crypto_migration.migrate_messages(session, apply=True)
    assert second["updated"] == 0 and second["already_current"] == 2


def test_migration_rotates_old_envelopes_and_allows_old_key_removal(database, crypto_settings):
    crypto_settings.message_encryption_active_key_id = "old"
    ciphertext = crypto.encrypt_message("rotate me", "channel")
    seed(database, "old-envelope", ciphertext)
    crypto_settings.message_encryption_active_key_id = "current"
    with Session(database) as session:
        report = crypto_migration.migrate_messages(session, apply=True)
    assert report["rotated"] == 1
    del crypto_settings.message_encryption_keys["old"]
    assert crypto.decrypt_message(stored(database, "old-envelope"), "channel") == "rotate me"


def test_invalid_flagged_ciphertext_aborts_all_rows(database, crypto_settings):
    valid = legacy_encrypt("safe legacy history")
    seed(database, "a-valid", valid)
    seed(database, "z-invalid", "plaintext incorrectly flagged encrypted")
    with Session(database) as session:
        with pytest.raises(crypto_migration.MessageMigrationError, match="z-invalid"):
            crypto_migration.migrate_messages(session, apply=True)
    assert stored(database, "a-valid") == valid
    assert stored(database, "z-invalid") == "plaintext incorrectly flagged encrypted"


def test_migration_rejects_an_existing_transaction(database, crypto_settings):
    with Session(database) as session:
        session.get(User, "owner")
        with pytest.raises(ValueError, match="fresh"):
            crypto_migration.migrate_messages(session, apply=True)
