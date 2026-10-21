import pytest
import base64
from pydantic import ValidationError
from app.config import Settings


def test_production_rejects_default_credentials():
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        Settings(_env_file=None, environment="production", jwt_secret="change-me-in-production")


@pytest.mark.parametrize("origins", [["*"], [], ["http://example.com"]])
def test_production_requires_explicit_https_origins(origins):
    with pytest.raises(ValidationError, match="CORS_ORIGINS"):
        Settings(_env_file=None, environment="production", jwt_secret="a" * 64, cors_origins=origins)


def test_production_accepts_explicit_configuration():
    settings = Settings(_env_file=None, environment="production", jwt_secret="a" * 64, cors_origins=["https://chat.example.com"],
                        message_encryption_keys={"current": base64.b64encode(b"k" * 32).decode()}, message_encryption_active_key_id="current")
    assert settings.cors_origins == ["https://chat.example.com"]


def test_production_rejects_missing_content_key():
    with pytest.raises(ValidationError, match="MESSAGE_ENCRYPTION_KEYS"):
        Settings(_env_file=None, environment="production", jwt_secret="a" * 64, cors_origins=["https://chat.example.com"])


def test_production_rejects_reusing_jwt_derived_key():
    with pytest.raises(ValidationError, match="independent"):
        Settings(_env_file=None, environment="production", jwt_secret="a" * 64, cors_origins=["https://chat.example.com"],
                 message_encryption_keys={"current": base64.b64encode(b"a" * 32).decode()}, message_encryption_active_key_id="current")


@pytest.mark.parametrize("key", ["bad-key", base64.b64encode(b"short").decode()])
def test_invalid_content_key_does_not_expose_secret_in_error(key):
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, message_encryption_keys={"current": key}, message_encryption_active_key_id="current", jwt_secret="do-not-display-signing-secret")
    assert "do-not-display-signing-secret" not in str(error.value)
    assert key not in str(error.value)


def test_content_key_ring_requires_active_member():
    with pytest.raises(ValidationError, match="missing"):
        Settings(_env_file=None, message_encryption_keys={"old": base64.b64encode(b"k" * 32).decode()}, message_encryption_active_key_id="new")


@pytest.mark.parametrize("value", ["", " ", "\n\t"])
def test_blank_optional_legacy_environment_key_is_absent(monkeypatch, value):
    monkeypatch.setenv("LEGACY_MESSAGE_ENCRYPTION_KEY", value)
    settings = Settings(_env_file=None)
    assert settings.legacy_message_encryption_key is None
