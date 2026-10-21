# Integrate FreeCollab into a host product

Install the reviewed `@freecollab/components` tarball into your own application's
normal bundler. These files are source examples, not a CDN-hosted production
deployment. Use a disposable installation with synthetic data when evaluating.

| Example | Integration |
| --- | --- |
| `index.html` + `host.js` | Native Chat, Files, Feed and Notifications |
| `modules.html` + `modules.js` | Native Tasks, Documents, Calendar, Meetings, Workflows, Decisions and Search |
| `embedded.html` + `embedded.js` | Trusted-origin full workspace and customer-owned sign-in |
| `React.tsx` | Optional React wrappers |
| `Vue.vue`, `Angular.ts`, `Svelte.svelte` | Standards-based Web Components |

Replace the explicit application URL, registered embed application ID, workspace
and channel with values from your own deployment. The full workspace uses the
deployment's real customer OIDC configuration. Register the host's exact HTTPS
origin in its embed application settings. Loopback HTTP is for development only.
Do not use wildcard origins, permanent user tokens, administrative API keys or
customer client secrets in these files.

Native `tokenFactory` returns a current FreeCollab native/workspace JWT or `null`.
The example host session route in `host.js` is implemented by your application's
own authenticated backend. Arbitrary Microsoft/Google/IdP JWTs are not accepted
as a native FreeCollab session. The full embedded sign-in flow does not require
that custom host backend route.

Call native `client.reauthenticate()` immediately on login, logout and account
change; call the embed controller's corresponding lifecycle action or destroy it.
Destroy every mounted view and session client when their route ends. For encrypted
channels retain trusted-client device verification and separate recovery keys;
the host does not receive decrypted content through SDK events.

See [the SDK guide](../../docs/COMPONENT_SDK.md),
[customer embed identity](../../docs/EMBED_IDENTITY.md) and
[release procedure](../../docs/SDK_RELEASES.md) for the exact APIs and boundaries.
