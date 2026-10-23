# One work area, three views

The conversation keeps one active work area. Documents, tasks, decisions,
channel groups and the other tools opened through the conversation's WorkHub
use the same view controls. Change how much space the work occupies without
opening a second editor or moving its draft into another component.

| View | Use it for | What stays open |
| --- | --- | --- |
| **Split view** | Reading, replying and working together. Work sits beside the conversation, or in the same scrolling column on a narrow screen. | The current editor, message list and composer. |
| **Full screen** | Giving the active work the application viewport. **Split view** restores it beside the conversation. | The same editor and its current session; the conversation is retained underneath. |
| **Minimize** | Setting work aside while returning to messages. A **Continue work** bar identifies the active work. | The hidden editor, draft, selection and live document session. |

**Continue work** restores the last visible view, split or expanded, and returns
to the last available work field. Text selection and work/conversation scroll
positions are restored when the original nodes and field value are still
current. A deliberate focus move to another field or a nested dialog takes
precedence over delayed restoration.

Full screen means the application's viewport. It does not request browser or
operating-system fullscreen. In a trusted embedded client, it occupies the
iframe's viewport and cannot expand beyond the host's frame.

## Set work aside or close it

The header has separate controls for **Minimize**, **Full screen / Split view**
and **Close this work**. The footer's **Back to conversation** also sets the
work aside. Minimizing keeps the actual component mounted: a Yjs session is
not recreated, and an unsaved task, decision or group form is not discarded.
The Continue bar also has a real close control.

Close is different from a view change. **X** closes through the current
editor's normal guard. Pending document synchronization or an active save can
block closing. An ordinary unsaved draft requires its existing discard
decision. Declining keeps the editor and its draft. A minimized editor retains
those same guards; hiding it does not make it safe to discard during later
channel or workspace navigation.

Changing the view does not call those leave guards, save a draft, create a
resource, mark work complete or make a server mutation. It is available while
an existing write is pending. The write can continue through its ordinary
client independently of the view control. Creating documents, saving tasks,
recording decisions and changing groups still require their normal explicit
actions. Existing document synchronization keeps its normal behavior.

## Keyboard, source messages and context

- In expanded work, **Escape** first restores split view. It does not discard
  the draft. A nested menu, reference picker or genuine dialog handles its own
  Escape first; IME composition must not trigger a view change.
- In split work, Escape retains the editor's ordinary guarded close behavior.
  Escape in the conversation composer does not close the work area.
- **Show source message** restores split view, reveals and focuses the real
  message, or opens a saved reply's genuine thread, while keeping the editor
  mounted. It does not create a source-message copy or save the work.
- Closing back to the source keeps its reading position, even when it lies
  close to the timeline's end. Composer resizing cannot turn this return into
  following the latest message. A deliberate move in the conversation while
  work is open takes precedence over the original source position.
- Closing work or changing its actual target uses normal navigation guards.
  Pending writes still block leaving; choosing another document, task, group,
  channel or workspace cannot silently discard the current form.
- Account, credential, workspace, channel, encryption and active-work-target
  changes invalidate the previous view and focus snapshot. Revocation and
  authoritative access loss clear the old context rather than preserving it
  behind a minimized surface.

The initial view for a new work context is split. Layout mode and remembered
work-field focus are session state, without a local-storage preference or
server setting. Reloads and scope changes do not restore a prior full-screen
or minimized view. Account-backed appearance preferences and the existing
encrypted message-draft vault have their separate persistence rules.

## Existing product and encryption boundaries

The view controls wrap the ordinary product editors. They do not change
authorization, native data models, document revisions, API routing or the
verified encrypted clients. Native resources remain native; Matrix resources
retain their existing verified-device content path. Expanding or minimizing
never introduces a readable fallback for encrypted content or a new native
message-to-work routing row.

The same Chat work surface is used by the normal installation and the public
example installation. Standalone SDK components and explicit standalone
WorkHub dialogs retain their existing presentation contracts; these controls
belong to work opened inside the conversation. No backend migration or new
server preference is required.

## Implementation and verification

The shared [view-state hook](../frontend/src/lib/useWorkSurfaceLayout.ts),
[work controls](../frontend/src/components/ConversationWorkControls.tsx) and
[surface styles](../frontend/src/styles/work-surface.css) are connected by
[Chat](../frontend/src/components/Chat.tsx) to its existing editors. The hook
tracks real DOM nodes, focus, selections and scroll positions within the
current authority and work context; it does not serialize editor content.

The new view behavior passed seven real React/Chromium hook groups and 15
actual product browser groups, alongside the existing conversation and demo
regressions. See the dated results in [VALIDATION.md](VALIDATION.md) for the
executed tests, source bindings and encryption boundaries.
See also [conversation work](CONVERSATION_WORK.md),
[the design guide](DESIGN_SYSTEM.md) and [references and groups](MENTIONS_REFERENCES.md).
