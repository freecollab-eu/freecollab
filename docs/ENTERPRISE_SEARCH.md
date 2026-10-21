# Content and federated search

FreeCollab searches messages, current filenames and decisions locally. Version
0.11 adds current shared-document Yjs title/body, project descriptions, task
descriptions/checklists, activity posts/polls and supported file bodies. Filters
cover channel, person, date, content type and individual document. Results include
their source and channel. Opening a result verifies current authority and revision.

## Native content

`GET /channels/{channel_id}/search/sources` returns authorized current content,
not a query result. The browser performs literal, case-insensitive all-word
matching. The history query is never sent to this endpoint. Native DLP policies
can suppress sources. `POST /search/resolve` takes the returned `reference` and
rechecks account/session, workspace/channel access, native mode, source existence,
current revision and policy. Citations are encrypted, user-bound and expire after
ten minutes. Replaced file versions, deleted files and archived records are omitted.

There is **no persistent plaintext search index**, external vector store or search
engine dependency. The source database remains authoritative, so retained/exported
source data is governed by its existing policies. No claim is made that this
bounded mode provides OpenSearch-scale performance. Large installations should
measure their workloads before choosing a separately implemented indexing adapter.
OpenSearch and pgvector are not silently enabled or required.

Limits are explicit: 200 candidates per content type/channel, 1.5 million output
characters and a 15-second budget checked between sources. An active bounded
storage fetch/parser can finish beyond that budget; parsers have a four-second
wall-time limit. Shared documents support at most 1,000 updates / 2 MiB
input for server extraction. Files support 2 MiB input, 100,000 text characters and
40 PDF pages. UTF-8 text/JSON/XML/YAML, non-macro DOCX/XLSX/PPTX and unencrypted PDF
are passively extracted. Office ZIP/XML limits also apply. Yjs and PDF parsing run
in disposable processes with CPU, memory and wall-time limits. No PDF actions,
scripts, Office formulas, macros, embedded objects or external relationships run.
Unsupported, corrupt, oversized or unverified sources produce an incomplete-search
notice; they are never described as successfully indexed.

## End-to-end encrypted channels

The server rejects source extraction in Matrix channels. Existing client adapters
verify encrypted room, sender, event type, revision and content metadata, then
reconstruct document Yjs state in memory. The browser searches decrypted document,
project, task, post/poll and small UTF-8/text/JSON/PDF file bodies locally. The
12-file, 2 MiB/file and 40-PDF-page limits are visible through incomplete results;
encrypted Office content is not server extracted. Locked sources are excluded.
Closing the dialog releases its plaintext state; no search content or queries are
written to browser storage. On citation open the client re-fetches current access
and verifies the current encrypted source again.

## Personal external delegation

Federated search has a separate form. It never inherits the local history query
or forwards channel content. The user explicitly sends a separate query to one
chosen personal provider. Connections belong to one current user and workspace;
workspace/channel administrators do not gain access to another person's results.
Channel membership is never substituted for external-provider ACLs.

Customers issue their own **personal, read-only delegated access tokens** and
register them using `POST /workspaces/{workspace_id}/enterprise-search/connections`:

```json
{
  "name": "My Google collection",
  "provider": "google",
  "config": {"folder_id": "customer-selected-folder"},
  "access_token": "customer-issued-personal-delegated-token",
  "delegated_access_acknowledged": true
}
```

Microsoft config is `{"drive_id":"...","folder_id":"..."}`. The product does
not supply a shared vendor SSO app. The customer's identity integration obtains
and replaces tokens. This search grant does not currently run its own OAuth
authorization/refresh flow: expired tokens fail closed and need replacement.
Credentials are encrypted at rest and omitted from all responses. Deleting the
connection removes the delegation and invalidates outstanding citations.

Google uses the Drive `files.list` fullText query constrained to direct children
of the selected folder, then fetches each resource again with the same token.
Microsoft uses Graph `driveItem/search` rooted at the selected folder and walks
current ancestors under the same delegation. Current provider permissions and
collection boundaries are rechecked for every hit and again on result open.
Google/Microsoft quotas and resource permissions still apply. Application-wide
admin tokens are outside the supported contract; callers must supply their own
personal delegation. No content is copied into a FreeCollab provider index.

Query with `POST .../connections/{connection_id}/query`:

```json
{"query":"design proposal","connection_revision":1,"limit":10,"share_query_with_provider":true}
```

Results carry an actor-bound ten-minute `reference`. `POST /enterprise-search/resolve`
accepts that reference and performs a fresh provider resource lookup before the
UI navigates. The API rechecks native account/session and connection after network
I/O, including concurrent disconnect, workspace removal or logout. Provider
errors omit response bodies and secrets. HTTPS destination validation, current
public DNS pinning, hostname/certificate validation, response bounds and a shared
operation deadline apply. Redirects are refused.

## Custom connector protocol

Use provider `custom` and config:

```json
{"search_url":"https://search.customer.example/search","resolve_url":"https://search.customer.example/resolve","external_subject":"customer-user-subject"}
```

Both HTTPS endpoints must have the same origin. The personal bearer delegation is
sent only to these customer-configured endpoints. Search receives
`{"query":"...","limit":10,"subject":"customer-user-subject"}` and returns:

```json
{"items":[{"id":"resource-1","title":"Design proposal","snippet":"Optional actual excerpt","url":"https://docs.customer.example/1","acl":{"subject":"customer-user-subject","allowed":true}}],"has_more":false}
```

Resolve receives `{"id":"resource-1","subject":"customer-user-subject"}` and
returns the same single resource object with a **fresh current ACL decision**.
Subject mismatch, absent/false `allowed`, moved resources and provider denial are
excluded or rejected. The customer connector is responsible for validating that
the bearer token identifies the supplied subject and checking its backing system;
the contract is explicit, not inferred from a FreeCollab channel grant.
