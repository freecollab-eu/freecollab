# Release operations and optional Linux backup scheduling

[Railway](../RAILWAY_DEPLOY.md) is the primary hosted self-installation guide;
[Compose operations](OPERATIONS.md) remains the Linux alternative. FreeCollab is
open source: no mandatory pilot cohort or project-website account. This kit does
not expose the development preview, provision a deployment or send email.

## Passive preflight and monitoring

Run `python scripts/pilot-preflight.py https://YOUR_APPLICATION_HOST`. Optionally
use `--expect-version 0.9.0` and repeat `--require-capability` for a boolean key,
e.g. `integrations.connection_setup` or `secure_messaging`. Only `/api/health`,
`/api/ready`, `/api/capabilities` are read. HTTPS port 443 and public DNS/IP answers
are required; credentials/paths/queries/fragments in the origin and redirects are
refused. Request deadlines are ten seconds by default (maximum 15), DNS at most
five, each response at most 256 KiB. Certificate-chain/hostname verification stays
enabled, including with `--ca-file`; inherited proxy/CA settings are retained.

`--metrics-token-file /private/metrics-token` adds GET `/api/operations/metrics`.
Use an independent `OPERATIONS_METRICS_TOKEN` of at least 32 random bytes in an
owned mode-0600 regular file; symlinks and group/world access are refused. This is a
scoped metrics credential, not an account JWT. No remote body, raw error or
credential is printed. Exit0 means the passive checks passed. Nothing sends mail,
authorizes/tests OAuth, retries work, reads content or proves delivery/recovery.

Merge [Prometheus](../deploy/pilot/prometheus.example.yml) and
[blackbox](../deploy/pilot/blackbox.example.yml) examples into private monitoring,
replacing every `REPLACE_...`. Mount the metrics token privately/read-only. Route
[alerts](../deploy/pilot/alerts.yml) to your own receiver. They cover readiness,
certificate expiry, metrics access, enabled-worker heartbeats, failed/overdue
queues, Linux backups/disk. Provider delivery is separate from HTTP readiness;
retained failed rows can keep an alert active. Tune thresholds to actual workload.
Use `promtool check rules`/`check config` before installation.

Linux backup/disk alerts require node_exporter's textfile/filesystem collectors
with job `freecollab-pilot-host`. Configure Railway storage/backup alerts in
Railway separately. Also alert on missing backup series or failed node-exporter
scrapes: no series does not mean a successful backup. This kit does not configure
an external monitor or send alerts to anyone.

## Compose-only scheduled sealed off-host backups

This section does **not** export Railway volumes. A cold backup stops running
frontend/API/mail/integration/Matrix writers, then restarts only source services
previously running. Reserve a maintenance window and check writer restart.

1. Install the existing locked Python environment/PyNaCl, Docker Compose v2 and
   your chosen off-host tool. The example uses `rclone` with your own remote and
   private credentials file. No accounts or credentials come with this kit.
2. Create `/etc/freecollab-backup/keys` and `/var/lib/freecollab-backup` mode 0700,
   plus your node-exporter textfile directory. Prefer a dedicated backup filesystem
   so archives cannot consume the database disk.
3. Generate the independent key **once**:
   `backend/.venv/bin/python scripts/backup-seal.py keygen /etc/freecollab-backup/keys/offhost.key`.
   Keep a controlled recovery copy separate from off-host storage/application and
   client-recovery keys. Never regenerate a retained installation's keys.
4. Copy [backup-plan.example.json](../deploy/pilot/backup-plan.example.json) to
   `/etc/freecollab-backup/plan.json`, owned mode 0600. Set real paths and the
   existing Compose project/private env file. Replace the remote placeholder in
   both argv lists. [The schema](../deploy/pilot/backup-plan.schema.json) documents
   the closed set of fields; the runner validates permissions, bounds, absolute
   paths and substitution names without evaluating shell strings.
5. Keep transfer secrets only in your tool's private credential store, never argv
   or JSON. `upload_argv` must copy only `{bundle}`; `verify_argv` must independently
   verify that exact object. The example uses immutable upload and rclone's full
   download/byte check, limited to the unique bundle via `{file_list}`.
6. Measure before setting `max_backup_bytes`/`reserve_bytes`. The sample requires
   22 GiB free for a 10 GiB maximum archive plus 2 GiB reserve. Space is checked before
   archive/seal and an oversized finished archive is rejected; there is no live
   filesystem quota during pg_dump. Budget live data growth and Docker temporary
   helpers separately and monitor both disks. This estimate cannot guarantee a
   reserve against unbounded source growth.
7. Run `python deploy/pilot/backup.py /etc/freecollab-backup/plan.json` explicitly.
   It locks one run, calls existing `pilot-backup.py`, then `backup-seal.py`, upload
   and verifier. Success advances only after every stage succeeds. No new archive
   format, cryptography, pruning or remote deletion is implemented.
8. Verify the actual remote object and independent restore. Review/copy the
   [service](../deploy/pilot/freecollab-backup.service.example) and
   [timer](../deploy/pilot/freecollab-backup.timer.example) to named files in
   `/etc/systemd/system`. Adjust paths/time/window, run `systemd-analyze verify`,
   then explicitly enable the timer. Nothing installs/enables it automatically.

Only generic stages reach the journal; child output is suppressed to avoid
logging provider/private diagnostics. Failures retain local artifacts. The
plaintext backup remains in its private run directory after sealing: protect it
as credentials and define retention separately. Unique immutable off-host names
prevent accidental overwrite. The runner never deletes backups, images, source
volumes or keys.

The service has no forced cold-helper deadline and sends stop only to its wrapper.
The wrapper lets the existing helper finish its restart-writers `finally` before
honoring stop. Do not SIGKILL it or its child group during cold backup. A two-hour
alert identifies hung snapshots; inspect source state after host/power failure.
Seal/transfer commands have configured deadlines after writers restart. No
wrapper can restore processes after a power loss.

The success timestamp establishes only that configured upload/verification
commands succeeded. Remote retention, geographical independence, account/key
recovery and a usable application restore require a real rehearsal.

## Recovery rehearsal

Retrieve the sealed bundle privately, retain exact recorded Docker images, then:

```bash
backend/.venv/bin/python scripts/backup-seal.py open /private/recovery/backup.fcsealed \
  /private/recovery/opened --key-file /private/independent/offhost.key
python scripts/pilot-restore.py /private/recovery/opened \
  --project freecollab-restore-20261021 --env-file /private/recovery/restored.env --port 8081
```

Choose a genuinely fresh project/env/port; refusal protects an existing target.
The bundle does not include Docker images. Both outbound workers stay stopped by
default after restore. Review recipients/destinations and duplicate-delivery risk
before explicitly resuming either. Never use `down --volumes` on retained source
data merely to pass a restore guard.

Compare original file bytes, historical content, memberships/sessions, exports,
customer configuration and queues. Secure installations also require original
Matrix identity/media/database and actual trusted-client decryption with the
user's independent recovery material. Server backup does not replace client keys.

## What still needs the selected installation

The repository provides runtime/configuration, passive preflight, alert templates
and scheduled orchestration of existing backup/sealing tools. Actual public
DNS/TLS renewal, Railway private routing/volume permissions, SMTP inbox receipt,
customer provider consent, off-host credentials/retention and fresh-host recovery
remain deployment checks. No external deployment, provider login, message send or
off-host upload occurs by adding or testing this kit.
