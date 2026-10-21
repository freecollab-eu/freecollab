# Enterprise governance

FreeCollab supports an organization above multiple workspaces, native content
policies, case and custodian preservation, evidence collection and review, usage
counts, and an optional customer Vault Transit adapter for server encryption.
The default SQL-backed management and preservation features run without an
external provider. Vault requires a customer-operated service and credentials.

## Organization administration

Open **Integrations → Governance** using your personal workspace-owner account.
Create an organization and attach each workspace you own. A workspace can have
one parent organization. Attaching it requires both a current organization
owner/admin role and a current owner role in that workspace. Detaching requires
both owner roles. Organization owners can appoint owners, admins and auditors;
at least one active owner must remain.

Organization roles provide management metadata and aggregate usage. They never
create workspace membership, channel membership, Matrix room membership, content
keys or an administrator decryption capability. Workspace names, membership
counts, channel counts, native message counts and stored-file byte counts are
organization management metadata. Usage does not return message text, private
channel names, filenames, search queries or per-person message activity. Counts
are obtained from the current SQL records rather than inferred from server
inspection of Matrix history.

The optional organization SSO rule requires members to use **their workspace's
own configured identity provider**. Every attached workspace must have its own
enabled provider before the rule can be enabled or the workspace attached.
Workspace owners retain personal emergency access. A workspace-scoped SSO login
cannot configure the parent organization or grant access to sibling workspaces.
Roles and policy are checked from current SQL values after mutation locks.

Country and region labels record the hosting operator's chosen location. They
do not relocate databases or make a managed residency guarantee. Choose the
actual database, object-store, Matrix, backup, KMS and processing locations in
your own deployment and provider contracts.

## Native content policies (DLP)

Workspace owners configure explicit policies. Installation does not enable
detectors or block existing content by default. Policies support:

| Detector | Matching behavior |
| --- | --- |
| Protected phrase | Case-insensitive literal matching; phrase encrypted in SQL |
| Payment card | Bounded card-number candidate matching plus Luhn checksum |
| AWS access key ID | Fixed AKIA/ASIA access-key ID format; not every credential format |
| Email address | Fixed bounded email-address pattern |

Choose **Warn and audit** or **Block**, and choose the native surfaces to which
the policy applies. Rules are deliberately fixed classifiers; arbitrary regular
expressions and downloaded scanning code are not executed. These detectors have
false positives and false negatives and do not replace a dedicated enterprise
DLP service or malware scanner.

Message, thread, offline and scheduled-message creation, native edits, direct
file uploads and versions, Office saves, cloud file imports, sync imports,
native document/task/decision/feed fields, app cards, assistant outputs and
native search use the shared policy service. Search suppresses blocked native
results. Policy changes take effect on subsequent operations. Existing database
rows are not rewritten or removed by enabling a policy; ordinary historical
message reads and an explicitly authorized evidence export remain available to
their existing authorized readers.

Plain UTF-8 text, CSV, Markdown, JSON and XML files up to 2 MiB are scanned. The
filename is also scanned. PDF, Office containers, images, archives, binary files,
invalid UTF-8 and larger files cannot be reliably inspected by this engine.
Choose **Block files that cannot be inspected** on a blocking file policy to
deny those uploads and imports. Without that explicit option, the policy scans
the filename only for such files; it does not claim a successful content scan.
Native collaborative documents use binary Yjs updates; a blocking policy with
the document surface denies those updates rather than pretending to scan them.
Plain document titles and comments use text policy matching.

Blocked writes return HTTP 423 and the stable `native_dlp_blocked` code. Warnings
return `policy_warnings` containing policy IDs, and record `dlp.warn` audit
metadata. Matched text, literal values, file bytes and credentials are excluded
from governance audit records. UI translations fall back to English for new
governance strings when a selected language has no translation yet.

Matrix messages and encrypted attachments stay client encrypted. This server
cannot inspect their plaintext. Native DLP rejects use as a Matrix ciphertext
scanner. This release does not claim an authoritative client-side Matrix DLP
agent or independent enterprise inspection of secure channels.

## Preservation cases and eDiscovery

Create a case before collecting evidence. A case starts active immediately and
can cover the entire workspace, selected channel IDs, selected custodians, or a
combination. Channel and custodian scopes are intersections: selecting both
preserves those authors' native content in those channels. Empty selections mean
all channels or all authors respectively. Retain departed custodians' opaque
user IDs in case records; removing membership does not silently release a hold.

Case creation and release lock the same native channel rows used by writes and
deletion. A deletion either completes before the hold exists or observes the
committed hold. Active cases prevent message overwrites, user and moderated
message deletion, physical file deletion, and retention purges within their
scope. Native immutable revisions and file versions already retain their older
content. Bulk deletion conservatively stops when it could mix held custodians
with other authors. A previously queued retention purge is checked again when
it runs. Individual case release never clears a different case or the separate
workspace retention policy's hold flag. The UI reports both independently.

Opening a case does not restore content erased before preservation began. This
includes physical cleanup of a deletion whose metadata transaction had already
committed before the case started. New preservation cannot restore that record.
This is native server preservation: Matrix history, user devices, external providers,
third-party recordings and independent backups require their own legal and
technical preservation processes. No case grants additional content access.

Case evidence collection requires a current personal workspace owner, password
and MFA when enabled, a live session, active preservation and current membership
in every selected native channel. The native-only collector rejects Matrix
channels. It collects **full authorized channel context**, including surrounding
authors, rather than promising a custodian-only content filter. A case bound to
specific channels cannot collect outside them. The preservation reference is
bound atomically to the export job and checked again when the job runs.

The existing bounded export engine creates a real encrypted-at-rest archive,
records the plaintext archive's SHA-256 and byte count, rechecks current access
at download, and streams a private download that is removed after use. Exports
include shared native app cards and the requester's own currently authorized
private app drafts. Other members' private drafts, external app request arguments,
other members' private assistant history, credentials and content keys are
excluded. Archive metadata includes the case ID, revision and preservation
scope. Review records mark the collection reviewed, relevant or excluded; UI
supports marking reviewed. Downloads expire according to the data-control
engine's short-lived artifact policy. Export jobs and reviews are metadata,
not a replacement for an organization's durable evidence repository.

The application audit trail records case preservation, release, collection,
review, policy and role changes. The API returns at most 200 recent events. SQL
administrators can change the database; this is not an externally attested,
immutable compliance ledger. Export required records to your approved audit and
evidence systems and retain their backups under your own policy.

## Customer Vault Transit keys

The native encryption adapter uses a fresh random 256-bit data-encryption key
per encrypted value and libsodium SecretBox authenticated encryption. Vault
Transit encrypts/decrypts only that data key. Message plaintext is never sent to
Vault. The envelope includes the exact provider reference and the Vault
ciphertext version, and authenticates the channel context within the encrypted
content. Neither plaintext data keys nor Vault tokens are written to content
rows. Data keys are not cached, so revoked Vault access affects subsequent reads.

Configure the hosting operator's environment:

```dotenv
ENTERPRISE_VAULT_URL=https://vault.example.com
ENTERPRISE_VAULT_MOUNT=transit
ENTERPRISE_VAULT_KEY=freecollab
# Store through your deployment secret manager, never in Git:
# ENTERPRISE_VAULT_TOKEN=<customer-provided scoped token>
# Optional Vault Enterprise namespace:
ENTERPRISE_VAULT_NAMESPACE=
```

Provision a Transit encryption key and grant only `update` on
`transit/encrypt/freecollab` and `transit/decrypt/freecollab`. Provisioning, token
renewal, provider availability, rotation and backup remain the operator's
responsibility. FreeCollab does not create, rotate, export or destroy a Vault
master key. Renew the token through your deployment secret mechanism. A
configured URL without a working token fails closed and never switches new
writes to the local key ring.

Vault must be reachable over public HTTPS on port 443 through the configured
egress path. The shared transport performs fresh public-address resolution,
pins the connection, verifies the original TLS hostname, bounds requests and
responses, rejects redirects and private/link-local destinations, and retains
the inherited outbound proxy. An internal Vault deployment needs an approved
public HTTPS gateway; there is no setting that disables TLS or SSRF defenses.
Use your installed CA trust for an enterprise gateway certificate.

New native ciphertext uses `fc2:vault:`. Existing `fc1:` content continues to use
its retained versioned local key ring; enabling Vault does not rewrite history.
Keep `MESSAGE_ENCRYPTION_KEYS` and any explicit legacy key required by existing
data. Keep the original Vault URL/mount/key/namespace and every Vault key version
needed by retained ciphertext. Vault can rotate its own key version while
retaining old versions; this release does not automatically rewrap existing
data keys or migrate ciphertext to another provider/key reference. Never remove
old key versions merely because a new active key exists.

This instance-level adapter is intended for customer-operated deployments. It
does not assign a different Vault key per parent organization in a shared
installation. Native server encryption is not end-to-end encryption; Matrix
device keys and recovery remain a separate client-controlled system. Encrypted
export artifacts use the existing private archive key mechanism and native file
storage uses its configured storage/provider controls; enabling this adapter
does not retroactively claim encryption coverage for legacy file bytes.

## API and validation

- Organization management: `/organizations` and `/{id}/members`, `/policy`,
  `/workspaces/{workspace_id}`, `/usage`, `/audit`.
- Workspace controls: `/workspaces/{id}/governance`, `/dlp`, `/cases`,
  `/cases/{case_id}/release`, `/cases/{case_id}/exports`, and `/audit`.
- Existing data-control job processing and downloads remain under
  `/workspaces/{id}/data-controls`.

All management responses use private/no-store headers. Configuration changes
require personal-account password/MFA confirmation; scoped tenant sessions are
not elevated to global organization management. All models are additive SQL
tables; no existing content column is silently altered.

`test_enterprise_governance.py` exercises real private API writes, roles, scoped
SSO, file bytes, evidence archives, late holds and concurrent hold/delete
serialization. `test_enterprise_kms.py` uses a real local verified HTTPS Transit
protocol fixture, with explicit test-only network redirection, to exercise
wrapping, authenticated channel context, retained key versions, provider
reference changes, immediate token revocation, redirects, TLS hostname failure
and unsafe destinations. These are protocol fixtures, not proof of live customer
Vault credentials or a production provider's uptime.
