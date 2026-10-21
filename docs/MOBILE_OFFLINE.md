# Mobile installation, offline library and private notifications

FreeCollab includes an installable web app, encrypted device drafts, an opted-in offline history/file library and durable encrypted text/attachment outboxes. Open the app online once to install its public application shell. Use the browser's **Install app** action; on iPhone/iPad use **Share → Add to Home Screen**. Service workers require HTTPS, with localhost supported for development. Vite development mode deliberately does not register a worker: test installation against a production build.

The **Mobile** action in the chat header opens installation, private drafts, the offline library, queued text/attachments and device notification settings, including after a fresh offline reload when no workspace directory is available. The layout supports a 320 px viewport and the user's appearance settings.

## Offline behaviour

- The worker caches only the public HTML shell, bundled scripts/styles/fonts and app icons from the same origin. It never caches authenticated API responses, Matrix requests, meeting access, private attachment downloads, export archives, guest capabilities or conversation history. Private history and files are available only through the separately opted-in encrypted Offline library described below; the shell itself retains no private directory or history.
- Drafts and queued text are stored in IndexedDB using browser Web Crypto AES-256-GCM. Every account/browser profile has its own nonextractable key, random 96-bit IVs and authenticated context binding the account, record kind and identifier. Message content, mention lists and bearer credentials do not appear as plaintext in vault records. Local routing identifiers and record kinds remain visible as metadata.
- This protects local stored values; a trusted origin running JavaScript can use its own browser key. This is not a password lock or a defence against compromised same-origin code, browser extensions with site access or an unlocked device. Browser storage eviction, private browsing restrictions and clearing site data can remove drafts. Keep the original window open if saving fails.
- Signing out or switching accounts removes the other account's vault records and keys. Each asynchronous read/write/send also checks the current account and exact sign-in against shared browser storage, preventing a stale tab from saving or sending under another account. Draft text is never copied into CacheStorage or localStorage.
- There are at most 100 drafts and 100 queued messages per account/browser profile, each within the server's text limit. The draft list shows opaque channel identifiers and permits editing or discarding drafts. Chosen channel names/history are retained separately only after explicit Offline library enrollment.

Sending offline first stores the encrypted queue entry durably. Reconnection automatically sends eligible entries only after verifying the current server account, current channel membership, writable conversation state and current encryption mode. The final server/native or Matrix send repeats its own authorization checks. Queue entries older than seven days, or bound to a different sign-in, require review. A removed membership, blocked conversation or encryption-mode change also stops automatic sending. Move an entry back to a draft, check conversation history, then send deliberately from the authorized channel.

Native channels use an atomic server receipt keyed by account and durable client message identifier. The receipt, encrypted message and thread changes commit together; a retry after a lost acknowledgement returns the same message. An identifier cannot be reused for different content or another channel. Content retention preserves a minimal receipt tombstone after deleting the message, so a lost-acknowledgement retry cannot recreate purged content; it requires review instead. End-to-end encrypted channels use the Matrix client's stable transaction identifier. Automatic retry is bound to the original native sign-in, which maintains the same Matrix device. Sign-in changes require review because a new Matrix device has a separate transaction namespace. Drafts for end-to-end encrypted channels remain locally encrypted and never enter a server plaintext fallback.

## Opt-in offline history and files

In **Mobile & offline → Offline library**, explicitly save the current channel.
The app retains up to the latest 200 readable messages from each of ten chosen
channels. Only normal authorized API/Matrix readers supply these snapshots;
locked events are excluded. Already decrypted secure content is re-encrypted
under the browser's nonextractable AES-GCM key. Embedded components cannot enroll
channels, read or persist the standalone app's offline library.

Choose individual current files to save, including authenticated client-decrypted
Matrix attachments. Keep up to twenty files, each at most 5 MiB. Cached files and
the attachment outbox together allow 25 MiB of logical file bytes. File names,
channel titles, message bodies, MIME types, file bytes and sign-in fingerprints
are inside authenticated ciphertext, with origin/account/kind/id bound to each
record. Opaque routing identifiers and record kinds remain visible. The service
worker continues to cache **only public assets**; private API responses and media
never enter CacheStorage.

Copies expire after seven days and are usable only under the original sign-in.
A new sign-in removes old history/file copies on access. Sign-out/account changes
erase all vault records and keys. Removing a saved channel erases its saved files.
When online, opening the library verifies the current account, membership,
encryption mode and secure device access, refreshes messages, and reconciles file
IDs with the current source. Known revoked/deleted channels and files are purged;
a failed online check never displays stale history or files. Reconnection also
starts this revalidation. A currently open conversation updates its opted-in
snapshot after an authorized history refresh.

Offline reading cannot check a server-side revocation, deletion or retention
change until reconnection. The interface explicitly labels the copy and date.
An unlocked trusted browser or same-origin code can use its own key; this is not
an independent password lock. Copies explicitly downloaded from the library leave
browser storage and are not erased from the operating system on sign-out.
Clearing site data or storage eviction can remove all offline data.

## Encrypted attachment outbox

For an enrolled channel, choose **Queue a file privately**. This works on the
saved channel after a fresh offline reload, without loading the server directory.
Up to ten files of at most 5 MiB each are encrypted durably before any transfer.
The existing channel file picker also persists an offline upload for an enrolled
channel. New versions preserve their original document ID.

Transfers run while the application is open; the service worker does not upload
private files in the background. Reconnection checks the exact original sign-in,
seven-day age, live account,
current channel membership, encryption mode and secure device before transfer.
Version uploads additionally recheck the original document in that channel.
Native uploads use the server's existing atomic file receipt and unchanged
idempotency key. Secure uploads retain their Matrix file request/transaction ID
and normal client-side encryption; they never fall back to plaintext. Ambiguous
network outcomes leave the durable ID for retry. Changed authority or encryption
requires review and deliberate removal/reselection, never blind retry. Other
authorized recipients may have already received an acknowledged upload even if
its local acknowledgement was lost. Server/Matrix retention is independent.

Offline channel creation is not implemented. An online browser indicator alone
does not guarantee connectivity; failed network attempts remain queued.
Authorization failures require review instead of retrying blindly.

## Native mobile notifications

Android FCM and iOS APNs use the separate opt-in [native push setup](NATIVE_PUSH.md)
and customer-signed native builds. Their operator-owned credentials, delivered
device proof and current session/channel checks are independent of browser VAPID.
Generic incoming-call alerts can open current meeting controls; they do not
automatically answer, enable media or promise ringing while terminated. See
[NATIVE_CLIENTS.md](NATIVE_CLIENTS.md) for platform packaging and acceptance limits.
Native push does not send queued private attachments from a closed application.

## Operator Web Push setup

Browser Web Push is disabled by default. Each browser must opt in, allow notifications, and prove possession of the browser subscription through an encrypted push challenge. Registering an endpoint does not activate it. The authenticated API never returns the challenge or browser endpoint/key material, and a different account or sign-in cannot confirm it. Keep the app open while enabling notifications.

Generate an independent operator VAPID key with the existing backend environment:

```sh
backend/.venv/bin/python scripts/push-keygen.py /private/secrets/freecollab-vapid.pem
```

The command creates a new P-256 PKCS8 PEM file with mode `0600`, refuses existing paths and never prints the key. VAPID is owned by this installation; no customer identity-provider credential is needed. Store the PEM in the deployment secret manager, or mount the private file and load its contents through the process launcher. Supply these backend and worker settings:

| Setting | Value |
| --- | --- |
| `PUSH_ENABLED` | `true` after the key and subject are installed |
| `PUSH_VAPID_PRIVATE_KEY` | Complete multiline PEM contents, from a secret store |
| `PUSH_VAPID_SUBJECT` | Operator contact URI, for example `mailto:operations@example.com` |

The API and integration worker must use the same VAPID key, account-security key and database. Do not put the private key in a `VITE_*` setting, committed environment file, browser build, command-line argument or public diagnostics. A mounted file path is not the value of `PUSH_VAPID_PRIVATE_KEY`: the launcher must read the file privately into the process setting. A missing/invalid signing key or contact URI leaves the authenticated configuration endpoint disabled.

Run the existing integration worker:

```sh
cd backend
.venv/bin/python -m app.services.integration_worker
```

The browser provider's public HTTPS subscription endpoint is contacted only after the device opt-in flow. Outbound traffic uses the existing public-HTTPS transport: fresh public-address validation, pinned connections, verified TLS hostname, bounded response/deadline and no redirects or direct fallback after a proxy failure. HTTP, private/loopback addresses, nonstandard ports, credentials in URLs and fragments are rejected. Subscription capabilities are encrypted in the server database; the worker retains only the original session digest and signed scope metadata.

Before every activity dispatch, the worker verifies the original live session, account auth version, current provider/signed workspace scope, notification preference, current channel membership and unread activity. The payload is only an activity type and an opaque delivery identifier. Sender names, channel titles, message text, attachment URLs, bearer credentials and recovery keys are absent. A confirmation push carries only its short-lived one-use device challenge. The operating system displays generic notifications. Clicking one opens the authenticated activity centre.

There are at most ten saved notification subscriptions per account. Removing a device deletes its server capability and pending deliveries, preventing further sends from this installation. Browser/operating-system permission remains controlled through that browser's settings. An expired endpoint is disabled; transient failures receive at most five attempts with bounded backoff. Generic notifications may be delivered more than once when a provider acknowledgement is lost. Opening the app always loads currently authorized activity.

Rotating the VAPID key requires browsers to enable notifications again. A sign-out, expired session, account revocation, provider disable or removed workspace scope prevents future dispatch for the original subscription. Disabling `PUSH_ENABLED` stops both registration and worker dispatch. Serve `service-worker.js` with revalidation, and keep the public production assets available during updates.

## Repeatable verification

```sh
backend/.venv/bin/python -m pytest backend/tests/test_push.py -q
CHROMIUM_PATH=/usr/bin/chromium backend/.venv/bin/python frontend/tests/mobile-offline-browser.py
node --test frontend/tests/offline-library.test.mjs
CHROMIUM_PATH=/usr/bin/chromium backend/.venv/bin/python frontend/tests/offline-library-browser.py
```

The API suite covers consent ownership, one-use challenge races, session revocation while confirmation is in flight, signed workspace scope, membership/read/preferences checks, retries, native message idempotency/concurrency, encryption fences and thread counts. It also runs a private synthetic HTTPS push receiver through a real CONNECT tunnel, independently decrypts standard `aes128gcm` Web Push and verifies the VAPID signature. This proves the protocol and transport without submitting anything to a live push provider.

The browser proof builds the complete production app into an owned temporary directory and uses Chromium's actual service worker, CacheStorage, IndexedDB and Web Crypto against a disposable API/database. It verifies offline reload and draft editing, encrypted queue persistence, reconnect, lost acknowledgement replay, removed membership, logout/two-account tabs and 320 px layout. It removes its services, fixture keys, database and browser profiles. Its report and screenshot are stored in the workspace's private `.freecollab/proof-results` directory. It does not claim delivery through a real browser push provider; that final device/provider smoke test requires the operator's configured production Web Push service and a user's explicit browser opt-in.

The offline-library browser proof uses the complete production build, real
Chromium service worker, IndexedDB, Web Crypto and a disposable native API. It
checks explicit opt-in, encrypted metadata/bytes, actual offline reload/read and
binary download, durable attachment reload, lost-ack receipt replay, current
membership revocation, local purge, sign-out erasure and a 320 px layout. The
focused policy tests additionally exercise changed sign-in/encryption, expiry,
size bounds and authenticated source metadata. These do not replace a physical
mobile-device storage-eviction test or a live Matrix provider acceptance run.
