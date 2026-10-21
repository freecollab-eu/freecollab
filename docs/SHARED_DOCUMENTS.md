# Shared documents

The **Work → Documents** tab holds channel pages with concurrent text editing,
authenticated comments, named revisions, archive/restore and local Markdown
export. The editor uses pinned **Yjs 13.6.27** text CRDTs. Different browsers can
edit from the same starting state, reconnect in either order and merge both
sets of changes. The one-second HTTP synchronization transports append-only Yjs
updates; it does not replace a whole document with the last received text.

## Access and storage

Every read checks current channel and workspace membership. Each mutation takes
the channel writer lock, rechecks the actual signed-in token and current
membership, and honors direct-message write restrictions. Encrypted-event
validation is followed by another token and membership check before writing.
The same writer lock fences a native write against secure-channel activation.
An author or channel administrator can archive a document; other channel members
can edit an active page. Comment text and authorship are immutable. Only its
author or a channel administrator can change the resolved state, with a version
check to reject stale requests.

In native channels, Yjs updates, title summaries, comment text and named snapshot
content use the server message key ring. Encrypted envelopes bind the channel,
document, entry identifier and content kind, so copying an envelope to a different
entry fails verification. Native content is available to the authorized server;
this is encryption at rest.

In secure channels, the browser encrypts updates, titles, comments and snapshot
content as Matrix events. The application database contains routing metadata and
opaque Matrix event identifiers. It receives no native Yjs payload, comment text
or document title. The server verifies the event's encrypted room and authenticated
sender. Before applying decrypted content, the browser additionally verifies the
room, event type, author, document ID, entry ID, redaction state and snapshot
sequence. Larger updates use encrypted 16 KiB part events and an encrypted
manifest; each part's identity, ordering, sender and SHA-256 commitment must match.
There is no plaintext fallback when encrypted content cannot be verified.

The server can see document existence, participants, times, counts, archive state,
comment resolution state and event identifiers. Matrix retention and device-key
recovery are separate from application database retention. Removing an opaque
reference does not promise erasure of Matrix copies or exported files.

## History, limits and recovery

Updates are immutable and uniquely identified. An identical retry from the same
author is idempotent; reuse with different content or authorship is rejected.
Clients consume contiguous sequence pages and never advance their read cursor
from a POST acknowledgement. This prevents concurrent updates from being skipped.
Malformed, unsupported, oversized or misidentified updates stop editing and show
an explicit error before they can modify the verified document.

Named revisions store an immutable full Yjs state and the exact synchronized
sequence it covers. A concurrent edit makes a stale snapshot request fail rather
than attaching an inaccurate sequence. Restoring a revision creates new text
edits; earlier updates, later updates and comments remain available. Full-state
snapshots provide explicit state compaction without deleting the immutable update
log. This release replays the bounded update log on open and does not implement
destructive history compaction.

Limits are deliberately bounded: 50 retained documents per channel, 10,000 updates
per document, 32 MiB of native update bytes, 256 KiB per update, 512 KiB per named
snapshot, 100 comments and 100 named revisions per document. Archived documents
still count. Titles allow 200 characters, document text 50,000 characters, comments
2,000 characters and revision names 100 characters. Secure update storage counts
opaque references; decrypted browser limits still apply. A reached limit rejects
the next write while retaining earlier content. Export or create a continuation
document rather than silently discarding history.

Temporary network failures preserve editing and pending updates in the open
browser's memory, with automatic retry and an explicit unsynchronized count.
Closing or navigating away while updates are pending warns before discarding
them. Shared-document drafts are not persisted to plaintext browser storage;
reloading or discarding an unsynchronized page loses those memory-only edits.
Loss of authorization or an integrity failure locks editing. Markdown export is
created locally from the current browser text. The reading view renders safe
plain text; embedded HTML, rich-text objects and Office document editing are
outside this feature.

## Transport and validation

Routes are rooted at `/channels/{channel_id}/shared-documents` and return
`Cache-Control: no-store`. Document creation and update writes accept either
`update_base64` plus optional `title`, or only `matrix_event_id` for secure content.
Comments and snapshots use the same mutually exclusive native/Matrix contract.
`GET /{document_id}/updates?after_sequence=N&limit=200` returns ordered items,
current document metadata, `has_more` and `next_sequence`. Archive and comment
resolution use `expected_version` compare-and-swap. A successful new-document
transaction queues a metadata-only `document_created` workflow event exactly once.

Backend regressions: `backend/.venv/bin/python -m pytest
backend/tests/test_shared_documents.py -q` from the repository root with
`PYTHONPATH=backend`, or run pytest from `backend/`. Client boundary regressions:
`node --test tests/shared-documents.test.mjs` from `frontend/`. The private Chromium proof is
`CHROMIUM_PATH=/usr/bin/chromium backend/.venv/bin/python
frontend/tests/shared-documents-browser.py`. Its temporary API, Vite server,
accounts, SQLite database and cryptographic keys are isolated and removed after
the run. For `--secure`, start a disposable test homeserver and explicitly set
`MATRIX_INTERNAL_URL`, `MATRIX_SERVER_NAME` and
`MATRIX_REGISTRATION_SHARED_SECRET` for that fixture. The harness requires all
three values and creates isolated test identities. The caller owns cleanup of
the test homeserver and its volume. This additional
proof verifies two trusted devices, chunked encrypted updates and snapshots,
authenticated comments, document reconstruction, project/task peer edits and
revision history, an encrypted approval note completed by the real workflow
worker, opaque application API/database records and actual
`m.room.encrypted` wire events. Native tests alone do not establish Matrix
encryption.
