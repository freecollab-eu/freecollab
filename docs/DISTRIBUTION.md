# Release, installation and public website

FreeCollab 0.13 provides reviewable container, SDK and native release pipelines.
Source availability, a successful build and registry/store publication are
separate states. The workflows default to artifact preparation; publication is
an explicit maintainer action. Customer identity providers, OAuth registrations,
notification services, signing identities and infrastructure remain customer-owned.

## Release identity

From the repository root, validate the intended version:

```sh
python scripts/release.py check --version 0.13.0
python scripts/test-release.py
```

The helper checks the API, frontend, SDK, desktop/mobile packages and lockfiles,
Android version name and iOS marketing version together. Release workflows also
require a clean source checkout. A `v0.13.0` tag must match the source version
exactly. Publication and signed native release require an actual successful
completed `ci.yml` run for the exact source commit, using GitHub's API; a prepared
workflow or a different commit's CI success cannot satisfy that check.

Use the ordinary unsigned native-client workflow first. It runs real Android and
iOS simulator compilation automatically for relevant source changes; desktop
packaging remains available through its manual dispatch. Native SDK compilation
on Apple hosts needs Xcode 16.3+ / Swift 6.1+ for the pinned LiveKit dependency.

## Container preparation and publication

`release-containers.yml` builds API, application frontend and independent public
website images for `linux/amd64` and `linux/arm64` using the checked-in Dockerfiles
and dependency locks. Each image receives source/version labels, a BuildKit SBOM
and maximum provenance. A review run exports OCI archives and metadata without
registry login or registry publication:

```sh
gh workflow run release-containers.yml --ref main -f version=0.13.0 -F publish=false
```

To publish, first configure the repository's GHCR package permissions and the
`container-release` GitHub environment with the intended maintainer protections.
The publish job uses only the run's GitHub package token and GitHub OIDC identity;
it needs no Docker Hub password or customer provider credentials. Then invoke
the same workflow with `publish=true` from reviewed `main` or the matching tag.
The official publisher is restricted to `freecollab-eu/freecollab`.

The pipeline refuses existing `vVERSION` and full `sha-COMMIT` tags. Authentication,
TLS, DNS, throttling and unknown registry errors fail closed rather than being
treated as an unused tag. GHCR tags can be changed by other authorized registry
writers; installations therefore use exact content digests. The published names
are:

```text
ghcr.io/freecollab-eu/freecollab-backend:v0.13.0
ghcr.io/freecollab-eu/freecollab-frontend:v0.13.0
ghcr.io/freecollab-eu/freecollab-website:v0.13.0
```

These names describe the publishing contract; they are not a claim that those
versions have already been published. The workflow creates GitHub-signed build
attestations for each exact image index digest and for the public release
metadata. A completed publish run produces `release-manifest.json`,
`images.env`, `SHA256SUMS` and BuildKit metadata. A review-only run marks its
manifest `local-oci`, adds `NOT-PUBLISHED.txt` and withholds installation pins.
The helper refuses to turn local OCI review manifests into published image pins.

If one parallel publication job fails after another has published, keep the
successful immutable tags and rerun only the failed jobs. The pipeline will
never overwrite a version to conceal a partial release; publish a new patch
version if the original reviewed bytes cannot be recovered and verified.

### Verify and install exact images

Obtain the release metadata from the successful official workflow/release and
review its source commit. With a recent GitHub CLI supporting attestations:

```sh
python scripts/release.py pins release-manifest.json \
  --verify-attestations --output images.env
```

This checks the exact repository/image names, source commit, digests and version,
then requires GitHub attestations from `release-containers.yml` on GitHub-hosted
runners for that source digest. Existing output files are never overwritten.
An unsupported old `gh` CLI or missing attestation fails the command. Air-gapped
operators can verify the attestations before transfer and generate pins locally;
omitting the verification flag validates metadata shape, not publisher identity.

For a new installation, create private keys locally using `pilot-init.py`, then
review production HTTPS, SMTP and storage settings using [OPERATIONS.md](OPERATIONS.md).
Keep operator secrets in `pilot.env`; `images.env` contains only public pins:

```sh
python scripts/pilot-init.py --output pilot.env
docker compose --project-name freecollab \
  --env-file pilot.env --env-file images.env \
  -f docker-compose.yml -f deploy/release/compose.yml \
  config --quiet
docker compose --project-name freecollab \
  --env-file pilot.env --env-file images.env \
  -f docker-compose.yml -f deploy/release/compose.yml \
  up -d --no-build --wait
```

The override requires Docker Compose 2.24.4+ for `!reset`; it removes source build
configuration and pins the API and all workers to the same backend digest. It
preserves existing service names, profiles, data volumes and private settings.
The image pin file is not a secret file and does not configure provider access.

For upgrades, retain all existing keys, volumes, Matrix identity and private
configuration. Stop writers and create an authenticated encrypted backup using
the existing backup/seal guides. Rehearse restore with the exact images before
replacing image pins, then check actual health/version, both intended workers,
accounts/SSO, files, client recovery and encrypted peer communication. Keep outbound
workers stopped during a restore until explicitly resumed. A downgrade requires
a compatible schema/data restore, not just swapping an image tag. See the
release notes and [validation record](VALIDATION.md).

## SDK npm distribution

`sdk-release.yml` tests the standalone public SDK, packs the allowlisted payload
and uploads the exact tarball and hash manifest. `publish=false` remains the
default. The publication job downloads those same reviewed bytes and verifies
package name, exact version/filename, registry, distribution tag, SHA-256 and
SHA-512 before `npm publish --ignore-scripts --provenance --tag next`.

The owner must control the `@freecollab` npm scope/package and register an npm
trusted publisher for organization `freecollab-eu`, repository `freecollab`,
workflow `sdk-release.yml`, environment `npm-release`. Configure that GitHub
environment's maintainer protections too. The job receives `id-token: write`
and uses npm OIDC; no npm token belongs in this repository or customer setup.
Registry ownership, initial-package creation and the trusted-publisher acceptance
must be performed by the account owner. Missing registry authorization blocks
publication; it never changes the outcome into a simulated success.

```sh
gh workflow run sdk-release.yml --ref main -f version=0.13.0 -F publish=false
# After the exact version has actually been published:
npm install --save-exact @freecollab/components@0.13.0
```

See [SDK_RELEASES.md](SDK_RELEASES.md) for local tarball installation and exact
pre-1.0 API-line compatibility.

## Customer-signed native distribution

`native-release-signed.yml` creates customer-installable signed packages. Select
one target or `all`, the exact version, public HTTPS installation origin and
mobile application ID. The identity step requires matching clean source and
successful CI. Configure signing inputs as secrets in `native-release`; the
workflow never searches the checkout or retained runtime for credentials. A
missing signing identity fails the signed job. The unsigned/debug/simulator
workflow remains separately named and is never advertised as a signed release.

| Target | Required `native-release` secrets | Verification before upload |
| --- | --- | --- |
| Windows x64 | `WINDOWS_SIGNING_P12_BASE64`, `WINDOWS_SIGNING_P12_PASSWORD` | electron-builder forces code signing; every public EXE must have a valid Authenticode signature/trust chain |
| macOS arm64/x64 | `MACOS_SIGNING_P12_BASE64`, `MACOS_SIGNING_P12_PASSWORD`, `APPLE_NOTARY_API_KEY_BASE64`, `APPLE_NOTARY_API_KEY_ID`, `APPLE_NOTARY_API_ISSUER` | Forced Apple signing, notarization, strict codesign, Gatekeeper and stapled-ticket verification |
| Android | `ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD`, `GOOGLE_SERVICES_JSON_BASE64` | Firebase package matches build ID; APK alignment/apksigner and AAB JAR signature; both signer certificates match the provided key |
| iOS | `IOS_SIGNING_P12_BASE64`, `IOS_SIGNING_P12_PASSWORD`, `IOS_PROVISIONING_PROFILE_BASE64` | Matching unexpired push-enabled distribution profile and authorized certificate; actual archive and exported IPA signatures, certificate and production entitlements |

The mobile configuration uses `FREECOLLAB_APP_URL` and
`FREECOLLAB_APPLICATION_ID`. Firebase's public client configuration must match
that Android application ID; Apple profile, bundle ID, APNs topic and the
customer backend's notification configuration must agree. Service-account,
APNs private and LiveKit server signing keys belong only in the customer's
backend configuration and are never embedded in a client package.

Temporary Apple keychains, certificate files, Android keystore and installed
provisioning profile are cleaned by job traps. Signing inputs are scoped to the
steps that need them. Output contains public binaries, checksums and a release
manifest binding source commit, version, platform, signing verification and the
mobile origin/application ID. GitHub build attestations bind the produced
binary bytes to the workflow. Artifact upload does not submit to App Store,
Google Play or Microsoft Store. Store ownership, distribution agreements,
review and customer-specific entitlement approval remain the owner's tasks.

Run physical Android/iOS acceptance for system Answer/End/Mute, background
audio, reconnection, notification delivery, denied/revoked sessions, customer
SSO and device recovery. A macOS simulator compile, a debug APK or a prepared
signing workflow does not prove those platform/provider behaviors.

## Public freecollab.eu website and demo

The independent static website serves the landing page and documentation, and
opens the separate [product demo](PRODUCT_DEMO.md). That demo runs the actual
`Chat`/`WorkHub` client and normal API with an isolated temporary sample workspace
for each visit. It therefore needs its own dedicated demo backend and storage;
it is no longer a browser-only replacement for product features.

The normal customer application image remains separate. Build the public site
with `npm run build:website` and the sample product entry with the dedicated demo
Vite configuration. See [deploy/website](../deploy/website/README.md) for the
maintained delivery paths. The website's own account routes stay unavailable;
the demo frame uses only its dedicated origin and ephemeral normal session.
External providers, secrets, mail and irreversible operator actions are blocked
in the public sample installation. Public hosting, DNS, TLS and exact deployed
assets still require actual deployment evidence; source configuration alone is
not evidence of a running service.

## Executed checks and remaining acceptance

Local helper regressions exercise integrity/identity failures, exact-commit CI
gating, registry error handling, output preservation, injection protection and
public binary allowlisting. Workflow validation and static website/browser
results are recorded separately in [VALIDATION.md](VALIDATION.md).

Container publication, npm acceptance, Apple/Windows code signing, notarization,
store submission and physical push/call delivery require configured external
identities and services. Those actions are not implied by source implementation
or static workflow validation. Installation/load/backup-restore measurements
must identify the actual source, image IDs, workload and host; they are bounded
evidence for that environment, not an unmeasured capacity promise.
