# Customer workspace sign-in, onboarding and SCIM

Each FreeCollab workspace connects its own OpenID Connect identity provider. The customer's administrator registers and manages that customer's application, client ID and client secret in **Integrations → Sign-in**. Customer connections do not use the installation's legacy `OIDC_*` environment variables.

The default flow explicitly links an existing personal FreeCollab account. Version 0.7 also offers opt-in organization-managed accounts for provisioned users, one-use invitations or creation at first SSO. A managed account belongs to one customer workspace, has a separate internal identity and cannot claim an existing personal account by email.

## Prepare the installation

Set `PUBLIC_APP_URL` to the trusted public HTTPS origin. The customer callback is derived from it: `https://collab.example.org/api/auth/oidc/callback`. Customers cannot override this callback. Retain the independent `ACCOUNT_SECURITY_KEY` that encrypts customer client secrets and short-lived PKCE verifiers, alongside native account factors and recovery mail.

Customer SSO works with `OIDC_ENABLED=false`. Public lookup requires one workspace's opaque `login_id`; there is no public directory of all customer providers. The ordinary sign-in link is a routing address, not an access credential. A managed invitation link additionally contains a one-use bearer invitation and must be shared privately.

Keep a personal native workspace owner account for administration and provider recovery. Settings that establish IdP, directory and cloud authority require native password/MFA confirmation. Managed users sign in through their organization and do not have a separate native password or global password-reset flow. Email delivery remains necessary for personal-account verification and recovery; managed first sign-in does not globally verify an IdP-asserted email address.

## Configure the workspace's provider

1. Sign in with a personal FreeCollab administrator account and local MFA, if enabled. Open **Integrations → Sign-in** in the target workspace.
2. Copy the displayed callback URI and register that exact URI in the customer's OIDC application. Enable authorization code flow with S256 PKCE.
3. Enter the provider name, exact issuer URL, client ID, client secret and scopes. The default is `openid profile email`; `openid` is required. Confidential clients support `client_secret_basic` or `client_secret_post`. Public clients can omit a secret when the provider advertises token authentication method `none`.
4. Choose **Save SSO configuration** to create a disabled draft. Confirm with the native password and local factor when enabled.
5. **Test connection**, then **Enable SSO**, confirming each action. The saved discovery test lasts one hour and enable rechecks discovery. Save and retest changes before enabling them.
6. Choose the onboarding policy below and share the workspace sign-in link with eligible people: `https://collab.example.org/#workspace-sso=<login_id>`.

Testing checks discovery, issuer, code/PKCE metadata and endpoint destinations. It does not complete a user's authorization or prove that the client secret works. Validate a real eligible member's sign-in before rollout.

The saved secret is write-only. Reads expose `has_secret`, never its value. Keeping it or leaving a replacement empty retains it only when the canonical issuer and client ID remain the same; a new string rotates it; **Remove secret (public client)** clears it. Changing the issuer or client ID clears the old secret unless that edit explicitly supplies a replacement. A different confidential application needs its own secret; a supported public client can remain empty. API clients omit/send empty `client_secret` to retain only that unchanged app identity, send a nonempty value to replace, or JSON `null` to clear. Saving disables SSO, advances the provider revision and invalidates its previous test and scoped sessions.

This prevents an administrator who can edit a workspace connection but cannot read its saved secret from forwarding that secret to another issuer or application. A test still validates discovery rather than authenticating the client; validate the new application's actual sign-in before rollout. Operator activity records configuration changes without recording the secret or request body; see [SECURITY_OPERATIONS.md](SECURITY_OPERATIONS.md).

Provider examples:

- **Keycloak:** public realm issuer, Standard Flow, S256 PKCE and exact callback. Direct password grants are unnecessary.
- **Microsoft Entra:** tenant-specific issuer such as `https://login.microsoftonline.com/<tenant-id>/v2.0` and a Web redirect URI. A `common` issuer does not match an exact tenant issuer.
- **Google:** `https://accounts.google.com` with a Web application client and exact redirect URI. Configure organization access at Google. These identity scopes do not authorize Drive or Calendar synchronization.

Issuer, authorization, discovery, token and JWKS endpoints must use public HTTPS on port 443. Cross-host public endpoints are supported; private-only Keycloak and arbitrary ports are outside this customer transport policy.

## Choose how people join

Open **Integrations → People → Onboarding**. Saving a policy requires native administrator password/MFA. The default remains **Personal accounts**.

| Policy | First sign-in | Authority granted |
| --- | --- | --- |
| Personal accounts (`linked`) | Existing verified personal account and workspace membership, followed by explicit password/MFA identity linking. | Existing membership only. No automatic account or role creation. |
| Provisioned organization accounts (`provisioned`) | SCIM creates the managed person before their first organization sign-in. | The person's managed membership and explicitly configured directory rules. |
| Invited organization accounts (`invited`) | An administrator creates a one-use organization invitation; the recipient follows it and authenticates at the IdP. | Member or guest access to that workspace. Channel access remains separate. |
| Create on first organization sign-in (`jit`) | The administrator explicitly enables creation; any identity accepted by that configured IdP can create a managed account. | Workspace member only; never owner, global admin or automatic private-channel access. |

Restrict the customer's provider application to the intended population before enabling JIT. The UI requires explicit acknowledgement. Existing inactive or removed managed identities do not regain access merely by signing in again; reactivation is an administrative provisioning action.

Select the signed **Provisioned identity identifier** used to match SCIM `externalId`: `sub` by default, or `oid`, `email`, or `preferred_username`. Prefer an immutable directory identifier. Configure the provisioner's `externalId` mapping to the same value. Email/username matching, when selected, is confined to the managed customer namespace; it never reserves, verifies or merges a global personal email account. Changing identity mappings can require an administrator to resolve existing bindings.

Managed invitations are listed separately from personal-account invitations. Their links expire after 24 hours by default, can be revoked before use, and are returned only once. The app forwards the invitation through the browser-bound SSO transaction, consumes it only for eligible managed creation and removes the invitation fragment from the browser URL. A native personal account is not created as an intermediate step.

The onboarding policy controls new entry paths. It does not convert or merge existing accounts. A conflicting existing personal SSO link requires administrator resolution rather than automatic adoption.

## Personal linking and returning sign-in

For the default personal flow, sign in with the native password, verify the FreeCollab email and obtain workspace membership. In **Integrations → Sign-in**, choose **Link organization sign-in**, confirm native password/MFA, then authenticate at the customer's provider. Keep the native session active through the callback. An identity already linked elsewhere cannot be moved silently.

Returning users use the workspace link or organization identifier. Local MFA remains required for a personal account that has enabled it; the session records `sso` and, after local verification, `otp`. Removing a personal link requires native password/MFA and revokes all its native sessions without deleting content or the remote provider account.

Managed users continue directly through the workspace's IdP. Their email is organization profile metadata; their account remains isolated from any same-email personal account and from another customer's identity. They cannot switch to native password/reset access, mutate global account security or enumerate other workspaces.

## Directory provisioning with SCIM

Open **People → Directory & groups** using a personal native workspace owner account. Copy the displayed endpoint, for example `https://collab.example.org/api/scim/v2/<directory_id>`, and create a provisioning bearer token. The token is shown once and stored only as a digest. Save it in the customer's IdP provisioning settings and acknowledge that it is stored before leaving the panel.

Creating/rotating a token requires a current native workspace owner and native password/MFA. A token stops working when rotated, disabled, or when its issuing owner is no longer active as an owner. A current owner must rotate it to resume. Workspace admins can pause provisioning, but cannot mint this credential. It grants SCIM directory access only and is not a native login token or authority over another workspace.

The service implements SCIM 2.0 Users and Groups create/read/replace/delete/PATCH, pagination, resource versions/ETags, conditional updates and the advertised schema/resource/service metadata. Supported filters are scalar equality: user `userName`, `externalId`, `id`, `active`, or group `displayName`, `externalId`, `id`. Unsupported filter syntax is refused. This is not a claim of every optional SCIM extension or a provider certification.

SCIM creates managed identities only. It never discovers or adopts personal accounts by email. Set the IdP's SCIM `externalId` to the configured signed OIDC identity claim. **Provisioned organization accounts** is the usual policy for directory-led entry; provisioned people can also enter under the managed invitation/JIT policies.

After groups arrive, configure the default member/guest role and explicit group rules in the UI. Rules can grant member/guest access and selected channel member/admin roles. Only a native workspace owner can add, change or remove a workspace-admin mapping. No group rule can create a workspace owner or installation administrator. Workspace role alone does not grant private-channel access; each channel grant needs approval from its current administrator. Stale approver grants require renewed approval and cannot continue adding access.

`active=false` or SCIM user deletion deactivates that managed person's membership, revokes workspace-scoped sessions and removes current channel access through the normal membership/Matrix controls. Reactivation restores the managed identity under current rules. Deprovisioning does not globally block a same-email personal account, remove another workspace's user, erase retained history or take back content/keys already downloaded. Group removal withdraws tracked directory grants without claiming unrelated manual permissions. Activity records omit bearer tokens and profile contents.

Pausing provisioning rejects incoming SCIM requests; it does not deactivate every existing user. Disabling a person only at the IdP prevents their next IdP login but does not itself revoke an issued FreeCollab session. Deliver the SCIM deactivation or use FreeCollab's membership/session controls for offboarding.

## Workspace and encryption boundaries

Every customer SSO session remains workspace-scoped across REST, Socket.IO and Matrix. Current membership, provider revision, native revocation and blocking are rechecked. Transactions also bind the original provider client and current onboarding policy. Missing/inconsistent scope records fail closed. Customer authority never inherits a personal user's global staff powers.

Personal linked accounts may share a Matrix identity/backup across workspaces. Their SSO sessions therefore need a separate native password/MFA encryption unlock, bound to the exact token, provider revision and account security version, lasting at most 30 minutes. Room/data access stays scoped afterward.

A managed organization account has a separate identity constrained to one workspace, so its SSO session can initialize that encryption identity without a nonexistent native password. Device verification and the independent encrypted-history recovery key still apply. SSO alone does not recover history. Managed lost-device removal uses an explicit confirmation and a recent organization sign-in; reauthenticate through the IdP when requested. Neither path falls back to plaintext.

## API and protocol

The `/api` prefix below is supplied by the application proxy:

| Operation | Endpoint |
| --- | --- |
| Workspace provider read/save | `GET` / `PUT /api/workspaces/{id}/sso` |
| Provider test/enable/disable | `POST /api/workspaces/{id}/sso/test`, `/enable`, `/disable` |
| Onboarding policy / managed people | `GET` / `PUT /api/workspaces/{id}/identity-policy`; `GET /api/workspaces/{id}/workforce` |
| Managed invitation create/list/revoke | `POST` / `GET /api/workspaces/{id}/workforce/invites`; `POST .../{invitation_id}/revoke` |
| SCIM setup/token/disable/mappings/audit | `/api/workspaces/{id}/scim`, `/token`, `/disable`, `/mappings`, `/audit` |
| SCIM service | `/api/scim/v2/{directory_id}/Users`, `/Groups`, `/Schemas`, `/ResourceTypes`, `/ServiceProviderConfig` |
| One enabled customer provider | `GET /api/auth/oidc/providers?login_id=<login_id>` |
| Member's provider | `GET /api/auth/oidc/providers?workspace_id=<workspace_id>` |
| Start customer sign-in | `POST /api/auth/oidc/start` with `login_id` and optional `invitation_token` |
| Explicit personal link | `POST /api/auth/oidc/link/start` with `workspace_id`, `password`, optional `code` |
| Callback / one-use browser handoff | `GET /api/auth/oidc/callback`; `POST /api/auth/oidc/exchange` |

Authorization uses random single-use state/nonce, S256 PKCE, an encrypted verifier and an HttpOnly SameSite=Lax browser cookie. The callback returns a short-lived opaque handoff in the fragment; the app clears and exchanges it once. Native bearer credentials and raw provider tokens are not placed in callback URLs. Provider access, refresh and ID tokens are not retained by this identity integration; cloud sync has a separate explicit token store.

RS256/384/512 and ES256/384/512 signatures are verified with public JWKS. Unsigned/shared-secret signatures are rejected. Exact issuer, audience, authorized party, expiry, issued-at and nonce are checked before identity resolution. Customer requests reject redirects, userinfo, private/loopback/link-local/metadata endpoints, mixed DNS answers and oversized JSON. Validated public addresses are pinned with original-host TLS/SNI, inherited proxy/CA trust and finite DNS/body/overall deadlines. Errors omit provider bodies, URLs and credentials.

## Legacy operator provider compatibility

The earlier installation-wide provider remains separately available for existing operator-managed deployments. Its environment variables do not configure customer workspaces:

```dotenv
OIDC_ENABLED=false
OIDC_PROVIDER_NAME=Organization SSO
OIDC_ISSUER_URL=
OIDC_CLIENT_ID=
OIDC_CLIENT_SECRET=
OIDC_REDIRECT_URI=
OIDC_SCOPES=openid profile email
OIDC_TRUST_EMAIL_CLAIM=false
```

Keep it disabled when using only customer-managed connections. Deliberately retaining it requires private operator configuration and the exact same-origin redirect URI. Its earlier global sign-in/provisioning and explicit `OIDC_TRUST_EMAIL_CLAIM` assurance policy remain for compatibility, not as customer managed-account authority. Coordinate migration before replacing legacy links.

## Verification

Backend suites cover customer/legacy OIDC, workforce namespaces and policy races, SCIM role/token isolation and transport. `frontend/tests/enterprise-onboarding-browser.py` exercises actual policy/invitation/SCIM UI and first managed SSO using synthetic HTTPS IdPs. `frontend/tests/customer-sso-browser.py` preserves the personal-link regression path. Real encryption/device/recovery proofs use private Matrix and actual Rust clients; see [VALIDATION.md](VALIDATION.md) for executed outcomes.

```bash
(cd backend && .venv/bin/python -m pytest tests/test_workspace_oidc.py tests/test_workforce_onboarding.py tests/test_workforce_security.py tests/test_scim.py tests/test_oidc_transport.py -q)
backend/.venv/bin/python frontend/tests/enterprise-onboarding-browser.py
backend/.venv/bin/python frontend/tests/customer-sso-browser.py
```

Fixtures replace transport only for their exact local synthetic origins; production has no private-network bypass switch. They use temporary databases/keys and no real Google, Entra, Keycloak or SCIM tenant credentials. Customer application registration, consent and actual provider interoperability must be validated by the customer before rollout.
