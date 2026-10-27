# Self-hosted installation operations

FreeCollab runs as your own installation on a computer or private LAN/VPN
server, as well as on a chosen hosting service. Accounts authenticate to that
installation; there is no required central FreeCollab cloud account or control
plane. The profile's historical `pilot` name does not require a pilot group.
See [local and network self-hosting](SELF_HOSTING.md) for the deployment model
and optional-service boundaries.

This runbook targets a single Linux Docker Engine with Docker Compose v2 and Python 3.11+. It runs one API worker, one PostgreSQL 15 database and an Nginx frontend. Nginx serves the application/widget and proxies `/api/` and `/socket.io/` on the same origin. The default port is bound to loopback only.

## Product queues and browser capabilities

The default Compose installation starts the **integration-worker**. It handles scheduled messages and reminders alongside signed apps, consented recaps, webhooks, timestamp proofs, remote sync, workflows, data export/erasure and configured push. It also performs bounded expired Slack-preview and personal OAuth cleanup; native device dispatch remains disabled until its separate provider setup is ready. External services remain inactive until explicitly configured and granted. Local workflows and data controls are enabled by default; retention itself remains disabled and on hold. Each queue has its own transaction and durable claims, so one failure does not block the others. Restores keep outbound workers stopped by default; review queued jobs, current grants and pending scheduled sends before explicitly resuming them.

The optional `large-meetings` profile starts a separate **sfu-worker** every five seconds so slow provider jobs cannot delay meeting-access reconciliation. Configure your own LiveKit HTTPS/WSS service first; see [MEETINGS.md](MEETINGS.md). Restoring it requires `--resume-sfu` after reviewing external rooms and restored leases. Live media is transient and is not part of a cold application backup. [OBJECT_STORAGE.md](OBJECT_STORAGE.md) explains the separate snapshot requirements for S3 file storage.

The complete frontend is an installable PWA on HTTPS (localhost is allowed for development). The service worker revalidates itself and caches only public application shell/assets. Private drafts, opted-in history/files and queued text/attachments live in an account-bound encrypted IndexedDB vault; authenticated API responses and Matrix content are excluded from CacheStorage. The [offline library](MOBILE_OFFLINE.md) has explicit quotas, same-sign-in expiry and reconnect authorization; it cannot observe a server revocation while disconnected. Browser storage and open-document pending edits are separate from server backups.

WebRTC uses no third-party STUN/TURN service by default. Configure `MEETINGS_ICE_SERVERS` (JSON STUN URLs), `MEETINGS_TURN_URLS` (JSON TURN URLs) and an independent 32-byte-or-longer `MEETINGS_TURN_SECRET` for a TURN server supporting short-lived REST credentials. Public HTTPS and a working relay are needed for reliable meetings across NATs. Media remains between browsers; signaling and participant metadata pass through the API. Meetings use WebRTC DTLS-SRTP, separately from Matrix trusted-device encryption.

Optional Web Push requires `PUSH_ENABLED=true`, an independent EC private VAPID key from `scripts/push-keygen.py`, and a valid `PUSH_VAPID_SUBJECT`. Keep the private PEM in private operator configuration. Devices opt in and prove endpoint ownership with an encrypted challenge. Only a generic activity notice is sent; delivery still contacts the device's browser push service. Removing a session, membership or consent prevents further deliveries. [Mobile setup](MOBILE_OFFLINE.md) describes support and limits. Native Android/iOS notifications use separate customer-owned FCM/APNs configuration and consented native device registrations; see [NATIVE_PUSH.md](NATIVE_PUSH.md). Neither transport grants call admission or guarantees ringing while an application is terminated.

[Data controls](DATA_CONTROLS.md) require a native personal workspace owner, fresh password/MFA and explicit selected-channel consent. Export archives are encrypted at rest; download is an authenticated POST with fresh reauthentication. Purge uses a one-use exact-content preview and rechecks the policy revision, hold, grant and channel mode at dispatch. Physical file erasure follows committed metadata deletion through a retryable erasure queue. Copies on other devices, external services and backups have their own lifecycle.

## Create and start a private local pilot

From the repository root:

```bash
python scripts/pilot-init.py
docker compose --env-file pilot.env --project-name freecollab up --build -d --wait
```

`pilot-init.py` creates a mode-0600 `pilot.env` with separate random database, JWT, account-security and message-encryption keys. It refuses to overwrite any existing file. Keep this configuration: losing the message keys prevents recovery of encrypted history; losing the account-security key prevents decrypting existing authenticator secrets and queued recovery messages. Neither the helper nor these instructions print key values. `pilot.env`, `pilot-*.env` and `pilot-backups/` are ignored by Git.

The default local origin is `http://127.0.0.1:8080`; use local requests for readiness checks:

```bash
curl --fail http://127.0.0.1:8080/api/ready
docker compose --env-file pilot.env --project-name freecollab ps
```

Database and API ports are not published. The frontend sends HTTP and Socket.IO requests to its own origin. Leave `VITE_API_URL` unset for this Compose deployment. The widget bundle is served as `/widget.iife.js`.

A separately hosted frontend can instead be built with `--build-arg VITE_API_URL=https://api.example.com`. That selects the static-only Nginx configuration and does not require a `backend` DNS name. Its browser requests go directly to the configured API origin, which must allow the frontend's HTTPS origin in CORS. This is a different layout from the same-origin Compose default.

Use `python scripts/pilot-init.py --port 8088 --output pilot-other.env` and a distinct Compose project name for an independent installation. Select project names carefully: Compose associates persistent volumes with that name. Do not change PostgreSQL major image versions against an existing data volume; use a reviewed dump/restore migration instead.

Registration creates ordinary accounts. After registering the intended operator, bootstrap the first admin with server access:

```bash
docker compose --env-file pilot.env --project-name freecollab exec backend \
  python -m app.cli bootstrap-admin --email operator@example.com
```

Reload the application after bootstrapping so the account menu refreshes its role.
The installation admin and a workspace owner are separate roles; creating a
workspace makes its creator the owner without granting installation-wide access.

To bring a first team into the local installation:

1. Open the application at the configured local origin and create an account with a password of at least 12 characters.
2. Create a workspace from the workspace switcher, then use its getting-started guide to create and open the first channel.
3. Open **Channel members**, create an invitation link and share it privately. In a separate browser profile, the second person opens that link, creates an account or signs in, and explicitly joins the channel.
4. Exchange a message and upload/download a permitted file from both accounts.

A channel invitation gives a new recipient guest access to that workspace and
membership in that channel. A workspace invitation alone does not grant access
to existing channels; invite the recipient to each intended channel separately.
Use **Open channel invitations** in the workspace guide to reach that step.

For a normal restart, keep the same environment file and project name:

```bash
docker compose --env-file pilot.env --project-name freecollab restart
docker compose --env-file pilot.env --project-name freecollab ps
curl --fail --retry 30 --retry-connrefused --retry-delay 1 --retry-max-time 60 \
  http://127.0.0.1:8080/api/ready
```

Substitute the configured port if changed. Reinitializing configuration changes
keys rather than restarting a service; deleting volumes removes stored data.

## Account email and recovery

The initializer selects private local file delivery and the Compose `mail` profile enables its worker explicitly:

```bash
docker compose --env-file pilot.env --project-name freecollab --profile mail up -d mail-worker
```

The backend queues encrypted verification/recovery instructions; only the worker delivers committed messages. The `mail` volume contains mode-0600 `.eml` files inside a private directory for local development. Read a selected file through trusted container/filesystem access; do not put recovery links in logs or shared terminals. Outside Compose, run `python -m app.services.mail` from `backend`, or use `--once` for a single batch. The same worker queues opted-in daily/weekly channel activity digests without message bodies. No external email is sent by the default file configuration.

For public deployments, set `MAIL_DELIVERY_MODE=disabled` until the mail provider is configured. To enable actual email, set `MAIL_DELIVERY_MODE=smtp`, `PUBLIC_APP_URL` to the exact HTTPS frontend URL, `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM` and any required `SMTP_USERNAME`/`SMTP_PASSWORD`, then start/recreate both backend and mail worker. Use STARTTLS (`SMTP_STARTTLS=true`, usually port 587) or implicit TLS (`SMTP_IMPLICIT_TLS=true` and `SMTP_STARTTLS=false`, usually port 465). TLS certificates are verified. File delivery is refused in production; a worker retry is not evidence that an external mailbox received a message. Verify delivery with an operator-controlled test inbox before inviting users.

Existing private environments predating account security must add an independent random standard-base64 32-byte `ACCOUNT_SECURITY_KEY` using their secret manager. Keep the existing JWT/content keys. New installations receive this key from the initializer. Account verification, TOTP secrets and session state live in additive database tables; no existing user-table rewrite is required. Keep the account-security secret in controlled backups separately from database exports. See [AUTH_SESSIONS.md](AUTH_SESSIONS.md) for the exact recovery/MFA guarantees and limits.

## Private encrypted messaging service

The `secure` Compose profile adds a digest-pinned Synapse service with no host port. Its bootstrap script generates and retains the homeserver database, signing identity, configuration and media in the `matrix` volume. Public registration and federation are disabled. Browser traffic reaches it through FreeCollab's authenticated gateway.

To enable it, retain a stable `MATRIX_SERVER_NAME`, configure an independent `MATRIX_REGISTRATION_SHARED_SECRET`, then start and verify the service before enabling `MATRIX_ENABLED=true` and recreating the backend:

```bash
docker compose --env-file pilot.env --project-name freecollab --profile secure up -d --wait matrix
docker compose --env-file pilot.env --project-name freecollab up -d --force-recreate backend
```

Keep the same server name and registration secret across upgrades and restores. A new homeserver identity cannot replace an existing encrypted-message history. The local single-server pilot uses SQLite inside the durable Matrix volume; an externally configured Matrix database requires its own coordinated backup procedure and is rejected by the local cold-backup helper. Encrypted-channel users still need browser device-key setup and their independent message-key recovery material.

## External integrations

Use [INTEGRATIONS.md](INTEGRATIONS.md) for customer-owned workspace OIDC, channel-granted webhooks/n8n, document links, calendar exchange and optional version timestamps. Customer admins configure their own IdP in the app; no installation-wide OIDC credentials are required. Preserve the account-security key that encrypts their client secrets, and keep PUBLIC_APP_URL at the correct trusted public HTTPS origin for callbacks. The default integration worker uses durable database queues and independent bounded batches; enable outbound features only after configuring their intended grants. The worker is included among cold-backup writers and exact image records. Mail remains a separate `mail` profile. [INTEGRATION_OPERATIONS.md](INTEGRATION_OPERATIONS.md) describes authenticated diagnostics, limited metrics credentials, SMTP tests and alert inputs.

Version 0.7 adds opt-in [workforce admission](WORKFORCE_IDENTITY.md),
[SCIM provisioning](SCIM.md) and selected native-channel [two-way remote
sync](REMOTE_SYNC.md). Customers supply their own IdP, OAuth app/consent or DAV
app-password through workspace settings. `SYNC_ENABLED=false` is the installation
default; enable it and the integration worker when intended. The worker shares
no plaintext from secure Matrix channels. Keep account-security keys and all
existing Matrix identity/volume state during upgrades. Additive workforce,
directory and sync tables initialize at startup; do not recreate an existing
database or replace its keys to enable these features.

## HTTPS and public exposure

This default is an isolated local pilot, not an installed public TLS endpoint. Before routing real external users:

- Set `ENVIRONMENT=production` and `CORS_ORIGINS` to a JSON array of the actual HTTPS frontend origins. Keep the independently generated signing/content/account-security keys. Configure HTTPS `PUBLIC_APP_URL` and production SMTP or disable email delivery explicitly.
- Terminate TLS at a trusted reverse proxy and route the same origin to the loopback frontend. Configure certificates, renewal, firewalling and the proxy's request/body limits. Do not remove TLS verification in clients or builds.
- Keep API/database services internal. Nginx overwrites `X-Forwarded-For` with its peer address; the internal API trusts this private proxy. An additional public proxy needs an explicit trusted-client-IP policy before per-client rate limits can be assumed correct. Client-supplied forwarding headers must never bypass limits.
- Retain one API worker/replica with the default process-local coordination. Additional replicas require private TLS Valkey and the complete shared storage/key/proxy setup in [SCALING.md](SCALING.md).
- Use private durable storage and protect/retain backups. Database/file encryption and key retention are described in [KEY_MANAGEMENT.md](KEY_MANAGEMENT.md).

No DNS, certificate provisioning or public deployment is performed by these commands.

## Build trust and reproducibility

Python runtime dependencies install from `backend/requirements.lock` with `--require-hashes`; npm uses its frozen lockfile. Both Dockerfiles support a BuildKit `proxy_ca` secret. Compose supplies the public combined trust bundle at `PILOT_BUILD_CA`, defaulting to `/etc/ssl/certs/ca-certificates.crt` on Linux. Set that variable to a trusted public CA bundle if your host requires another location. It must contain normal roots as well as any approved HTTPS proxy CA. The mount is temporary and is not copied into image layers.

In the managed cloud execution environment the local Docker daemon was checked explicitly. Buildx's cache may need a writable location without changing Docker credentials:

```bash
BUILDX_CONFIG=/workspace/.cache/docker-buildx COMPOSE_PARALLEL_LIMIT=2 \
  docker compose --env-file pilot.env --project-name freecollab build
```

Keep Docker's registry authentication and proxy settings intact. Do not print a resolved `docker compose config`, container environment or secret file to diagnose setup. `docker compose config --quiet` validates configuration without dumping its values.

Keep registry/package credentials, SSH keys, mobile signing material and runtime
databases outside the repository build contexts. The root, frontend and backend
`.dockerignore` files exclude common credential paths and private key formats;
check them when adding a new build context. Files uploaded to a remote builder
are exposed to that builder even if a later stage omits them from the final image.
Use temporary BuildKit secret mounts for local build authentication or CA trust,
preserving TLS verification. Railway-specific Dockerfiles keep their supported
plain install steps; do not copy credentials or proxy trust bundles into them.

## Upgrade and message-key migration

Keep the previous image identifiers and take a cold backup first. Stop all writers, including external maintenance jobs, before applying a message-key migration. Add the old content key to the configured key ring, choose a new active ID, and follow [KEY_MANAGEMENT.md](KEY_MANAGEMENT.md) before removing any key. Legacy JWT-derived rows require an explicit legacy key.

The CLI audits by default and writes only with `--apply`:

```bash
set -euo pipefail
# Keep this Bash shell open until the same running writers have been resumed.
pilot_running_services="$(docker compose --env-file pilot.env --project-name freecollab ps --services --status running)"
pilot_writers=()
while IFS= read -r pilot_service; do
  if [[ -n "$pilot_service" && "$pilot_service" != db ]]; then
    pilot_writers+=("$pilot_service")
  fi
done <<< "$pilot_running_services"
if (( ${#pilot_writers[@]} )); then
  docker compose --env-file pilot.env --project-name freecollab stop "${pilot_writers[@]}"
fi
docker compose --env-file pilot.env --project-name freecollab run --rm --no-deps backend \
  python -m app.cli migrate-messages
docker compose --env-file pilot.env --project-name freecollab run --rm --no-deps backend \
  python -m app.cli migrate-messages --apply
if (( ${#pilot_writers[@]} )); then
  docker compose --env-file pilot.env --project-name freecollab up -d --no-build --no-deps --wait "${pilot_writers[@]}"
fi
```

This stops every running Compose writer, including integration, mail and optional
workers, while leaving PostgreSQL available. Only the previously running services
are recreated with the reviewed key configuration; stopped optional profiles stay
stopped. Stop external writers separately. If an audit or migration fails, keep
writers stopped while investigating against the backup.

Do not rotate `JWT_SECRET` as a substitute for the legacy decryption key. Audit errors must be investigated against a backup; do not relabel unknown plaintext as authenticated ciphertext. SQLModel startup creates missing tables for the pilot's additive schema. It does not implement arbitrary schema alterations; future incompatible releases require explicit, tested migration tooling.

## Cold backup

Create the parent directory, then choose a new output directory:

```bash
mkdir -p pilot-backups
python scripts/pilot-backup.py pilot-backups/before-upgrade \
  --env-file pilot.env --project freecollab
```

The v2 helper stops every running frontend/API/mail/integration/AI/SFU/Matrix writer, leaves PostgreSQL running, creates a consistent custom-format `pg_dump`, and archives the files, mail and Matrix volumes that exist. The Matrix archive includes its SQLite database, configuration, server signing keys and media; the mail archive may contain development-only plaintext `.eml` instructions. It copies the private key configuration, records exact application/Matrix/worker image IDs and hashes every artifact. The AI worker must use the same backend image. It restarts only services that were running before the backup. A failed operation leaves incomplete output for diagnosis and never reports success.

The directory contains plaintext private keys and may contain plaintext files. It has private permissions but is not encrypted by this tool. Transfer it to controlled, encrypted backup storage. SHA-256 checks detect accidental corruption; they do not authenticate an archive obtained from an untrusted party. Retain the exact Docker images, or export them with `docker image save`, alongside the backup's manifest. The helper does not export images automatically.

To prepare an authenticated encrypted bundle for off-host storage, use the separate
sealing helper from the existing locked backend Python environment (PyNaCl is
already a dependency):

```bash
# Keep this independent binary key outside the backup and its off-host storage.
backend/.venv/bin/python scripts/backup-seal.py keygen /private/keys/freecollab-backup.key
backend/.venv/bin/python scripts/backup-seal.py seal pilot-backups/before-upgrade \
  /private/exports/before-upgrade.fcsealed --key-file /private/keys/freecollab-backup.key
# On the trusted recovery host, authenticate into a new private directory:
backend/.venv/bin/python scripts/backup-seal.py open /private/exports/before-upgrade.fcsealed \
  pilot-backups/authenticated-copy --key-file /private/keys/freecollab-backup.key
```

Create the private parent directories first. The helper generates a mode-0600
32-byte binary key, uses bounded XChaCha20-Poly1305 SecretStream frames, and
authenticates the complete bundle before publishing the recovered directory.
It rechecks the existing artifact hashes and inner archive paths, refuses existing
keys/destinations, and removes staged plaintext on failure. Linux procfs directory
anchors and atomic no-replace publication protect against renamed staging paths.
Use private parent directories; an untrusted shared parent can retain empty
staging directories, but cannot replace the authenticated published content.
The original plaintext backup is
retained; sealing does not erase it, upload anything, include Docker images or
schedule off-host copies. Retain the independent key separately: losing it makes
the sealed copy unrecoverable. Anyone holding it can authenticate a new bundle;
keep the expected backup source/date under operator control. Sealing is separate
from client message-recovery keys and from application signing/content keys.

## Restore into a separate installation

Never test restore against your source project. The following refuses an existing target project, target volumes or target environment file:

```bash
python scripts/pilot-restore.py pilot-backups/before-upgrade \
  --project freecollab-restore --env-file pilot-restore.env --port 8081
```

Choose an unused target port. If the public website already uses its default
8081 on this host, select another port such as `--port 8082` for the restore.

The helper accepts v1 and v2 backups. It checks every artifact hash and tar path, rejects links/special files, requires the exact backed-up service images locally, and refuses existing containers, networks or colliding files/mail/Matrix/database volumes. It restores the database and every recorded volume, preserving private file permissions and numeric ownership for the app and Synapse. A previously running Matrix service restarts with its recorded image and retained identity. Mail, integration and AI workers remain stopped by default, preserving their configuration and queues without sending restored work. After reviewing retained recipients/destinations and duplicate-delivery risks, `--resume-mail`, `--resume-integrations` and `--resume-ai` independently opt in to workers that were running in the source backup. These flags do not start workers that were stopped or absent at backup time. Before `--resume-ai`, review queued/uncertain jobs, model recipients, profile grants, budgets and whether the source AI worker is still active: separate restored databases cannot deduplicate provider charges across both copies. A legacy backup lacking an account-security key receives a new independent key; existing keys are preserved. The source installation is not changed. Restoring the same target again is refused. Do not manually delete an existing installation merely to satisfy this guard.

The integration worker also runs recurring AI recaps, so its resume review includes those model calls. `--resume-ai` controls the independent durable AI-run worker.

Run the core workflow against the restored installation and compare history and file bytes before routing traffic. If a restore fails after creating resources, retain the logs and the original backup; use a different fresh project for the next diagnosed attempt. To remove only a disposable test installation you created:

```bash
docker compose --env-file pilot-restore.env --project-name freecollab-restore down --volumes
```

Never use `down --volumes` on a retained pilot unless its data is intentionally being deleted.

## Repeat the operations acceptance check

After building the two pilot images, run:

```bash
python scripts/test-pilot.py --port 18090
```

This creates fresh, randomly named source/restore projects on loopback ports 18090/18091, inserts synthetic encrypted history and file data, performs a cold backup/restore, compares decrypted history and original file bytes, checks that the source remains intact, and verifies refusal of existing-target and corrupt backups. It removes only the projects and volumes it created. Choose two unused adjacent ports.

Verified in the managed development environment: both final frontend layouts and backend image build using frozen/hash-checked dependencies, Nginx/PostgreSQL readiness and real API read/write, one synthetic legacy-message migration (dry run changed no rows, apply changed one, repeat changed none), and the complete separate-project restore acceptance check. The explicit-API frontend also passed `nginx -t` without backend networking and served HTML plus the widget bundle over HTTP in a separate container without a backend DNS entry. Public TLS, off-host storage and an external deployment were not exercised.

## Observability and recovery limits

`/health` is process liveness and `/ready` checks database connectivity. Compose checks database readiness before starting the API, then API readiness before the frontend. Inspect bounded service logs with `docker compose ... logs --tail 100`; do not dump credentials. Restore checks should include an encrypted historical message, a file byte comparison, membership restrictions and a WebSocket exchange. Authenticated operator diagnostics and Prometheus metrics expose queue/worker health without content or recipient labels; see [INTEGRATION_OPERATIONS.md](INTEGRATION_OPERATIONS.md). Routing alerts, external certificate renewal and off-host backup scheduling remain installation responsibilities.
