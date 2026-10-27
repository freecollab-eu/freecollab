# Security and quality cleanup — 2026-10-27

This internal review follows `f35d499`. It covers privilege changes, native
content policy, file erasure, frontend account boundaries, dependency advisories,
build contexts and Docker acceptance. Reproduced findings were repaired and
given regression coverage. This is not an independent external audit or a new
assessment of the cryptographic protocols.

## Findings and repairs

| Severity | Finding | Repair |
| --- | --- | --- |
| High | An administrator request could create a replacement administrator after demotion, blocking or revocation during password hashing. Concurrent creation could also exceed the administrator limit. | [Management](../backend/app/api/admin.py) serializes current administrator/actor/target state, fences the browser session and any native parent, then rechecks authority before publishing. |
| High | Legacy posts/comments bypassed preservation holds and native message DLP, including replies and comments on files. | [Posts](../backend/app/api/posts.py) and [comments](../backend/app/api/comments.py) apply existing preservation and content-policy guards. Channel-lock waits also refresh actor and membership authority. |
| Medium | Asynchronous file-provider lookup, encryption or publication review could continue with another account's newly selected identity. | [File transfers](../frontend/src/lib/files-api.ts) retain the original owner. [Encrypted files](../frontend/src/lib/secure/files.ts) also retain the original Matrix client, generation and room binding through preparation, result acceptance and the final SDK/HTTP publication guard. |
| Medium | A delayed secure API JSON body could restore the previous account's bindings after a session or encryption reset. | [Secure requests](../frontend/src/lib/secure/index.ts) recheck the original token and encryption generation after parsing and before updating bindings. |
| Medium | A storage failure after file metadata deletion permanently lost the identities needed to retry blob erasure. | [Deletion](../backend/app/api/files.py) atomically commits indexed [tombstones](../backend/app/models/file_version.py) with metadata removal. An [always-on lane](../backend/app/services/file_erasure.py) retries after commit without holding channel locks across storage I/O. |
| Medium | Local credentials and signing material could enter remote Docker contexts despite being ignored by Git or unused in the final image. | Root, backend and frontend Docker exclusions now cover nested cloud/SSH/package credentials, signing keys, databases and private reports. Git exclusions and [operations guidance](OPERATIONS.md) match. |
| Reliability | Password/reset/verification writes used the reverse account-lock order from management. Logout could commit while a previously authorized management mutation was publishing. | [Account writes](../backend/app/api/account.py) use User → AccountSecurity ordering and revalidate links/password/MFA state. An explicit [session fence](../backend/app/services/request_guard.py) orders publication against browser and native-parent logout. |
| Build validation | Docker CI built a website without a demo URL, then expected an iframe. All image builds passed, but browser acceptance failed. | [CI](../.github/workflows/ci.yml) supplies an owned-loopback demo URL and verifies its exact frame origin/path and CSP. These read-only Docker checks also run on pull requests. |

## Executed evidence

Focused groups overlap and must not be added together as a unique total.

- Complete frontend Node suite: **914 passed**, no failures, skips or
  cancellations. TypeScript and app, embedded-client and product-demo builds
  passed.
- Frontend account-boundary regressions: **92 focused tests passed**, including
  57 new cases across the two repairs. Sixteen initial cases fail against the
  original implementations; 36 encrypted-provider lifecycle cases fail against
  their pre-correction source. Positive cases retain legitimate same-actor and
  delayed-review behavior. The final adapter/HTTP boundary uses real guards with
  SDK stubs, without real accounts or external network activity.
- **Eight actual PostgreSQL tests passed**: password/reset/MFA lock ordering,
  both management/logout orders for browser/native-parent sessions, and
  concurrent reset/email verification. Prior failures were reproduced. Tests
  create and drop only their own random schemas; CI's existing PostgreSQL test
  service runs the permanent module automatically.
- Content/storage compatibility: **192 passed**; focused governance, revocation
  and erasure: **65 passed**; final isolated erasure/index check: **six passed**.
  Administrator mutation coverage includes **12 new passing regressions** and
  related passing account/session/authentication suites.
- Actual Docker website image and Chromium acceptance: **eight groups passed**.
  A wrong-origin negative control failed as intended. The owned iframe handoff
  is inspected and aborted; this does not claim an external customer instance.
- Fresh PyPI/pip-audit and full npm lock audits: **zero known advisories** across
  both Python locks and all four npm projects. No lockfile upgrades were needed.
  [Dependency notes](DEPENDENCIES.md) record the scope.
- Fixed-signature credential scan: **2,656 historical text blobs** across
  reachable refs and **1,358 current source files**. Its one Slack-token match
  is explicitly synthetic test data. Values were not logged. Binary objects and
  four oversized historical/image objects were excluded; embedded archives and
  secret stores were not forensically inspected.
- **34 deployment-kit tests passed**. Base, federation and website Compose
  configurations validated. Credential/key ignore checks and whitespace checks
  passed. TLS, package hashes and managed CA handling remain enabled.

Exact-commit CI and combined-suite results are recorded with
[the review change](https://github.com/freecollab-eu/freecollab/pull/2).

## Operating boundaries

File-erasure records bind to their original storage namespace fingerprint.
Reconfiguration cannot make an old cleanup delete a new bucket/root's bytes.
Pending cleanup remains visible in operations, and migration refuses to proceed
until it is drained. Cleanup needs the original storage configuration and a
functioning integration worker, even with optional provider features disabled.
See [object storage](OBJECT_STORAGE.md).

Previously orphaned blobs are not reconstructed or deleted by this repair;
reconciliation needs the installation's retained metadata and storage inventory.
No customer data, production storage or credentials were deleted by this review.
Real vendor tenants, physical devices, hosting capacity, DNS and backup recovery
remain separate deployment checks.
