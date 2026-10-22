# Customer-owned native mobile notifications

> Standard alerts described here remain independent of the opt-in native media lifecycle. FreeCollab 0.13 adds customer-owned CallKit/PushKit and Android Telecom calling; see [NATIVE_CALLING.md](NATIVE_CALLING.md) for configuration, explicit consent, the SFU encryption boundary and platform verification limits. Standard notification payloads still do not authorize media admission.


Open **Mobile → Native mobile notifications** in a linked Android or iOS app.
The user explicitly enables device alerts and can separately opt into alerts when
a channel call starts. The OS permission and a short-lived delivered challenge
must both complete before the server activates a device. Browser sessions and
desktop child sessions cannot register native capabilities.

The shell includes an actual Android Firebase Messaging service and iOS
`UNUserNotificationCenter`/APNs bridge. Android registers notification channels and
requests `POST_NOTIFICATIONS` on supported versions. Firebase messaging auto-init
and default data collection are disabled in the manifest; explicit device opt-in
enables messaging registration, and unregistering disables it again. iOS requests normal alert
permission and uses the customer's signed push entitlement. Native bridge
methods check that the WebView still uses its configured HTTPS installation.
Android uses an application-owned guarded WebView selected before Capacitor
constructs its bridge. It refuses every `addJavascriptInterface` installation,
including Capacitor's legacy all-frame fallback. Native functionality requires
the modern main-frame-aware WebMessage listener; unsupported WebViews, explicit
legacy mode or listener failure show an update-WebView/system-browser screen.
Optional native HTTP/cookie Java-interface interception is deliberately unavailable.
The
iOS shell additionally checks the actual WK message frame and exact configured
HTTPS origin before forwarding any native bridge message; main-frame-only
JavaScript injection alone does not authorize an iframe message.

## Operator setup

Everything belongs to the installation operator. FreeCollab supplies no central
Firebase project, Apple account, identity provider, device service or push secret.
Native alerts default to disabled and fail closed if the selected adapter is not
configured. Both the API and default integration worker need the same settings,
database, account-security encryption key and retained versions.

Android:

1. Create an Android app in your own Firebase project for the build's application
   ID. Add its `google-services.json` privately at
   `clients/mobile/android/app/google-services.json`. It is ignored by Git.
2. Build/sign the native app normally. The pinned Firebase SDK dependency is
   present even in an unconfigured build; without project resources registration
   shows a configuration error and sends nothing.
3. Give a dedicated service account only the Firebase Messaging permissions
   required to send to this project. Store its complete service-account JSON in
   `NATIVE_PUSH_FCM_SERVICE_ACCOUNT`; set `NATIVE_PUSH_FCM_PROJECT_ID` to the same
   project ID. The adapter signs a short-lived RS256 assertion, obtains a token
   with only `firebase.messaging` scope and uses the FCM HTTP v1 send endpoint.
   Tokens and credentials never enter browser configuration or database logs.

Apple:

1. Register the build's bundle ID in your own Apple team, enable Push
   Notifications and sign with a matching provisioning profile.
2. Set the build's `APNS_ENVIRONMENT` to `development` for development APNs or
   `production` for distribution. `App/App.entitlements` uses this explicit build
   setting; a development default is present. Set the signing team/profile in
   your private build configuration.
3. Store the APNs P-256 `.p8` key as complete PEM in
   `NATIVE_PUSH_APNS_PRIVATE_KEY`. Set `NATIVE_PUSH_APNS_TEAM_ID`,
   `NATIVE_PUSH_APNS_KEY_ID` and the exact bundle ID in
   `NATIVE_PUSH_APNS_TOPIC`. Set `NATIVE_PUSH_APNS_SANDBOX=true` only for a
   development entitlement; it defaults to production APNs.
4. The adapter signs ES256 provider JWTs and requires HTTP/2 with verified TLS.
   It sends ordinary alert notifications, not VoIP/PushKit pushes.

Finally set `NATIVE_PUSH_ENABLED=true` and run the default integration worker:

```sh
cd backend
.venv/bin/python -m app.services.integration_worker
```

Compose forwards the settings through the existing shared API/worker environment.
Supply real secrets through the deployment's private secret mechanism. Do not put
service-account JSON, APNs keys or native signing profiles in a public repository,
`VITE_*` variables, screenshots or command-line arguments. No provider URL is
accepted from device registration or from credential JSON: OAuth/FCM/APNs
requests use fixed vendor HTTPS destinations through the existing pinned public
transport, proxy, fresh public-address checks, bounded responses, verified
hostname and no redirects. APNs explicitly negotiates HTTP/2; it has no HTTP/1
fallback.

## Privacy, revocation and incoming calls

Activity payloads contain only a generic activity type and a random delivery
reference. Call alerts contain only `incoming_call` and that reference. A device
confirmation also includes its one-use challenge and random registration ID.
Names, channel/workspace titles, message text, meeting IDs, invitation tokens,
authorization headers and encryption keys are absent. Apple/Google can still
observe push timing, device routing and the generic activity/call type.

Device capabilities and complete verified source claims are context-encrypted
at rest. Only the originating session digest persists. Each dispatch checks the
native mobile session and its original browser parent, signed expiry, account
version/block status, current customer provider revision/workspace scope,
notification preference, writable channel membership and current unread activity
or live meeting host authority. FCM repeats that check after its OAuth exchange
and before the send request, so a revocation committed during token exchange
stops the alert. Missing child/scope sidecars fail closed. Workspace-scoped sign-ins can enumerate and remove only their own exact-source
devices; an unrestricted account sign-in can manage all its devices. A scoped
sign-in cannot rebind a device belonging to another still-live source. After the
old source expires or logs out, a fresh delivered proof allows re-enrollment,
including for managed SSO-only users. Active
registration cannot be stolen by another account; after the previous session is
invalidated, a new account still needs a fresh delivered challenge before taking
ownership. A successful token rotation removes superseded devices belonging to
that exact mobile session/platform; other sessions remain independent.

Foreground/resume silently refreshes an already-consented device token only
while the OS permission is still granted. It never opens a new permission prompt.
Logging out clears the native pending alerts and unregisters the OS capability;
the server also invalidates dispatch through session checks. Removing a device
removes its queued references and capability. Explicit account identity erasure
also deletes every native device capability, encrypted source snapshot and
associated delivery before deleting notifications. Registration and erasure use
the same User → AccountSecurity lock order, so a concurrent registration cannot
leave a new private capability behind after closure. Permanent vendor expiration disables
the device; transient failures have at most five attempts. A lost provider
acknowledgement can result in duplicate generic alerts.

Call alerts cover mesh and configured LiveKit member rooms that started in the
last two minutes after device confirmation. Scans are bounded to 100 recent
rooms per transport/device pass, with a fair bounded device scan. They do not
invite guests or confer meeting admission. A removed participant, ended/expired
room, invalid host or revoked recipient cannot resolve an alert. An authenticated
click checks access again, opens the current permitted workspace/channel and
meeting controls, and leaves camera/microphone off until the user chooses to join.

Normal APNs/FCM alert delivery depends on platform permission, battery/network
policy and provider acceptance. Android force-stop and iOS notification settings
can prevent delivery. Standard alerts do not use PushKit/CallKit, full-screen
intent or a background media service. The separate opt-in
[NATIVE_CALLING.md](NATIVE_CALLING.md) feature supplies OS calling with native
LiveKit audio after explicit Answer; neither path guarantees ringing. An
already-delivered generic OS alert can remain after later server revocation;
its reference grants no access and fails current resolution.

Native pending storage holds at most ten validated opaque references/device
proofs in app-private OS storage. It stores no conversation contents, bearer
credential or channel catalog. Push does not change Matrix encryption or create
a server plaintext fallback.

## Verification

```sh
backend/.venv/bin/python -m pytest backend/tests/test_native_push.py backend/tests/test_push.py backend/tests/test_native_clients.py -q
CHROMIUM_PATH=/usr/bin/chromium backend/.venv/bin/python frontend/tests/native-push-browser.py
```

The backend proof performs real mobile PKCE handoff and session tracking, tests
one-use/racing device confirmation and revocation fences, generic dispatch,
call-end checks, provider expiration, token rotation and customer workspace
boundaries. It verifies actual FCM assertion/APNs signature structure and sends
APNs-format alerts through a synthetic TLS HTTP/2 receiver via a real CONNECT
tunnel. The browser proof uses the actual product/API with a synthetic native
bridge and provider delivery. Android compilation checks the real Java/Firebase
service. These checks do not prove delivery through a real Apple/Google account,
iOS compilation/signing or physical-device background behavior; operators must
perform that final smoke test on their signed customer builds.
