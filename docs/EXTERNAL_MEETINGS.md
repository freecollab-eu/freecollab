# Customer-owned meeting providers

FreeCollab explicitly schedules Google Meet, Microsoft Teams and Zoom meetings
using the customer's own OAuth application and provider account. This is separate
from FreeCollab WebRTC calls and from workspace single sign-on. No provider
account, app secret or licence is supplied by FreeCollab.

An operator enables `EXTERNAL_MEETINGS_ENABLED=true`; the default is false.
Provider privacy, recording, retention and encryption policies apply. There is
no automatic provider discovery, external AI use or background meeting worker.

## Configuration

Open Work → Meetings or the channel calendar. A personal account that currently
administers both the workspace and channel can configure a connection, with its
current password and MFA/recovery code if enabled.

1. Choose the customer app, provider, calendar ID for Meet/Teams, and Microsoft
   tenant ID for Teams. Enter the customer's client secret privately.
2. Explicitly consent to meeting creation for this channel.
3. Authorize the customer account at the provider.
4. Test the saved connection before scheduling.

Register the exact callback in the customer's OAuth app:

    https://YOUR-FREECOLLAB-HOST/api/external-meetings/oauth/callback

OAuth uses state, browser binding, PKCE, ten-minute expiry and single-use exchange.
It is bound to the initiating native session, account security version,
connection revision and current administrator grant. Editing configuration
discards old delegated tokens and requires new authorization/test.

Secrets, access/refresh tokens and PKCE verifiers are encrypted with the retained
private account key. They are omitted from public responses and browser storage.
A matching calendar OAuth connection from the same channel can be reused after
separate meeting consent and testing. File/Drive tokens are never repurposed.
Source revision/permission changes invalidate that meeting grant.

| Provider | Required customer permission and protocol |
| --- | --- |
| Google Meet | Google Calendar events API with `https://www.googleapis.com/auth/calendar.events`, or an already broader calendar grant. Creation includes a Meet conference request. The read-only test verifies selected calendar events access; explicit creation confirms actual Meet availability. |
| Microsoft Teams | Microsoft Graph selected-calendar event API with delegated `Calendars.ReadWrite` and `teamsForBusiness` available on that calendar. No separate `OnlineMeetings.ReadWrite` grant is used. |
| Zoom | Customer OAuth app with `meeting:write:meeting`, `meeting:read:meeting`, `meeting:read:list_meetings` and `meeting:delete:meeting`; equivalent legacy `meeting:write` plus `meeting:read` is accepted. Configure approved scopes in the customer app. |

A provider may require administrator consent, an approved app or a particular
licence. The local fixture does not establish acceptance for a live tenant.

## Explicit disclosure and calendars

Choose a tested connection, enter a title and timed start/end, and confirm the
disclosure checkbox. Only that title, timing and connected provider account are
used. Channel messages/documents are never read; no attendee invitations or
addresses are sent. Meet/Teams create provider calendar events. Zoom creation
requests a waiting room, join-before-host off and automatic recording off;
provider/account policy can impose its own settings.

In server-encrypted channels, a successful link creates one immutable native
calendar entry, encrypted at rest. Some providers prepare links asynchronously;
Retrieve provider meeting checks the existing request without creating another.

In Matrix-encrypted channels, no server-created calendar entry or durable
server-readable title/time/join link is stored. The explicitly requested provider
result is returned to the initiating client, which writes a verified encrypted
Matrix calendar event and attaches only its validated opaque reference. Durable
reconciliation data contains encrypted remote IDs/ETags and hashes, not the title,
time or joining URL. Unlock verified calendar access to finish that step. If it
fails, explicitly retrieve the existing meeting and retry; it is not recreated.

Joining links are validated against the selected provider's HTTPS hostnames and
opened with `noopener noreferrer`. Zoom links may contain a meeting passcode.

## Idempotency and conflicts

A random request ID is retained for retries of the same form details. The server
binds it to the author, channel, connection and normalized request hash. An
identical retry returns the existing result; changed details with the same ID
conflict. An uncertainty marker is committed before a possible external write.

After a timeout, recover the existing request instead of creating another.
Recovery performs reads only, using Google's deterministic event ID, Microsoft's
transaction ID or Zoom's FreeCollab agenda reference. Teams/Zoom searches are
bounded to three pages of 100 records. An absent or ambiguous match remains
uncertain and requires provider-side review. An uncertain Zoom create is never
blindly repeated.

Cancellation checks the local calendar revision, reads the provider's current
meeting and checks its fingerprint. Google/Teams delete also sends If-Match.
Zoom does not expose an ETag delete precondition here: its fingerprint check is
a preflight check, and an independent provider edit between that read and delete
cannot be made atomic by FreeCollab.
Provider-side edits detected by these checks cause a conflict and do not overwrite local events or delete
the changed remote meeting. Review the provider before retrying. Repeating
cancellation can finish local cleanup after a remote delete already succeeded.
Native cleanup appends a tombstone; encrypted cleanup happens on the client and
can be retried independently.

Disconnect deletes local delegated credentials, disables the connection and
advances its revision. Existing remote meetings and reused source calendars
remain. Revoke the app in the provider console to end its provider grant.
Membership, administrator, account, native session and source revision changes
are rechecked before later operations.

## Network and bounds

Fixed provider HTTPS endpoints use the existing transport: fresh public DNS,
pinned addresses, original TLS hostname verification, configured proxy/CA trust,
private-destination refusal, no followed redirects, bounded response bodies and
total operation time. Credentials never enter returned links, native JWT fields,
logs or arbitrary pagination hosts. Error messages contain typed status codes,
not provider bodies.

Limits: 12 connections/workspace, 20 OAuth attempts/connection, 500 external
meeting records/channel, 100 listed meetings, 24-hour meetings, and bounded
per-account create/recover/cancel rates. Backups can retain encrypted records.
Retain the existing private account key and content-key ring with backups.

## Integration and verification

Native API routes (without the reverse proxy /api prefix):

- GET/POST /workspaces/{id}/external-meetings/connections
- PUT /workspaces/{id}/external-meetings/connections/{connection}
- POST .../{connection}/oauth/start, /test, /disconnect
- GET /external-meetings/oauth/callback (browser-bound callback)
- GET/POST /channels/{id}/external-meetings
- POST /channels/{id}/external-meetings/{meeting}/recover, /cancel, /calendar-link

`ExternalMeetingsPanel({workspaceId,channelId,isAdmin?,onCalendarChanged?})`
contains customer configuration and explicit scheduling. Calendar writes call the
optional callback and dispatch `freecollab:calendar-changed` with `{channelId}`.
Owned UI has English/Swedish catalogs and clears sensitive state on auth changes.

    (cd backend && .venv/bin/python -m pytest tests/test_external_meetings.py tests/test_external_meeting_protocols.py -q)
    backend/.venv/bin/python frontend/tests/external-meetings-browser.py

The fixture uses actual hostname-verified TLS and a private CONNECT proxy for all
three OAuth/meeting protocols. The Chromium proof uses explicitly local native
session and customer OAuth consent fixtures, with no live account or credential.
It exercises real adapters, native calendar writes, uncertainty recovery,
conditional cancellation, mobile layout and Swedish UI. Secure storage boundaries
are tested in the backend and use the existing verified Matrix calendar client.
This is not a live provider/Matrix acceptance test or an independent audit.

