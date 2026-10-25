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

### Document organization and current channel details

Native owner exports include verified stack titles, placements, document pairs, saved-message routing references
and current channel name/description metadata; secure channel detail exports
contain the opaque Matrix event reference only. Native stack payloads are verified
against their immutable creation generation before export. A failed binding stops
the export rather than emitting a ciphertext dump.

Retention includes shared-page placements and both sides of saved relations in
the complete-history age check. A recent move or relation protects the relevant
page history. A recent saved-message reference protects the source and its thread parent chain; old references are allowlisted dependents. Eligible old dependents are deleted before pages/stacks, without
orphaned foreign keys. An old empty stack is eligible only if no placement remains.
Current channel details are retained with the channel identity, as the channel's
name and membership are, and are not erased by a time-based content purge.
Edit the current channel profile to change those values. Its revision remains
monotonic while the channel exists, so retention cannot revive an old editor's
compare-and-set token. Account closure keeps the usual disabled opaque author
stub for shared authored records, including these new records; it does not erase
other participants' shared content.

## Collaboration module privacy

Exports include only the requester's private calendar and AI runs/usage within
the authorized workspace/selection. Cross-channel AI context is omitted unless
every formal source channel is selected and still authorized. Collection pointers
are resolved under current resource access. Captures, live Slack mappings,
foreground app delivery and encrypted-work discovery contribute bounded metadata;
credentials, payload copies, recording bytes and decrypted Matrix content are not
exported through those metadata projections. Private progress and consent rows
are limited to the requester. Nested feature records count toward the archive cap.

Retention stops while an AI run uses a selected channel as either its target or
a formal RAG source, a live Slack bridge or queued writer remains active, or a
recording/caption capture has not stopped. A foreground disclosure still within
its bounded delivery window also blocks purge; an interrupted receipt older than
90 seconds becomes permanently uncertain and is never resent automatically.

Purging a native source invalidates matching copied AI context, including private
runs targeting another conversation, and anonymizes its numeric usage receipt.
Newer unrelated private runs remain. AI usage is never refunded by content or
account erasure: outstanding reservations become conservatively charged unknown
usage, and financial counts remain after account or empty personal-workspace
erasure with identity/workspace links removed. No prompt or output remains in
those receipts.

Source erasure removes derived local semantic caches and the configured internal
OpenSearch vector mirror under the channel write fence. A configured mirror
erasure failure leaves the purge incomplete. Collection pointers disappear with
their source; retained collection members protect their containing collection.
Slack timestamp mappings retain permanent content-free tombstones, so signed
replays or fresh edits cannot reconstruct an erased native post after resume.
Consumed recording publication receipts likewise retain erased-clip tombstones;
they are not reusable upload authority. Stopped capture metadata is eligible only
after every retained publication and AI authorization window is older than the
cutoff. No cleanup recalls a copy already disclosed to an external recipient.

Account closure additionally stops capture/Slack grants, wipes personal calendar,
AI keys/consents/results and private encrypted-work progress, and preserves only
shared opaque publication authority and anonymous financial/idempotency receipts.
The existing app-platform credential and private-draft cleanup remains part of
the same transaction.

Regression coverage: `backend/tests/test_collaboration_privacy.py` runs actual
owner export ZIPs, confirmation-bound purges and account closure with enforced
foreign keys, source-aware AI guards, nonrefundable anonymous usage, provider
failure handling and permanent capture/Slack erasure authority.
