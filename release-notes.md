**Try FreeCollab: [download the offline preview](https://github.com/freecollab-eu/freecollab/releases/download/v0.13.0/freecollab-demo-0.13.0.html)** and open the HTML file in your browser. Explore chat, tasks, documents and appearance in English or Swedish without an account or server setup. Sample changes reset on reload. The [preview ZIP](https://github.com/freecollab-eu/freecollab/archive/refs/heads/demo-preview.zip) can also be downloaded anonymously: unzip and open `index.html`.

FreeCollab 0.13 brings mobile calling, calendar delivery, encrypted shared work and resumable migration to the self-hosted collaboration platform.

- iOS CallKit/PushKit and Android Telecom calling with revocable device/session authority.
- Google/Microsoft recurring calendars, reviewed invitations, RSVP and revocable Busy-first subscription links.
- Encrypted tasks, calendar series and collaborative Yjs documents across independent installations.
- Resumable Slack imports with client-local encryption of messages, threads, attachments and historical edits/reactions.
- A standalone offline preview, static product website and versioned distribution with customer-owned identity/signing configuration.
- Realtime transaction/pool fixes, authorization regressions and authenticated backup/restore checks.

[Install the verified OCI archives](https://github.com/freecollab-eu/freecollab/releases/download/v0.13.0/OCI_INSTALL.md) · [Detailed validation and limits](https://github.com/freecollab-eu/freecollab/releases/download/v0.13.0/VALIDATION.json) · [Release details](https://github.com/freecollab-eu/freecollab/blob/0927e8e258819250d63e27cf0b514e09227960e9/docs/PRODUCT_013.md)

[Full CI](https://github.com/freecollab-eu/freecollab/actions/runs/39755974580) passed on the tagged final source: all 2,038 backend cases, 274 client cases, 26 SDK cases, 12 secure verification steps, 53 product commands and six built-website browser groups. The exact browser-tested SDK tarball and verified dual-architecture OCI archives are distributed through release files; this release does not publish to npm or GHCR. SDK/API/trusted clients use the 0.13.x compatibility line. The source tag points to 0927e8e; SDK/OCI downloads and native build evidence retain verified cdec provenance, with identical product/build-context source.

Customers configure their own SSO/OAuth, push, LiveKit and signing identities. Android debug, iOS simulator and unsigned Linux/Windows/macOS builds passed; physical-device and signed-store acceptance remain separate. Secure-channel calls explicitly disclose SFU-readable audio; previously decrypted copies cannot be remotely erased. The offline HTML is a locally packaged sample showroom, separate from OCI/backend/provider/E2EE validation. Hosted Railway configuration is prepared; source reconnection and live deployment remain separate.
