# Documents and calendars

Open **Integrations → Documents** or **Integrations → Calendar**, then choose a channel. Every operation checks current channel and workspace membership. Guests see only channels they can access. Members can add items; the author or a channel administrator can revise or remove them. Revisions are immutable and stale edits return a conflict instead of overwriting another person's work.

## Document providers

The **Documents** link feature stores a reference to the original document. It does not copy or fetch the remote file, access its provider account, mint a public sharing link, or change its permissions. **Open document** deliberately opens the provider in a new tab with no referrer. The destination still enforces its own access rules.

| Provider | Supported examples |
| --- | --- |
| Nextcloud | `https://cloud.example.com/s/Abc123`, `/index.php/s/Abc123`, `/f/123`, `/apps/files/…` |
| Google Drive | `https://drive.google.com/file/d/FILE_ID/view`, `/drive/folders/FOLDER_ID`, and Google Docs/Sheets/Slides/Forms/Drawings `/…/d/ID/…` links on `docs.google.com` |
| SharePoint | HTTPS document paths under an organization's `*.sharepoint.com` or `*.sharepoint.de` hostname |

URLs must use HTTPS with no user/password, nonstandard port, query parameters or fragment. Remove tracking parameters such as `?usp=sharing` from a copied link before saving it. Hostname suffix spoofing, IP-literal URLs, control characters and active URL schemes are rejected. Nextcloud is self-hostable, so a selected Nextcloud hostname identifies the chosen destination; it is not a FreeCollab attestation of that server's ownership or software.

Document links need no OAuth account or synchronization worker. The separate **Sync** feature below copies supported file content and versions only after explicit provider authorization. Neither feature changes provider sharing permissions or automatically grants remote access. Never paste an OAuth token or password into a document URL.

## Events and calendar applications

Create a timed event with an explicit IANA time zone, or an all-day date range. The all-day editor includes both selected dates. Internally and in ICS, the end date is exclusive, as required by RFC 5545. Timed events store an exact instant with the chosen display zone; exports use UTC to retain that instant across daylight-saving changes. Nonexistent spring-clock wall times are rejected. Ambiguous autumn-clock times select the first occurrence, as defined by RFC 5545.

**Download ICS** exports an individual event; **Export ICS** exports the currently accessible channel calendar. **Import ICS** parses up to 100 individual events from a file no larger than 1 MB and shows a review before adding them. Existing imported UIDs and FreeCollab-generated event UIDs are skipped, making reimport safe. Events have editable, immutable revision histories. Importing does not send invitations or RSVP responses.

Supported import data includes UTC times, floating times interpreted in the current user's time zone, IANA `TZID` times, all-day dates, title, description, location, UID and relative display reminders. RFC line folding and text escaping are handled locally. Invalid structures, duplicate singleton fields, unsupported recurrence (`RRULE`, `RDATE`, `EXDATE`, `RECURRENCE-ID`), email/audio alarms and end-relative or absolute alarms are rejected explicitly. Expand recurring events into individual instances before importing. Custom non-IANA `VTIMEZONE` definitions are not interpreted.

Reminders are RFC `VALARM` display alarms in the downloaded calendar file. They run in the calendar application after the user adds that file; FreeCollab does not promise browser push reminders from this feature. The user can also explicitly choose **Google Calendar** or **Outlook Calendar**, which opens the provider's event composer with the event's details. Those compose links are one-time handoffs, not background account synchronization. The provider may apply its own default reminder settings.

Public subscription feeds and feed bearer tokens are not implemented. Local ICS exchange remains available without connecting an account. For a native channel with an enabled **Sync** connection, supported event edits and deletions can also propagate through that connection; previously exported ICS files and independent calendar-composer copies are not automatically tracked.

## Cloud synchronization

Open **Integrations → Sync** with a personal native workspace administrator account that also administers the selected channel. Each connection grants one non-direct channel and one remote folder or calendar. Files and calendars use separate connections. The connection includes existing and future supported channel content; it is not limited to the administrator's own uploads or events.

See [REMOTE_SYNC.md](REMOTE_SYNC.md) for the detailed connection, provider and recovery contract.

| Provider | Files | Calendar | Customer configuration |
| --- | --- | --- | --- |
| Google | Drive folder, ordinary file bytes and read-only PDF exports of Google Docs/Sheets/Slides. | Selected Google calendar with supported standalone events. | Customer OAuth client ID/secret, folder/calendar ID and explicit account consent. |
| Microsoft | Selected OneDrive/SharePoint drive and folder, with ordinary file bytes/versions. | Selected Outlook calendar. Events with attendees are read-only. | Tenant-specific directory UUID, customer OAuth client ID/secret, drive/folder or calendar ID. |
| Nextcloud | WebDAV folder. | CalDAV calendar collection. | Exact collection URL, username and app-specific password. |
| Generic CalDAV | Not applicable. | CalDAV calendar collection. | Exact collection URL, username and app-specific password. |

### Connect a provider

1. Ask the installation operator to set `SYNC_ENABLED=true` and run the existing `integration-worker` profile with the API's database and independent keys. With the flag off, configuration drafts can be prepared but background dispatch stays disabled. See [INTEGRATIONS.md](INTEGRATIONS.md).
2. Choose **Add cloud connection**, provider, files/calendar and a native channel you administer. End-to-end encrypted channels cannot be selected for server background sync.
3. For Google/Microsoft, register the displayed exact OAuth callback, `https://collab.example.org/api/sync/oauth/callback`, in the customer's application. Supply its client ID and secret plus the resource identifiers. Microsoft requires a tenant UUID rather than a generic multi-tenant authority.
4. For Nextcloud/CalDAV, supply the exact public HTTPS collection URL on port 443 and an app-specific credential. Directory discovery and recursive account-wide synchronization are not performed.
5. Acknowledge sharing existing and future channel files/events, including two-way changes and deletions. Save the paused draft with native password/MFA confirmation.
6. For Google/Microsoft, choose **Connect provider account**, confirm the native administrator identity and complete the provider's consent. This cloud grant is separate from organization SSO; sign-in alone grants no Drive or Calendar access.
7. **Test connection**, then **Enable sync** while the saved test is current (one hour). Each administrative action requires native password/MFA. **Sync now** queues a cycle; activity confirms completion separately.

Google file consent requests Drive read/write scope (`https://www.googleapis.com/auth/drive`); calendar consent uses `calendar.events`. Microsoft uses delegated `Files.ReadWrite.All` or `Calendars.ReadWrite` with `offline_access`. These provider permissions can be broader than the selected resource. FreeCollab independently confines operations to the configured folder/calendar; use a dedicated resource appropriate for sharing with the channel.

OAuth uses state, S256 PKCE, an encrypted verifier and an HttpOnly browser-binding cookie. The callback rechecks the initiating native session, account version, administrator grant and connection revision. It does not replace the native login or expose provider tokens in the URL. Client secrets, app passwords, access tokens and refresh tokens are encrypted with `ACCOUNT_SECURITY_KEY` and never returned by configuration reads. Preserve this key with backups.

Leaving a credential replacement empty keeps the saved value. Changing the selected resource, OAuth client or grant pauses the connection and requires the appropriate reauthorization/test. **Disconnect** pauses it and erases locally stored provider credentials. It does not revoke the application at the provider or delete previously shared copies; remove upstream consent in the provider's own settings when needed.

### Synchronization and conflicts

The worker maintains durable item mappings, remote versions, local versions, cursors, leases and retry schedules. It checks the current connection and authorizing administrator's workspace/channel permissions around I/O and before mutations. Removing the grant or enabling secure-channel preparation stops the connection. A provider outage does not block other integration queues.

Remote edits create normal immutable local file/calendar revisions. Native synchronized file deletion is a retained tombstone, preserving previous bytes/versions. Supported deletions propagate after a known baseline; a missing or incomplete listing is not treated as proof that everything was deleted. Google Drive file removal moves the file to trash, with conditional version checks. Graph, DAV and calendar removal follow the provider's deletion/retention semantics. Local history retention does not promise that a remote provider can restore a deleted copy.

If both sides changed, **Conflicts** requires **Use channel version** or **Use provider version** with native confirmation. Resolution is queued and rechecks versions; another change returns to conflict instead of overwriting silently. Read-only exports cannot accept a channel-version push or upstream deletion. Uncertain writes are reconciled using stable identities rather than unconditionally creating duplicates. DAV/Graph resources created by FreeCollab may carry an `fc-…--` name prefix for this recovery mapping; the local display name omits that prefix.

The UI shows queued, active, paused, failed and conflict states separately. Activity omits credentials and content. Channel members see an external-sharing notice in Files/Calendar; they do not receive provider credentials or configuration rights. Pausing stops future cycles but does not remove previously shared provider data.

### Supported scope

- File synchronization is **depth one**, using direct children of the selected folder. Select nested collections explicitly as separate connections. Folder trees, provider permission/ACL mirroring and account-wide discovery are not implemented.
- File size and content types follow channel uploads, up to 50 MiB. Supported office documents transfer as bytes; this does not add an inline Word/Excel/PowerPoint/OpenDocument editor or preview. Google-native Docs/Sheets/Slides are imported as read-only PDF exports, not editable native provider documents.
- Calendar synchronization handles supported single timed/all-day events and display reminders. It does not implement recurrence expansion, meeting invitations, RSVP handling, attachments or arbitrary custom calendar metadata. Graph meetings with attendees are read-only to avoid scheduling side effects. Unsupported calendar structures are refused or surfaced as read-only/conflicts rather than flattened; choose a dedicated calendar of supported standalone events.
- Snapshot/operation/body limits are finite: currently 1,000 items per connection, at most 20 cloud pagination pages, bounded response sizes and request deadlines. A truncated/failed snapshot does not advance as a successful complete listing or infer deletions. This is a bounded team integration, not an unlimited drive migration tool.
- Only public HTTPS endpoints on port 443 are supported. Private-network DAV services need a separately designed deployment/transport policy; there is no production private-host bypass. Redirects, unsafe pagination/download hosts, private/mixed DNS destinations, XML DTD/entities and oversized bodies are refused. Connections retain original-host TLS verification and inherited proxy/CA trust.
- **End-to-end encrypted Matrix channels are unsupported for server background sync.** The worker has no message keys, does not receive their decrypted content and never offers a plaintext fallback. Their document references and explicit local ICS exchange remain available.

## Encryption and privacy

For native channels, document/calendar payloads are encrypted at rest with the existing content keyring. The server receives this content through the authenticated API, matching the explicit server-encryption mode. Native file bytes remain unencrypted at the application-storage layer. An enabled sync connection deliberately shares supported content with its selected provider; protect native storage and backups accordingly.

For secure channels, titles, provider URLs, notes, event dates, locations, descriptions, external UIDs and reminder settings are encrypted on the client inside `eu.freecollab.integration.documents` or `eu.freecollab.integration.calendar` Matrix application events. Native records contain opaque event references, revision/author identifiers and deletion state. The client checks the room, full Matrix sender identity, application type, item ID, kind and revision before accepting decrypted fields. The native API rejects plaintext writes even while a secure channel is preparing. Channels with retained native integration history cannot be relabelled as newly end-to-end encrypted.

The document-link feature does not search or preview remote file contents. Synchronized supported file bytes are separate native channel files with their normal local access/version controls. Opening the link reveals access to its external provider. Calendar export writes decrypted details to the user's selected local file. Opening an external calendar composer passes those details to the chosen provider. These actions are explicit and do not run as a consequence of opening a secure channel.

## Verification

```bash
(cd backend && .venv/bin/python -m pytest tests/test_document_calendar_integrations.py -q)
(cd frontend && node --test tests/document-calendar.test.mjs)
CONTENT_DATABASE_URL=sqlite:////absolute/path/to/disposable-api.db \
  CHROMIUM_PATH=/usr/bin/chromium \
  backend/.venv/bin/python frontend/tests/document-calendar-browser.py
```

The document/calendar browser harness requires the running API database specified above, a private Matrix homeserver and the Vite client. It uses fixture accounts, actual component interactions, downloaded/imported ICS and two independent Rust crypto devices. It never opens a real provider link or sends calendar content to an external provider. Backend tests verify access, encrypted native storage, immutable revisions and the secure-content fence; Node tests cover DST, RFC escaping/folding, malformed/active calendar content, provider spoofing and encrypted metadata routing.

The remote sync suites are `backend/tests/test_remote_sync.py`, `test_remote_providers.py`, `test_remote_dav.py` and `test_remote_transport.py`. `frontend/tests/remote-sync-browser.py` exercises a real isolated API/worker/UI against local HTTPS DAV/CalDAV fixtures. Provider protocol fixtures are separate from real customer Google/Microsoft consent, private DAV deployments or an independent security audit. See [VALIDATION.md](VALIDATION.md) for executed results.
