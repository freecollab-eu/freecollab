# Integration operations

FreeCollab keeps account mail and generic notifications in its existing durable, encrypted outbox. The operator dashboard adds configuration checks, redacted delivery state, explicit test delivery, failed-delivery retry and authenticated monitoring. These endpoints return no SMTP credentials, recipients, subjects, account links, recovery keys or message bodies.

## Operator role

The native installation role `admin` controls operations. A workspace owner/admin or native moderator cannot read operational settings, the outbox or metrics. Register the intended operator and bootstrap the first installation administrator through trusted server access:

```bash
docker compose --env-file pilot.env -p freecollab exec backend python -m app.cli bootstrap-admin --email operator@example.com
```

Use the existing installation's project/configuration names. This command preserves content and account keys. An existing installation administrator manages later role changes through the admin API.

Before testing mail, verify the operator account's email and enable MFA. Verification mail can be delivered through the normal worker before the operator test feature is used. Test and retry actions require the current password and, when enabled, an authenticator or unused account recovery code. Only the requesting operator's verified current address receives a test; callers cannot supply a destination. Queueing does not mean delivery succeeded.

## SMTP setup

Choose a provider with an appropriate EU region, data processing agreement, sender-domain support and delivery/bounce reporting. FreeCollab does not create provider accounts, purchase a domain or provision DNS. Store credentials in the private operator environment, never frontend source or workspace settings.

```dotenv
MAIL_DELIVERY_MODE=smtp
SMTP_HOST=smtp.provider.example
SMTP_PORT=587
SMTP_USERNAME=provider-account-name
SMTP_PASSWORD=<private-provider-password>
SMTP_FROM=FreeCollab <no-reply@your-domain.example>
SMTP_STARTTLS=true
SMTP_IMPLICIT_TLS=false
SMTP_TIMEOUT=15
PUBLIC_APP_URL=https://collab.your-domain.example
```

For implicit TLS use port 465, `SMTP_IMPLICIT_TLS=true` and `SMTP_STARTTLS=false`. Select exactly one TLS mode. The client verifies certificates/hostnames using the runtime's trusted CA store and has a finite timeout. Production refuses SMTP without TLS. SMTP_HOST is a hostname/IP, not a URL. Anonymous relays can omit both username and password; configure both when authentication is required.

Retain `ACCOUNT_SECURITY_KEY` with protected secrets and backups. It protects MFA state and queued mail bodies. Do not regenerate it during an SMTP change or replace existing signing/content keys.

Start the existing mail profile alongside the installation's other required profiles:

```bash
docker compose --env-file pilot.env -p freecollab --profile mail up -d mail-worker
```

Keep the secure profile/services enabled on a secure installation. The mail worker records a heartbeat on each poll and transport attempt: status is `unknown` before its first poll, `healthy` within three minutes, then `stale`. The web API alone does not deliver mail.

## Domain and external delivery checks

1. Verify the sending domain and publish the provider's exact SPF record. Maintain one SPF TXT record including every authorized sender; copied placeholders do not work.
2. Publish the provider's DKIM selector/key records and wait for verification. Rotate keys through the provider's process.
3. Publish DMARC with aggregate reporting and suitable alignment. Start with monitored policy if needed, then tighten after checking every legitimate sender.
4. Confirm envelope/return-path and visible From-domain alignment. Monitor provider bounce/complaint reports.
5. Configure HTTPS for PUBLIC_APP_URL so recovery links use the actual public origin. A private file sink is development-only.
6. Explicitly send an operator test. Check outbox status, receipt in the operator inbox, provider delivery records and message authentication results. SMTP acceptance is not proof of final inbox placement.

The panel checks local settings only. Opening it performs no provider login, DNS lookup or unsolicited delivery. A successful configuration check does not establish valid external credentials, SPF/DKIM/DMARC or inbox delivery.

## Retry and retention

The worker retries committed pending mail with bounded backoff. After five failed attempts it records `failed`. Reauthenticated operators can retry only failed records. Retry preserves the original recipient, encrypted content and outbox ID, then resets the automatic attempt budget. Test and retry actions each have a persistent limit of ten per operator/hour.

SMTP delivery is at-least-once. Remote acceptance followed by a connection/database failure can cause a duplicate. Recovery links remain one-use and their expiry is not extended. Request a new account verification/reset link when an old one has expired. Responses never decrypt account content into the operational API.

Recipient addresses remain routing metadata in the outbox. Retained counters describe current records, not permanent cumulative statistics. Define retention/access for the database and private mail sink. This dashboard does not expose or delete retained `.eml` files.

## Monitoring

`GET /health` is process liveness. `GET /ready` checks database access. Both are generic existing endpoints for an external HTTPS uptime check. They do not prove SMTP, OIDC, OTRUST anchoring or webhook delivery.

| Operator endpoint | Meaning |
| --- | --- |
| `GET /operations/status` | Safe local mail configuration, DB reachability, queue counts/age and independent mail/integration/AI worker heartbeats. |
| `GET /operations/mail/deliveries?limit=50` | Bounded delivery records, without recipients, subject/body or raw provider exceptions. |
| `POST /operations/mail/test` | Body `{password, code?}`; queue a diagnostic to the verified requesting operator. |
| `POST /operations/mail/deliveries/{id}/retry` | Same reauthentication body; reschedule a terminal failed delivery. |
| `GET /operations/metrics` | Authenticated Prometheus text with fixed queue/status labels. |

All responses use `Cache-Control: no-store`. For read-only scraping, configure a separate random `OPERATIONS_METRICS_TOKEN` of at least 32 bytes in the private environment. It is accepted only by `/operations/metrics`; it grants no account, status, outbox or mutation access. Store the same token in the monitor's secret store/private credential file and send Authorization: Bearer, never a query parameter. Rotate the configuration value to revoke it. Native installation-admin JWTs can also read metrics, but remain revocable and expire.

The metrics expose mail configuration, worker health, outbox state/age and all 18 queue groups. AI runs have an independent worker; their heartbeat is distinct from integrations and mail. They have no recipient, account, workspace, channel, destination or provider-model labels. Counters describe retained records, not cumulative throughput or successful external acceptance. The dashboard uses the same aggregate states as the metrics.

| Worker group | Counted records and interpretation |
| --- | --- |
| `webhooks` | Durable metadata delivery state. |
| `otrust` | Submission/proof state; a pending anchoring receipt is distinct from an unsent request. |
| `sync` | Connection state, not a count of synchronized files; `queued` → `pending`, `syncing` → `processing`, `error` → `failed`. |
| `calendar-delivery` | Explicit provider invitations/RSVP jobs; `queued` → `pending`, `running` → `processing`, `succeeded` → `completed`; `uncertain` remains separate. |
| `workflows` | Local workflow runs, including approval waits. |
| `data` | Export/erasure jobs, including file-erasure progress. |
| `push` / `native_push` | Browser/native delivery records; delivery does not prove a person read the notification. |
| `apps` | App-command jobs; `uncertain` and failed review are not automatic retries or completed work. |
| `slack-livebridge` | Actual native/Slack bridge jobs, including due work, future retries and uncertain outcomes. Active bridges and claimed jobs protect source erasure. |
| `ai-runtime` | Durable queued/running runs and separate human review waits. Review waits do not count as overdue worker dispatch. The independent AI worker reports its own heartbeat. |
| `encrypted-work` | Coalesced opaque work-notice/cursor metadata, with real polling cooldown. No encrypted room text is read by the worker. |
| `chat` | Scheduled messages and reminders; sent messages awaiting notification dispatch have a separate `notification_pending` bucket and are excluded from `sent`. |
| `recaps` | Enabled schedules due now (`pending`), enabled future schedules (`scheduled`), and disabled schedules. A claimed run advances its next time; this is not a separate processing-job counter. A private AI draft is not a published recap. |
| `native-call-cleanup` | Retained call capabilities past expiry (`pending`) or not yet expired (`scheduled`); these are cleanup candidates, not call counts. |
| `slack-import-cleanup` | Active preview/import copies past expiry (`pending`), active copies with future expiry (`scheduled`), and other lifecycle states (`inactive`). |
| `personal-oauth-cleanup` | Pending/active grants due for local authority validation (`pending`), recently checked grants (`scheduled`), and inactive grants. Counts do not decrypt claims, validate provider credentials or call the provider. |
| `avatar-cleanup` | Committed `AvatarErasure` records for replaced or removed immutable private profile photos awaiting blob erasure (`pending`), not a count of visible photos. Age starts at the erasure record's creation. Each group pass examines at most 20 records, erases only unreferenced blobs through the installation's private storage backend, and retains storage failures for retry. |

Queue age uses the relevant due time: sync's next run, app/native-push next attempt, chat delivery time, schedule time or cleanup expiry/check deadline. A future due time has zero overdue age. A never-checked personal OAuth grant uses its creation time, not its epoch-valued initial check marker. OTRUST pending receipts are excluded from unsent-job age. Existing webhook/workflow/data and calendar-delivery ages describe retained queued/processing work from creation, rather than a universal provider-response latency.

Integration-worker enablement includes every dispatch flag (`WEBHOOKS_ENABLED`, `OTRUST_ENABLED`, `SYNC_ENABLED`, `WORKFLOWS_ENABLED`, `DATA_CONTROLS_ENABLED`, `PUSH_ENABLED`, `NATIVE_PUSH_ENABLED`, `COPILOT_ENABLED`, `CONNECT_FEDERATION_ENABLED`) and retained pending/processing/future work from always-on app, chat, Slack bridge and local-cleanup groups. Pending profile-photo erasure alone requires the worker even when every dispatch flag is disabled. This prevents an installation using only scheduled messages, personal grants or photo erasure from silently hiding a missing worker. Terminal records and work behind a disabled feature alone do not require a running worker. Photo-erasure metrics expose only aggregate count and oldest age, without photos, filenames, blob IDs, hashes, account identifiers or storage credentials; this group performs no integration-provider dispatch.

The integration worker polls its 17 groups serially, using bounded batches and an independent database session for each group; AI-runtime dispatch uses its own worker. Run one integration-worker instance per supported single-replica installation. Transaction isolation prevents one failed group from rolling back another; it does not make slow provider batches concurrent. The heartbeat is recorded at the start of a poll, and a pass with many slow providers can exceed the three-minute freshness threshold. Investigate sustained lag together with queue progress rather than treating one heartbeat as proof of provider health.

Set `SYNC_ENABLED=true` and run the `integrations` Compose profile to activate configured sync connections; it defaults to false. A normal successful connection is scheduled again after 60 seconds. A pass selects at most two connections; each uses a durable fair scan cursor and a bounded batch of 20 items with an approximately 45-second work budget. Provider requests also have bounded absolute deadlines and pagination. Pausing a connection prevents new dispatches; use the connection activity/conflict controls for scoped retries and resolution. See [REMOTE_SYNC.md](REMOTE_SYNC.md).

A Prometheus scrape can use the public reverse proxy without exposing the API container:

```yaml
scrape_configs:
  - job_name: freecollab
    scheme: https
    metrics_path: /api/operations/metrics
    static_configs:
      - targets: [collab.your-domain.example]
    authorization:
      type: Bearer
      credentials_file: /run/secrets/freecollab_metrics_token
```

Mount the credential file privately/read-only in the monitor. Keep certificate verification enabled. Example rules for enabled workers are:

```yaml
groups:
  - name: freecollab-workers
    rules:
      - alert: FreeCollabMailWorkerStale
        expr: freecollab_mail_configured == 1 and on(instance, job) freecollab_mail_worker_healthy == 0
        for: 5m
      - alert: FreeCollabIntegrationWorkerStale
        expr: freecollab_integrations_enabled == 1 and on(instance, job) freecollab_integrations_worker_healthy == 0
        for: 5m
```

Useful initial alerts are readiness failure, HTTPS certificate expiry, an enabled worker missing recent heartbeats, growing failed/unsent queues and low disk/database space. Keep infrastructure CPU/memory/disk/certificate/backup monitoring outside the content API. Route operational alerts to the installation operator.

## Backup and recovery drills

Use [OPERATIONS.md](OPERATIONS.md), including the Matrix volume for secure installations. Retain the independent account-security key and integration secrets. Stop every writer, including mail/integration/AI workers, during a consistent cold backup.

Schedule restoration into a fresh isolated project and verify native history/files, encrypted room/media access with retained client keys, outbox state, integration configuration, SCIM directory/managed identities and restored image versions. Keep external SMTP, webhooks, timestamp submission, remote sync and AI execution stopped on the drill target until explicitly enabled; a restored worker can resend pending work or change remote content. The restore helper leaves mail, integration and AI workers stopped by default. Its separate `--resume-mail`, `--resume-integrations` and `--resume-ai` flags require deliberate review and resume only workers that were running in the source backup. Before resuming AI, inspect queued/uncertain jobs, actual model recipients, profile grants, budgets and source-worker activity; two restored/source databases cannot prevent duplicate provider charges across both copies. Configuration, credentials and queues are retained. Disable the restored SCIM endpoint's external route or rotate directory credentials before admitting provisioning traffic into a drill copy. Use the authenticated sealing helper in [OPERATIONS.md](OPERATIONS.md) when preparing an encrypted off-host backup copy, and retain its independent key separately.

A server backup does not replace users' separate encryption recovery keys. Independent security review remains a separate prerequisite for broader deployment.

## Local verification

```bash
cd backend
.venv/bin/python -m pytest tests/test_operations.py tests/test_operations_worker_groups.py tests/test_smtp_delivery.py tests/test_security_status.py -q
```

The SMTP tests start a loopback-only receiver with an ephemeral test CA. Real SMTP clients negotiate STARTTLS and implicit TLS, authenticate, deliver exact diagnostic bytes and acknowledge durable state. Another test rejects an untrusted certificate without sending content. OpenSSL only generates local test certificates; no external mail is sent and certificate verification stays enabled.

Provider credentials, public DNS/TLS, delivery reputation and actual inbox placement still require installation-specific validation. The local proof does not establish those external systems are configured.
