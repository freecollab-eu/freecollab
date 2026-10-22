# Shared runtime and deployment boundaries

The default installation uses one API process, local file storage and local
realtime/presence/auth throttles. Multiple API replicas require a shared SQL
database, shared object storage and shared realtime coordination; adding replicas
alone does not turn local storage into shared storage.

## Valkey

Configure a private, authenticated TLS Valkey endpoint using:

```env
REDIS_URL=rediss://freecollab:customer-secret@valkey.internal.example:6379/0
REDIS_NAMESPACE=freecollab-production
REDIS_TLS_REQUIRED=true
REDIS_PRESENCE_LEASE_SECONDS=75
```

`REDIS_URL` is a secret setting. Use one namespace per installation (1–64 ASCII
letters, digits, colon, underscore or hyphen). Default system CA validation applies;
add the customer's private CA to the normal trusted CA bundle, for example through
`SSL_CERT_FILE`. Certificate and hostname checks cannot be disabled through URL
parameters. Plain `redis://` is accepted on loopback for private tests; elsewhere
it needs an explicit `REDIS_TLS_REQUIRED=false` override. Production should keep
TLS enabled and restrict network access to FreeCollab services.

Use `maxmemory-policy noeviction` with enough reserved memory. The application
checks it before shared runtime startup and on each authentication limiter
operation. Grant the application the needed key/pubsub/Lua permissions and read
access to `CONFIG GET maxmemory-policy`; no configuration write permission is
needed. A full store, unsafe eviction policy, denied command or broker outage
returns a sanitized 503 for shared authentication throttles. There is no local
throttle fallback and live limit keys are never evicted by the application.
Durability, replication, credentials, backups and capacity are customer-operated.
This adapter uses one Redis-protocol endpoint/database; Redis Cluster/Sentinel
discovery is not implemented.

### Delivery and authorization

Each API process retains its own Socket.IO sessions and raw JWTs in memory. The
private broker transports bounded event envelopes with channel/user identifiers
and event content, never raw bearer tokens. It deliberately does not use global
Redis Socket.IO room broadcasting. Every receiving API process rechecks each
local browser's current SQL session, revocation, account status, SSO/embedded
scope and channel membership before delivering to that exact socket. Retained
notification title/body additionally require current visibility of the referenced
notification resource for native and SSO sessions alike. Empty recipient sets
produce no room fallback.

Typing, messages, private notifications and presence use this same local delivery
boundary. Publishers can run from normal API event loops or repeated synchronous
worker `asyncio.run` calls; a thread-safe bounded publisher pool avoids keeping
async connections attached to closed worker loops. Each broker envelope is
limited to 1 MiB. Private Valkey is an application trust boundary because native
event content can pass through it; it must not be exposed to browsers or other
untrusted applications.

Pub/sub delivery is transient. SQL history/inbox records remain authoritative.
If a committed mutation cannot publish, it still delivers to currently authorized
local sockets and returns its committed result, avoiding duplicate writes from a
misleading retry error. `/ready` reports the configured subscriber unavailable;
periodic native history/inbox synchronization recovers retained data. Typing and
presence events are ephemeral. This is not a durable, exactly-once message queue.
Socket.IO polling requests still need load-balancer affinity; enable WebSocket
upgrade/long-lived connections and configure trusted proxy forwarding explicitly.

### Presence and authentication windows

Presence uses one lease per socket/device, with at most 64 leases per user. A
second device keeps the user online when another device disconnects. Explicit
status changes carry their own timestamp, so heartbeat refreshes do not silently
override a newer status. Leases refresh at one third of their 30–300 second TTL
(75 seconds by default) after local JWT reauthorization. Crashed API/device leases
expire. REST presence reads additionally recheck the stored session digest and
verified claim metadata against current SQL account/session/SSO state; only the
caller's currently visible directory/channel members are queried. A request is
limited to 1,000 visible people. No JWT or provider credential enters presence
keys.

Authentication throttles use atomic Lua sliding windows with Valkey server time:
registration 30/minute/address, sign-in 60/minute/address, failed accounts
10/five minutes and failed addresses 30/five minutes. Successful sign-in clears
only the normalized account-failure window. Address/account key parts are hashed.
The shared registry is bounded to 10,000 live throttle buckets; capacity exhaustion
fails closed rather than evicting a live throttle. All limiter keys expire.
Forwarded headers do not select a client address unless the deployment's trusted
proxy configuration supplies that address.

## Validation and remaining operational work

`backend/tests/test_valkey_runtime.py` exercises an actual certificate-verified
TLS Valkey server: cross-client atomic limits, concurrent requests, bounded live
buckets, noeviction checks, unsafe TLS rejection, unavailable broker behavior,
subscriber fanout, repeated worker event loops and multi-device lease removal.
Set `VALKEY_SERVER_BIN` to a TLS-enabled Valkey binary to run this integration
suite. Private proof runs use temporary keys/storage and never pilot services.

Native socket authorization runs as complete worker-thread SQL units, with a
fresh scoped decision for each recipient. Message and notification writers end
their read transactions before awaiting fanout. The bounded-pool regressions in
`backend/tests/test_realtime_pool_contention.py` exercise real one-connection
waits and message/edit/delete/offline/mention delivery; increasing the pool does
not substitute for releasing a transaction or keeping SQL off the event loop.

For a disposable installation, run the bounded functional load tool:

```sh
backend/.venv/bin/python scripts/load-smoke.py \
  --base-url http://127.0.0.1:8000 --report /tmp/freecollab-load.json
```

It creates test accounts, a workspace and a file. Defaults are eight members,
16 real WebSockets, 400 reads, 40 messages and 20 in-flight
REST requests. The report includes latency percentiles, unique live fanout,
committed history, exact file bytes, outsider denials and logout revocation.
`--alternate-api` adds a second correctly configured replica; it does not
configure shared storage or a broker. Remote test targets require an explicit
disposable-installation flag. Remove the isolated installation after inspection.
The tool excludes Matrix/media/provider traffic and does not establish capacity.

`backend/tests/test_valkey_two_apis.py` independently starts two real API
processes against an actual TLS Valkey instance. It verifies cross-process
messages, mentions, typing, notifications and multi-device presence; current
native JWT, workspace SSO and channel revocation; separate worker event loops;
absence of raw JWTs from broker traffic/leases; shared sign-in limits; and 503
authentication/presence/readiness responses after the broker is stopped.

Shared infrastructure makes multiple processes possible; it is not a capacity
certification. Load-test representative channel fanout, memberships, file sizes,
database I/O, connection counts and failure recovery before choosing replica
counts. Native content search currently reads explicitly bounded current sources;
no external full-text/vector cluster is silently configured. LiveKit/S3/database
operation, provider quotas, backup restore and regional failover remain explicit
deployment responsibilities.
