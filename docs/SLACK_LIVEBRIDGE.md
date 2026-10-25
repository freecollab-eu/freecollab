# Continuous Slack connection

The Slack bridge is a real ongoing integration, separate from
[Slack archive import](SLACK_IMPORT.md). Open a native channel's Apps & automation
work surface and choose **Slack bridge**. A personal workspace/channel
administrator configures their own Slack app, workspace ID, channel ID, bot user
ID, bot token and signing secret. Credentials are sealed with the existing
account-security key and never returned by list/status/export APIs.

The administrator explicitly grants inbound, outbound or both directions and
reviews future message disclosure with password/MFA confirmation. Other channel
members see the active Slack recipient notice in the normal conversation. Slack
retains its own copies and is not an E2EE destination. The bridge rejects encrypted
channels instead of decrypting or silently mirroring their contents.

## Customer setup

1. Create/install your Slack app in the intended Slack workspace. Grant
   `chat:write` for outgoing posts and the appropriate message-event/read scopes
   for the selected public/private channel. Invite the bot to that channel.
2. Enter the app's actual workspace/channel/bot IDs and secret values in your
   own FreeCollab installation's connection form. Save the reviewed directions.
   **Test connection** calls Slack `auth.test` and verifies the actual workspace
   and bot identity; saving alone does not establish successful provider access.
3. Set Slack's Events Request URL to the exact returned
   `/slack/bridges/{bridge}/events` URL on your installation and subscribe to its
   message events. Slack URL verification is authenticated with the same signed
   request contract. HTTPS/domain exposure is your installation's responsibility.
4. Run the integration worker's Slack lane, or the independent
   `python -m app.services.slack_livebridge --interval 5` worker. No queued
   requests are represented as delivered before a worker processes them.

Tokens belong to the customer app. They are retained across ordinary restarts;
the connection does not require daily logins. A revoked token or changed Slack
scope pauses delivery until the owner reviews and restores the connection.
Changing to another Slack channel creates a separate mapping, preserving the
meaning of existing message identifiers.

## Actual delivery behavior

- Inbound Slack Events API requests verify `v0` HMAC-SHA256 over the exact body,
  a five-minute timestamp window, the current signing secret/generation, the
  exact Slack workspace and channel, and current local administrator grants.
  Request bodies and queues are bounded. Stable event IDs reject conflicting
  replays and reuse the same durable receipt on a genuine retry.
- New messages, edits, deletions and threads become ordinary native message
  records encrypted at rest. Incoming text is explicitly labeled with its Slack
  user ID and attributed to the local account that authorized the bridge. It does
  not impersonate a native user's identity or reuse someone's profile photo.
  Native change polling and notification preferences/deduplication still apply.
- Message links retain exact Slack timestamps, native IDs and event ordering.
  Delayed old events cannot resurrect a deleted post. Slack bot echoes and
  messages imported by any other bridge are not relayed into loops.
- Outbound processing follows the native change cursor from the time the bridge
  was enabled/resumed. It does not silently backfill historical or paused work.
  New posts use Slack `chat.postMessage` with a stable client ID; existing mapped
  posts use `chat.update`/`chat.delete`, preserving mapped thread parents.
  Link/media previews are disabled. Sources, generation, grants and content
  policy are checked again immediately before disclosure.
- A rate-limit response can schedule a bounded retry. An interrupted or ambiguous
  create is **uncertain**, not automatically retried. The operator inspects Slack
  and explicitly confirms an unchanged request was not processed before retrying.
  Job receipts expose IDs/status, never tokens or copied message content.
- Pause/revoke cancels pending work; revoke strips saved credentials. Resuming
  needs a fresh explicit future-sharing review. Closing the authorizing account
  disables the bridge and erases private configuration/payloads while retaining
  shared authorship/idempotency metadata. Retention blocks live bridge writers
  and purges their inactive dependent copies/mappings with the parent plan.

The public product demo has no Slack credentials or live bridge. Live Slack
acceptance requires the customer's real app grants; synthetic protocol tests
exercise signatures, actual native storage, outbox outcomes and revocation without
claiming a customer workspace was connected. File/archive migration is a separate
explicit import; this text-message bridge does not fetch Slack file URLs or execute
embedded code.

## Implementation and checks

`backend/app/{api,services,models}/slack_livebridge.py`,
`frontend/src/components/SlackBridgePanel.tsx` and
`frontend/src/lib/slack-livebridge-api.ts` implement the actual UI/API/storage and
worker. `test_slack_livebridge.py` covers real signed ingress, encrypted native
records, threads/edits/deletes, tombstones, replay limits, future-only outbox,
uncertain delivery, grant loss, cross-bridge loop suppression and privacy helpers.

Installed composer commands have separate actual backend/schema tests and an
`installed-app-command-browser.py` component-host proof against the real native
API/outbox. Full Chat command/source retention remains a separate product proof.
