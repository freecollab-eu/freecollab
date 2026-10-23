# Design and interaction guide

FreeCollab's reference client uses an open editorial room: warm stone, an ink foreground, quiet surfaces and the person's selected accent and typeface. Conversation content sets the visual hierarchy. Sign-in, the room topbar and the appearance preview use the same four-leaf `Brand` component. Personal colors, typography, spacing and corners remain account choices.

## Rooms and focus

The topbar keeps the workspace and channel available without a permanent sidebar. **Toggle channels** opens a modal channel drawer on desktop and mobile. It supports keyboard navigation, Escape and focus restoration, and exposes the full channel name when the visible label is shortened. **More channel tools** groups the existing actions under **Quick access**, **This conversation**, **Workspace** and **Your space**, in two columns on desktop and one on mobile. **Work in this conversation** resumes work beside the current conversation.

**Channel details** in that tools menu opens the real channel's name and description. Members read; a current channel admin edits through an explicit **Save channel** or **Cancel**. **Manage channel** also appears in the member panel. Conflicts keep the person's draft and require deliberate reload. Client-encrypted labels resolve only their committed, verified event reference; missing history does not use a readable server fallback. Direct conversations and connected shared channels keep their separate controls. See [CHANNELS.md](CHANNELS.md).

The room keeps the same conversation-and-material layout for short and long histories, including while older messages load. Every loaded message remains available; a genuine short opening message can receive more typographic emphasis when the beginning of the conversation is loaded. **Focus** narrows the reading measure and hides the material column while retaining the conversation. When shared material is available, its left/right arrangement can be changed and reset from the tools menu. This arrangement is personal to the current session and channel; appearance preferences have their separate account-backed persistence.

The material column reads existing product records in an eligible native channel through the ordinary API. It can show a file, one shared document with a bounded excerpt and its stack, and an unfinished task from recent channel projects. It creates no content or assignments and is not a complete inventory or an AI recommendation. Empty channels receive no invented material. Account, credential, workspace, channel and encryption checks prevent late responses from another context becoming visible.

- A file card previews that exact file when its format is supported; other formats open the channel's ordinary Files panel.
- A document title or **Open document** link opens that document directly in the conversation's work region. A stack link opens its actual stack, with the document's current stack membership and count supplied by the organization API.
- A next-step card opens that exact task and its project in the same region. Changing a task uses the real task editor and revision checks.

The normal conversation starts with a nonmodal work region in **Split view**, without a tool tablist. **Add a document**, **Next step**, **Decision** and **Meet** sit beside the composer; **More** exposes channel activity, workflows and other existing services. Documents, tasks, decisions, channel groups and the other inline WorkHub tools share **Minimize**, **Full screen / Split view** and a separate guarded close control. These views retain the same editor DOM and session. Split view keeps conversation messages and the composer interactive; on a narrow screen they share one scrolling column. Full screen gives that work the application's viewport, limited to the iframe in an embedded client. Minimize hides the retained work and exposes **Continue work** beside the composer. See [the workspace-mode contract](WORKSPACE_MODES.md).

**Continue in a document**, **Make a next step** and **Record decision from message** begin explicit native work from the selected message. Saving creates the real resource first. Document and task links use a native routing index containing resource and source-message IDs. A decision's canonical source-message field already records its relationship, so its visible link derives from that saved decision. Titles, task status and decision content are still read from the authorized resource APIs. Opening a reference selects the exact resource. The index does not duplicate work content or create a second document/task model. Message-to-work references are currently native-channel functionality; encrypted channels retain their existing verified work services and do not receive a plaintext routing fallback. Workflows remain native channel workflows started and approved in their existing panel; they are not message-bound workflow instances.

**Back to conversation** in the work footer parks the editor just like Minimize. **Continue work** restores its last visible view and available work field, including an unchanged text selection. Layout changes do not call leave guards or perform saves/server mutations, including while an existing write is pending. The real **X** and Escape in split work retain guarded close behavior. Escape in expanded work first restores split view; a nested popup or dialog handles Escape first. Switching a work target, channel or workspace checks the live scoped guard before changing selection, even when the editor is minimized. Outstanding document synchronization and active saves block leaving; ordinary unsaved drafts receive one confirmation. Declining leaves the current editor and its draft intact. A failed save remains a failed save; it must not emit a successful resource reference. **Show source message** returns to split view and focuses the genuine message or saved reply's thread while retaining the editor. Escape in the conversation composer does not close work. Sign-out, session replacement and authoritative loss of access clear the previous context unconditionally. The standalone WorkHub presentation used by explicit embedded/SDK entry points keeps its separate dialog behavior.

Rooms display the member identity supplied by the product API. Native profile photos belong to actual user IDs and use authenticated private image reads; the same photo appears in conversation, threads, member lists and account settings. People can upload, replace or remove their own photo through the account panel. Missing or unavailable native photos, including unsupported external identities, use initials. The demo's fictional people have sample photos stored through that same product service, with personal-photo upload disabled in the public installation. See [profile photos and appearance](APPEARANCE.md).

**Work in this conversation** recalls the last selected kind of work within the current account session, workspace and channel. This bounded navigation memory is distinct from minimizing an active editor: closed tools and their data are not retained, while minimized work stays mounted. Split/expanded/minimized layout and focus snapshots reset for a changed account, credential, workspace, channel, encryption mode or actual work target, and on reload. They are not account preferences and are not saved in browser storage. Explicit task, document and stack links take precedence over tool-navigation memory. Native documents, stacks, references, comments and relations use their ordinary persistent models and APIs. Additional document operations and stack management remain reachable through **Document options** and **Organize documents**. **Project options** keeps project history and management available; **Task options** exposes search, assignment and archived-task filters so archived work can be reopened. Client-encrypted document organization remains unsupported and is rejected explicitly; the material overview never falls back to readable server content for an encrypted room. See [the work-content audit](audit/WORK_CONTENT.md).

The public [product demo](PRODUCT_DEMO.md) mounts the same **Chat**, **WorkHub**, account, language and appearance providers used by a self-hosted installation. Each visit receives an isolated temporary account and workspace, a real signed in-memory session and server-stored sample records. Messages, files, Yjs documents, stacks, tasks and native workflow actions use the ordinary product API and authorization. A confirmed reset or reload starts a new visit; expiry and cleanup bound the old visit's lifetime. The demo is not durable personal storage and does not read or write an existing product account's browser storage.

Contextual assistance uses the same product component beside messages, drafts, documents and tasks. External AI is disabled in the public sample, which shows the normal disabled/unconfigured state instead of fabricated local model replies. Real AI use requires configured services, current grants, reviewed disclosure and explicit application of a result. Microsoft and Google setup panels likewise show their actual configuration state. The public deployment has no operator credentials, outbound provider access or Matrix service; a native demo test is not proof of a working external provider or end-to-end encrypted deployment. See [the AI audit](audit/AI_WORKFLOWS.md).

The marketing website shows a screenshot captured from the real Courtyard product view. Its **Explore the demo** button opens the interactive product environment. The screenshot is a preview of the shared interface; interaction happens in the actual product session.

## First conversation

Eligible native workspace administrators can open **Set up this workspace** directly from an empty room, without an automatic modal. The guide distinguishes workspace membership from channel access and opens the selected channel's existing member panel for the separate channel invitation. A workspace administrator who is only a channel member sees the read-only member action. **Write the first message** moves focus to the composer after the guide's modal isolation has been removed. Existing issued-link, unsaved-change and current-authority guards also apply to these navigation actions.

## Appearance

The Appearance panel keeps its preview visible above the **Colors**, **Typography** and **Layout** navigation. Changes apply as a draft until **Save changes** succeeds. A save confirmation follows the authenticated server response; a failed save keeps the preview available for retry. Cancel and Reset retain the behavior described in [APPEARANCE.md](APPEARANCE.md).

Frontend surfaces use the `--fc-*` CSS variables derived in `frontend/src/lib/appearance.ts`. Reuse these variables for text, surfaces, borders, accent, type, spacing and corners when extending the client. The token generator derives readable foreground colors from the selected palette. Bundled fonts avoid a font CDN dependency. Contrast checks cover tested token pairs and do not establish complete accessibility conformance.

Supporting text follows the same personal type scale: `--fc-caption-size` has
a 12 px floor and `--fc-detail-size` a 13 px floor, and both grow with body
text. Conversation controls, references, document tools and settings retain
44 px touch targets. Forms share surface, border and keyboard-focus treatment;
document writing areas retain the real editor while using the selected type
and palette. Menus, drawers and the public demo frame use the same restrained
surface language, with wrapping labels on narrow screens. Reduced-motion and
forced-color preferences remain part of the shared client styles.

## Search and switching

**Search & switch**, **Quick switcher** in the tools menu or **Cmd/Ctrl+K** opens the quick switcher. It searches channels already available to the signed-in person, actions and loaded top-level messages in the selected channel. The initial message load is the latest 50. Deleted messages and thread replies are excluded from message suggestions. **Search all history** is a separate tool for the authorized history-search flow.

Channel names match across case, accents and extra spaces. Prefix a query with `#` to restrict results to channels. **↑/↓** selects a result, **Enter** activates it and **Escape** closes the switcher. A message result scrolls to that loaded message. Actions open Appearance, create a channel or open the selected channel's Files/Members panel; channel-specific actions are absent when no channel is selected.

## Writing and reading

The composer's `/` opens a nonmodal feature list directly above the writing
area. English and Swedish words search the available entries; arrows and Enter
open a feature. **/ All features** supplies a separate search field when a
message is already being written. The list uses the current theme, keeps focus
in its initiating input, scrolls its own results and closes when the person
moves to another channel or account. It never publishes a command query or
starts an external action. Escape preserves literal text; a cancelled work
navigation preserves both drafts. See [the full command contract](SLASH_COMMANDS.md).

Commands accept titles and context directly: `/task "Review budget" @Ada`
opens the actual task editor with the selected person's stable ID. Creation
still requires its normal Save action. The command list keeps usage help and
argument previews compact beside its results.

`@` opens a caret-aware reference list in messages, thread replies and edits.
People, channel groups and shared work have distinct icons and stable IDs;
duplicate names include an account hint. Arrow keys choose, Enter inserts,
Escape dismisses and Tab continues normal focus traversal. The selected text
stays readable, while typed spans carry its exact identity through encrypted
drafts and delivery. Editing a reference's text removes its binding. Clicking
a saved reference checks current access before opening its exact native
resource. Group management opens in the conversation with explicit Save and
unsaved-change protection. See [reference behavior](MENTIONS_REFERENCES.md).

The composer grows for multiline text. **Enter** sends, **Shift+Enter** inserts a line break, and input-method composition prevents Enter from sending prematurely. Message editing also preserves line breaks. In ordinary personal-account sessions, per-channel drafts are serialized into an account-bound AES-GCM browser vault and survive channel changes, failed sends and reloads. The Mobile dialog exposes drafts and the separately opted-in [offline history/file library](MOBILE_OFFLINE.md), including after an offline reload without loaded channels. Chosen history, file downloads and durable attachment queues use the same appearance settings and clearly label local-copy freshness. Reconnection rechecks the current sign-in, membership and encryption mode; native receipts prevent duplicate delivery after a lost response. New typing takes precedence over an asynchronous restored draft. Sign-out purges the local vault and its device keys. The public demo keeps its draft state in the current visit and does not use that persistent browser vault.

Messages run from oldest at the top to newest at the bottom, next to the composer. Incoming messages follow the latest conversation when the reader is already near the bottom. While reading earlier messages, the view stays in place and shows a new-message count with **Jump to latest**. Those arrivals alone do not advance the latest-message read receipt while the reader remains scrolled back. Reaching the bottom, choosing the jump action or sending your own message returns to the latest conversation. Scroll transitions respect the device's reduced-motion preference.

## Dialog behavior

The shared modal helper keeps focus in the active dialog, makes the background inactive with `inert`, supports Tab/Shift+Tab and Escape, and restores focus to the opening control when it remains available. If one dialog opens another, only the top dialog receives interaction. Closing the final dialog restores the background's prior state.

Keep these behaviors when adding dialogs, including a visible close control and a useful accessible name. The inline conversation work region in split view uses neither modal isolation nor a focus trap. Expanded work occupies the application viewport while retaining its editor; genuine nested dialogs keep their own modal behavior and handle Escape before the work surface. Minimized work is hidden from interaction until Continue restores it. Keyboard and mobile browser checks complement the automated token tests; they are not a full independent accessibility audit.

## Notifications

The notification panel uses the server's read/unread state. Its six category settings govern newly generated in-app notifications through the shared helper: mentions, likes, comments, replies, follows and system updates. Disabling a category or the server's `in_app` preference suppresses both storage and realtime delivery of new notifications; it does not delete existing entries. Unknown notification types require an explicit category before they can be delivered.

Native and encrypted activity use their existing membership and notification-level checks; secure activity carries opaque references rather than message text. The comments API remains an experimental opt-in. Optional daily/weekly email summaries require channel opt-in, the global email preference and a configured mail worker, and contain counts without message bodies. Browser Web Push and native FCM/APNs have separate device-consent/setup controls in **Mobile & offline**; their generic payload and delivery limits are documented in [mobile setup](MOBILE_OFFLINE.md) and [native push](NATIVE_PUSH.md). Saving a preference alone does not prove delivery.
