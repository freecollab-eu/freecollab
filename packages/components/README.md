# @freecollab/components

Collaboration inside your product, using your identity provider and infrastructure.
Version **0.12.0** is a pre-1.0 SDK. Native modules and the trusted embedded
workspace cover different integration needs:

- Mount Chat, Files, Feed, Notifications, Tasks, Documents, Meetings, Calendar,
  Workflows, Decisions and Search independently. Use JavaScript, Web Components
  or optional React wrappers; the native server processes their content.
- Mount the trusted FreeCollab workspace for the full application and encrypted
  channels. The Matrix client and its device keys stay on the trusted application
  origin. The host page receives lifecycle events, not decrypted history or keys.
- Use the customer's own OIDC sign-in. Native mounts require a real current
  FreeCollab native/workspace session; the embedded flow supports hosted sign-in
  and short-lived launch grants. No administrative browser API key is used.

## Native components

```js
import {createClient} from '@freecollab/components';
import {mountChat} from '@freecollab/components/chat';
import {mountTasks} from '@freecollab/components/tasks';

const client = createClient({
  apiBase: 'https://collaboration.your-company.eu/api',
  workspaceId: 'workspace-id', channelId: 'channel-id',
  tokenFactory: async ({signal}) => yourApplication.currentCollaborationToken({signal}),
});
const chat = mountChat(document.querySelector('#conversation'), client, {locale: 'sv'});
const tasks = mountTasks(document.querySelector('#tasks'), client, {locale: 'sv'});

// Immediately when the host account changes or signs out:
await client.reauthenticate();
// On route/session teardown:
chat.destroy(); tasks.destroy(); client.destroy();
```

The host owns its session lifecycle and must return `null` when signed out.
Configure the backend's exact CORS origins. Native components fail closed for
encrypted channels; use `/embed` for those channels and the full application.
Theme with documented `--fc-sdk-*` CSS variables. No external font is loaded.

Calendar field types include optional bounded recurrence and occurrence
exceptions. Preserve these fields when updating an existing series. The small
native calendar component lists series and creates individual events; use the
trusted calendar embed for recurrence editing, invitations and shared-time UI.

The trusted workspace renews a server-side lease while mounted and authorized.
Renewal preserves its JWT and Matrix device; ordinary renewal does not prompt
for sign-in or trust verification again. Every renewal rechecks the original
source, application, provider and membership and cannot outlive the original
source session. Offline/closed frames cannot renew; their remaining lease expires
within ten minutes. Host logout must destroy the mounted workspace immediately.
Embedded drafts are memory-only and are not queued for offline writes.

## Build and distribution

From a standalone source checkout:

```sh
npm ci --ignore-scripts
npm run release:check
```

This builds typed ESM, runs package checks and generates the tarball plus a
SHA-256/SHA-512 release manifest in `artifacts/`. Install a reviewed tarball with
`npm install --save-exact /path/to/freecollab-components-0.12.0.tgz`.
This command does not publish the package to a registry. React is an optional
peer; Yjs is a pinned runtime dependency for shared documents.

Read [COMPATIBILITY.md](COMPATIBILITY.md) before upgrading. In the source
repository, `docs/COMPONENT_SDK.md`, `docs/EMBED_IDENTITY.md` and
`docs/SDK_RELEASES.md` describe authorization, trusted embedding, browser
boundaries, release prerequisites and exact integration examples. The SDK
contains no central hosted service and does not supply customer provider apps.
