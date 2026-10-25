# Client encryption in FreeCollab 0.5

The new private-channel path uses Matrix JS SDK 44.0.0 and its Rust crypto implementation. Synapse runs privately behind FreeCollab's authenticated Matrix gateway. This is an implementation of client-side encryption; it has not received an independent review of the FreeCollab integration.

Existing channels retain their original server-encrypted history. They are labelled **Server-encrypted messages**. Only new empty private channels can become **End-to-end encrypted**. Preparing a secure channel durably fences the legacy message/file endpoints; a failed setup never falls back to sending plaintext. The installer leaves Matrix disabled unless explicitly selected.

## Start a new local installation

```bash
python scripts/pilot-init.py --secure
docker compose --env-file pilot.env -p freecollab --profile secure --profile mail up --build -d --wait
```

The initializer generates independent signing, legacy content, account-factor and Matrix registration secrets. It refuses to overwrite an existing configuration. The `matrix` volume contains the homeserver configuration, signing key, SQLite database and encrypted media. There is no published Matrix port, public registration, public directory or federation listener. The registration secret is a service credential, never a message-decryption key.

Choose the Matrix server name before creating accounts and keep it unchanged. The supplied Synapse SQLite profile is intended for a small, single-server installation; use the bootstrap's PostgreSQL settings and test the database/backup procedure before scaling it. The supported FreeCollab API still uses one replica. Production also requires HTTPS/WSS and the SMTP/account settings in [AUTH_SESSIONS.md](AUTH_SESSIONS.md).

The private homeserver initializer supplies a bounded per-user `rc_message`
allowance of 10 events/second with a burst of 100, preserving any explicit
operator setting. Encrypted Yjs changes, meeting negotiation, heartbeats and
captions share this allowance with chat. Synapse's human-message default of
0.2 events/second and a burst of 10 can throttle these ordinary combined flows.
An independently managed homeserver needs an appropriate equivalent policy;
size it with actual clients and capacity. Registration, current gateway access,
room membership and application limits remain enforced.

For an existing pilot, retain its content keys and database. Add an independent `ACCOUNT_SECURITY_KEY` as described in the account runbook. Generate a new random Matrix registration secret, add `MATRIX_ENABLED=true`, a stable `MATRIX_SERVER_NAME` and `MATRIX_REGISTRATION_SHARED_SECRET` to the private configuration, then start the `secure` profile. Existing history is not automatically re-encrypted. Create new secure channels for new content.

## First encrypted conversation

1. Create a channel with **End-to-end encrypted** selected. Secure DMs are enabled automatically on configured installations.
2. Invite the intended participant and let that person explicitly join on their device.
3. Open the encryption indicator, choose **Devices**, and compare fingerprints through a separate trusted channel. Each participant verifies the other's device.
4. Send only after the required recipients have joined and have verified devices. Missing trust is shown as a pending condition; there is no plaintext fallback.
5. Set up and save your separate recovery key. Keep it private and outside the browser profile.

## Device setup and recovery

Open **Account settings → Manage encryption devices & recovery**, or the encryption indicator on a secure channel. Private device keys are created by Rust crypto in the browser. Its IndexedDB store uses a browser-held wrapping key; message keys and the recovery key are never placed in application localStorage or sent in plaintext to FreeCollab.

Create a recovery key and save it separately. Secret storage and the server's key backup are encrypted using client-held key material. Signing in, verifying email, changing a password or using an MFA backup code does not recover encrypted history. A new device needs verified trust or the separate encryption recovery key.

Verify another device's fingerprint through a separate trusted channel, then enter that fingerprint in the device dialog. The SDK restricts sharing to verified devices. A server-controlled replacement key is not automatically accepted as a trusted device. Account session revocation also invalidates the associated Matrix gateway token, including outstanding sync responses.

To remove a lost encryption device, open **Devices**, select one of your own devices and choose **Revoke lost device**. Confirm your password and, when enabled, an authentication or account recovery code. The homeserver deletes that device before FreeCollab revokes its linked login sessions. Recipient snapshots include persisted device-revocation records; sending waits while the crypto engine still has a stale revoked recipient, and future sends use a new outbound session. Removing another device leaves your current session active. Removing this browser signs it out. Keys and content already stored on a removed device cannot be erased remotely.

The browser remains a trusted endpoint. Malicious code running in its origin can use unlocked keys; browser-store encryption does not prevent that. A compromised server that changes the web client is outside the protection of ciphertext at rest. The integration does not claim a security audit or every property of MLS.

## Protected content and visible routing information

| Data | Secure-channel handling |
| --- | --- |
| Messages, threads and edits | Encrypted Matrix room events; the native chat endpoints reject these channels. |
| Reactions and read positions | Encrypted application events. |
| Channel title | Encrypted metadata event; the application database uses a generic placeholder. |
| File bytes | Matrix v2 encrypted attachments with client-side integrity verification. |
| File name, MIME type, document/version information and file key | Inside the encrypted attachment metadata event. Preview and PDF rendering happen locally. |
| Decision text and revision fields | Encrypted application events; backend records hold opaque event references and revision permissions. |
| Pins and private bookmarks | Opaque event references; the client decrypts the referenced content. Bookmark ownership is private at the API, but the server can see that a reference was saved. |
| Search | Authorized history is decrypted and searched locally. No search words or plaintext search index are sent to the server. |

Accounts, device public keys, channel/workspace membership, event identifiers, timestamps, sizes and traffic are visible to the service. Generic notification routing can additionally disclose mentioned recipient IDs and a thread's opaque identifier. It sends no decrypted message text, channel title, filename or decision content. Encrypting content does not conceal the communication graph.

New members receive history from joining by default. A removed member cannot fetch new room content through the gateway. Membership changes are applied to the private homeserver, and short-lived Megolm outbound sessions limit future key reuse. This does not revoke content or keys already downloaded. Megolm history backup is not the same as MLS forward secrecy for retained history keys.

## Operations and verification

Back up the application database, content files, all independent secrets and the Matrix volume together using the v2 cold-backup scripts. Restoring only FreeCollab's PostgreSQL database does not restore Matrix rooms or encrypted media. Server backup alone does not replace users' recovery keys. See [OPERATIONS.md](OPERATIONS.md).

The validation record lists the tests actually completed. The separate protocol proof runs two independent Chromium contexts with Rust crypto, verified fingerprints and a wire assertion that the sent event is `m.room.encrypted` without the original message text. The integrated gateway, recovery, membership and file tests are recorded separately; a successful build alone does not establish their behavior.
