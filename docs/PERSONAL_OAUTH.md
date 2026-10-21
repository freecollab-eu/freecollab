# Personal Google and Microsoft provider accounts

Open **Integrations → Provider accounts**. The same panel is available from
connected-service search and installed apps. Each organization registers its own
OAuth web application in Google Cloud or Microsoft Entra. FreeCollab's publisher
does not supply a central OAuth application, identity provider or provider secret.
Customer workspace SSO remains independent from these provider delegations.

## Configure the customer's application

A personal-account workspace owner/admin configures a registration for one
provider and one purpose, after password and enabled MFA verification. Register
the exact redirect URI shown by the panel:

```text
https://YOUR-FREECOLLAB.example/api/personal-oauth/callback
```

`PUBLIC_APP_URL` must identify that installation's public HTTPS origin. Loopback
HTTP is accepted only outside production. Configure your reverse proxy to omit
callback query strings from access/error logs; the supplied Nginx configuration
does this for the exact callback, and the API redacts its query in Uvicorn logs.
Application secrets are submitted in a private request body, sealed using the
retained account-security key and never returned. Replacing or disabling a
registration invalidates existing grants for that provider/purpose. Keep backups
and retained encryption keys under the installation's existing recovery policy.

| Purpose | Google delegated scope | Microsoft delegated scope |
| --- | --- | --- |
| Personal search | `https://www.googleapis.com/auth/drive.readonly` | `Files.Read.All` |
| Reviewed calendar action | `https://www.googleapis.com/auth/calendar.events` | `Calendars.ReadWrite` |

Microsoft additionally requests `offline_access`. Use the customer's exact tenant
UUID, not `common`/`organizations`. Enable the selected APIs and complete any
Google verification/Microsoft administrator consent required by your organization.
Both supported web-client registrations use their own client ID and secret.
Use separate client applications for search and calendar write actions; reusing
the same client ID for both purposes in a workspace is rejected. This prevents
combined provider consent from turning a read-only search token into a broader
write credential. Registering one purpose does not silently grant the other purpose. OAuth is
delegated user consent, never application-wide client-credentials authority.

## Connect your personal account

Choose a configured provider and purpose, review the exact scope and continue to
the provider's own consent screen. The authorization-code flow uses S256 PKCE,
random one-use state, a short-lived HttpOnly SameSite browser binding and a
ten-minute attempt deadline. The callback never returns provider tokens to the
browser. It redirects to a token-free result fragment that the app consumes.

Consent setup runs in a directly opened system browser. Native desktop/mobile
renderers and embedded frames show the installation address and explicit browser
setup guidance; their marked sessions cannot initiate the cookie-bound consent
callback. The app/system browser cookie jars and provider frame restrictions
remain intact. After browser setup, the same person can use their current
personal search/action delegation in an otherwise authorized app. That original
browser sign-in must remain valid; no raw provider token is handed to the app.

For search, select one Google folder or Microsoft drive/folder. OAuth creates a
normal personal search connection; existing queries/citations keep their current
provider ACL and collection checks. The granted provider scope can cover more of
the person's provider account than the chosen folder; the folder is FreeCollab's
application boundary, not a provider-enforced folder-specific OAuth scope. Search
never receives write tokens. The separate custom/manual-token connection path
remains available and retains its manual replacement policy.

For actions, first install the **Google Calendar** or **Microsoft Calendar** recipe
with explicit native channel grants. Then connect your own account to that exact
installation generation. Only you can use your delegation; another member must
connect their own provider account. Existing application management/invocation
rules require an unrestricted personal sign-in. Scoped customer SSO sessions can
connect/search their own workspace's read-only provider delegation; their UI
does not offer installed write-app consent.

Queued write actions retain the exact immutable grant reviewed at approval,
including in their idempotency identity. Revoke/reconsent or connecting a different
provider account cannot resume an old approved action under the new consent.
Legitimate access/refresh-token rotation keeps the same grant and remains usable.

The calendar commands accept reviewed `title`, `start`, `end`. Start/end must be
ISO timestamps with explicit timezone, with a positive duration no longer than
seven days; they are normalized to UTC. The Google endpoint is fixed to
`https://www.googleapis.com/calendar/v3/calendars/primary/events`; Microsoft's is
fixed to `https://graph.microsoft.com/v1.0/me/events`. No attendees, invitation
emails, channel history or FreeCollab identities are sent. Google's event ID and
Microsoft's transaction ID derive from the durable request, but ambiguous create
outcomes still require manual review instead of a blind retry.

## Refresh and revocation

Tokens and the complete verified original source claims are encrypted; the raw
FreeCollab bearer is never persisted. Every use, callback completion and refresh
checks the original active sign-in, expiry/auth version, native/embed parent
restrictions, customer SSO revision/scope, current workspace membership and target
revision. Actions additionally check current installation/channel grants and
invoking membership. Logging out or changing the original sign-in authority
requires new provider consent. A separate currently authorized sign-in cannot
borrow or renew an earlier expired source delegation.

Access tokens refresh automatically near expiry. Read-only search also retries
once after a provider 401. A database compare-and-swap lease serializes refresh;
rotated refresh tokens commit before a subsequent provider read. An interrupted,
ambiguous or rejected refresh fails closed and asks for fresh consent instead of
replaying a refresh token that the provider may already have rotated. Token
endpoints are fixed, use verified TLS/public-address pinning and the existing
bounded HTTPS transport, and reject redirects. Broader returned scopes are rejected.

Disconnect immediately erases local access/refresh credentials. Google token
revocation is additionally requested; the response distinguishes a requested,
rejected or unavailable provider operation. Microsoft has no per-token OAuth
revocation endpoint; remove consent in the customer's Microsoft account/Entra
portal when provider-wide revocation is needed. FreeCollab does not invoke a broad
"revoke all Microsoft sign-ins" operation. Copies already held by a provider or
backups have their own lifecycle.

Google can revoke the person's combined consent for a client application. If an
operator deliberately reuses a provider client across separate FreeCollab
workspaces/installations, revocation can therefore require new consent there too.

The existing integration worker runs a bounded fair local cleanup of expired,
revoked-source and changed-target delegations without making provider requests.
This erases stale encrypted tokens and pending PKCE material. Source validity is
always enforced immediately on use even before cleanup runs. Account closure
erases personal credentials and disables customer registrations configured by
the closing account, invalidating their grants; another admin must explicitly
configure a replacement application.

## API and verification

* `GET /workspaces/{workspace}/personal-oauth`: readiness, exact callback/scopes,
  public registrations and only the current person's delegation metadata.
* `PUT .../clients/{google|microsoft}/{search|calendar-action}`: customer
  registration with expected revision and password/MFA reauthentication.
* `POST .../{google|microsoft}/start`: reviewed personal consent, name, purpose and
  selected search collection or installed app generation.
* `GET /personal-oauth/callback`: browser-bound one-use provider response.
* `DELETE .../grants/{grant}`: own local revocation plus Google revoke request.

```sh
backend/.venv/bin/python -m pytest backend/tests/test_personal_oauth.py \
  backend/tests/test_enterprise_search.py backend/tests/test_app_platform.py -q
```

The suite exercises real synthetic verified HTTPS endpoints through a CONNECT
tunnel for both providers, exact token requests, PKCE, rotation, Google revoke and
actual Google/Graph calendar create requests. It also covers callback/session/
revision/closure races, immutable queued consent, token privacy and source fences.
`frontend/tests/personal-oauth-browser.py` verifies nine actual-App groups including
registration reauthentication, callback, real search consumption, revoke cleanup,
Swedish/320 px layout and native browser-setup guidance. Its provider consent
screen/token responses are explicit synthetic fixtures. These fixtures do not prove customer tenant
consent, real Google/Microsoft acceptance, public TLS routing, mobile deep-link
return or vendor verification; each operator verifies its own deployment.
