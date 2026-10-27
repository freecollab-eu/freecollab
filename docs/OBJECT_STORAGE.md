# Customer-owned object storage

FreeCollab supports its existing local immutable blob directory and a real
S3-compatible backend. The operator selects one backend for an installation.
There is no automatic local fallback, automatic migration, bucket creation or
claim that a configured endpoint has been verified.

Use a dedicated bucket or prefix in your own AWS, MinIO, Ceph or compatible
service. The service must implement SigV4, conditional `PutObject` with
`If-None-Match: *`, SHA-256 checksum headers, `GetObject`, version listing and
version-aware deletion. A provider that rejects those operations fails closed.
Validate your selected provider before enabling a production installation.

## Configuration

| Variable | Purpose |
| --- | --- |
| `STORAGE_BACKEND` | `local` (default) or `s3` |
| `STORAGE_PATH` | Local metadata/logo/private export volume; original blob directory in local mode |
| `S3_ENDPOINT_URL` | Your operator-configured HTTPS origin; omit for AWS regional endpoint |
| `S3_BUCKET` | Existing dedicated bucket |
| `S3_REGION` | Signing region, default `us-east-1` |
| `S3_ACCESS_KEY_ID` | Dedicated scoped service access key |
| `S3_SECRET_ACCESS_KEY` | Service secret, passed through secret configuration |
| `S3_SESSION_TOKEN` | Optional temporary-credential session token |
| `S3_KEY_PREFIX` | Safe prefix ending in `/`, default `freecollab/` |
| `S3_CA_BUNDLE` | Optional mounted CA bundle for your private storage TLS certificate |
| `S3_ALLOW_INSECURE_HTTP` | Development/test only, never accepted in production |

TLS certificate verification stays enabled. Requests are restricted to the
configured endpoint origin, including SDK redirect/retry handling. Requests use
explicit service credentials; a missing secret does not select another account's
ambient credentials. Credentials and raw SDK errors are not returned to clients.
Async upload/download/delete routes execute synchronous SDK work in a thread
pool. Native files retain the existing 50 MiB upload/read bound; Office previews
use 10 MiB and search/assistant sources retain their smaller limits.

The service account needs `s3:PutObject`, `s3:GetObject`, `s3:DeleteObject` and
`s3:DeleteObjectVersion` on its object prefix, and `s3:ListBucketVersions` plus
the necessary read/list access on its bucket. Restrict bucket listing to the
configured prefix where your provider supports that condition. No public bucket
or public object policy is required. Temporary credentials must be rotated by
the deployment's secret management and service restart process.

Run `python -m app.storage_probe` explicitly from `backend/` to test your selected
backend. The command creates one random immutable 64-byte probe object, reads and
checksums it, then removes its object versions and verifies absence. It prints
only verified status metadata, never credentials, object content or URLs. Failure
returns a nonzero exit status; failed cleanup may require operator action. The
application never runs this outbound probe automatically at startup. Operations
health labels local private-volume capacity separately from unprobed S3 settings.

Every new object carries a SHA-256 checksum and digest metadata. Reads check
length, configured bound and digest before releasing bytes. File IDs are opaque
32-character hexadecimal keys; user-provided paths and provider URLs cannot
select arbitrary keys. Existing stored filesystem paths are not trusted for
downloads. Uploads cannot overwrite an existing immutable key.

Native server-mode files remain server-readable, as with local storage. S3
transport encryption does not turn those files into end-to-end encrypted files.
Configure bucket encryption at rest/SSE-KMS in your storage service as needed.
Matrix channels retain their existing client encryption and separate homeserver
media storage; this native-blob adapter does not move Matrix media.

## Explicit local migration

Take a verified backup and stop uploads, background sync and destructive jobs
for the maintenance window. Drain pending native file erasure with the worker
using the original storage configuration before changing backends; the migration
command refuses to proceed while its durable erasure queue is nonempty.
Retain the original blob volume. Configure the
destination S3 backend and run from `backend/` with your operator settings:

```sh
python -m app.storage_migrate --source /absolute/original/files --copy
python -m app.storage_migrate --source /absolute/original/files
```

`--copy` reads every database-referenced local version with an exact size check,
writes only missing objects with conditional creation, re-downloads and compares
SHA-256, then updates the stored location metadata after all selected objects
verify. Repeated runs verify existing objects and never overwrite them. Without
`--copy`, the command verifies both copies and writes nothing. A missing or changed
blob stops the operation. Local files are never deleted by this command. The
operation is bounded to 100,000 blobs; larger installations need an explicitly
partitioned operator migration. Test restore and normal authorized downloads
before reopening traffic or retiring the old volume.

## Erasure, backup and restore

Application erasure removes **all listed versions and delete markers for the
exact key**, with a bound of 10,000 entries. A retained object lock, provider
error or missing deletion permission keeps physical erasure pending. Ordinary
native file deletion commits a durable cleanup record with metadata removal,
returns `erasure_pending: true` while bytes remain, and retries in the worker
even when data-control jobs are disabled. Cleanup is bound to its original
storage configuration; drain it before changing roots, endpoints, buckets or
prefixes. A successful `DeleteObject` marker alone is not reported as physical
erasure. Provider replicas, independent backups and snapshots are separate
retention domains and require their own deletion policy.

Enable object versioning and a separate backup/replication policy if required;
versioning by itself is not a verified backup. A complete restore requires a
consistent PostgreSQL/SQLite backup, native object versions/checksums, local
private export/logo volume, application encryption/security keys, and the
Matrix database/media/configuration if enabled. Client-held Matrix content keys
also depend on each organization's device/recovery process. Restoring only S3
objects cannot restore the application or decrypt client-held history.

The S3 adapter and migration command do not create database snapshots, enable
provider versioning, restore backups or claim successful disaster recovery.
Schedule and verify those operations in your chosen infrastructure. Restore to
an isolated installation first, check authorization, exact file versions,
Office editing, export and retained/erased objects, then switch traffic.

`scripts/pilot-backup.py` and `scripts/pilot-restore.py` deliberately refuse an
S3 configuration before stopping writers, invoking Docker or creating output or
target configuration. Their local cold-backup format does not contain remote
objects. This also protects old v1/v2 archives whose configuration selects S3;
a local file-volume archive must not be reported as a complete S3 restore.
Use your provider's consistent snapshot/backup and the manual restore process
above. The scheduled wrapper inherits the same refusal and cannot claim success
for an unsupported S3 snapshot.

## Validation

`tests/s3_fixture.py` is an isolated HTTPS protocol server with a temporary CA.
It independently verifies real AWS SigV4 signatures and SHA-256 headers from the
real boto3 transport; it is not a mocked SDK success. `tests/test_s3_storage.py`
exercises immutable writes, bounds, corrupted bytes, missing-object behavior,
untrusted TLS, incorrect credentials, version/delete-marker cleanup, object-lock
failure, authorized file API downloads, Office reads and an encrypted data export.
This validates the protocol adapter locally; it does not certify every provider's
implementation or constitute a production load/restore test.
