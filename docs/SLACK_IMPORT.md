# Import a Slack archive

A workspace owner or administrator using a personal account opens **Integrations → Import from Slack**. Upload a Slack standard ZIP export containing root `users.json`, `channels.json`, and channel directories with daily `YYYY-MM-DD.json` message lists. Export permissions and included conversation types remain controlled by Slack and your organization's export rights. FreeCollab does not log into Slack or bypass those controls.

The preview shows channel/message counts, historical names, bundled file counts and missing file counts. It creates no channels or messages. Choose at most 25 channels, set a new channel prefix, and optionally map historical Slack identities to active non-guest members of the current workspace. Mapping explicitly adds those people to their imported channel rosters. It never creates accounts, enrolls outsiders or borrows another workspace's members. Unmapped people remain historical labels.

Import creates **new native channels**; it never merges into existing or Matrix encrypted channels. Message bodies are encrypted at rest using the installation's existing native content encryption, which the server can decrypt. This is not a retroactive Matrix E2EE migration. Every historical message visibly states its original Slack label and timestamp; its FreeCollab author is the importing administrator, so a Slack name cannot impersonate a current account or gain account edit rights. Original source timestamps and thread parent relationships are preserved. System membership/edit events are skipped. A missing thread parent remains a top-level message. Included replies must refer to a root; nested and cyclic thread graphs are rejected. Slack mrkdwn is retained as text rather than translated into executable HTML.

Standard Slack exports usually contain remote file references rather than binary files. FreeCollab **never fetches those URLs**. For a portable archive, include an authorized binary at `files/SLACK_FILE_ID`, with the original message file metadata `id`, `name`, and `mimetype`. Supported bundled files become normal channel files. Files absent from the ZIP receive a visible missing-attachment marker. Their private Slack URLs are not imported or requested. This version imports channel text, reply relationships and optionally bundled files; it does not import Slack credentials, applications, reactions, memberships as accounts, edit history or unexported conversations.

## Bounds and recovery

The importer accepts at most 10 MB compressed, 50 MB expanded, 2,500 ZIP entries, 100 source channels, 10,000 historical messages, and 200 bundled file references totaling at most 50 MB. Source rosters have at most 5,000 identities; explicit mappings have at most 250 entries. Unsafe paths, symlinks, encrypted entries, excessive compression, duplicate paths/JSON keys/message timestamps and malformed metadata are rejected without extracting files. Imported files follow current native file and DLP checks. Historical imports do not emit mention, webhook, email or workflow events.

Preview bytes are sealed in the account-security envelope, expire after one hour, and are erased by the default integration worker's cleanup batch. Discard erases them immediately. A successful import erases the stored ZIP and retains only its digest, selected-operation digest, result and source IDs. Account identity erasure immediately clears the account’s private ZIP copies and removes unreferenced jobs, including expired/discarded previews; provenance still referenced by shared messages remains under normal shared retention. A repeated confirmation of the same preview and choices returns the same result; different choices cannot replay a completed import. Administrators can delete imported channels/messages/files through existing retention and hold controls. Retained original archives/backups remain the operator's responsibility.

SQL channel/message/file metadata commit together. Newly owned immutable blobs are tracked before each write, including an uncertain provider acknowledgement, and erased on rollback. Every independent erasure is attempted even if another fails; a cleanup outage logs only the opaque owned blob ID for operator reconciliation and does not replace the original import error. Conditional-write collisions never erase an existing object. Current account/session and workspace administrator authority, and every mapped person’s active non-guest workspace membership, are rechecked after upload and file I/O. No original installation data or keys are replaced.

```sh
cd backend
.venv/bin/python -m pytest tests/test_slack_import.py -q
cd ..
CHROMIUM_PATH=/usr/bin/chromium backend/.venv/bin/python frontend/tests/import-library-browser.py
```
