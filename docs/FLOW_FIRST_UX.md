# Work follows the conversation

FreeCollab uses one conversation interface in an ordinary installation and in
the isolated example installation. These changes prioritize everyday reading,
writing and the work attached to a discussion.

| Need | Entry | Behavior |
| --- | --- | --- |
| Continue reading | Open a channel | An explicit source takes precedence, then an account-scoped remembered reading/follow choice, then the first unread incoming root established by real receipts. Known read conversations open at latest. |
| Write or find a tool | Composer, `/`, `@` | Attach, write and send stay primary. `/` exposes permitted features; `@` selects actual people and resources. Message options hold scheduling, focus and relevant draft assistance. |
| Read a document | An actual document link or Documents | A saved document opens as a readable page. Edit document and Done editing retain the same editor and live session. New documents start in editing. |
| Find work from a discussion | Compact work summary under a message or thread | Expand authorized document, task and decision links. Opening a link selects the exact native resource and retains its real source. |
| Understand or change the channel | Channel selector and Channel menu | The full channel name identifies the current place. The menu exposes introduction, members, files and details. A scoped optional introduction contains the room context and shared material. |
| Ask for assistance | Relevant message, thread or draft action | A concrete action opens review beside the content. Provider, sources, disclosure and application remain explicit. |

The feature list keeps keyboard selection while its own list scrolls under a
stationary pointer. Actual mouse movement changes the hovered row; selection
still requires an explicit action. Available entries follow current account and
workspace permissions, including owner-only data controls.

## Reading and entry

Read-receipt writes are serialized per channel for the current account and coalesce queued positions. A slower older request cannot overwrite this interface's newer reading position.

Messages remain oldest to newest, with the newest beside the composer. Ordinary
messages share the selected reading scale. The channel introduction carries
the optional larger heading and room context; the first message is not an
automatic hero.

Unread counts are not used to guess a message index. Native and verified
encrypted transports have different counting rules. The current person's
actual receipt and the authorized root-message ordering establish the first
unread incoming message. Initial history seeking is bounded. An unavailable,
deleted, out-of-window or unreadable boundary is shown honestly, with retry or
earlier-history controls. It is not permission to mark the whole channel read.

Remembered reading is in memory, scoped to actor, credential, workspace,
channel and encryption mode. A message ID and its viewport offset preserve
reading across different loaded history windows. A successful own send or an
explicit **Jump to latest** made during an initial
history request overrides its older unread plan. A newer source selection also
supersedes an older source request and its queued scroll/focus frames. Automatic
following and pointer activity cannot authorize read receipts across an unknown
or unreadable boundary; the explicit latest action remains a deliberate choice.

## Documents

A saved document's read page renders real title and plain text safely. Its
native title input, textarea, live session and leave guard remain mounted.
Reading/editing and split/expanded/minimized are independent presentation
choices, without server writes. New and command-prefilled documents start in
editing; creation still requires its real explicit action. Existing document
synchronization retains its normal behavior and visible pending status.

Reading-page focus is retained when work is set aside. Hidden old editable
fields do not take focus from the visible reading page. Document options keep
export, immutable revisions and archive actions available; comments retain
their ordinary API and permission rules.

## Discussion work

The compact row counts unique authorized documents, tasks and decisions.
Native routing IDs and canonical decisions remain the source of the
association; titles and state come from resource APIs. Derived
`discussion_message_id` metadata groups work from unopened replies under the
actual root. It does not create a second content store or persist a new
thread model. Cross-channel, deleted, cyclic or invalid source ancestry never
invents a connection. Partial loads are labeled as available work.

One scoped resolver loads bounded routing/resource pages for the conversation;
rendering each message does not issue a new channel query. Existing encrypted
work services retain their verified paths. Native message-work routing and
root derivation are not used as a plaintext fallback for encrypted rooms.

## Contextual assistance

Message actions include drafting a reply, summarizing reviewed content and
comparing actual selected native documents. A thread can summarize its loaded
replies. A nonempty draft offers tidy/shorten actions through Message options.
The channel menu can review a recap or possible next steps from loaded sources.
These scopes are named; they are not an assertion that all history was loaded.

Related-document comparison lets the person select authorized metadata
candidates and opens only those verified sources for review. It starts with
one document. The existing source-count/text budgets remain enforced; the UI
does not silently trim verified text or invent a global semantic search.

Opening review never grants consent, sends to a provider, publishes a message
or applies a result. Existing provider/model selection, warnings, source
verification, encrypted-content disclosures, cost disclosures and explicit apply
remain. Results name their resolved sources. Clearing a draft, closing its
thread or changing identity invalidates that activation; typing new text does
not reopen a dismissed review. An older close frame cannot take focus from a
newer activation or a deliberately focused field. Nonempty edits keep the same
review instance
while invalidating stale results and acknowledgments.

The public example installation shows the real unconfigured/disabled state.
It does not fabricate AI replies or operator-provider connections.

## Implementation and verification

The shared [Chat](../frontend/src/components/Chat.tsx) integrates the
[entry decision helper](../frontend/src/lib/conversation-entry.ts),
[document panel](../frontend/src/components/SharedDocumentsPanel.tsx),
[discussion resolver](../frontend/src/lib/discussion-work.ts) and
[contextual review](../frontend/src/components/ContextualCopilot.tsx).
[Flow browser checks](../frontend/tests/flow-first-browser.py) exercise actual
native product and isolated-demo data. The [validation log](VALIDATION.md)
records executed runs and their source versions; implementation in the review
branch does not itself establish deployment or external-provider acceptance.

See [conversation work](CONVERSATION_WORK.md),
[work-area views](WORKSPACE_MODES.md), [documents](SHARED_DOCUMENTS.md),
[commands](SLASH_COMMANDS.md) and [AI disclosure](COPILOT.md) for their detailed
contracts and remaining limits.
