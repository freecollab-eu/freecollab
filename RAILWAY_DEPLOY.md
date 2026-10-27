# Self-host FreeCollab on Railway

FreeCollab is open source. Keep your data and provider credentials in your own Railway project. `freecollab.eu` is the project website, not a required runtime service or OAuth credential broker. Microsoft 365 and Google connections use the customer's own application, organization account and explicit consent in their installation.

These files prepare deployment; they do not provision Railway, DNS, TLS or SMTP. No authenticated Railway deployment has been performed for this guide.

## Service layout

Create a project from your repository/fork. Select each service's repository root and config path explicitly; verify the resulting Dockerfile/build context in Railway. For legacy config-as-code services, the config path is absolute from the repository root, such as `/frontend/railway.json`; it does not follow the service's `Root Directory`. A website service uses `/frontend/website/railway.json` with root `/frontend`, not the product app's config. Keep existing names, volumes, database and secrets during upgrades.

| Service | Root / config | Persistent storage | Exposure |
| --- | --- | --- | --- |
| `postgres` | Railway PostgreSQL 15 | Database volume | Private URL only; no public TCP proxy |
| `backend` | `backend` / `/backend/railway.json` | One volume at `/data` | Private port 8000; no public domain/TCP proxy |
| `frontend` | `frontend` / `/frontend/railway.json` | None | Only public HTTPS app service; target port 8080 |
| `matrix`, optional | `scripts` / `/scripts/railway-matrix.json` | Independent volume at `/data` | Private port 8008; no public domain/federation |

The Railway-specific Dockerfiles install dependencies without BuildKit secret mounts, which Railway's builder does not support. npm still verifies TLS and pip still verifies TLS and every locked package hash. For local validation behind a managed proxy, use the existing local Dockerfiles or a disposable Dockerfile copy outside the deployment/upload tree, supplying the public proxy CA through the local builder's temporary mount and the relevant tool's trust setting. Keep that copy local, keep the CA out of image layers and uploaded source, and never disable certificate verification.

The backend Railway image runs **one API plus three independent workers: integrations, mail and durable AI runs, in the same service**. Railway volumes cannot be shared between services; a separate integration worker would not see uploads, exports or files awaiting erasure. The supervisor waits for database-aware API readiness before starting the workers. Any child exit stops all children and fails the service for Railway restart. SIGTERM allows 30 seconds to stop before killing/reaping stragglers; allow at least that platform grace period.

Keep one backend replica. Disable overlapping active deployments, autoscaling and sleep/scale-to-zero for this stateful service. Presence and some request limits are process-local. Plan a short maintenance window and verify Railway's current volume/deployment behavior before an upgrade; do not run a second backend against the same database/volume as a zero-downtime workaround.

## Private configuration

For a **new** installation, run `python scripts/pilot-init.py --output pilot-railway.env` on a trusted computer. It creates an owned mode-0600 file without printing secrets. Transfer values through Railway's private variable interface. Never commit/share this file or regenerate an existing installation's keys.

| Backend variable | Value |
| --- | --- |
| `PORT`, `ENVIRONMENT` | `8000`, `production` |
| `DATABASE_URL` | Reference to the private PostgreSQL URL, e.g. `${{postgres.DATABASE_URL}}`; confirm private host routing without printing the secret |
| `JWT_SECRET` | Independent generated signing secret, at least 32 bytes |
| `ACCOUNT_SECURITY_KEY` | Independent generated standard-base64 32-byte key |
| `MESSAGE_ENCRYPTION_KEYS` | Generated JSON content-key ring |
| `MESSAGE_ENCRYPTION_ACTIVE_KEY_ID` | Ring ID, initially `pilot-1` |
| `STORAGE_PATH` | `/data/files` |
| `ACCOUNT_SECURITY_KEY_FILE`, `MESSAGE_ENCRYPTION_KEY_FILE` | `/data/security/account-security.key`, `/data/security/message-keys.json` |
| `MAIL_FILE_PATH`, `MAIL_DELIVERY_MODE` | `/data/mail`, `disabled` until your SMTP is configured |
| `WORKFLOWS_ENABLED`, `DATA_CONTROLS_ENABLED`, `ACTIVITY_FEEDS_ENABLED` | `true` for local product work |
| `OIDC_ENABLED` | `false`; workspace-owned SSO is configured separately in the app |
| `ENABLE_EXPERIMENTAL_FEATURES`, `ENABLE_DEMO_AI` | `false` |
| `MATRIX_ENABLED` | `false` until private Matrix is healthy |
| `FORWARDED_ALLOW_IPS` | Explicit frontend private IP or verified environment subnet; never `*`, `0.0.0.0/0`, `::/0` |

The launcher initializes only `/data`, `/data/files`, `/data/mail`, `/data/security`, then permanently drops to uid/gid 10001 before starting application processes. It never recursively rewrites user files. An existing volume owned by another application UID needs a reviewed migration while writers are stopped; startup refuses unknown directory owners. Preserve original permissions and keys first.

## Public frontend and callback origin

Allocate the frontend's Railway HTTPS domain or attach a domain you control. Set:

```dotenv
# Frontend: host only, no scheme/path/port. Examples are not provisioned DNS.
PORT=8080
FREECOLLAB_PUBLIC_HOST=app.your-company.example
FREECOLLAB_BACKEND_HOST=${{backend.RAILWAY_PRIVATE_DOMAIN}}
# Safe default: no remote gateway trusted. Replace only after verification.
FREECOLLAB_TRUSTED_EDGE_CIDR=127.0.0.1/32

# Backend: exact selected HTTPS origin, no trailing slash.
PUBLIC_APP_URL=https://app.your-company.example
CORS_ORIGINS=["https://app.your-company.example"]
```

Leave `VITE_API_URL` **unset**. The supplied Railway frontend proxies `/api/` and Socket.IO to private `*.railway.internal:8000`, using one browser origin. Remove old start-command overrides that bypass these configs. Deploy PostgreSQL/backend first, then frontend. Frontend readiness is `/api/ready` and fails while the private backend/database is unavailable. Unknown Host headers receive only a generic response at that one readiness path; app/API paths reject foreign hosts.

Verify Railway's actual ingress peer/CIDR and header-overwrite behavior before trusting forwarded IPs. Frontend accepts client IP and HTTPS scheme only from `FREECOLLAB_TRUSTED_EDGE_CIDR`, then replaces upstream forwarding headers. Backend trusts only explicit private proxy addresses. Conservative defaults reject spoofed headers but aggregate client limits at the gateway until trust is configured. Confirm forged incoming `X-Forwarded-For` cannot choose the API's client identity before relying on per-client throttling. Review the chain again if adding a CDN.

Raw Nginx/Uvicorn request logging is disabled because Office capability paths and OAuth queries can be sensitive. Review platform-edge logs/retention separately; app config cannot control Railway's logs. Do not enable unredacted request/debug logging. Metrics credentials belong in Authorization headers, never URLs.

## Customer-owned Microsoft 365, Google and SSO

Enable `SYNC_ENABLED=true` for native two-way files/calendar and `EXTERNAL_MEETINGS_ENABLED=true` for Teams/Meet scheduling when intended. `CLOUD_FILES_ENABLED=true` enables the read-only Box/Dropbox picker; Google/OneDrive reuse the selected channel's broader sync grant. These flags allow configuration, not provider authorization. Keep AI, push, webhooks and timestamps disabled unless deliberately used.

The customer administrator opens **Integrations → Connection setup**, chooses Google Drive/Calendar/Meet or Microsoft OneDrive/SharePoint/Calendar/Teams, and selects a channel. The guide derives the exact callback from `PUBLIC_APP_URL`, scopes and prerequisite tenant/drive/calendar IDs. Register the customer's own application and enter its client configuration **only in that customer's instance**. Save a disabled draft, explicitly authorize, then explicitly test. A saved draft is not a verified connection. A separate installation does not need a callback on `freecollab.eu` or to give credentials to FreeCollab maintainers.

Organization login uses the same guide's customer-owned Entra/Google Workspace/OIDC preset; it is separate from data/calendar OAuth and SCIM. See [Connection setup](docs/CONNECTION_SETUP.md).

## Optional client-encrypted channels

Create `matrix` using its scripts-root config/digest-pinned image and durable `/data`. Set stable `MATRIX_SERVER_NAME` and independent `MATRIX_REGISTRATION_SHARED_SECRET` there. Retain those same values on backend with `MATRIX_INTERNAL_URL=http://${{matrix.RAILWAY_PRIVATE_DOMAIN}}:8008`. Enable backend `MATRIX_ENABLED=true` only after Matrix health succeeds. Never allocate Matrix a public domain or enable public registration/federation.

The existing bootstrap refuses changed homeserver identity. Back up its SQLite/config/signing keys/media together; an external Synapse database needs separate coordination. Test actual trusted-device setup, peer decryption and independent client recovery. Native channels remain server-readable with native files on the private volume; secure-channel content uses client Matrix encryption. HTTP readiness cannot prove encryption.

## SMTP and acceptance checks

Configure your verified sender/SMTP credentials privately, then select `MAIL_DELIVERY_MODE=smtp` with verified STARTTLS or implicit TLS. The existing mail worker is already supervised. See [Integration operations](docs/INTEGRATION_OPERATIONS.md) for SPF/DKIM/DMARC and an explicit operator-inbox test; SMTP acceptance does not prove inbox placement.

Register the intended native operator, then through trusted backend access run `python -m app.cli bootstrap-admin --email operator@your-company.example`. Enable MFA and inspect Operations for all three worker heartbeats/storage/queue state. The AI lane is active only when the operator enables AI. Passive public checks:

```bash
python scripts/pilot-preflight.py https://app.your-company.example \
  --expect-version 0.13.0 --require-capability integrations.connection_setup
python scripts/pilot-preflight.py https://app.your-company.example \
  --metrics-token-file /private/freecollab-metrics-token
```

The optional token is an independent scoped `OPERATIONS_METRICS_TOKEN` in an owned mode-0600 regular file, not an account JWT. Preflight checks verified HTTPS/public DNS/readiness/version/capabilities without provider calls, writes or mail. It refuses redirects and bounds response size/elapsed time. Run the real two-account invitation/chat/file, logout and member-revocation journeys before important data.

## Backups, upgrade and recovery

Retain the exact source commit/image release, all content/legacy keys, JWT/account keys, customer credential state and Matrix identity before upgrades. SQLModel creates additive tables, not arbitrary schema migrations. Enable Railway database/volume backups for your plan; database-only recovery omits files/Matrix identity, and volume-only recovery omits permissions/history. Independent live snapshots are not automatically application-consistent. Schedule a maintenance window: stop the whole backend supervisor and Matrix, then coordinate PostgreSQL and relevant volume backups at the same recovery point. Keep private variable/key recovery material independently controlled.

Test a **fresh isolated Railway environment**, with public access/SMTP/outbound workers held off until reviewed. The supervisor normally starts all three workers; for a drill use a reviewed API-only command at uid 10001 with the same private volume configuration, or inspect the offline restore before starting. Keep the AI lane off so restored grants cannot disclose retained sources automatically. Compare original file bytes, historical content, authorizations, queues and retained Matrix/client decryption. Never test by overwriting the source service.

Railway backup/export availability, off-host transfer and fresh-environment restore still need verification in the selected project. Existing `pilot-backup.py`, `pilot-restore.py`, `backup-seal.py` support local Compose, not arbitrary Railway snapshots or managed volumes. Do not manufacture a Compose manifest or claim a sealed complete backup from a database snapshot. Linux/Compose operators can use the optional [scheduled sealed off-host kit](docs/PILOT_OPERATIONS.md), which reuses the existing tools.

Public DNS/certificate renewal, platform proxy trust, volume permissions, SMTP inbox receipt, customer OAuth consent and complete recovery remain actual deployment checks. Adding these files does not establish any of them.
