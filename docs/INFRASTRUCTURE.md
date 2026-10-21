# Choose infrastructure you control

FreeCollab can run on a customer's server or in their chosen cloud account.
Owning the installation does not require owning the hardware. Each organization
keeps its own identity provider, provider applications, keys and service accounts.
The public FreeCollab website/demo is a separate installation.

## Recommended starting point

| Component | Recommendation | What FreeCollab uses it for |
| --- | --- | --- |
| Database | PostgreSQL; the supplied deployment retains PostgreSQL 15 | Membership, messages, metadata, audit and durable job queues |
| File storage | Local volume initially; customer-owned S3-compatible storage when sharing files across replicas | Immutable native file versions, authenticated reads and physical erasure |
| Shared coordination | Private TLS Valkey with `noeviction` when adding API replicas | Realtime fanout, presence leases and shared authentication rate limits |
| Encrypted channels | The supplied private Matrix/Synapse service | Trusted-device Matrix encryption, recovery and encrypted attachments |
| Calls | Browser mesh initially; customer-owned LiveKit for larger meetings | Camera, microphone and screen media; independent access-revocation worker |
| Search | Built-in bounded current-source search | Native documents/tasks/feed/file bodies; encrypted content stays on the device |
| Transcription | Browser Whisper with verified same-origin model mirror | Local audio inference; optional model download requires explicit consent |
| AI | Customer-selected compatible provider, optionally self-hosted | Explicitly selected context, reviewed drafts and approved actions |
| Key custody | Existing independent local key configuration; optional Vault Transit | Native envelope encryption without sending message plaintext to Vault |
| Deployment | Docker Compose on one host initially | A working installation without requiring Kubernetes |

Queues remain durable SQL records. Valkey is used for coordination and transient
fanout, rather than becoming the source of truth for message delivery. A broker
outage cannot weaken authentication limits; committed message history and inbox
records remain in SQL. The client reconciles native message changes while open.

Use Kubernetes when operational needs justify it. Kubernetes does not provide
application authorization, shared files, correct job claims, recovery keys or
media capacity by itself. The supported default is one API process. Multiple
API replicas require the complete setup in [SCALING.md](SCALING.md), including
shared SQL/storage, consistent key configuration and load-balancer behavior.
Local private export/logo volumes also need an appropriate shared storage plan;
an S3 file bucket does not replace them.

## Configure services deliberately

The default Compose installation starts the API, PostgreSQL, frontend and
integration worker. Optional external services stay inactive until configured.
Every provider belongs to the customer; there is no shared FreeCollab Microsoft
or Google client secret built into the source.

- [Object storage](OBJECT_STORAGE.md): settings, immutable writes, operator probe,
  explicit migration and external backup/restore requirements.
- [Scaling](SCALING.md): TLS Valkey, permissions, replica authorization and limits.
- [Meetings and clips](MEETINGS.md): LiveKit, TURN, dedicated worker, verified model
  preparation and the distinction between SFU transport and Matrix encryption.
- [Shared channels](CONNECT.md): bilateral organizations on the same installation;
  optional [cross-installation Connect](CONNECT_FEDERATION.md) requires pinned peer
  keys and explicitly configured Matrix federation, and is disabled by default.
- [Search](ENTERPRISE_SEARCH.md): current-source bounds and personal external grants.
- [Apps](APP_PLATFORM.md): customer-installed commands, scoped bots and signed delivery.
- [Governance](ENTERPRISE_GOVERNANCE.md): native DLP, preservation and Vault Transit.
- [Operations](OPERATIONS.md): production settings, local cold backup and recovery.

The public demo may be deployed on Railway when hosting is available. No Railway
login or customer provider credentials are needed to build or self-host the source.
Actual provider acceptance, internet TLS routing, service capacity and recovery
must be verified against the installation the customer chooses.
