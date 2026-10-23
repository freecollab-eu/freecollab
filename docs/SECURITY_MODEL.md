# FreeCollab security model

This describes current product boundaries. The default is one API process; configured replicas require the complete shared-runtime controls in [SCALING.md](SCALING.md). New secure channels use client-side Matrix encryption; legacy channels retain server-side encryption. The FreeCollab integration has not received an independent security audit. See [E2EE_IMPLEMENTATION.md](E2EE_IMPLEMENTATION.md) for device trust, recovery and visible routing metadata.

## Content, credentials and keys

| Data | Current handling | Trust boundary |
| --- | --- | --- |
| Legacy chat message bodies | API receives plaintext, encrypts stored content using PyNaCl SecretBox and decrypts authorized history. Realtime events contain plaintext. | Server process/operator, TLS termination and authorized clients. |
| Secure channels and attachments | Matrix JS SDK + Rust crypto encrypt messages, titles, decisions and attachment metadata/bytes on the client. Only opaque Matrix events/media pass the authenticated gateway. | Trusted verified recipient devices and separately saved recovery key; account/membership/traffic metadata remains visible. |
| Message content keys | Independent versioned key ring, with an active key ID. New authenticated envelopes bind key ID and channel context. | Anyone with the database and required content key can decrypt it. |
| JWT signing | Separate private secret signs seven-day login tokens. New logins have random identifiers; current-token logout persists a digest/expiry. | Signing-key holders and trusted server operators. |
| Old unversioned ciphertext | Reads require an explicitly retained legacy content key. It is never implicitly derived from the live signing secret for new writes. | Retained old key and controlled offline migration. |
| Native uploaded files | Original bytes use a local volume or explicitly configured S3-compatible storage. Native file bytes do not receive application-level content encryption. | Storage/backup administrators, server and authorized members; provider storage encryption is separate. |
| Passwords | bcrypt; new native registrations and administrator-created accounts require at least 12 Unicode code points and at most 72 UTF-8 bytes. Passwords reach the backend over the configured transport. | Backend and TLS termination. |
| Legacy user/channel key helpers | Registration creates key material on the server; Argon2id wraps the generated private key. These helpers are not the active content-encryption protocol. | The server handles the submitted password and generated key material. |
| Browser credentials | The standalone app retains its bearer in localStorage; the trusted embedded client keeps its child session in memory. The legacy widget takes a user-scoped host token. | Trusted client origins and host integration authorization. XSS can expose an origin’s credentials. |
| Offline browser copies | Opted-in history/file metadata and bytes, drafts and queued content use account-bound AES-GCM ciphertext with nonextractable browser keys. No private API/media responses enter service-worker CacheStorage. | Trusted same-origin code and the unlocked browser; offline copies cannot observe revocation until reconnection. |

Legacy file bytes and routing metadata do not inherit message-body encryption. Native decision/document/calendar revision payloads use the server key ring; secure document/calendar payloads use encrypted Matrix events. Generic notifications and webhook payloads disclose routing IDs and activity without decrypted content. Use storage-level encryption separately where required. Optional [S3-compatible native storage](OBJECT_STORAGE.md) uses immutable conditional writes, bounded verified reads and version-aware erasure; it does not turn native files into Matrix E2EE. OTRUST digests, receipts and timestamps do not encrypt content.

## Content-key lifecycle

Production requires an explicit `MESSAGE_ENCRYPTION_KEYS` JSON map, an active ID present in that map, a separate private `JWT_SECRET` and explicit HTTPS origins. The content key is a random standard-base64 32-byte value; it must not be the current signing-secret-derived legacy key. Development can generate a private persistent key file outside uploads; keep it with the associated development database.

New encrypted bodies use a versioned envelope. The encrypted payload authenticates channel context, so copying it to a different channel fails. Retain older ring entries while any required live history or backup uses them. Optional [Vault Transit](ENTERPRISE_GOVERNANCE.md) wraps native data keys without changing the server-readable content boundary; a configured provider failure has no local-write fallback. Signing-key rotation and content-key rotation are separate operations; changing the former does not re-encrypt legacy data.

Upgrade with writers stopped, a tested backup and an explicit legacy key retained from the old installation. Run the migration audit, then an applying transaction and a second audit. Malformed encrypted rows abort without partial updates; they must not be relabeled as plaintext automatically. See [KEY_MANAGEMENT.md](KEY_MANAGEMENT.md) for the exact commands and key-retention requirements. A database backup without its required content keys cannot recover encrypted history.

## Authorization and invitations

A bearer token identifies an account; a channel ID is not a credential. REST and Socket.IO require an existing, active account, a valid nonrevoked token and membership in the owning channel. Private broadcasts recheck recipients, including those connected before a block, logout or membership removal.

Only a channel admin can issue/revoke invitations or change another member's role. Invitations are expiring single-use bearer tokens with hashes stored in the database; links carry the token in a URL fragment and acceptance sends it in an authenticated request body. They grant a member role only. Token possession is not verified ownership of an email address. Expired/revoked/used invitations or invitations from an inactive former admin are rejected. The final channel admin must hand over before leaving/removal/demotion. See [CHANNELS.md](CHANNELS.md).

Installation administration starts through an operator command for an existing account, not a public first-registrant promotion. Channel administration and installation staff roles are separate. Email verification, one-use password recovery, TOTP MFA, hashed backup codes and session management are implemented. Customer-owned OIDC defaults to explicit native password/MFA linking for existing verified members. An administrator can instead admit separate workspace-managed identities through provisioning, organization invitations or explicit just-in-time policy. Email claims never merge these with personal accounts. Customer SSO stays workspace-restricted and cannot manage global account security or installation settings. Provider revisions, active principals and current membership are rechecked across REST, sockets and Matrix. Provider edits/disabling invalidate sessions; connected SCIM deactivation revokes managed sessions and membership. Without provisioning synchronization, an IdP-only disable takes effect at the next IdP login.

The 12-character minimum applies when creating new native accounts; existing short passwords remain valid for sign-in and reauthentication. The 72-byte bcrypt limit is checked without truncation, including for multibyte characters. Customer-managed SSO does not acquire a native-password requirement.

Personal Matrix identity and backup span the native account, requiring a separate native password/MFA unlock lasting at most 30 minutes per scoped token. Managed accounts have a separate per-workspace native/Matrix identity and can initialize it with valid scoped SSO. Both paths require device trust and separate recovery keys; SSO does not recover content keys. Managed namespace checks fail closed for owner promotion, personal homes or outside memberships. Recent managed SSO can revoke an owned encryption device with explicit confirmation; remote deletion precedes local revocation. See [AUTH_SESSIONS.md](AUTH_SESSIONS.md) and [WORKFORCE_IDENTITY.md](WORKFORCE_IDENTITY.md).

## Sessions, abuse limits and deployment

Current-token logout persists revocation. Browser token clearing alone cannot revoke an already copied credential; the client must receive a successful server response to claim completed revocation. Blocking/deleting a user affects all credentials. Rotation of the signing secret invalidates outstanding tokens. Revocation checks also apply to existing sockets before authorized events/private delivery.

Authentication throttling is bounded. The single-process default uses local state; explicitly configured TLS Valkey supplies shared atomic limits, realtime fanout and presence for replicas. Configured shared authentication limits fail closed during broker outages. Admit only the intended private proxy as a trusted forwarder. Complete [SCALING.md](SCALING.md) before adding replicas; these controls are not a complete denial-of-service defense.

Use HTTPS/WSS for public traffic, exact browser origins and a private API/database network. The default Compose profile binds only a loopback frontend and uses local HTTP; it is not a public TLS endpoint. Nginx proxies API/socket traffic and overwrites untrusted forwarding headers. Additional edge proxies need a deliberate client-IP policy. Set request-body limits at the edge as well as in the application.

Direct permanent channel deletion remains fenced; [reviewed data controls](DATA_CONTROLS.md) provide preview-bound native retention, physical file cleanup and a separate dependency-checked account identity erasure action. Preservation holds and current authority apply. Removing membership changes access without erasing previously delivered content or backups. Message moderation uses soft deletion to preserve thread references.

Protect durable file/database volumes, private configuration and backups. Backup helpers retain server signing/content/account keys and may contain legacy plaintext files. They do not contain users’ client encryption recovery keys; private filesystem permissions are not archive encryption. The restore workflow requires exact retained images and a new target. Follow [OPERATIONS.md](OPERATIONS.md) and independently verify restoration before accepting important content.

The restore helper quarantines both outbound mail and integration workers by default. Independent opt-in flags resume only workers recorded as running in the source backup. Optional XChaCha20-Poly1305 backup sealing authenticates an encrypted copy before publishing recovered plaintext into a new private directory. Its separate key must be retained outside that copy; sealing preserves the original backup and does not upload or schedule anything. Database exports and sealed copies retain historical credentials and activity independently of live retention.

## Private operational security and activity

Native unrestricted installation administrators can review eleven fixed configuration/local-state checks and bounded authenticated activity in **Integrations → Operations**. Workspace owners, customer-scoped sessions and read-only metrics credentials cannot read `/operations/security` or `/operations/security-events`. Authorized responses use `Cache-Control: no-store`. Checks expose aggregate operator MFA coverage, upload-filesystem capacity and queue state without contacting providers, decrypting records or opening key files. They establish configured controls rather than certificate validity, delivery or backup freshness.

The activity recorder accepts selected authenticated mutation routes and bounded opaque actor/workspace/target identifiers. It excludes content routes, request bodies, query strings, network addresses, email-shaped identifiers and credentials. Anonymous sign-in failures use existing bounded authentication counters rather than creating an unbounded per-request activity stream. The default window is up to 90 days with a 100,000-record target; writes perform bounded pruning and serialize age/cap decisions on SQLite and PostgreSQL. Lowered limits converge incrementally; reads immediately enforce the age window. Backup copies are unaffected.

Recording is best effort after the action. A failed activity write emits only a generic server-log message and does not relabel a committed successful action as failed. Database administrators control the records, so this is neither a complete immutable audit ledger nor external attestation. [SECURITY_OPERATIONS.md](SECURITY_OPERATIONS.md) gives exact API, retention and monitoring behavior.

## Optional integrations and advanced routes

Experimental posts and their generic comments are disabled by default via
`ENABLE_EXPERIMENTAL_FEATURES=false`; production activity feeds and document
comments have their own authenticated contracts. Core mentions are member-scoped.
The anonymous server AI-demo endpoints are removed, and `ENABLE_DEMO_AI` is
deprecated and ignored even when true. Production AI uses the separately
configured [Copilot](COPILOT.md), reviewed sources, provider revision and explicit
disclosure/consent. The public [product demo](PRODUCT_DEMO.md) uses the same
product UI/API in an isolated temporary sample installation; external providers
are disabled and no model replies or authorizations are fabricated. Its native
example session is separate from any retained customer credentials.

The legacy widget sends plaintext to the authenticated server and is refused access to secure channels. Its current protocol cannot join or send to encrypted rooms. It polls the latest 50 messages every three seconds. It does not reconcile edits/deletions or fill larger gaps. API URLs require HTTPS except HTTP loopback development. Requests omit cookies, disable caching and reject redirects. Changing the account or channel clears the displayed history and draft and cancels old work; destroying the instance is terminal. Render all host/user text as text. A host page must protect its user token, host a trusted bundle and never embed operator credentials. The [standalone example](../examples/widget/README.md) stores no token in the URL or persistent browser storage.

[External integrations](INTEGRATIONS.md) retain channel grants and encryption boundaries. Webhooks use HMAC-signed generic events, encrypted destination/secrets, durable retries and public HTTPS/DNS/TLS checks. Saved document links are not automatically fetched. Explicit secure-calendar export decrypts locally. Optional two-way [remote synchronization](REMOTE_SYNC.md) shares selected native-channel file/calendar content with the customer's provider under an explicit current administrator grant. Secure Matrix channels, including channels preparing encryption, reject background sync; there is no server plaintext fallback. Channel members see a sharing notice. Credentials are encrypted, OAuth consent is account-bound and changing a selected resource requires fresh provider authorization. Operator diagnostics expose aggregate/redacted queue information; optional metrics credentials can read only metrics.

[SCIM](SCIM.md) bearer credentials are shown once and stored as hashes. Authentication rechecks the issuing owner's current membership; demotion, blocking or removal requires another current owner to rotate the credential. Channel grants require both current workspace and channel approval. Provisioning cannot create a workspace owner or global administrator, merge personal accounts or erase previously held content keys. Deactivation removes access while retaining history.

[OTRUST version timestamps](OTRUST_INTEGRATION.md) are optional and require explicit digest-sharing consent. The server hashes exact native bytes; secure versions carry a client-asserted digest and opaque event reference. OpenTimestamps checks the proof commitment, while independent Bitcoin-chain verification remains unclaimed. Timestamps do not establish authorship, legal identity or content confidentiality. Local tests used a synthetic provider and made no hosted claims.

## Personal views, devices and optional connections

[My day](MY_DAY.md) aggregates only currently permitted source references and
bounded native fields. It creates no new channel grants or persistent plaintext
index. Secure tasks and calendars are resolved on the trusted device. Calendar
recurrence and exceptions follow the existing content encryption mode; internal
attendee identities/responses remain server metadata. [Shared-time planning](CALENDAR_PLANNING.md)
checks only the selected visible channel, not private or provider calendars.

The [offline library](MOBILE_OFFLINE.md) is explicit per channel/file, bounded,
same-sign-in and seven-day limited. Online reads recheck access and refresh the
snapshot; known revocation removes retained copies. While offline, a device can
still read content it previously received. An unlocked browser can use its own
nonextractable key; this is not an independent password lock. Signing out erases
browser vault records/keys, not files explicitly downloaded to the operating system.

Browser Web Push and [native FCM/APNs alerts](NATIVE_PUSH.md) are separate opt-in
transports, both disabled until their operator-owned provider setup is complete.
Native alerts require the delivered device proof and current source-session,
workspace/channel and activity/call checks. Payloads contain generic activity
and opaque references, not conversation text, names or content keys. A delivered
OS alert may outlive revocation, but resolving it checks current access. Call
alerts do not admit a participant, activate media or guarantee background ringing.

[Personal provider OAuth](PERSONAL_OAUTH.md) uses each customer's Google/Microsoft
registration with purpose-specific delegated consent, one-use PKCE/browser state,
sealed tokens and original verified source claims. Read-only search and reviewed
fixed-endpoint calendar actions use separate grants. Refresh rotation is serialized
and fails closed after ambiguous results; source logout or changed grants prevent
reuse. Manual/custom token connectors retain their manual lifecycle. SSO alone
does not authorize provider content or actions.

[Slack imports](SLACK_IMPORT.md) create new native, server-readable archive
channels only after explicit review. They never fetch embedded remote file URLs,
merge accounts or retroactively claim E2EE. A mapped person receives a channel
seat only through the importing administrator's selected mapping. Import rollback
tracks owned file writes, but a storage cleanup outage can require operator
reconciliation. The [integration library](INTEGRATION_LIBRARY.md) publishes bounded,
reviewed declarative packages and exact digests; installation still requires its
own current scopes and channel grants. Contract fixtures do not certify a
publisher, source repository, provider service or security properties.

[Connect](CONNECT.md) keeps the same-installation bilateral path. Optional
[cross-installation Connect](CONNECT_FEDERATION.md) is disabled by default and
requires deliberately pinned installation keys, allowed HTTPS peers and separately
configured Matrix federation. Each side authorizes only its own accounts; no
foreign workspace membership is inferred. Peer signatures and opaque control
messages do not prevent a malicious participating operator from redistributing
content its recipients already received. Neither path recalls recipient-held keys.

## Verification and remaining responsibilities

See [VALIDATION.md](VALIDATION.md) for exact executed checks and [DEPENDENCIES.md](DEPENDENCIES.md) for pinned installs/advisory reports. Positive happy-path tests alone are insufficient: include outsiders, blocked/deleted users, expired/revoked credentials, connected clients, failed provider/storage operations and recovery copies.

The [2026-10-22 internal review](SECURITY_REVIEW_2026_10_22.md) records the fixes and regression checks for the Open Studio design candidate. It is not an external security audit.

External pilot onboarding, alert routing using the private metrics, TLS issuance/renewal, off-host encrypted backup scheduling, organizational identity policy and any required independent security review remain deployment/product responsibilities. Do not infer those outcomes from a local build, isolated tests or a saved configuration file.

## Customer-configured provider network boundary

Workspace IdP URLs are untrusted tenant input. Discovery, token and JWKS requests
use independently validated public HTTPS endpoints on port 443, bounded DNS and
absolute request deadlines, pinned public IPs, original-host TLS/SNI verification,
no redirects and bounded JSON responses. Private, loopback, link-local and mixed
public/private DNS answers are refused. Inherited HTTP(S) proxy and CA trust are
retained; the public destination remains pinned through CONNECT. Synthetic local
IdP tests inject a test-only transport; there is no production private-host bypass.

Customer secrets are context-bound encrypted database values using the retained
account-security key. Admin responses expose only whether a secret exists.
Omitted or empty SSO secrets remain attached only to the same canonical issuer
and client ID; changing either clears the retained value unless explicitly
replaced. Cloud callbacks and saved tests recheck authority, session state and
connection revision after lock acquisition and network work. Provider file reads
validate the selected parent and revision again after downloading, adding strong
conditional ETags where supported. Concurrent retargeting, edits or moves cannot
silently relabel new credentials or downloaded bytes as the earlier saved state.
Customer IdP email claims cannot reserve or verify a personal account; explicit
personal linking requires its existing verified owner and current membership.
Managed provisioning creates a distinct reserved identity without personal email
matching. Scoped room filtering is an authorization boundary; personal account
key material still needs explicit native-owner unlock. Separate managed identity
isolation is documented above. Remote provider transport applies the same public
HTTPS/DNS/TLS boundary, bounded pagination and deadlines; no private-host bypass
is exposed in production.
