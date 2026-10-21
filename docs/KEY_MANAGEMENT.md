# Client and server key boundaries

New secure channels use client-held Matrix/Rust device keys and a separate user-owned recovery key. The server content-key ring described below belongs to legacy messages and internal service credentials; it cannot decrypt secure channel bodies or attachments. Preserve Matrix's durable volume and server secrets in v2 backups, and preserve the user's recovery key separately. See [E2EE_IMPLEMENTATION.md](E2EE_IMPLEMENTATION.md) for the actual client protocol, trust and recovery flow.

# Message keys and offline migration

FreeCollab encrypts message bodies on the server. The server can read content. Versioned content encryption does not provide end-to-end encryption and does not encrypt uploaded files, posts, comments or metadata.

## Configuration

JWT signing and message encryption use separate secrets:

| Setting | Meaning |
| --- | --- |
| `JWT_SECRET` | Signs login tokens. Rotating it invalidates existing tokens. It is never used to encrypt new messages. |
| `MESSAGE_ENCRYPTION_KEYS` | JSON object mapping a safe key ID to a standard base64-encoded random 32-byte content key. Keep old keys while any message uses them. |
| `MESSAGE_ENCRYPTION_ACTIVE_KEY_ID` | ID used for all new/edited message bodies. The ID must be present in the configured ring. |
| `LEGACY_MESSAGE_ENCRYPTION_KEY` | Optional, explicit base64-encoded 32-byte key for reading unversioned messages from old installations. It is not used for new writes. |
| `MESSAGE_ENCRYPTION_KEY_FILE` | Development-only generated key-ring file when no explicit ring is provided; default `./data/message-keys.json`, resolved from the backend working directory. |

Use random independent content keys, stored in a private secret manager or private deployment configuration. Do not commit them, paste them into chat, print them to logs or include them in support bundles. Production startup requires an explicit key ring, an active ID, a private JWT signing secret and allowed HTTPS origins; it rejects content keys equal to the current JWT-derived legacy key. Production never generates a key or derives one from `JWT_SECRET` implicitly.

In development, a random local key is published atomically to a private `0600` key file, outside the default upload directory. Restarts reuse it. Preserve that file with the development database if history matters. Removing it prevents historical-message decryption. Configure an explicit ring for a shared development environment instead of relying on a relative working directory. Key-file changes require restarting the process.

New ciphertext has the format `fc1:<key-id>:<base64 SecretBox payload>`. The authenticated encrypted payload binds the version, key ID, channel ID and message text. Copying a new ciphertext into a different channel fails authentication. A proof of successful decoding is required; malformed ciphertext is not guessed to be plaintext.

## Upgrading an old installation

Before upgrading, stop all writers and take a recoverable backup of the database, uploaded files and key material. Test restoration into a separate disposable installation first.

Older releases used the first 32 UTF-8 bytes of `JWT_SECRET`, padded with ASCII `0` bytes to length 32, as their message key. Retain **that old derived 32-byte key** as `LEGACY_MESSAGE_ENCRYPTION_KEY` while upgrading; do not derive it from a newly rotated signing secret. Do not rotate or lose the previous signing secret until the old key has been securely retained and the migration verified. Store only the derived content key in the new legacy-key setting, encoded with standard base64.

Provision a new random content key ring and active ID before migration. From `backend`, using the installation's actual database and key settings:

```bash
# Audit/decrypt every live body without changing database rows.
python -m app.cli migrate-messages

# Apply only after the audit and a recovery test succeed; keep writers stopped.
python -m app.cli migrate-messages --apply

# A second audit should report no rows needing migration.
python -m app.cli migrate-messages
```

The operator command uses `app.crypto_migration.migrate_messages(fresh_session, apply=False)`. It returns counts only: scanned rows, legacy encrypted rows, explicit plaintext rows, old versioned-key rows, already-current rows, deleted rows skipped, rows that would change and rows actually changed.

The migration authenticates all live encrypted rows before making any changes and commits the entire applying run transactionally. A malformed body, missing key or wrong authenticated context aborts the migration with no rows changed. Dry runs modify no database records. A development dry run can initialize its development key file if no explicit ring exists.

Explicit `encrypted=False` historical replies are encrypted during migration. Rows marked encrypted but actually containing plaintext fail the audit; repair them only after an independent operator review of a backup. Never silently relabel a decryption failure. Deleted rows retain their deletion marker and are skipped.

After migration, restart the service with the new ring and verify registration, login, history and threaded messages. Remove `LEGACY_MESSAGE_ENCRYPTION_KEY` only after an audit finds no live unversioned content and recovery copies have an appropriate retention/key plan. Old backups may still require that key even when the live database does not.

## Content-key rotation

1. Add a newly generated random key under a new ID while retaining existing ring entries.
2. Change the active ID, then stop writers and back up. New content uses the new ID; retained keys decrypt old envelopes.
3. Run the audit and applying migration above. Old versioned envelopes are re-encrypted with the active ID and authenticated channel context.
4. Run another audit and test restored data. Retire an old key only when no required live record or retained backup needs it.

JWT rotation is independent once historical content has been migrated or its legacy key retained explicitly. Changing only `JWT_SECRET` does not alter the content ring or break new versioned history.

Back up content keys separately from the database with access controls appropriate to decryption material. Encryption offers no recovery from a lost key, and storing a database and all its keys together offers limited protection against a backup disclosure.

## Additional server-encrypted records in 0.5

The message key ring also protects native decision/document/calendar revision payloads and private Matrix service/session credentials. The existing `app.crypto_migration` command audits and rewrites native message rows; its report is not a complete inventory of these additional records. Retain older key IDs after a message migration until those credential/decision/document/calendar records and retained backups have also been migrated and verified. Removing a still-referenced server key can break Matrix authentication even though client-held message keys remain intact. Client Megolm rotation and a user's separate recovery key are independent of this server key-ring lifecycle.

## Account factors and customer SSO secrets

`ACCOUNT_SECURITY_KEY` is separate from JWT signing and the content key ring.
It protects native MFA factors, private account mail, short-lived OIDC PKCE
verifiers, customer workspace client secrets, remote OAuth access/refresh tokens
and DAV app-passwords. Preserve the same independent
key with database backups and restores. In local development its generated
private key file is durable state, not disposable cache. Do not regenerate it
while upgrading to customer-owned SSO or rotating an IdP client secret.

Customer secrets are context-bound to their workspace and never returned by the
configuration API. Rotate a customer secret through **Integrations → Sign-in**,
then test and enable the saved draft. Editing or disabling a provider invalidates
its previous scoped sessions. This is distinct from native signing-key rotation
and client-side Matrix key recovery. See [OIDC.md](OIDC.md).

Remote sync credentials are connection-context-bound encrypted values; the API
returns presence metadata only. Editing a selected remote resource clears the old
account grant and requires fresh authorization. Replacing OAuth app identity also
clears its old client secret. Pausing sync does not rotate its keys. SCIM directory
bearer credentials and invitation tokens are hashes, not decryptable secrets;
rotation replaces the accepted credential. Database restores retain these access
grants, so restored integration workers stay stopped until explicitly enabled.

Managed workforce accounts use distinct per-workspace Matrix identities and
separate client recovery keys. Their recovery keys are not part of the server's
database backup and cannot be replaced with an IdP login, a SCIM token or the
account-security key. Personal accounts retain their existing Matrix identity and
native password/MFA unlock. See [WORKFORCE_IDENTITY.md](WORKFORCE_IDENTITY.md).
