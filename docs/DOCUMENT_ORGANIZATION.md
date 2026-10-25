# Shared-document stacks and relations

Open **Workspace tools → Documents** in an authenticated native channel. This is
persistent product functionality. The public [product demo](PRODUCT_DEMO.md)
uses this same panel and API with isolated temporary sample records; there is
no parallel demo reducer for stacks.

Create named stacks, rename them, and delete an empty stack. Move a page with its
**Move document** selector or drag its card onto a stack. **Unstacked** is the
built-in destination, not a stored stack. All channel members can organize pages
and save/remove relations; only a stack's creator or a current channel admin can
rename or delete it. An archived page retains its placement and relations; restore
it before moving or changing its relations. A stack containing an archived page
cannot be deleted even when the active view does not display that page.

Each move has a placement revision independent of Yjs text revisions. **Undo move**
returns only that page to its previous stack and preserves later text, comments
and revision history. Another move rejects an old Undo with `409`; reload the
organization before choosing again. The last Undo receipt lives in this panel,
not across closing/reloading the panel. A deleted/recreated stack ID has a new
server-generated generation: old rename, delete, move and Undo requests cannot
address its replacement, even if their numeric revision happens to match.

Use a native message’s **Save in document stack** action in the actual conversation
to open Documents with an authoritative source review. Choose or create a stack,
then explicitly **Save message reference**. The server checks the exact reviewed
message text digest, current source visibility and destination generation before
writing. Only message/stack IDs and audit metadata are stored, never a copied
message body or digest. The shelf resolves a bounded current excerpt on each
load, opens the original message/thread, and shows an unavailable source after
message deletion. Removing a reference does not delete the original message.

Comments are automatically grouped under the page’s current stack by looking up
its placement. They remain on the original page; opening **Comment in …** focuses
that comment in its authenticated page. Moving or archiving the page updates the
grouping without a comment copy or reparenting. The shelf displays the latest 100
comments for the selected archive view and explicitly discloses a partial result;
open the page for its full discussion. Source authors use the current channel
roster or an opaque fallback label.

Inside a page, **Related documents** saves a bidirectional pair or opens the other
page. **Possible connections** compares significant words in the current page title or its visible unresolved
comments with other visible native page titles. Its explanation names the shared
words and links to the original comment when used. It does not fetch hidden
documents, inspect page bodies, call a model or write automatically. A suggestion is not an inferred fact.
**Save relation** performs fresh server permission/channel/archive checks. Pages
with unrelated titles can also be paired manually. Existing comments remain
attached to each page; they are not annotations on a relation.

## Reviewed AI inside a native stack

Expand **Review this stack with AI**, choose up to eight current pages, then open
**AI help**. The default bounded selection and total selected/omitted page count
are visible. The shared real Copilot flow reviews the exact authorized page
excerpts, provider/model and disclosure before an explicit send. The combined
reviewed context limit is 24,000 characters including source headers. No request
is sent automatically, and comments or saved messages are not silently added to
these page sources.

The actions summarize, suggest a reading order or find open questions. Their
results are read-only: they do not move documents, save links or change content.
The context identity binds the stack generation/version, current page membership,
placement and source metadata, selected IDs and locale. Current source/provider
checks still run at send time. A configured provider and user disclosure are
required; no model service or customer credentials are bundled. The public
product demo uses these same controls with AI disabled, so it produces no
model answer.

## All-content collections

A native document stack can also contain references to binary files, external
links, pages, messages, projects, tasks, feeds, decisions, calendar events, clips
and workflows. **Add material** chooses actual accessible resources. Originals,
ACLs and histories stay in their original location; removing a reference does
not delete content. These collections do not move attachments between channels
or provide Office rich-text coauthoring. AI grouping proposes reviewed resource
IDs; only explicit approval creates a collection, after a fresh source/version
check. No provider-invented ID can become a resource pointer.

`/channels/{channel_id}/content-collections` lists or atomically creates a title
and up to 100 distinct typed pointers. Native collections reuse the existing
stack identity. Add/remove uses `expected_version` and immutable `generation`;
every write takes the channel lock and resolves current canonical resources.
A task's supplied parent must match its actual project. Deleted/replaced or
unauthorized items become unavailable references. A nonempty collection cannot
be deleted. Pointer metadata is included in authorized exports and FK-ordered
retention; no copied message/document body is stored in a collection.

## Encryption and scope

Native stack titles use a purpose-bound envelope including channel, stack ID
and immutable generation. Page IDs, placements, generations, versions, author IDs,
timestamps and saved relation/resource pairs remain server-visible routing
metadata. This native mode is encryption at rest.

Encrypted channels use the verified encrypted-work protocol for stacks,
placements, relations and saved message references. The client keeps a bounded
organization aggregate inside encrypted Matrix events; no native organization
title/body sidecar is sent. Nested stack authors have immutable full Matrix
identities and author namespaces. Rename/delete is allowed only for the creator
or an exact publication-time administrator receipt bound to the verified event
sender and room. A later role promotion cannot authorize an older edit. Missing
receipts fall back to creator-only validation; invalid nested changes are
quarantined while valid ancestry remains readable.

Saved encrypted messages retain the original room event ID. Their previews read
the complete bounded replacement history and independently verify each edit's
device, original author, exact target and replacement text. The latest verified
edit supplies the excerpt and edit timestamp. At most 100 edit candidates are
accepted; incomplete, redacted or unverified history blocks the preview instead
of falling back to an older body. Saving checks that text again after loading the
current organization and rejects a changed review before publishing a pointer.

Independent concurrent changes merge by item. Competing edits to the same item
show verified versions for explicit review; prior revisions remain in history.
An unauthorized merge cannot delete another author's independent stack. Native
secure pages use their existing pooled Yjs editor, not a duplicate writer.
Generic encrypted collections organize verified native/peer resources alongside
these page stacks. Peer document links open the actual federated Yjs reader.
Unverified, locked or conflicting resources never receive fabricated native IDs
or a plaintext fallback.

## Protocol and bounds

`/channels/{channel_id}/shared-document-organization` is authenticated and uncached:

| Method | Resource | Contract |
| --- | --- | --- |
| GET | root, optionally `?archived=true` | Current stacks, current page metadata, derived comments, saved-message references, visible-page placements and relations whose two endpoints are in the selected archive view. |
| POST | `/stacks` | Client-generated new UUID `id`, title 1–100 characters; matching author/ID/title retry is idempotent. |
| PATCH | `/stacks/{id}` | Title plus `expected_version` and `expected_generation`; creator/admin only. |
| DELETE | `/stacks/{id}` | Both expected values as query parameters; creator/admin; empty including archived pages. |
| PUT | `/documents/{id}/placement` | `expected_version` (0 for no placement), nullable `stack_id`, matching `stack_generation` for a non-null target. |
| POST | `/relations` | New request `id`, `document_id`, `related_document_id`; exact retry idempotent, unordered duplicate/self/cross-channel pairs rejected. |
| DELETE | `/relations/{id}` | Current membership and two active same-channel endpoints checked again. |
| GET | `/message-preview/{message_id}` | Authoritative current native text and SHA256 for explicit source review; deleted/hidden-parent sources rejected. |
| POST | `/stacks/{id}/message-references` | New request `id`, `message_id`, exact `expected_text_sha256` and destination `stack_generation`; matching retry idempotent. |
| DELETE | `/message-references/{id}` | Current channel membership checked; can remove an unavailable source without deleting its message. |

At most 30 stacks, 200 saved pairs and 200 saved-message references per channel; existing shared-page limit is
50 including archived pages. Case-insensitive duplicate stack names are rejected,
while changing only a stack's own letter case is allowed. New installations and
existing databases create four additive tables (`SharedDocumentStack`,
`SharedDocumentPlacement`, `SharedDocumentRelation`, `SharedDocumentStackReference`) through `create_all`; no
existing table is altered. Clients must generate a new UUID for every new stack
or relation and retain that request ID when retrying a lost acknowledgment.

Verified owner exports include stack titles, placements, relation pairs and saved-message routing records.
Retention considers a page's organization timestamps and relationships as part
of its retained history; a recent relationship protects both endpoints. A recent saved-message reference protects its source, any thread parent chain and the stack. Eligible
whole old histories and their placements/relations are erased in FK order; an
old stack is eligible only when no placement or saved-message reference would remain. No comment or message plaintext copy is introduced. Export rejects an
unverifiable title envelope instead of emitting its ciphertext.

## Validation

- `backend/tests/test_document_organization.py`: native CRUD/auth/revocation,
  generation+revision races, text-preserving Undo, archive filtering, limits,
  canonical pairs, secure fail-closed, real owner export and FK-enforced retention.
- `frontend/tests/document-organization.test.mjs`: native transport, account and
  encryption guards, and strictly visible-title proposals.
- `frontend/tests/document-organization-browser.py`: isolated real API/Vite/Chromium
  product flow, persistence, two-member conflict, explicit related-page navigation,
  pending comment draft retention and 320px layout. No live external provider or
  Matrix acceptance is implied by this native-only proof.

## Automatic local connection proposals

On a selected page, opt into **Automatically suggest document connections on this
device**. A real local LSA worker derives a low-rank corpus from currently visible
page titles and unresolved comments, then refreshes proposals when that context
changes. It runs without an AI provider or network disclosure. The exact corpus
is described in the UI; it does not claim to have read page bodies, hidden
comments or private channels. At least four pages are required for this semantic
mode. Its nonsecret opt-in preference is account/device scoped; embedded memory
sessions keep the preference in memory.

Proposals cite actual visible comments and exclude existing relations, foreign
channels and archived/unavailable pages. The user must choose **Save relation**;
no background worker moves or links a document. The existing explicit shared-word
suggestions remain separately explained. Model-assisted semantic grouping and
reviewed collection creation use the configured AI/runtime path with their own
provider/disclosure review. None of these flows silently reparent comments or
claim autonomous background LLM writes.
