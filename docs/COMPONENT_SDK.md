# Modular component SDK

`packages/components` builds **`@freecollab/components@0.12.0`**, a distributable,
typed ESM package. Native modules mount independently inside a host application;
the trusted-origin embed runs the full application, including its existing
Matrix encryption runtime. Web Components and optional React wrappers are also
available. The floating widget remains a separate legacy integration.

The SDK is pre-1.0. Read the [compatibility contract](../packages/components/COMPATIBILITY.md)
and [artifact/publication procedure](SDK_RELEASES.md). This source builds a
reviewable tarball; it does not establish an npm publication or hosted service.

## Build and install

From the repository root:

```sh
npm ci --prefix packages/components --ignore-scripts
node packages/components/scripts/build.mjs
(cd packages/components && npm test)
(cd packages/components && npm pack)
```

The build uses the package's pinned TypeScript/esbuild tools when installed, or
the monorepo's existing frontend tools. A standalone package checkout supports
`npm ci --ignore-scripts && npm run release:check`. React is an **optional peer**; the
framework-neutral components have no runtime framework dependency. The tarball
contains compiled modules, declarations, the optional CSS token preset and the
MIT licence. Install a reviewed artifact with `npm install /path/to/freecollab-components-0.12.0.tgz`.
Packing does not publish the package to any registry.

| Entry point | Public API |
| --- | --- |
| `@freecollab/components` | `createClient`, `CollaborationClient`, `SDKError`, public types |
| `/chat` | `mountChat` |
| `/files` | `mountFiles` |
| `/feed` | `mountFeed` |
| `/notifications` | `mountNotifications` |
| `/tasks` | `mountTasks` — projects and tasks |
| `/documents` | `mountDocuments` — Yjs shared documents |
| `/meetings` | `mountMeetings` — start/list/end, with host navigation for joining |
| `/calendar` | `mountCalendar` — channel events |
| `/workflows` | `mountWorkflows` — definitions, runs and approval actions |
| `/decisions` | `mountDecisions` — decision records |
| `/search` | `mountSearch` — authorized channel message search |
| `/auth` | Customer-session helpers and hosted sign-in |
| `/embed` | Trusted-origin workspace and encrypted chat mounts |
| `/elements` | `defineCustomElements`, typed `CollaborationElement` |
| `/react` | Native module wrappers, including `Chat`, `Files`, `Tasks` and `Documents` |
| `/styles.css` | Optional `.freecollab-theme` token preset |

## Your application's identity

```ts
import {createClient} from '@freecollab/components';
import {mountChat} from '@freecollab/components/chat';

const client = createClient({
  apiBase: 'https://collaboration.your-company.eu/api',
  workspaceId: 'workspace-id',
  channelId: 'channel-id',
  tokenFactory: async ({scope, reason, signal}) => {
    return yourApplication.currentCollaborationToken({scope, reason, signal});
  },
});
const chat = mountChat(document.querySelector('#conversation')!, client, {
  locale: 'sv',
});
```

The factory returns a current **FreeCollab native user JWT or customer-workspace
SSO JWT**, or `null` when signed out. Arbitrary external IdP access tokens are not
accepted by the native API. Use the deployment's existing customer-owned OIDC
sign-in/provisioning flow to obtain the scoped FreeCollab session. The customer's
identity provider, application and backend own that integration. The SDK does not
require a central FreeCollab login, invent a shared browser API secret, provision
users automatically, or accept an administrative API key as a browser credential.

The factory receives an abort signal and an immutable workspace/channel scope.
It is checked before each request; concurrent requests share one authorization
step. The SDK verifies `/auth/me` on a new session and refreshes `/channels/{id}` before
reading native content, and rejects a workspace mismatch. Tokens are held in
memory and sent only in the `Authorization` header. They are not stored in HTML
attributes, URLs, local storage, session storage or cookies by the SDK. API fetches
use `credentials: omit`, `cache: no-store` and reject redirects. HTTPS is required
except on loopback for development. Configure the deployment's explicit CORS
allowlist for the host application's origin.

Client-side scope is a component boundary, not a replacement for server
authorization. Prefer workspace-scoped SSO sessions when embedding customer
workspaces. A native account JWT may authorize more than one workspace; the
server remains responsible for every resource grant.

## Session helpers and customer-owned sign-in

For an existing host session backend, use
`createSessionTokenFactory({endpoint: '/your-app/collaboration-session'})` from
`@freecollab/components/auth`. It POSTs `{workspace_id, channel_id}` to that
same-origin endpoint using the host's cookies, rejects redirects and returns
`null` for a signed-out response. The endpoint is implemented by your own backend:
it must authorize the current host session, check scope and enforce its CSRF
policy. A successful response contains a real `access_token`; the helper is not
an external-IdP-token exchange and does not create accounts by trusting email.

For the trusted full workspace, `createEmbedSignIn` uses the actual customer's
configured OIDC flow on the trusted application origin. The host needs no custom
backend token endpoint for this path. Register the host's exact origin as an
embedding application first: a native workspace administrator opens
**Integrations → Embedding**, confirms password/MFA and registers the exact host
origin. The screen supplies an actual SDK example. Editing or disabling the
application invalidates its sessions. See [EMBED_IDENTITY.md](EMBED_IDENTITY.md).

```ts
import {createEmbedSignIn} from '@freecollab/components/auth';
import {mountWorkspace} from '@freecollab/components/embed';

const appUrl = 'https://collaboration.your-company.eu';
const applicationId = 'registered-application-id';
const signIn = createEmbedSignIn({appUrl, applicationId, locale: 'sv'});
let workspace;
connectButton.onclick = () => {
  signIn.open(); // Synchronous user click; do this before awaiting anything.
  workspace?.destroy();
  workspace = mountWorkspace(document.querySelector('#collaboration')!, {
    appUrl, applicationId, workspaceId: 'workspace-id', channelId: 'channel-id',
    module: 'workspace', locale: 'sv', grantFactory: signIn.grantFactory,
    onState: ({status}) => yourApplication.showCollaborationStatus(status),
  });
  void workspace.ready.catch(() => yourApplication.showSignInError());
};
// Immediately on host logout/account change or route teardown:
// workspace?.destroy(); signIn.destroy();
```

The trusted application origin **must differ from the host origin**. Use separate
origins such as `app.company.eu` and `collaboration.company.eu`. The popup returns
only a short-lived, one-use launch code bound to PKCE, application, workspace,
channel and parent origin. The iframe exchanges it for its isolated session.
Neither its bearer session nor content keys are sent to the parent page.
No bearer credential or content key is placed in a host URL. The customer remains responsible for OIDC app
registration, native authorization, production HTTPS and trusted client delivery.

`mountWorkspace` accepts `module: 'workspace' | 'chat' | 'tasks' | 'documents' |
'meetings' | 'calendar' | 'workflows' | 'decisions' | 'files' | 'feed' |
'integrations' | 'search' | 'copilot' | 'cloud' | 'external-meetings'`.
`mountSecureChat` uses the same configuration without `module` and requires an
encrypted channel; it never falls back to native content. The embedded application
still enforces enabled features, customer grants and channel permissions.

Both mounts return `{ready, reauthenticate, setScope, destroy}`. Authentication
or scope changes synchronously remove the old frame and require a new grant.
Call `signIn.open()` from a user click before an action that needs another popup;
a fresh mount after host account change is the simplest explicit lifecycle.
Destroy the controller immediately on host logout; hiding it with CSS does not
end its lifetime. `onState` and `onError` contain safe lifecycle information,
not decrypted messages, filenames, provider tokens or Matrix keys.

An embedded child session has an authoritative server-side lease of at most
ten minutes. The mounted client renews after 60% of its current lease has elapsed;
each renewal rechecks the original source session, embedding application, provider
revision and current membership. It can extend the lease only within the original
source session's lifetime. An expired lease cannot be revived by renewal.

The child JWT is stable: its signed expiry never exceeds the original source
session's expiry, and lease renewal does not rotate that JWT. The existing Matrix
device identity, verification, unlocked state and drafts remain in the mounted
client during ordinary renewal. Users do not repeat sign-in or device verification
every ten minutes. Renewal also does not refresh the source authentication time,
methods or recent-authentication grants; sensitive actions retain their normal
confirmation requirements.

Transient network renewal failures receive bounded retries, with no offline
writes. A rejected authority check or an expired lease clears the embedded
content/runtime and emits `signed-out`. Source expiry requires a new explicit
sign-in. Source logout, disabled applications/providers and removed access cannot
be bypassed by renewal. Host logout/account changes must still immediately call
the documented teardown action. Online teardown requests revocation and checks
its acknowledgement; an offline or abruptly closed frame cannot renew and its
remaining lease expires within ten minutes.

Embedded session credentials and drafts are memory-only; encrypted Matrix device
storage remains in the trusted origin's IndexedDB. Standalone application session
storage is not reused or overwritten by the frame.

The embedded client uses online writes. Its offline queue, push enrollment and
PWA installation are disabled in embedded mode. Deploy the trusted client with
its same-origin `/api` proxy and the fixed `/embed-entry.js`, CSS and generated
assets produced by the normal frontend build; a separately hosted API URL alone
does not supply that document/bootstrap route.

Set `media: true` only when the host should permit camera, microphone and screen
capture inside the frame; the browser still asks for actual permission. The
default disallows those media capabilities. Optional `theme` fields are `accent`,
`background`, `surface`, `text` and `font`; the trusted client validates them.
Normal appearance, language, device verification and recovery remain available.
Browser storage restrictions may prevent embedded crypto from working; the client
must surface that failure rather than downgrade encryption. Neither iframe
isolation nor E2EE protects against malicious code delivered by the trusted
FreeCollab application origin.

## Account changes, navigation and teardown

Call `client.reauthenticate()` **immediately** from the host's login, account
switch, revocation and logout callbacks. It clears mounted content synchronously,
aborts old work and obtains the current user from the token factory. A token
change discovered during submission cancels the old draft instead of sending it
under the new identity. A factory returning `null` leaves the components signed
out. Call `client.setScope({workspaceId, channelId})` for explicit channel
navigation; it performs the same cleanup and new authorization.

Each mount returns `{refresh, destroy}`. Destroying a view removes its private
DOM, stops polling and aborts its requests. The host session service owns the
shared client and calls `client.destroy()` when that session ends. A destroyed
client cannot be reused. React wrappers own only their view lifetime and support
StrictMode effect replay; keep the shared client in the host session service.
An already committed server write cannot be undone by aborting a browser request.

```ts
const unsubscribe = client.on('navigate', ({scope, entityType, entityId}) => {
  yourRouter.openCollaborationItem(scope, entityType, entityId);
});
client.on('notification', ({notification}) => yourApplication.showBadge(notification));
client.on('state', state => yourApplication.showCollaborationStatus(state.status));
client.on('error', error => yourApplication.reportSafeCode(error.code));

unsubscribe();
chat.destroy();
client.destroy();
```

Notification navigation emits typed identifiers and never follows a server
`action_url` automatically. Only channel-scoped notifications are requested;
the SDK rejects older servers that cannot prove each returned notification's
channel. Notification and file actions are limited to the current displayed
channel resources. Server revocation detected during a read, write or download
clears the client session.

## Frameworks and customization

```js
import {defineCustomElements} from '@freecollab/components/elements';
defineCustomElements(); // Explicit and idempotent.
const chat = document.querySelector('freecollab-chat');
chat.options = {locale: 'sv', pollIntervalMs: 5000};
chat.client = client; // A JS property, never a token attribute.
```

The registered tags are `freecollab-chat`, `freecollab-files`, `freecollab-feed`,
`freecollab-notifications`, `freecollab-tasks`, `freecollab-documents`,
`freecollab-meetings`, `freecollab-calendar`, `freecollab-workflows`,
`freecollab-decisions` and `freecollab-search`. They forward `fc-state`, `fc-error`,
`fc-notification` and `fc-navigate` events. Use one shared client for components
in the same channel. Removing an element disposes its own view.

React, Vue, Angular and Svelte integration examples are in `examples/sdk`.
The native web components use open shadow roots and static styles. Host CSS is isolated;
all user content and filenames are rendered as text. Feed content is text-only.
Files download through an authenticated request as attachments rather than
executing uploaded HTML in the host page.

Set these CSS variables on an individual component or a containing element:
`--fc-sdk-background`, `--fc-sdk-surface`, `--fc-sdk-text`, `--fc-sdk-muted`,
`--fc-sdk-border`, `--fc-sdk-accent`, `--fc-sdk-accent-text`, `--fc-sdk-danger`,
`--fc-sdk-radius`, `--fc-sdk-font`, and `--fc-sdk-heading-font`. No external font
or stylesheet is loaded. Components support English and Swedish through
`locale: 'en' | 'sv'`; override individual labels with `labels` and safe error
messages by code with `errorMessages`.

## Native content and encryption boundary

These components support **native channels whose server processes content**.
HTTPS protects transport; server-side encryption protects eligible stored
records. Neither is end-to-end channel encryption. The SDK checks channel
security metadata and **fails closed for encrypted channels before requesting
or submitting native content**. The backend independently enforces that fence.

The `/embed` entry point runs the existing Matrix crypto runtime on the trusted
FreeCollab application origin. Device ownership, unlock/recovery and verified
membership remain in that isolated client. Native mounts continue to refuse
secure-channel content; they never downgrade it or silently send it through the
server-readable API. The package does not import the legacy widget.

Chat and Feed display the latest 50 records; Files displays up to 100 current
files. Notifications displays up to 50 channel records. Native Search searches
the latest 500 authorized channel messages locally; it is not unbounded history
search. The full trusted application provides its existing history workflows. Polling is serialized per
view and bounded to 1.5–120 seconds. Requests are not persisted for offline retry.
Native Chat also exposes thread/reaction/edit actions through the scoped client;
Files retains authenticated download and channel resource boundaries. The new
modules use the production project/task, Yjs document, calendar, decision,
meeting and workflow APIs. Tasks load at most 1,000 records per project. Shared
documents use a bounded in-memory Yjs text editor with two-second synchronization.
Calendar exposes create/remove; Meetings exposes list/create/end and typed host
navigation to the trusted media client. Workflows exposes list/start and
approve/reject, with navigation for administrative configuration. Module screens expose their documented actions rather
than claiming full editor parity. Use the trusted full workspace for the full
meeting media, workflow designer, provider connections, Office, cloud files,
Copilot and other application tools. A host router handles native `navigate`
events; identifiers alone do not authorize access.

## Acceptance and release checks

```sh
backend/.venv/bin/python packages/components/tests/browser-proof.py
```

The native harness builds and packs the actual package, extracts the tarball,
and runs a standalone host page against a private instance of production APIs
with real workspace-scoped JWTs. It exercises the native modules, exact file
bytes, account changes, outsider denial, logout, encryption fencing, teardown,
safe rendering, narrow layout and packed React wrappers. Its customer-login
endpoint is explicitly a private fixture. Separate auth and trusted-embed proofs
exercise their own origin, grant and device boundaries. No local fixture
establishes a live external customer tenant, deployment or registry publication.
See [VALIDATION.md](VALIDATION.md) for the checks actually executed and evidence.
