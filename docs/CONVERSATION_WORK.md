# Work inside the conversation

The signed-in product and public example installation use the same `Chat` and
native editors. Documents, tasks and decisions open in the conversation's work
area. The message list and composer stay available; there is no daily WorkHub
dialog or tool tab strip. On narrow screens the same work area follows the
conversation in one column.

## Everyday flow

- Choose **Add a document**, **Next step**, **Decision** or **Meet** beside the
  composer. **More** offers channel activity, workflows, cloud files, apps and
  installation settings in the same work area.
- Open a message's **More message actions** to **Continue in a document**,
  **Make a next step** or create a decision. Document and task text starts as a
  reviewed draft. Creating the real record requires the normal explicit action;
  opening a draft does not save a resource.
- A saved document or task can be linked to the source message. A decision uses
  its existing native source-message field. The message then offers the actual
  current resource, including its native title and task/decision status.
- **Show source message** reveals the genuine message or saved reply's thread
  while keeping the editor mounted. **Back to conversation** restores the
  opening control and reading position unless the person has already moved on
  in the conversation.

Documents use the existing Yjs session, comments, revisions and export. Document
options and organization are contextual disclosures. Tasks keep their existing
checklist, status, assignment, history and conflict handling; project and task
options expose management, archived records and filters. These controls use the
same product APIs as the full standalone components. Personal appearance and
uploaded profile photos follow the normal account settings.

Pending document synchronization and native writes block navigation. Unsent
drafts require one discard decision. Guards apply to work, source, channel and
workspace navigation. Session revocation and authoritative membership changes
still clear the old account's work rather than allowing a draft to retain access.

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
source/target erasure also removes dependent routing rows.

New native rooms and DMs do not generate an unused legacy encryption envelope.
Existing legacy envelopes remain untouched. The reference API rejects legacy
encrypted channels, Matrix/secure channels and shared Connect scopes; it does not
fall back to plaintext. Their existing encrypted editors remain separate from
this native routing capability.

## Current limits

Link creation is explicit, not automatic semantic pairing. Editing a source
message does not rewrite the resulting document or task. The conversation reads
the most recent 50 routing references and resolves a bounded set of task projects;
this is not a complete channel inventory. The paginated API is available for
other clients. Local changes, window focus and a bounded 60-second refresh update
the reference cards; no dedicated realtime reference event is promised. There is
currently no unlink button on the conversation card.

See [the shared design contract](DESIGN_SYSTEM.md), [demo installation and
boundaries](PRODUCT_DEMO.md) and [dated validation evidence](VALIDATION.md).
