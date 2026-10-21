# Design and interaction guide

FreeCollab's reference client pairs warm neutral surfaces and generous spacing with a shared four-leaf mark. Sign-in, the chat sidebar and the appearance preview use the same `Brand` component. Personal colors, typography, spacing and corners remain account choices; the shared identity does not override them.

## Appearance

The Appearance panel keeps its preview visible above the **Colors**, **Typography** and **Layout** navigation. Changes apply as a draft until **Save changes** succeeds. A save confirmation follows the authenticated server response; a failed save keeps the preview available for retry. Cancel and Reset retain the behavior described in [APPEARANCE.md](APPEARANCE.md).

Frontend surfaces use the `--fc-*` CSS variables derived in `frontend/src/lib/appearance.ts`. Reuse these variables for text, surfaces, borders, accent, type, spacing and corners when extending the client. The token generator derives readable foreground colors from the selected palette. Bundled fonts avoid a font CDN dependency. Contrast checks cover tested token pairs and do not establish complete accessibility conformance.

## Search and switching

**Search & switch** or **Cmd/Ctrl+K** opens the quick switcher. It searches channels already available to the signed-in person, actions and loaded top-level messages in the selected channel. The initial message load is the latest 50. Deleted messages and thread replies are excluded from message suggestions. No full-history search endpoint or cross-channel message search is implemented.

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
