# AI orchestration

FreeCollab contains a bounded execution layer in the normal product: reviewed
model profiles, native durable runs/steps, cancellation/resume, worker recovery,
multiple-model handoffs, actual usage and shared budgets. See the runnable
[AI runtime contract](AI_RUNTIME.md), [API](../backend/app/api/ai_runtime.py),
[worker](../backend/app/services/ai_runtime_worker.py),
[state models](../backend/app/models/ai_runtime.py) and
[provider protocols](../backend/app/services/ai_providers.py).
The actual writer, source selection, planner and review UI is documented in
[AI surfaces](AI_SURFACES.md).

## Reviewed execution

A user selects real sources or writing, an operation, actual model recipients
and their revisions. Disclosure identifies each endpoint/model and possible API
charges. A bounded planner can propose up to three steps from the reviewed model
catalog; the proposal executes nothing until separately reviewed and started.
Each step explicitly selects its operation, model profile and instruction. The
previous result is bounded handoff context. A changed profile never silently
routes work to another provider.

Operations are draft, summarize, answer, compare, group and task_plan. Grouping
returns proposals containing only selected IDs; normal collection/document
writes remain separately reviewed. Task proposals use the selected native
project and deterministic transactional approval receipts. A model cannot choose
an arbitrary URL, run code or execute an unapproved write.

The worker claims one step, reserves its conservative token/cost allowance and
commits before networking. Current session authority, membership, profile/key,
consent, original source version and content policy are checked before and after
calls. Claim tokens/generations reject late answers after cancellation. Native
run content is purpose-encrypted; stored authority contains verified claim
metadata and a session digest, never a raw bearer token.

A lease lasts180seconds; a transport call has a30second deadline. There are at
most three steps and two explicitly reviewed attempts per step. An expired
processing lease becomes failed/uncertain and is not automatically billed again.
Resume reauthorizes the current sources and recipients and requires acknowledgment
when a previous attempt may have incurred a charge. Cancellation stops future
steps and stale publication; it cannot recall data already disclosed.

## One current execution view

The authenticated `/copilot/runtime/executions` read model combines actual states
from native runs, foreground Copilot turns, private app-AI drafts, recurring
recaps, workflows and app-command outbox. It includes permission-checked run/step
metadata, source-engine identifiers and numeric usage, without decrypting source
or answer text merely to browse. Domain engines retain their real transactional
claims and write approvals; the projection does not fabricate runs or pretend
one engine executed another engine's work. Existing foreground Copilot and app
AI/recap calls use the same usage reservations as named runtime profiles.

## Sources, RAG and registered reads

Semantic retrieval returns actual authorized native records. The user reviews
and selects them before disclosure. Sources in other channels require an
additional cross-channel acknowledgment and remain within the same workspace;
their original ACL, version and content policy are rechecked. The server never
indexes decrypted end-to-end-encrypted content.

Public search references retain their ten-minute lifetime. A queued run derives
and seals a canonical resource descriptor/version from a fresh reference, then
reauthorizes that actual record during recovery. It does not depend on keeping
an expired capability alive or on silently retaining revoked access.

Registered JSON web search and HTTP-read tools use a separate recipient warning,
current tool revision and explicit query disclosure. Owners configure exact
endpoints and response schemas. Search returns actual bounded citations and
excerpts; generic HTTP reads use GET. Model-selected URLs, arbitrary code and
unreviewed HTTP writes are not executable operations. Results remain ephemeral
until the user explicitly selects them for a later model request.

## Models, credentials and budgets

Named profiles implement native OpenAI-compatible/xAI, Anthropic Messages, Azure
Chat Completions, Gemini GenerateContent and Ollama Chat. Profiles have their own
instructions, model, endpoint and revision. A user's personal key is sealed for
that user/profile/revision and never returned. Multiple profiles may run
concurrently and may participate in one reviewed plan.

Public HTTPS443 is the default. Private model and registered read destinations
require exact operator-owned origin allowlists. Fresh DNS/IP pinning, inherited
proxy settings, normal TLS, bounded bodies and no redirects/failover/retries
remain in force. Metadata/link-local, multicast and unspecified destinations are
forbidden even when an origin is mistakenly listed.

Monthly budgets reserve a conservative UTF-8 input/output bound before sending.
Actual reported usage settles it; absent or uncertain usage consumes the
reservation and remains marked unknown. Monetary prices/caps are estimates in
USD microdollars, not provider invoices or exactly-once billing guarantees.
Private-history removal cannot refund usage. Account/channel erasure removes
private job content and anonymizes numeric usage so an in-flight call cannot
resurrect content or silently replenish the workspace budget. Provider-side
controls are still needed for a contractual external spending ceiling.

## Encrypted content and recording consent

Secure AI is an explicitly disclosed foreground exchange from the trusted
device. Responses and reviewed step handoffs stay in device memory. No secure
plaintext enters the native queue, retries, shared history or server index.
Numeric usage contains no prompt/source/answer. Device-only source catalogs
constrain grouping output, but do not claim server verification of encrypted
record contents; the trusted device rechecks each actual source before every
step and apply operation. Closing/changing scope stops subsequent device steps.

Native recording summaries may use a captured future-source grant. Recording
generation, actual profile/provider, participant consent, bounded segment/expiry
and the immutable clip are validated before enqueue and around execution. A
server-derived request ID permits one job per recording generation/clip. Revoked
consent cannot authorize a later provider call. This does not permit background
decryption of secure media.

## Verification

[Runtime regressions](../backend/tests/test_ai_runtime.py) exercise actual
API/SQL claims, delayed calls, cancellation/revocation/source changes, budgets,
uncertain recovery, idempotent approval, canonical RAG recovery, registered-read
schema/recipient changes and ephemeral secure completion. Six owned HTTPS
fixtures exercise native protocol headers, JSON framing and usage with normal
certificate verification. These test the implementation contract; customer
provider credentials, model availability and billing remain live configuration
and acceptance responsibilities.
