# AI in the conversation and work surfaces

The demo and installed application use the same authoring, reading and AI components. A public demo without a configured provider shows the configuration state. It does not manufacture AI responses or borrow a product operator’s API key.

## Writing and reading

`CollaborationInput` offers **AI help** in an authenticated, writable collaboration scope. This covers the product’s opted-in message, document, feed, decision, task, calendar, workflow and comment fields through the shared input. Sensitive inputs and fields with `allowAI={false}` do not gain AI access. The AI request, profile settings, read-tool query and semantic search fields disable their own AI button to prevent nested assistants.

The writer captures the actual field, account, token, provider scope, current value, references and selection. A selected passage is replaced in place; text outside the selection remains unchanged. Unchanged `@` bindings move with that text, and edited bindings are discarded. A response is a draft: **Use in this field** changes only the current draft. The ordinary Save/Send action publishes it. Changing the draft, replacing the field, changing account or retiring the collaboration lease prevents an old result from applying. Slash and mention palettes retain their existing keyboard, composition, focus and submission guards.

The read helper (`ReviewedResourceCopilot`) supplies summaries, open questions and suggested next steps for the current resource. It is mounted on feed details, decisions, linked-document details, channel calendar events, verified file text/current PDF page, clips, workflow run details and the encrypted/federated work readers. Documents and document stacks retain their existing contextual AI actions. A writing button alone is not treated as a read integration.

A long writer or reader requires a user-selected passage of at most 2,800 UTF-16 units. Reader passages omit the full-resource reference, so choosing an excerpt cannot also disclose the rest through a source reference. The UI displays the selected count and never silently trims that passage. Files contribute text only after passive text/Office decoding or successful PDF text-layer extraction; a PDF whose optional text layer fails remains previewable without AI text. Images and unavailable transcripts do not become invented text sources.

The contextual card shows the actual endpoint, model, selected source text and explicit provider disclosure. A named model can be chosen directly in this card. Native work can optionally become a persistent private AI job. Encrypted work stays in the foreground and requires a separate acknowledgment that selected decrypted copies leave end-to-end encryption.

## Models, requests and model plans

**Models and AI work** lives in the normal Copilot work surface and connection setup. It provides:

- Named OpenAI, xAI/Grok, Anthropic, Azure OpenAI, Gemini and Ollama profiles, exact endpoint/model selection, per-model instructions and Azure API version.
- Owner reauthentication for shared profile/key and budget changes; revision-bound per-person consent; optional encrypted personal API keys for a profile. Saved keys are never returned to a form.
- Monthly token and estimated USD limits, reservations, usage, per-profile prices and legacy/default model prices. A zero monetary cap means no local monetary cap. Provider billing remains separate.
- Registered bounded actions: draft, summarize, task plan, content grouping, comparison and answering. There is no silent model fallback or arbitrary code/URL execution.
- One to three explicit model steps. **Suggest an AI plan** makes a real model call using the reviewed request and allowed model names/IDs/models. The proposed plan does not execute until the user reviews every step and recipient and gives a new disclosure acknowledgment.
- Run status, real steps/attempts/usage, cancellation of future work, explicitly acknowledged resume after an uncertain charge, private-history removal and a shared status/usage projection of the existing Copilot, app-draft, recap, workflow and app-command engines.

Native jobs use the separate AI runtime worker and survive a closed panel. The UI says this before queueing. Secure multi-model work runs one foreground call at a time, hands off the prior output only within this reviewed plan, rechecks the actual sources and recipients before every call, and stops future calls when the scope changes, the user stops it or the local deadline is reached. It does not create a server plaintext queue. Information already sent to a recipient cannot be retracted.

A failed foreground/model-planner request can already have been processed and charged. The UI blocks a new attempt until the user acknowledges that risk and then reviews disclosure again. A queued native request retains its idempotent request ID instead of blindly creating another job. Task proposals and content organization remain separate reviewed writes.

## Retrieval, connected reads and grouping

**Retrieve context by meaning** uses the installation’s native semantic index or the device’s encrypted local index. It searches the current conversation by default. Workspace retrieval is an explicit option; selected results from another channel need an additional disclosure acknowledgment. Search itself does not contact the selected AI provider. Results display their conversation, title and reviewable text; only checked results enter the request.

Source references, current access, original channel/workspace, versions and exact text are rechecked before disclosure. Native durable RAG stores server-derived sealed canonical descriptors, so a normal short-lived public search capability expiring does not break a queued job. Revoked or changed sources still fail. Secure retrieval remains device-local; explicit `channel:kind:id` catalog entries describe only those device-verified sources, grant no server access and never enable a persistent plaintext job.

**Web search and connected reads** is a separate reviewed recipient. An owner registers the fixed JSON search/read endpoint and response schema. A query requires its own disclosure acknowledgment before a bounded real HTTPS read. Returned excerpts are reviewable and remain local until the user selects them for an AI request and then reviews the model recipient. A limited upstream preview is labeled. The model does not choose arbitrary destinations. Private read origins require explicit operator configuration; see [AI_RUNTIME.md](AI_RUNTIME.md).

A grouping result contains only IDs from the reviewed selection. **Review as a collection** resolves the real canonical resource kinds and current parents, and previews the actual members. **Create this collection** checks source access/text versions again, compares current catalog version fingerprints and uses the normal typed collection API. Supported kinds are document, file, linked document, task, feed, message, project, decision, calendar, clip and workflow. Duplicate, ambiguous, missing or invented IDs fail. No suggestion automatically moves or edits content.

## Verification and boundaries

Focused frontend tests cover UTF-16 selections, reference preservation/removal, exact profile routing, bounded model-plan catalogs, real typed group members, CAS API payloads, exact file/transcript sources, source leases and possible-charge retries. Existing contextual assistant and slash/mention tests retain their prior assertions.

`frontend/tests/ai-surface-browser.py` runs the actual product components in an explicit private collaboration host against a disposable real API/SQLite database, an independent runtime worker, the real semantic index/collection APIs and owned TLS model/read endpoints. No model or product API response is mocked. Its JSON report records source hashes and states its scope. Full Chat navigation, a deployed Matrix federation and customer provider credentials are separate integration boundaries; this proof does not claim to exercise them.

Runtime/backend contracts and deployment details are documented in [AI_RUNTIME.md](AI_RUNTIME.md), [AI_ORCHESTRATION.md](AI_ORCHESTRATION.md) and [COPILOT.md](COPILOT.md).
