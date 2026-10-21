# Private security and service operations

A native installation administrator opens **Integrations → Operations** to review
security configuration, local service health and recent authenticated security
actions. Workspace ownership, a customer SSO session or a metrics credential does
not grant this operator access. The existing email delivery controls and history
remain in the same panel.

These are configuration and retained-state checks. A green result does not
establish a valid public certificate, successful provider delivery, a recent
restorable off-host backup or an independent security audit. Use
[OPERATIONS.md](OPERATIONS.md) for installation, backup sealing and isolated
recovery, and [INTEGRATION_OPERATIONS.md](INTEGRATION_OPERATIONS.md) for mail,
workers and private metrics.

## Security overview

The read-only overview returns aggregate counts and fixed check identifiers. It
does not contact an IdP, cloud provider or SMTP server, open encrypted messages,
decrypt credentials, read development key files or change configuration.

| Check | What it establishes |
| --- | --- |
| HTTPS application origin | The configured public URL has supported public HTTPS origin syntax. No DNS lookup or certificate probe is performed. |
| Allowed browser origins | HTTPS origins use the browser's exact canonical origin form and include the configured public origin. |
| Experimental routes | Whether advanced API routes are disabled or deliberately enabled. |
| AI demo routes | Whether the separate server AI demo is disabled or deliberately enabled. |
| Independent server keys | Explicit signing, account-security and content-key settings have the expected shape and separation. File-backed development keys remain unknown rather than being opened or regenerated. |
| Operator authenticators | Aggregate coverage for active personal installation administrators. Blocked accounts and managed workspace identities are excluded. No names or email addresses are returned. |
| Storage headroom | Free bytes and percentage on the configured upload filesystem. Below 1 GiB or 5% free needs attention; an inaccessible or missing volume is unknown. This does not measure a separate database volume, enforce a quota or inspect backup storage. |
| Email worker leases | Due, expired or invalid processing claims and failed retained mail jobs, when mail delivery is available. |
| Webhook worker leases | The same retained queue checks for enabled webhook delivery. |
| Version proof worker leases | The same checks for enabled version proofs; a legitimate anchoring wait is distinct from an expired processing claim. |
| File and calendar sync leases | Enabled connections whose schedules or processing leases need attention. Paused connections do not become dispatch work. |

The fixed statuses are `pass`, `attention`, `disabled` and `unknown`. Unknown is
not proof of failure or readiness. Queue totals use the actual worker lease
boundaries; missing claim timestamps are reported as invalid rather than silently
healthy. Reading this page never retries or reclaims a job. Review the existing
mail or connection activity before deliberately retrying work.

## Authenticated security actions

The activity view records selected server-observed authenticated mutations:
account sign-in/out and security controls, installation roles/access, customer SSO
configuration, workforce invitations/policies, directory credentials/rules and
provisioning, workspace roles/removal, encryption controls, cloud connection
changes and diagnostic mail requests. Ordinary chat, documents, files, queries
and read-only operator requests are outside this recording allowlist.

Each record contains an event ID, UTC time, fixed action name, result
(`success`, `denied` or `error`), HTTP status and bounded opaque actor/workspace/
target identifiers where applicable. Directory requests identify the directory
credential rather than pretending a human performed the provisioning action.
The recorder does not read request bodies, query strings, IP addresses, headers,
passwords, bearer tokens or message content. Email-shaped path identifiers are
excluded. The API returns no email, recipient or provider secret fields.

The actor is annotated only after server-side credential validation. Caller
headers cannot choose it. Anonymous failed sign-ins do not create per-attempt
activity rows; the existing bounded authentication rate counters handle those
attempts. This prevents anonymous requests from growing a new audit table without
bound. The activity view is therefore a selected authenticated action history,
not a complete access or intrusion log.

Recording is best effort after the request's action. If an already committed
action succeeds but its separate activity write fails, the original response
remains successful and the server emits the generic message **Security activity
record could not be persisted**. No credential or exception body is added to that
message. Monitor the server log for this condition. Do not infer that an absent
row proves an action never occurred.

Database administrators control the stored records and backups. The log is not a
tamper-proof external ledger and does not provide independent attestation.

## Retention and storage bounds

```dotenv
SECURITY_AUDIT_RETENTION_DAYS=90
SECURITY_AUDIT_MAX_RECORDS=100000
```

Retention accepts 7–365 days and the record target accepts 1,000–1,000,000 rows.
The UI shows **Up to 90 days, newest 100,000 records** with the installation's
configured values. Busy installations can retain less than the full day window.

Reads immediately exclude records older than the configured age. Each write
removes at most 200 age-expired rows and at most 200 oldest rows toward the record
target. SQLite writers serialize this decision with an immediate transaction;
PostgreSQL uses a transaction-scoped advisory lock. Concurrent normal insertions
do not grow a log already at its configured record target. Lowering the target
below a larger existing log converges incrementally as subsequent writes prune
old rows; it is not immediate physical erasure. An idle installation may retain
age-expired rows on disk until a later recorded write.

Existing database exports, sealed bundles and other backup copies retain their
own historical records. Changing live retention does not rewrite or delete those
copies. Coordinate their retention separately.

## Private API

The reverse proxy adds `/api` to these application paths:

| Endpoint | Access and result |
| --- | --- |
| `GET /operations/security` | Native unrestricted installation administrator; fixed checks, operator counts, upload filesystem counters and four queue summaries. |
| `GET /operations/security-events` | Same administrator requirement; bounded metadata-only history and `next_cursor`. |
| `GET /operations/metrics` | Existing native operator access or the optional independent read-only metrics credential. That credential cannot read either security endpoint. |

Authorized security responses use `Cache-Control: no-store`. The event endpoint
accepts `limit` from 1–100, an opaque `cursor` from a previous page, and optional
`outcome=success`, `denied` or `error`. It sorts by time and event ID for stable
keyset pagination. It returns the configured `retention_days` and `max_records`
alongside the page; there is no public event export or destructive operator UI.
The client can refresh, filter results and load earlier actions without changing
server configuration.

## Provider changes and concurrent work

Customer SSO secrets remain write-only and workspace-bound encrypted values.
An omitted or empty replacement retains the saved secret only when the canonical
issuer and client ID are unchanged. Changing either clears it unless that edit
explicitly supplies a new secret. A new confidential client needs its own secret;
a supported public client may omit one. Saving still disables the provider,
advances its revision and invalidates old tests/sessions. See [OIDC.md](OIDC.md).

Cloud OAuth callbacks and saved connection tests recheck current native authority,
account/session state and connection revision after acquiring the relevant locks
and after network work. An administrator's concurrent retarget, pause or access
change cannot attach an older authorization to a newer resource. Retargeting
continues to clear affected credentials as described in [REMOTE_SYNC.md](REMOTE_SYNC.md).

Google/Graph file reads also revalidate the selected parent and canonical revision
after downloading bytes. Strong supported ETags add conditional download guards;
revision or scope changes fail without importing those bytes as the earlier
version. This is stable-read validation across provider calls, not a claimed
remote transaction or provider permission mirror.

## Recovery and verification

The restore helper leaves both mail and integration workers stopped by default.
Review retained destinations, recipients and possible duplicate work before using
the independent `--resume-mail` or `--resume-integrations` options. The optional
backup sealing helper prepares authenticated encrypted copies while preserving
the original plaintext backup. Keep its separate key outside the backup and its
storage location. See the exact commands and limits in [OPERATIONS.md](OPERATIONS.md).
Sealing does not upload a copy or schedule off-host recovery drills.

```bash
(cd backend && .venv/bin/python -m pytest tests/test_security_status.py \
  tests/test_security_audit.py tests/test_operations.py tests/test_pilot_backups.py \
  tests/test_backup_seal.py tests/test_workspace_oidc.py tests/test_remote_sync.py \
  tests/test_remote_providers.py -q)
CHROMIUM_PATH=/usr/bin/chromium backend/.venv/bin/python frontend/tests/security-operations-browser.py
```

The browser proof passed nine groups using a real private API, Vite, Chromium,
audit middleware and private file-mail worker. It covers operator/member/scoped/
metrics-token isolation, MFA/storage/expired-claim diagnostics, real sign-in and
privileged success/denial records, body/credential redaction, result filtering and
pagination, endpoint-failure isolation from mail controls, keyboard focus and a
320px viewport. All its temporary services, accounts, database, keys and mail are
removed. [VALIDATION.md](VALIDATION.md) records the final release checks.

These local fixtures do not establish external SMTP delivery, actual customer
provider consent, public TLS provisioning, off-site uploads or scheduling, or an
independent security audit.
