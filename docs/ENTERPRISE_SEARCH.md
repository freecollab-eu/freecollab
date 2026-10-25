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

The literal source endpoint has no persistent plaintext index. **Search by meaning**
also provides a purpose-encrypted persistent native index, documented below. Both
use the current source database as authority; no external vector store, OpenSearch
or pgvector service is silently enabled.

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

## Persistent semantic retrieval and reviewed RAG

**Search by meaning** uses real latent semantic analysis (`local-lsa-v1`): bounded
TF/IDF vectors are projected into a truncated corpus-derived space. Co-occurring
concepts can match without the literal query word. It is an offline statistical
model, with no downloaded neural model or external AI request. It does not claim
universal synonyms, cross-language neural retrieval, or unlimited index size.

`POST /workspaces/{workspace_id}/semantic-search` accepts
`{"query":"material choices","channel_ids":["current-channel"],"limit":10}`.
Only current workspace/channel members can query. The native index retains at
most 1,000 source records and 2 million source characters per channel, up to 4,096
terms and 12 latent axes. It scans current revisions, reuses unchanged extracted
sources, replaces changed sources and removes deleted, archived, restricted or
replaced sources on the next synchronization. A changed corpus recomputes its
projection. The extraction pass has a 12-second between-source budget; parser
limits still apply. Incomplete coverage is explicit. A query covers at most 50
channels and returns at most 25 hits.

`ChannelSemanticIndex` stores an authenticated encrypted payload bound to its
channel and index purpose. Content can be read by the authorized native server.
Every result gets fresh actor-bound citations and current membership, governance
and source-version checks at read. Persistent stale cache entries cannot grant
access. Enabling channel encryption deletes the native cache before committing
the durable preparing fence; personal erasure/retention also invalidate affected
channel caches. Encrypted channels never enter this server index.

The source catalogue covers native shared pages, messages, projects, tasks,
posts/polls, passive file text, decisions, clips/transcripts, linked documents,
calendar events and workflows. Binary files remain organizable references even
when their bodies have no passive text extractor.

Reviewed AI retrieval uses these same sources. The user chooses current channel
or explicit workspace scope, reviews excerpts and recipients, and selects sources
before disclosure. Search alone never sends content or a query to a model.
The private runtime derives sealed canonical descriptors from fresh public
citations for durable jobs. Its internal `resolve_canonical(...,commit=False)`
rechecks the same live ACL/version/policy without extending public citation TTL
or committing an export/queue caller's transaction. No public route accepts an
untrusted canonical descriptor as an expiry bypass.

## End-to-end encrypted channels

The server rejects source extraction in Matrix channels. Existing client adapters
verify encrypted room, sender, event type, revision and content metadata before
reconstructing document Yjs state. Device search covers the same supported native
resource families, actual encrypted messages and verified peer resources. Peer
legacy documents are reconstructed through the actual read-only federated Yjs
reader, without creating a second writer or fabricating native IDs.

The device's **Search by meaning** projects the verified corpus in a dedicated
worker and keeps its index encrypted in the account/origin-isolated IndexedDB
vault using a nonextractable key. Embedded memory sessions retain it in memory.
Queries and decrypted text never enter native search APIs or an external model.
Fresh source versions are read before reuse/opening; previous cached content is
never substituted for a currently missing source. Account transitions invalidate
the active operation. Corrupt, locked, oversized or competing revisions are
excluded with incomplete coverage. Text/PDF extraction has the existing 12-file,
2 MiB/file and 40-page limits; encrypted Office content is not server extracted.

## Personal external delegation

Federated search has a separate form. It never inherits the local history query
or forwards channel content. The user explicitly sends a separate query to one
chosen personal provider. Connections belong to one current user and workspace;
workspace/channel administrators do not gain access to another person's results.
Channel membership is never substituted for external-provider ACLs.

For Google/Microsoft, **Integrations → Provider accounts** now supports customer-
owned OAuth applications, personal read-only consent, automatic refresh and
revocation. Registrations remain separate from workspace SSO. See
[PERSONAL_OAUTH.md](PERSONAL_OAUTH.md) for exact scopes, deployment and source-
session boundaries. The manual token option remains compatible: customers can
register their own **personal, read-only delegated access tokens** using
`POST /workspaces/{workspace_id}/enterprise-search/connections`:

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
not supply a shared vendor SSO app. In the manual-token path the customer's
identity integration obtains/replaces tokens; manual tokens do not acquire an
inferred refresh grant. The separate OAuth path renews its own source-bound grant.
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

## Optional operator-owned OpenSearch k-NN

An installation can opt into a real OpenSearch 2.19-compatible Lucene/HNSW vector
mirror. It authenticates with a dedicated operator-owned account, verifies TLS
certificates/hostnames (including an explicitly mounted private CA), validates
OpenSearch's product/mapping identity, and uses actual k-NN queries. Configuration
is deployment-only; users cannot enter arbitrary backend URLs in the product.
Public demo configuration rejects these endpoints/credentials entirely.

```dotenv
SEMANTIC_OPENSEARCH_URL=https://opensearch.customer.internal:9200
SEMANTIC_OPENSEARCH_INDEX=freecollab-semantic-v1
SEMANTIC_OPENSEARCH_USERNAME=freecollab-semantic
SEMANTIC_OPENSEARCH_PASSWORD=customer-managed-secret
SEMANTIC_OPENSEARCH_CA_BUNDLE=/run/secrets/opensearch-ca.pem
SEMANTIC_OPENSEARCH_TIMEOUT=8
```

Both Compose application and worker environments inherit these variables. Mount
any private CA read-only at the configured path. Use a dedicated index/account
with cluster-main/product-info read plus index create/mapping read, bulk/index,
search/count, refresh and delete-by-query permissions. Inherited HTTPS proxy and
NO_PROXY routing remain in force; a private CA extends the inherited trust store. Credentials are never sent to the browser or logged in error bodies.
A private container/loopback HTTP endpoint is supported for an operator-owned
isolated network; public addresses require HTTPS. TLS verification cannot be
disabled. Passwords belong in the deployment's secret management.

The mirror stores normalized 12-dimensional native LSA vectors and canonical
channel/kind/source/version/generation references, with no document/message text,
filename, query text or Matrix-decrypted vector. Vectors still derive from native
content and need protected storage/backups. Each channel has its own corpus basis;
its query vector is generated for that exact channel, with channel/generation
filters applied inside the k-NN query. Vectors from incompatible channels are
never searched with one borrowed query vector.

A changed corpus performs bounded bulk vector upserts and real deletion of the
previous generation. Unchanged generations avoid repeated bulk writes. Current
canonical source ACL/version/governance is checked for returned hits. Publication,
query and privacy purge share the native channel writer fence, preventing a late
native upsert after encrypted-channel activation. Erasure/retention and encryption
activation delete actual mirror records before acknowledging completion. Partial,
timed-out or unavailable backend deletion rejects/retries the operation. The
backend response is not silently relabelled as local success.

This adapter uses the same explicit per-channel corpus/extraction bounds as the
bundled engine. It provides a configurable vector service and indexed nearest
neighbor queries, not an unmeasured claim of unlimited Slack/OpenSearch scale.
Operators must benchmark their own workloads and coordinate removal of an old
backend before changing endpoints/indexes. No service or secret is bundled by
default. `backend/tests/test_semantic_opensearch.py` includes operator/TLS,
transaction, current ACL and actual service tests. The optional proof launcher
uses an official SHA512-verified standalone OpenSearch bundle, disposable data,
fixture credentials and a generated trusted local CA; it never disables TLS
verification or contacts a customer's service.
