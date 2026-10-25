# AI runtime and model profiles

The normal product offers real model-profile configuration, reviewed runs/plans,
current progress, usage and registered search/read tools. Workspace owners
configure destinations; each member opts into the exact profiles they use.

## Operator startup

Run this independent worker beside the API and integration worker:

```sh
python -m app.services.ai_runtime_worker
```

It uses normal database/encryption configuration and records the `ai-runtime`
heartbeat. Options are `--once`, `--interval 1..60` and `--batch 1..5` (default2).
`COPILOT_ENABLED=true` is required. Public demo mode never executes external AI.
Tables are additive; no plaintext migration or existing-column relabeling is
used. An interrupted provider attempt requires review before retry.

Docker Compose and the Railway runtime start the independent AI lane alongside
the API, mail worker and integration worker. Railway starts all three workers
after API readiness and restarts the service if any child exits. AI remains
inactive until enabled and configured; public demo mode never calls providers.

Cold backup stops and records the AI worker with the other writers. Restore
keeps it stopped by default and pins it to the backed-up backend image.
`--resume-ai` resumes only a worker that was running in the source backup;
review queued/uncertain jobs, model grants, budgets and source activity first.
Two database copies cannot deduplicate provider charges across both workers.
Recurring AI recaps run in the integration worker and require its separate
`--resume-integrations` review; `--resume-ai` controls the durable-run lane.
See [backup and restore operations](OPERATIONS.md).

## Native profiles

The legacy OpenAI-compatible settings remain the `default` profile. Up to16
named profiles select independent endpoints, protocols, models and instructions.
Keys may be workspace-owned or personal to a user/profile/revision. APIs never
return key values; profile changes invalidate consent and old personal-key
bindings. Queued work also binds the actual credential fingerprint.

| Protocol | Complete endpoint | Authentication |
| --- | --- | --- |
| OpenAI-compatible | `https://api.openai.com/v1/chat/completions` | Bearer |
| xAI | `https://api.x.ai/v1/chat/completions` | Bearer |
| Anthropic | `https://api.anthropic.com/v1/messages` | x-api-key and anthropic-version2023-06-01 |
| Azure | `https://YOUR-RESOURCE.openai.azure.com/openai/deployments/YOUR-DEPLOYMENT/chat/completions` | api-key; separate api_version appended by transport |
| Gemini | `https://generativelanguage.googleapis.com/v1beta/models/YOUR-MODEL:generateContent` | x-goog-api-key |
| Ollama | Exact `/api/chat` endpoint | Optional configured authorization |

Choose a model/deployment available in your own provider account. Protocol
support does not provide a provider subscription or guarantee model availability.
Azure's default API version is2024-10-21; keep credentials out of endpoint URLs.

Public HTTPS443 is the default. Operators can opt into exact private origins:

```sh
FREECOLLAB_AI_PRIVATE_ORIGINS='["http://localhost:11434"]'
FREECOLLAB_AI_TOOL_PRIVATE_ORIGINS='["https://search.internal.example:8443"]'
```

The model allowlist applies to all six native protocols, including compatible
private vLLM/OpenAI servers. An explicitly approved local endpoint may omit an
API key. Plain HTTP remains restricted to approved private destinations.

These operator settings do not bypass network policy, the inherited proxy or TLS
verification. Metadata/link-local, multicast and unspecified addresses remain
forbidden. Users cannot set an origin allowlist in a request.

## API

All paths below start with `/workspaces/{id}/copilot/runtime`, require normal
current account access and inherit private no-store responses. Owner settings
require fresh password/MFA and expected revisions.

| Method/path | Actual behavior |
| --- | --- |
| GET `/` | Profiles, consent/configured flags, monthly budget and20 private runs |
| PUT `/profiles/{id}` | Named endpoint/protocol/model/instructions, key source, token cap and estimated prices |
| POST `/profiles/{id}/consent` | Opt in/out of the exact revision |
| PUT/DELETE `/personal-keys/{id}` | Reauthenticated personal-key storage/removal |
| PUT `/budget` | Monthly token allowance and optional estimated money cap |
| POST `/plans` | One disclosed planning call; validate up to3 proposed steps; execute nothing |
| POST `/runs` | Idempotent native queued run from reviewed sources/writing/plan |
| GET `/runs/{id}` | Actual steps, usage, private result and fresh authorized citations |
| POST `/runs/{id}/cancel` | Fence future steps and stale answers |
| POST `/runs/{id}/resume` | Reauthorize and acknowledge charged/uncertain earlier attempts |
| POST `/runs/{id}/approve` | Accept result or approve an exact transactional task proposal |
| DELETE `/runs/{id}` | Remove stopped/finished history without refunding usage |
| POST `/complete` | One profile-aware foreground completion; secure text stays ephemeral |
| GET `/executions` | Shared actual run/step metadata across existing engines |
| GET `/tools`, PUT `/tools/{id}` | List/configure registered search/read destinations and JSON mapping |
| POST `/tools/{id}/read` | Separate revision-bound query disclosure and actual ephemeral results |

GET `/tools` lists registered tools; PUT uses the named tool ID. Default profile
settings retain their legacy API. Names/IDs are stable, bounded, owner-configured
identities; a model cannot invent another recipient.

A run includes `request_id`, `channel_id`, `profile_id`,
`expected_profile_revision`, operation, prompt, selected sources/writing,
`max_steps`1–3, `max_tokens`256–8192 and `disclose_to_provider:true`. Source text
plus selected writing is capped at24,000 characters. Optional `plan` contains
explicit per-step operation/profile/revision/instruction; its first step matches
the primary selection. The planner accepts an explicit `allowed_profiles`
catalog and returns a proposal that still needs separate user review/start.

Native search results become `{kind:"resource",id,reference}` input. A fresh
actor-bound capability is validated once, then replaced by a sealed canonical
record descriptor for durable reauthorization. Other native channels need
`disclose_cross_channel_sources:true`; another workspace is never included.
Queue creation fences the workspace and all selected source channels in stable
order and rechecks current source versions before retaining its sealed snapshot.
Secure foreground input additionally needs `disclose_decrypted_content:true`.
A secure trusted-device catalog can constrain groups through
`reviewed_source_ids`; it is not a server grant or a claim that the server can
verify encrypted plaintext. Native queued work rejects device-only catalogs.

Foreground/planner request IDs are attempt receipts: repeating one returns409
instead of silently billing another call. An identical queued request returns
its existing run; different content with the same identifier returns409.

## Search/read tools

Registered web search uses an exact JSON endpoint, GET or search POST, a query
field, bounded result path and configured title/URL/text fields. The native
adapter returns actual citation URLs/snippets. Generic HTTP reads use GET and an
explicit JSON path. Every call shows this recipient separately from the model,
requires `disclose_to_tool:true`, current revision and query, and returns at most
12,000 characters. Secure-derived queries need their own decrypted-content
acknowledgment. Results are not automatically added to a model request or stored
in history. Native queries and returned text follow workspace assistant content
policy. Reflected configured secrets are redacted; `truncated` explicitly marks
bounded results. Arbitrary side effects
belong to the separately approved app-command outbox.

## Budgets and lifecycle

The token allowance is positive; monetary cap0 disables the local monetary cap.
Prices are integer USD microdollars per million input/output tokens. A billed
profile needs configured prices when a monetary cap is enabled.
Default-profile prices can be set in the same budget form for legacy Copilot,
app drafts and recaps. Each call first
reserves a conservative bound. Reported usage settles it; absent/uncertain usage
consumes the reservation. A retry may incur another charge and is not automatic.
These are local estimates/controls, not provider invoices or exactly-once billing;
provider-side limits remain necessary for an external spending guarantee.

Private-history removal retains numeric usage. Account/channel erasure deletes
scoped private runs/steps/results and anonymizes numeric receipts without
committing the caller's transaction. Account erasure also removes personal keys
and profile consent. An in-flight reservation
becomes conservative unknown usage; a late answer cannot resurrect deleted work.
Shared profile/budget policy retains no deleted editor identity. Workspace erase
removes its policy and private work while retaining anonymous numeric financial
receipts with no workspace, user, model profile or original execution reference.
Own-data export rechecks source access and never
exports keys or raw authority credentials.

See [AI_ORCHESTRATION.md](AI_ORCHESTRATION.md) for disclosure, execution and
recording-summary boundaries, [AI_SURFACES.md](AI_SURFACES.md) for the actual
writer/read/review controls, and [runtime tests](../backend/tests/test_ai_runtime.py)
for actual API/worker/protocol coverage.
