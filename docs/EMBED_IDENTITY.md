# Customer-owned identity for embedded FreeCollab

FreeCollab 0.10 offers two integration paths:

* **Native components:** the host application supplies a real FreeCollab native or customer-workspace session through `tokenFactory`. The host owns its existing session backend. Configure the exact host origin in the deployment's CORS allowlist. Arbitrary external IdP access tokens are not FreeCollab sessions.
* **Trusted embedded product:** the SDK mounts the actual client on a separate FreeCollab origin. A customer-owned OIDC popup and one-use PKCE handoff authorize it. The host receives a short-lived handoff code, never the popup's bearer session, message plaintext or crypto keys through the supported bridge. Customer applications do not need a central FreeCollab account, shared browser API secret or custom token-exchange backend for this popup path.

The trusted embedded client supports native and encrypted channels through the existing product APIs and Matrix runtime. Native inline components retain their separate security boundary and reject encrypted-channel content. See [the component SDK](COMPONENT_SDK.md) and [the encryption model](E2EE_IMPLEMENTATION.md).

## Register the customer's product

1. Run a reviewed FreeCollab deployment on its own HTTPS origin, for example `https://collaboration.customer.eu`, with exact `PUBLIC_APP_URL`, independent retained keys and working API/socket proxy routes.
2. Configure and enable the workspace's **own** OIDC provider and managed/provisioned/invited membership policy. SSO does not implicitly grant channel access, recover encryption keys or consent to Microsoft/Google file access.
3. A native workspace owner/admin opens **Integrations → Embedding** and registers a name and exact product origin such as `https://app.customer.eu`. Registration or editing requires native password/MFA reauthentication. Keep the application identifier and the intended workspace/channel identifiers for the host integration.
4. The product origin and trusted FreeCollab origin must differ. Paths, wildcards, userinfo, queries, fragments, noncanonical aliases and production HTTP origins are rejected. Use separate applications for separate host origins. Loopback HTTP is allowed only in development/test environments.

Registration APIs are `GET/POST /workspaces/{workspace_id}/embed/applications` and `PUT /workspaces/{workspace_id}/embed/applications/{application_id}`. A write contains `{name, parent_origin, enabled, password, code?}`. Updating increments the registration revision and invalidates outstanding grants and embedded sessions. Set `enabled: false` to revoke that application. Up to 50 applications may be registered per workspace. Workspace SSO credentials cannot manage these registrations.

The public application lookup at `GET /embed/applications/{application_id}?parent_origin=...` returns nonsecret registration/provider metadata only for its exact registered origin while both application and provider are enabled. It does not issue a session or grant membership. API examples here omit the public reverse-proxy `/api` prefix.

## Embed using the customer's OIDC

Install the reviewed SDK package artifact as described in the [SDK guide](COMPONENT_SDK.md), then:

```ts
import {createEmbedSignIn} from '@freecollab/components/auth';
import {mountWorkspace} from '@freecollab/components/embed';

const signIn = createEmbedSignIn({
  appUrl: 'https://collaboration.customer.eu',
  applicationId: 'registered-application-id',
  locale: 'sv',
});
let embedded: ReturnType<typeof mountWorkspace> | undefined;

document.querySelector('#connect')!.addEventListener('click', () => {
  // Open synchronously in the user's gesture so the browser permits the popup.
  signIn.open();
  if (embedded) {
    void embedded.reauthenticate().catch(showConnectionError);
  } else {
    embedded = mountWorkspace(document.querySelector('#collaboration')!, {
      appUrl: 'https://collaboration.customer.eu',
      applicationId: 'registered-application-id',
      workspaceId: 'workspace-id',
      channelId: 'channel-id',
      module: 'workspace',
      locale: 'sv',
      grantFactory: signIn.grantFactory,
      onError: showConnectionError,
    });
  }
});

// Immediately on the host's logout/account change or page teardown:
function disconnect() {
  embedded?.destroy();
  embedded = undefined;
  signIn.destroy();
}
function showConnectionError() { /* Show a safe, localized connection message. */ }
```

The popup uses the actual workspace OIDC login, including configured MFA. Its callback remains the operator's fixed trusted FreeCollab callback. No arbitrary host redirect URL is accepted. Only nonsecret request context, including the S256 challenge and exact destination origin, is retained across provider redirects. The parent retains the PKCE verifier in memory. A blocked/closed popup, provider denial, lost opener, expired request or changed configuration fails visibly; no password, MFA or encryption-trust step is silently bypassed.

Call `signIn.open()` again from a new user gesture before an explicit new sign-in. A host account switch must destroy/rebind the mounted session; it must not retain the previous account's UI or drafts. Destroying a frame clears its mounted content immediately. Online logout revokes its dedicated child session; browser shutdown/network loss can prevent that request, so the server also requires an active lease lasting at most ten minutes. The connected trusted frame renews that lease while its original customer SSO session and current permissions remain valid. Device verification and in-progress content survive successful renewal; the original authentication age and personal encryption unlock are not renewed. An expired lease requires a fresh connection through customer SSO.

## Integrate an existing host session backend

If the customer already obtains workspace-scoped FreeCollab SSO sessions on its backend, it may supply its own `grantFactory` instead of the popup. Create an S256 challenge from the SDK-provided context and call:

`POST /workspaces/{workspace_id}/embed/launch`

The JSON body is `{application_id, channel_id, parent_origin, code_challenge, code_challenge_method: "S256"}`. Authenticate with the current workspace-scoped SSO bearer. Launch accepts the exact registered host origin or the trusted FreeCollab popup origin, and rechecks live workspace/channel membership, provider revision, application revision and source session. A native account-wide token or an embedded child token cannot launch another embedded session. A server-side caller must authorize its own current user and scope; there is no public universal token-minting endpoint.

The result contains `{code, expires_in, frame_url, application_id, workspace_id, channel_id}`. The code lasts at most 60 seconds and may be consumed once. Return only that code to the SDK. Never put a session, code or verifier in a URL, HTML attribute, permanent browser configuration, logs or source control. Public frame URLs contain only the application identifier, registered parent origin and an unrelated handshake nonce.

## Handoff, revocation and isolation

The SDK and trusted frame validate exact `postMessage` source, origin and per-mount nonce. The frame consumes the code with `POST /embed/{application_id}/exchange`, supplying `{code, code_verifier, parent_origin, channel_id}`. The backend verifies S256, exact application/origin/channel, expiry and current authority, and atomically consumes the grant. Concurrent exchange attempts create at most one child session.

Only code hashes, source-session hashes and verified identity/freshness metadata are stored in handoff rows. The original bearer is never stored there, including as encrypted text. A derived JWT carries a signed application marker and remains restricted to the customer's workspace. It preserves the original authentication time, methods, hard expiry and security version. A separate authoritative `ActiveSession.expires_at` lease is the earlier of source expiry and ten minutes after exchange. REST, sockets and the Matrix gateway check that server lease even while the signed JWT's hard expiry remains in the future. The response reports the **lease** as `expires_in` and `expires_at`; handoff cannot renew recent SSO, extend a source session or bypass MFA.

`POST /embed/{application_id}/renew`, authenticated by the current child on the trusted origin, renews its live server lease after rechecking the original source session, application/provider revision, workspace and initial channel membership. It returns the same session payload and **same bearer**, with the new lease expiry. It creates no additional child session, changes no authentication claims and keeps the same Matrix device and gateway token. Matrix authorization lifetime is updated in the same transaction. Personal encryption-unlock expiry remains exactly unchanged; an absent or expired unlock is never recreated. Concurrent renewal is idempotent. A revoked or expired child lease cannot renew itself, and renewal can never pass the original source expiry. Failed renewal does not retry a content write or fall back to plaintext.

REST, Socket.IO and the Matrix gateway share the central workspace-scope fence. Source logout/revocation, account-security changes, removed workspace membership, provider edits/disabling and application edits/disabling invalidate the child on subsequent authorization. Channel membership is still enforced by each channel API. A child cannot launch grandchildren. `POST /embed/{application_id}/logout` revokes only that child; it does not sign the host out of its source SSO session.

The backend serves `/embed/{application_id}/document?parent_origin=...` as a fixed trusted HTML shell with an exact `frame-ancestors` CSP, no-referrer and no-store headers. The public URL uses `/api/embed/...`; existing API proxies must preserve its CSP. The frontend build must supply `/embed-entry.js`, `/embed-entry.css` and their reviewed assets on the same trusted origin. The iframe keeps content, unlocked device keys and Matrix recovery inside that origin. Browser storage restrictions can prevent crypto initialization; the client must fail rather than move keys or plaintext into the host.

Embedding does not remove the trusted-device, recipient verification, unlock or separate recovery-key requirements of encrypted channels. Normal/native channels remain server-readable. The host and service still observe routing, sizes, timing and other metadata. A compromised trusted client origin or its delivered JavaScript is outside the protection of this isolation, as in the normal web client. This feature is not an independent security certification.

The supported browser bridge keeps bearer sessions and content keys inside the trusted client. It is not an attestation of a hostile host backend: the host SDK holds the launch code and PKCE verifier, and a server-side host can relay an exchange request outside browser Origin enforcement to obtain the authorized child session. The host product is therefore trusted with the user's integration authorization. Existing encrypted history still requires the separate verified-device keys; a child session alone does not recover them. Do not describe the bridge as protection against a fully malicious host operator.

## Verification

`backend/.venv/bin/python -m pytest backend/tests/test_embed_identity.py -q` exercises real scoped sessions, foreign-key-enabled storage, origin restrictions, native-admin reauthentication, PKCE, replay/concurrent exchange, server-lease lifetime/authentication age, idempotent renewal, unchanged Matrix device/unlock, parent/application/provider/member revocation and logout separation. Expired-lease checks cover REST, renewal, an already joined socket and the completion of a held Matrix sync response, including a JWT whose signature and hard expiry are still valid. Run it from the repository root with the backend package on `PYTHONPATH`, or run `.venv/bin/python -m pytest tests/test_embed_identity.py -q` from `backend`.

Executed browser and protocol acceptance, including the trusted encrypted runtime, is recorded separately in [VALIDATION.md](VALIDATION.md). Local fixtures do not establish a live customer IdP/provider tenant, public deployment, npm publication or independent security review.
