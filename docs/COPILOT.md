# Production AI Copilot

The product also includes [native model profiles and durable reviewed runs](AI_RUNTIME.md):
multiple model recipients, personal encrypted keys, bounded plans/handoffs,
usage budgets, registered web search/HTTP reads and a shared execution timeline.
The original foreground/conversation API below remains compatible; its calls
share the same budget ledger. See [orchestration](AI_ORCHESTRATION.md) for the
native queue and encrypted foreground boundaries and [AI surfaces](AI_SURFACES.md)
for the writer, reader and reviewed execution controls.

Production assistance is an opt-in integration. The public [product demo](PRODUCT_DEMO.md) uses the same components and API with temporary sample accounts, but its AI provider is disabled; it does not fabricate model replies. It has no provider key by default and makes no AI request merely by opening its controls. An installation operator must set `COPILOT_ENABLED=true`, a personal workspace owner must configure a provider with fresh password/MFA, and each member must explicitly opt in to that exact provider configuration. Changes to the destination, key, model or agents invalidate earlier consent.

The earlier anonymous Groq demo endpoints have been retired. `ENABLE_DEMO_AI` is
an ignored compatibility setting: neither `/demo/chat` nor `/api/demo/chat` becomes
available when it is true. The product demo has a separate temporary sample backend and no AI provider;
production inference uses the authenticated, reviewed Copilot or Apps & automation paths.

## Configure a provider and agents

Open **Workspace tools → AI Copilot → Provider and agent settings**. Enter the complete public HTTPS443 OpenAI-compatible **chat completions** endpoint, model and your API key. For example, `https://api.openai.com/v1/chat/completions` with a compatible model such as `gpt-4o-mini`. Set the desired workspace enable flag and save with the owner's personal password and current authenticator/recovery code if enabled. Existing keys are never returned; leaving the key blank retains the current key only for the same destination endpoint. Changing the endpoint requires a new explicitly supplied key so an existing hidden key cannot be forwarded to another destination. Provider URLs must have no user information, fragments or query credentials. Arbitrary ports, loopback/private addresses and private DNS answers are refused.

The legacy foreground endpoint implements OpenAI chat-completions with `stream:true`, `max_tokens:2048`, one choice and optional function tools. Its streaming parser accepts xAI's intermediate `end_turn` marker without treating it as completion: a final `stop` or `tool_calls` and `[DONE]` remain required. Named model profiles additionally implement native Anthropic, Azure, Gemini and Ollama protocols; configure them through the [runtime](AI_RUNTIME.md), including reviewed API version and personal/workspace credential source.

The connection guide includes **Grok · xAI**, filling `https://api.x.ai/v1/chat/completions` and `grok-4.20-0309-non-reasoning`. The model remains editable. This dated, non-reasoning model is documented by xAI with function calling and fits the existing bounded request contract; choosing a reasoning model does not extend FreeCollab's thirty-second transport deadline or 2,048-token request limit. See xAI's [model documentation](https://docs.x.ai/developers/models/grok-4.20-0309-non-reasoning) and [Chat Completions reference](https://docs.x.ai/developers/rest-api-reference/inference/chat-completions). An xAI API account/key is required; a Grok chat subscription is separate. Selecting the preset only edits the unsaved draft and clears typed destination credentials. The actual saved xAI hostname/model appear in the same request warnings after configuration and member opt-in. This is another choice for the workspace's single provider, with no automatic provider switching. Compatibility is based on the documented protocol and private synthetic-provider tests, without a live xAI API acceptance test.

Add up to eight named agents with bounded instructions and optional fixed tools. Address one as `@name` or choose it in the composer. The only tools are `context_sources`, which lists the explicitly selected citation identifiers, and `create_task`, which proposes a task. Tools cannot fetch arbitrary context, run code, make arbitrary HTTP calls or execute unapproved writes. Choose context to load authorized native projects, then explicitly select a task destination. The provider receives only that project ID in the fixed tool schema; the task tool is unavailable until a destination is selected and proposals for any other project are rejected. A task proposal requires the user to click **Approve and create task**; current personal workspace administration, native channel administration, source access and project validity are checked again. Approval is transactional and idempotent. Secure channels never receive the native task-creation tool.

## Quick actions and request warnings

**Summarise selection**, **Suggest next steps**, **Draft a reply** and **Propose a task** prepare a grounded instruction in the existing question field. They keep any text already written, keep the selected sources and disclosure choices, and make no provider request. Repeating the same final instruction does not append it twice. If the combined draft would exceed the composer limit, the draft stays unchanged and the panel asks the user to shorten it. Review the draft, then use the existing **Send to provider** action.

**Propose a task** can return a cited text draft when no task tool is available. Real native task creation still requires the configured tool, an explicitly selected authorized project, current grants and the separate **Approve and create task** action. Secure channels do not gain a native task-creation tool.

Before sending, an inline notice shows the saved provider hostname, configured model and selected-source count, and explains prompt/history disclosure, possible provider API charges, retention terms and output review. The notice uses saved configuration and never displays an API key or credential-bearing URL. A chat subscription is not an API account. Named runtime profiles support personal keys, native provider protocols and explicit recipients for every planned step; old configuration does not silently opt members into those destinations.

The existing per-request provider disclosure and additional secure-content acknowledgment remain required. Quick actions do not check those boxes, select extra sources, send a prompt or approve a task.

## In-place assistance

Authenticated conversations offer concrete actions beside the relevant content.
A message's **More message actions** opens **Draft a reply**, **Summarize this
message** or, when actual native documents are available, **Find related
documents**. Threads offer **Summarize replies / Draft a reply** for loaded
sources. **Message options** offers tidy/shorten actions for a nonempty main
draft, and a nonempty thread draft offers tidy. The channel menu can review a
recap or possible next steps from loaded conversation sources.

Shared documents offer a reviewed append through their existing synchronized
editor. Project/task editors use suggestions for the unsaved description. A
generated answer never posts a message, saves a task or edits a document
automatically: the relevant **Add reply to draft**, **Replace my draft** or
**Append to document** action requires a separate click and checks the current
target again. Appending a document is a synchronized edit, not an unsaved private
draft.

Related-document comparison lets the person choose actual authorized native
documents, starting with one. Only the selected document identities and current
message sources enter source review; source verification resolves their actual
text before sending. The shared eight-source and text budgets remain enforced,
without silently trimming verified text. This is deliberate comparison of
selected sources, not global semantic retrieval or automatic linking.

Opening an action only opens review; it grants no consent and sends or applies
nothing. Clearing a draft, closing its thread or changing identity invalidates
that activation. New typing cannot reopen a dismissed review, and an older
close frame cannot take focus from a newer review or deliberately focused field.
See [the everyday flow contract](FLOW_FIRST_UX.md).

Each inline request reviews its exact prompt and selected sources before sending, uses the workspace's saved provider and member consent, and requires the same per-request and secure-content acknowledgments. The component rechecks provider revision, current access, selected source text/revisions and target snapshot; changed context invalidates an answer instead of replacing newer work. It creates a separate private conversation only on explicit Send, forwards no central Copilot history and clears that owned request history after completion. Inline suggestions do not expose native write tools or approve proposals. The central Copilot remains available for deliberate longer conversations and separately approved task creation.

Inline review counts the selected context before Send, including citation/kind
headers and source separators. If it exceeds 24,000 Unicode characters, Send is
disabled and the user is guided to choose fewer sources. Prepared document text
and message bodies stay intact; this guard never silently truncates a source.

Native document stacks also offer **Review this stack with AI**. Choose up to eight
current pages, then review their verified excerpts in the same inline component.
Summary, reading-order and open-question actions return read-only cited text.
Only checked pages are sources: unselected pages, grouped comments and saved
conversation references are not silently disclosed. Stack AI never reorders,
regroups or edits the native material automatically.

### Direct HTTP requests

`POST /workspaces/{workspace_id}/copilot/conversations/{conversation_id}/turns` requires `expected_config_revision`, a strict positive integer from the provider configuration the user reviewed in `GET /workspaces/{workspace_id}/copilot`. This is separate from the conversation's `expected_revision`. A changed configuration returns HTTP409 before allocating a turn or contacting a provider, including when another tab has already opted in to the new revision. Reload and review the actual recipient/model before retrying; never silently replace the reviewed revision with a newly fetched value.

## Public interactive preview

The public [product demo](PRODUCT_DEMO.md) mounts the same `Chat`, `WorkHub`,
`ContextualCopilot` and settings components used by an installation. Its real
API stores temporary example content. AI remains disabled/unconfigured in that
public environment, so the normal controls show that state and no provider call
or invented answer is presented. The separate template/extractive demo recipes
have been retired. A customer installation can explicitly configure its own
provider and use the reviewed inference flow described here. Native sample
content and browser interaction do not establish live AI-provider acceptance.

## Explicit context and citations

Choose **Choose messages, documents and files**, then check only the sources you intend to disclose. Native message content is read only for those selected IDs. The current client sends an `expected_text_sha256` for each reviewed native message; the server compares it with the authorized current text and returns HTTP409 before contacting the provider if it changed. Direct HTTP callers should send the SHA256 of the exact UTF-8 message text they reviewed in this source field. Text/JSON files must be UTF-8 and at most 12 KB; immutable file identities and current file availability are checked. Shared documents are reconstructed and verified by the trusted Yjs client, with up to 12,000 document characters bound to the current authorized document version. Each request is limited to eight sources and 24,000 context characters. There is no hidden index, background summarizer, automatic channel history ingestion or provider-initiated read tool.

Review the provider destination and consent text before sending. The prompt, selected context and bounded visible native AI history are disclosed to that provider. Its region, retention, training and privacy terms apply. Recheck factual answers against the `[S1]` citations. Citation buttons revalidate current permissions and then open the current source; removed or changed sources make old conversation history unavailable until reset. A model answer cannot create a citation outside the selected source set.

Provider calls use freshly resolved public IPs, pinned connections, the original TLS/Host identity, the inherited proxy/CA, no redirects or direct fallback, a thirty-second absolute deadline and a 256 KB response bound. The strict SSE parser handles arbitrary UTF-8/TCP splits, bounded frames and fragmented function calls; incomplete streams and invalid citations/tools do not publish a partial answer. After provider networking, actor/session/provider/consent/channel grants and every source are checked again before publishing. Revocation cannot undo data already explicitly sent to an external provider.

## End-to-end encrypted channels

Matrix content is decrypted **only in a trusted client**. Each secure request additionally requires an explicit disclosure acknowledgment: selected decrypted material leaves end-to-end encryption when the client sends it through the server relay to the AI provider. The relay validates opaque event references and current channel/document grants; it never obtains a device encryption key or builds a server-side plaintext index.

Secure prompts and answers remain only in the open panel's memory, are cleared on logout, channel/account change or panel close, and are not included automatically in subsequent requests. The server persists only opaque source references, keyed request digests and status/count metadata for secure turns. There is no encrypted-result blob containing secure prompt or answer plaintext. Providers retain their own separately disclosed copies under their policies.

## History, audit and cleanup

Native private conversation prompts/results are encrypted at rest and visible only to their requesting user with current channel/source permissions. Workspace owners do not receive other members' private AI transcripts. A conversation is limited to twelve completed turns and twenty-four total attempts, a user to twenty conversations, and forwarded prior exchanges to 24,000 characters. Each completion is capped at 2,048 requested tokens and 16,000 returned characters. Rate limits bound new conversations and provider turns. **Reset history** deletes the conversation, turns and approval receipts; tasks already explicitly created remain shared workspace work. Logout clears browser memory. Provider keys/configurations stay outside exports and UI responses.

The dedicated activity table contains only an opaque actor/workspace ID, fixed action name, source count and time, with a thirty-day/1,000-row workspace bound. Prompts, keys, provider bodies and document text are not logged. Installation logging and external provider retention remain operator responsibilities.

## Verification

`cd backend && .venv/bin/python -m pytest tests/test_copilot.py -q` runs a private HTTPS server with an ephemeral CA and synthetic key through the real pinned transport and SSE parser. It covers selected-only context, citations, encrypted private history/reset, real approved/idempotent task creation, truncated streams, post-network revocation, private-channel and scoped configuration denial, secure explicit client relay without stored plaintext, SSRF/query-key rejection and Unicode framing. Production network policy is changed only by exact private-fixture helper replacements; no runtime bypass is exposed.

The Copilot browser proof (`frontend/tests/copilot-browser.py`) uses a frozen frontend snapshot, disposable loopback API/Vite/Chromium services and synthetic accounts. It first displays **AI configuration required** with no key and verifies zero provider calls. It then configures a real private HTTPS/SSE fixture provider with a synthetic key, exercises named agents, opt-in, explicitly selected context and task destination, citations, approval/idempotency and history reset. Quick-action checks preserve the exact typed draft, selected context and unchecked disclosure without making a provider call, verify the actual recipient/model/source-count and API/privacy warnings, and refuse overflow without truncation. Actual Chat integration checks review a selected message, invalidate an answer when the draft changes, append a reply only on approval, and rewrite only the reviewed unsent draft without native source references. They verify zero message posts, zero unapproved task writes and cleanup of the owned private AI conversations. A real stack group creates verified Yjs pages and a native stack through the UI, deselects a page, and sends only the checked page to the private TLS provider. Unselected pages, grouped comments and conversation text stay out; no document or stack write follows the answer. The proof verifies saved Swedish preference and mobile layouts at 390px and 320px, including 44px quick-action targets, and records source hashes. No real AI provider credentials, production data or external provider calls are used.
