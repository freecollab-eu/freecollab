# Find features from the conversation

Type `/` at the beginning of the message composer to see every feature entry
point available in that conversation. Continue typing to filter by a command,
name or intent, in English or Swedish. The list includes short descriptions and
does not truncate the available features. Arrow keys choose a result, Enter
opens it, and Escape closes the list. Shift+Enter still inserts a new line.

The **/ All features** button opens the same list with its own search field.
This keeps an existing message draft unchanged. Both entry points are part of
the normal product `Chat`, including the example installation.

## Available entry points

Availability depends on the current account, workspace, channel and embedding
scope. The server and existing native/encrypted clients retain their normal
authorization checks. These are shortcuts to existing interfaces:

| Command | Opens |
| --- | --- |
| `/document` | Documents, stacks, comments and revision history |
| `/task` | Projects, next steps and checklists |
| `/decision` | Decisions and their context |
| `/meet` | Voice, video and screen sharing |
| `/files` | Channel uploads, preview and file versions |
| `/members` | Channel members and invitations |
| `/channel` | Channel name, description and settings |
| `/saved` | Private saved messages and completed follow-ups |
| `/pins` | Shared pinned messages |
| `/notify` | Channel notification preferences |
| `/activity` | Updates, announcements and polls |
| `/workflow` | Workflow runs and approvals |
| `/cloud` | Connected cloud files or encrypted provider links |
| `/meeting-services` | Connected external meeting services |
| `/apps` | Installed app commands, cards and reviewed AI drafts |
| `/ai` | AI providers and context permissions |
| `/shared-work` | Work shared with a verified connected installation |
| `/schedule` | Draft scheduling and personal reminders |
| `/focus` | Toggle the quieter conversation layout |
| `/search` | Authorized history search |
| `/channels` | Channel list |
| `/new-channel` | New-channel form |
| `/message` | Direct and small-group conversations |
| `/integrations` | Organization sign-in and connected providers |
| `/setup` | Workspace setup guide |
| `/feedback` | Workspace feedback form |
| `/data` | Workspace export, retention and data controls |
| `/admin` | Installation administration |
| `/my-day` | Assigned work and reminders |
| `/notifications` | Personal notifications |
| `/status` | Presence and availability |
| `/mobile` | Device, offline and encrypted outbox settings |
| `/appearance` | Personal colors and typography |
| `/account` | Profile, account and language settings |
| `/encryption` | Trusted devices, encryption and recovery |

For example, `uppgift`, `typsnitt`, `dokument` and `Grok` find the relevant
features without requiring the English shortcut. The displayed names and
descriptions follow the selected interface language.

## Drafts, permissions and boundaries

- Opening an entry does not send a message, save a new resource, run an
  installed app or approve external data sharing. Normal explicit review and
  save/approval controls remain.
- A typed slash query is removed only after its selected entry opens. If a
  pending write or an unsaved-work warning rejects navigation, both the
  existing work and the composer query remain.
- Enter with no matching features sends nothing. Escape keeps the literal
  text; a subsequent ordinary send can send it as a message. `//`, multiline
  text and slashes in ordinary prose or URLs do not open the feature list.
- Data controls require a native personal workspace owner. Installation
  administration requires the current native staff role and an available
  administration handler. Workspace setup requires a native owner/admin.
  Restricted embedded sessions do not receive organization navigation entries.
- App commands are omitted for encrypted channels and DMs. Federated shared
  work appears only after the actual current channel's work context verifies.
  Documents and tasks continue to use their existing native or verified-device
  encrypted clients; the shortcut does not change their encryption model.
- Query matching uses local feature metadata. It does not send message text to
  an AI provider or external search service. Account, workspace and channel
  changes close the picker, and dispatch checks the current session again.

This is a feature launcher. Arbitrary installed app syntax such as
`/vendor-command arguments` is not interpreted or executed by the composer.
Use `/apps` to review the installed command's actual argument form and consent.

See [conversation work](CONVERSATION_WORK.md), [appearance](APPEARANCE.md) and
[the feature audit](FEATURE_AUDIT.md). The registry and parser are in
[slash-commands.ts](../frontend/src/lib/slash-commands.ts); the actual browser
proof is [slash-commands-browser.py](../frontend/tests/slash-commands-browser.py).
