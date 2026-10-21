"""
Group Crypto Service - Channel key management for E2E encrypted group chats.

Flow:
1. When channel is created, generate a random symmetric key
2. Encrypt channel key for each member using their public key
3. Store encrypted key per member in ChannelMember.encrypted_channel_key
4. When member sends message, decrypt channel key with their private key, encrypt message
5. Recipients decrypt channel key with their private key, decrypt message
"""

import nacl.utils
import nacl.secret
from nacl.public import PrivateKey, PublicKey, Box, SealedBox
from nacl.encoding import Base64Encoder
from typing import Dict


class GroupCryptoService:
    """
    Manages symmetric group keys for channel encryption.
    Each channel has a unique key that's distributed to members
    via asymmetric encryption.
    """

    @staticmethod
    def generate_channel_key() -> bytes:
        """Generate a random 32-byte key for symmetric encryption."""
        return nacl.utils.random(nacl.secret.SecretBox.KEY_SIZE)

    @staticmethod
    def encrypt_channel_key_for_user(
        channel_key: bytes,
        user_public_key: str,
    ) -> str:
        """
        Encrypt channel key for a specific user using their public key.
        Uses SealedBox (anonymous sender) - only recipient can decrypt.
        """
        public_key = PublicKey(user_public_key.encode(), Base64Encoder)
        sealed_box = SealedBox(public_key)
        encrypted = sealed_box.encrypt(channel_key, encoder=Base64Encoder)
        return encrypted.decode()

    @staticmethod
    def decrypt_channel_key(
        encrypted_key: str,
        user_private_key: str,
    ) -> bytes:
        """
        Decrypt channel key using user's private key.
        Returns the raw symmetric key for message encryption.
        """
        private_key = PrivateKey(user_private_key.encode(), Base64Encoder)
        sealed_box = SealedBox(private_key)
        return sealed_box.decrypt(encrypted_key.encode(), encoder=Base64Encoder)

    @staticmethod
    def encrypt_message(plaintext: str, channel_key: bytes) -> str:
        """Encrypt message with channel's symmetric key."""
        box = nacl.secret.SecretBox(channel_key)
        encrypted = box.encrypt(plaintext.encode(), encoder=Base64Encoder)
        return encrypted.decode()

    @staticmethod
    def decrypt_message(ciphertext: str, channel_key: bytes) -> str:
        """Decrypt message with channel's symmetric key."""
        box = nacl.secret.SecretBox(channel_key)
        decrypted = box.decrypt(ciphertext.encode(), encoder=Base64Encoder)
        return decrypted.decode()

    @staticmethod
    def encrypt_key_for_members(
        channel_key: bytes,
        member_public_keys: Dict[str, str],  # user_id -> public_key
    ) -> Dict[str, str]:
        """
        Encrypt channel key for multiple members.
        Returns dict of user_id -> encrypted_key.
        """
        result = {}
        for user_id, public_key in member_public_keys.items():
            if public_key:  # Skip users without keys
                result[user_id] = GroupCryptoService.encrypt_channel_key_for_user(
                    channel_key, public_key
                )
        return result

    @staticmethod
    def rekey_channel(
        old_key: bytes,
        member_public_keys: Dict[str, str],
    ) -> tuple[bytes, Dict[str, str]]:
        """
        Generate new channel key and encrypt for all members.
        Used when member is removed (forward secrecy).
        Returns (new_key, encrypted_keys_dict).
        """
        new_key = GroupCryptoService.generate_channel_key()
        encrypted_keys = GroupCryptoService.encrypt_key_for_members(
            new_key, member_public_keys
        )
        return new_key, encrypted_keys


def encrypt_private_key(private_key: str, password: str) -> str:
    """
    Encrypt user's private key with their password.
    Stored server-side but unreadable without password.
    Uses Argon2id for key derivation.
    """
    import nacl.pwhash
    
    # Derive key from password
    salt = nacl.utils.random(nacl.pwhash.argon2id.SALTBYTES)
    key = nacl.pwhash.argon2id.kdf(
        nacl.secret.SecretBox.KEY_SIZE,
        password.encode(),
        salt,
        opslimit=nacl.pwhash.argon2id.OPSLIMIT_INTERACTIVE,
        memlimit=nacl.pwhash.argon2id.MEMLIMIT_INTERACTIVE,
    )
    
    # Encrypt private key
    box = nacl.secret.SecretBox(key)
    encrypted = box.encrypt(private_key.encode())
    
    # Combine salt + encrypted for storage
    combined = salt + encrypted
    return Base64Encoder.encode(combined).decode()


def decrypt_private_key(encrypted_key: str, password: str) -> str:
    """
    Decrypt user's private key with their password.
    Called client-side after login.
    """
    import nacl.pwhash
    
    combined = Base64Encoder.decode(encrypted_key.encode())
    salt = combined[:nacl.pwhash.argon2id.SALTBYTES]
    encrypted = combined[nacl.pwhash.argon2id.SALTBYTES:]
    
    # Derive same key from password
    key = nacl.pwhash.argon2id.kdf(
        nacl.secret.SecretBox.KEY_SIZE,
        password.encode(),
        salt,
        opslimit=nacl.pwhash.argon2id.OPSLIMIT_INTERACTIVE,
        memlimit=nacl.pwhash.argon2id.MEMLIMIT_INTERACTIVE,
    )
    
    # Decrypt private key
    box = nacl.secret.SecretBox(key)
    return box.decrypt(encrypted).decode()
