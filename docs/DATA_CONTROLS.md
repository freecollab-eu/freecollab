# Data controls

Open **Workspace tools → Data & retention**. A personal workspace owner can export channels they currently belong to and manage native content retention. Workspace ownership never grants access to an unassigned private channel. Customer SSO scoped sessions and managed identities cannot use these account-level controls.

## Private exports

1. Select one to twenty assigned channels, enter the personal account password and current authenticator/recovery code when enabled, then acknowledge that native content will be readable in the downloaded ZIP.
2. Create a private export. A durable bounded job assembles `export.json` and immutable file bytes under `files/<opaque file id>`. The archive includes authorized channel metadata, member profiles, messages, file/version records, decisions, linked documents/calendar records, tasks/projects, collaborative document updates/comments and workflow history. Workflow configuration is included only for channel administrators.
3. Reauthenticate to download within thirty minutes, using the same native session that requested the job. Current workspace/channel grants, account security version, revocation and session expiry are checked during processing and download. There are no public download URLs or query-string credentials.

The server stores the finished archive with authenticated XChaCha20-Poly1305 secretstream encryption, a job-specific derived key and private filesystem permissions. ZIP assembly uses an anonymous temporary file descriptor so a killed build cannot leave a named plaintext archive. The downloaded ZIP itself is readable. Download temporary files are removed after serving or failure, with bounded cleanup after a crashed download. Jobs stop at 10,000 records / 128 MiB including member profiles; select fewer channels for larger workspaces. Credentials, authentication/session tables, SCIM/OAuth tokens, push endpoints/keys, meeting SDP/ICE, server paths and device encryption keys are excluded by an explicit field allowlist. Meetings contribute only safe metadata and participant counts, without guest names, invitations or routing credentials.

Matrix channels export authorized opaque resource/event references and metadata. This server cannot decrypt Matrix history, export client-held keys or promise erasure of homeserver/device history. Export decrypted Matrix material from an explicitly trusted client using its own provider facilities.

## Retention and permanent local erasure

Retention defaults to **disabled**, **preservation hold enabled**, and 365 days. Choose 7–3,650 days. Automatic deletion remains off: every permanent purge requires a new dry run, current password/factor verification, an exact typed `PURGE <workspace id>` confirmation and acknowledgment of separate backup/provider/device retention.

Select native channels in which you are also a channel administrator. Pause synchronization connections and workflow definitions, and finish/cancel nonterminal workflow runs before previewing. The preview shows eligible record counts and per-channel effects without exposing unassigned channels. It expires after ten minutes and is single-use, bound to the same session, policy revision and exact candidate record snapshots. Changes to content, dependencies, policy or authority stop the job and require another preview.

A batch removes only eligible native channel content and known dependents. It preserves thread roots with newer replies, complete newer file/document/task histories, and structured links from retained tasks/decisions. Shared document children are erased together only when the entire native document history is old. Only terminal workflow run history is eligible; paused definitions remain. Ended/expired meeting routing records can be erased together only when every child and its expiry/activity timestamps are old; active meetings remain. Unknown foreign-key dependencies block erasure instead of cascading arbitrarily.

File metadata removal records a durable garbage queue before committing. Physical bytes are then unlinked by validated immutable file identity. A job remains `erasing_files` until that succeeds; retries complete this already-authorized physical cleanup even after a browser logout. The UI never reports completion while scheduled file bytes remain. Offline message retry receipts retain a tombstone so reconnecting devices cannot resurrect erased messages. Paused sync mappings/cursors are cleared without requesting a remote deletion; resuming sync can import a provider copy again.

Existing backups, provider copies, Matrix homeservers and other users’ devices have separate retention policies. Operators must define backup expiry and controlled restore procedures outside this product. Restoring a backup can restore previously erased content unless the operator reapplies applicable erasure records.

## Erasing an account identity

**Review account dependencies** lists workspace/channel handovers and pending data jobs that must be resolved first. The last active workspace owner, channel administrator or installation administrator cannot erase their identity until another active person holds that role. An otherwise empty personal home can be removed automatically.

Enter a fresh password/factor, the exact phrase `ERASE MY ACCOUNT`, and acknowledge retained shared authorship and separate backup/device/provider copies. Secure-channel access revocation runs before native erasure; a provider failure stops the action. The server then removes login/profile/private settings, authentication and device credentials, identity links and personal outbox material, disables the person's integration/workflow grants, removes memberships and invalidates prior sessions. Their authored shared work retains a blocked, anonymous author stub so other members’ history and references remain valid. This is permanent identity erasure, not a claim that all authored text or every external copy has disappeared.

## Processing and verification

`services.data_control.run_pending(session, limit=1)` processes bounded leased jobs through the shared worker. The panel can also process a specific authorized job immediately; no worker is required for the review flow. Failed exports can be retried after fresh authorization. Failed metadata purges require a new preview; committed file cleanup has a dedicated safe retry.

`DATA_CONTROLS_ENABLED=false` disables workspace control routes and pauses the shared worker's data queue, including pending physical cleanup. Re-enable it to resume that queue. A directly invoked trusted processing service still completes already-committed file cleanup, while refusing new metadata export/purge work. Account identity erasure remains available as a separate privacy action. Keep workers paused during backup restore and review applicable erasure records before resuming them.

Verification uses private SQLite databases with foreign-key enforcement and private fixture file storage: `backend/.venv/bin/python -m pytest backend/tests/test_data_controls.py -q`. The browser proof `frontend/tests/data-controls-browser.py` runs disposable local API/Vite/Chromium processes and exercises an actual private archive download, preview/confirmation, row and file-byte erasure, responsive UI and identity erasure. No production account, data, provider or public service is required.
