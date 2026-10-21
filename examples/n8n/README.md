# FreeCollab → n8n

FreeCollab's signed, metadata-only HTTPS delivery works with an n8n **Webhook** node. This recipe includes the actual raw-body verification code and a durable deduplication pattern. The backend's real HTTP/HMAC receiver and the copied JavaScript verifier are tested locally; a live n8n instance has **not** been deployed or tested here.

## Create the receiving workflow

1. Create a **Webhook** node with method **POST**, a private random path and **Raw Body** enabled. Choose response through a **Respond to Webhook** node, so an unauthenticated request is never acknowledged before verification. Publish behind HTTPS on port 443. Copy its production URL; a temporary editor/test URL stops listening when its test run ends.
2. In FreeCollab, open the workspace integrations and create a webhook with that URL. Explicitly select channels you administer and the generic event types you want. Save the one-time signing secret privately. Creation alone sends nothing.
3. Configure this **dedicated webhook secret** for your n8n verification environment under `FREECOLLAB_WEBHOOK_SECRET`. Never use a FreeCollab login token, account key, content key or Matrix recovery key. Do not paste the secret into workflow JSON, event data, screenshots or a shared Code node source.
4. Add a JavaScript **Code** node immediately after Webhook and paste [verify-event.js](verify-event.js). It reads the original `data` binary through `this.helpers.getBinaryDataBuffer(0, 'data')`, verifies the signed timestamp/body, then parses the event. It does not serialize the parsed request body back to JSON for HMAC.
5. Persist the verified event under a unique `event.id` before responding 2xx. Process that durable queue separately with idempotent actions. Only verified metadata leaves the Code node. Do not attach an automatic privileged FreeCollab content reader to this generic event route.
6. In FreeCollab, choose **Send test event**. The receiver should accept a `webhook.test` with only the opaque workspace ID. Confirm delivery in FreeCollab, then trigger a permitted channel event and inspect its generic ID/type payload.

The raw-body recipe was checked against the public n8n Webhook implementation on 2026-10-20: Raw Body places the received bytes in the `data` binary. Validate this setting on your installed n8n version. If raw bytes are missing, the verifier fails closed; don't replace them with `JSON.stringify($json.body)`.

## Secret and Code-node permissions

Self-hosted n8n restricts modules and environment access differently across versions and task-runner configurations. The example requires the standard Node `crypto` module and a securely supplied `$env.FREECOLLAB_WEBHOOK_SECRET`. The operator may need to explicitly allow `crypto` with `NODE_FUNCTION_ALLOW_BUILTIN=crypto` in the appropriate execution environment. Keep that environment and all its workflow editors trusted.

If your n8n instance blocks environment access or has untrusted workflow editors, use an approved credential/vault-backed verification step or a dedicated receiver in front of n8n. Do **not** weaken a shared instance's environment isolation to make this snippet work. No n8n permission or secret configuration is changed by FreeCollab.

## Durable deduplication

An HTTP acknowledgement can be lost after the receiver accepted a request. FreeCollab then retries the same signed event with the same ID. A timestamp freshness window blocks old requests; a unique event ID blocks duplicate effects within that window.

For a PostgreSQL-backed receiving queue, create an application-owned table, separate from n8n's internal tables:

```sql
CREATE TABLE freecollab_incoming (
  event_id text PRIMARY KEY,
  payload jsonb NOT NULL,
  status text NOT NULL DEFAULT 'pending',
  received_at timestamptz NOT NULL DEFAULT now()
);
```

After successful verification, use a Postgres node with bound query parameters:

```sql
INSERT INTO freecollab_incoming (event_id, payload)
VALUES ($1, $2::jsonb)
ON CONFLICT (event_id) DO NOTHING;
```

Bind `$1` to the verified `event.id` and `$2` to `JSON.stringify(event)`. Reserializing here is fine because signature verification has already used the raw bytes. Never build SQL by interpolating event strings. Return 2xx only after the insert commits; duplicates may also receive 2xx because their original event is already durable. Configure the insert node to continue to the response even when no row is inserted.

A separate scheduled workflow reads pending events, performs the intended action with `event_id` as its idempotency key where supported, and marks it complete afterward. Failed processing remains pending for a controlled retry. Marking an ID “processed” before its effect happens would lose work after a crash.

For a controlled secret rotation, pause the webhook, rotate its secret in FreeCollab, install the returned secret at the receiver, then enable the webhook again. Choose a quiet cutover window: paused configurations do not capture activity, and old queued work is cancelled rather than replayed under the new secret. A receiver with an approved brief old/new-key overlap can support a coordinated active cutover; remove the old key after it ends. Inspect failed deliveries and retry applicable current-generation events explicitly after receiver configuration is corrected.

See [WEBHOOKS.md](../../docs/WEBHOOKS.md) for exact headers, delivery limits, routing disclosure and channel-grant rules.
