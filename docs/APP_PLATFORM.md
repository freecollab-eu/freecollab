# Workspace apps and reviewed AI automation

FreeCollab includes a small installable app platform, not a claim to reproduce
another product's app catalog. In **Integrations → Apps**, a personal workspace
administrator who also administers the selected native channels can install a
manifest, review exact scopes and channel grants, and supply the organization's
own HTTPS endpoint and optional authorization. **Work → Apps & automation** is
available to current channel members for installed commands, app cards and private
AI drafts. Customers continue using their own identity provider and provider apps.

## Seven working setup recipes

| Recipe | Actual outgoing protocol | Customer configuration |
| --- | --- | --- |
| Signed app / n8n | Signed JSON `app.command` POST | Your public HTTPS webhook and installation signing secret |
| GitHub issues | `POST /repos/{owner}/{repo}/issues` with title/body, GitHub API version header | Repository endpoint and your GitHub App installation token or fine-grained token with Issues write |
| Jira Cloud issues | `POST /rest/api/3/issue`, project key, summary, Task issue type, Atlassian Document Format description | Tenant endpoint and your own scoped OAuth gateway or Basic base64(email:API-token) |
| Zendesk tickets | `POST /api/v2/tickets.json` with ticket subject and comment body | Tenant endpoint and Bearer OAuth token or Basic base64(email/token:API-token) |
| Salesforce cases | `POST /services/data/v61.0/sobjects/Case` with Subject/Description | Organization instance endpoint and customer-owned Connected App OAuth token |
| Google Calendar | Fixed `POST /calendar/v3/calendars/primary/events` with reviewed title/start/end, no attendees | Customer-owned OAuth registration plus the invoking person's calendar-action consent |
| Microsoft Calendar | Fixed `POST /v1.0/me/events` with reviewed title/start/end, no attendees | Customer-owned exact Entra tenant registration plus the invoking person's calendar-action consent |

These are explicit create-action recipes. Google/Microsoft use the separate
[personal OAuth setup](PERSONAL_OAUTH.md) with encrypted refresh and revocation;
installation alone never grants another person's provider account. The other
recipes keep their existing customer-managed token/gateway lifecycle. Recipes do
not subscribe to provider webhooks, synchronize all records, or claim a provider
was connected merely because configuration was saved. A
customer gateway can own refresh, conditional logic and additional providers.
Existing Google/Microsoft cloud synchronization, calendar, document, SSO and
metadata-only n8n/webhook connections remain separate and compose with this API.
No customer provider credentials are requested by FreeCollab's publisher.

## Manifest and installation contract

`GET /workspaces/{workspace}/apps/catalog` returns all seven recipe manifests and
setup notes. A custom app uses the same `POST /workspaces/{workspace}/apps` API:

```json
{
  "manifest": {
    "schema_version": 1,
    "app_id": "example.delivery",
    "name": "Delivery helper",
    "version": "1.0.0",
    "description": "Run a reviewed request in our own environment.",
    "scopes": ["commands:invoke", "cards:write"],
    "commands": [{
      "name": "delivery",
      "description": "Send our helper the reviewed text.",
      "protocol": "signed-webhook",
      "arguments": [{"name": "text", "label": "Text", "required": true, "max_length": 2000}]
    }]
  },
  "channel_ids": ["explicit-native-channel-id"],
  "endpoint": "https://customer-app.example.com/freecollab",
  "authorization": "",
  "expected_generation": 0
}
```

The response returns a `bot_id`, installation ID, generation, signing secret
**once**, and callback path. Bots are installation identities; they have no user
account, session, administrator JWT, channel-history read API, or person identity.
Provider credentials, destination URL, queued arguments, app-card bodies and
private AI results are sealed with the installation's existing account-security
key. Browser clients receive origin/manifest/grants only, never saved credentials.
Native retention collects old cards, private drafts and terminal app requests;
revoke active app grants and stop recap schedules before purging their channels.
Content-free callback replay tombstones remain. Identity erasure stops delegated
apps and recaps, strips credentials and private argument/draft copies, and keeps
shared authorship records subject to preservation holds. Authorized exports include
only the requester's own current private drafts, alongside shared native cards.

The three supported scopes are `commands:invoke`, `cards:write`, `actions:write`.
Direct provider writes require `actions:write`. Commands and their exact argument
names, requiredness and bounds are validated against the installed manifest.
An ordinary current channel member can invoke an installed command only for that
channel, with `confirm: true`, `disclose_to_app: true` and the exact reviewed
`expected_generation`; the request includes only
the reviewed arguments and bounded routing metadata. No history is included.

Arguments can additionally declare `type: "text" | "number" | "boolean" |
"choice"`. Choice arguments provide a bounded unique `choices` list. Number
arguments accept finite decimal/scientific notation; boolean values are the
literal strings `true` and `false`. The payload remains an object of strings,
preserving custom app receiver contracts. Unknown, duplicated or oversized
arguments are rejected before creating a job.

## Installed commands in every writing surface

The shared `/` catalogue includes currently granted installed commands alongside
the product's built-in tools. `/customer.app:command name="quoted text"` selects
an exact app namespace. A unique command name can also be used without its app
namespace; duplicate names require choosing the actual installation. Single-text
commands accept positional text. Parsing does not run shell expressions or code.

Selecting a command opens the ordinary retained review surface. Its arguments
can be edited, and editing clears approval. The actual recipient origin,
installation generation and argument schema are checked again before enqueue.
Opening or typing does not queue a request, disclose history or execute an app.
An unsent initial argument draft participates in the same scoped navigation guard
as other work. A lost queue response retries the same request ID and body; an
ambiguous external delivery remains a separate inspection step.

`GET /workspaces/{workspace}/channels/{channel}/apps/commands` returns the actual
granted command catalogue. `POST` to that path plus `/{installation}` requires
`request_id`, `expected_generation`, `command`, `arguments`, `confirm` and
`disclose_to_app`. The channel comes from the path and is reauthorized. The
original `/apps/{installation}/commands` write endpoint now also requires the
reviewed generation: callers must carry the generation returned when they showed
the installation to the user. Omitting it fails closed with HTTP422; a changed
generation returns409. Do not fetch a new generation silently at invocation and
use it to authorize an old recipient review. This is a security tightening for
existing API clients, not automatic approval of changed apps.

The [workflow builder](WORKFLOWS.md) can also grant a registered app command with
fixed reviewed arguments for future matching runs. These use the same outbox and
uncertain-delivery receipts; a signed-webhook app is the registered HTTP action,
not an arbitrary URL/code-execution step. [Continuous Slack bridges](SLACK_LIVEBRIDGE.md)
are separate customer-owned app connections with their own signed event ingress.

`PUT /workspaces/{workspace}/apps/{installation}` requires the expected current
generation and regrants the complete manifest, channels, destination and
credentials. Every update increments generation, saves an immutable manifest/grant
revision, rotates the signing secret and cancels previous queued requests.
`DELETE` revokes immediately, strips saved secrets and cancels queued requests.
Workspace admins can still revoke after losing a channel grant. Current grants
are rechecked after locking and again in the worker/callback paths.

## Signing, retry and card callbacks

The custom app receiver verifies these headers against the **exact request body**:

```text
X-FreeCollab-Timestamp: <integer Unix seconds>
X-FreeCollab-Signature: v1=<hex HMAC-SHA256(secret, timestamp + "." + exact_body)>
X-FreeCollab-Event-ID: <durable job id>
Idempotency-Key: <same durable job id>
```

Reject timestamps outside a five-minute window, compare MACs in constant time,
and durably deduplicate the stable event ID before performing an effect. The
envelope contains schema version, job ID/type, installation/generation, workspace,
channel, invoking actor, bot, command and reviewed arguments. It never contains
native channel history or an account token. The runnable reference receiver in
[`examples/apps/signed_receiver.py`](../examples/apps/signed_receiver.py) verifies
signatures, durably deduplicates requests and queues signed card callbacks.

Use the same HMAC protocol to POST a callback to the returned path:

```json
{
  "event_id": "unique_receiver_event_123",
  "generation": 1,
  "channel_id": "explicit-native-channel-id",
  "card": {
    "title": "Delivery reviewed",
    "text": "Your external operation completed.",
    "url": "https://customer-app.example.com/delivery/123",
    "fields": [{"label": "Status", "value": "Completed"}]
  }
}
```

Callbacks are bounded to 16 KiB, signed within five minutes, deduplicated by
installation/event ID and exact body digest, and fenced by the current signing
generation and channel grants. Reusing an event ID for a different body is
rejected. Only `cards:write` can create cards. Callback history is bounded to
10,000 events per installation; rotate/reinstall deliberately before reaching it.
Cards are text, fields and one explicitly opened HTTPS link. No HTML, remote
images, URL fetching, executable buttons, embeds or scripts are supported.
Published AI recap cards retain source citations; changed/removed sources prevent
the old cited result from being returned. Native DLP applies to arguments,
callbacks, AI disclosures, drafts and publication; Matrix ciphertext is not read.

The integration worker runs `app_platform.run_pending` with leased database claims,
stable event IDs, exponential backoff, Retry-After, a maximum of eight automatic
attempts and current-grant checks. Run the existing worker normally:

```sh
cd backend
python -m app.services.integration_worker --once
```

Signed receivers are at-least-once and must deduplicate. Generic receiver network
errors/temporary failures retry. Provider create APIs have no common idempotency
guarantee: interrupted requests, network errors and 5xx outcomes become
`uncertain` instead of automatically duplicating a possible issue/ticket/case.
An explicit administrator retry requires confirmation that the receiver did not
process the request. 429 retries honor backoff. `delivered` means HTTP acceptance,
not proof that a remote asynchronous business workflow completed. Successful or
cancelled outbox requests erase their saved argument payload; job history keeps
routing/status metadata. The UI shows an absent/stale worker honestly.

## Private summaries, generated workflows and cross-app proposals

The existing workspace OpenAI-compatible provider is reused, including its HTTPS
transport, streamed-response validation, encrypted configuration, per-user opt-in
and provider revision. No new provider key store is introduced. An immediate
request explicitly selects at most eight sources and 24,000 characters and
consents to disclosure. Source fingerprints and membership are checked again
after the provider returns. Summaries must cite real supplied `[S1]` identifiers;
private drafts retain the checked citation metadata. Opening the panel or loading
drafts never makes a provider request.

Immediate draft requests and new/updated recap schedules require a strict positive
`expected_config_revision` from the saved provider configuration the user reviewed.
A stale revision returns HTTP409 before draft creation, scheduling or provider
networking, even if another tab has already opted in to the replacement provider.
Refresh and review the destination/model again; clients must not silently substitute
a new revision. The channel UI shows the actual hostname/model, source count, API
charges, separate chat subscriptions, retention and output fallibility before Send.
Selected message text is fully readable in its scrollable review list. It is not
silently truncated. The client binds each selected native message with
`expected_text_sha256`; a message edited after review is rejected before disclosure.
Selections over eight messages or 24,000 context characters are refused.

AI can produce one of three private drafts:

* A cited summary, published as a channel card only after a separate review.
* A manual workflow with notify/approval steps addressed only to explicitly
  selected current channel members. Installation uses existing workflow schema,
  grants, password/MFA checks, limits and encrypted revision format. Draft
  approval and workflow creation commit together, so repeated approval cannot
  install a duplicate workflow.
* One to three bounded cross-app actions from explicitly selected installed
  manifests. AI sees public command schemas, never app credentials. Separate
  human approval checks each unchanged installation generation and queues only
  the reviewed arguments. No provider output can supply a URL, token, arbitrary
  tool, executable code or unselected app destination.

Channel administrators who also administer the workspace can explicitly consent
to a recurring private recap tied to the current provider revision. Each run uses
at most the **eight most recent new native messages** in its bounded time window;
this is not a complete-history digest. The schedule supports 1–168 hour intervals,
with the first check due after one interval. Every explicit enable/regrant starts
a new future-only disclosure cursor: messages from before that grant, including
messages written while paused or under a previous provider, are not backfilled.
The existing `last_run_at` field is this last disclosure/check cursor, initialized
at consent; it is not proof that a successful completion already occurred.
Legacy rows without a cursor start at their first check without sending history.
Schedules retain durable due/claim state, manual stop, and automatic pause after consent, grant or
provider failure. A schedule being stopped while the provider runs discards the
new draft. Recaps never publish automatically. Revoked membership/consent or
changed sources make private drafts unavailable; owners can delete their private
drafts subject to preservation holds. Organization admins never receive another
person's private AI drafts or app argument payloads through the app API.

All app commands, callbacks, recurring recaps and these persistent AI drafts
reject E2EE channels and both direct-message channel types. Existing secure
copilot exchanges remain explicitly selected and disclosed from a trusted device,
ephemeral, and outside recurring server automation. FreeCollab does not weaken
end-to-end encryption to make an integration appear connected.

## Isolated verification

```sh
cd backend
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_app_platform.py
cd ..
backend/.venv/bin/python frontend/tests/app-platform-browser.py
```

Backend fixtures cover actual loopback provider protocols and signed receivers,
real HTTPS/SSE AI, current grants/revocation, signature replay, generation fencing,
scope/schema limits, ambiguous outcomes, consent/citation changes, atomic human
approvals, recurring previews and DLP. The private Chromium journey navigates the
real Integrations and Work Hub, stores a once-only secret, invokes a reviewed
command, delivers to a real receiver, publishes a real signed callback card,
checks honest unconfigured AI, persisted Swedish and mobile layout. No production
accounts, provider keys, external messages or external browser requests are used.
The same browser proof now configures a private loopback HTTPS/SSE provider, checks
full selected text and the actual revision/digest request fields, keeps a generated
summary private until separate publication, and rejects an old recap-schedule
review after a provider change and fresh consent in another tab.

## Reviewed foreground actions from encrypted channels

A customer administrator may opt an installation into `secure_foreground: true`
and explicitly grant encrypted workspace channels. This mode forbids
`cards:write`: a provider cannot write plaintext callback cards into an encrypted
room. The same catalogue and `/` review UI expose only currently granted commands
with `supports_secure_foreground: true` and `delivery: foreground`.

Each invocation requires separate approval that its exact arguments leave
end-to-end encryption for the displayed recipient. The trusted device supplies
only those arguments; the service never fetches or decrypts channel history. The
native authenticated request must be bound to the current unrevoked Matrix
device session. Native actor, exact app generation and grant, Matrix room/account,
channel membership epoch and device are rechecked before and after provider I/O.
Changed authority discards the response preview even if the recipient already
received the action.

The dedicated `POST /workspaces/{workspace}/channels/{channel}/apps/foreground/
{installation}` endpoint accepts the ordinary reviewed command fields plus
`disclose_decrypted_content: true` and `matrix_device_id`. It retains a
purpose-bound keyed request digest and delivery status in `AppForegroundReceipt`,
never argument text, output, payload copies or provider error bodies. Its
HTTPS relay uses the registered destination and bounded transport. No background
job or automatic retry exists. Repeating the same request identifier only reads
its content-free outcome; unknown delivery must be inspected at the recipient.
A lost response cannot be recovered as plaintext from the server.

A successful response preview remains on the current device. Publishing it into
the encrypted conversation is a separate reviewed action using the ordinary
secure client and a stable encryption transaction identifier. Slack mirroring
and recurring server workflows do not receive encrypted-channel plaintext.
