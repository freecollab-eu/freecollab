# Dependency baseline for the 0.6.0 candidate

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

Unused boto3 was removed: the current storage service is local-only, and no repository code imports it. This does not implement S3 support. Autoprefixer was removed from the PostCSS configuration because Tailwind 4 handles the required processing through its own plugin. The existing Tailwind theme remains loaded through `@config`.

## Reproducible installs

Backend production and development dependencies, including transitive versions and artifact hashes, are saved in `backend/requirements.lock` and `backend/requirements-dev.lock` for Python 3.11. The `.txt` files remain the direct-dependency inputs. From `backend`, install the relevant lockfile with artifact checking:

```bash
python -m pip install --require-hashes -r requirements.lock
# Development and test environment:
python -m pip install --require-hashes -r requirements-dev.lock
```

The development lockfile was installed successfully into a fresh Python 3.11 virtual environment with hash verification. Use `npm ci` in `frontend` to install its lockfile without changing versions. Do not use an uncompiled direct-requirements file with `uv pip sync`: `sync` expects the complete resolved set and otherwise removes transitive dependencies.

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
