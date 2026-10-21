# SDK release and distribution

The source release builds `@freecollab/components@0.10.0`. Building or packing
does not publish to npm, create a GitHub release, tag a repository or deploy an
application. This SDK is pre-1.0 and uses the npm `next` distribution tag.

## Build a reviewable artifact

From a standalone `packages/components` checkout with Node 24:

```sh
npm ci --ignore-scripts
npm run release:check
```

The command runs the package tests, builds all declared public entry points,
checks their declaration files, packs only the public allowlist and verifies
npm's SHA-1/SHA-512 against the exact resulting bytes. It writes:

```text
artifacts/freecollab-components-0.10.0.tgz
artifacts/release-manifest.json
artifacts/SHA256SUMS
```

An alternative output directory can be passed with
`npm run release:artifact -- /absolute/review-directory`. That command builds and
verifies the artifact; use `release:check` when test execution is also required.
`dist/release.json` carries the package version, source digest and API line. The
tarball includes the MIT license and the [compatibility contract](../packages/components/COMPATIBILITY.md).
No runtime configuration, private test reports or secret values are included.

Install the reviewed artifact in the host application:

```sh
npm install --save-exact /path/to/freecollab-components-0.10.0.tgz
```

Once that exact version has actually been published, the corresponding registry
installation is `npm install --save-exact @freecollab/components@0.10.0`. That
command is a future registry option, not evidence of publication. The package's
Yjs runtime dependency is resolved by the host bundler; React is optional.

## Publication workflow

`.github/workflows/sdk-release.yml` is a manually invoked, two-job workflow.
By default it only tests, packs and uploads the artifact. A matching version
input is required. Publication additionally requires all of the following:

1. The source and workflow exist on `main` in `freecollab-eu/freecollab`.
2. The maintainer owns the `@freecollab` npm scope and package. Package creation
   and initial ownership are registry operations; the workflow does not invent
   or obtain that ownership.
3. The package's npm **Settings → Trusted Publisher** authorizes GitHub Actions:
   organization `freecollab-eu`, repository `freecollab`, workflow filename
   `sdk-release.yml` (filename only), with direct `npm publish` allowed. This
   workflow does not use a GitHub environment name.
4. The maintainer invokes the workflow with `publish: true` for the reviewed
   package version. npm must accept that identity and version.

The publication job receives only `contents: read` and `id-token: write`; it
downloads the artifact from the same run, checks its exact identity and hashes,
and publishes those bytes with provenance to `next`. It does not rebuild the
package, create commits, add coauthors, tag source or require an npm token in
GitHub secrets. Node 24 supplies the recent npm CLI required for OIDC publishing.
The ordinary test/pack job has no OIDC permission. The workflow cannot publish
from another repository or branch.

See npm's current [trusted publishing instructions](https://docs.npmjs.com/trusted-publishers/)
for account setup, allowed actions and any initial-package requirements. A local
`npm whoami --registry=https://registry.npmjs.org` check in this environment
returned `ENEEDAUTH`; no authenticated registry publication was available here.
Do not send registry credentials in chat or put them into example code.

## Upgrade from 0.9

The existing `createClient`, four original native mount functions and
`tokenFactory` contract remain available. Native encryption fencing remains in
place; encrypted channels now use the separate trusted-origin `/embed` path.
Use the existing customer OIDC configuration and register an explicit embedding
application before enabling that path.

`/documents` adds the pinned Yjs runtime. `/elements` and `/react` include the new
shared-document module, so raw unbundled browser imports need a correct import
map for dependencies. The supported host examples use an ordinary application
bundler. The legacy floating-widget IIFE remains a separate integration.

## Upgrade rules

- Keep the SDK, backend and trusted embedded client on the same `0.10.x` release
  line. Check deployment health and current server capabilities before rollout.
- Patch releases preserve documented APIs; fixes may reject insecure or obsolete
  behavior. A pre-1.0 minor release can change APIs and needs migration notes.
- Pin the SDK during evaluation. Test the native components and the trusted
  embedded client against the intended customer deployment before widening use.
- Test lease renewal and source logout against the intended deployment. The
  server renews the mounted child's at-most-ten-minute lease only while the
  original source, application, provider and membership remain authorized, and
  never beyond the original source expiry. The stable child JWT retains its
  Matrix device and authentication history during renewal. Offline/closed frames
  cannot renew; expired leases cannot be revived. Check acknowledged online
  teardown and bounded expiry when offline.
- Preserve client-side Matrix device storage and users' separate recovery keys.
  An npm update or server backup does not recover encrypted history.
- Rehearse native API use, OIDC login/logout/account switching, removed membership,
  encrypted device verification and host teardown for the actual host product.

The current feature and security boundaries are in [COMPONENT_SDK.md](COMPONENT_SDK.md).
Executed local checks belong in [VALIDATION.md](VALIDATION.md); a prepared workflow
does not establish a completed GitHub Actions run or a live customer deployment.
