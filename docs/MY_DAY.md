# My day

Open **My day** in the sidebar, or **More channel tools → My day** on a narrow
screen. The personal view brings together assigned open tasks, the next seven
days of channel calendar occurrences, unread activity, message reminders and
unfinished saved follow-ups. It follows the account's language and appearance
settings and supports a 320 px viewport, keyboard focus and reduced motion.

**Your private calendar** in My day opens your separate persisted personal calendar.
Create, edit or delete timed/all-day events, choose finite recurrence and export
ICS. These events belong only to you in the selected workspace; channel members
and administrators do not inherit access. They are separate from the channel
agenda and shared free/busy calculation. See [PERSONAL_CALENDAR.md](PERSONAL_CALENDAR.md)
for storage, reference-context navigation and privacy boundaries.

The view covers the workspaces and channels accessible through the current
sign-in. A customer workspace SSO session stays inside that customer's scope;
organization administration does not grant private channel membership. Current
Connect grants, managed-account restrictions, organization SSO requirements,
blocked direct conversations and removed memberships apply to the aggregate.
The focused embedded channel interface does not expose a global My day entry.

Tasks use their current project and task revisions. Completed tasks, archived
projects/tasks, deleted calendar records, deleted native message targets and
completed saved items disappear from the next snapshot. Recurring calendars and
occurrence exceptions use the same expansion as the channel calendar. A channel
calendar occurrence is shown for context even if no individual attendee roster
has been created; it is not a claim that its viewer accepted an invitation.

## Encryption and freshness

`GET /my-day` returns a private, `no-store`, bounded snapshot rather than writing
a personal search index or a second content store. Native task and calendar
fields use their existing encrypted-at-rest source serializers. Secure tasks and
events remain opaque Matrix revision references on the server. The trusted
browser verifies the exact room, event type, sender, revision and item identity,
then decrypts and filters task assignments locally. Locked or unverifiable
revisions produce an explicit incomplete-view notice and no plaintext fallback.
Reminders, activity and saved follow-ups use generic labels and current source
references; their retained inbox bodies are not copied into the personal view.

Refresh clears the old view while current authorization is checked. The app
refreshes when returning to a visible tab and every minute while visible and
idle. Account/sign-in changes clear and close the view. Removing access prevents
future reads and actions; it cannot erase information a permitted user already
received or captured. My day is an online view and is not saved to the offline
library automatically.

The server scans at most 250 caller channel seats, 1,000 current tasks, 500
calendar records, 500 unread inbox records, 100 active reminders and 200 saved
references. The client resolves at most four encrypted records concurrently and
shows at most 100 calendar occurrences. Scan or resolution limits are disclosed;
open the original channels to inspect their full current lists. Unmapped legacy
channels without a workspace cannot be placed in this workspace-based view.

## Actions and navigation

Completing a task creates a normal revision through its original channel task
endpoint, including optimistic revision conflicts and current resource grants.
Reminder snooze/completion, saved completion and marking activity read reuse
their existing personal endpoints. A failed action clears stale rows and offers
a fresh reload. The dashboard grants no new editing capability.

Source links fetch the current authorized channel, choose its current viewer
workspace and wait for that workspace's channel list before navigating. Task
links select their project and focus the referenced current task; calendar links
open that channel's calendar. A stale or unavailable target produces an error.
Device notifications can use the same navigation path to open the meetings
panel. Such a hint never admits someone into a meeting or enables media.

## Verification

```sh
cd backend
.venv/bin/python -m pytest tests/test_my_day.py -q
cd ..
CHROMIUM_PATH=/usr/bin/chromium backend/.venv/bin/python frontend/tests/my-day-browser.py
```

The API cases cover private/current assignment, cross-workspace filtering,
dangling seats, scoped SSO/provider revision revocation, archived/deleted sources,
opaque encrypted revisions and source-authorized completion. The isolated
full-app browser proof signs in through the real interface, opens a task in a
different workspace and its exact project, completes it, snoozes and completes a
reminder, completes a saved follow-up, opens the calendar, checks Swedish at
320 px and verifies refresh removes deleted content. Its disposable processes,
database, keys and browser profile do not touch the retained pilot environment.
