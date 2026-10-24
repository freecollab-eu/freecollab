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
| `GET /channels/{id}/metadata` | Current member only; current name/description or an opaque encrypted-event reference, version and management rights. |
| `PATCH /channels/{id}/metadata` | Channel admin only; explicit name/description change with `expected_version`, or a client-encrypted event reference. |
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

Invited members have no legacy `encrypted_channel_key` value. Native channel content uses server-managed content encryption; client-encrypted channels use their separate Matrix membership, verified-device trust and recovery controls. An invitation alone does not establish a trusted device or rotate content keys. See [SECURITY_MODEL.md](SECURITY_MODEL.md) and [KEY_MANAGEMENT.md](KEY_MANAGEMENT.md) for the distinct boundaries.

## Edit a channel's name and description

Open **More channel tools → Channel details**, or **Channel members → Manage channel**. Members can read the details; a current channel admin can edit the name and description, then choose **Save channel**. **Cancel**, close and Escape leave the server copy unchanged. A successful save updates the room header and channel list. Direct/group-direct conversations and connected shared channels retain their existing conversation/connection controls and cannot be renamed through this endpoint.

The name accepts 1–100 characters and the optional description up to 1,000, without unsafe control characters. Leading/trailing whitespace is removed and description line endings are normalized. Channels are identified by ID; names need not be globally unique. Membership, invitations and the last-admin rule are unchanged.

For a native channel, send `{"expected_version":0,"name":"Studio Nord","description":"A room for the launch."}`. The first save creates revision 1. Every changed save requires the current version; simultaneous or stale edits return HTTP 409 rather than replacing another person's work. An identical retry at the current version is a no-op. The form retains a rejected draft until the person explicitly reloads the current details. Writer locks, current account/session/workspace checks and the channel-admin check apply before mutation. The realtime event carries only the channel ID and version; recipients fetch the authorized current state.

Native descriptions and a bound copy of the name live in an authenticated content-encryption envelope tied to the channel and metadata revision. The existing native `Channel.name` remains readable by the authorized server. `ChannelMetadata` is additive and must be included in backups. Current channel identity is retained by timed history purges, as the channel's own name is; removing its row while retaining the channel could reset the compare-and-swap version. Key rotation for message bodies does not automatically rewrite this metadata: retained decryption keys remain necessary until a separate metadata rewrite is implemented.

For a client-encrypted channel, the device sends an encrypted `eu.freecollab.channel.metadata` event containing the name, description, channel ID, next revision and `registration_required:true`, then registers only `{"expected_version":0,"matrix_event_id":"$event"}`. The server verifies the encrypted wire event's room and sender, rechecks current authorization after remote verification, and stores only its opaque reference and audit/revision fields. Supplying readable name/description fields in that request is rejected. Native metadata history prevents converting that same channel to client encryption; create a new empty encrypted channel instead.

Once a metadata row exists, readers resolve exactly its registered event, checking encryption, event/room/sender/type/channel/revision and bounded content after decryption. A newer uploaded event whose registration failed does not replace the committed title. Original name-only events are eligible only for legacy channels without an authoritative metadata row. Missing keys, failed verification or unavailable history display an unlock/retry state and never fall back to a server-readable label. This protocol does not hide membership, timestamps or revision/reference metadata from the server.

## Validation

Run `python -m pytest tests/test_channel_membership.py -q` from `backend`. Coverage includes positive joining, token persistence boundaries, expiry/revocation and issuer invalidation, blocked recipients, forbidden role escalation, last-admin protection, current-socket eviction and concurrent single-use/admin-leave races.

Channel details add `tests/test_channel_metadata.py`, `frontend/tests/channel-metadata.test.mjs` and `frontend/tests/channel-metadata-browser.py`. They cover native persistence/authority, stale and simultaneous edits, context-bound envelopes, encrypted wire verification, post-I/O authorization changes, exact committed-event resolution, failed-registration exclusion and the real English/Swedish keyboard/mobile editor. The default browser proof uses a disposable native API/database; it does not establish live Matrix homeserver acceptance.
