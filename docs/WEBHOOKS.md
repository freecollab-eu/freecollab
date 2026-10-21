# Signed workspace webhooks

FreeCollab can deliver a durable stream of generic activity events to an HTTPS webhook, including an n8n Webhook node. Delivery is disabled until the operator enables `WEBHOOKS_ENABLED=true` and runs the integration worker. Creating a configuration queues no test request automatically. Sending a test is a separate explicit action.

## Channel grants and protected content

The person granting a webhook must be a workspace owner/admin **and** an administrator of every selected group channel. Direct messages cannot be granted. A workspace role alone does not expose a private channel's webhook configuration or delivery history. Each delivery rechecks the granting person's current roles and active account. Removing those rights cancels queued work. A currently authorized administrator can update a grant; that administrator then becomes its grant issuer.

Events contain only the schema version, event ID, event type, timestamp, workspace ID and, for real activity, opaque channel/resource IDs. There are no message bodies, channel titles, people names, filenames, decision text, timestamp commitments or provider receipts in the payload. This applies equally to native and end-to-end encrypted channels. Secure `channel.activity` comes from the existing verified encrypted-event acknowledgement, without interpreting its encrypted body.

Enabling a webhook deliberately shares this activity metadata with its destination. It does not grant the receiver content access or decryption keys. Any subsequent authenticated API access needs its own authorized user and follows the existing channel/encryption rules.

## Operator setup

Set the following private installation configuration:

```dotenv
WEBHOOKS_ENABLED=true
```

Use the integrated worker profile documented in [INTEGRATIONS.md](INTEGRATIONS.md), or run the standalone queue processor with the same database and independent account-security key as the API:

```bash
cd backend
python -m app.services.webhooks --once
python -m app.services.webhooks --interval 5
```

Signing secrets and complete webhook URLs are encrypted at rest with context-bound account-security encryption. Keep `ACCOUNT_SECURITY_KEY` and database backups together. The API displays the destination origin, but does not return an existing URL's potentially secret path/query. A blank replacement URL in the UI keeps the stored destination. A signing secret appears only at creation or explicit rotation.

Destinations must use HTTPS port 443, without embedded credentials or fragments. DNS is checked at configuration and immediately before delivery; all answers must be public. Local/private/metadata/transition-network answers are rejected. Requests pin a validated address and verify TLS against the original hostname. The inherited HTTP(S) proxy and CA trust are retained, including an HTTPS proxy's own certificate name. There is no direct fallback after a proxy denial, and redirects are never followed. An egress proxy that disallows connections to pinned public IPs must be configured appropriately by its operator; the worker fails delivery instead of weakening validation.

DNS lookup waits at most five seconds. A transport has an absolute thirty-second deadline as well as phase timeouts, so a slow-drip receiver cannot keep a job indefinitely. Worker leases last ninety seconds. Grants/configuration changes serialize with bounded in-flight delivery: an already-started request may finish before the revocation is acknowledged; queued requests after it are cancelled.

## Receiver contract

Supported subscriptions are:

- `message.created`
- `file.created`
- `file.version.created`
- `decision.created`
- `decision.updated`
- `channel.activity`

The separate manual test uses `webhook.test`, with only `workspace_id` in `data`.

An activity payload resembles:

```json
{
  "data": {
    "channel_id": "opaque-channel-id",
    "resource_id": "opaque-resource-id",
    "workspace_id": "opaque-workspace-id"
  },
  "id": "opaque-delivery-event-id",
  "occurred_at": "2026-10-20T12:00:00.000Z",
  "schema_version": 1,
  "type": "message.created"
}
```

Use the **actual request bytes**, not the formatted example or reserialized JSON, for verification. The request carries:

```text
X-FreeCollab-Timestamp: <Unix seconds at this attempt>
X-FreeCollab-Event-ID: <same id as the JSON payload>
X-FreeCollab-Event: <same type as the JSON payload>
X-FreeCollab-Signature: v1=<lowercase hex HMAC-SHA256>
```

The signed input is `ASCII(timestamp) + b"." + raw_request_body`, using the one-time webhook secret as UTF-8 key bytes. Compare equal-length MAC bytes in constant time. Check that the signed timestamp is within five minutes of receiver time, and reject malformed/future/expired requests. After authentication, parse JSON, require schema version 1, and match payload `id`/`type` to their headers.

Treat the event ID as an idempotency key. Persist it with the verified event before acknowledging success, and use a unique database constraint to stop duplicate effects. Retries retain the exact event ID and body while receiving a fresh signed attempt timestamp. A five-minute replay window alone does not deduplicate requests inside that window. See the [n8n recipe](../examples/n8n/README.md) and its copied-code regression tests.

## Delivery lifecycle

Queue insertion participates in the same database transaction as native content creation. A failed content transaction cannot leave a successful webhook behind. Verified secure acknowledgements deduplicate the same opaque event. The worker claims one committed delivery at a time with an expiring lease and random claim token, allowing recovery after a crash while fencing a late worker acknowledgement.

HTTP 2xx acknowledges delivery. Network failures, HTTP 408/425/429 and 5xx are retried up to eight attempts with bounded exponential backoff. `Retry-After` is respected up to one hour. Other responses, including redirects, fail without automatic follow-up. A receiver may accept a request and lose its response, so **delivery is at least once**; the receiver must deduplicate.

The UI shows generic status, attempt count and HTTP status. Receiver bodies and exception strings are not stored because they can contain secrets. Failed/pending jobs can be retried explicitly with their original event ID, subject to rate limits. Pausing, replacing the URL/grants/subscriptions, rotating the secret or deleting a webhook cancels older pending/processing/failed jobs rather than replaying them to a changed destination. New events use the new configuration. Deletion also clears reusable URL/signing material.

The metadata outbox and configuration are part of the application database backup. Retain the independent account-security key to open active destinations after restoration. A restore can replay an unacknowledged delivery; receiver idempotency remains necessary.

## Verification performed

`backend/tests/test_webhooks.py` exercises channel/admin isolation, real local HMAC receiver acknowledgement, retries/rate limits/deduplication, grant revocation, secret rotation, expired leases, fresh native-session checks after a lock wait, private/mixed DNS refusal, and an actual HTTP CONNECT proxy carrying TLS to an isolated test receiver. The TLS test verifies the original hostname and rejects the wrong certificate hostname; a separate deadline test prevents trickle traffic from resetting its overall budget. No customer webhook or live n8n installation was contacted by these tests.
