# Channel meetings

Open a channel, choose **Work → Meetings**, then **Start channel meeting** or
**Join meeting**. Choose whether to start with a camera. The browser asks for
microphone/camera permission before the native participant joins. Use the
microphone, camera and screen-sharing controls during the call. Up to **six
admitted browser devices** participate in a meeting.

Calls use browser WebRTC mesh: every admitted device connects to the other
admitted devices. **DTLS-SRTP encrypts media between browsers.** Recording is
an explicit, separate all-participant choice described below; it starts only
after the disclosed policy has been approved by every admitted device.

Ordinary channels authenticate meeting participants through the native API.
SDP/ICE routing metadata is encrypted at rest under the installation content-key
ring. Native sign-in and customer SSO restrictions are checked for every poll
and write. This native signaling authority is distinct from Matrix fingerprints.

Secure channels use `freecollab-matrix-meeting/1`: actual verified Matrix device
identities carry encrypted proposals, admission, SDP/ICE, lease heartbeats,
captions and recording consent. This also works between installations sharing
a negotiated encrypted Matrix room. Native IDs are never treated as remote
identities. Clients require the event's exact verified device keys, room,
connection and current participant roster, and recheck current revocations
before applying a batch. They do not send secure signaling to the native mesh
API as a fallback. The existing browser WebRTC engine carries actual media.
A six-device limit, two-hour proposal expiry and 35-second host/device heartbeat
lease bound each encrypted mesh session. Only the exact host device can admit,
remove or end it. Guest invitations and the operator SFU are native-channel
flows; an encrypted room does not silently downgrade to either.

No third-party relay is selected automatically. A participant can always copy
what their endpoint receives or use another recording tool; application consent
controls cannot prevent that. Current device trust and channel permissions are
required to reopen encrypted recordings, including their proof authors.

## Native membership and host controls

Any current channel member can start a native meeting. The native API allows only
one live meeting per channel. Other current channel members may join. The person
who starts it is the host and controls guest admission and participant removal.
Matrix proposals use the separate verified-device protocol described above;
that protocol does not enforce a single live proposal per channel.

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

**Closing workspace tools preserves a member's active call in the floating meeting dock.** Microphone, camera and screen-share controls remain available there. The host leaving a browser mesh meeting ends it for everyone. Guests still use their dedicated call page. Reloading a page stops local capture; choose Leave and then Join to obtain a fresh device lease.
Polite application clients close peer connections and stop captured tracks when
leaving, being removed, losing access, or when the host ends the meeting. Already
received media cannot be recalled from a participant's device.

## Native meeting guests

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

## Native signaling bounds and retention

These SQL signaling/retention limits apply to native browser mesh meetings.
Matrix signaling instead uses its bounded verified event window, opaque index,
two-hour proposal expiry and 35-second device lease. Its client polls 2.5 seconds
after each completed request; Matrix ciphertext and recipient-held copies follow
the homeserver/device lifecycle rather than the native signaling cleanup below.

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

## Native meeting API

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


## Larger rooms: your LiveKit infrastructure

The browser mesh retains its six-device limit. Larger rooms use the actual
`livekit-client` SDK, LiveKit JWT grants and its RoomService Twirp protocol. They
do not expand the mesh. The operator supplies its own LiveKit deployment:

```dotenv
MEETINGS_SFU_PROVIDER=livekit
MEETINGS_SFU_URL=wss://meetings.example.org
MEETINGS_SFU_API_URL=https://meetings.example.org
MEETINGS_SFU_API_KEY=<your-server-api-key>
MEETINGS_SFU_API_SECRET=<your-server-secret-at-least-32-characters>
MEETINGS_SFU_MAX_PARTICIPANTS=100
```

Run `python -m app.services.sfu_worker` with the same database and settings as the
API. It checks original login expiry/revocation, auth version, customer SSO scope
and current channel membership every five seconds independently of browser
polls, then removes invalid/idle devices through RoomService. Joins fail closed
if its database heartbeat is older than 15 seconds. Participants send heartbeats
every five seconds; leases idle for 20 seconds are removed. Provider/network
failures are retried by the worker; a disconnected provider cannot guarantee
immediate removal. Monitor the worker and SFU together. Client connectivity
checks also stop local capture when API authorization fails.

Room grants last 60 seconds and allow publication/subscription only in the exact
room; they grant neither room administration nor data publication. Tokens are
kept in memory. Existing LiveKit connections are explicitly removed by the
worker; JWT expiry alone does not disconnect an established SFU connection.
A single authorization worker admits at most **2,000 outstanding devices across
all rooms**, independently of the per-room setting. This is an explicit safety
bound, not a capacity benchmark. The persisted oldest-first queue rotates after
every check, including failed removal attempts. Every outstanding device must
have been checked within 15 seconds. Joins and heartbeats fail closed when a
check is stale, a revocation remains unconfirmed, the three-second scan budget
is exceeded, or the global device bound is exceeded; a fresh worker heartbeat
alone cannot advertise readiness. Different rooms serialize new admissions
against this global bound.

Rooms have a two-hour application lease, survive a host leaving, and can be
ended by the host or channel administrator. The host can remove a participant;
a removed account cannot rejoin that room with another sign-in. Browser reload
requires fresh explicit device capture and admission. No provider credentials
are sent to the browser. Larger rooms currently accept workspace members;
call-only guest admission remains in six-device browser meetings.

**DTLS-SRTP terminates at the customer's SFU. The SFU can access media.**
This adapter does not advertise insertable-stream or Matrix E2EE for calls.
Use the product's Matrix channels and secure clips for end-to-end encrypted
asynchronous content. In production both configured LiveKit origins require
TLS; loopback HTTP/WS is allowed only in test/development. Provision TURN,
certificates, firewall media ports, capacity and monitoring in your own
infrastructure. The configured participant limit is an application bound, not
a throughput benchmark or a capacity guarantee.

## Consented automatic recording and continuous captions

The persistent meeting dock includes **Recording and live captions**. A host may
choose **Set up automatic recording**, or check **Ask participants to record
automatically after consent** before starting a native meeting. Nothing records
by default. The policy identifies audio/video, spoken language, captions,
transcript retention, and any exact automatic AI recipient before people agree.
Every admitted device must explicitly consent. The recording starts itself after
the last approval. Pause, resume, stop/save and withdrawal stay in the dock while
users work in other tools. Guest consent uses the existing native call capability
and grants no workspace or file access.

The recorder mixes only already-admitted local/remote tracks in Web Audio and
optionally draws camera/shared-screen tiles to a local canvas. It does not open
another camera/microphone or stop call devices. MediaRecorder rotates parts at
five minutes or approximately 48 MiB, subject to the 50 MiB attachment ceiling.
A roster change or withdrawn consent invalidates the current native generation,
discards its unfinished segment and cancels observed in-flight publication.
Paused capture records no new audio. The final stop obtains an idempotent,
clip-bound publication receipt and waits for the final part to enter the device's
private vault before closing the call. It drains in-flight and partial caption
windows for at most six seconds before taking the transcript snapshot. Caption
times follow accepted active audio; manual pause preserves pre-pause speech.
Revocation, roster change and sign-out abort immediately without draining. If
recognition cannot finish in its bound, the saved part explicitly marks its
transcript incomplete and does not queue automatic AI notes. Page unload/sign-out discards active
capture rather than publishing under an expired identity.

A slow original stop response is preserved after the six-second local receipt
deadline. Its exact capture, generation and part ID are reconciled against the
retained draft, current account and consent before publication; it cannot become
a receipt for another part. Automatic reconciliation waits at most 30 seconds.
Leaving waits up to eight seconds for the actual publication queue after local
retention. If publication is still pending, the floating **Private recording
drafts** controls remain available after the call, with retry, download and
discard. The notice clears when its final draft is published or discarded.
It uses the same isolated transient storage in sample/embedded sessions.

If browser storage itself fails, the app does not claim a private draft was
saved. It keeps the available Blob in the current controls, offers an explicit
download/discard, and reports that failure before completing an ordinary leave.
Keep that window open until those bytes have been handled. Signing out clears
that temporary Blob; an observed revoked capture grant still blocks publication.

Completed parts first enter account-isolated AES-GCM IndexedDB storage with a
nonextractable key. Demo and embedded memory sessions use their own transient
private state and never borrow the standalone account's vault. At most two captured parts may await upload; further capture
pauses instead of growing storage without bound. **Private recording drafts** in
Clips supports retry, downloading one's private copy, or discard. Retry rechecks
current membership, consent generation, exact clip ID and receipt expiry. It
cannot resurrect a purged clip: retention preserves a consumed receipt tombstone.
Capture-time content mode is immutable. Enabling encryption invalidates older
capture grants; a stored encrypted recording never falls back to native
plaintext upload after a mode change. Unknown legacy modes deny publication,
and manual clips also retain their capture-time mode. Native file metadata
commits reauthorize after private blob storage; rejected
unpublished blobs are deleted. An already stored published part remains subject
to the file and clip retention policy. A demo's smaller per-upload/session quota
still applies and can require stopping a short part and retrying or downloading
its private copy.

For secure native calls, encrypted file metadata carries an opaque native consent
receipt tied to the actual verified recording device. This proves authenticated
native participant consent, not a claim that the server read Megolm consent.
Encrypted Matrix meetings instead carry the exact verified proposal, request,
per-device join and consent events, and host publication event inside each
recording's encrypted metadata. Recipients validate that frontier before listing
or decrypting the file. Capture transaction IDs have a dedicated prefix and the
SDK's actual encrypted HTTP send boundary requires a live authorization callback
on every retry, including live caption transactions. A queued caption cannot
bypass current recorder, audience, mode, roster or consent checks; replay after
reload without its live callback fails closed. Observed withdrawal aborts queued publication; unsigned or stale
proofs are quarantined. There is no plaintext media/transcript fallback.
Cross-server consent is causal to events a device has received, not a globally
atomic erasure mechanism. Offline or malicious recipients can retain prior copies.

**Generate continuous captions on this device** loads actual pinned Whisper tiny
ONNX q8 through the browser's WASM worker, then transcribes bounded five-second
mixed-audio windows continuously. Select detection or an offered spoken language.
The default model weights use immutable revision
`ff4177021cc41f7db950912b73ea4fdf7d01d8e7` from
`onnx-community/whisper-tiny`; audio never leaves the device for ASR. Executable
WASM/loader files come from the application release. Operators may set
`MEETING_CAPTION_MODEL_BASE=/whisper-models/` and a corresponding
`MEETING_CAPTION_MODEL` for a prepared same-origin mirror; arbitrary remote
model URLs are rejected. The worker warms the model before recording so its
first download does not silently drop audio. Slow/unsupported devices receive an
error and pause capture; no remote transcription service is selected.

Native caption lines appear in the dock only for admitted participants. Before
secure captions can be enabled, the immutable request explicitly discloses
**all encrypted channel members, including people outside this call** as their
recipients. Captions default off; every admitted device consents to that exact
audience. An old or unknown audience is not reused. Native-channel live
captions are encrypted in a bounded RAM lane and expire after 45 seconds; they
are not written as a server transcript. Secure lines are encrypted Matrix events
with best-effort redaction after 45 seconds; homeserver retention, disconnected
clients and recipient copies can outlive that request. **Save the transcript with
each recording part** separately attaches text and timed captions to durable clips.
Recognition can make mistakes. Browser/worker tests and fake media establish the
capture protocol and worker bounds; actual model inference requires the separate
local-ASR acceptance command below.

## Automatic private meeting assistant

For a native-channel recording, the host may choose one configured and consented
model profile under **Automatic private meeting notes**. The setup shows its exact
recipient, model and protocol, discloses transcript sources and possible API
charges, and requires a separate host approval before the all-participant vote.
A model/profile revision or participant change invalidates that grant. There is
no implicit provider or silent fallback.

After a real part and transcript are durably saved, the client queues one bounded
`summarize` run for that exact clip. Server-derived request identity prevents two
client retries from billing two runs for the same capture generation and clip.
The grant permits at most 24 parts within seven days, one step per part, up to
8192 output tokens, and remains subject to workspace token/cost budgets. The
worker reauthorizes the capture, profile, membership and immutable source before
each provider attempt, after its response and before approval. Host/participant
AI withdrawal cancels further disclosure; guests retain a revoke-only capability.
Results are private proposals in **Models and AI work** until explicitly shared
or applied. A failed summary does not lose the saved clip; its private pending
entry can retry or keep the clip without automatic notes.

Secure meeting transcripts use the inline reviewed AI helper on the device with
fresh recipient/decrypted-content approval. They never enter the native server's
plaintext background queue. Meeting metadata and clip transcripts also expose
READ assistance in their existing context; these controls do not invoke a model
without review.

## Asynchronous clips and local transcripts

In **Work → Meetings**, record a microphone note, camera clip or screen
walkthrough after confirming recording permission. Browser `MediaRecorder`
captures actual devices; screen capture uses the browser's separate picker and
adds microphone audio. This separate clip composer starts only after an explicit click. Stop explicitly or at
five minutes/approximately 48 MiB. Preview before publishing, write a title,
optionally add/review a transcript, then publish. The existing 50 MiB attachment
limit and channel file authorization apply. Clips have native audio/video
playback, accessible transcript text and WebVTT timed captions. Editing the
transcript removes old timed captions to avoid mismatched subtitles.

**Transcribe on this device** runs pinned Whisper tiny ONNX q8 inference in a
dedicated browser worker through Transformers.js. Explicit consent permits
downloading the pinned model revision from the configured source; the executable
ONNX loader and WASM are served from this application’s own release assets;
the model may use browser cache. Audio/transcripts are never sent to Hugging Face
or an ASR service. Downloads require browser network/CSP access, WebAssembly and
sufficient memory. Cancellation terminates the worker. Recognition may be slow
on small devices and can make mistakes; review before publishing. Local model
loading failure produces an error, never a generated placeholder transcript.

Native clip title/transcript/captions are encrypted at rest with the existing
server content-key ring; native media follows the product's native file-storage
policy. In secure channels the existing Matrix attachment implementation
encrypts recording bytes on the device and verifies integrity on download.
The title, attachment reference, transcript and captions are inside a Matrix
encrypted event. FreeCollab's clip API accepts only an authenticated opaque
event reference and rejects all plaintext secure clip fields. Clients verify
room, event type, author, clip ID and file ownership before rendering. Server ASR
is not used for secure clips.

Clip entries are immutable and limited to 200 per channel. Authors or channel
administrators can remove entries; uploaded files remain governed by file
retention/deletion controls. Removing an index entry does not erase already
received media or encrypted room history. The UI makes this distinction explicit.

Protocol/authorization verification: `cd backend && .venv/bin/python -m pytest
tests/test_clips.py tests/test_sfu.py`. The SFU protocol tests use an explicit
local Twirp fixture and are distinct from actual LiveKit browser media tests.


For a completely self-hosted model source, prepare verified official files before
building the frontend:

```sh
python scripts/prepare-whisper-model.py --directory frontend/public/whisper-models
VITE_WHISPER_MODEL_BASE_PATH=/whisper-models/ npm --prefix frontend run build
```

The mirror option accepts only a same-origin absolute path. It preserves the
exact pinned repository/revision path structure. The preparer verifies the
official immutable Git blob IDs and Git LFS SHA-256 values, plus release-pinned
digests for both ONNX weight files, and writes a manifest. Keep the model card
and manifest with the hosted artifacts. Generated model bytes are optional
operator build assets, not committed source. This also permits local inference
where browser policy blocks the public model source. No TLS verification needs
to be disabled.

To create AI meeting notes, open **AI Copilot**, choose an explicitly configured
customer provider and opt in, select the reviewed **Clip transcript** context,
and request a summary, decisions or next steps. Only selected transcript text
(up to 12,000 characters per clip) is sent through the existing bounded provider
protocol, with citations bound to the immutable clip and current membership.
Secure clips require the additional per-request decrypted-content disclosure;
that deliberate copy leaves E2EE. Those secure exchanges remain device-only
and are not saved as server plaintext/history. The opt-in automatic native
meeting assistant described above is a separate, bounded authorization; secure recordings
continue to require foreground decrypted-content review.

Executed local browser verification can be reproduced with
`LIVEKIT_TEST_BINARY=/path/to/livekit-server backend/.venv/bin/python
frontend/tests/media-browser.py`. It uses seven real Chromium transports with
synthetic devices and an actual private LiveKit server. For actual local model
inference, add `CLIPS_TEST_LOCAL_ASR=1` and optionally
`CLIPS_TEST_MODEL_DIRECTORY=/path/to/prepared-mirror`. Public browser downloads
remain subject to the browser's normal certificate trust and network policy;
the mirror proof uses verified official weights served by the private fixture.


## Upgrade behavior for call authorization

Version 0.11 adds an encrypted, independent verified-claim snapshot for every
authenticated mesh/SFU device. It preserves the original subject, issue/expiry
times, auth version/methods, workspace/provider scope, and native/embed child
markers. Deleting a native/embed restriction sidecar cannot silently upgrade a
meeting capability to a broader session. Source logout, provider revision
change, membership removal, or a missing meeting snapshot revokes the call.

In-progress 0.10 calls have no such snapshot and therefore end on upgrade.
Start a new meeting and explicitly enable devices again; no unrestricted
authority is inferred for legacy call rows. Call cleanup, retained call erasure
and account closure also erase these private snapshots. Their token hashes and
encrypted claims are excluded from workspace exports. Call-only guests retain
their existing admission capability and inherit the current host's verified
authorization.

## Focused completion verification

`tests/test_media_capture.py` covers actual consent/API/storage boundaries,
including normal HTTP withdrawal committing while private blob storage is held,
rejected blob cleanup, stopped receipts, purge tombstones, immutable encryption
mode, exact caption audiences and committing rate-limiter reauthorization.
`tests/test_ai_meeting_runtime.py` covers actual durable recording→clip→private
runtime integration with one synthetic provider response, canonical duplicate
suppression and revocation before dispatch/approval.

The real public sample flow is reproducible with:

```sh
BACKEND_PYTHON=backend/.venv/bin/python \
MEETING_CAPTURE_PROOF_REPORT=/tmp/freecollab-sample-capture.json \
backend/.venv/bin/python frontend/tests/meeting-capture-browser.py
```

It uses the actual product Chat/meeting/clip components and API with owned
short-lived sample data and synthetic browser devices. It checks sample member
credentials, automatic 15-second recording parts, native publication/playback,
persistent minimized call controls and retained-account storage isolation.
`frontend/tests/continuous-captions-browser.py` separately runs the real pinned
Whisper WASM over known generated speech, partial drains, pause/resume, timeout,
stop cancellation and the bounded audio backlog. No third-party audio upload is
used for transcription.

`frontend/tests/encrypted-work-browser.py --media-only` uses two real owned
Synapse installations, verified Rust browser crypto, actual encrypted signaling,
WebRTC and MediaRecorder, indexed admission/recording proofs, peer decryption
and adverse caption publication/retry cases. On managed Chromium that disallows
non-proxied UDP, use the fixture's explicit private TURN-over-TCP relay. The
browser policy is preserved; this is not an external provider throughput claim.
