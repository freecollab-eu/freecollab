# Accounts, sessions and pilot abuse controls

## Session behavior

The server issues seven-day HS256 JWTs with a random `jti` per login and an account security version. Requests require a valid signature, an unexpired numeric expiry, a nonempty subject, an existing unblocked user, the current security version and a token that is not revoked. REST and Socket.IO use the same database-backed account/revocation check.

`POST /auth/logout` requires the current bearer token and returns `{"logged_out": true}` after storing its SHA-256 digest and expiry in the `RevokedToken` table. Raw bearer tokens are never stored there. The digest invalidates the current credential, including connected Socket.IO clients before their next authorized event or private broadcast. Other independently issued sessions remain valid. Blocking or deleting an account prevents all of its sessions from accessing protected resources.

Logging out is effective after the server acknowledges the request. A client should clear its local token and disconnect even if the network fails, but cannot claim that server-side revocation completed without a successful response. Revocations persist across application restarts; expired entries are removed during subsequent logout transactions.

Tokens issued by previous versions without `jti` remain valid if otherwise correctly signed and unexpired, and can be revoked by their digest. Legacy identical tokens are the same bearer credential; the new random identifier prevents separate new logins from producing identical credentials in the same second. Rotating `JWT_SECRET` invalidates all old tokens. Content-key migration is a separate operation described in [KEY_MANAGEMENT.md](KEY_MANAGEMENT.md).

The account panel lists active sessions with their creation/expiry, browser user agent, client address and current-session marker. Browser/address strings are informational, not verified device identities. Only a SHA-256 token digest is persisted. Revoking one session requires the current password and, when enabled, an authenticator or recovery code. “Log out everywhere” increments the account security version, revokes tracked sessions and invalidates unused recovery links and password-first MFA challenges. Tokens created by older versions without a version are treated as version zero, so the same action also revokes untracked legacy credentials.

Password reset/change increments this version as part of the same transaction as the password update. Socket checks reject old credentials before their next authorized event or private delivery. Previously delivered data and a Matrix device's cryptographic keys are separate from FreeCollab account-session authorization; account recovery does not delete or restore message keys.

## Credential rules

Registration, login and admin-created users canonicalize email addresses by trimming surrounding whitespace and lowercasing them. Names are nonempty after trimming and limited to 80 characters; email input is validated and limited to 320 characters. New native registrations and admin-created accounts require at least 12 Unicode code points and at most 72 UTF-8 bytes, with no null character. Existing-account login and reauthentication retain the 1–72 UTF-8 byte rule so valid legacy passwords remain usable. The server checks the byte limit without bcrypt truncation; the creation forms check the same code-point and byte boundaries.

Normal registration and admin creation both reject canonical duplicates and handle concurrent unique-key conflicts. Legacy case-only duplicate addresses are not merged automatically: login rejects an ambiguous canonical address. Back up and reconcile those records through operator access, preserving channel ownership and membership, before enabling their login.

Password reset/change requires at least 12 Unicode code points within the same 72-byte UTF-8 limit. Existing valid shorter passwords can still sign in. Password changes and sensitive account actions require the current password; with MFA enabled they also require a valid authenticator or unused recovery code. A rejected reauthentication returns HTTP 403 and does not revoke the signed-in session.

## Email ownership and password recovery

Registration queues a verification message when email delivery is configured; the account panel can request another. Verification links expire after 24 hours. A successful one-use link marks ownership of that account's current email address, not legal or organizational identity. Verification is displayed but is not a prerequisite for personal login. Ordinary invitations remain bearer links. Customer-owned [OIDC workspace sign-in](OIDC.md) defaults to explicit native password/MFA linking for an existing verified member. An administrator can instead select a separate managed workforce policy: provisioned, organization invitation or explicit just-in-time admission. Managed invitations require the configured IdP and cannot be accepted through the ordinary native invitation endpoint. IdP email claims never verify or merge a personal account. Native registration remains separate.

`POST /auth/forgot-password` returns the same message for existing, unknown, blocked and ambiguous email addresses. A disabled or invalid mail configuration returns a uniform HTTP 503 before looking up the account. A new reset request invalidates older reset links. Password-reset links expire after 30 minutes and are consumed atomically with the password/version update, allowing only one successful use even if requests overlap. Both reset and verification tokens are random 256-bit values; account-token tables store only their SHA-256 digests, expiry, purpose, email and security version.

Links use a fragment (`#verify-email=…` or `#reset-password=…`) on the configured `PUBLIC_APP_URL`; the browser submits the token in a POST body. Fragments avoid ordinary HTTP access logs. Do not copy links into logs, analytics or support bundles. The browser clears consumed link fragments. Password recovery does not bypass an enabled second factor and does not restore encrypted-message history, device keys or an old password-wrapped legacy key.

## Authenticator and recovery codes

MFA setup requires reauthentication and returns a base32 secret plus an `otpauth://` URI. The pending secret expires after ten minutes. A valid six-digit RFC 6238 SHA-1 TOTP confirms setup; the server accepts the current 30-second interval and one adjacent interval on each side. Setup confirmation revokes old sessions and returns a fresh factor-verified session. The confirmed interval is marked used, so an immediate fresh login may require the next code or an unused recovery code.

Enabled MFA changes login into two steps: password verification returns a random challenge, then `POST /auth/mfa/verify` accepts an authenticator/recovery code and issues a bearer token. Challenges expire after five minutes, stop after five failed attempts and are one-use. TOTP intervals cannot be reused for separate logins. Sensitive account actions still require the password; they may reuse the current TOTP interval, while recovery codes remain one-use everywhere.

Ten random recovery codes are shown once on confirmation/regeneration; only normalized hashes are stored. Regeneration and disabling MFA require password plus factor, and regeneration invalidates every prior code. Disabling MFA revokes old sessions and returns a new session. The server encrypts pending and confirmed TOTP secrets with PyNaCl SecretBox under `ACCOUNT_SECURITY_KEY`, authenticated to their purpose and account. This is server-side account security material, separate from signing keys and end-to-end message keys.

## Mail configuration and worker

`ACCOUNT_SECURITY_KEY` is an independent standard-base64 32-byte random secret. Retain it with protected recovery configuration: losing it prevents decrypting existing authenticator secrets and queued mail. Production does not generate a replacement. Development can create a persistent mode-0600 `ACCOUNT_SECURITY_KEY_FILE`; keep it outside uploads and do not commit it. Production setup must provide the explicit secret, an HTTPS `PUBLIC_APP_URL` and configured SMTP to use recovery email. An existing installation upgrading from an older `pilot.env` must add a newly generated independent account-security key; do not replace its existing signing/content keys. `scripts/pilot-init.py` supplies this key for new installations.

Email instructions are queued in a durable outbox; their subject/body, including links, are encrypted under the account-security key. Recipient addresses remain routing metadata. No endpoint sends email synchronously or claims successful external delivery. Run the worker described in [OPERATIONS.md](OPERATIONS.md). It retries failures after restart and records bounded generic error statuses without exception strings or plaintext links. SMTP is at-least-once: a crash after SMTP acceptance but before acknowledgement can resend a message; its recovery link remains one-use.

`MAIL_DELIVERY_MODE=file` is a development-only private mail sink, refused in production. Its directory is mode 0700 and `.eml` files are atomically published with mode 0600. Read those files through trusted local filesystem access; there is no public token/debug endpoint. `disabled` uniformly disables recovery email; `smtp` requires a host and a separately running worker. Production SMTP requires verified TLS through STARTTLS or implicit TLS. Configure either `SMTP_STARTTLS=true` or `SMTP_IMPLICIT_TLS=true`, not both. Do not enable external delivery until the operator has configured the provider and sender.

## Auth throttling

The default pilot uses **one application worker/replica**. A bounded in-memory limiter applies to authentication routes when `REDIS_URL` is unset:

| Scope | Limit |
| --- | --- |
| Registration requests per client address | 30 per minute |
| Login requests per client address | 60 per minute |
| Failed logins per canonical account | 10 per five minutes |
| Failed logins per client address | 30 per five minutes |

HTTP 429 responses include `Retry-After`. Successful login clears the account failure bucket but does not clear the address failure bucket. Account throttling temporarily affects legitimate logins too; wait for the indicated interval rather than automatically retrying. State is bounded to 10,000 keys, cleaned as it expires, and never evicts a live restriction to accommodate new untrusted identities. When capacity is exhausted, new buckets are refused temporarily. Account keys are hashes rather than raw email addresses.

The limiter uses `request.client.host`; it does not trust caller-supplied `X-Forwarded-For` directly. The reverse proxy must overwrite forwarding headers, and Uvicorn's trusted-forwarder configuration must admit only that proxy/network. Keep the backend private. Additional replicas require the TLS Valkey limiter and full coordination setup in [SCALING.md](SCALING.md); configured shared limits fail closed on broker failure. In-memory limits reset when the process restarts, so they are an application safeguard rather than a distributed denial-of-service defense.

Recovery and sensitive account routes also use persistent database buckets. Recovery requests allow three per canonical email and twenty per address per fifteen minutes; email-verification requests allow three per account and twenty per address. Sensitive account changes allow fifteen per account and thirty per address per five minutes. Link confirmation and MFA challenges have additional address/account limits. These restrictions survive process restarts and return `Retry-After`; account identifiers and client addresses are hashed in rate-limit keys.

Personal registration remains available for the self-hosted pilot. Workspace managed admission is independent of installation-wide personal registration; it does not make the public registration endpoint invitation-only. TLS termination, request-size limits and external traffic controls remain deployment responsibilities.

## Retention and permanent removal

Direct administrative account/channel DELETE routes remain fenced with HTTP 409 instead of removing only a parent row and orphaning shared history. Separate [reviewed data controls](DATA_CONTROLS.md) provide native content exports, preview-bound permanent retention purges with physical file cleanup, and personal account identity erasure in **Workspace tools → Data & retention**. Identity erasure requires fresh password/MFA confirmation, a current dependency preview and ownership handovers; it removes private account data, revokes sessions and retains a blocked anonymous author reference for shared work. Secure-channel access revocation must succeed before native identity erasure commits.

Pausing an account or removing membership revokes access without erasing content. Native retention does not erase Matrix homeserver/device history, previously shared provider copies or backups. Operators must define those separate retention and restore policies; account identity erasure does not promise removal of all authored shared text or every external copy.

Staff message moderation is a soft deletion: the message row remains with a deleted flag and a replacement marker, existing thread references and counts remain intact, and a `message_deleted` event updates connected channel members. Deleted messages are excluded from normal history. This preserves referential integrity; it does not provide an irreversible purge from database copies or backups.

## Customer-owned SSO sessions

Each workspace owner/admin configures their own IdP in **Integrations → Sign-in**
using an unrestricted native session and fresh password/MFA confirmation. Saving
a draft disables the provider and advances its revision. Testing validates
discovery; enable requires a current successful test and revalidates it. The
server returns secret-presence metadata, never the saved client secret.

Customer authorization transactions, explicit identity bindings, browser handoffs,
local MFA challenges and resulting sessions are bound to the workspace, client
and provider revision. Signed scope claims and additive database sidecars are
checked together; deleting a sidecar cannot turn a scoped token into a global
session. Provider edits/disabling, removed membership, account blocking and native
revocation stop access, including existing Socket.IO/Matrix requests. Connected
[SCIM](SCIM.md) deactivation removes managed access and revokes issued sessions.
Without provisioning synchronization, an IdP-only disable takes effect at the
next IdP login.

A customer SSO session can access only its workspace and current channel grants.
It cannot enumerate other workspaces, access unassigned legacy channels, manage
installation settings, change account security, list global sessions or configure
IdPs. Effective global staff capabilities are masked without changing the user’s
persisted role. Native password sessions retain their existing permissions.

The personal Matrix identity and encrypted backup span the native account.
Customer SSO alone therefore cannot open a Matrix session or retrieve global
crypto material. `POST /secure/unlock` accepts the native password and local MFA
code and creates a grant for that exact scoped token, provider revision and
account-security version, expiring within 30 minutes and no later than the token.
Room/data access stays scoped after unlock; global account/admin controls remain
unavailable. Personal key material is account-wide after this explicit native
owner check; this is not cryptographic isolation of independent per-workspace
identities. Device trust and the separate recovery key remain necessary.

## Separate managed workforce accounts

An optional workspace policy creates a new identity confined to that workspace;
it never converts an existing personal account. A reserved synthetic native email
and unknown random password prevent native email/password login. The IdP email
is workspace metadata, even when it equals a personal account's address. The
configured external identity claim matches provisioning; issuer/client/subject
become immutable once bound. See [WORKFORCE_IDENTITY.md](WORKFORCE_IDENTITY.md).

Managed credentials are SSO-only and always require the signed scope and its
database sidecar. Every bind rechecks active status, the correct workspace and
namespace, and refuses a personal home, outside memberships or workspace-owner
promotion. Password/reset/MFA/global-account and installation controls are
unavailable. SCIM group mappings can grant approved workspace and channel roles;
the IdP callback alone does not overwrite roles.

The managed Matrix identity and recovery backup are separate from the person's
personal identity and from other workspaces. Valid managed SSO can initialize
this identity without a personal password. Device verification and the separately
saved recovery key still apply. `POST /secure/devices/{id}/revoke-managed` requires
explicit confirmation and a scoped SSO token issued within five minutes, rechecks
the live session, and revokes only an owned device. Remote device deletion must
succeed before local session revocation is committed. Older SSO requires a new
organization sign-in; personal accounts retain their native password/MFA path.
