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

## Encryption and scope

Stacks and relations are currently native-channel features. Native stack titles
are encrypted at rest in a purpose-bound envelope including channel, stack ID
and immutable generation. Page IDs, placement, generation, versions, author IDs,
timestamps and saved relation pairs are server-visible routing metadata.
This is not end-to-end encryption.

The organization API returns `409` in preparing or active encrypted channels.
The client displays the unsupported capability explicitly and sends no native
stack title or organization request. Existing Matrix-encrypted collaborative
pages, comments and named revisions remain available as a flat list. There is no
plaintext fallback and no claim that encrypted document organization is complete.

The feature organizes FreeCollab shared pages within one channel. It does not
create binary-file folders, move attachments between channels, group external
provider files or provide rich-text/Office document coauthoring.

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
