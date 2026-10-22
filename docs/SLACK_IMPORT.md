# Import a Slack archive

A workspace owner or administrator using a personal account opens **Integrations → Import from Slack**. Choose ordinary native storage or **End-to-end encrypted archive**, then select a standard ZIP containing root `users.json`, `channels.json`, and channel directories with daily `YYYY-MM-DD.json` lists. Slack controls the available export and included conversation types. FreeCollab does not log into Slack or fetch unexported conversations.

Preview creates no conversation history. Select up to **100 channels**, a new channel prefix, and optional mappings to active non-guest members of this workspace. Mapping explicitly adds those people to the destination rosters; it never creates accounts or grants access to outsiders. Unmapped people remain historical labels. All destinations are new private channels, with an idempotent reservation in encrypted mode; imports never merge into an unrelated existing channel.

## Historical authors, reactions and edits

Each message visibly states its original Slack label and timestamp. Its authenticated FreeCollab/Matrix sender is the importing administrator. Historical Slack identities do not become current accounts or receive editing rights. **Imported Slack history** in the message or thread expands to show original authors, source timestamps, historical reaction badges with exported counts/names, the full exported body and browsable earlier bodies.

The parser accepts ordinary `edited` metadata, supplied `edit_history`/`history` snapshot lists, and standard `message_changed` envelopes with `message` and `previous_message`. A normal Slack export may contain only the last edit timestamp: the UI states when earlier bodies were absent, rather than inventing them. Reaction statements and old edits remain export provenance, separate from current members' live reactions and edits. Duplicate snapshots are coalesced; malformed counts, timestamps and overlarge metadata are refused. At most 25 prior snapshots, 100 historical reaction kinds and 128 KB of complete historical metadata are retained per message.

Source thread references are preserved. Roots included in the export must precede their replies; nested/cyclic/future-root graphs are rejected. A missing root remains an explicitly historical top-level message. Slack mrkdwn stays text, without executable HTML. Membership and unrelated system events are skipped. Source timestamps remain source facts: Matrix events retain their authenticated actual import-time timestamps and sender.

## Native storage and resumable batches

Native message bodies and structured provenance use the installation's existing native content encryption, which the server can decrypt. Bundled files use ordinary server-readable native storage. Current native content/file governance checks include the historical text. The ZIP is sealed temporarily using the account-security envelope.

The UI starts a reviewed migration and commits at most **250 messages per batch**. The immutable archive digest, exact selected channels/prefix/mapping and monotonic sequence bind each operation. A retried committed sequence returns current progress, without creating duplicate channels/messages/files or thread counts. Current account/session, workspace administration, active mapped people and exact destination membership are checked before continuation; destination writes hold the normal channel fence against secure-enable and membership mutations. Changing the destination or its roster pauses further batches.

**Pause after this batch** stops after the in-flight batch. A network failure preserves earlier committed batches. Select the exact same ZIP and review the same choices to continue; a different archive or changed selection cannot silently replace that migration. The original atomic `/confirm` API remains available for existing clients.

Each batch commits its SQL changes together. Newly owned immutable blobs are tracked before I/O, including uncertain acknowledgements, and cleanup attempts every owned blob on rollback. Conditional-write collisions never erase an existing object. A later failed batch leaves earlier batches intact. Cleanup failures log only opaque blob IDs for operator reconciliation, preserving the original error.

ZIP bytes expire after one inactive hour; the default integration worker clears them. An expired partial migration retains sealed cursor/choice metadata so reselecting the original ZIP resumes its existing destinations. Discard immediately erases private ZIP/cursor copies; partial shared channels remain subject to ordinary retention. Completion erases the ZIP and private cursor and keeps source IDs/result receipts. Identity erasure immediately clears private material; provenance referenced by shared messages remains under shared retention. The operator retains responsibility for original archives and backups.

## End-to-end encrypted migration

This mode parses the ZIP **only on the trusted device**. The server receives opaque archive/choice/source commitments and authorized destination member IDs; the ZIP, source names, historical messages/reactions/edits and attachment bytes never go to native import endpoints. Rust Matrix crypto encrypts messages and history before transmission. Large historical metadata uses encrypted chunks, with their exact sender, source, indices and SHA-256 commitment verified on read. Long source bodies remain available in the history disclosure even when the chat preview is bounded.

Portable attachment bytes use the ordinary encrypted-file provider: encryption precedes upload, encrypted room metadata carries keys/names, and stable upload receipts prevent duplicate files after a lost acknowledgement. Every message/chunk has a deterministic Matrix transaction ID bound to the archive, choices, source channel and message. A confirmed message is reread and its sender/source commitments verified before advancing the checkpoint. Historical imports do not enqueue mention, webhook, email or workflow notifications.

Before import continues, mapped recipients must join and verify their devices using the normal encrypted-channel flow. The import can pause after reserving an empty destination so that this can be done. No imported content falls back to native plaintext storage when trust or encryption is unavailable. Future access and removal use current Matrix/channel membership and existing verified-device checks.

A bounded checkpoint stores only opaque event/root IDs, destination choices and counters in the account's AES-GCM browser vault. Resume requires the **exact ZIP, reviewed choices, original sign-in and original Matrix device**. The archive itself is not persisted there. Logout/account change uses normal vault erasure. Separately retained ZIPs and already decrypted/exported copies are outside application erasure. A browser storage failure or lost original device may require operator reconciliation of already imported channels; a fresh sign-in cannot silently adopt an old device checkpoint.

## Portable attachments and limits

Standard Slack exports often contain remote file references without binaries. FreeCollab **never fetches those URLs**. To include an authorized binary, place it at `files/SLACK_FILE_ID` and retain message metadata `id`, `name`, `mimetype`. Supported files become normal native or encrypted channel files according to the selected mode. Missing files get an explicit marker; private Slack URLs are not copied or requested.

Both modes enforce at most **50 MB compressed, 100 MB expanded, 10,000 ZIP entries, 1,000 source channels, 100,000 messages**, and 2,000 bundled file references totaling at most 100 MB. JSON entries are at most 8 MB. Source rosters/users are bounded at 5,000; mappings at 250, with encrypted destination requests limited to 100 explicitly mapped people. Three unfinished native ZIP jobs and ten encrypted reservation jobs per importer are allowed. These limits describe accepted input, not a throughput benchmark.

The device reads/inflates entries individually, with a bounded output allocation and incremental expanded-byte checks, rather than inflating the whole archive simultaneously. Parsed normalized records remain in memory within the declared expanded limit; very large archives need corresponding device memory and can take time. The maintained pinned `fflate` decoder handles raw DEFLATE; separate checks reject ZIP64, split/encrypted archives, unsafe paths, symlinks, duplicate entries/JSON keys, overlapping data, local/central discrepancies, excessive ratios, incorrect sizes and CRC failures. Native parsing uses Python ZIP CRC/size checks and equivalent strict path/content bounds. Bundled bytes are validated without network fetches.

```sh
cd backend
.venv/bin/python -m pytest tests/test_slack_import.py tests/test_slack_import_closure.py tests/test_slack_migration.py -q
cd ../frontend
node --test tests/slack-archive.test.mjs
cd ..
CHROMIUM_PATH=/usr/bin/chromium backend/.venv/bin/python frontend/tests/import-library-browser.py
CHROMIUM_PATH=/usr/bin/chromium backend/.venv/bin/python frontend/tests/slack-migration-browser.py
```

The encrypted browser proof owns a disposable pinned Synapse, API, database, keys and two actual Rust crypto devices. Its explicitly generous test-only Matrix burst budget tests a small bulk-migration workflow; it does not establish production provider throttling behavior or large-scale performance. No live Slack service or customer identity provider is required or claimed.
