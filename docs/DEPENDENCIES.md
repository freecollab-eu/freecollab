# Dependency baseline and audits

## Security review — 2026-10-27

Fresh registry audits of commit `f35d499` reported **zero known advisories** in
all 76 runtime and 87 development Python lock entries using `pip-audit` 2.10.1's
PyPI service. Full npm 11.9.0 audits, including development and optional
dependencies, also reported zero advisories for the frontend (323 locked
packages), component SDK (34), desktop (277) and mobile (99). The repository
contains four tracked npm manifest/lockfile pairs. No dependency versions or
overrides needed changing.

Every Python lock entry retains SHA-256 artifact hashes. npm manifests match
their lockfile roots, and registry tarball entries retain HTTPS URLs and
SHA-512 integrity values; six bundled Tailwind WASM dependencies are covered by
their parent tarball's integrity. These checks inspect the recorded locks, not
every downloaded artifact or package's behavior. Audits cover runtime and build
dependencies, but exclude container OS packages, Electron's bundled Chromium,
native Gradle/Swift dependencies and application reachability analysis.

Reproduce from `backend` with
`pip-audit --strict --disable-pip --require-hashes -r requirements.lock --format json`
and the equivalent command for `requirements-dev.lock`; use
`npm audit --package-lock-only --json` in each npm project. Raw dated JSON
reports and lockfile SHA-256 values are retained in the private local audit
directory `/workspace/.freecollab/audit-tools-20261027/results/`. The audit tools
use a separate environment and do not modify the application's Python environment.

## Security review — 2026-10-22

The exact Python locks contain 76 runtime packages and 87 development packages.
An OSV lookup of all 87 pinned package/version pairs reported no published
advisories. Full npm audits reported no advisories for the frontend and component
SDK. These are dated registry results, rather than an application or container-OS
security certification.

The native packaging toolchains initially reported eight desktop and three
mobile moderate findings; both production-only native audits were already clear.
The desktop findings shared the deprecated `roarr`/`sprintf-js` path beneath
`@electron/get` 3.1.0. A scoped `global-agent` 4.1.3 override removes that path.
The mobile findings came from `xcode` 3.0.1's old UUID dependency; its scoped
override selects patched UUID 11.1.1. The direct Electron, Electron Builder and
Capacitor versions and application runtime dependencies remain unchanged. Full
npm audits of both updated native locks now report no advisories.

Compatibility checks included clean native installs, the existing native unit
suites, an actual unsigned Linux directory package with checksum-verified
Electron 45.0.0, and private Android/iOS Capacitor synchronization. The patched
Xcode dependency parsed, changed and rewrote a real project and generated 200
unique project IDs. Electron's actual proxy initializer passed an owned loopback
proxy round trip. These checks do not establish macOS/Windows signing, mobile
device behavior or store acceptance; three platform-specific mobile tests were
skipped because their toolchains were unavailable on this Linux worker.

The Railway frontend builder now uses the same documented CUDA-download skip as
the other frontend builds. Browser inference still uses WASM; TLS, proxy trust,
normal installation scripts and lockfile integrity checks remain enabled.

## Historical 0.11 audit — 2026-10-21

The current hash-locked Python runtime resolves **73 packages**, with zero known
vulnerabilities reported by `pip-audit` 2.10.1's PyPI service. Production npm
audits also report zero advisories for the frontend, component SDK, desktop and
mobile projects. These are dated registry results; they do not assess container
operating systems or certify the application.

New Python adapters pin boto3/botocore 1.42.74, redis 7.1.1, y-py 0.6.2 and
pypdf 6.21.2. Browser media uses LiveKit and Transformers.js with the exact
versions/integrities in the frontend lock. Transformers' unused Node branches
need scoped overrides for patched sharp 0.35.5 and global-agent 4.1.3; the latter
removes the vulnerable roarr/sprintf-js dependency path. The tested browser
Transformers/ONNX files remain byte-identical after these overrides.

Use `ONNXRUNTIME_NODE_INSTALL_CUDA=skip npm ci` for frontend installs. This runs
normal installation scripts while skipping the Node-only CUDA download; audio
inference uses the bundled browser WASM runtime. Compose's builder and CI set
the same option. Keep proxy, CA and checksum verification enabled.

The following sections retain the earlier 0.6 dependency decisions and audit
history. Current feature validation is recorded in [VALIDATION.md](VALIDATION.md).

## Historical baseline — 2026-10-20

Audit date: 2026-10-20. Use Python 3.11 and Node.js 24 LTS for the validated development/deployment baseline. The frontend requires Node.js 22.12 or newer.

## Current client encryption dependencies

The 0.5 increment pins `matrix-js-sdk` 44.0.0, `matrix-encrypt-attachment` 1.0.3, `pdfjs-dist` 6.4.299 and local TOTP QR generation with `qrcode` 1.5.4. The observed crypto runtime is Rust SDK 0.19.1 with Vodozemac 0.11.0. The Synapse service uses an exact container digest in Compose; it is an additional service, not part of the Python environment. `httpx==0.28.1` is now an explicit runtime dependency for the private authenticated gateway, retained in both hash lockfiles.

On 2026-10-20, `npm audit --omit=dev --json` against the updated lock reported **zero production dependency advisories**. This checks npm advisories, not the Matrix service's OS packages or the correctness of FreeCollab's integration. The historical broader audit below predates these new client dependencies.

## Integration dependencies

OIDC uses `PyJWT[crypto]==2.15.1` with the hash-locked `cryptography` backend for asymmetric JWKS verification. Version proofs use established `opentimestamps==0.4.5` for parsing and digest-commitment validation; the integration does not claim independent Bitcoin-chain verification. The HTTPS webhook transport explicitly pins `httpcore==1.0.9` and retains the session proxy, original TLS hostname and certificate verification. Both Python hash lockfiles were regenerated and installed. A fresh PyPI-service audit of the updated installed environment on 2026-10-20 reported zero known vulnerabilities.

## Earlier findings and changes

The starting installed Python environment produced 45 known advisory findings across seven packages in `pip-audit`'s PyPI service (some findings share an advisory identifier). The starting npm lockfile produced 19 findings, including 14 high-severity findings.

After the updates, the checked Python environment and frontend lockfile each reported **zero known vulnerabilities** in the selected audit services. This is a dated dependency database result, not a complete security assessment or a guarantee against future advisories.

| Area | Validated baseline / decision |
| --- | --- |
| API / uploads | FastAPI 0.152.0, resolved Starlette 1.9.0, python-multipart 0.0.32, Uvicorn 0.54.0 |
| JWT | PyJWT 2.15.1 replaces python-jose. Native sessions use HS256; OIDC additionally uses the maintained cryptography backend for asymmetric provider tokens. The former python-jose dependency tree was removed. |
| Passwords | bcrypt 5.0.0; the application rejects unsupported length/null inputs instead of silently truncating. Unused passlib was removed. Existing bcrypt hashes remain supported. |
| Message crypto | PyNaCl 1.6.2 |
| Database | SQLModel 0.0.44, SQLAlchemy 2.0.55, psycopg2-binary 2.9.13. SQLModel 0.0.45+ changes datetime storage behavior and rejects this repository's existing naive UTC timestamps; upgrading it needs an explicit schema/data migration. |
| Configuration | pydantic-settings 2.16.1 |
| Realtime | python-socketio 5.17.0; patched npm Socket.IO client/parser dependencies are retained by the lockfile. |
| Frontend tooling | Vite 7.3.7, React plugin 5.2.0, Tailwind CSS / PostCSS plugin 4.3.3; React stays on the 18.x line. |
| Browser testing | Python Playwright 1.62.0; install its matching Chromium in CI. |

At 0.6, unused boto3 was removed because storage was local-only. Version 0.11 restores it for the implemented [S3 adapter](OBJECT_STORAGE.md). Autoprefixer was removed from the PostCSS configuration because Tailwind 4 handles the required processing through its own plugin. The existing Tailwind theme remains loaded through `@config`.

## Reproducible installs

Backend production and development dependencies, including transitive versions and artifact hashes, are saved in `backend/requirements.lock` and `backend/requirements-dev.lock` for Python 3.11. The `.txt` files remain the direct-dependency inputs. From `backend`, install the relevant lockfile with artifact checking:

```bash
python -m pip install --require-hashes -r requirements.lock
# Development and test environment:
python -m pip install --require-hashes -r requirements-dev.lock
```

The development lockfile was installed successfully into a fresh Python 3.11 virtual environment with hash verification. Use `ONNXRUNTIME_NODE_INSTALL_CUDA=skip npm ci` in `frontend` to install its lockfile without changing versions. Do not use an uncompiled direct-requirements file with `uv pip sync`: `sync` expects the complete resolved set and otherwise removes transitive dependencies.

To intentionally refresh Python locks after reviewing updates:

```bash
uv pip compile requirements.txt --python-version 3.11 --generate-hashes --output-file requirements.lock
uv pip compile requirements-dev.txt --python-version 3.11 --generate-hashes --output-file requirements-dev.lock
```

Repeat the audit and tests after any refresh. Keep TLS and artifact verification enabled. Private-registry configuration and credentials do not belong in a lockfile or image layer.

## Audit commands and scope

`npm audit --json` was run in `frontend`. Python audit used `pip-audit==2.10.1 --vulnerability-service pypi --path <venv>/lib/python3.11/site-packages`, with a writable cache. Raw before/after reports are stored in the local setup area `/workspace/.freecollab/` rather than committed; they contain dependency/advisory metadata.

The baseline checks included a real application import/HTTP health request, a naive UTC database insert, a successful frontend production build after the Tailwind migration, and a fresh hash-checked lockfile installation. Application, browser and deployment regression outcomes are recorded separately in the release validation documentation.

Audits cover registry dependencies, not the correctness of application authorization, key custody, data migration, external services, container base operating-system packages or deployment configuration. A release must repeat these checks against the actual resolved installation.
