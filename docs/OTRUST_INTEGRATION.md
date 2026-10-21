# OTRUST version timestamps

FreeCollab has an optional hash-only timestamp integration for immutable file versions and decision revisions. It is disabled until the operator sets `OTRUST_ENABLED=true`. Ordinary chat and file access work independently of the provider.

## What is sent

The inline **Timestamp proof** control opens on a specific file version or decision revision. The user first checks that version's SHA-256 hash locally, then explicitly agrees to share the digest with the configured provider. The request sends only the hash and `source: "api"`; it excludes filename, channel title, emails and content. A hash can still correlate known content. This action does not sign a document or invite anyone.

For native files the server independently computes SHA-256 from the exact authorized immutable stored bytes, ignoring caller-controlled storage paths. Native decisions use the versioned canonical format below. A supplied digest must match these bytes.

For end-to-end encrypted content the client computes the digest after authenticated local decryption. The server validates the opaque room/event or revision reference and retains the submitted digest as `client_asserted`. It cannot independently attest to plaintext it does not possess. Another channel member must check the same exact version locally before treating that digest as a content match. No cleartext fallback is used.

## Setup and delivery

```dotenv
OTRUST_ENABLED=true
OTRUST_BASE_URL=https://www.otrust.eu
OTRUST_TIMEOUT_SECONDS=10
```

Production requires an HTTPS origin without credentials, path or query. Development/test installations can use an explicit loopback HTTP provider for isolated fixtures. No provider credentials are currently needed by the documented simple-claim API.

Run the integration worker profile described in the general integration runbook, or the dedicated worker:

```bash
python -m app.services.otrust --interval 10
# One bounded batch:
python -m app.services.otrust --once
```

API calls commit a durable request and return promptly; they do not contact the provider. The worker uses finite timeouts, bounded responses, no redirects, durable receipts and a stable `Idempotency-Key` of `freecollab-proof-<local-id>`. Retries reuse that ID after a lost acknowledgement. Pending responses preserve `Retry-After`; explicit refresh does not bypass pending backoff. A requesting user who has lost channel access cannot start a still-queued digest disclosure. Fetching an already-published receipt can continue without resending the digest. Changing the configured provider never silently forwards an old request to a new provider.

States distinguish queued, processing, pending, available proof and failure. File/revision identity and digest bind each record; a new version cannot inherit the prior version's status. Current channel membership is required to create, list and download records. A member can read an authorized proof; only its requester or a channel administrator can schedule a retry. Proof records and downloads remain associated with their channel if the source file is deleted; current channel access remains required.

## What has and has not been verified

The established `opentimestamps==0.4.5` library parses every downloaded `.ots` file and independently checks that its SHA-256 commitment equals the requested digest. Malformed, oversized and mismatched proofs cannot receive a commitment-checked state.

**Commitment checked is not independently verified Bitcoin anchoring.** Merely finding a Bitcoin attestation in a provider proof does not establish that its block exists on the accepted chain. `bitcoin_verified` remains false; the UI says that independent Bitcoin verification has not been performed. Authorized users can download the exact `.ots` proof and verify it independently using the OpenTimestamps client and their Bitcoin node. No badge claims verified authorship, legal identity, ownership, truth or an eIDAS signature.

OTRUST documents an estimated 24–48 hours for anchoring. Treat this as an estimate, not immediate success or an availability guarantee. The provider can return a pending calendar proof before anchoring.

## Canonical decision bytes

The SHA-256 input is the UTF-8 encoding of this compact ordered JSON array, with Unicode preserved and no extra whitespace:

```json
["freecollab-decision-v1","<decision-id>",1,"<title>","<content>","accepted",null,"2026-10-20",null]
```

The last three positions are responsible user ID, decision date and source message ID; absent values are JSON `null`. The revision number is an integer. The first position versions this format. Backend and client tests check exact Unicode/newline bytes and revision changes.

## Documented provider contract and validation

The hosted OpenAPI document at `https://www.otrust.eu/openapi.json` was fetched again on 2026-10-20. It confirms:

- `POST /claim/simple`: JSON `hash` and optional `source`, with `Idempotency-Key`; response `receipt_id`, `hash` and optional provider timestamp.
- `GET /proof/{receiptId}`: binary `.ots` on 200, pending on 202.

References: [public repository](https://github.com/otrust-eu/opensource), [OpenAPI](https://www.otrust.eu/openapi.json), [API policy](https://github.com/otrust-eu/opensource/blob/main/docs/API_POLICY.md).

Local tests exercise a fake provider, real OpenTimestamps serialization/parsing, hash-only wire data, immutable version separation, access removal, encrypted reference binding, pending/rate-limit backoff, lost-response retries, malformed/mismatched proofs and honest Bitcoin status. No hosted timestamp claim, user data, signing invitation or external email was submitted during implementation. Hosted anchoring and independent chain verification were not executed.

The isolated real Chromium proof passed the exact-version consent controls, durable delivery to a loopback provider, pending-to-proof lifecycle, exact `.ots` download, honest unverified-Bitcoin wording, old-version separation, 320px layout and canonical decision-revision hash. Its private API/provider/database/key fixtures were removed after the run. Backend: 16 focused tests passed. Client: 3 protocol/privacy tests and TypeScript passed.

```bash
(cd backend && .venv/bin/python -m pytest tests/test_version_proofs.py -q)
(cd frontend && node --test tests/proofs.test.mjs)
CHROMIUM_PATH=/usr/bin/chromium backend/.venv/bin/python frontend/tests/proofs-browser.py
```
