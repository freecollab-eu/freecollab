# Current product: FreeCollab 0.10

The integration increment adds eleven native SDK modules, optional React wrappers and Web Components, plus the full trusted-origin embedded client. Customers can register their exact product origins in **Integrations → Embedding**, connect their own OIDC provider through the popup/PKCE bridge and embed ordinary or encrypted collaboration. Live server leases renew without replacing the verified Matrix device, resetting authentication freshness or replaying content writes. Native inline components keep their server-readable boundary; encryption runs inside the trusted embedded client. See [the SDK](COMPONENT_SDK.md), [embedding identity and sessions](EMBED_IDENTITY.md), [compatibility](../packages/components/COMPATIBILITY.md) and [release artifacts](SDK_RELEASES.md).

The preceding eight approved Weavy gaps remain implemented: personal Swedish/English choice, expanded files and desktop Office saveback, activity feeds, a modular component package, customer-owned cloud pickers, opt-in contextual AI/agents and external meeting services. See [the preceding 0.9 product and its limits](PRODUCT_090.md) and [validation](VALIDATION.md).

External connections now have a [guided setup center](CONNECTION_SETUP.md): fifteen provider recipes, customer-owned SSO presets, exact callback/scopes/field checklists, credential-free setup downloads and direct existing configure/authorize/test flows. Preparing a provider does not silently activate it or contact its service.

Provider activation, public hosting/TLS, live customer grants and npm publication remain operational actions; none is implied by local protocol tests. Customer SSO remains customer-owned. Future work should follow actual pilot feedback, accessibility review and operational evidence rather than add another unrelated feature list.

---

## Historical plans and implemented milestones

# FreeCollab pilot product plan

## Product increment 0.8

The six requested additions now share the channel **Work** dialog and personal appearance settings. Feature contracts and bounded limits are documented in [projects/tasks](PROJECT_TASKS.md), [meetings](MEETINGS.md), [mobile/offline](MOBILE_OFFLINE.md), [shared documents](SHARED_DOCUMENTS.md), [workflows](WORKFLOWS.md) and [data controls](DATA_CONTROLS.md). This increment keeps customer-owned SSO and existing workspace/channel boundaries. An empty-channel fence prevents native project/document/workflow history from being relabelled as end-to-end encrypted.

Native task/document contents use the server content key ring; secure-channel task revisions and CRDT updates use verified encrypted Matrix events. Document editing merges concurrent changes, with append-only history. Meeting media uses browser WebRTC; server signaling is temporary and encrypted at rest. The PWA queues encrypted private drafts/messages, rather than caching private API responses. Workflow and data jobs survive process restarts, with fresh grants checked at dispatch. Data deletion requires a current policy, no hold, an exact reviewable preview and native reauthentication. No automatic retention purge is enabled by default.

Operator setup remaining for a particular deployment: public HTTPS, the deployment's own STUN/TURN service when required, optional VAPID/browser consent for push, and explicit retention/backup lifecycle choices. See [validation evidence and limits](VALIDATION.md).

The current [Weavy comparison](WEAVY_COMPARISON.md) identifies potential next priorities: Swedish localization, a broader file/Office workflow, production feeds and a published component SDK. These are comparison findings, outside the implemented six-part increment.

## Product and first audience

FreeCollab is a self-hosted collaboration service for a small team or an application adding private team chat. The first product is an installable server, a React reference client and a documented browser widget integration. There is no published React SDK package or managed SaaS account offering in this repository.

The pilot uses one API worker/replica, PostgreSQL, persistent file storage and an Nginx frontend. It supports personal registration/login, private channels with explicit invitations, text chat/history, threads, member-scoped file sharing and operator-controlled administration. That original 0.4 path trusts the server with message plaintext and stores legacy files without application encryption. The 0.5 client adds private workspaces, account recovery/MFA, local search, file versions, decisions, canonical DMs and optional client-encrypted channels using a private Matrix service. The 0.6 increment adds [OIDC, webhooks, linked documents, calendar interoperability and optional version proofs](INTEGRATIONS.md). The 0.6.1 correction makes SSO customer-owned per workspace: each workspace administrator configures its own provider in the app, and customer SSO sessions stay within that workspace. The 0.7 increment adds optional managed first SSO through provisioning/invitations/JIT, workspace SCIM Users/Groups with explicit role/channel rules, and bounded two-way native-channel file/calendar synchronization. Customers own their IdP, directory credentials and cloud applications; encrypted Matrix channels remain outside server background sync. See [the encryption implementation](E2EE_IMPLEMENTATION.md).

Version 0.7.1 adds [private security and operations controls](SECURITY_OPERATIONS.md):
configuration/MFA/storage/queue diagnostics, a bounded metadata-only security
activity trail, fresh authorization checks across SSO/sync races, optional
authenticated encrypted backup bundles, and separate explicit opt-in for restored
mail and integration workers. These are implemented and locally verified controls;
public TLS, external alert routing, off-site scheduling and an independent security
review remain installation/release work.

## Release scope and evidence

| Area | Product behavior | Verification gate |
| --- | --- | --- |
| Personal access | Ordinary account creation, login, current-session logout and blocked-account rejection. | Existing/expired/revoked credentials rejected across REST and Socket.IO; auth throttling and restart behavior checked. |
| Customer onboarding and directory | Default explicit personal linking or optional isolated managed SSO accounts; SCIM Users/Groups, invitations and owner-approved group access. | Same-email tenant isolation, first sign-in without native password, invitation replay, SCIM token/owner scope, group grants, deactivation and native fallback. |
| Selected cloud resources | Google/Microsoft/DAV files and supported single calendar events synchronize both ways for a native channel, with explicit disclosure and conflict review. | Actual protocol fixtures and browser/worker checks for bytes, revisions, conditional writes, competing changes, deletion, retries, grant loss and secure-channel denial; hosted provider consent remains customer validation. |
| Shared channels | Creator is channel admin; expiring one-use invites grant member access; role handover, member removal and leaving are explicit. | Nonmember denial, invite expiry/reuse/revocation, current-socket eviction and concurrent final-admin invariants. |
| Chat and files | Real-time text, persisted history, threads, author edits/deletes and authenticated upload/download in the UI. | Two independent browser accounts share a channel, exchange messages and exact file bytes; an outsider is denied. |
| Content keys | JWT signing and versioned message encryption use independent keys; old history has an explicit migration path. | JWT rotation independence, authenticated channel binding, dry-run/apply/idempotence, malformed-row rollback and recovery. |
| Browser integration | The actual widget bundle can be hosted and authenticated from a standalone page. | Safe text rendering, successful send/polling, failed-token behavior, channel changes and cleanup. |
| Local installation | Private generated settings; loopback frontend; internal API/database; persistent PostgreSQL/files; health/readiness. | Fresh install, restart, meaningful API/WebSocket requests and data persistence. |
| Recovery | Stop writers, back up database/file/key state, restore into a separate installation with exact images. | Compare restored message history, member access and file bytes; refuse overwrites/corrupt archives. |
| Dependencies | Hash-pinned Python installs, frozen npm installs and current advisory checks. | Fresh locked install, package advisory reports, backend suites and production frontend/widget builds. |

[VALIDATION.md](VALIDATION.md) records actual outcomes. A feature description or a passing isolated suite does not establish a completed deployment or external pilot.

## Supported integration contract

Use the [standalone widget example](../examples/widget/README.md), [channel/invitation API](CHANNELS.md) and actual server OpenAPI routes. Obtain a user-scoped token through an appropriate login flow, retain channel membership checks and allow only intended frontend origins. Do not embed an operator key or permanent credential in a public page.

The widget currently polls the latest 50 messages every three seconds. It does not reconcile edits/deletions or recover larger gaps. This limit must be acceptable to the initial integration audience before treating the widget as its full chat client.

## Controlled external pilot

Before routing external users, select a real host/domain, provision HTTPS/WSS and retain a recoverable backup. Set production secrets/origins, validate trusted-proxy addressing and agree who operates the single replica. Run the documented restore test before accepting content that matters. Never expose the local development profile unchanged.

Have a small group of external developers complete installation and the core flow using only the documentation. Record failures, successful completion of onboarding, message/file delivery, restore duration and support interventions. Broader access depends on closing their blocking defects; there is no presumed completion date or claim that a pilot has already happened.

## Deferred features

The eight workstreams in the [product roadmap](PRODUCT_ROADMAP.md) now have a 0.5 implementation. See [client encryption](E2EE_IMPLEMENTATION.md) for the selected architecture and [validation](VALIDATION.md) for completed checks and remaining limits. The older baseline below remains historical.

Posts/flows and comments are optional advanced API routes, disabled by default via `ENABLE_EXPERIMENTAL_FEATURES=false`. The AI-demo API is independently disabled by `ENABLE_DEMO_AI=false`. Enabling a feature requires its own access-control and browser validation; hiding navigation alone is insufficient. AI-demo opt-in may send demo messages/history to Groq if a key is configured.

These are separate product decisions before a wider organizational release:

- Installation-wide invitation-only personal registration, SAML and organizational identity assurance beyond the configured customer OIDC/SCIM policies. Managed invitation/provisioning/JIT entry is implemented separately.
- Shared rate limits and realtime state before multiple workers or replicas.
- Alert routing using the implemented private metrics, external TLS renewal, off-host encrypted backup scheduling and recovery objectives.
- File malware/content scanning, retention/deletion policy and storage-level encryption where required. Permanent account/channel deletion is currently rejected rather than silently orphaning retained content.
- Larger-history synchronization and edit/delete reconciliation for the widget.
- A versioned published SDK package with supported-browser policy, release automation and tested examples.
- Recursive folder trees, remote permission mirroring, rich meeting/recurrence synchronization and a client-only approach to synchronizing encrypted Matrix content. The current worker deliberately handles only selected native-channel resources.

Client-side encryption is implemented on the separate Matrix path with browser-owned keys, recipient verification, device lifecycle and recovery. Independent integration review and any future legacy-history conversion remain release gates; adding a timestamp provider does not change a channel’s encryption model.

OTRUST is an [optional hash-only version-proof integration](OTRUST_INTEGRATION.md). It has no role in login or message encryption. The UI distinguishes a checked proof commitment from independent Bitcoin-chain verification, which is not claimed.

## Operator documentation

- [Installation, backup and restore](OPERATIONS.md)
- [Accounts, sessions and pilot limits](AUTH_SESSIONS.md)
- [Channel invitations and roles](CHANNELS.md)
- [Content keys and migration](KEY_MANAGEMENT.md)
- [Dependency locks and advisory results](DEPENDENCIES.md)
- [Security boundaries](SECURITY_MODEL.md)
