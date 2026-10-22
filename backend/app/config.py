import base64
import binascii
import re
from urllib.parse import urlsplit

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, SecretStr, field_validator, model_validator
from typing import Literal


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", hide_input_in_errors=True)

    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite:///./freecollab.db"
    jwt_secret: str = "change-me-in-production"
    storage_path: str = "./data/files"
    storage_backend: Literal["local", "s3"] = "local"
    s3_endpoint_url: str = ""
    s3_bucket: str = ""
    s3_region: str = "us-east-1"
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None
    s3_session_token: SecretStr | None = None
    s3_key_prefix: str = "freecollab/"
    s3_ca_bundle: str = ""
    s3_allow_insecure_http: bool = False
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    enable_experimental_features: bool = False
    enable_demo_ai: bool = False
    message_encryption_keys: dict[str, SecretStr] = Field(default_factory=dict)
    message_encryption_active_key_id: str = "local-dev"
    message_encryption_key_file: str = "./data/message-keys.json"
    legacy_message_encryption_key: SecretStr | None = None
    account_security_key: SecretStr | None = None
    account_security_key_file: str = "./data/account-security.key"
    public_app_url: str = "http://127.0.0.1:3000"
    mail_delivery_mode: Literal["disabled", "file", "smtp"] = "file"
    mail_file_path: str = "./data/mail"
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str = ""
    smtp_password: SecretStr | None = None
    smtp_from: str = "FreeCollab <no-reply@localhost>"
    smtp_starttls: bool = True
    smtp_implicit_tls: bool = False
    smtp_timeout: float = Field(default=15.0, gt=0, le=120)
    matrix_enabled: bool = False
    matrix_internal_url: str = "http://matrix:8008"
    matrix_server_name: str = "freecollab.local"
    matrix_registration_shared_secret: SecretStr | None = None
    matrix_public_base_url: str = ""
    connect_federation_enabled: bool = False
    connect_federation_public_url: str = ""
    connect_federation_peers: dict[str, dict[str, str]] = Field(default_factory=dict)
    oidc_enabled: bool = False
    oidc_provider_name: str = "Organization SSO"
    oidc_issuer_url: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: SecretStr | None = None
    oidc_redirect_uri: str = ""
    oidc_scopes: str = "openid profile email"
    oidc_trust_email_claim: bool = False
    webhooks_enabled: bool = False
    sync_enabled: bool = False
    otrust_enabled: bool = False
    otrust_base_url: str = "https://www.otrust.eu"
    otrust_timeout_seconds: float = Field(default=10.0, gt=0, le=30)
    operations_metrics_token: SecretStr | None = None
    security_audit_retention_days: int = Field(default=90, ge=7, le=365)
    security_audit_max_records: int = Field(default=100000, ge=1000, le=1000000)
    workflows_enabled: bool = True
    data_controls_enabled: bool = True
    activity_feeds_enabled: bool = True
    cloud_files_enabled: bool = False
    external_meetings_enabled: bool = False
    copilot_enabled: bool = False
    push_enabled: bool = False
    push_vapid_private_key: SecretStr | None = None
    push_vapid_subject: str = ""
    native_push_enabled: bool = False
    native_calls_enabled: bool = False
    native_push_fcm_project_id: str = ""
    native_push_fcm_service_account: SecretStr | None = None
    native_push_apns_team_id: str = ""
    native_push_apns_key_id: str = ""
    native_push_apns_topic: str = "eu.freecollab.mobile"
    native_push_apns_private_key: SecretStr | None = None
    native_push_apns_sandbox: bool = False
    meetings_ice_servers: list[str] = Field(default_factory=list, max_length=10)
    meetings_turn_urls: list[str] = Field(default_factory=list, max_length=10)
    meetings_turn_secret: SecretStr | None = None
    enterprise_search_enabled: bool = True
    meetings_sfu_provider: Literal["disabled", "livekit"] = "disabled"
    meetings_sfu_url: str = ""
    meetings_sfu_api_url: str = ""
    meetings_sfu_api_key: str = ""
    meetings_sfu_api_secret: SecretStr | None = None
    meetings_sfu_max_participants: int = Field(default=100, ge=2, le=1000)
    enterprise_vault_url: str = ""
    enterprise_vault_token: SecretStr | None = None
    enterprise_vault_mount: str = "transit"
    enterprise_vault_key: str = "freecollab"
    enterprise_vault_namespace: str = ""
    redis_url: SecretStr | None = None
    redis_namespace: str = "freecollab"
    redis_tls_required: bool = True
    redis_presence_lease_seconds: int = Field(default=75, ge=30, le=300)

    @field_validator("redis_url", mode="before")
    @classmethod
    def optional_redis_url(cls, value):
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        return None if raw is None or raw == "" else value

    @model_validator(mode="after")
    def validate_object_storage(self):
        if self.storage_backend != "s3":
            return self
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", self.s3_bucket) or ".." in self.s3_bucket:
            raise ValueError("S3_BUCKET must be an explicit valid bucket name")
        if (not self.s3_access_key_id or not self.s3_secret_access_key
                or not self.s3_access_key_id.get_secret_value().strip()
                or not self.s3_secret_access_key.get_secret_value().strip()
                or not self.s3_region.strip()):
            raise ValueError("S3 storage requires its own access key, secret key and region")
        if len(self.s3_key_prefix) > 256 or (self.s3_key_prefix and (
            not self.s3_key_prefix.endswith("/") or any(
                not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", part)
                for part in self.s3_key_prefix[:-1].split("/")))):
            raise ValueError("S3_KEY_PREFIX must contain safe directory components and end with /")
        if self.s3_endpoint_url:
            endpoint = urlsplit(self.s3_endpoint_url)
            insecure = self.s3_allow_insecure_http and self.environment in {"development", "test"} and endpoint.scheme == "http"
            if (endpoint.scheme != "https" and not insecure) or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment or endpoint.path not in {"", "/"} or any(c.isspace() for c in self.s3_endpoint_url):
                raise ValueError("S3_ENDPOINT_URL must be an explicit HTTPS origin; development HTTP requires S3_ALLOW_INSECURE_HTTP")
        if self.environment == "production" and self.s3_allow_insecure_http:
            raise ValueError("Production object storage requires verified HTTPS")
        return self

    @field_validator("meetings_ice_servers", "meetings_turn_urls")
    @classmethod
    def meeting_ice_urls(cls, values, info):
        schemes = ("stun:", "stuns:") if info.field_name == "meetings_ice_servers" else ("turn:", "turns:")
        if any(not isinstance(value, str) or not value.startswith(schemes) or not 1 < len(value) <= 512
               or any(char.isspace() for char in value) or "@" in value or "#" in value for value in values):
            raise ValueError("Meeting ICE URLs must use their configured STUN/TURN protocol without embedded credentials")
        return values

    @field_validator("meetings_turn_secret")
    @classmethod
    def meeting_turn_credential(cls, value):
        if value and len(value.get_secret_value().encode()) < 32:
            raise ValueError("MEETINGS_TURN_SECRET must contain at least 32 bytes")
        return value

    @field_validator("operations_metrics_token", mode="before")
    @classmethod
    def metrics_credential(cls, value):
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        if raw is None or raw == "":
            return None
        if not isinstance(raw, str) or len(raw.encode()) < 32 or any(ch.isspace() for ch in raw):
            raise ValueError("OPERATIONS_METRICS_TOKEN must contain at least 32 bytes without whitespace")
        return value

    @field_validator("legacy_message_encryption_key", mode="before")
    @classmethod
    def optional_legacy_key(cls, value):
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        return None if isinstance(raw, str) and not raw.strip() else value

    @model_validator(mode="after")
    def validate_production(self):
        if self.account_security_key is not None:
            account_key = decode_message_key(self.account_security_key.get_secret_value())
            if account_key == self.jwt_secret.encode()[:32].ljust(32, b"0"):
                raise ValueError("ACCOUNT_SECURITY_KEY must be independent of JWT_SECRET")
            if any(account_key == decode_message_key(value.get_secret_value()) for value in self.message_encryption_keys.values()):
                raise ValueError("ACCOUNT_SECURITY_KEY must be independent of message encryption keys")
        if not self.public_app_url.startswith(("http://", "https://")) or any(ch in self.public_app_url for ch in "\r\n"):
            raise ValueError("PUBLIC_APP_URL must be an absolute HTTP(S) URL")
        if self.smtp_implicit_tls and self.smtp_starttls:
            raise ValueError("Choose either SMTP implicit TLS or STARTTLS")
        if any(ch in self.smtp_host + self.smtp_from + self.smtp_username for ch in "\r\n"):
            raise ValueError("SMTP settings must not contain header line breaks")
        if self.smtp_host and (any(ch.isspace() for ch in self.smtp_host) or any(ch in self.smtp_host for ch in "/@?#")):
            raise ValueError("SMTP_HOST must be a hostname without credentials or a URL path")
        if self.oidc_enabled:
            issuer = urlsplit(self.oidc_issuer_url)
            redirect = urlsplit(self.oidc_redirect_uri)
            for value, parsed, name in [(self.oidc_issuer_url, issuer, "OIDC_ISSUER_URL"), (self.oidc_redirect_uri, redirect, "OIDC_REDIRECT_URI")]:
                local_http = self.environment != "production" and parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
                if (parsed.scheme != "https" and not local_http) or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or any(ch.isspace() for ch in value):
                    raise ValueError(name + " must be an explicit HTTPS URL without credentials, query or fragment")
            if not self.oidc_client_id.strip() or not self.oidc_provider_name.strip() or "openid" not in self.oidc_scopes.split():
                raise ValueError("OIDC requires a client ID, provider name and openid scope")
        if self.otrust_enabled:
            provider = urlsplit(self.otrust_base_url)
            local_http = self.environment != "production" and provider.scheme == "http" and provider.hostname in {"localhost", "127.0.0.1", "::1"}
            if (provider.scheme != "https" and not local_http) or not provider.hostname or provider.username or provider.password or provider.query or provider.fragment or provider.path not in {"", "/"} or any(ch.isspace() for ch in self.otrust_base_url):
                raise ValueError("OTRUST_BASE_URL must be an HTTPS origin without credentials, path or query")
        if self.matrix_enabled:
            if not self.matrix_registration_shared_secret or len(self.matrix_registration_shared_secret.get_secret_value()) < 32:
                raise ValueError("MATRIX_ENABLED requires an explicit registration secret of at least 32 characters")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*(?::[0-9]{1,5})?", self.matrix_server_name):
                raise ValueError("MATRIX_SERVER_NAME must be a stable hostname with an optional port")
            internal = urlsplit(self.matrix_internal_url)
            if internal.scheme not in {"http", "https"} or not internal.hostname or internal.username or internal.password or internal.query or internal.fragment or internal.path not in {"", "/"} or any(c.isspace() for c in self.matrix_internal_url):
                raise ValueError("MATRIX_INTERNAL_URL must be an HTTP(S) origin without credentials, path or query")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", self.message_encryption_active_key_id):
            raise ValueError("MESSAGE_ENCRYPTION_ACTIVE_KEY_ID must be a safe 1–32 character identifier")
        decoded_keys = {}
        for key_id, secret in self.message_encryption_keys.items():
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", key_id):
                raise ValueError("MESSAGE_ENCRYPTION_KEYS contains an invalid key identifier")
            decoded_keys[key_id] = decode_message_key(secret.get_secret_value())
        if decoded_keys and self.message_encryption_active_key_id not in decoded_keys:
            raise ValueError("MESSAGE_ENCRYPTION_ACTIVE_KEY_ID is missing from MESSAGE_ENCRYPTION_KEYS")
        if self.legacy_message_encryption_key is not None:
            decode_message_key(self.legacy_message_encryption_key.get_secret_value())
        if self.environment == "production":
            if self.mail_delivery_mode == "smtp" and not (self.smtp_implicit_tls or self.smtp_starttls):
                raise ValueError("Production SMTP requires STARTTLS or implicit TLS")
            if len(self.jwt_secret.encode()) < 32 or self.jwt_secret.startswith("change-"):
                raise ValueError("Production requires a unique JWT_SECRET of at least 32 bytes")
            if not self.cors_origins or any(origin == "*" or not origin.startswith("https://") for origin in self.cors_origins):
                raise ValueError("Production requires explicit HTTPS CORS_ORIGINS")
            if not decoded_keys:
                raise ValueError("Production requires explicit MESSAGE_ENCRYPTION_KEYS")
            jwt_derived_key = self.jwt_secret.encode()[:32].ljust(32, b"0")
            if any(key == jwt_derived_key for key in decoded_keys.values()):
                raise ValueError("Message encryption keys must be independent of JWT_SECRET")
        return self


def decode_message_key(value: str) -> bytes:
    """Decode an explicit 256-bit content key without leaking its value."""
    try:
        key = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("Message encryption keys must be standard base64-encoded 32-byte keys") from None
    if len(key) != 32:
        raise ValueError("Message encryption keys must be standard base64-encoded 32-byte keys")
    return key


settings = Settings()
