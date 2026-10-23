# Work inside the conversation

The signed-in product and public example installation use the same `Chat` and
native editors. Documents, tasks and decisions open in the conversation's work
area. Split view keeps the message list and composer available without a tool
tab strip. The same editor can fill the application viewport or be minimized
without unmounting. On narrow screens, split work follows the conversation in
one column. See [workspace modes](WORKSPACE_MODES.md).

## Everyday flow

- Type `/` in the composer, or choose **/ All features**, to find every
  available feature without leaving the conversation. Search in English or
  Swedish; arrows and Enter open a feature, Escape keeps the draft. The button
  searches separately without replacing an existing message. See
  [commands, permissions and safe draft behavior](SLASH_COMMANDS.md).
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
discard decision. Minimized work retains its guards when changing the target,
channel or workspace. Revealing the source within the same conversation keeps
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
