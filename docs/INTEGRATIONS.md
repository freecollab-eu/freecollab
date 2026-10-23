# FreeCollab integrations

Open **Integrations** in the chat sidebar. Channel selection follows current membership; a workspace administrator does not automatically have access to private channels. Personal appearance and shared keyboard/dialog controls apply throughout.

Administrators can start with **Connection setup** for provider recipes, exact callbacks/permissions, a credential-free setup checklist and direct configuration through the existing forms. Customer-owned identity presets simplify the first SSO configuration. [Guided customer connections](CONNECTION_SETUP.md) explains the one-time provider registration and current-state checks.

| Connection | Implemented behavior | Configuration and boundary |
| --- | --- | --- |
| Account email | Durable delivery, operator test/retry, worker health and redacted diagnostics. | The installation configures SMTP and a verified sender. [Operations](INTEGRATION_OPERATIONS.md). |
| Organization sign-in | Customer-owned OIDC with code/PKCE, signed claims, explicit personal linking or optional managed first sign-in. | Each workspace configures its own IdP. Managed accounts are isolated to that workspace. [OIDC and onboarding](OIDC.md). |
| People and directory | Managed invitations, optional member-only creation at first SSO, SCIM 2.0 Users/Groups and explicit group access rules. | A native workspace owner creates the directory bearer token. No automatic adoption of personal accounts by email. [SCIM setup](OIDC.md#directory-provisioning-with-scim). |
| Webhooks / n8n | Explicit channel grants, signed metadata-only events, durable retries/history and secret rotation. | Public HTTPS receivers; no plaintext message, title or attachment content. [Webhook API](WEBHOOKS.md), [n8n receiver](../examples/n8n/README.md). |
| Document links | Provider-validated Nextcloud, Google Drive and SharePoint references with immutable revisions. | Opening a link uses the remote provider's own access policy. Linking requires no cloud-account authorization. [Documents](DOCUMENTS_CALENDAR.md). |
| Channel calendar / ICS | Events, immutable revisions, local ICS import/export and calendar-app reminders. | Local export is an explicit plaintext download; importing does not send meeting invitations. [Calendar behavior](DOCUMENTS_CALENDAR.md). |
| Cloud synchronization | Two-way supported file bytes/versions or supported bounded calendar events/series between one selected native channel and one remote folder/calendar. | Google Drive/Calendar, Microsoft OneDrive/SharePoint/Outlook, Nextcloud WebDAV/CalDAV and generic CalDAV. Opt-in, bounded, depth one; server sync rejects end-to-end encrypted channels. [Sync setup and limits](DOCUMENTS_CALENDAR.md#cloud-synchronization). |
| Cloud file picker | Browse and explicitly import an exact Google Drive, OneDrive, Dropbox or Box revision into immutable native file versions. | Customer-owned OAuth; Google/Microsoft reuse the channel file-sync grant. Secure channels use separate encrypted links. [Cloud files](CLOUD_FILES.md). |
| External meeting services | Explicit Google Meet, Microsoft Teams or Zoom create/recover/cancel with channel calendar links. | Customer-owned OAuth and provider licence; title/time disclosure requires consent. The provider runs its own media. [Meeting services](EXTERNAL_MEETINGS.md). |
| Personal provider accounts | Separate personal Google/Microsoft consent for read-only search or reviewed calendar commands, including refresh and source-bound revocation. | Separate customer web clients per purpose; account sign-in and channel sync grants remain distinct. [Personal OAuth](PERSONAL_OAUTH.md). |
| Integration library | Bundled recipes and operator-approved immutable community packages installed by exact digest and channel grants. | Installation-local declarative catalog, no executable plugin installation or central marketplace. [Library](INTEGRATION_LIBRARY.md). |
| OTRUST | Explicit hash-only version requests, durable receipts, exact `.ots` download and proof-commitment checks. | A checked commitment is distinct from independently verified Bitcoin anchoring. [Version timestamps](OTRUST_INTEGRATION.md). |

## Customer-owned configuration

**Sign-in** configures the workspace's IdP, callback and login link. **People → Onboarding** selects the default personal-link flow or opt-in managed provisioned/invited/JIT accounts. **People → Directory & groups** supplies the SCIM endpoint, one-time provisioning token, group rules and audit. Customers register and manage their own provider applications; these are not the installation vendor's IdP credentials.

Personal accounts retain password/MFA login and explicit identity linking. Managed organization accounts can start through SSO without a separate FreeCollab password or global email verification. An equal email address never merges the two account types. SCIM and managed onboarding operate only within the configured customer namespace.

**Sync** separately connects the customer's cloud account or DAV app password. SSO does not authorize Drive or Calendar access. The native administrator must also administer the selected channel, explicitly consent to sharing its existing and future files/events, connect the provider, test the saved draft and enable it. Existing provider permissions are not copied into FreeCollab roles, and FreeCollab channel roles do not change remote provider ACLs.

The [remote sync guide](REMOTE_SYNC.md) describes provider setup, resource limits, conflict handling and deletion behavior.

## Enable outbound integrations

Document links, local calendar exchange, customer SSO and SCIM configuration need no installation-wide customer provider credentials. Configure the trusted HTTPS `PUBLIC_APP_URL` and retain the independent `ACCOUNT_SECURITY_KEY` that protects customer secrets and account factors.

Webhooks, OTRUST, background synchronization, cloud file picking and external meeting services are disabled by default:

```dotenv
WEBHOOKS_ENABLED=false
OTRUST_ENABLED=false
OTRUST_BASE_URL=https://www.otrust.eu
OTRUST_TIMEOUT_SECONDS=10
SYNC_ENABLED=false
CLOUD_FILES_ENABLED=false
EXTERNAL_MEETINGS_ENABLED=false
```

Set only the intended flags to true in private deployment configuration, then rebuild/recreate the API and start its worker:

```bash
docker compose --env-file pilot.env -p freecollab \
  up --build -d --wait backend frontend integration-worker
```

Keep an existing private Matrix service and its durable identity. New encrypted installations follow [E2EE setup](E2EE_IMPLEMENTATION.md). Mail uses the separate `mail` profile. Do not place client secrets, app passwords, refresh tokens, signing keys or metrics credentials in frontend build variables.

Outside Compose, run from `backend` using the same database, independent keys and private configuration as the API:

```bash
python -m app.services.integration_worker --interval 10
python -m app.services.integration_worker --once
```

The worker records its heartbeat and processes bounded batches for each enabled queue in independent database sessions. A failed provider transaction does not roll back other groups. Groups run serially on the supported single worker, so a slow provider can delay later groups; the dashboard and metrics cover all 14 groups. [Integration operations](INTEGRATION_OPERATIONS.md) explains the actual counters and heartbeat limits. Flags prevent dispatch while disabled, even if pending work exists. Durable sync mappings, claims, cursors and retry times survive restarts. Webhook delivery remains at least once; receivers must deduplicate event IDs.

## Encryption and disclosure

Secure-channel document titles/URLs and calendar data remain encrypted Matrix application events with opaque native references. No worker receives their message keys. Server background sync refuses secure or preparing channels; it never converts them to plaintext. Their existing document links and explicit local ICS import/export remain available.

Native channels use the server-encryption model: the server can read calendar content, and native file bytes remain unencrypted at the application-storage layer. Enabling cloud synchronization shares the selected channel's existing and future supported content with the selected provider resource. Changes and deletions may travel both ways. Files and Calendar show members a connection notice. Pausing or disconnecting does not remove copies previously received by the provider.

Opening a document link sends the browser to its provider. Calendar export decrypts locally after an explicit action. Webhooks disclose identifiers/types/times. An OTRUST request additionally discloses the selected version digest; known content may be correlated from that digest.

A personal account linked through customer SSO still needs a native password/MFA encryption unlock lasting up to 30 minutes, because its personal Matrix identity may span workspaces. A managed account has a separate workspace-only identity and needs no native-password unlock. Trusted devices and the separate recovery key remain necessary in both cases; SSO cannot recover encrypted history by itself.

## Access removal and operations

Provider edits/disabling and removed membership invalidate customer SSO access on authorization. An IdP-only account disable does not revoke an already-issued session; SCIM `active=false` or deletion performs the workspace offboarding. Directory group rules grant only explicitly approved workspace/channel access. They never create owners or installation administrators. Disabling directory provisioning itself pauses incoming requests; it does not deactivate all current users.

Sync rechecks the connection revision and authorizing administrator's current workspace/channel rights around network work. Loss of the required grant stops dispatch. Activity is distinct from queued work; a queued cycle is not proof that remote data changed. Conflicts require a selected version rather than silent overwrite.

Global operators can inspect private diagnostics and limited Prometheus metrics. A separate `OPERATIONS_METRICS_TOKEN` reads only metrics. Customer admins cannot inspect the global mail outbox. Real SMTP/IdP/cloud consent, public DNS/TLS and alert routing remain deployment configuration.

Cold backup stops integration workers with other writers and records their exact images. Restore keeps the outbound worker stopped by default; inspect retained destinations and pending work before using `--resume-integrations`. Preserve the account-security key for OIDC secrets/PKCE, cloud tokens/app passwords, webhook secrets and native factors. Never regenerate production keys during an upgrade.

[VALIDATION.md](VALIDATION.md) records executed local checks separately from hosted provider validation. Protocol fixtures and browser proofs do not establish a real customer-tenant connection, live n8n deployment, hosted Bitcoin anchoring, public deployment or independent security review.

[The dated integrations audit](audit/INTEGRATIONS.md) traces each promised connection to its actual UI, API, provider/worker and tests, including preparation-only behavior and remaining live-customer acceptance.
