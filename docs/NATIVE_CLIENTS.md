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

The packaged desktop has a signed-manifest updater, described below. An operator
pins its publisher public key and release endpoint; distribution remains under
that operator's control. Missing configuration leaves automatic checks inactive.

## Android and iOS builds

Node 24, a Java 21 JDK and Android SDK platform/build-tools 36 are required for
Android. iOS builds require macOS, Xcode16.3+ / Swift6.1 for the pinned LiveKit
Swift package, and the iOS15+ target supported by that Xcode version.

    cd clients/mobile
    npm ci
    FREECOLLAB_APP_URL=https://collab.example.com npm run sync
    npm run build:android

Open the native IDE projects with npm run android or npm run ios.
FREECOLLAB_APP_URL must be an HTTPS origin without credentials, path, query or
fragment. Its hostname is also written into iOS's WKAppBoundDomains. Optional
FREECOLLAB_APPLICATION_ID sets a matching Capacitor, Android and iOS reverse-DNS
application identity; customer Firebase, signing and APNs topic must match it.
The same-origin gateway must mount the backend at /api, as the shipped Nginx does.
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
Standard alerts open the foreground meeting controls. The separate opt-in
[Native calling](NATIVE_CALLING.md) feature uses real CallKit/PushKit and Android
Telecom with native LiveKit audio in the background. OS Answer or foreground
Join is explicit consent and fetches current admission; ringing never activates
the microphone. Audio encryption terminates at the customer SFU and encrypted
channels require a separate per-device acknowledgement. See
[NATIVE_PUSH.md](NATIVE_PUSH.md) for independent standard alerts.
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


## Signed desktop automatic updates

The packaged app checks after 30 seconds and then every six hours. **FreeCollab →
Check for updates…** runs an explicit check. A verified release downloads in the
background; installation requires **Restart and install**. A renderer's unsaved
work veto cancels the pending install instead of silently arming a later quit.

An operator bundles `clients/desktop/resources/update-config.json`:

```json
{
  "manifest_url": "https://updates.your-domain.example/stable-linux-x64.json",
  "public_key": "YOUR ACTUAL ED25519 PUBLIC KEY IN PEM FORMAT",
  "artifact_origins": ["https://downloads.your-domain.example"]
}
```

Use a real operator-generated Ed25519 keypair. Only its public key goes into the
client. The private publisher key stays offline or in the operator's protected
release signer. Environment `FREECOLLAB_UPDATE_MANIFEST_URL` and
`FREECOLLAB_UPDATE_PUBLIC_KEY` can configure an unbundled operator build. A public
key downloaded beside an update is never trusted as a replacement pin.

The signed payload has exactly `protocol` (`freecollab-update-v1`), stable `version`,
monotonic integer `sequence`, exact `platform`/`arch`, ISO `issued_at`/`expires_at`,
HTTPS artifact `url`, byte `size`, lowercase SHA-256 `sha256`, and `format`
(`appimage` on Linux, `zip` on macOS/Windows). Its lifetime is at most 31 days.
Compute size/hash from the actual final distributable, then sign offline:

```sh
node clients/desktop/scripts/sign-update.cjs manifest.json publisher-private.pem signed-update.json
```

Publish the resulting `{payload, signature}` JSON at the configured manifest URL.
Each OS/architecture needs its own manifest/artifact. The verifier rejects
signature changes, expired/future manifests, downgrade/replay, wrong platform or
architecture, unapproved origins, redirects, over-size streams and hash mismatch.
Staged bytes are hashed again before installation so post-download tampering
cannot reach the installer. Artifacts are bounded to 768 MiB and expanded ZIP contents to 2 GiB/20,000 entries.
ZIP preflight validates local and central paths and rejects traversal, path/type
overrides, encrypted entries, duplicates, unsupported types and Windows links.
macOS framework symlinks are allowed only within `FreeCollab.app`, with no archive
writes through link ancestors; post-extraction realpaths must stay in the bundle.

Linux installs only a packaged AppImage at its existing owned path; Deb upgrades
remain the package manager's responsibility. macOS validates the new bundle's
strict code signature, executable identity and the same actual Apple Team ID as
the installed app. Windows validates real Authenticode status and the installed
publisher identity. Therefore unsigned CI packages are review builds, not evidence
of trusted macOS/Windows auto-update acceptance. Native signing and notarization
require the operator's real platform credentials.

A detached guardian replaces only the existing app with the staged verified
package, keeps a uniquely owned previous version, and starts the new app with a
random health nonce. The main process writes the nonce only after its window has
loaded successfully. No healthy startup within 45 seconds restores that exact
owned previous version. Applied/seen sequence state prevents replay. Tests run
the actual verifier, malicious ZIP/stage rejection and a real temporary Linux
guardian rollback; physical macOS/Windows signing/install behavior needs those OS
runtimes and the operator's certificates.

## Verified mobile resource links

OS-verified HTTPS domain association is implemented for Android/iOS. Desktop and
web use the permission-checked browser resource route; the Electron client does
not register a native HTTPS domain association.

The mobile configuration command binds Android App Links and iOS Universal Links
to the chosen HTTPS installation origin and `/open` only. Supply the real
`FREECOLLAB_APPLICATION_ID`, `FREECOLLAB_APPLE_TEAM_ID` (ten-character Apple team)
and/or comma-separated `FREECOLLAB_ANDROID_CERT_SHA256` certificate fingerprints
when running `clients/mobile/scripts/configure.mjs`. These are public signing
identifiers, not secret signing keys. Generated documents are written under
`clients/mobile/association-files/.well-known/`. Reconfiguration removes stale
owned platform documents when an identity is omitted.

Serve `/.well-known/apple-app-site-association` and `/.well-known/assetlinks.json`
from that exact installation without redirects. The product API serves these
using `NATIVE_APPLICATION_ID`, `NATIVE_APPLE_TEAM_ID` and
`NATIVE_ANDROID_CERT_SHA256`; frontend proxies forward those exact well-known
paths. Alternatively host the generated documents directly. Missing signing
identity produces 404 for that platform and malformed identity produces 503;
there are no invented certificates or wildcard domains. App-store/release
signing must match those identifiers, including the actual Android app-signing
certificate when Play signs the delivered app.

A link contains only workspace/channel/kind/item IDs and a project ID when needed
for a task. Supported kinds are channel, message, task, project, document,
linked-document, file, clip, decision, feed, calendar, meeting, stack, workflow
and workflow-run. The OS bridge captures cold/warm launches, enforces the exact
HTTPS origin/path and unique allowed query keys, and gives each receipt a new
generation. The browser then validates exact kinds and IDs and fetches current
channel/workspace permissions. No link auto-joins a call, enables devices, writes
a document or executes a workflow.

Standalone pre-login IDs survive a customer SSO redirect in bounded same-origin session
storage for at most ten minutes and bind to the authenticated account. Demo
and embedded memory sessions keep separate transient routing hints and never
read, erase or replace a retained standalone account hint. A newer
native generation, browser link, hash navigation or auth change invalidates older
pending reads, including a slow dirty-document prompt. Only an accepted exact
hint is cleared; retrying the same URI is a fresh OS intent. A missing/denied
resource reports unavailable rather than opening the first unrelated item.
Unsigned simulators/debug builds and generator/parser tests cannot establish
physical-device domain association or app-store approval. Verify those using the
operator's signed app and actual domain.
