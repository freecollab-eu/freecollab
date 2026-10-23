# Design and interaction guide

FreeCollab's reference client uses an open editorial room: warm stone, an ink foreground, a vermilion accent and the person's selected typeface. Conversation content sets the visual hierarchy. Sign-in, the room topbar and the appearance preview use the same four-leaf `Brand` component. Personal colors, typography, spacing and corners remain account choices.

## Rooms and focus

The topbar keeps the workspace and channel available without a permanent sidebar. **Toggle channels** opens a modal channel drawer on desktop and mobile. It supports keyboard navigation, Escape and focus restoration, and exposes the full channel name when the visible label is shortened. **More channel tools** groups the existing actions under **Quick access**, **This conversation**, **Workspace** and **Your space**, in two columns on desktop and one on mobile. **Open workspace tools** remains directly available for tasks, documents and other work.

**Channel details** in that tools menu opens the real channel's name and description. Members read; a current channel admin edits through an explicit **Save channel** or **Cancel**. **Manage channel** also appears in the member panel. Conflicts keep the person's draft and require deliberate reload. Client-encrypted labels resolve only their committed, verified event reference; missing history does not use a readable server fallback. Direct conversations and connected shared channels keep their separate controls. See [CHANNELS.md](CHANNELS.md).

Short conversations can give a genuine message more typographic emphasis and show a real shared file beside the conversation. Long conversations retain every message in a compact layout with a reading measure of up to `68ch`. **Focus** gives the conversation a narrower reading measure while keeping every message available. When shared material is available, its left/right arrangement can be changed and reset from the tools menu. This arrangement is personal to the current session and channel; appearance preferences have their separate account-backed persistence.

Mobile uses one reading column. Message actions open from a labelled **More message actions** control. Real rooms use the member identity supplied by the API, with initials when no photograph is available. The public sample uses clearly fictional people and artwork.

The public demo exposes its existing five sections through **Tools**. Its **Arrange** controls can pin, order and position the sample courtyard image and editable brief; **Focus** concentrates on the conversation. Each sample channel keeps a separate draft with a local draft indicator. Sending clears only that channel's draft. These sample changes stay in memory in the current tab and reset on reload or confirmed reset. They do not change another person's layout or connect external services. Authenticated channel drafts use the separate encrypted persistence described below.

Open the demo's shared brief or next step from its material or context links. They appear beside the same mounted conversation on desktop. Mobile shows one context at a time with **Back to conversation** and Escape returning to its source, draft and reading position. Settings and channel detours also retain that return position. Conversation navigation keeps the selected channel. Recording a decision adds one task and shows its next step beside the conversation; revising updates that same task. Changing its owner or action reopens it, while unchanged action/owner retain completion. Provider cards open the chosen Microsoft or Google preparation guide directly, in both the full app and independent website. These routes and interactions do not authorize a provider.

The sample desk groups pages in local stacks, keeps message references and offers explicit document connections. Contextual assistance stays beside the selected message, draft, page or task; its demo output is a local example and does not contact an AI provider. The authenticated document panel has separate native storage for stacks, references and document relations. It currently rejects organization in client-encrypted channels rather than storing readable titles. A demo interaction alone is not evidence of authenticated or encrypted persistence. See [the work-content audit](audit/WORK_CONTENT.md) and [the AI audit](audit/AI_WORKFLOWS.md).

In the authenticated client, **Work** resumes the last selected tool within the current account session, workspace and channel. Its bounded memory contains tool choices, without keeping closed tools or their data mounted. Reloads and identity/workspace changes reset it; a changed channel encryption mode invalidates that channel's remembered view. Explicit task and resource links take precedence. **Back to conversation** uses the existing guarded close and restores the opening control. Tab changes keep the current dialog mounted, with the document/feed/shared-work guards and secure-channel eligibility checks intact.

## First conversation

Eligible native workspace administrators can open **Set up this workspace** directly from an empty room, without an automatic modal. The guide distinguishes workspace membership from channel access and opens the selected channel's existing member panel for the separate channel invitation. A workspace administrator who is only a channel member sees the read-only member action. **Write the first message** moves focus to the composer after the guide's modal isolation has been removed. Existing issued-link, unsaved-change and current-authority guards also apply to these navigation actions.

## Appearance

The Appearance panel keeps its preview visible above the **Colors**, **Typography** and **Layout** navigation. Changes apply as a draft until **Save changes** succeeds. A save confirmation follows the authenticated server response; a failed save keeps the preview available for retry. Cancel and Reset retain the behavior described in [APPEARANCE.md](APPEARANCE.md).

Frontend surfaces use the `--fc-*` CSS variables derived in `frontend/src/lib/appearance.ts`. Reuse these variables for text, surfaces, borders, accent, type, spacing and corners when extending the client. The token generator derives readable foreground colors from the selected palette. Bundled fonts avoid a font CDN dependency. Contrast checks cover tested token pairs and do not establish complete accessibility conformance.

## Search and switching

**Search & switch**, **Quick switcher** in the tools menu or **Cmd/Ctrl+K** opens the quick switcher. It searches channels already available to the signed-in person, actions and loaded top-level messages in the selected channel. The initial message load is the latest 50. Deleted messages and thread replies are excluded from message suggestions. **Search all history** is a separate tool for the authorized history-search flow.

Channel names match across case, accents and extra spaces. Prefix a query with `#` to restrict results to channels. **↑/↓** selects a result, **Enter** activates it and **Escape** closes the switcher. A message result scrolls to that loaded message. Actions open Appearance, create a channel or open the selected channel's Files/Members panel; channel-specific actions are absent when no channel is selected.

## Writing and reading

The composer grows for multiline text. **Enter** sends, **Shift+Enter** inserts a line break, and input-method composition prevents Enter from sending prematurely. Message editing also preserves line breaks. Per-channel drafts are serialized into an account-bound AES-GCM browser vault and survive channel changes, failed sends and reloads. The Mobile dialog exposes drafts and the separately opted-in [offline history/file library](MOBILE_OFFLINE.md), including after an offline reload without loaded channels. Chosen history, file downloads and durable attachment queues use the same appearance settings and clearly label local-copy freshness. Reconnection rechecks the current sign-in, membership and encryption mode; native receipts prevent duplicate delivery after a lost response. New typing takes precedence over an asynchronous restored draft. Sign-out purges the local vault and its device keys.

Incoming messages follow the latest conversation when the reader is already near the bottom. While reading earlier messages, the view stays in place and shows a new-message count with **Jump to latest**. Those arrivals alone do not advance the latest-message read receipt while the reader remains scrolled back. Reaching the bottom, choosing the jump action or sending your own message returns to the latest conversation. Scroll transitions respect the device's reduced-motion preference.

## Dialog behavior

The shared modal helper keeps focus in the active dialog, makes the background inactive with `inert`, supports Tab/Shift+Tab and Escape, and restores focus to the opening control when it remains available. If one dialog opens another, only the top dialog receives interaction. Closing the final dialog restores the background's prior state.

Keep these behaviors when adding dialogs, including a visible close control and a useful accessible name. Keyboard and mobile browser checks complement the automated token tests; they are not a full independent accessibility audit.

## Notifications

The notification panel uses the server's read/unread state. Its six category settings govern newly generated in-app notifications through the shared helper: mentions, likes, comments, replies, follows and system updates. Disabling a category or the server's `in_app` preference suppresses both storage and realtime delivery of new notifications; it does not delete existing entries. Unknown notification types require an explicit category before they can be delivered.

Native and encrypted activity use their existing membership and notification-level checks; secure activity carries opaque references rather than message text. The comments API remains an experimental opt-in. Optional daily/weekly email summaries require channel opt-in, the global email preference and a configured mail worker, and contain counts without message bodies. Browser Web Push and native FCM/APNs have separate device-consent/setup controls in **Mobile & offline**; their generic payload and delivery limits are documented in [mobile setup](MOBILE_OFFLINE.md) and [native push](NATIVE_PUSH.md). Saving a preference alone does not prove delivery.
