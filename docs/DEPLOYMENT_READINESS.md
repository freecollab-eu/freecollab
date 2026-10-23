# Deployment readiness

Installation operators can read `/operations/pilot-readiness` in the private
operator dashboard. The route name is retained for compatibility; this is a
self-hosting diagnostic, not enrollment in a managed pilot or a deployment gate.
Use a current, tracked personal installation-admin session. Workspace ownership,
moderator access, a scoped organization session or the metrics credential cannot
read it. A legacy untracked session must sign in again.

The response combines the existing security and mail diagnostics. It checks only
local configuration and the database serving this request. Reading it executes
SELECTs; it does not bootstrap security rows, test SMTP, call providers, open
key/backup files, generate credentials or change queues.

- `ready`: the stated local check passed. SMTP configuration and a recent worker
  heartbeat do not establish recipient delivery.
- `attention`: review a mismatch, expired heartbeat, clock skew or production
  recommendation. Production SQLite receives a recommendation to use PostgreSQL;
  this diagnostic does not disable SQLite.
- `unconfigured`: a required local setting for that check is absent or the mail
  configuration is only a development file sink.
- `unknown`: this application has no authoritative evidence. Proxy/host policy
  and off-host backup/restore verification remain unknown and require an operator
  check. Source files or an archive existing on disk do not prove the active
  deployment or successful recovery.

Heartbeats are current for 180 seconds. A timestamp over 30 seconds in the future
needs attention. The integration worker handles always-available app events,
scheduled messages, reminders and cleanup, plus enabled recaps, workflows, data
controls, webhooks, proofs, remote synchronization and browser/native push.
On-demand AI, cloud-picker and meeting actions execute independently. The private
dashboard reports fourteen actual worker groups and their stored states or due
times; flags describe local availability only;
customer configuration, consent and current grants are separate.

From the configured backend environment, the actual worker entry points are:

```sh
python -m app.services.mail
python -m app.services.integration_worker
```

Use the existing Compose profiles instead when running Compose, as documented in
[Integration operations](INTEGRATION_OPERATIONS.md). Starting workers processes
already authorized queued work. Explicit SMTP test delivery and retries remain
separate password/MFA-confirmed actions in that dashboard.

Before publishing a self-hosted installation, verify public DNS and HTTPS from
outside its network, exact CORS origins, edge Host rejection, overwritten
forwarding headers, restricted proxy trust and private API/database access. The
application cannot infer these controls from a deployment file. Monitor the
existing authenticated `/operations/metrics` endpoint, queues, infrastructure,
certificate expiry and alert routing independently. Its credential is shown only
as a configured boolean in readiness.

For backup evidence, follow [Cold backup and authenticated sealing](OPERATIONS.md#cold-backup)
and [Restore into a separate installation](OPERATIONS.md#restore-into-a-separate-installation).
Record backup creation time, verified off-host transfer and an isolated restore
result in the operator's dated runbook. Retain the independent seal key separately
and retain the exact required images. Keep restored outbound workers paused until
their destinations and retained jobs have been reviewed. The readiness API has no
trusted evidence registry and therefore reports backup timestamps/freshness as
unknown; it never promotes a local plaintext directory to off-host success.
