# SDK compatibility contract

`@freecollab/components` **0.12.x** targets FreeCollab **0.12.x** servers and the
matching trusted embedded client. It is a pre-1.0 API distributed under the npm
`next` tag. Install an exact version or reviewed tarball during evaluation.

- Patch releases preserve documented entry points, method signatures, DOM event
  meanings and session isolation. Security corrections may reject previously
  accepted unsafe input or obsolete server behavior.
- A minor release before 1.0 may change public APIs. It must include a migration
  note and a new server/client compatibility line. We do not promise silent
  compatibility with older 0.x servers.
- JavaScript mounts, Web Components and optional React wrappers are web APIs.
  Supported host browsers are current Chromium, Firefox and Safari with Fetch,
  AbortController, Web Crypto and Shadow DOM. Product browser proofs record the
  exact engines exercised; this support target does not claim a completed browser
  matrix. The embedded encrypted client also requires IndexedDB and its Matrix
  crypto runtime; private browsing and third-party storage policy may restrict it.
- Build tooling requires Node 22.12 or later. React 18.2 through 19 is an optional
  peer. Framework-neutral modules do not import React. Shared-document operations
  use the pinned Yjs dependency; ordinary application bundlers resolve it.
- Native components authorize a specific workspace/channel through the server
  before content access. They process server-readable channel content and refuse
  encrypted channels. The trusted-origin `/embed` adapter runs the actual
  encrypted client in an isolated frame; it does not expose content keys or
  decrypted history through parent-page events.
- Customer OIDC, provider registration, exact host origins and production TLS
  remain deployment configuration. An arbitrary IdP JWT is not a FreeCollab user
  session. Account changes and logout must call the documented lifecycle methods.
- Embedded sessions use an authoritative server lease of at most ten minutes.
  The mounted client renews it before expiry after checking the original source
  session, application, provider and membership. Renewal preserves the child JWT,
  Matrix device and verification, and never extends the source session's original
  signed expiry or refreshes authentication methods/recent-authentication time.
  An expired lease cannot be revived. Offline/closed frames cannot renew; online
  teardown requests acknowledged revocation. Host logout requires immediate
  teardown. An expired/revoked source requires a new explicit sign-in.
- Embedded drafts are not queued offline; push/PWA installation is outside the
  embedding contract. The trusted client document and its assets must be deployed
  together with their same-origin API proxy.
- No private source file, generated chunk name, CSS implementation class or
  unexported method is part of the public API. Use exported types, host events and
  documented CSS variables. Destroy mounted views and clients when their host
  session or route ends.

`dist/release.json` identifies the SDK version, API line and deterministic source
digest. `release-manifest.json` and `SHA256SUMS` accompany the packed artifact.
Checksums identify reviewed bytes; they do not replace a trusted download origin,
npm provenance, provider acceptance or an independent security review.
