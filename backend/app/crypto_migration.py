"""Offline, transactional content-key migration. Never changes JWT signing keys."""
from sqlmodel import Session, select

from .config import settings
from .models.message import Message
from .services.crypto import decrypt_message, encrypt_message, message_keyring


class MessageMigrationError(ValueError):
    """A message could not be authenticated; the migration changed no rows."""


def migrate_messages(session: Session, *, apply: bool = False) -> dict:
    """Audit/rotate live message bodies in a fresh Session, committing atomically.

    Stop writers and back up the database/key material before an applying run.
    Explicit plaintext (`encrypted=False`) is encrypted. Invalid bodies marked
    encrypted are never guessed to be plaintext; any one aborts the whole run.
    """
    if session.in_transaction():
        raise ValueError("Message migration requires a fresh database session")
    message_keyring()
    result = {
        "dry_run": not apply,
        "scanned": 0,
        "legacy_encrypted": 0,
        "plaintext": 0,
        "rotated": 0,
        "already_current": 0,
        "deleted_skipped": 0,
        "updated": 0,
    }
    changes = []
    try:
        with session.begin():
            messages = session.exec(select(Message).with_for_update()).all()
            for message in messages:
                result["scanned"] += 1
                if message.deleted:
                    result["deleted_skipped"] += 1
                    continue
                if not message.encrypted:
                    plaintext = message.content
                    result["plaintext"] += 1
                else:
                    try:
                        plaintext = decrypt_message(message.content, message.channel_id)
                    except Exception:
                        # IDs identify records for an operator audit; never put
                        # ciphertext, plaintext, keys or exception data in logs.
                        raise MessageMigrationError(
                            f"Message {message.id} could not be authenticated; no rows changed"
                        ) from None
                    if message.content.startswith(f"fc1:{settings.message_encryption_active_key_id}:"):
                        result["already_current"] += 1
                        continue
                    category = "rotated" if message.content.startswith("fc1:") else "legacy_encrypted"
                    result[category] += 1
                # Prepare before updating any objects: all rows must validate.
                changes.append((message, encrypt_message(plaintext, message.channel_id)))
            if apply:
                for message, ciphertext in changes:
                    message.content = ciphertext
                    message.encrypted = True
                    session.add(message)
                result["updated"] = len(changes)
            result["would_update"] = len(changes)
        return result
    except Exception:
        session.rollback()
        raise
