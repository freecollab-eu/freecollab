# Work inside the conversation

The signed-in product and public example installation use the same `Chat` and
native editors. Documents, tasks and decisions open in retained work areas.
Several areas can coexist with independent drafts and sessions. Split view keeps the message list and composer available without a tool
tab strip. The same editor can fill the application viewport or be minimized
without unmounting. On narrow screens, split work follows the conversation in
one column. See [workspace modes](WORKSPACE_MODES.md).

## Everyday flow

- Type `/` in the composer, or choose **/ All features**, to find every
  available feature without leaving the conversation. Search in English or
  Swedish; arrows and Enter open a feature, Escape keeps the draft. The button
  searches separately without replacing an existing message. See
  [commands, permissions and safe draft behavior](SLASH_COMMANDS.md).
- Use the same `/` and `@` controls in collaboration writing fields. A command
  in a document can open a task without closing the document. Chosen identities
  follow the text into reviewed drafts and remain typed links after saving.
  See [the field and concurrency contract](UNIVERSAL_COLLABORATION.md).
- The composer keeps attachments, writing and sending primary. **Message
  options** holds scheduling, focus and relevant draft assistance. Documents,
  tasks, decisions, meetings, activity, workflows, cloud files and other
  permitted services are reached through the same `/` list.
- Open a message's **More message actions** to **Continue in a document**,
  **Make a next step** or create a decision. Document and task text starts as a
  reviewed draft. Creating the real record requires the normal explicit action;
  opening a draft does not save a resource.
- A saved document or task can be linked to the source message. A decision uses
  its existing native source-message field. Each discussion has a compact
  expandable count of unique documents, tasks and decisions, including work
  from unopened replies. Its links show current native titles and statuses.
- Saved documents open as readable pages. **Edit document / Done editing**
  switches the view without replacing the real editor or Yjs session. New
  documents start in editing; creation still requires its explicit action.
- A message's concrete **Draft a reply**, **Summarize this message** or
  **Find related documents** action opens source review beside it. Threads can
  summarize loaded replies; nonempty drafts have tidy/shorten actions. Review
  never sends to a provider or applies a result automatically.
- **Full screen / Split view** changes the active work's space. **Minimize**
  and the footer's **Back to conversation** park it. **Continue work** restores
  the last view and available field; the same editor, selection and session
  stay alive. The real **X** remains a guarded close action.
- **Show source message** restores split view, reveals and focuses the genuine
  message or saved reply's thread while keeping the editor mounted.

Documents use the existing Yjs session, comments, revisions and export. Document
options and organization are contextual disclosures. Tasks keep their existing
checklist, status, assignment, history and conflict handling; project and task
options expose management, archived records and filters. These controls use the
same product APIs as the full standalone components. Personal appearance and
uploaded profile photos follow the normal account settings.

Layout changes alone call no leave guard and perform no save or server mutation,
even while a write is pending. Pending document synchronization and native
writes still block actual close or navigation away. Unsent drafts require one
discard decision. Opening another retained area preserves that work. Closing
one area checks its own guards; changing channel or workspace checks them all.
Minimized work retains its guards. Revealing the source within the same conversation keeps
the editor and does not discard its draft. Escape in expanded work restores
split view first; nested popups handle Escape first. Layout/focus state resets
on reload or a changed account, credential, workspace, channel, encryption or
work target. Session revocation and authoritative membership changes still clear
the old account's work rather than allowing a draft to retain access.

## Native message-to-work references

The channel API is `/channels/{channel_id}/conversation-work`:

| Method | Behavior |
| --- | --- |
| `GET` | List authorized references with optional `source_message_id`, `limit` and `offset`; returns `items` and `next_offset`. |
| `POST` | Accept `source_message_id`, `resource_kind` (`document`, `task`, `decision`) and `resource_id`. Return 201 for a new link or 200 for the same existing link. |
| `DELETE /{reference_id}` | Unlink as the creator or current channel administrator; does not delete the document, task, decision or message. |

The routing row stores IDs, creator and creation time. It does not copy message
text, resource titles, status or document content. Task project IDs and visible
metadata are resolved from current native records. The server checks the actual
source and target in the same currently authorized channel, including fresh
membership and DM-block checks around writes. Missing or inaccessible targets
are omitted from reads. A routing reference is not a retention hold; normal
source/target erasure also removes dependent routing rows. Reads add optional
`discussion_message_id` metadata derived from valid same-channel native source
ancestry. It groups reply work under its root without persisting another thread
model. Missing, deleted, cross-channel, cyclic or invalid ancestry never invents
a root. The canonical decision source remains authoritative and needs no second
routing write.

New native rooms and DMs do not generate an unused legacy encryption envelope.
Existing legacy envelopes remain untouched. The reference API rejects legacy
encrypted channels, Matrix/secure channels and shared Connect scopes; it does not
fall back to plaintext. Their existing encrypted editors remain separate from
this native routing capability.

## Current limits

Link creation is explicit, not automatic semantic pairing. Editing a source
message does not rewrite the resulting document or task. One scoped conversation
resolver reads the bounded routing pages and authorized resource lists once for
all message summaries. It pages through the server's
2,000-reference bound and resolves referenced task projects with at most four
concurrent requests, rather than omitting projects beyond the first boards.
Resource APIs retain their own limits. Partial or legacy metadata loads are
labeled **Available work**; the summary is not a complete channel inventory.
Local changes, window focus and a bounded 60-second refresh update the reference
cards; no dedicated realtime reference event is promised. There is
currently no unlink button on the conversation card.

See [the six everyday flows](FLOW_FIRST_UX.md), [the shared design contract](DESIGN_SYSTEM.md), [demo installation and
boundaries](PRODUCT_DEMO.md) and [dated validation evidence](VALIDATION.md).
