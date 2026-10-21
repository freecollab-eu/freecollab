# Channel activity feeds

Open **Workspace tools → Activity feed** to publish an update, follow an author, discuss an update, react, vote in a poll or mark a revision as read. The production API is separate from the older experimental posts/comments routes. `ACTIVITY_FEEDS_ENABLED=true` enables this feature without enabling `ENABLE_EXPERIMENTAL_FEATURES`.

Every operation requires current workspace/channel membership. Customer SSO users can participate only inside their authorized workspace. Post/comment content edits and archive/restore require the original author or a current channel administrator. A participant can change only their own vote, reaction, following and read state; those actor identifiers come from authenticated access, never a client-supplied author. Direct-message write restrictions apply. Writers lock the actor and channel, refresh identity and membership, and recheck the current request after Matrix validation before committing.

## Posts, discussion and history

Posts have a title, body and optional poll. The small rich-text renderer supports **bold**, italic, inline code and bullet-style lines. It renders user values as React text: HTML, scripts and arbitrary embedded documents are not executed. It has no remote-content fetcher, automatic external link preview or arbitrary iframe.

Posts and comments retain immutable content revisions. Edits and archive/restore carry the exact expected revision; stale writes fail with HTTP 409. Repeating an identical write after a lost acknowledgement returns the same revision rather than producing another comment, vote or notification. Archiving keeps prior content/history and stops new comments, reactions and votes. Restoring a post reopens it. Comment history remains readable to current channel members even after the comment is archived.

Following is scoped to the channel and filters its feed by selected authors. Generic in-app notices announce new posts from followed authors, comments and reactions, subject to notification preferences and current membership. Notices contain no post title, poll label or comment body. There is no external email/provider dispatch from this feature. Personal read state records the highest explicitly marked post revision and does not reveal a list of readers to peers. Editing a post makes that newer revision unread.

The panel supports English and Swedish, the account's locale, theme variables and a 320 px viewport. Its search examines only updates already loaded on that device. Cursor pagination uses creation time and a stable identifier, so equal timestamps do not skip updates. Unsaved composer/comment text lives only in the open panel's memory; it is not a durable offline feed editor.

## Polls and authenticated responses

Polls have two to eight options, allow one or multiple choices, and may have a timezone-aware deadline. They are explicitly **not anonymous** to channel members. A user has one current vote per poll, can update it with a version check, and can remove their choices before the deadline. Earlier choices remain in that user's immutable response history. Removed or blocked participants' choices are excluded from current responses. A closed or archived poll rejects later changes.

Poll identity, option identities, mode and deadline are routing metadata. Option identifiers are random 32-character hexadecimal values, not labels. A poll also has a SHA-256 commitment to its full configuration, including a random nonce contained inside its encrypted content. The server cannot dictionary-guess a secure poll's question/labels from the digest without that hidden nonce. After the first vote, routing and the commitment cannot change, even if someone later clears their vote; this prevents changing the meaning of an earlier response. Ordinary post text can still be revised.

Native votes validate selected options and selection count on the server. In Matrix channels, selections stay encrypted and the server stores only an actor-bound, versioned event reference. Readers verify its author, channel, type, post, revision, poll identity and selections. A malformed or misbound response is shown as unverifiable and excluded from counts; it never silently counts as a valid vote. A rejected encrypted event may still exist in Matrix history but does not become a valid application response.

## Encryption and metadata

Native post/comment/vote/reaction content is encrypted at rest with the server message keyring. Envelopes bind channel, content kind, object identity and revision. The authorized server can read native content.

For Matrix channels, the browser encrypts post titles, bodies, poll questions/labels/nonces, comment bodies, selected options and reaction choices as `eu.freecollab.activity.{post|comment|vote|reaction}` events. The API stores opaque event identifiers and public routing/version metadata. Native plaintext is rejected in both preparing and active secure channels. The server validates that an encrypted event belongs to the current actor and room; clients independently verify the decrypted payload's exact context. Copying ciphertext from a different post or submitting another person's event cannot create trusted content. There is no plaintext fallback if verification/decryption fails.

The server can see channel/post existence, actors, times, archive state, revision counts, follow/read metadata, random poll option identities, poll mode/deadline and response existence. Current members can read each other's verified responses. Matrix retention, device keys, previously received history and local exports are separate retention domains. Changing a channel's encryption mode must use the central conversion fences, which include activity-feed native history.

## Bounds and API

- At most 500 retained posts per channel, 200 retained comments per post, 100 revisions per content/choice item and 1,000 retained actors per choice kind/post. Archived records count toward these limits.
- Post title: 160 characters; body: 20,000; comment: 4,000; poll question: 240; option label: 160.
- List pages contain at most 50 posts (20 by default). History and detail responses remain bounded by the retained limits. Requests are rate limited before acquiring writer locks; rate limiting cannot commit away the mutation's lock fence.
- Responses use `Cache-Control: no-store`. There is no permanent-delete endpoint; explicit workspace data-control previews/retention own permanent erasure and export scope.

The base path is `/channels/{channel_id}/activity-feed`:

| Method | Suffix | Purpose |
| --- | --- | --- |
| GET | empty | Page posts with `following`, `archived`, `before_id`, `limit` |
| POST | empty | Create/revise/archive/restore a post |
| GET | `/{post_id}` | Post, authenticated comments, current-member votes and reactions |
| GET | `/{post_id}/revisions` | Immutable post history |
| POST | `/{post_id}/comments` | Create/revise/archive/restore a comment |
| GET | `/{post_id}/comments/{comment_id}/revisions` | Comment history |
| POST | `/{post_id}/vote` or `/reaction` | Current actor's versioned response |
| GET | `/{post_id}/choices/{kind}/revisions` | Current actor's response history |
| PUT | `/following/{target_id}` | Follow/unfollow a current channel author |
| PUT | `/{post_id}/read` | Mark a valid post revision read, monotonically |

Native post writes use `{id, expected_revision, archived, fields:{title, body, poll}}`. A native poll contains `{id, nonce, question, options:[{id,label}], multiple, closes_at}`. Secure writes replace `fields` with `matrix_event_id`; secure post writes also carry public poll routing `{id, option_ids, digest, multiple, closes_at}`. Responses expose native `fields` only in native channels. Consumers should use `activity-feed-api.ts` for encrypted resolution and verified counting rather than interpreting opaque references as content.

For authorized native exports, `services.activity_feed.open_fields(session, channel_id, kind, identifier, revision, encrypted_payload)` returns verified native fields. Choice identifiers are `post_id + ':' + user_id + ':' + kind`; post/comment identifiers are their own IDs. Foreign-key ownership is explicit in `models/activity_feed.py` so retention can delete dependent revisions, reads, comments and choices before the parent post. Unknown future dependencies must continue to fail closed.

## Verification

`backend/.venv/bin/python -m pytest backend/tests/test_activity_feed.py` tests roles, scoped sessions, post-lock and post-network revocation, CAS/history/archive, native envelope storage, idempotent retries and concurrent vote writes, immutable poll meaning, option/deadline constraints, membership removal, following/read privacy, stable cursor pagination and secure plaintext fences.

`CHROMIUM_PATH=/usr/bin/chromium backend/.venv/bin/python frontend/tests/activity-feed-browser.py --secure` uses disposable API/Vite/SQLite accounts and a frozen frontend copy plus the authorized private Matrix service. It exercises the actual native composer, safe formatting, comments, poll, reaction, following/read, history/archive/restore and Swedish mobile layout. Two real trusted Matrix devices verify encrypted posts/comments/polls/reactions and confirm opaque API/database storage and `m.room.encrypted` wire events. Wrong post contexts and authenticated invalid poll choices are rejected by client verification. Own processes, local databases/files/keys and browser contexts are cleaned; the retained Matrix service is preserved.
