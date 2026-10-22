# FreeCollab 0.13

This release connects the previously separate mobile, calendar, migration and
cross-installation work flows. Customers retain their own identity providers,
OAuth registrations, infrastructure and signing identities. There is no PHP.

## Native mobile calls

iOS integrates CallKit, PushKit, AVAudioSession and LiveKit. Android integrates
self-managed Telecom, call notifications and a foreground media service. Calls
use narrow device credentials bound to the original current sign-in, explicit
answer actions, current channel membership and bounded call heartbeats. Logout,
device removal and account closure revoke the corresponding call authority.

Secure-channel users explicitly acknowledge the SFU-readable audio before joining.
This media is not Matrix end-to-end encrypted. Customer APNs/Firebase and LiveKit
configuration, signed builds and physical-device acceptance remain necessary.
See [NATIVE_CALLING.md](NATIVE_CALLING.md).

## Google and Microsoft calendars

Supported finite recurring series and moved/cancelled exceptions synchronize with
conditional versions and explicit conflict handling. A separate reviewed queue
publishes invitations, reads attendee responses, responds as the connected account
or cancels a provider event. Normal background sync does not send invitations.
Authority is checked before each provider request; uncertain partial effects are
not automatically replayed.

Native administrators can create expiring, revocable subscription links after
password/MFA confirmation and explicit disclosure approval. Busy times are the
default; titles are a separate opt-in. Encrypted channels cannot enter these
server-readable delivery paths. See [CALENDAR_DELIVERY.md](CALENDAR_DELIVERY.md).

## Encrypted shared work

**Work → Shared work** in an active cross-installation channel provides tasks,
finite calendar series and text documents. Content travels through verified
Matrix encryption; servers exchange only opaque event pointers. Task/calendar
revision graphs expose concurrent heads for explicit merging. Real Yjs updates
merge concurrent document edits.

Current grant and device checks remain mandatory. Malformed individual events
are quarantined, while resources with missing ancestry remain read-only. Bounded
history, missing keys, removed devices or unavailable peers can prevent recovery.
Native projects, provider identities and workflows remain local. See
[FEDERATED_WORK.md](FEDERATED_WORK.md).

## Slack migration

Larger bounded ZIP archives support resumable native batches with idempotent
receipts, encrypted private previews and current mapping authority. Historical
authors, reactions and edits are labelled as imported history, without pretending
they are actions by current FreeCollab accounts.

For encrypted destinations, the trusted client parses the ZIP locally and sends
encrypted Matrix messages, threads and attachments directly. Source-bound
checkpoints support retry without uploading the archive to the backend. Strict
ZIP structure, entry, expansion and path limits apply. See
[SLACK_IMPORT.md](SLACK_IMPORT.md).

## Distribution and the public demo

Release workflows prepare versioned multi-architecture images, SBOM/provenance,
digest-pinned installation metadata and exact SDK tarballs. Signed native release
jobs validate the customer's platform identity and stop when it is absent.
Publication requires completed CI for the exact source commit.

The independent static website serves the product presentation and an account-free
local demo without account, provider, socket or service-worker startup. Railway
deployment remains separate. See [DISTRIBUTION.md](DISTRIBUTION.md).

## Upgrade and evidence

Keep SDK, API and trusted client on the matching 0.13.x line. The
`native_background_calls` capability is now a structured configuration result;
see the SDK compatibility guide. New tables are additive, but retain database,
Matrix and independent key backups before upgrading.

[VALIDATION.md](VALIDATION.md) records executed checks and their limits. Synthetic
provider protocol fixtures are not live-tenant acceptance, unsigned/debug builds
are not signed distribution, and functional load checks are not a scale guarantee.
