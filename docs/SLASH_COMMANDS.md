# Commands and drafts in the conversation

Type `/` at the beginning of the message composer to see every available command
in that conversation. Continue typing to filter by a command, name or intent,
in English or Swedish. Built-in commands can also take arguments to prepare
real document, task, decision and group drafts, or a history search. The list
shows the command format, your argument preview and any validation error.
Arrow keys choose a result, Enter opens its reviewed draft or existing controls,
and Escape closes the list. Shift+Enter inserts a new line in the command.

The **/ All features** button opens the same list with its own search field.
This keeps an existing message draft unchanged. Both entry points are part of
the normal product `Chat`, including the example installation. Choosing a
feature from this separate browser does not reinterpret or erase that message.

## Write a command

| Example | Result after Enter |
| --- | --- |
| `/uppgift Granska budget` | Task editor with **Granska budget** as its title |
| `/task "Review budget" Compare the revised estimate.` | Task editor with a title and description |
| `/dokument "Mötesanteckningar" Deltagare och nästa steg.` | Document editor with a title and starting content |
| `/beslut "Vi lanserar" Godkänt efter säkerhetsgranskning.` | Proposed decision with a title and its context |
| `/sök budget` | History search with **budget** already entered |
| `/group "Design team" @Ada @Sam` | Group review form, with people chosen from the `@` suggestions |

English shortcuts remain stable when the interface language changes. Swedish
aliases include `/dokument`, `/uppgift`, `/beslut`, `/sök`, `/grupp` and
`/grupper`; `/group` and `/groups` open the same group interface. Command names
are case insensitive. Selecting a command suggestion preserves its arguments.

Without quotes, the first line after a document, task or decision command is
the title; following lines become its content or description. Double quotes
allow a separate title and context on the same line. Inside quoted titles,
`\"` and `\\` represent a quote and a backslash. Unicode text and context line
breaks are preserved; CRLF line endings become LF. Titles must contain
1–200 characters on one line. Group names allow 1–80 characters on one line;
searches allow up to 256 characters. Oversized or incomplete input shows help
and stays in the composer instead of being silently truncated.

```text
/document "Product review"
What changed this week?
What should happen next?
```

Typing or choosing suggestions never saves these drafts. Enter opens the same
editable forms used elsewhere in the product; their normal explicit save or
record action creates the resource. A task needs an existing project: its draft
remains queued while you choose or create one. A decision starts as proposed,
and a title without separate decision context also supplies its initial text.
Use the ordinary search action after reviewing a prefilled history query.

Select people from `@` suggestions to supply their actual account identities.
One selected person can prefill a task's assignee or a decision's responsible
person. More than one selected person shows a choice warning for those commands.
Selected people at the end of the command are removed from the title/context;
middle mentions stay in its text. A visible name alone, a typed `@` string, a
document or a group is never guessed to be a single person's identity.

The group command prepares the real group form with selected people; membership
can be reviewed there before saving. `/groups` without arguments opens the
existing list. Current channel admins manage groups, and the server validates
the group name and its 1–50 distinct current channel members. A group does not
grant access to a channel or resource. No group is created while you type.

Other commands, such as `/status`, `/meet` and `/apps`, open their existing
controls without arguments. Extra text produces an explanation rather than
being discarded or executed. Unknown commands stay visible with help. Press
Escape to retain their literal text; a subsequent normal send is an intentional
message send.

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
| `/groups` | Mention groups and their reviewed membership form |
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
- Enter with no matching command sends nothing. Escape keeps the literal text;
  a subsequent ordinary send can send it as a message. `//` and slashes in
  ordinary prose or URLs do not open the command list. Recognized commands can
  span multiple lines; unknown multiline text keeps normal message behavior.
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

The composer interprets the built-in commands above locally. Arbitrary installed
app syntax such as `/vendor-command arguments` is not executed by the composer.
Use `/apps` to review the installed command's actual argument form and consent.

See [conversation work](CONVERSATION_WORK.md), [appearance](APPEARANCE.md) and
[the feature audit](FEATURE_AUDIT.md). The registry is in
[slash-commands.ts](../frontend/src/lib/slash-commands.ts), the argument
interpreter in [composer-commands.ts](../frontend/src/lib/composer-commands.ts),
and its parser tests in
[composer-commands.test.mjs](../frontend/tests/composer-commands.test.mjs).
Browser proofs cover the
[feature list](../frontend/tests/slash-commands-browser.py) and
[commands with references](../frontend/tests/commands-references-browser.py).

When an exact command has arguments, its suggestion stays fixed while the
arguments are edited. Selecting an `@` reference cannot change `/task` into
a different command under the pointer. The separate feature search remains
available for browsing all permitted entries.
