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
| `GET /operations/status` | Safe local mail configuration, DB reachability, queue counts/age and mail/integration worker heartbeats. |
| `GET /operations/mail/deliveries?limit=50` | Bounded delivery records, without recipients, subject/body or raw provider exceptions. |
| `POST /operations/mail/test` | Body `{password, code?}`; queue a diagnostic to the verified requesting operator. |
| `POST /operations/mail/deliveries/{id}/retry` | Same reauthentication body; reschedule a terminal failed delivery. |
| `GET /operations/metrics` | Authenticated Prometheus text with fixed queue/status labels. |

All responses use `Cache-Control: no-store`. For read-only scraping, configure a separate random `OPERATIONS_METRICS_TOKEN` of at least 32 bytes in the private environment. It is accepted only by `/operations/metrics`; it grants no account, status, outbox or mutation access. Store the same token in the monitor's secret store/private credential file and send Authorization: Bearer, never a query parameter. Rotate the configuration value to revoke it. Native installation-admin JWTs can also read metrics, but remain revocable and expire.

The metrics expose mail configuration, worker health, outbox state/age and webhook/OTRUST/sync queue counts. They have no recipient, account, workspace or channel labels. Sync states normalize `queued` to `pending`, `syncing` to `processing` and `error` to `failed`; age uses the next scheduled run rather than connection creation. OTRUST receipts may legitimately wait for anchoring; those pending receipts are counted separately and excluded from unsent-job age. Integration-worker enablement includes `SYNC_ENABLED`, even when webhooks and OTRUST are disabled.

The integration worker independently polls three durable queues: webhooks,
OTRUST and remote sync. Set `SYNC_ENABLED=true` and run the `integrations` Compose
profile to activate configured sync connections; it defaults to false. A normal
successful connection is scheduled again after 60 seconds. A pass selects at most
two connections; each uses a durable fair scan cursor and a bounded batch of 20
items with an approximately 45-second work budget. Provider requests also have
bounded absolute deadlines and pagination. One provider failure cannot roll back
another queue's transaction. Pausing a connection prevents new dispatches; use
the connection activity/conflict controls for scoped retries and resolution.
See [REMOTE_SYNC.md](REMOTE_SYNC.md).

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

Use [OPERATIONS.md](OPERATIONS.md), including the Matrix volume for secure installations. Retain the independent account-security key and integration secrets. Stop every writer, including mail/integration workers, during a consistent cold backup.

Schedule restoration into a fresh isolated project and verify native history/files, encrypted room/media access with retained client keys, outbox state, integration configuration, SCIM directory/managed identities and restored image versions. Keep external SMTP, webhooks, timestamp submission and remote sync stopped on the drill target until explicitly enabled; a restored worker can resend pending work or change remote content. The restore helper leaves both mail and integration workers stopped by default. Its separate `--resume-mail` and `--resume-integrations` flags require deliberate review and resume only workers that were running in the source backup. Configuration, credentials and queues are retained. Disable the restored SCIM endpoint's external route or rotate directory credentials before admitting provisioning traffic into a drill copy. Use the authenticated sealing helper in [OPERATIONS.md](OPERATIONS.md) when preparing an encrypted off-host backup copy, and retain its independent key separately.

A server backup does not replace users' separate encryption recovery keys. Independent security review remains a separate prerequisite for broader deployment.

## Local verification

```bash
cd backend
.venv/bin/python -m pytest tests/test_operations.py tests/test_smtp_delivery.py -q
```

The SMTP tests start a loopback-only receiver with an ephemeral test CA. Real SMTP clients negotiate STARTTLS and implicit TLS, authenticate, deliver exact diagnostic bytes and acknowledge durable state. Another test rejects an untrusted certificate without sending content. OpenSSL only generates local test certificates; no external mail is sent and certificate verification stays enabled.

Provider credentials, public DNS/TLS, delivery reputation and actual inbox placement still require installation-specific validation. The local proof does not establish those external systems are configured.
