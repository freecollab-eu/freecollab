# Channel workflows and approvals

Open **Workspace tools → Workflows** inside a channel. A personal account that is both a workspace administrator and a channel administrator can build a workflow from an event, optional conditions and up to five action cards. Saving, editing, pausing and cancelling require the current FreeCollab password and, when enabled, an authenticator or recovery code. A customer SSO session can read its channel and approve its assigned requests, but cannot grant automation privileges.

## Events and actions

Supported events are a manual start, native file upload/new version, shared-document creation, decision creation/revision and task creation/revision. Producers enqueue metadata in the same database transaction as the resource change. A manual start runs only the selected workflow. The creator condition can limit events to the administrator who saved the current revision. The editor supports native task status conditions and native decision status conditions (`proposed`, `accepted`, `superseded`). Decision producers forward only that validated status to the local workflow queue; it is not added to outgoing webhook payloads. Encrypted decision events retain no readable status condition.

Actions are an approval request, a generic in-app notification to selected current channel members, and creation of a To do task in an existing native project. There are no arbitrary URLs, scripts, email actions, file exports or external-provider destinations. Generated tasks do not trigger more workflows. A task action creates one task per run and does not overwrite subsequent user edits.

Each approval step has a quorum, a deadline, zero to three reminders and optional escalation recipients. Escalation makes the selected current members eligible after the final reminder. A rejection ends the run. Each actor can record one immutable choice per step; retries of that same choice are idempotent. Removed, blocked or deprovisioned members cannot vote, and their earlier votes do not count toward a later dispatch. An impossible quorum cancels the run explicitly.

## Encryption boundaries

Workflow names, action settings, task titles and routing choices are administrator configuration. They are encrypted at rest with the server message keyring and visible to current channel members. Do not put encrypted channel content in those fields.

The worker never reads Matrix message bodies or shared-document content. Encrypted channels support activity metadata, generic notifications and public approval choices. Optional approval notes are encrypted on the client as a Matrix event; the API stores an opaque event reference and validates the current sender and channel. Clients check the decrypted event's sender, run, step and choice before displaying a note. There is no plaintext note fallback. Native approval notes use server encryption at rest.

Content/status conditions, native file triggers and server-created task actions are unavailable in encrypted channels. Secure attachment uploads do not pass through the native file event source. Generic notification titles contain no workflow name, resource title or approval note. Existing notification preferences and current channel access apply. Changing encryption mode cancels incompatible queued work; native channel history must be handled by the channel's conversion safeguards.

## Current grants and durable processing

Every dispatch checks that the saving administrator is still an active personal account with both workspace and channel administrator access. Workers cannot borrow an installation owner's permissions. Writes lock the grant actor before workspace/channel rows, refresh membership and recheck request authentication after locks. Edits create immutable revisions, cancel earlier pending runs and require a fresh grant; pausing cancels pending runs without undoing completed actions.

Run `python -m app.services.integration_worker --interval 10` beside the API. `WORKFLOWS_ENABLED=true` enables the queue and worker; setting it false pauses new events and processing while keeping configuration and history readable. A queue with no definitions is idle. The durable worker claims bounded batches, recovers expired 60-second leases and retries failures at most five times with bounded backoff. Unique event identities, row claims and deterministic notice/task identities prevent duplicate effects after retry or concurrent claims. No action sends content outside FreeCollab.

Run states include queued, processing, waiting for approval, completed, rejected, cancelled, timed out and failed. The panel shows recent runs, per-step choices and activity, plus immutable configuration versions. The default history API returns 50 runs, supports a limit of 100 and a `before` timestamp. A channel permits at most 20 definitions and 2,000 retained runs. At capacity, enqueue trims up to 100 oldest terminal runs and their approval/activity metadata. If every retained run is active, new events fail atomically with HTTP 429 rather than silently disappearing. Archive or cancel active work before retrying. Resource files and tasks are not removed by history trimming.

## Producer contract

```python
from app.services.workflow import queue_event

queue_event(
    session, event_id, workspace_id, channel_id,
    kind="task_updated", resource_id=task.id,
    actor_id=actor.id, metadata={"status": "done"},
)
# The producer owns commit/rollback.
```

Kinds: `manual`, `file_uploaded`, `document_created`, `decision_updated`, `task_updated`. Event IDs must be stable for the same resource revision and at most 256 characters. Only the bounded `status` metadata field is accepted; Matrix channels discard it. Native file and decision producers may omit `actor_id`: the service resolves the author from same-channel resource metadata without opening content. Other producers should supply the current actor. The optional `workflow_id` argument restricts a manual event to one definition.

The worker entry point is `run_pending(session, limit=10, now=None)`. It owns claim/effect commits. The optional clock is intended for deterministic scheduler tests, not a public API.

## HTTP API

All paths begin `/workspaces/{workspace_id}/channels/{channel_id}/workflows` and require current channel membership. Responses are `Cache-Control: no-store`.

| Method | Suffix | Purpose |
| --- | --- | --- |
| GET / POST | empty | List definitions / create with password and optional MFA |
| PUT | `/{workflow_id}` | Save a new version with `expected_revision` |
| POST | `/{workflow_id}/disable` | Pause and cancel pending runs |
| GET | `/{workflow_id}/revisions` | Immutable configuration history |
| POST | `/{workflow_id}/run` | Queue one manual run |
| GET | `/runs/history` | Recent channel run history |
| GET | `/runs/{run_id}` | Current votes and bounded activity history |
| POST | `/runs/{run_id}/vote` | Record approve/reject and an optional note/reference |
| POST | `/runs/{run_id}/cancel` | Administrator cancellation with reauthentication |

Data exports must use current channel access. Retention may delete only terminal run history. Disabling a definition alone does not erase its immutable revisions or past actions. The separate data-control service blocks purge until configured automation is disabled and pending runs are cancelled.

## Verification

`backend/.venv/bin/python -m pytest backend/tests/test_workflows.py` covers native reauthentication/MFA, scoped SSO, workspace/channel role combinations, post-lock session revocation, producer rollback, event deduplication, concurrent worker claims, lease recovery, quorum and membership changes, bounded reminders/escalation/timeouts, immutable revisions, encryption boundaries and the real task-creation helper.

`CHROMIUM_PATH=/usr/bin/chromium backend/.venv/bin/python frontend/tests/workflows-browser.py` runs a private API, integration worker, Vite and Chromium against disposable native accounts, keys and SQLite storage. It exercises the visual builder, actual file trigger, approval, reminder dispatch, history, task creation and mobile layout. Its scheduler fixture advances only its own private run's timestamps; all effects are performed by the real worker. The proof deletes its processes, keys and database in `finally` and writes a sanitized report under `/workspace/.freecollab/proof-results/`.
