# FreeCollab Connect

Connect shares a new, end-to-end encrypted channel between two organization
workspaces on **the same FreeCollab installation**. Each organization keeps its
own SSO provider, directory and administrators. It does not become a member of
the other workspace. Both administrators must consent, and acceptance does not
give the receiving administrator or their coworkers access to the conversation.

An operator must first configure the existing private Matrix service described
in [E2EE_IMPLEMENTATION.md](E2EE_IMPLEMENTATION.md). Connect has no plaintext
fallback. `GET /api/connect/capabilities` reports whether encryption is configured
and explicitly reports `cross_installation: false`.

## Share a channel

1. In Integrations → Shared channels, an administrator enters the partner's
   workspace ID and creates an invitation. This creates a dedicated empty private
   channel; an existing channel and its history cannot be shared accidentally.
   The creating administrator explicitly receives the first content seat.
2. Send the displayed token privately to the receiving workspace's administrator.
   Only its SHA-256 digest is stored; the token is returned once with `no-store`
   and expires after 24 hours by default (the API allows 1–168 hours).
3. The receiving administrator accepts while signed in to their own workspace.
   The token is targeted, single use, and the source administrator's current
   authority is checked again. No recipient content seat is created.
4. Each organization's administrator grants access to selected existing members
   of their **own** workspace. Guests still require an explicit grant. An account
   belonging to both workspaces must participate through one chosen workspace.
5. Participants open the channel and synchronize Matrix membership. Message and
   file content uses the existing Matrix client encryption and device handling.
   Joined-only room history and per-message key rotation preserve the existing
   encryption model; no custom cryptographic transport is introduced.

Channel labels, workspace names/IDs, participant identities, invitation state and
other control-plane metadata are visible to the participating administrators and
the installation operator. They are not represented as encrypted message text.

## Access and revocation

REST, socket delivery and the Matrix gateway recheck active bilateral state,
the local member grant, current workspace membership and managed account status.
A recipient channel appears with the recipient's own `workspace_id`; the new
`owner_workspace_id` and `connect_connection_id` fields preserve its ownership
and shared-channel identity. Ordinary workspace membership and directory APIs
continue to reject access to the partner workspace. Only actual shared-channel
peers become visible for participant identity and presence.

Administrators manage their own grants through Connect. Ordinary channel invites,
role changes and cross-organization kicks cannot bypass that control. Participants
can leave through the normal channel leave action. Either administrator can end
the entire connection, which removes every participant's product access. Revoked
connections are not reactivated; create a fresh invitation and empty channel.

Revocation commits local denial before attempting remote Matrix cleanup. A
homeserver outage therefore does not restore REST, socket or gateway access.
`secure_sync_pending: true` means remote cleanup or provisioning remains pending;
it is not a claim of completed synchronization. An authorized administrator can
retry with `POST .../connections/{connection_id}/retry-sync`. Active provisioning
also requires their own explicit content seat. An administrator can retry cleanup
of a revoked connection without joining its room. Previously downloaded content
and keys cannot be recalled from participants' devices.

## API

All routes below have the `/api/connect` prefix. Each workspace operation requires
that workspace's current owner/admin. Customer SSO sessions can administer their
own workspace's connections without acquiring authority over the partner tenant.

| Method and path | Operation |
| --- | --- |
| `GET /capabilities` | Authenticated transport and encryption readiness |
| `GET /workspaces/{workspace_id}/connections` | Latest 100 incoming/outgoing connections |
| `POST /workspaces/{workspace_id}/connections` | `{name,target_workspace_id,expires_in_hours?}`; returns one-time `invitation_token` |
| `POST /workspaces/{workspace_id}/accept` | Accept `{token}` without granting content access |
| `GET /workspaces/{workspace_id}/connections/{id}/members` | Only this organization's grants |
| `POST /workspaces/{workspace_id}/connections/{id}/members` | Grant own member `{user_id}` |
| `DELETE /workspaces/{workspace_id}/connections/{id}/members/{user_id}` | Revoke own member |
| `DELETE /workspaces/{workspace_id}/connections/{id}` | End both organizations' shared access |
| `POST /workspaces/{workspace_id}/connections/{id}/retry-sync` | Retry provisioning/membership or revoked-room cleanup |

The additive `connectconnection` and `connectgrant` tables preserve the original
single owning `workspacechannel` mapping. Upgrades use the normal additive
`SQLModel.metadata.create_all`; no existing channel is silently converted.

## Separate installations

This release does not exchange messages or identities between independent
FreeCollab installations. A local workspace grant is not Matrix federation. The
default private homeserver and gateway remain private; Connect does not enable
public federation or accept arbitrary external room IDs, URLs or credentials.

A production cross-installation adapter needs operator-approved remote domains,
standard Matrix federation and encrypted room membership, remote identity and
device verification, bilateral administrative authorization, interoperable key
sharing, revocation and gateway filtering. It also needs tests against two real
homeservers. Hosting capacity alone does not provide those trust boundaries.
Until that adapter is configured and validated, this capability reports false.

## Validation

`backend/tests/test_connect.py` uses isolated SQLite accounts and mocked external
Matrix provisioning. It tests bilateral consent, source authority changes,
single-use targeted invitations, own-organization grants, customer SSO isolation,
shared-channel projections, no plaintext fallback, revoked socket and Matrix
visibility, long-lived authorization checks, self leave and cleanup outages.
These unit/API tests do not constitute a two-homeserver federation test.

`backend/tests/test_connect_embed.py` exercises recipient-owned SSO and trusted
Embed launch, exchange and renewal; current grants, connection and canonical
ownership; independent Matrix child markers; and encrypted sends that recheck
membership, room binding and recipient epoch under the channel write lock.
The deterministic upstream in these tests does not perform cryptography.
`backend/tests/test_connect_presence.py` verifies governed SSO and embedded
presence against each socket's own current session, including partner peers,
unrelated organizations, revoked children and differently scoped sibling devices.

`frontend/tests/connect.test.mjs` exercises the compiled component with a hook-state
harness and isolated API responses, including one-time token handling, scope encoding,
explicit grants, encryption setup gating and stale-session response rejection.
`frontend/tests/secure-room-bootstrap.test.mjs` checks invitation synchronization
before the first encrypted join, denied seats, existing reads and write epochs.

Run `backend/.venv/bin/python packages/components/tests/connect-browser-proof.py
--skip-build` from the repository root after building the trusted client and SDK.
This bounded proof creates and removes its own pinned Synapse and isolated data.
It checks actual Connect invitation/acceptance and local member enrollment across
two managed organization workspaces, each with its own SSO provider registration
and `require_sso` policy, and separate customer host origins. It mounts the packed
trusted Embed clients, verifies device fingerprints through the shipped UI,
sends and decrypts real Rust/Matrix messages in both directions, and checks
existing REST, socket and Matrix authorization after recipient and connection
revocation. This fixture signs real workspace-scoped SSO sessions itself; it does
not perform two external OIDC popup sign-ins. The separate full
`embed-browser-proof.py --skip-build --matrix` covers a synthetic HTTPS customer
OIDC popup, and neither proof covers separate-installation federation.
