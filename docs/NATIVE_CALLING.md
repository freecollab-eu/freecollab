# Native audio calling

FreeCollab 0.13 adds native Android Telecom and iOS CallKit/PushKit call controls. Customers operate their own LiveKit server and their own Apple/Firebase applications. The feature defaults to disabled (`NATIVE_CALLS_ENABLED=false`); existing generic call alerts and the foreground meeting controls remain available independently.

Native audio uses LiveKit's DTLS-SRTP transport. Encryption terminates at the customer's SFU, which can access audio. This is **not Matrix end-to-end encrypted audio**. Matrix message encryption is unchanged. Before using native audio in an encrypted channel, each device must acknowledge this boundary in Mobile & offline settings for that channel. The acknowledgement is bound to the current Matrix room; replacing it requires review again. Until then incoming calls use the existing standard alert and foreground secure flow.

## Customer setup

1. Configure the existing customer LiveKit public WSS origin, HTTPS RoomService origin and API identity described in [MEETINGS.md](MEETINGS.md). Run its revocation worker; new admission pauses if the authorization worker is stale.
2. Configure [native notifications](NATIVE_PUSH.md). On the phone, connect through browser sign-in and explicitly enable incoming call alerts. Activation still requires the provider-delivered one-use device proof.
3. Build the customer mobile application with its HTTPS installation origin. Optional `FREECOLLAB_APPLICATION_ID=eu.customer.collab` selects the same reverse-DNS ID in Capacitor, Android applicationId and the Xcode bundle identifier. This ID must match the customer's Firebase package, Apple signing profile and APNs topic. The default remains `eu.freecollab.mobile`.
4. For Android provide the customer's Firebase configuration at build time. For iOS use a signed build with push entitlement and matching development/production APNs environment. The project declares background `audio` and `voip`; the provider sends real incoming calls to the bundle topic suffixed `.voip`. No test, registration proof, message, or periodic keepalive is sent as a VoIP push.
5. Enable `NATIVE_CALLS_ENABLED=true` on the installation, then explicitly enable Native audio calls on the device. Microphone permission is requested in the foreground without starting capture. For encrypted channels, acknowledge the audio boundary separately in that channel's mobile settings.

The Android implementation uses LiveKit Android 2.29.1, Kotlin 2.2.21 and coroutines 1.10.2. LiveKit's pinned AudioSwitch dependency requires the repository entry restricted to `com.github.davidliu` on JitPack. The iOS application uses LiveKit Swift 2.18.0; resolving that package requires Swift 6.1 / Xcode 16.3 or later. Package pins are source dependencies, not customer service credentials.

## Call lifecycle and authority

A push contains only an opaque delivery reference and call-device identifier. It never contains a caller, account name, channel title, message, Matrix key, account JWT, media token or LiveKit URL. Android uses high-priority data-only FCM for native call offers. iOS uses APNs HTTP/2, `apns-push-type: voip`, priority 10 and expiration 0. Expiring a PushKit token removes that token without revoking independent standard APNs alerts.

Ringing does not join a room or enable capture. Android registers a self-managed Telecom phone account and presents a private CallStyle notification. iOS must report a generic incoming CallKit call immediately when PushKit wakes the app, before asynchronous network lookup, then end stale or unauthorized offers. Answer and foreground Join are explicit consent. Admission is fetched again from the installation; it cannot be carried in a notification.

The native secure store contains a random, purpose-limited call credential. Android encrypts it with an Android Keystore AES-GCM key; iOS stores it in device-only Keychain. It authorizes only this device's call offers, admission, controls, heartbeat and revocation. Ordinary account APIs reject it. The original mobile sign-in, its parent, its complete verified restrictions, account security version, current channel membership, confirmed push incarnation, current room and expiry remain authoritative. Re-registering or removing a push device revokes dependent calls. Account closure erases call credentials, encrypted VoIP tokens, consents and native call rows before deleting push/notification references.

Call controls reauthorize before enabling the microphone. Native heartbeats run every five seconds. A native monotonic watchdog closes media within fifteen seconds without successful authorization; the independent SFU revocation worker also rejects stale native leases. Logout, source revocation, removed membership, host removal, device revocation, expiry, OS reset and ended rooms close access. Muting and ending stop local capture immediately; ending does not wait for a server response. An expired capability can only end/revoke its own existing call.

The browser UI displays native call status and mute/end controls without owning the media session. A suspended WebView is not used as a background media engine. Native audio is audio-only. Existing mesh meetings retain their foreground browser flow.

## Platform and verification limits

Android native calling requires Android 8 / API 26 or later for self-managed Telecom. Older supported clients retain standard alerts. Microphone and notification permission must be granted explicitly before background calling. The service declares `phoneCall|microphone` foreground types, uses a persistent call notification and does not restart an abandoned call after process death. Call acceptance, audio focus, lock screen and background execution depend on Android/OEM behavior and must be accepted on the customer's physical devices.

iOS requires a customer-signed application, push capability, correct APNs environment/topic, supported CallKit region and actual Apple delivery. CallKit owns audio session activation and interruption. An unsigned simulator compilation cannot validate PushKit delivery, lock-screen audio, entitlements, App Store acceptance or physical device behavior. Force quit, OS/network policy and provider outages can prevent timely delivery. FreeCollab does not promise guaranteed ringing.

The repository tests exercise real backend HTTP admission, signed call capabilities, original/parent logout, provider and device changes, secure-channel consent, explicit answer, heartbeat expiry, account erasure with enforced foreign keys, and the actual TLS/HTTP2 APNs adapter through an owned local CONNECT fixture. Android is compiled against the actual LiveKit SDK; the application bridge guard is executed on a JVM. Browser tests use a synthetic OS bridge and do not claim real native audio or provider delivery. macOS CI must compile the actual iOS target; physical signed Android/iOS call acceptance remains a customer release step.
