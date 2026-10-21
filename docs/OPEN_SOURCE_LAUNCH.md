# Open-source website and self-hosted launch

FreeCollab is MIT-licensed software. The public website is intended for `freecollab.eu`; each organization operates its own installation, identity-provider apps, provider grants and infrastructure. The website does not create a managed hosting subscription or a shared Microsoft/Google identity service.

## Public website and demo

The landing page introduces conversations, files, documents, projects, meetings and personal appearance. It links to the source, setup documentation and an account-free interactive demo at `/#demo`. The same frontend bundle includes the working authenticated application.

The demo is a browser-only showroom with synthetic colleagues and sample content. Visitors can send sample messages, add and complete tasks, edit/download Markdown, explore Microsoft 365/Google setup checklists and change the demo's colors/type. Changes remain in React memory and disappear on reset or reload. It does not create a server account, invoke AI, authorize a provider, send email or demonstrate live cryptography. Provider callbacks in the showroom are clearly examples; each real installation supplies its exact addresses through its authenticated connection guide.

The demo uses no analytics or visitor-content collection. Changing the site's language can save a guest language preference locally; demo content is not persisted. Actual authentication, cloud authorization and encrypted collaboration belong to the real application.

## A customer's first workspace

1. Install with the [Railway guide](../RAILWAY_DEPLOY.md) or the existing [Compose operations runbook](OPERATIONS.md). Retain independent account/content keys, persistent uploads and PostgreSQL. Configure the installation's own HTTPS origin.
2. Create an account and workspace. **Get started** opens the five-part launch guide: workspace identity, personal appearance/language, first native channel, team invitations and customer connections. A newly created empty workspace receives a suggestion; established workspaces are not interrupted by an automatic modal.
3. Use **Integrations → Connection setup** for both Microsoft 365 and Google Workspace, including customer SSO, files, calendars and external meetings. App registration, provider permissions and administrator consent remain in the customer's environment. The guide supplies exact callbacks, scopes and fields. Saving, authorizing, testing and enabling are explicit actions.
4. Use **Integrations → Connection health** to review saved services, pending setup/test steps, paused connections and current access. Selecting a repair opens the exact recipe/channel. These checks do not claim provider uptime or a future expiry date that the provider has not supplied.
5. An installation operator can review **Integrations → Operations → Deployment readiness**. Workspace administrators cannot read installation-wide diagnostics. Unknown external TLS/backup/proxy evidence stays unknown until the operator verifies it separately; see [deployment readiness](DEPLOYMENT_READINESS.md).

Workspace feedback is optional and stays in that installation. People explicitly save their own task outcomes, ratings and private encrypted notes. Administrators see counts, not participants' private notes or identities. People can delete their reports and include their own records in account export; account closure erases their retained feedback. No participant cohort or feedback submission is required to use the software.

## Build FreeCollab into another product

The [0.10 component SDK](COMPONENT_SDK.md) supplies independent native modules and a trusted-origin embedded workspace. A workspace administrator opens **Integrations → Embedding**, registers the exact product origin and copies a channel-specific SDK example. The host and trusted client use separate origins. The customer's configured OIDC provider signs users in; the parent receives a one-use PKCE launch code rather than a bearer session or encryption keys. The iframe runs the actual client and its Matrix device/recovery controls, with a renewable server lease bounded by the original customer session. Native inline components use their separately documented session factory and reject encrypted channels.

The reviewed package can be installed from its generated tarball. [Registry publication](SDK_RELEASES.md) requires the maintainer's npm scope/package ownership and trusted-publisher configuration; preparing a workflow or artifact does not publish it. Host logout and account changes must call the SDK teardown methods. External providers, Office applications, media permissions and browser storage policy retain their documented requirements.

## Release evidence and operation

[Validation](VALIDATION.md) separates executed private tests from actual public operation. A passing fixture is not a real customer's Microsoft/Google tenant, a successful public Railway deployment or a usability study. Deployment configuration is concrete and reviewable, but the repository cannot provision a user's provider account or infer authorization to an existing hosting project.

For the public site, use the built-in social card and the two distinct entry points: **Explore the demo** and **Open App**. Do not put real provider credentials in the public bundle. Provider secrets belong in the authenticated customer setup forms and server-side secret storage.

[The open-source scorecard](OPEN_SOURCE_SCORECARD.md) describes where FreeCollab differs from Weavy and where further evidence is needed. Functional breadth alone does not establish performance, usability or operational superiority.
