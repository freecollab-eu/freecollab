# Native clients

FreeCollab includes an Electron desktop client and real Capacitor Android/iOS
projects. They connect to a customer's FreeCollab installation. They do not
introduce a FreeCollab-hosted identity provider, file store or encryption service.

The desktop app lets its user choose an HTTPS installation on first launch.
Each installation has a separate persistent browser partition, including its
Matrix devices and local encrypted drafts. Use **FreeCollab → Choose another
installation** to switch. Removing an app profile also removes its local
encryption keys; retain the normal Matrix recovery key before removing a device.

The mobile projects are built for the HTTPS origin chosen by their operator.
The configured origin must serve this version of FreeCollab, including its native
sign-in API and frontend. The public default https://freecollab.eu is not evidence
that a production demo is currently deployed.

## Desktop builds

Node 24 and npm are required:

    cd clients/desktop
    npm ci
    npm test
    npm run dist

The build creates Linux AppImage/Deb, Windows NSIS or macOS DMG/Zip artifacts
under clients/desktop/dist, depending on the build operating system. Use npm start
for local desktop development. Both development and packaged clients require an
HTTPS deployment; they do not disable certificate verification.

The packaging wrapper uses umask 022 for distributable files, so a Deb installed
by root remains readable and executable by ordinary desktop users. This does not
change the app's runtime profile or key permissions.

The renderer uses Chromium's sandbox, context isolation, no Node integration,
no webview tag and normal web security. The narrowly scoped preload exposes
only native browser sign-in and cancellation to the current installation's main
frame. Its local setup window has a separate configuration-only bridge. Camera,
microphone, screen sharing and notifications require explicit permission;
screen capture requires a selected source. Off-origin navigation cannot load
into the privileged app window and opening an HTTPS link externally asks first.

A usable Chromium OS sandbox is required on Linux. Install the Deb package or
configure user namespaces according to the host's Chromium/Electron guidance.
Do not add --no-sandbox to a distributed app or normal desktop launcher.
The AppImage includes a small launcher that preserves Chromium's sandbox;
it does not silently disable it when user namespaces are unavailable.

No automatic update service is configured: a customer's distribution/signing
process decides which reviewed build users install.

## Android and iOS builds

Node 24, a Java 21 JDK and Android SDK platform/build-tools 36 are required for
Android. iOS builds require macOS, Xcode compatible with Capacitor 8, and the
iOS 15+ target supported by that Xcode version.

    cd clients/mobile
    npm ci
    FREECOLLAB_APP_URL=https://collab.example.com npm run sync
    npm run build:android

Open the native IDE projects with npm run android or npm run ios.
FREECOLLAB_APP_URL must be an HTTPS origin without credentials, path, query or
fragment. Its hostname is also written into iOS's WKAppBoundDomains.
The sync command copies the small offline page and synchronizes native projects.
The real product loads from the customer's origin, so normal web product updates
do not require rebuilding the native shell. Changing the permitted origin does.

Android disables cleartext traffic, mixed content, WebView debugging and system
backup of the app's private data. Camera and microphone permissions are requested
through the platform only when used. iOS declares their purpose and limits native
bridge navigation to its configured app-bound domain. Both projects register the
same custom native sign-in callback and keep the PKCE verifier in native memory.

The mobile shell includes opt-in native FCM HTTP v1 (Android) and APNs HTTP/2
(iOS) alerts, separate from Web/PWA push. Customers configure and sign their own
builds. Generic activity and incoming channel-call notifications can display in
the background; tapping reopens the app and current authorized call controls.
Joining and camera/microphone access still require a foreground user action.
There is no PushKit/CallKit integration, automatic answering, guaranteed ringing,
background call audio service or background recording. See [NATIVE_PUSH.md](NATIVE_PUSH.md).
Verify camera, microphone, notification delivery, file downloads and Matrix
recovery on target physical devices before rollout.

## Browser sign-in and encryption

Use **Sign in with browser** in a signed-out native app. This opens the
installation in the system browser, so Microsoft, Google and customer IdPs use
their normal supported browser flows. Complete ordinary sign-in and MFA there,
then explicitly approve **Connect this app**.

1. The native process generates random state and an S256 PKCE verifier.
2. Only the public challenge, callback and ten-minute request expiry enter the
   browser URL. Public routing survives the ordinary IdP redirect in session
   storage; passwords, access tokens and the verifier never enter that context.
3. Explicit approval issues a hashed, one-use connection code, valid for one
   minute and bound to the callback, challenge and current browser session.
4. Desktop receives that code on an ephemeral 127.0.0.1 HTTP listener. Mobile
   receives freecollab://native/complete. Both verify state before returning the
   code and verifier through the current installation's restricted native bridge.
5. The application exchanges the code for its own tracked, source-bound session.
   It preserves the browser's identity, authentication methods, original
   authentication time, hard expiry, MFA state and exact customer workspace scope.

The callback carries a PKCE-protected code and state, never a JWT. Mobile uses a
custom scheme rather than claiming to implement customer-owned verified universal
links. Another app can interfere with a custom-scheme callback, but interception
alone cannot exchange it without the verifier; restart sign-in if delivery fails.
The verifier is deliberately not restored after the app process is killed.

Signing out of or revoking the parent browser session also invalidates the native
session. Source expiry, security-version changes, customer provider revision and
membership changes are checked again by REST, Socket.IO, locked write authority
and the Matrix gateway. Signing out of the native session does not sign out the
browser. Native or embedded child sessions cannot create further native children.

Native sign-in never transfers the browser's Matrix recovery key, cross-signing
private keys, device identity or encryption unlock. Each native profile creates
and verifies its own device using the existing encrypted messaging flow.

| Endpoint | Purpose |
| --- | --- |
| POST /native-clients/connect | Authenticated, explicit browser approval; returns a one-use PKCE code. |
| POST /native-clients/exchange | Anonymous code/verifier/callback exchange; no reusable browser credential. |

The additive NativeClientGrant, NativeClientSession and NativeClientMatrixBinding
tables must be included in database backups. None contains a raw access token or
PKCE verifier. Keep the native Matrix marker when retaining its Matrix session;
its independent marker prevents a missing native restriction broadening gateway access.

## Distribution and validation boundaries

[Native client artifacts](../.github/workflows/native-clients.yml) is a manual
workflow that builds unsigned desktop installers, an Android debug APK and an
unsigned iOS simulator application for a specified public installation origin.
It uploads build artifacts; it does not publish releases, deploy an installation,
submit an app store listing, or require signing secrets.

Windows/macOS trusted distribution needs the operator's code signing and, for
macOS, notarization. Android production distribution needs the operator's release
keystore; iOS devices/App Store need the operator's Apple team, provisioning and
review. Debug APKs and simulator builds are review artifacts, not store releases.
App store approval and platform-specific builds must not be claimed merely
because their project or workflow exists.

The desktop's Node tests exercise actual loopback callbacks, forged-state
rejection, expiry/cancellation and origin/frame boundaries. Backend tests exercise
real PKCE issue/exchange, one-use consumption, source revocation and workspace
scope. The packed desktop browser proof uses a generated local HTTPS certificate
trusted only by its exact SPKI and exercises the actual packaged UI and preload:

    backend/.venv/bin/python clients/desktop/tests/browser-proof.py \
      --binary clients/desktop/dist/linux-unpacked/freecollab \
      --report /tmp/freecollab-desktop-proof.json

The Linux package proof checks root ownership, public read/traverse permissions,
matching runtime archives, and the actual packed AppImage launcher. CI runs it
before installing the Deb and exercising the real desktop application:

    python clients/desktop/tests/package-proof.py --directory clients/desktop/dist

The explicit --container-no-sandbox fixture flag exists only for isolated test
containers unable to provide Chromium's OS sandbox. A report using it explicitly
records that OS sandbox execution was not verified. It is never applied to
product code or the release workflow. Android emulator/physical-device behavior,
macOS/Windows installers, and iOS compilation require their corresponding runtime
or CI result; local Linux compilation does not establish those outcomes.

