# Channels and invitations

The first channel creator is a channel admin. Global staff roles do not automatically grant membership or channel-admin rights. An existing member can see the channel and its members; ordinary members cannot invite, promote or remove other members.

## Join through an explicit invitation

1. An active channel admin sends `POST /channels/{channel_id}/invites` with optional JSON `{"expires_in_hours":24}`. The allowed range is 1–168 hours. HTTP 201 returns `id`, `channel_id`, `token`, `expires_at` and safe audit timestamps.
2. The client builds a link on its own trusted frontend origin with `#invite=<token>`. A fragment is not sent in the HTTP request or ordinary server access logs. The token is a bearer credential and must only be shared with the intended recipient. No email is sent by this workflow.
3. The recipient signs in or creates a personal account and explicitly accepts with `POST /channels/invites/accept`, JSON `{"token":"..."}`. The response is the joined channel with `role:"member"`.
4. Acceptance atomically consumes the invitation. Only a hash of its random 256-bit bearer token is stored. The raw token is returned once in a non-cacheable creation response and is absent from subsequent list responses.

Invitations are single-use, expiring and revocable. They cannot grant admin rights. Acceptance fails if the issuer was blocked, deleted, removed from the channel or demoted. An existing member receives HTTP 409 without consuming the invitation; an invalid, expired, used or revoked invitation receives HTTP 404. A blocked recipient is rejected by normal authentication before acceptance.

The service does not expose a global user directory or search people by email for invitations. The UI may retain an invite fragment through sign-in in session storage, then clear it after completion. Never put the token in a query string, an analytics event or an error log.

## Admin controls

| Request | Access and behavior |
| --- | --- |
| `GET /channels` | Current user's channels, including their channel `role`. |
| `GET /channels/{id}` | Current channel member only; includes their `role`. |
| `GET /channels/{id}/members` | Current member only; returns member IDs, display names, public keys and roles. |
| `GET /channels/{id}/invites?limit=50` | Channel admin only; bounded to 1–100 rows, no raw tokens or hashes. |
| `DELETE /channels/{id}/invites/{invite_id}` | Channel admin only; idempotent revocation. |
| `PATCH /channels/{id}/members/{user_id}` with `{"role":"admin"}` or `{"role":"member"}` | Channel admin only; promotion/demotion. |
| `DELETE /channels/{id}/members/{user_id}` | Channel admin removing a member, or a member removing themselves. |
| `POST /channels/{id}/leave` | Convenience endpoint for leaving one's own channel. |

The final channel admin cannot leave, be removed or be demoted: promote another member first. Channel membership mutations serialize on the channel row before checking this rule. A single-use invite is consumed conditionally in the same transaction as adding membership.

Removal prunes existing Socket.IO channel subscriptions before broadcasting `members_changed`. The removed account receives a personal `channel_removed` event with the channel ID so its client can clear local channel data. All subsequent REST and socket operations recheck membership. Previously downloaded content cannot be remotely erased from another user's device.

There can be at most 100 unexpired, unrevoked, unused invitations per channel. Revoke one before creating another at this limit. For deployment rate limits and session controls, use the separate authentication/operations configuration.

## Existing installations

The additive `ChannelInvitation` table is created with the application schema initialization; existing channel/member columns and explicit `member_ids` channel creation remain compatible. Include the invitation table in database backups. It contains only token hashes, so backups do not reveal usable invitation URLs.

Invited members have no legacy `encrypted_channel_key` value because the active chat API uses server-side content encryption rather than client-owned channel keys. Invitations do not establish end-to-end encryption or rotate content keys. See the current security and key-management documentation before exposing a deployment.

## Validation

Run `python -m pytest tests/test_channel_membership.py -q` from `backend`. Coverage includes positive joining, token persistence boundaries, expiry/revocation and issuer invalidation, blocked recipients, forbidden role escalation, last-admin protection, current-socket eviction and concurrent single-use/admin-leave races.
