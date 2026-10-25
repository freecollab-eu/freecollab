# Private personal calendar

My day includes a private calendar separate from shared channel calendars.
Create, edit, delete and export your own timed/all-day events, with a time zone
and finite daily/weekly/monthly repeat rules. All-day end dates are exclusive;
turning an ordinary same-day appointment into an all-day event chooses the next
day as its end. Refresh/reopen reads actual persisted events.

`GET/POST /workspaces/{workspace_id}/personal-calendar` and
`DELETE .../{event_id}?expected_revision=N` are authenticated, actor/workspace
scoped and uncached. A private ID cannot be edited or transferred by another
member, administrator or guest. New reference contexts must be a currently
accessible channel in the same workspace. Existing historical references can be
retained when changing an appointment after channel access is removed, without
reading or granting access to the former resource. Selected mention IDs use the
shared writing controls and their existing target validation.

`PersonalCalendarEvent` is an additive table. The event payload is authenticated
and encrypted with actor, workspace and event purpose; this is native encryption
at rest, not a Matrix-shared event. A User → authentication state → workspace write fence serializes writes with
identity closure and membership changes. Rate limiting commits before this fence;
the fence stays held through the private-event commit. Revision compare-and-swap rejects stale edits/deletes, and
same actor/ID/body create retries are idempotent. Each private workspace calendar
retains at most 1,000 events. Closing a dirty editor uses the same unsaved-work
protection as other writing surfaces; in-flight saves block departure.

ICS export is generated on the device. Events are not published to shared
availability, invitations, provider sync or a notification-delivery service.
Those deliberate shared-channel/provider workflows retain their separate review
and consent. Private entries are included only in their owner's authorized
workspace data export; actor erasure removes that actor's private events across
workspaces. Export/erasure helpers never commit their caller's transaction.

Validation is in `backend/tests/test_content_completion.py`,
`frontend/tests/content-completion.test.mjs` and the real API/component host
`frontend/tests/content-completion-browser.py`. The browser proof records its
scope and exact source hashes; it does not substitute for production deployment
or full Chat integration validation.

Run the component-host proof from the repository root with the backend virtual
environment, Playwright and `/usr/bin/chromium` installed. CI can choose its own
report directory; the JSON report, screenshot and bounded failure logs stay there:

```sh
BACKEND_PYTHON="$PWD/backend/.venv/bin/python" \
CONTENT_COMPLETION_REPORT="$PWD/proof-results/content-completion/report.json" \
backend/.venv/bin/python frontend/tests/content-completion-browser.py
```

When editing an event whose reference context is channel A while the current chat
is channel B, the editor preserves A. It shows an authorized **Continue in A**
action rather than silently rebinding references or broadening the common input
scope. The action rechecks actual membership/workspace and the exact retained
editor/source lifetime. The host retains My day and its draft while moving to A;
only then do the shared `/` and `@` controls use A's matching provider. A revoked
original channel is identified explicitly and existing historical references stay
attached to the private event.
