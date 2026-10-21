# FreeCollab 0.12

This release adds eight areas to the existing self-hosted product. Customers retain their own infrastructure, Microsoft/Google registrations, identity providers and encryption material. Optional integrations remain disabled until the installation configures and verifies them. Source availability does not activate Apple/Firebase, cloud services, Matrix federation or a public deployment.

## A personal working day

**My day** combines current assigned tasks, calendar occurrences, unread activity, private reminders and saved follow-ups across authorized workspaces. It opens the actual source and uses existing task revisions, snooze, completion and inbox APIs. An organization's scoped sign-in sees only its current scope. Bounded scans report incomplete results. Native fields are read through existing authorization; encrypted fields are verified and resolved on the trusted device. See [MY_DAY.md](MY_DAY.md).

## Native notifications and incoming call alerts

Customer-controlled FCM HTTP v1 and APNs HTTP/2 adapters support consented native mobile registrations. A device must receive and confirm a one-use ownership challenge. Delivery rechecks the original sign-in, parent session, signed customer scope, current channel access, unread activity and notification preferences. Payloads contain opaque references rather than sender names, conversation text or file URLs.

Android/iOS native bridges request system notification permission and hand an alert back to the authenticated application. Incoming-call alerts lead to current call controls and an explicit foreground join. This is standard notification delivery, not PushKit/CallKit, background audio, unattended joining or a guaranteed OS wake-up. Real installation/device delivery needs the customer's own Apple/Firebase configuration, appropriate application identity/signing and a physical-device smoke test. See [NATIVE_PUSH.md](NATIVE_PUSH.md).

## Calendar planning

The calendar adds bounded daily, weekly and monthly recurrence, individual occurrence moves/cancellations, internal current-channel-member invitations and RSVP, and shared-time suggestions. Series fields stay within the existing native or Matrix encrypted calendar revision. Encrypted availability is calculated on the trusted device; native availability returns busy intervals for explicitly shared channel sources rather than private calendar names. Cloud providers are not silently given a flattened first occurrence when a local series cannot be represented by the current sync adapter. See [DOCUMENTS_CALENDAR.md](DOCUMENTS_CALENDAR.md).

## A deliberate Slack move

A personal workspace administrator can upload and preview a bounded ZIP export, choose conversations, map eligible existing people, and import into new native channels. Historical author labels, timestamps, reply relationships and optionally bundled supported files are retained. Missing attachments are explicit. The importer never fetches private Slack file URLs, creates accounts or emits historical mention/workflow notifications. Confirmation is idempotent; import failure rolls back SQL and attempts cleanup of every newly owned blob. Imported message bodies and temporary preview ZIPs are encrypted at rest; bundled files use ordinary server-readable native storage. The import does not retroactively apply Matrix end-to-end encryption. See [SLACK_IMPORT.md](SLACK_IMPORT.md).

## Personal Google and Microsoft consent

Workspace-owned OAuth registrations provide auth-code/PKCE, one-use browser-bound consent, encrypted access/refresh tokens, refresh rotation and revocation. Personal read-only search grants remain distinct from explicit calendar-write app grants. Tokens are bound to the initiating user's current signed authority; another person or administrator cannot borrow them. Google Calendar and Microsoft Calendar app recipes use the invoking person's own reviewed delegation. Existing unrelated vendor recipes still require their own credentials or customer gateway. See [PERSONAL_OAUTH.md](PERSONAL_OAUTH.md).

## An encrypted offline library

Users can deliberately save selected current channel history and files in the local Web Crypto vault and queue attachments durably. Limits, expiry and the exact sign-in boundary remain visible. Reconnection reauthorizes before fetching or sending; known revoked sources are removed and uncertain operations require review. Stable native upload receipts and Matrix transaction identifiers prevent blind duplicate uploads after lost acknowledgements. The service worker still caches only the public app shell. An offline device cannot discover a server-side revocation until it reconnects, and separately downloaded OS files remain outside the app's local erase operation. See [MOBILE_OFFLINE.md](MOBILE_OFFLINE.md).

## A reviewable integration library

Bundled recipes and reviewed community packages have immutable app versions and package hashes. Contributors submit declarative manifests, source/license metadata and contract fixtures; instance administrators control public approval and withdrawal. Installation reviews the exact version, scopes, endpoint and selected native channels through existing app-platform controls. Packages cannot execute downloaded code or grant administrative bot accounts. Contract tests validate request shapes, not publisher identity or a live service. This is an installation-local public registry rather than a centrally hosted marketplace. See [INTEGRATION_LIBRARY.md](INTEGRATION_LIBRARY.md).

## Channels across installations

Optional cross-installation Connect adds explicitly pinned installation identities, signed audience/operation/time/nonce-bound requests, bilateral workspace acceptance and independent local channel/grant records around a federated Matrix room. Each side manages its own members and identity provider. Remote accounts do not become local workspace members. Current remote roster and verified-device checks remain part of the encryption boundary. Operators must explicitly configure public HTTPS trust and compatible Matrix federation; the default Matrix deployment stays private. See [CONNECT_FEDERATION.md](CONNECT_FEDERATION.md) for exact configuration, revocation behavior, evidence and remaining deployment checks.

## Upgrade

Back up SQL, local/remote files, native content keys, account-security material and Matrix recovery/configuration before upgrading. New tables are additive; legacy content is not silently converted. Upgrade API/frontend and the integration worker together. The worker now also performs bounded expired Slack-preview and OAuth-consent cleanup; optional native notification dispatch needs its configured provider settings.

Existing apps, ordinary calendar events and same-installation Connect remain supported. No new optional external provider is enabled by installing this release. Customer identity/notification/cloud registrations, Matrix federation, public routing/TLS, signing/store distribution and measured capacity need installation-specific verification. Railway and public website deployment are separate from this source release. See [VALIDATION.md](VALIDATION.md).
