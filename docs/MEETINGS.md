# Channel meetings

Open a channel, choose **Work → Meetings**, then **Start channel meeting** or
**Join meeting**. Choose whether to start with a camera. The browser asks for
microphone/camera permission before the native participant joins. Use the
microphone, camera and screen-sharing controls during the call. Up to **six
admitted browser devices** participate in a meeting.

Calls use the browser's native WebRTC mesh: every admitted device connects to
the other devices. Media is not uploaded to the FreeCollab HTTP API, stored in
its database, transcribed, or recorded by the application. **DTLS-SRTP encrypts
WebRTC media between browsers.** The signalling service authenticates the
participant list and relays SDP/ICE routing information. This trust model is
separate from Matrix's verified-device message encryption; there is no claim
that a Matrix device fingerprint verifies a meeting peer.

Existing encrypted channels retain their message encryption. Meetings do not
submit channel titles, messages, documents or media to the signalling API. Call
metadata contains channel/host identifiers, participant names and timing.
Signalling includes network routing metadata and is encrypted at rest using the
retained server content-key ring. Participants can capture their own screen or
record locally; the application cannot prevent another endpoint doing that.

## Membership and host controls

Any current channel member can start a meeting. Only one live meeting is allowed
per channel. Other current channel members may join. The person who starts it is
the host and controls guest admission and participant removal.

Native participants are bound to the exact FreeCollab login used when joining.
Every signalling poll/write checks that login's current expiry/revocation,
account security version, block state, workspace/channel membership and customer
SSO provider revision/scope. A separate meeting capability cannot bypass those
checks. Host authorization is also checked for guests and other members: removing
or revoking the host's access stops subsequent signalling for everyone. Removing
a native participant excludes the account from that meeting, including a newly
issued login for that account.

One native device may join per sign-in session at a time; a duplicate active join
returns a clear conflict. An explicit leave and rejoin creates a new peer identity,
so old SDP/ICE and peer connections are not reused. After a connection error,
media stops locally; once the network recovers, choose **Leave meeting** before
joining again to release the previous admission.

**Closing workspace tools leaves the meeting. The host leaving ends it for
everyone.** Moving between tabs inside workspace tools preserves the call.
Polite application clients close peer connections and stop captured tracks when
leaving, being removed, losing access, or when the host ends the meeting. Already
received media cannot be recalled from a participant's device.

## Guests

1. The host chooses **Create guest invitation** and privately shares the link.
2. The guest enters a display name and chooses **Ask to join meeting**.
3. The host admits the waiting guest.
4. The guest explicitly enables microphone/camera after admission.

Invitations expire after **15 minutes**, work once, and contain the invitation
secret in a URL fragment. The app removes that secret from the visible address.
Only its hash is persisted. The guest receives a different, call-only capability
and cannot use it to log in or read the workspace, chat or files. A pending guest
cannot receive participant details, ICE configuration or signals. Its camera and
microphone remain off until the guest has been admitted and enables them.

A guest capability is kept in that tab's session storage. After a refresh,
choose **Request to rejoin meeting**: this exchanges the old capability once for
a new peer identity and requires fresh host admission before media can start.
Explicit leave clears the stored capability. Removed/expired guests cannot
resume. A new guest invitation starts a new admission request. Guest display names are unverified
and always labelled as guest identities. Host removal revokes that participant's
call access.

## HTTPS, networks and relays

Microphone, camera and screen capture require a supported secure browser context:
HTTPS in deployments, or loopback development. Permission refusal, missing
hardware and unavailable screen capture produce visible errors. The application
stops captured tracks on failed setup and stale/closed sessions.

There is **no automatic third-party STUN/TURN service**. Local/private-network
browsers can connect using host candidates. Separate networks and restrictive
firewalls commonly require an operator-managed TURN relay. Configure it in the
private server environment:

```dotenv
# Optional public STUN endpoints; empty is the default.
MEETINGS_ICE_SERVERS=["stun:stun.your-domain.example:3478"]
# TURN REST/HMAC credentials use the relay's retained shared secret.
MEETINGS_TURN_URLS=["turns:turn.your-domain.example:5349?transport=tcp"]
MEETINGS_TURN_SECRET=your-private-relay-shared-secret
```

Use your own relay's actual settings. The shared secret remains server-side.
Admitted participants receive HMAC-derived temporary TURN credentials lasting
slightly longer than the two-hour maximum call; pending guests do not receive
these credentials. STUN/TURN connectivity is a separate deployment acceptance
check. A configured URL alone does not establish that a relay is reachable or
that two external networks can exchange media.

## Bounds and retention

- Meeting duration: at most **two hours**.
- Devices: at most **six admitted**, with **24 total participant records** per call.
- Guest invitations: at most **20 per meeting**, each one-use and 15 minutes.
- Signalling: at most **120 messages per participant per minute** and **3,000
  retained active messages per meeting**; SDP/candidate shapes and sizes are
  explicitly bounded.
- Polling: 750 ms after each completed request, one request at a time. Poll pages
  contain at most 100 recipient-specific signals and do not skip page overflow.
- Signalling older than five minutes is ineligible for reads and is removed in
  bounded maintenance batches. Ended/expired meeting metadata becomes eligible
  for cleanup after one day, during later meeting creation. Backups may retain
  older encrypted records.

Only the intended current participant can read its signalling. Expired/revoked
senders are filtered again before delivery. Server-side capabilities are stored
as SHA-256 digests, not reusable raw credentials. The media protocol and network
relay are separate from native file/calendar background synchronization.

## API

All paths below omit the reverse proxy's `/api` prefix. Native controls use the
normal authenticated account token. Bodies for controls without fields are `{}`.

| Operation | Endpoint |
| --- | --- |
| List / start | `GET` / `POST /channels/{channel_id}/meetings` |
| Join as current native member | `POST /meetings/{meeting_id}/join` |
| Create guest invitation | `POST /meetings/{meeting_id}/invites` |
| Admit / remove | `POST /meetings/{meeting_id}/participants/{participant_id}/{admit\|remove}` |
| Host ends call | `POST /meetings/{meeting_id}/end` |
| Exchange invitation | `POST /meeting-access/{meeting_id}/guest` with `invitation_token` and `name` |
| Poll | `GET /meeting-access/{meeting_id}/poll?after={cursor}` |
| Signal | `POST /meeting-access/{meeting_id}/signal` with recipient and one SDP description or ICE candidate |
| Guest enables media | `POST /meeting-access/{meeting_id}/ready` |
| Guest requests fresh admission | `POST /meeting-access/{meeting_id}/resume` |
| Leave | `POST /meeting-access/{meeting_id}/leave` |

The last five operations use `X-Meeting-Token`, a call-only device capability.
They do not accept that credential as a native account login. Private responses
are `Cache-Control: no-store`; service workers must not cache meeting endpoints.

## Verification

```bash
(cd backend && .venv/bin/python -m pytest tests/test_meetings.py -q)
backend/.venv/bin/python frontend/tests/meetings-browser.py
```

The browser harness starts its own API/database/keys and an isolated Vite entry
using the production meeting components. Chromium supplies its native synthetic
camera/microphone test devices. The proof observes actual peer connections,
inbound RTP/video frames, native capture/control lifecycle, guest admission and
track cleanup. This is distinct from a public-network TURN deployment test or an
independent security audit. See [VALIDATION.md](VALIDATION.md) for executed results.

The managed cloud Chromium policy disables non-proxied UDP. For that environment,
the executed proof used an explicit temporary **loopback TURN/TCP** relay and the
application's operator TURN/HMAC configuration; browser policy remained in place.
To reproduce this mode, install `coturn` as a local test tool and set
`MEETINGS_TEST_TURN_BINARY=/path/to/turnserver` for the browser command. The harness
creates a temporary relay configuration with random credentials, binds only
loopback, and removes its own relay/data/keys afterward. It does not contact an
external relay, change browser policy, or change the application's empty relay
defaults.
