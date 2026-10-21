# Guided customer connections

Open **Integrations → Connection setup** as a personal workspace administrator. The connection center prepares the services already supported by FreeCollab: workspace SSO and SCIM, Google/Microsoft file and calendar synchronization, Nextcloud/CalDAV, Dropbox/Box file selection, Meet/Teams/Zoom, an OpenAI-compatible AI provider and signed webhooks/n8n.

Choose a service, prepare its provider application, configure it through the existing connection form, and check its saved state. The guide supplies the exact callback address, required permissions, provider documentation and a downloadable setup checklist. It does not supply a shared FreeCollab identity provider or vendor-owned OAuth credentials.

## First installation and first customer setup

1. The installation operator configures the real public HTTPS `PUBLIC_APP_URL`, retains the existing independent account/content keys and enables only the features the installation intends to offer. Background sync and webhook delivery also need the integration worker. These are installation settings; the setup guide does not silently enable them.
2. The customer registers its own provider application once, using the guide's exact callback and permissions. Its provider administrator grants any required tenant consent. The customer retains its app registration, billing, licences, credential rotation and revocation controls.
3. A customer administrator supplies that application's identifiers and secret in the appropriate connection form, chooses the explicit channel/folder/calendar where needed, and confirms with the existing password/MFA flow. Cloud OAuth consent happens at the provider. The application never saves or authorizes a connection just because a recipe was opened.
4. Run the provider's explicit saved-connection test where supported. Enable SSO, synchronization or a meeting connection only through its normal confirmation flow. File selection and AI use retain their own per-action consent and current channel access checks.

Once the customer app is registered, connecting an account uses the prepared authorization flow. Registering apps or granting administrator consent cannot be skipped without authority in the customer's provider account. Ordinary members use the features granted to their channel; the connection center itself is an administrator tool.

Use the client type and secret required by the provider. PKCE supplements a confidential Web application's client secret; it does not replace it. Omit a secret only for a public-client configuration that the provider explicitly permits for this server callback flow.

## Identity presets

The workspace SSO form includes helpers for Microsoft Entra ID, Google, Okta, Auth0, Keycloak and generic OpenID Connect. Tenant/domain/realm fields construct the customer's issuer; generic OIDC permits a manually supplied public HTTPS issuer. These are application-owned suggestions, not a federation service operated by FreeCollab.

Applying a preset does not save, test or enable it. Existing saved configuration remains unchanged until the administrator confirms a save. A different issuer or client application needs its own secret; the old secret must not be forwarded to another provider. The underlying API still enforces this independently of the UI.

SSO authenticates people. Cloud-file, calendar and meeting OAuth grants separately authorize provider resources. Configuring Entra or Google for sign-in does not give FreeCollab permission to read that customer's files. SCIM provisioning has a separate one-time bearer credential and explicit directory/group rules. See [OIDC](OIDC.md).

## Preparation files and status

The downloaded JSON is a preparation checklist, not a credential backup or an importable provider application. It includes the selected service's public callback, scopes, field names/labels and required/secret flags, requirements, steps and documentation. The displayed SSO scopes can reflect the saved workspace provider. It excludes session tokens, client secrets, provider refresh/access tokens, AI keys, SCIM/webhook signing secrets, saved connection records and resource identifiers. Keep secret values in the dedicated authenticated forms and the customer's secret store.

The setup API is `GET /workspaces/{workspace_id}/connection-setup`, optionally filtered by `channel_id`. It is private, read-only and limited to a current unrestricted workspace administrator. A selected channel additionally requires current channel administrator membership in that workspace. Setup reads do not contact providers or decrypt stored credential blobs. A workspace administrator does not acquire access to another person's private channel.

Displayed readiness describes saved local configuration and grants. It is separate from a new live-provider test, successful SSO login, completed synchronization, delivered webhook, created meeting or AI response. Refresh rechecks the current stored state; disabled features, revoked grants and stale revisions must not be presented as connected. An unavailable callback/public origin is shown as a prerequisite rather than an invented address.

## Provider boundaries

Google/Microsoft file recipes use the existing two-way sync grant, including its write permissions; the read-only picker can reuse that same grant. Dropbox/Box file selection uses the separate read-only picker flow. Preparing or downloading a recipe does not reduce a previously consented grant's scope.

Server background sync and file import refuse end-to-end encrypted channels. Trusted-client encrypted provider links remain available separately. External meetings and AI retain their explicit secure-client disclosure rules. Selecting a recipe never decrypts or sends channel content.

Unsaved connection changes and in-flight operations guard navigation. One-time SCIM/webhook credentials must be acknowledged before leaving their panel. Swedish/English labels follow the account's language; customer names, resource IDs and provider interface text retain their original values.

See [Cloud files](CLOUD_FILES.md), [Remote sync](REMOTE_SYNC.md), [External meetings](EXTERNAL_MEETINGS.md), [Copilot](COPILOT.md), [Webhooks](WEBHOOKS.md) and [Validation](VALIDATION.md) for protocol behavior and verification boundaries.
