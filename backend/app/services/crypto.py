import nacl.secret
import nacl.pwhash
import nacl.utils
import base64
import json
import os
from pathlib import Path
import tempfile
from functools import lru_cache
from nacl.public import PrivateKey, PublicKey, Box
from nacl.encoding import Base64Encoder
from nacl.exceptions import CryptoError
from ..config import settings, decode_message_key


@lru_cache(maxsize=8)
def _development_keys(filename: str, key_id: str) -> dict[str, bytes]:
    """Persist development keys independently of JWTs, outside uploaded files."""
    path = Path(filename)
    if path.is_symlink():
        raise ValueError("The development message key file must not be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.exists():
        document = {key_id: base64.b64encode(nacl.utils.random(32)).decode("ascii")}
        # Publish a complete private file atomically, without replacing another
        # process's key file. No reader can observe a partially written secret.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=".message-key-", delete=False) as stream:
                temporary = stream.name
                json.dump(document, stream)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                pass
        finally:
            if temporary is not None:
                os.unlink(temporary)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Invalid development message key file")
    if path.stat().st_mode & 0o077:
        raise ValueError("Development message key file must have private (0600) permissions")
    try:
        document = json.loads(path.read_text())
        if not isinstance(document, dict) or key_id not in document:
            raise ValueError("The development message key file lacks the active key")
        return {name: decode_message_key(value) for name, value in document.items()}
    except (json.JSONDecodeError, TypeError):
        raise ValueError("Invalid development message key file") from None


def message_keyring() -> dict[str, bytes]:
    if settings.message_encryption_keys:
        return {key_id: decode_message_key(secret.get_secret_value())
                for key_id, secret in settings.message_encryption_keys.items()}
    if settings.environment == "production":
        raise ValueError("Production requires explicit MESSAGE_ENCRYPTION_KEYS")
    return _development_keys(str(Path(settings.message_encryption_key_file).absolute()), settings.message_encryption_active_key_id)


def get_box(key_id: str | None = None) -> nacl.secret.SecretBox:
    key_id = key_id or settings.message_encryption_active_key_id
    key = message_keyring().get(key_id)
    if key is None:
        raise CryptoError("Unknown message encryption key")
    return nacl.secret.SecretBox(key)


def encrypt_message(plaintext: str, channel_id: str) -> str:
    key_id = settings.message_encryption_active_key_id
    payload = json.dumps({"version": 1, "key_id": key_id, "channel_id": channel_id, "content": plaintext}, separators=(",", ":"))
    encrypted = get_box(key_id).encrypt(payload.encode(), encoder=Base64Encoder)
    return f"fc1:{key_id}:{encrypted.decode()}"


def decrypt_message(ciphertext: str, channel_id: str) -> str:
    if not ciphertext.startswith("fc1:"):
        # Legacy content used the JWT-derived key, but reading it now requires
        # an explicitly retained key. Never silently derive from the live JWT.
        if ":" in ciphertext or settings.legacy_message_encryption_key is None:
            raise CryptoError("Legacy message key is not configured or the envelope version is unsupported")
        key = decode_message_key(settings.legacy_message_encryption_key.get_secret_value())
        return nacl.secret.SecretBox(key).decrypt(ciphertext.encode(), encoder=Base64Encoder).decode()
    try:
        _, key_id, encoded = ciphertext.split(":", 2)
        payload = json.loads(get_box(key_id).decrypt(encoded.encode(), encoder=Base64Encoder))
        if (not isinstance(payload, dict) or payload.get("version") != 1
                or payload.get("key_id") != key_id or payload.get("channel_id") != channel_id
                or not isinstance(payload.get("content"), str)):
            raise CryptoError("Message context does not match")
        return payload["content"]
    except (ValueError, TypeError, UnicodeDecodeError):
        raise CryptoError("Invalid authenticated message payload") from None


class CryptoService:
    """
    Key and encryption primitives using PyNaCl (libsodium).
    The active API uses server-side encryption and receives plaintext.
    """

    @staticmethod
    def generate_keypair() -> tuple[str, str]:
        """Generate public/private keypair for a user."""
        private_key = PrivateKey.generate()
        public_key = private_key.public_key
        return (
            private_key.encode(Base64Encoder).decode(),
            public_key.encode(Base64Encoder).decode(),
        )

    @staticmethod
    def encrypt_for_recipient(
        plaintext: str,
        sender_private_key: str,
        recipient_public_key: str,
    ) -> str:
        """
        Encrypt message for a specific recipient.
        Only the recipient can decrypt it.
        """
        sender_key = PrivateKey(sender_private_key.encode(), Base64Encoder)
        recipient_key = PublicKey(recipient_public_key.encode(), Base64Encoder)

        box = Box(sender_key, recipient_key)
        encrypted = box.encrypt(plaintext.encode(), encoder=Base64Encoder)

        return encrypted.decode()

    @staticmethod
    def decrypt_from_sender(
        ciphertext: str,
        recipient_private_key: str,
        sender_public_key: str,
    ) -> str:
        """
        Decrypt message from a specific sender.
        Only works if you're the intended recipient.
        """
        recipient_key = PrivateKey(recipient_private_key.encode(), Base64Encoder)
        sender_key = PublicKey(sender_public_key.encode(), Base64Encoder)

        box = Box(recipient_key, sender_key)
        decrypted = box.decrypt(ciphertext.encode(), encoder=Base64Encoder)

        return decrypted.decode()

    @staticmethod
    def encrypt_for_group(plaintext: str, group_key: bytes) -> str:
        """
        Encrypt message for a group using symmetric encryption.
        Group key is distributed via asymmetric encryption.
        """
        box = nacl.secret.SecretBox(group_key)
        encrypted = box.encrypt(plaintext.encode(), encoder=Base64Encoder)
        return encrypted.decode()

    @staticmethod
    def decrypt_for_group(ciphertext: str, group_key: bytes) -> str:
        """Decrypt group message."""
        box = nacl.secret.SecretBox(group_key)
        decrypted = box.decrypt(ciphertext.encode(), encoder=Base64Encoder)
        return decrypted.decode()

    @staticmethod
    def hash_password(password: str) -> str:
        """Hash password using Argon2id (memory-hard, GPU-resistant)."""
        return nacl.pwhash.str(password.encode()).decode()

    @staticmethod
    def verify_password(password: str, hash: str) -> bool:
        """Verify password against hash."""
        try:
            return nacl.pwhash.verify(hash.encode(), password.encode())
        except nacl.exceptions.InvalidkeyError:
            return False
