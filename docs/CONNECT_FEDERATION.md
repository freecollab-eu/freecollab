# Shared channels between independent installations

FreeCollab Connect has a separate, explicitly configured cross-installation mode.
Each installation retains its own database, workspace, accounts, identity provider
and channel identifier. A bilateral connection binds these two local channels to
one encrypted room federated by the two customer-operated Matrix homeservers.
No remote account is inserted into the other installation's user directory and
no participant gains membership in the partner's workspace.

Open **Integrations → Shared channels → Across installations**. Each operator
first pins the other installation's signing key and Matrix server name. A source
workspace administrator then creates a new empty channel invitation targeted to
one exact trusted installation and workspace. A target workspace administrator
accepts it. Acceptance alone grants no content seat, including to either admin.
Each administrator explicitly selects only their own current workspace members.
Participants open the channel and verify each other's actual Matrix device
fingerprints through a separate trusted channel before sending.

The federation scope is encrypted conversation events, threads/reactions/read
receipts and encrypted attachment/file-version events in this shared room.
Workspace directories, customer SSO, native task/calendar/document indexes,
server-side apps/workflows, meetings and personal notifications remain controlled
by each installation; they are not automatically mirrored into the partner's
installation. This feature does not turn all workspace APIs into a federation
protocol or expose native server-readable content to a remote administrator.

## Operator configuration

This mode is disabled by default. It does not require FreeCollab-hosted SSO,
Microsoft/Google secrets or a central federation broker. Both installations need:

- A stable public HTTPS API origin, correctly routed to the intended installation.
- A private Matrix homeserver with a stable, distinct DNS `server_name`, trusted
  TLS and explicitly permitted federation with the peer homeserver.
- Independently retained native encryption keys and the database. The installation
  Ed25519 signing seed is encrypted in the additive
  `connectfederationidentity` table with the existing native keyring; losing that
  row/key changes the installation identity and requires deliberate peer re-pinning.

Configure the API:

```dotenv
CONNECT_FEDERATION_ENABLED=true
CONNECT_FEDERATION_PUBLIC_URL=https://collaboration-api.company-a.example
CONNECT_FEDERATION_PEERS={"https://collaboration-api.company-b.example":{"public_key":"BASE64_ED25519_PUBLIC_KEY_FROM_PARTNER","matrix_server_name":"matrix.company-b.example"}}
```

`CONNECT_FEDERATION_PUBLIC_URL` and peer origins are exact HTTPS origins with
standard port 443, no path, query, credentials or fragment. Public DNS must resolve
only to public addresses. The existing bounded public-HTTPS transport performs a
fresh DNS check, pins the connection, verifies the original TLS hostname and
refuses redirects. A proxy failure never falls back to a direct connection.
The peer's public key is supplied deliberately; the app does not trust a key
merely because an unauthenticated remote endpoint advertises it.

For initial key exchange, enable the feature with `CONNECT_FEDERATION_PEERS={}`
and open **Installation identity** in the authenticated workspace-admin view.
Compare/exchange its public signing key and stable Matrix server name over an
independent trusted channel, then set each other's pins and restart each API.
Public keys are safe to share; identity-provider/client secrets are not involved.
Do not replace existing database/native keys to regenerate an installation key.

The bundled Matrix configuration script retains federation disabled by default.
Operators who use it can set a bounded explicit list:

```dotenv
MATRIX_FEDERATION_DOMAINS=["matrix.company-b.example"]
```

The script adds Matrix federation resources to its private listener only when
this list is nonempty. This does not configure DNS, certificates or a public TLS
listener. Expose only the required `/_matrix/federation/` and `/_matrix/key/`
server-to-server routes through a correctly configured TLS gateway/8448 listener
and verify that the two Matrix servers can resolve, authenticate and contact each
other. Keep Synapse admin, registration and client endpoints private behind
FreeCollab's authenticated gateway. Retain public/private address restrictions;
production federation is not a reason to allow private destination ranges.
`docker-compose.yml` remains a private single-installation default;
it does not automatically publish new Matrix federation ports or routes.

Route the public peer control plane at
`/connect/federation/wire/{accept,roster,revoke}` to the matching FreeCollab API.
A frontend deployment with `/api` rewriting must expose these exact server routes
at the configured API origin. Requests are bounded to 128 KiB. TLS is mandatory.
The API checks the pinned peer signature, protocol, audience, operation, timestamp
(within 60 seconds) and one-use SQL nonce before handling an operation. Signed
responses bind the operation, audience and original request nonce as well.
Never log request bodies, invitations, signing seeds or bearer tokens.

## Optional Compose TLS edge

`deploy/federation/compose.example.yml` adds an opt-in Nginx TLS edge. It exposes
app/control-plane HTTPS on443 and only Matrix federation/key routes on8448;
Synapse client/admin/registration remain private. Supply these nonsecret selectors
in the existing private installation environment (retain all existing keys):

```dotenv
PUBLIC_APP_URL=https://collaboration.company-a.example
CORS_ORIGINS=["https://collaboration.company-a.example"]
FREECOLLAB_PUBLIC_HOST=collaboration.company-a.example
MATRIX_SERVER_NAME=matrix.company-a.example
MATRIX_ENABLED=true
MATRIX_FEDERATION_DOMAINS=["matrix.company-b.example"]
CONNECT_FEDERATION_ENABLED=true
CONNECT_FEDERATION_PUBLIC_URL=https://collaboration.company-a.example
CONNECT_FEDERATION_PEERS={"https://collaboration.company-b.example":{"public_key":"PARTNER_PUBLIC_KEY","matrix_server_name":"matrix.company-b.example"}}
FEDERATION_TLS_DIRECTORY=/absolute/operator/managed/tls
```

Mount a directory containing the operator-managed `server.crt` full certificate
chain and private `server.key`, readable by the Nginx process. Its certificate
must cover both app and Matrix DNS names. Restrict the directory/key on the host;
do not commit it. Configure/renew valid public certificates with the operator's
existing certificate manager. Point both DNS names to the edge and permit443 and
8448 through the intended firewall. Do not change an existing Matrix server name.
The peer Matrix domain uses the standard8448 discovery fallback; optional custom
`.well-known` routing is an operator choice, not configured by this example.

Review resolved configuration without printing interpolated environment/secret
values, then use the same private environment and project selectors as the
existing deployment:

```sh
docker compose --env-file /private/freecollab.env \
  -f docker-compose.yml -f deploy/federation/compose.example.yml \
  --profile secure config --quiet
docker compose --env-file /private/freecollab.env \
  -f docker-compose.yml -f deploy/federation/compose.example.yml \
  --profile secure up -d
```

The root Compose backend anchor forwards the three `CONNECT_FEDERATION_*` settings
to API/workers, and the Matrix service receives `MATRIX_FEDERATION_DOMAINS`.
The default root deployment exposes none of these federation/TLS ports. Existing
external reverse proxies can instead implement the same route boundary without
starting the example edge. The edge disables URL access/error logs on both listeners so OAuth callbacks,
office and guest capabilities are not copied into outer-proxy logs. It sends
`Referrer-Policy: no-referrer` on the app listener; authenticated application audit
records remain available independently. The example does not acquire certificates
or modify DNS/firewalls on the operator's behalf.

## Membership and revocation

Every participant remains a local account on their own installation. The signed
control plane exchanges only connection/room references, display names, Matrix
identities and device-revocation metadata. It carries no message bodies,
attachments, Matrix bearer tokens, Megolm sessions or content encryption keys.
Remote identities retain their full Matrix domain in the client to prevent them
from masquerading as a same-ID local account.

Each side's current signed roster is refreshed before preparing a send; the
send gateway checks the peer again before its local SQL/Matrix send lock. A
changed roster changes the opaque membership epoch and rejects stale prepared
sends. A cached snapshot expires after 30 seconds; an unavailable peer blocks new
sends rather than treating cached access as current authorization. Matrix room
membership, invitation-only joining, joined-only history and device verification
continue to apply. Removed or blocked local accounts disappear from the next
signed roster and lose their own product/API gateway access immediately. Normal
workspace/SCIM offboarding permanently revokes local grants, preventing a later
workspace re-invitation from resurrecting an old shared-channel grant.

Either administrator can revoke the whole connection without holding a content
seat. The local status/grants/member removal commits first. A peer notification
and Matrix removal follow. A disconnected service can delay remote cleanup; the
local fence remains effective and current peer checks block writes. Operators
can retry the same DELETE revocation endpoint after recovery. Remote member and
key changes already in flight across two independent systems cannot be undone.
Previously delivered ciphertext, exported content and recipient-held decryption
keys cannot be recalled. Both customer operators are trusted to enforce their own
roster and operate their own Matrix server; a malicious administrator of a peer
is not prevented from deliberately redistributing content that peer has received.

Encrypted remote media stays ciphertext when proxied through the local
homeserver. A remote media origin is admitted only for a current explicitly
shared Matrix peer visible to the authenticated local participant. The API never
makes arbitrary customer-selected media HTTP requests or decrypts attachment
bytes. Participants continue to verify/ decrypt attachments in their browser.
Native server-side search, bots, DLP and export do not gain Matrix plaintext.

The **Shared work** tab adds encrypted cross-installation tasks, calendar series
and Yjs text documents. Each installation keeps opaque event pointers; verified
clients derive the shared view from the same encrypted room. Native local task,
calendar/provider and document records retain their own installation scope.
See [FEDERATED_WORK.md](FEDERATED_WORK.md) for collaboration semantics, explicit
conflict handling, storage bounds, key recovery and availability limitations.

## Verification and rollout

```sh
cd backend
.venv/bin/python -m pytest tests/test_connect_federation.py tests/test_connect.py tests/test_federated_work.py -q
cd ..
CHROMIUM_PATH=/usr/bin/chromium backend/.venv/bin/python frontend/tests/connect-federation-browser.py
```

The API suite uses two independent SQL databases and actual Ed25519 envelopes,
with an explicitly synthetic Matrix control plane. It checks consent, separate
local identities, revoked/current rosters, lifetime, replay, response binding,
participant authorization and unavailable peers. The browser proof separately
creates two owned Synapse homeservers with verified fixture TLS, two API
processes/databases and actual Rust Matrix clients. Its proof groups include an actual encrypted attachment upload, remote
owner namespace and authenticated cross-homeserver download/decryption. Critical
runtime source hashes are captured before API startup and checked unchanged at
completion. Its synthetic host mapping
and private-address exception exist only in the generated test launchers and
homeserver fixtures; product transport and operator defaults remain unchanged.
The proof cleans only its own containers/network/processes/data and writes its
bounded result to `.freecollab/proof-results/connect-federation-browser-013.json`.
Customer DNS, public TLS routing/firewalls, real customer IdP login, physical
clients and production capacity remain installation-specific acceptance checks.
