"""Customer Vault Transit key wrapping for native server-processed content.

Vault wraps a fresh 256-bit data key; libsodium authenticates and encrypts the
content locally. No message plaintext leaves the server for the KMS provider.
No plaintext data keys are cached or persisted. This is not Matrix E2EE.
"""
import base64
import json
import re

from nacl import secret, utils
from nacl.exceptions import CryptoError

from ..config import settings
from . import remote_transport as transport

PREFIX = "fc2:vault:"


def status():
    enabled = bool(getattr(settings, "enterprise_vault_url", ""))
    return {"provider": "vault_transit" if enabled else "local_versioned_keyring", "enabled": enabled,
        "configured": enabled and bool(getattr(settings, "enterprise_vault_token", None)),
        "plaintext_sent_to_provider": False, "automatic_rotation": False}


def enabled():
    return bool(getattr(settings, "enterprise_vault_url", ""))


def _configuration():
    url = getattr(settings, "enterprise_vault_url", "").rstrip("/")
    credential = getattr(settings, "enterprise_vault_token", None)
    mount = getattr(settings, "enterprise_vault_mount", "transit")
    key = getattr(settings, "enterprise_vault_key", "freecollab")
    namespace = getattr(settings, "enterprise_vault_namespace", "")
    if (not url or not credential or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", mount)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", key)
            or not isinstance(namespace, str) or len(namespace) > 200
            or any(ord(c) < 33 or ord(c) == 127 for c in namespace)):
        raise CryptoError("Customer KMS is not configured")
    # Validate each request in the shared pinned HTTPS transport as well.
    try:
        parsed, _ = transport.pinned.normalize_url(url)
        if parsed.query or parsed.path not in {"", "/"}:
            raise transport.pinned.TransportError("Invalid Vault base URL")
    except transport.pinned.TransportError:
        raise CryptoError("Customer KMS is not configured") from None
    reference = {"url": url, "mount": mount, "key": key, "namespace": namespace}
    headers = {"X-Vault-Token": credential.get_secret_value(), "Content-Type": "application/json"}
    if namespace:
        headers["X-Vault-Namespace"] = namespace
    return reference, headers


def _request(operation, value):
    reference, headers = _configuration()
    url = reference["url"] + "/v1/" + reference["mount"] + "/" + operation + "/" + reference["key"]
    try:
        response = transport.request("POST", url, headers=headers, body=json.dumps(value, separators=(",", ":")).encode(), max_bytes=16 * 1024, timeout=12)
        transport.check_response(response, allowed=(200,))
        document = response.json()
        if not isinstance(document.get("data"), dict):
            raise ValueError()
        return reference, document["data"]
    except (transport.ProviderError, ValueError, TypeError):
        # No credential, ciphertext or provider response in logs/exceptions.
        raise CryptoError("Customer KMS operation failed") from None


def encrypt(plaintext, channel_id):
    data_key = utils.random(secret.SecretBox.KEY_SIZE)
    reference, wrapped = _request("encrypt", {"plaintext": base64.b64encode(data_key).decode("ascii")})
    wrapped_key = wrapped.get("ciphertext")
    if not isinstance(wrapped_key, str) or not re.fullmatch(r"vault:v[1-9][0-9]*:[A-Za-z0-9+/=]{16,8192}", wrapped_key):
        raise CryptoError("Customer KMS returned an invalid wrapped key")
    payload = json.dumps({"version": 2, "channel_id": channel_id, "content": plaintext}, separators=(",", ":")).encode()
    encrypted = secret.SecretBox(data_key).encrypt(payload)
    envelope = {"version": 2, "reference": reference, "wrapped_key": wrapped_key,
        "ciphertext": base64.b64encode(encrypted).decode("ascii")}
    return PREFIX + base64.b64encode(json.dumps(envelope, separators=(",", ":")).encode()).decode("ascii")


def decrypt(ciphertext, channel_id):
    try:
        if not ciphertext.startswith(PREFIX) or len(ciphertext) > 32 * 1024 * 1024:
            raise ValueError()
        envelope = json.loads(base64.b64decode(ciphertext[len(PREFIX):], validate=True))
        reference, _ = _configuration()
        if not isinstance(envelope, dict) or envelope.get("version") != 2 or envelope.get("reference") != reference:
            raise CryptoError("Customer KMS key reference is unavailable; retain the original provider key")
        wrapped = envelope.get("wrapped_key")
        if not isinstance(wrapped, str) or not re.fullmatch(r"vault:v[1-9][0-9]*:[A-Za-z0-9+/=]{16,8192}", wrapped):
            raise ValueError()
        _, response = _request("decrypt", {"ciphertext": wrapped})
        data_key = base64.b64decode(response["plaintext"], validate=True)
        if len(data_key) != secret.SecretBox.KEY_SIZE:
            raise ValueError()
        payload = json.loads(secret.SecretBox(data_key).decrypt(base64.b64decode(envelope["ciphertext"], validate=True)))
        if (not isinstance(payload, dict) or payload.get("version") != 2 or payload.get("channel_id") != channel_id
                or not isinstance(payload.get("content"), str)):
            raise CryptoError("Message context does not match")
        return payload["content"]
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise CryptoError("Invalid customer KMS authenticated message payload") from None
