# Open-source website and self-hosted launch

FreeCollab is MIT-licensed software. The public website is intended for `freecollab.eu`; each organization operates its own installation, identity-provider apps, provider grants and infrastructure. The website does not create a managed hosting subscription or a shared Microsoft/Google identity service.

## Public website and demo

The landing page introduces conversations, files, documents, projects, meetings
and personal appearance. It links to source, setup documentation and the
[product demo](PRODUCT_DEMO.md) at `/#demo`. The website opens the real product
client against its own temporary sample backend. `Chat`, `WorkHub`, providers,
forms and ordinary APIs are shared with customer installations; there is no
separate illustrated implementation of documents, tasks or AI.

Visitors receive an isolated temporary account/workspace containing synthetic
colleagues and native sample content. Their messages, document updates, task
changes and files use the normal API/model contracts. A visible banner shows
expiry and an explicit reset action. Session credentials remain in memory;
retained customer account storage is ignored. Reload/reset opens a new example
visit. The demo is unsuitable for private or durable work and has bounded
session/storage/write quotas.

The public sample instance deliberately has no customer provider credentials,
SMTP, Matrix, federation, push or external media services. Its real UI shows
those unconfigured/disabled states or a clear sample-environment restriction.
It never fabricates successful AI, SSO or provider synchronization. Customers
configure those optional services in their own installation. See
[local/LAN/VPN self-hosting](SELF_HOSTING.md) and
[the website deployment guide](../deploy/website/README.md).

Provider links may open the existing matching connection-setup guide in the real
product. Preparing a guide is separate from connecting a service or giving
consent. Public demo telemetry and logs must not collect bearer tokens or content
as evidence; browser checks use synthetic data and bounded source/report hashes.

## A customer's first workspace

1. Install with the [Railway guide](../RAILWAY_DEPLOY.md) or the existing [Compose operations runbook](OPERATIONS.md). Retain independent account/content keys, persistent uploads and PostgreSQL. Configure the installation's own HTTPS origin.
2. Create an account and workspace. **Set up this workspace** appears in its empty room; **Get started** also opens the five-part launch guide: workspace identity, personal appearance/language, first native channel, team invitations and customer connections. The team step distinguishes the accepted workspace invitation from a separate channel invitation. **Open channel invitations** opens the existing member panel for the selected channel; its administrator issues that explicit invitation. **Write the first message** opens the real composer. Established workspaces are not interrupted by an automatic modal.
3. Use **Integrations → Connection setup** for both Microsoft 365 and Google Workspace, including customer SSO, files, calendars and external meetings. App registration, provider permissions and administrator consent remain in the customer's environment. The guide supplies exact callbacks, scopes and fields. Saving, authorizing, testing and enabling are explicit actions.
4. Use **Integrations → Connection health** to review saved services, pending setup/test steps, paused connections and current access. Selecting a repair opens the exact recipe/channel. These checks do not claim provider uptime or a future expiry date that the provider has not supplied.
5. An installation operator can review **Integrations → Operations → Deployment readiness**. Workspace administrators cannot read installation-wide diagnostics. Unknown external TLS/backup/proxy evidence stays unknown until the operator verifies it separately; see [deployment readiness](DEPLOYMENT_READINESS.md).

Workspace feedback is optional and stays in that installation. People explicitly save their own task outcomes, ratings and private encrypted notes. Administrators see counts, not participants' private notes or identities. People can delete their reports and include their own records in account export; account closure erases their retained feedback. No participant cohort or feedback submission is required to use the software.

## Build FreeCollab into another product

The [component SDK](COMPONENT_SDK.md) supplies independent native modules and a trusted-origin embedded workspace. A workspace administrator opens **Integrations → Embedding**, registers the exact product origin and copies a channel-specific SDK example. The host and trusted client use separate origins. The customer's configured OIDC provider signs users in; the parent receives a one-use PKCE launch code rather than a bearer session or encryption keys. The iframe runs the actual client and its Matrix device/recovery controls, with a renewable server lease bounded by the original customer session. Native inline components use their separately documented session factory and reject encrypted channels.

The reviewed package can be installed from its generated tarball. [Registry publication](SDK_RELEASES.md) requires the maintainer's npm scope/package ownership and trusted-publisher configuration; preparing a workflow or artifact does not publish it. Host logout and account changes must call the SDK teardown methods. External providers, Office applications, media permissions and browser storage policy retain their documented requirements.

## Release evidence and operation

[Validation](VALIDATION.md) separates executed private tests from actual public operation. A passing fixture is not a real customer's Microsoft/Google tenant, a successful public Railway deployment or a usability study. Deployment configuration is concrete and reviewable, but the repository cannot provision a user's provider account or infer authorization to an existing hosting project.

The independent public site uses the built-in social card and **Explore the demo**. The full application also supplies **Open App** for its own authenticated installation. Do not put real provider credentials in the public bundle. Provider secrets belong in the authenticated customer setup forms and server-side secret storage.

[The open-source scorecard](OPEN_SOURCE_SCORECARD.md) describes where FreeCollab differs from Weavy and where further evidence is needed. Functional breadth alone does not establish performance, usability or operational superiority.
