# Customer-owned file and calendar synchronization

Open **Integrations → Sync** in the relevant workspace. A connection grants one
remote folder or calendar to one channel. Files and supported calendar events can
be created, updated and removed in either direction; concurrent changes require
an explicit choice. Document links and ICS import/export remain separate features
in [DOCUMENTS_CALENDAR.md](DOCUMENTS_CALENDAR.md).

Background sync supports channels with **server-side encryption**. It cannot read
or write end-to-end encrypted Matrix channels, including channels being prepared
for encryption. Their client-encrypted document links and deliberate local ICS
export remain available. No worker decrypts Matrix content or converts a secure
channel to plaintext to make an integration work.

## Providers and supported resources

| Provider | Configuration | Implemented behavior and limits |
| --- | --- | --- |
| Google Drive | Customer OAuth client and selected folder ID | Two-way ordinary file content and metadata. Direct children only. Google Docs, Sheets and Slides export as read-only PDFs; they are not converted back into editable Google documents. Other Google-native formats are refused. |
| Google Calendar | Customer OAuth client and selected calendar ID | Two-way individual nonrecurring events, all-day dates, supported time zones and a single supported display reminder. Incremental sync tokens and cancelled-event tombstones are handled. Unsupported recurrence or reminder structures fail explicitly instead of being flattened. |
| Microsoft OneDrive / SharePoint | Customer OAuth client, directory tenant UUID, drive ID and folder ID | Two-way files in the selected drive folder, including delta changes and deletions. SharePoint document libraries use their Graph drive IDs. Direct children only. A preauthorized download URL from Graph is fetched without forwarding the Graph bearer token. |
| Microsoft Outlook Calendar | Customer OAuth client, directory tenant UUID and calendar ID | Two-way ordinary nonrecurring events through Graph. Times are requested in UTC. Meetings with attendees are read-only to avoid changing scheduling through the simpler channel editor. Recurrence and unsupported body representations are refused. |
| Nextcloud WebDAV | Exact public HTTPS folder URL, username and app password | Two-way direct child files, conditional writes and deletion tombstones. Subfolders require separate connections. Existing remote paths remain fixed; DAV MOVE/rename is not implemented. |
| Nextcloud / other CalDAV | Exact public HTTPS calendar collection URL, username and app password | Two-way ordinary single-VEVENT resources, conditional writes, sync tokens where supported and full-scan fallback for expired/unsupported tokens. UTC, IANA zones, all-day dates and a single display reminder are supported. |

CalDAV scheduling (`ATTENDEE`, `ORGANIZER`), recurrence rules/instances, arbitrary
custom properties, custom non-IANA timezone definitions and unsupported alarms
cannot be represented losslessly by the current channel calendar. Such resources
are marked read-only or reported as unsupported; they are not silently rewritten.
A connection may require attention until unsupported resources are removed from
the selected collection. Existing privacy/transparency properties supported by
the adapter are preserved on update.

This release does not provide a provider folder/calendar picker, remote document
editing, provider permission management or recursive directory synchronization.
Enter the provider's existing resource identifiers and collection URL. The native
file MIME allowlist also applies to imported files; unsupported formats are not
silently accepted.

## Prepare the installation

Remote dispatch is disabled by default:

```dotenv
SYNC_ENABLED=false
```

An installation operator can set `SYNC_ENABLED=true` and run the existing
integration worker with the API's database, storage, independent keys and private
configuration. The switch enables the sync queue; it does not configure any
customer's provider credentials or enable an individual connection.

```bash
docker compose --env-file pilot.env -p freecollab \
  up --build -d --wait backend frontend integration-worker
```

Outside Compose, run from `backend` using that same private configuration:

```bash
python -m app.services.integration_worker --interval 10
# One bounded polling cycle:
python -m app.services.integration_worker --once
```

When the flag is false, administrators can prepare paused drafts and read their
status. Connection tests, OAuth authorization and dispatch remain unavailable.
Keep the existing account-security and content keys when upgrading. This change
adds tables; it does not replace existing account or Matrix identities.

## Configure a connection

Use a personal FreeCollab administrator account signed in through native
password/MFA authentication. The administrator needs both workspace owner/admin
access and administrator membership in the selected channel. A customer SSO
session cannot manage provider credentials, even when its workspace role is
admin. Private channel access is not implied by a workspace role.

1. Choose **Add cloud connection**, provider, files/calendar and the channel.
2. Enter the selected resource IDs or public WebDAV/CalDAV collection URL.
3. Enter the customer's own OAuth application credentials or app password.
4. Explicitly authorize sharing **existing and future** channel files/events with
   that remote collection. Changes and deletions can propagate in both directions.
5. Choose **Save paused connection**, then confirm with the native password and
   local authenticator/recovery code when enabled.
6. For Google/Microsoft, choose **Connect provider account** and finish the
   provider's actual authorization screen.
7. Choose **Test connection**, then **Enable sync**. A successful saved test is
   valid for one hour; enabling rechecks the provider and current grants.
8. Use **Sync now** to queue another pass. Check **Sync activity** and conflicts
   for completion. A queued operation is not a successful provider write.

A saved edit pauses dispatch, invalidates the previous test and resets in-flight
revision-bound work. Credentials remain bound to the account that authorized
them. A different administrator's metadata edit does not transfer that account's
remote grant. Reconnect OAuth, or explicitly supply replacement DAV credentials,
to authorize a replacement account.

Changing the provider, OAuth client/tenant, channel or remote collection before
any synchronization clears the affected authorization. A saved OAuth application
secret is not forwarded to a different client/provider/tenant. A collection or
channel change requires fresh provider authorization. Once item mappings exist,
retargeting the channel/provider/collection is refused: create a separate
connection. Duplicate connections to the same provider collection and channel are
also refused to prevent imports echoing back as duplicate documents.

**Pause sync** stops future dispatch while retaining credentials and records.
**Disconnect** additionally erases locally stored provider credentials and
cursors. Neither action deletes existing local or provider copies, nor revokes an
upstream OAuth consent. Revoke the application or app password at the provider
when that upstream authorization should end. Protected backups may still retain
older encrypted credentials.

## Customer OAuth and app passwords

`PUBLIC_APP_URL` determines the fixed callback URI, for example:

```text
https://collab.example.org/api/sync/oauth/callback
```

Register this exact callback in the customer's own provider application. Cloud
sync permissions are separate from SSO permissions; enabling a customer's SSO
does not grant file/calendar access. No installation-wide Google/Microsoft client
secret is required by the customer configuration path.

Google uses code flow with S256 PKCE and offline authorization. Drive connections
request the Drive scope; calendar connections request Calendar Events. Microsoft
uses the configured tenant UUID with code flow, S256 PKCE, `offline_access` and
`Files.ReadWrite.All` or `Calendars.ReadWrite`. Provider permissions may cover
more of the authorizing account than the selected collection; FreeCollab's worker
still enforces the configured folder/calendar. Choose a suitable provider account
and consent policy for that grant.

OAuth state is single-use and bound to an HttpOnly SameSite browser cookie, the
native session/account-security version and exact connection revision. PKCE
verifiers, access/refresh tokens, app passwords and client secrets are encrypted
with the retained `ACCOUNT_SECURITY_KEY`. Saved credentials are never returned
by configuration reads or put in callback URLs. Safe configuration exposes only
credential-presence metadata. Token refresh is persisted before a retried
operation; invalid/revoked authorization requires reconnecting the provider.

Nextcloud/CalDAV use an app-specific username/password over verified HTTPS. The
saved username/password are not prefilled on edit. Blank credential fields retain
existing values when the grant remains unchanged; disconnect removes them.
Customer credentials belong in the separate credentials fields, never in a URL,
public config JSON or frontend environment variable.

## Conflicts, deletion and history

The worker compares the last synchronized local fingerprint and remote ETag with
both current copies. A one-sided change can synchronize. If both copies changed,
that item pauses as a conflict; neither version is silently selected by time.
Choose **Use channel version** or **Use provider version**, confirm native
password/MFA, then check activity for the result. A changed copy after the choice
requires another review. Read-only provider resources cannot be overwritten with
the channel version.

Remote updates/deletes use conditional ETags. Google Drive file removal uses
`PATCH` with `trashed: true`, preserving the provider's trash/recovery path; it
does not call Drive's permanent file-delete endpoint. Other providers use their
ordinary delete operation and retention rules; WebDAV/CalDAV removal does not
promise a provider recycle bin. Calendar event deletion remains an event-delete
operation. Unsupported or absent preconditions fail closed. Incremental responses apply explicit deletion tombstones; missing
items are treated as deleted only after a complete authoritative catalog. A
truncated or failed catalog never becomes a deletion instruction.

Imported file bytes create immutable native versions. File deletion records a
sync tombstone instead of erasing retained versions. Calendar changes append
immutable revisions, including deletion revisions. This retention is separate
from the external provider's retention policy. Native files retain the existing
server-mode storage behavior; do not describe them as application-encrypted file
bytes. Calendar payloads use the existing native content key ring.

Creates record a pending intent before contacting the provider. Provider IDs,
private application properties, transaction IDs or deterministic DAV/Graph paths
support reconciliation after a lost acknowledgement. If a possibly completed
create cannot be identified safely, the item becomes an explicit ambiguous-create
conflict rather than blindly creating a duplicate. Deterministic path prefixes
are hidden from the displayed human filename.

## Scope, network policy and bounded work

The authorizing native account, workspace role, channel admin membership,
connection revision and channel encryption mode are checked before and after
network work and under writer locks for mutations. Removing that person's grant,
blocking the account, pausing the connection or changing its revision prevents
further authorized dispatch. A provider's own permissions remain an additional
boundary. Channel members see a notice when the channel has external sync.

Customer collection URLs require **public HTTPS on port 443**. Private-only
Nextcloud/CalDAV servers, loopback, link-local/metadata destinations, mixed
public/private DNS answers and nonstandard ports are refused. Every request
resolves afresh and pins an allowed public IP while preserving the original Host
and TLS/SNI name. Inherited proxy/CA trust remains effective; redirects and direct
fallback after a proxy failure are refused. XML DTD/entities, oversized responses
and unsafe collection-relative paths are rejected.

The integration worker handles at most **two sync connections per polling
cycle**, using durable claims and separate queue sessions. A connection pass
processes at most **20 mapped items** and stops starting further items once about
**45 seconds** have elapsed. An already-started bounded provider operation may
finish after that scheduling budget. Each adapter operation has its own shared
45-second deadline across pagination/subrequests; individual HTTP calls are
bounded to 30 seconds by default. A fair persistent scan position resumes remaining
items in later passes. Completed scans normally become eligible again after one
minute; remaining batches become eligible sooner and still wait for the poller.

Current bounds are 20 connections per workspace, 1,000 tracked mappings/catalog
items per connection, 20 provider pages per snapshot, 50 MiB per file, 2 MiB JSON
responses and 1 MiB individual CalDAV content. Retained mappings/tombstones count
toward connection limits. The activity API returns the latest 100 safe events.
Failures expose bounded codes, not provider response bodies, credentials or remote
content; retry schedules persist in the database.

## Backup and verification

Retain the database, native files, account-security key and existing content key
ring together. The normal cold backup includes the integration worker among its
writers. Restore keeps that outbound worker stopped by default. Review provider
destinations and retained pending work before deliberately resuming dispatch; see
[OPERATIONS.md](OPERATIONS.md) and [INTEGRATION_OPERATIONS.md](INTEGRATION_OPERATIONS.md).

All paths below include the reverse proxy's `/api` prefix:

| Operation | Endpoint |
| --- | --- |
| List / create drafts | `GET` / `POST /api/workspaces/{workspace_id}/sync` |
| Save paused revision | `PUT /api/workspaces/{workspace_id}/sync/{connection_id}` |
| Test / enable / pause / disconnect / queue | `POST /api/workspaces/{workspace_id}/sync/{connection_id}/{test\|enable\|disable\|disconnect\|run}` |
| Read safe activity / conflicts | `GET /api/workspaces/{workspace_id}/sync/{connection_id}/{activity\|conflicts}` |
| Choose a conflict version | `POST /api/workspaces/{workspace_id}/sync/{connection_id}/conflicts/{conflict_id}/resolve` with `choice: "local"` or `"remote"` |
| Start provider OAuth | `POST /api/workspaces/{workspace_id}/sync/{connection_id}/oauth/start` |
| Fixed provider callback | `GET /api/sync/oauth/callback` |

Mutating administrator requests include the current native `password` and optional
local MFA `code`. Connection drafts also include the explicit `consent: true`.
Refer to generated OpenAPI for complete request schemas.

```bash
(cd backend && .venv/bin/python -m pytest tests/test_remote_providers.py \
  tests/test_remote_transport.py tests/test_remote_dav.py tests/test_remote_sync.py -q)
backend/.venv/bin/python frontend/tests/remote-sync-browser.py
```

The provider suites use actual local HTTP protocol fixtures and HTTP/HTTPS
CONNECT/TLS fixtures. The self-contained browser harness uses a real API, worker,
Chromium and isolated DAV/CalDAV fixture. Test-only exact-origin routing does not
create a production private-network exception. It cleans its own services,
credentials and data. [VALIDATION.md](VALIDATION.md) records executed outcomes.
No live Google, Microsoft, Nextcloud or customer CalDAV account, customer tenant
consent or public deployment is established by those tests.
