# Reviewed external calendar delivery and subscriptions

Open **Integrations → Calendar → External calendars & subscription links**.
These controls apply to native channel schedules. They are available only to a
current channel administrator who also administers the workspace. Google and
Microsoft connections must belong to that same account and be enabled, tested
at their current revision, and authorized for calendar writes. Use the customer's
own OAuth application and consent in **Integrations → Sync**; FreeCollab does not
supply a shared client, tenant, secret or provider SSO.

## Review an external action

Choose a connection, the current event and the action. Enter external attendee
email addresses one per line for publication. The preview uses the authoritative
event revision, including its supported bounded series and occurrence exceptions.
It lists the submitted recipients for publication. Cancellation and personal
response require a prior provider read and display that fenced provider schedule
and roster. Approving the disclosure queues a
separate action; **queued** is a receipt, not confirmation that the provider has
completed it. The integration worker runs the queue when `SYNC_ENABLED=true`.

* **Publish or update and send invitations** creates or conditionally updates the
  provider event. The submitted list replaces its external attendee list. Empty
  means remove everyone; removed recipients may receive cancellation notices.
  Google uses `sendUpdates=all`. Graph organizer event creation/update uses its
  normal invitation/update behavior. Do not use this action merely to read RSVP.
* **Read provider status and responses** reads the current provider event and
  the external attendee statuses. It sends no invitation or response. Local
  channel-member RSVP remains independent of the provider account's responses.
* **Respond as my provider account** accepts, tentatively accepts or declines
  only for the connected provider account when it is an attendee. It never sets
  arbitrary invitees' replies. Google patches only its `self` attendee with
  `attendeesOmitted=true` and `sendUpdates=all`. Graph invokes `accept`,
  `tentativelyAccept` or `decline` with `sendResponse=true`.
* **Cancel the provider event and notify attendees** requires organizer authority.
  Google conditionally deletes with `sendUpdates=all`; Graph invokes `cancel`.
  This queue action itself does not remove the local schedule. An enabled
  two-way sync connection can subsequently apply its normal remote-deletion
  policy; its channel notice explains that deletions can travel both ways.
* **Find an uncertain provider event without resending** searches only the
  selected calendar for the existing deterministic synchronization identity. It
  performs reads and reports a result; it does not resend an invitation.

Normal background synchronization sends no invitations. Both providers expose
attendee-bearing events as read-only there. Adding native channel attendees does
not silently invite their email addresses externally. Review an explicit action
whenever provider scheduling messages are intended.

## Versions, uncertainty and limits

The queue retains the exact source revision, event revision, publication revision
and approved effect. Every dispatch and each subsequent vendor HTTP request
repeats current account/session, workspace, channel, provider-grant and
content-mode checks. Provider writes use current
conditional versions; recurrence versions include the master and instances.
A provider conflict stops the action so it can be read and reviewed again.
Source reconnects do not silently migrate an existing publication to a different
grant: inspect the provider and the mapping before reconfiguring that source.

Explicit request identities return the same receipt after a lost acknowledgment.
A definite provider `401` before any acknowledged mutation permits one refresh
and retry after the rotated token has been saved and authority checked again.
After an acknowledged subwrite, a later rejection (including `401` or `412`)
does not replay the operation. Network ambiguity, partially applied series,
server failures after possible effects and expired running claims become **uncertain**, without
an automatic repeat. Read/reconcile and check the provider first; a further
mutating action requires an explicit uncertainty acknowledgment and fresh review.
Revocation prevents subsequent effects; an action accepted by the provider before
revocation cannot be recalled by deleting the local queue row. A revocation that
becomes visible after one vendor response stops later requests in the same
multi-resource series operation; it cannot recall an already in-flight request.

Finite daily, weekly and monthly rules and supported moved/cancelled exceptions
are mapped, not flattened. Unsupported recurrence, per-instance attendee changes,
complex reminders and time zones stop explicitly. Master schedule and exception
edits may require two separately reviewed operations because vendor APIs do not
offer a transaction spanning those resources. Graph cannot restore a removed
occurrence through its documented API; use a new series. Graph response/cancel
actions receive current read fences and `If-Match`, but live vendor enforcement
of atomic conditional action semantics has not been certified. Deleting the
local event does not implicitly notify external attendees; cancel the provider
copy deliberately before local deletion or directly in the provider.

Each action accepts at most 50 unique explicit external email addresses. This is
not an address-book import, outbound email delivery service, account-wide calendar
scanner, conferencing provider or guarantee of recipients' email delivery.

## Public subscription links

Select the lifetime (up to 90 days), explicitly approve public disclosure and
confirm the FreeCollab password plus MFA/recovery code when enabled. The default
feed reveals only **Busy** and the occupied times. Titles require a separate
unchecked opt-in. Descriptions, locations, external attendee addresses, internal
attendee identities and reminder details never enter the feed.

Copy the generated URL immediately: it is shown once and acts as a bearer key.
Add it to an external calendar as a URL subscription. Each channel can have at
most ten active links. Each fetch checks current publisher account status,
authorization version, workspace/channel administrator membership and native
channel mode before producing content or returning `304`. Expired and revoked
links return `404`. Password/MFA-confirmed **Revoke subscription** stops future
fetches; external calendars control their polling interval and can retain copies
they previously received. Individual logout does not revoke a deliberately
durable subscription; revoke its link or change account security authorization.

The feed expands the accessible calendar from 30 days before each fetch through
365 days after it, with at most 1,000 series and 1,000 total occurrences. It emits
RFC-folded, escaped ICS with feed-specific opaque event UIDs. Larger calendars
fail explicitly rather than return a misleading partial schedule. Responses use
`private, no-store` and `Referrer-Policy: no-referrer`. The application redacts
capability paths in access logs; operators must preserve equivalent redaction in
their reverse proxy and must not log full subscription URLs.

## Privacy and storage

Encrypted Matrix schedules remain inside verified device-encrypted events. They
cannot enter the native provider queue or public feed, including while a secure
channel is preparing. No remote calendar grant decrypts channel content. Secure
users can still explicitly export a decrypted ICS file locally using the
existing device controls.

External addresses and provider responses are sealed at rest in purpose-bound
records. They are visible only through the current authorized grant owner's
delivery panel and are not federated, included in public feeds or copied into
internal attendee metadata. Terminal jobs scrub their encrypted effect payload
and original session claim metadata. The worker keeps a verified session digest,
never a bearer JWT. Subscription storage keeps only a random token's hash.
Account closure revokes subscription links, cancels and scrubs jobs and removes
cached provider responses without contacting a provider. Account exports contain
only metadata; they exclude subscription hashes, session authority, provider
credentials, encrypted job content and cached external recipient data. Local
erasure cannot remove copies already accepted by external providers.

## Validation

```sh
cd backend
.venv/bin/python -m pytest tests/test_calendar_delivery_013.py tests/test_provider_calendar_013.py tests/test_remote_providers.py tests/test_remote_sync.py -q
cd ..
CHROMIUM_PATH=/usr/bin/chromium backend/.venv/bin/python frontend/tests/calendar-delivery-browser.py
```

These use disposable databases, ephemeral keys and owned synthetic Google/Graph
HTTPS servers with real CONNECT tunnelling, public-host validation, certificate
trust, TLS SNI, conditional requests and vendor JSON formats. Chromium drives
the real review/consent/queue UI and worker and reads/revokes actual ICS links.
They demonstrate implementation behavior. No live customer Google/Microsoft
tenant, real invitation delivery, provider app certification or independent
security audit is claimed.
