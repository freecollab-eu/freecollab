# FreeCollab 0.11 — product scope and operating boundaries

Version 0.11 expands daily collaboration, customer-owned integrations and
deployment options across the ten areas below. Existing workspaces, verified
Matrix devices, personal languages, themes and trusted-origin embedding remain
part of the product. Customers supply their own identity providers, provider
applications, infrastructure and credentials. The public website/demo is a
separate installation.

This page describes supported behavior and where to use it. Test results and
deployment evidence belong in [VALIDATION.md](VALIDATION.md); configuration being
saved does not establish a successful connection to a live customer provider.

## 1. Daily chat, group conversations and follow-up

Open **Direct messages → New group conversation** to create a private group with
two to nine other visible workspace members. The roster is fixed; participants
can leave, and a different
roster creates a different conversation. Existing workspace, block and current
membership rules apply. With Matrix enabled, new group conversations require the
normal encrypted-channel/device flow.

Use **Schedule & reminders** beside the composer or a message's reminder action.
Native scheduled messages use durable SQL jobs and the integration worker.
Delivery checks the original session, current channel access, encryption mode,
mentions and native content policy again. Schedule within seven days and before
the original sign-in expires; signing out or losing authority cancels delivery.
Personal reminders support snooze and completion, with a private, generic
notification that resolves the current target access when opened. Saved messages
can also be marked complete without changing another person's saved state.

Secure scheduled messages remain in the trusted device's AES-GCM encrypted local
outbox. The app must stay connected with its sign-in active for delivery; this is
device scheduling, with no promise of unattended server or mobile background
delivery. See [MOBILE_OFFLINE.md](MOBILE_OFFLINE.md) and the scheduling panel's
delivery status.

## 2. Desktop and mobile clients

Build the Electron client under `clients/desktop` or the Capacitor Android/iOS
projects under `clients/mobile`. Desktop users select an HTTPS installation;
mobile builds target the operator's configured HTTPS origin. **Sign in with
browser** uses explicit approval and a one-use S256 PKCE exchange, preserving the
source authentication and exact customer workspace scope. Each native profile
verifies its own Matrix device; browser recovery keys are not transferred.

Source projects and build workflows are available. Signing, notarization,
production mobile provisioning, app-store submission and physical-device
acceptance remain the distributor's work. Native FCM/APNs background push,
background call delivery and background recording are not implemented by the
mobile shell. See [NATIVE_CLIENTS.md](NATIVE_CLIENTS.md).

## 3. Encrypted shared channels between organizations

Open **Integrations → Shared channels**. Two workspaces on the same installation
can consent to a dedicated, initially empty encrypted channel. Each administrator
grants seats only to their own existing members. Accepting an invitation does not
automatically grant the recipient administrator a content seat, and ordinary
workspace membership does not grant access to the partner's workspace.

Connect requires the existing private Matrix service and has no plaintext
fallback. Either organization can revoke the connection; remote provisioning or
cleanup can remain visibly pending during a homeserver outage. Previously
downloaded content cannot be recalled. Independent-installation federation is
not enabled: capabilities explicitly report `cross_installation: false`.
See [CONNECT.md](CONNECT.md).

## 4. Installed apps, scoped bots and external commands

Personal workspace/channel administrators install manifests in **Integrations →
Apps**. Channel members use installed commands and view cards in **Work → Apps &
automation**. Installations have explicit scopes, selected native channels,
current grant generations and dedicated bot identities without user sessions or
administrator tokens.

Five recipes support signed JSON/n8n commands, GitHub issues, Jira Cloud issues,
Zendesk tickets and Salesforce cases. A runnable signed receiver demonstrates
HMAC verification, stable event deduplication and signed card callbacks. Customer
tokens or gateways provide the external authority; the recipes do not install a
vendor OAuth application or refresh those tokens. Provider create requests with
ambiguous outcomes become `uncertain` rather than automatically duplicating a
possible effect. Callbacks and bot cards contain bounded structured text, not
remote executable UI. See [APP_PLATFORM.md](APP_PLATFORM.md).

## 5. Consented AI, private recaps and approved automation

Use **Work → Apps & automation** for cited private summaries, workflow drafts,
cross-app action proposals and explicitly consented recurring private recaps.
The customer configures the existing compatible AI provider. Immediate requests
select and disclose bounded sources; source revisions, membership, consent and
provider grants are checked again before retaining or using a result.

Publication, workflow installation and external actions each require separate
human review. Generated actions can address only explicitly selected installed
commands and unchanged grants. Recaps use at most eight recent new native
messages in their bounded window and remain private until reviewed. They are not
complete-history digests or automatically published meeting notes.

The existing visual workflow builder supports metadata events, current-member
approvals, quorum/deadlines, bounded reminders/escalation, notifications and
native task creation. Installed app commands, callbacks, recurring recaps and
these persistent AI drafts reject encrypted channels and direct conversations.
Existing encrypted workflow approvals remain metadata-only with client-encrypted
optional notes. Existing secure Copilot requests can disclose selected
decrypted content only through a separate trusted-device consent; that disclosed
copy leaves the E2EE boundary and does not become recurring server automation.
See [APP_PLATFORM.md](APP_PLATFORM.md), [COPILOT.md](COPILOT.md) and
[WORKFLOWS.md](WORKFLOWS.md).

## 6. Broader current-source and personal external search

Open **Search all history**. Current-source search extends messages, filenames
and decisions with document title/body, project/task descriptions, checklists,
activity posts/polls and supported file bodies. Filters and source references
lead back to the current authorized resource. The browser matches the query;
native source extraction is bounded and does not create a persistent plaintext
index. Unsupported or oversized content produces an incomplete-results notice.
Secure sources are verified, decrypted and searched on the trusted device.

The separate external-search form accepts an explicit query for one personal
Google, Microsoft or custom connector delegation. The provider's current ACL and
configured collection boundary are checked again on resolution. These grants
are personal and workspace-bound; administrators cannot borrow another person's
results. The search connector does not provide its own OAuth authorization or
refresh flow: the customer's identity integration supplies/replaces its personal
read-only token. See [ENTERPRISE_SEARCH.md](ENTERPRISE_SEARCH.md).

## 7. Clips, local transcription and larger meetings

In **Work → Meetings**, explicitly record a microphone, camera or screen clip,
preview it, review its transcript/captions and publish. Clips follow existing
channel/file access and size bounds. Secure clips encrypt the recording and
metadata on the device; the server retains an opaque authenticated event
reference. Removing an index entry does not erase copies already received or
replace file-retention controls.

**Transcribe on this device** runs pinned Whisper tiny inference in a browser
worker. Audio is not uploaded to an ASR service. Model download requires consent;
operators can prepare verified official model assets for same-origin hosting.
Failures produce an error, and transcription requires review. Selected reviewed
clip transcripts can be disclosed separately to the configured Copilot provider
for cited notes; there is no automatic call recording or continuous live captions.

The existing browser mesh retains its six-device limit and call-only guest
admission. Larger member rooms use the actual LiveKit SDK and RoomService adapter
with an independent authorization worker. Configure customer LiveKit/TURN/TLS and
media networking explicitly. DTLS-SRTP terminates at the SFU, which can access
media; LiveKit calls do not inherit Matrix E2EE. Configured participant limits
are bounds, not measured production capacity. See [MEETINGS.md](MEETINGS.md).

## 8. Organization governance, native DLP and evidence

Personal workspace owners open **Integrations → Organization & policies**.
Organizations can group workspaces, assign management roles, require each
workspace's own SSO and view aggregate usage counts. Organization roles never
create channel membership or an administrator decryption capability. Hosting
country/region labels are operator metadata; actual residency comes from the
chosen infrastructure and provider arrangements.

Explicit native DLP rules warn or block fixed phrase/card/key-ID/email detectors
on selected native operations. Binary file inspection is limited; operators can
explicitly block uninspectable files. Blocking document policies deny binary
collaborative updates that this scanner cannot inspect. Matrix plaintext is not
available to server DLP.

Active case/channel/custodian holds fence native overwrites and deletion,
including later-running retention jobs. Evidence collection produces the existing
encrypted-at-rest archive, hash and review provenance, with current membership
required in every selected native channel. Case and retention holds remain
independent. Matrix history, devices, providers and independent backups need
their own preservation process. Audit records are metadata, not an externally
attested immutable ledger.

The optional instance-level Vault Transit adapter wraps fresh native data keys
without sending message plaintext to Vault. Enabling it requires a working
customer service; failure does not fall back to a local write. Retain local and
Vault key versions needed by older ciphertext. There is no automatic rotation,
history migration or per-organization Vault assignment in a shared installation.
See [ENTERPRISE_GOVERNANCE.md](ENTERPRISE_GOVERNANCE.md).

## 9. Customer-owned S3-compatible native files

Operators can choose `STORAGE_BACKEND=s3`, dedicated credentials and a bucket or
prefix. The adapter uses real SigV4 requests, immutable conditional writes,
bounded checksummed reads and version-aware deletion. It does not create a
bucket, silently fall back to local storage or automatically migrate existing
files. An explicit probe verifies a selected service; migration separately
copies/verifies database-referenced local versions while retaining the source.

Native files remain server-readable. Matrix media stays under its separate
client-encryption/homeserver controls. Keep local private export/logo storage,
database snapshots and encryption keys in the recovery plan. S3 object versions
alone are not a verified application backup. See
[OBJECT_STORAGE.md](OBJECT_STORAGE.md).

## 10. TLS Valkey coordination and multiple API processes

The default remains one API process. Operators adding replicas can configure
shared SQL/storage and authenticated TLS Valkey using `REDIS_URL`, a dedicated
`REDIS_NAMESPACE` and a `noeviction` policy. Valkey supplies transient event
fanout, device presence leases and atomic shared authentication throttles.
Receiving API processes independently reauthorize each local socket before
delivery; private broker messages contain no raw bearer JWTs.

Durable messages, inbox records and job claims remain in SQL. Pub/sub is not an
exactly-once durable message queue. Authentication limits fail closed when the
configured broker is unavailable; committed history is recovered through normal
client reconciliation. Configure trusted proxy behavior, connection affinity,
certificates, permissions, memory and monitoring. This setup enables shared
runtime coordination, not an unmeasured throughput guarantee or automatic
Kubernetes deployment. See [SCALING.md](SCALING.md) and
[INFRASTRUCTURE.md](INFRASTRUCTURE.md).

## Upgrade and recovery

1. Back up the existing database, local private volumes and retained keys before
   upgrading. Schema additions use new SQLModel tables; existing channels and
   ciphertext are not silently converted or migrated. Keep the same customer
   identity configuration, Matrix identity, content keys and recovery material.
2. Upgrade the API/frontend and workers together. The default Compose deployment
   now starts `integration-worker` alongside PostgreSQL, backend and frontend;
   native scheduled messages and reminders need it even with external adapters
   disabled. Mail and larger-meeting authorization remain separate optional
   worker/profile choices. Run only the background services your selected
   features require and verify their current health.
3. Version 0.11 retains independent verified authorization snapshots for mesh/SFU
   participants. Old in-progress calls lack those snapshots and end on upgrade.
   Start a new call and explicitly join/enable devices again; legacy participant
   rows do not gain inferred unrestricted authority.
4. Existing local file deployments remain local until the operator performs the
   explicit verified S3 migration. `pilot-backup.py` and `pilot-restore.py` refuse
   S3 configurations because their cold-backup format cannot capture remote
   objects. Use a consistent provider snapshot/restore together with database,
   private volumes and key backups. Restored external workers stay quarantined
   until explicitly resumed after destination and duplicate-delivery review.
5. Verify actual sign-in, current access, retained native bytes, trusted-device
   encrypted history and selected provider operations on an isolated restore
   before accepting production traffic. Native signing/store distribution, live
   provider acceptance, external TLS routing, capacity and disaster recovery are
   installation-specific checks, not consequences of source availability.
