# Encrypted work across independent installations

The **Shared work** tab in Workspace tools adds tasks, calendar series and text
documents to an active cross-installation Connect channel. It uses the channel's
existing Matrix E2EE room and verified devices. Both organizations keep their own
accounts, SSO, workspace membership, administrators and databases. A remote
participant is never enrolled as a local workspace member. Local directory groups
and group identifiers remain under their installation's authority. Cross-installation
`@` references bind verified Matrix participants or exact typed room resources;
they do not synchronize group directories or inherit remote SSO grants.

This is a shared, client-derived view. Negotiated version 2 adds projects, project-linked tasks, feeds, decisions, collections, file links, resource comments and reviewed device-owned workflows in the same encrypted room. Existing installation-local records keep their own IDs; cross-installation resources open through their verified room namespace. Provider synchronization, credentials and administrative settings remain local. See [encrypted work](ENCRYPTED_WORK.md) for the version 2 contract and the document workspace adapter.

## Customer setup

Complete [Connect federation setup](CONNECT_FEDERATION.md) on both installations:
pin installation signing keys and Matrix domains, configure verified HTTPS
federation, accept the bilateral workspace invitation, grant participants, and
verify their encryption devices. The feature remains unavailable without an
active current connection and an explicit participant grant. An administrator's
permission to manage a connection does not give that administrator content access.

There is no extra shared-work server credential, central identity service,
plaintext relay or separate encryption key to configure. The additive SQL model
is initialized through the existing federation router import. Back up each
installation's normal database and Matrix storage/identity, and retain participant
device keys through the supported device backup/recovery process.

## What participants can do

* Create and edit tasks from either installation, with title, description,
  status, priority, due date, checklist and an explicitly selected current
  participant. Assignees carry full Matrix identities, including homeserver;
  foreign numeric or local-only user IDs are not reinterpreted. Version 2 `/` tools and `@` selection use the same input flow with exact verified global participant/resource IDs. Document links use Yjs relative anchors, so concurrent text insertion moves links without resolving labels as identities.
* Create and edit calendar events, including IANA time zones, all-day dates,
  finite daily/weekly/monthly recurrence and skipped occurrence exceptions.
  Occurrence calculations use the same DST-aware calendar model as native work.
  Saved calendar data can be exported by the participant as ICS.
* Create text/Markdown documents and edit their title/body on either installation.
  Actual Yjs updates merge concurrent text changes. Save publishes the current
  change; this view does not publish every keystroke. Document creation metadata
  is immutable. Document archival/deletion is outside this protocol version.

Task and calendar edits form a revision graph. Each edit names its verified
parent event IDs. Concurrent heads remain visibly competing versions until the
participant explicitly reviews and merges them. Missing, cyclic or invalid
ancestry blocks editing instead of silently selecting it as the current value.
The merge names up to eight current heads. A stale editor must refresh and review
the newer heads before saving.

Calendar provider identities, external attendee addresses, external invitations,
native provider synchronization and public feeds are not replicated by this
encrypted room protocol. Shared events require `external_uid: null` and reject
unknown top-level fields. ICS is a deliberate participant export, not an automatic
server or provider connection.

## Storage, transport and verification

Resource identifiers are namespaced by the current connection, a hash of the
actual Matrix creator identity, and a UUID. Every content revision, document
update and fragment is sent as an encrypted Matrix event through the current
channel send path. Existing current membership/recipient/device/revocation fences
apply; encrypted work never falls back to a native plaintext send.

Each installation stores a small independent **opaque pointer index**. A row has
only its local sequence, connection ID, Matrix event ID, full Matrix sender ID,
local publisher ID and publication time. Publication requires the current local
grant and verifies that the referenced event is encrypted, in the exact room and
authored by that same local sender. The API rechecks the actor and grant after
Matrix I/O under the channel write lock. No task fields, calendar fields, document
text, Yjs plaintext, Matrix bearer tokens or decryption keys enter these indexes.

The signed peer control plane exchanges bounded index pages, authenticated to
the already pinned installation. It rechecks current bilateral authority and
fails closed when the peer is unavailable. Each browser discovers local and peer
pointers, fetches the actual events from its own homeserver and decrypts them on
its device. It checks the exact room/event/sender, encryption provenance, matching
device keys, manual/device verification, connection namespace and content schema.
Fresh event fetches also recheck redaction. Historical content authored by an
authorized former participant remains historical content; removal is not a
retroactive key recall.

An individual unverifiable/malformed candidate is quarantined without applying
its content. Its opaque pointer remains indexed and is retried on refresh, and
the UI displays a partial-history warning and quarantine count. Such a candidate
cannot nominate arbitrary unrelated resource IDs as invalid. Authenticated
recognized revisions with missing/cyclic ancestry remain visible as incomplete,
read-only resources; the client does not silently drop them and permit a blind
overwrite. Other verified resources remain usable. A later recovered task or
calendar revision can introduce an explicit competing head; Yjs deltas preserve
previously unseen concurrent operations. This view represents available verified
history, not guaranteed complete discovery.

Current account, bilateral grant, room, own-device revocation, signed-index and
transport-policy failures lock the complete view. Candidate isolation never
bypasses those authorization checks.

Document updates name their creation event. Large changes use encrypted chunks
with a final SHA-256 commitment to the complete update, exact part order and the
same verified sender. A partial publication cannot become a committed document
update. Reconstruction runs in a disposable Web Worker, with bounded input,
strict plain-text/Markdown structure and a computation deadline. No remote worker
or content service is used.

## Consistency and availability limits

Matrix delivery and SQL pointer insertion are separate systems. A successful
Matrix acknowledgement is followed by an opaque pointer publication. If the API
receipt is lost, the client retries the same event ID, which is idempotent. A
small IndexedDB outbox retains only event references and session/owner binding;
it retains no work content or raw bearer token. Retries require the same current
signed-in session and current channel grant. Signing out clears the in-memory retry view; another signed-in session cannot replay its session-bound receipts.

This is not an atomic cross-database transaction or an exactly-once delivery
guarantee. A browser crash before the Matrix acknowledgement is known, lost local
retry storage, lost source database/pointers or incomplete history/key recovery
can leave an encrypted event undiscoverable or an item unavailable. Back up the
opaque indexes together with the installation. Unsaved editor text remains in
memory and is cleared when the account/channel changes; navigation prompts before
discarding it.

Supported bounds per connection are 2,000 indexed events on each installation,
50 pointers per signed page, 4,000 combined client-discovered events and 8 MiB of
decrypted event JSON per view. A document supports 500 committed updates, 50,000
body characters, 200 title characters and 512 KiB of reconstructed Yjs state.
An individual update is at most 256 KiB, split into 16 KiB encrypted fragments.
The worker has 10 seconds to load its bundled module and 1.5 seconds to reconstruct
the state. These are explicit supported bounds, not a production load benchmark
or an absolute browser heap quota. Reaching a bound blocks further processing
with an error; background compaction/history pruning is not implemented.

An unavailable peer, unverified author device, missing decryption key, redacted
required event or failed current grant can prevent reconstruction. Restoring
availability or verified device keys is necessary; the server cannot recover
plaintext. Previously downloaded content and recipient-held keys cannot be
recalled. Both customer operators remain trusted to enforce their own roster and
run their homeserver honestly; this does not defeat a malicious peer operator or
a participant who deliberately redistributes content.

This version also excludes explicitly revoked author-device keys when rebuilding
work history. Historical events from such a removed device can become quarantined
and a resource can become incomplete, even when ciphertext and previously
downloaded copies remain. There is no separate event-time device-trust archive or
operator bypass for this history. Plan continuity and device offboarding with this
strict availability boundary in mind.

## Verification

```sh
cd backend
.venv/bin/python -m pytest tests/test_connect_federation.py tests/test_federated_work.py -q
cd ../frontend
node --test tests/federated-work.test.mjs tests/federated-work-ui.test.mjs
cd ..
CHROMIUM_PATH=/usr/bin/chromium backend/.venv/bin/python frontend/tests/connect-federation-browser.py
```

The focused backend suite uses two independent databases and real signed peer
envelopes with a synthetic Matrix control fixture. The pure frontend suite uses
actual Yjs replicas for concurrent edits and validates schema, creator namespace,
explicit conflict ancestry, DST recurrence and parser bounds.

The browser proof separately launches two disposable Synapse homeservers with
their own keys/databases, verified fixture TLS federation, isolated product APIs
and actual Rust browser Matrix encryption. It covers bidirectional messages and
media, shared task/calendar/document creation and peer edits, concurrent Yjs
convergence, fragment commitments, explicit concurrent task merges, actual React
UI task edits and Workspace tools navigation, native-session/grant isolation, opaque
wire/index storage and revocation. It records critical source hashes at startup
and verifies them unchanged at completion. Results are local, untracked proof
artifacts in `.freecollab/proof-results/connect-federation-browser-013.json`.
Fixture-only synthetic host routing and private federation addresses do not
change production network validation. Public DNS/TLS, customer IdP login, physical
device acceptance and production capacity remain customer deployment checks.
