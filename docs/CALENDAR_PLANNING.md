# Calendar planning

Open **Integrations → Calendar** in a current channel. Existing single events,
ICS, provider links, immutable revisions and Matrix encryption remain available.

## Recurring events and exceptions

Choose daily, weekly or monthly repeat, an interval from 1–30 and a finite count
from 1–366. Weekly repeats use the original weekday. Monthly repeats use the
original day of month; months without that day are skipped rather than moved to
another date. Timed repeats preserve the original IANA local wall time across
clock changes. Nonexistent spring times are skipped and do not consume the
count; autumn overlaps use the first instant, except that the explicitly supplied
initial instant is retained exactly. Timed duration remains elapsed duration.

Each series can contain at most 100 occurrence exceptions. **Move this occurrence**
changes one start/end while retaining its original identity. **Cancel this
occurrence** removes that occurrence from expansion. The editor shows up to 12
occurrences within the next year; the full bounded series is retained. Moving an
exception into a selected search window includes it even if its original date is
outside that window. Changing dates or a series rule requires resolving any now-invalid exception
identities. The explicit **Reset all occurrence changes** action restores moved
and cancelled dates only when the edited series is saved.

The immutable calendar revision contains all repeat/exception details. Native
records use existing encryption at rest. Secure records keep these details
inside verified Matrix events and expand locally after trusted-device decryption.
The API never accepts plaintext fields for a secure channel.

ICS imports support `FREQ=DAILY|WEEKLY|MONTHLY`, `INTERVAL`, a required `COUNT`, and
`EXDATE`. Other rules such as `UNTIL`, `BYDAY`, `RDATE` or `RECURRENCE-ID` must be
expanded by the originating calendar first. Export expands supported series into
at most 1,000 independent events, preserving their actual instants and overrides;
it does not establish external invitations or ongoing synchronization. Existing
Google/Outlook compose links copy the selected event's initial dates, so use ICS
for complete recurring exports. Google/Microsoft remote sync also maps supported
finite series and exceptions without sending invitations. Explicit reviewed
provider invitations, account RSVP and revocable native ICS subscriptions have
their own controls; see [CALENDAR_DELIVERY.md](CALENDAR_DELIVERY.md).

## Invitations and responses

The event organizer or a current channel administrator explicitly selects up to
50 active current channel members under **Attendees & responses**. No address
book lookup or external email invitations occur from these internal controls. Invitation membership, person
identifiers, response and revision are server metadata in both channel modes;
titles, dates and descriptions remain encrypted in secure channels.

An invited person can accept, tentatively accept or decline only for their own
account and the current reviewed event revision. The response applies to the
whole series. Changing the event resets responses to **needs action** and creates
a generic in-app invitation notice, following the recipient's system/in-app
preferences. Removing a person or deleting the event hides their old invitation
notifications. Every operation repeats current channel/workspace/provider/session
checks; channel access does not override a revoked signed scope. Notification
text contains no event title, date or description. It opens the authenticated
channel; review the current event there.

## Shared time

**Find a shared time** takes 1–25 selected current channel members, a window up to
31 days and a duration from 15 minutes to eight hours. It considers only the
selected channel's visible events created by those people or inviting them
without a decline. Unanswered or tentative invitations block time conservatively.
It does not inspect private calendars, other channels or external provider
calendars, and therefore does not promise complete personal free/busy information.
The interface explicitly asks users to confirm their other commitments.

For native calendars, the API scans at most 1,000 series and returns merged busy
intervals and up to 100 candidate starts on 15-minute UTC boundaries, with no
event titles, descriptions or person-specific busy reasons. For secure calendars,
the device uses currently authorized roster metadata and locally decrypted fields
from at most 100 series; schedules never enter this API. Unreadable events,
changed revisions and removed members stop calculation. The interface displays
the first 12 candidates. No implicit working-hours or time-zone assumptions are
made beyond the input's explicitly shown local zone.

The additive `CalendarAttendee` table participates in the native retention erase
allowlist and channel export. Provider-origin revision updates reset invitation
responses in the same transaction. Server erasure does not remove Matrix copies
already received by devices or independent external calendar copies.

## Validation

```sh
cd backend
.venv/bin/python -m pytest tests/test_calendar_planning.py tests/test_document_calendar_integrations.py -q
cd ../frontend
node --test tests/document-calendar.test.mjs
cd ..
CHROMIUM_PATH=/usr/bin/chromium backend/.venv/bin/python frontend/tests/calendar-planning-browser.py
```

These fixtures use disposable accounts, ephemeral keys and loopback services.
They do not send live customer calendar invitations or imply provider acceptance.
