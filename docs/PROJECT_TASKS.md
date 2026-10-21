# Projects and tasks

Every channel has its own project boards. Open **Workspace tools → Tasks** to create a project, add tasks and move them between **To do**, **In progress** and **Done**. A task can have a responsible channel member, a due date, a priority, up to 40 checklist items and references to channel files and decisions. Search, **Assigned to me** and **Show archived tasks** filter the board locally. The panel uses the account's appearance variables and a single-column layout on small screens.

Open a linked file to download its bytes through the channel's existing native or encrypted file provider. Open a linked decision to read its current revision inside the panel. Both actions fetch current channel records before opening anything, recheck the signed-in account after asynchronous work and report unavailable or revoked resources. Closing decision details restores keyboard focus to the task link.

Changes are persisted as immutable revisions. Task history shows previous content and completion state. A save includes the revision that the editor read; a concurrent change returns HTTP 409 without replacing the other person's work. The panel retains an unsaved form after a conflict. Choosing **Reload current revisions** explicitly closes that form and discards its unsaved edits, as the panel warns, so reopening the task starts from its current content. Archive and reopen create revisions too. An archived project must be reopened before its tasks can change.

## Access and limits

Current workspace and channel membership are required for every read and write. Workspace administration alone does not grant access to a private channel. All current channel members can collaborate on content. Only the item's creator or a channel administrator can archive or reopen it. Removed or blocked members lose access, including through previously issued customer SSO sessions. Writers recheck the verified native session, account security version, current provider scope and membership after acquiring the channel lock and after Matrix event validation.

There are at most 20 projects and 1,000 tasks per channel, including archived items, and 1,000 revisions per item. Task listing is paginated in batches of at most 200. Task fields allow at most 20 file references and 20 decision references. Native saves validate that the assignee is an active current member and that linked resources belong to this channel; deleted remote file references are rejected.

## Content protection

Native channel content is encrypted with the existing server content key ring and the channel ID as its cryptographic context. This protects content at rest; the application server decrypts it for authorized members.

For a channel preparing or using Matrix end-to-end encryption, the server rejects native project and task fields. The browser encrypts a `eu.freecollab.product.project` or `eu.freecollab.product.task` event and submits only its opaque event reference. The server validates the encrypted event's channel and sender, and stores routing, revision, creator and archive metadata. Titles, descriptions, status, assignees, dates, checklist text and linked resource IDs remain inside the encrypted event. The client checks the decrypted event's room, sender, type, item ID, parent project, revision and archive state before displaying its fields. Failed decryption or a mismatched envelope stays unavailable; there is no plaintext fallback.

The application does not convert existing native task history into end-to-end encrypted history. Enabling Matrix encryption requires a new empty channel. Current members still need a verified device with the appropriate room keys to read encrypted revisions. Opaque events sent immediately before a rejected save may remain unreferenced in Matrix.

## HTTP contract

All responses use `Cache-Control: no-store`. The channel prefix is `/channels/{channel_id}/projects`.

| Method and path | Behavior |
| --- | --- |
| `GET /` | List up to 20 projects, including archived projects. |
| `POST /` | Create or append a project revision. |
| `GET /{project_id}/tasks?offset=0&limit=200` | Return `{items, next_offset}`. |
| `POST /{project_id}/tasks` | Create or append a task revision. |
| `GET /{project_id}/revisions` | Latest project revision first. |
| `GET /{project_id}/tasks/{task_id}/revisions` | Latest task revision first. |

A write contains `id`, `expected_revision` (zero for creation), `archived`, and exactly one of `fields` or `matrix_event_id`. Unknown fields are rejected. Successful records include item and parent IDs, channel ID, original creator/time, current revision and reviser/time, archive state, content mode, and the native fields or Matrix reference. Lists and histories enforce the same current membership checks as writes.

The browser module `frontend/src/lib/project-tasks-api.ts` performs field validation and handles both content modes. `ProjectTasksPanel({workspaceId, channelId, isAdmin?})` renders inside the workspace tools dialog.

## Workflow and lifecycle integration

`services.project_tasks.create_workflow_task(session, channel_id, actor_id, project_id, title, idempotency_key, description="")` creates a native task without committing. The caller owns the transaction. The helper requires a current personal-account channel administrator who is also a workspace owner or administrator, a writable native channel and an active project. Its actor/project-scoped idempotency marker prevents a replay from duplicating or overwriting a task that a person has since edited. An unrelated task occupying the deterministic ID produces a conflict. It deliberately does not enqueue another workflow event.

Ordinary task saves queue `task_updated` in the same transaction. Native events contain only the validated stage as workflow metadata; encrypted tasks send no content metadata. Channel activity hooks also receive an opaque revision identifier.

Storage tables are `projectboard`, `taskcard` and `projecttaskrevision`. The latter uses `(kind, item_id, revision)` as its unique revision identity; `kind` is `project` or `task`. Native revision payloads contain `{id, kind, project_id, revision, archived, fields}` and use the raw channel ID as the encryption context. Data-control exports must recheck channel membership before decrypting native revisions. Retention removes a task only when all its revisions precede the cutoff, and removes a project only when its own and all child task revisions precede the cutoff. Delete revisions before task rows, then project rows. Matrix references are not decrypted by a server export.

## Verification

`backend/tests/test_project_tasks.py` covers persisted native history, encryption at rest, revision conflicts, current membership and private-channel isolation, scoped SSO, assignee and link checks, archive permissions, secure payload fences, Matrix-reference validation boundaries, logout and account-version races, quotas and workflow idempotency.

`frontend/tests/project-tasks.test.mjs` exercises the actual TypeScript API module's validation, encrypted wire shape, decrypted-envelope checks, authentication changes and pagination. `frontend/tests/project-tasks-browser.py` is a self-cleaning private browser proof covering two collaborating accounts, real linked file and decision records, concurrent edits, checklist and stage changes, archive/reopen, history, 320 px layout and focus containment. The release proof report records whether this browser scenario has run; merely having the harness is not a passing browser result.
