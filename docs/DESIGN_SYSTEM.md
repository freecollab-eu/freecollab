# Design and interaction guide

FreeCollab's reference client uses an open editorial room: warm stone, an ink foreground, quiet surfaces and the person's selected accent and typeface. Conversation content sets the visual hierarchy. Sign-in, the room topbar and the appearance preview use the same four-leaf `Brand` component. Personal colors, typography, spacing and corners remain account choices.

## Rooms and focus

The topbar keeps the workspace and channel available without a permanent sidebar. **Toggle channels** opens a modal channel drawer on desktop and mobile. It supports keyboard navigation, Escape and focus restoration, and exposes the full channel name when the visible label is shortened. **More channel tools** groups the existing actions under **Quick access**, **This conversation**, **Workspace** and **Your space**, in two columns on desktop and one on mobile. **Open workspace tools** remains directly available for tasks, documents and other work.

**Channel details** in that tools menu opens the real channel's name and description. Members read; a current channel admin edits through an explicit **Save channel** or **Cancel**. **Manage channel** also appears in the member panel. Conflicts keep the person's draft and require deliberate reload. Client-encrypted labels resolve only their committed, verified event reference; missing history does not use a readable server fallback. Direct conversations and connected shared channels keep their separate controls. See [CHANNELS.md](CHANNELS.md).

The room keeps the same conversation-and-material layout for short and long histories, including while older messages load. Every loaded message remains available; a genuine short opening message can receive more typographic emphasis when the beginning of the conversation is loaded. **Focus** narrows the reading measure and hides the material column while retaining the conversation. When shared material is available, its left/right arrangement can be changed and reset from the tools menu. This arrangement is personal to the current session and channel; appearance preferences have their separate account-backed persistence.

The material column reads existing product records in an eligible native channel through the ordinary API. It can show a file, one shared document with a bounded excerpt and its stack, and an unfinished task from recent channel projects. It creates no content or assignments and is not a complete inventory or an AI recommendation. Empty channels receive no invented material. Account, credential, workspace, channel and encryption checks prevent late responses from another context becoming visible.

- A file card previews that exact file when its format is supported; other formats open the channel's ordinary Files panel.
- A document title or **Open document** link opens that document in **Work → Documents**. A stack link opens its actual stack, with the document's current stack membership and count supplied by the organization API.
- A next-step card opens that task and its project in **Work → Tasks**. Changing a task uses the real task editor and revision checks.

These cards are previews and links. Editing takes place in the real **WorkHub dialog**, with the conversation and composer still mounted behind it. Closing through **Back to conversation**, the close control or Escape applies the existing unsaved-change guard and restores the opening control and reading position in the same authorized room. Document, feed and shared-work guards also apply to tab changes. An account or workspace change invalidates the previous context. Mobile places conversation and material in one column and uses the same tools and guarded dialog.

Rooms display the member identity supplied by the product API. Native profile photos belong to actual user IDs and use authenticated private image reads; the same photo appears in conversation, threads, member lists and account settings. People can upload, replace or remove their own photo through the account panel. Missing or unavailable native photos, including unsupported external identities, use initials. The demo's fictional people have sample photos stored through that same product service, with personal-photo upload disabled in the public installation. See [profile photos and appearance](APPEARANCE.md).

**Work** resumes the last selected tool within the current account session, workspace and channel. Its bounded memory contains tool choices, without keeping closed tools or their data mounted. Reloads and identity/workspace changes reset it; a changed channel encryption mode invalidates that channel's remembered view. Explicit task, document and stack links take precedence over the remembered tab. Native documents, stacks, references, comments and relations use their ordinary persistent models and APIs. Client-encrypted document organization remains unsupported and is rejected explicitly; the material overview never falls back to readable server content for an encrypted room. See [the work-content audit](audit/WORK_CONTENT.md).

The public [product demo](PRODUCT_DEMO.md) mounts the same **Chat**, **WorkHub**, account, language and appearance providers used by a self-hosted installation. Each visit receives an isolated temporary account and workspace, a real signed in-memory session and server-stored sample records. Messages, files, Yjs documents, stacks, tasks and native workflow actions use the ordinary product API and authorization. A confirmed reset or reload starts a new visit; expiry and cleanup bound the old visit's lifetime. The demo is not durable personal storage and does not read or write an existing product account's browser storage.

Contextual assistance uses the same product component beside messages, drafts, documents and tasks. External AI is disabled in the public sample, which shows the normal disabled/unconfigured state instead of fabricated local model replies. Real AI use requires configured services, current grants, reviewed disclosure and explicit application of a result. Microsoft and Google setup panels likewise show their actual configuration state. The public deployment has no operator credentials, outbound provider access or Matrix service; a native demo test is not proof of a working external provider or end-to-end encrypted deployment. See [the AI audit](audit/AI_WORKFLOWS.md).

The marketing website shows a screenshot captured from the real Courtyard product view. Its **Explore the demo** button opens the interactive product environment. The screenshot is a preview of the shared interface; interaction happens in the actual product session.

## First conversation

Eligible native workspace administrators can open **Set up this workspace** directly from an empty room, without an automatic modal. The guide distinguishes workspace membership from channel access and opens the selected channel's existing member panel for the separate channel invitation. A workspace administrator who is only a channel member sees the read-only member action. **Write the first message** moves focus to the composer after the guide's modal isolation has been removed. Existing issued-link, unsaved-change and current-authority guards also apply to these navigation actions.

## Appearance

The Appearance panel keeps its preview visible above the **Colors**, **Typography** and **Layout** navigation. Changes apply as a draft until **Save changes** succeeds. A save confirmation follows the authenticated server response; a failed save keeps the preview available for retry. Cancel and Reset retain the behavior described in [APPEARANCE.md](APPEARANCE.md).

Frontend surfaces use the `--fc-*` CSS variables derived in `frontend/src/lib/appearance.ts`. Reuse these variables for text, surfaces, borders, accent, type, spacing and corners when extending the client. The token generator derives readable foreground colors from the selected palette. Bundled fonts avoid a font CDN dependency. Contrast checks cover tested token pairs and do not establish complete accessibility conformance.

## Search and switching

**Search & switch**, **Quick switcher** in the tools menu or **Cmd/Ctrl+K** opens the quick switcher. It searches channels already available to the signed-in person, actions and loaded top-level messages in the selected channel. The initial message load is the latest 50. Deleted messages and thread replies are excluded from message suggestions. **Search all history** is a separate tool for the authorized history-search flow.

Channel names match across case, accents and extra spaces. Prefix a query with `#` to restrict results to channels. **↑/↓** selects a result, **Enter** activates it and **Escape** closes the switcher. A message result scrolls to that loaded message. Actions open Appearance, create a channel or open the selected channel's Files/Members panel; channel-specific actions are absent when no channel is selected.

## Writing and reading

The composer grows for multiline text. **Enter** sends, **Shift+Enter** inserts a line break, and input-method composition prevents Enter from sending prematurely. Message editing also preserves line breaks. In ordinary personal-account sessions, per-channel drafts are serialized into an account-bound AES-GCM browser vault and survive channel changes, failed sends and reloads. The Mobile dialog exposes drafts and the separately opted-in [offline history/file library](MOBILE_OFFLINE.md), including after an offline reload without loaded channels. Chosen history, file downloads and durable attachment queues use the same appearance settings and clearly label local-copy freshness. Reconnection rechecks the current sign-in, membership and encryption mode; native receipts prevent duplicate delivery after a lost response. New typing takes precedence over an asynchronous restored draft. Sign-out purges the local vault and its device keys. The public demo keeps its draft state in the current visit and does not use that persistent browser vault.

Incoming messages follow the latest conversation when the reader is already near the bottom. While reading earlier messages, the view stays in place and shows a new-message count with **Jump to latest**. Those arrivals alone do not advance the latest-message read receipt while the reader remains scrolled back. Reaching the bottom, choosing the jump action or sending your own message returns to the latest conversation. Scroll transitions respect the device's reduced-motion preference.

## Dialog behavior

The shared modal helper keeps focus in the active dialog, makes the background inactive with `inert`, supports Tab/Shift+Tab and Escape, and restores focus to the opening control when it remains available. If one dialog opens another, only the top dialog receives interaction. Closing the final dialog restores the background's prior state.

Keep these behaviors when adding dialogs, including a visible close control and a useful accessible name. Keyboard and mobile browser checks complement the automated token tests; they are not a full independent accessibility audit.

## Notifications

The notification panel uses the server's read/unread state. Its six category settings govern newly generated in-app notifications through the shared helper: mentions, likes, comments, replies, follows and system updates. Disabling a category or the server's `in_app` preference suppresses both storage and realtime delivery of new notifications; it does not delete existing entries. Unknown notification types require an explicit category before they can be delivered.

Native and encrypted activity use their existing membership and notification-level checks; secure activity carries opaque references rather than message text. The comments API remains an experimental opt-in. Optional daily/weekly email summaries require channel opt-in, the global email preference and a configured mail worker, and contain counts without message bodies. Browser Web Push and native FCM/APNs have separate device-consent/setup controls in **Mobile & offline**; their generic payload and delivery limits are documented in [mobile setup](MOBILE_OFFLINE.md) and [native push](NATIVE_PUSH.md). Saving a preference alone does not prove delivery.
