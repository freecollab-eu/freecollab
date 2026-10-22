import { api } from './api';
import { getSecureMessaging } from './secure-client';
import { secureFileProvider } from './secure/files';
import { assertOwner, listPrivate, owner, savePrivate, removePrivate, fingerprint } from '../mobile/storage';
import { slackDigest, type SlackArchive, type SlackHistoricalDetails } from './slack-archive';
import type { SlackImportResult } from './slack-import-api';
import type { MatrixClient, MatrixEvent, Room } from 'matrix-js-sdk';
export const SLACK_HISTORY_EVENT = 'eu.freecollab.slack.history';
const META = 'eu.freecollab.slack.import';
interface PendingMain { identity: string; channel: string; parts?: string[]; eventId?: string }
interface Checkpoint { version: 1; archive: string; options: string; workspace: string; session: string; device: string; channelIndex: number; messageIndex: number; processed: number; events: Record<string, string>; result: SlackImportResult; completed: boolean; jobId?: string; pendingMain?: PendingMain }
export interface SecureImportChoices { channels: string[]; mapping: Record<string, string>; prefix: string }
export async function secureSlackMigration(archive: SlackArchive, workspace: string, choices: SecureImportChoices, onProgress: (processed: number, total: number) => void, isCurrent = () => true): Promise<SlackImportResult> {
  const expected = owner(), tokenHash = await fingerprint(expected.token), secure = await getSecureMessaging(), c = await secure.ensureSession();
  const options = await slackDigest(JSON.stringify([choices.channels, Object.entries(choices.mapping).sort(), choices.prefix]));
  const key = await slackDigest(workspace + '\0' + archive.digest + '\0' + options);
  const total = choices.channels.reduce((sum, id) => sum + (archive.channels.find(v => v.id === id)?.messages.length || 0), 0);
  const assertIdentity = () => { assertOwner(expected); if (c.getDeviceId() !== state!.device) throw new Error('Resume this encrypted migration on its original trusted device.'); };
  const assert = () => { assertIdentity(); if (!isCurrent()) throw new Error('The migration was paused because its workspace changed.'); };
  let state = (await listPrivate<Checkpoint>('slack-import', expected)).find(row => row.id === key)?.value;
  if (state && (state.archive !== archive.digest || state.options !== options || state.workspace !== workspace || state.session !== tokenHash || state.device !== c.getDeviceId())) throw new Error('Resume this encrypted migration in its original sign-in and trusted device.');
  state ||= { version: 1, archive: archive.digest, options, workspace, session: tokenHash, device: c.getDeviceId()!, channelIndex: 0, messageIndex: 0, processed: 0, events: {}, result: { channels: [], messages: 0, files: 0, missing_files: 0 }, completed: false };
  assert(); if (state.completed) { if (state.jobId) await finishSecure(workspace, state.jobId, archive.digest, options); return state.result; }
  if (!choices.channels.length || choices.channels.length > 100 || new Set(choices.channels).size !== choices.channels.length || choices.channels.some(id => !archive.channels.some(v => v.id === id))) throw new Error('Choose between one and 100 channels from this archive.');
  await savePrivate('slack-import', key, state, expected);
  for (; state.channelIndex < choices.channels.length; state.channelIndex++) {
    assert(); const source = archive.channels.find(v => v.id === choices.channels[state!.channelIndex])!;
    const members = [...new Set(source.members.flatMap(id => choices.mapping[id] ? [choices.mapping[id]] : []))];
    const reservation = await api.request<{ id: string; channel_id: string }>('/workspaces/' + encodeURIComponent(workspace) + '/slack-imports/secure-reserve', { method: 'POST', body: JSON.stringify({ archive_digest: archive.digest, options_digest: options, source_digest: await slackDigest(archive.digest + '\0' + source.id), member_ids: members }) });
    assert(); state.jobId = reservation.id; const channelId = reservation.channel_id;
    const binding = (await secure.refreshBindings()).find(row => row.channel_id === channelId);
    if (!binding || binding.status !== 'active' || !binding.room_id) await secure.enableChannel(channelId, (choices.prefix + source.name).slice(0, 100));
    if (!state.result.channels.some(row => row.id === channelId)) await secure.sendEvent(channelId, 'eu.freecollab.channel.metadata', { name: (choices.prefix + source.name).slice(0, 100) }, 'slack-title-' + await slackDigest(key + source.id));
    assert(); const currentRoom = await secure.roomFor(channelId, true);
    if (!state.result.channels.some(row => row.id === channelId)) state.result.channels.push({ id: channelId, name: (choices.prefix + source.name).slice(0, 100), source_id: source.id });
    await savePrivate('slack-import', key, state, expected);
    const timestamps = new Set(source.messages.map(row => row.ts)), roots = new Set(source.messages.flatMap(row => row.parent && row.parent !== row.ts ? [row.parent] : []));
    for (; state.messageIndex < source.messages.length; state.messageIndex++) {
      assert(); const message = source.messages[state.messageIndex], identity = await slackDigest(key + '\0' + source.id + '\0' + message.ts);
      const relation = message.parent && message.parent !== message.ts ? state.events[source.id + '\0' + message.parent] : undefined;
      if (message.parent && message.parent !== message.ts && timestamps.has(message.parent) && !relation) throw new Error('Import the thread root before its replies.');
      let body = '[Imported from Slack · ' + message.details.author.name + ' · ' + message.ts + ']\n' + message.text.slice(0, 5000);
      if (message.text.length > 5000) body += '\n[Full source text is available in imported history]';
      if (message.missing) body += '\n[' + message.missing + ' original attachment(s) unavailable in this archive]';
      const pending = state.pendingMain, transaction = 'slack-message-' + identity;
      if (pending && (pending.identity !== identity || pending.channel !== channelId)) throw new Error('The pending encrypted import receipt does not match this source record.');
      let sent = pending?.eventId ? { event_id: pending.eventId } : undefined;
      if (pending && !sent) {
        sent = await recoverAcceptedMain(c, currentRoom.room, options, archive.digest, source.id, message.ts, body, relation, transaction, assert);
        if (!sent && !currentRoom.room.getEventForTxnId(transaction)) throw new Error('The encrypted acknowledgement is still unavailable. Wait for encrypted sync or reconcile the destination before continuing.');
      }
      if (!sent) {
        const serialized = JSON.stringify(message.details), detailHash = await slackDigest(serialized);
        let parts = pending?.parts;
        let metadata: Record<string, unknown> = { ...message.details, migration_digest: options, archive_digest: archive.digest, source_id: source.id };
        if (new TextEncoder().encode(JSON.stringify(metadata)).length > 8000) {
          if (!parts) {
            parts = []; const chunks = Math.ceil(serialized.length / 6000);
            for (let index = 0; index < chunks; index++) { assert(); const part = await secure.sendEvent(channelId, SLACK_HISTORY_EVENT, { migration: options, record: message.ts, index, total: chunks, data: serialized.slice(index * 6000, (index + 1) * 6000) }, 'slack-part-' + identity + '-' + index); assert(); parts.push(part.event_id); }
          }
          metadata = { version: 1, source: 'slack', source_timestamp: message.ts, parts, hash: detailHash, migration_digest: options, archive_digest: archive.digest, source_id: source.id };
        }
        state.pendingMain = { identity, channel: channelId, ...(parts ? { parts } : {}) };
        await savePrivate('slack-import', key, state, expected); assert();
        sent = await secure.sendEvent(channelId, 'm.room.message', { msgtype: 'm.text', body, [META]: metadata, ...(relation ? { 'm.relates_to': { rel_type: 'm.thread', event_id: relation, is_falling_back: true, 'm.in_reply_to': { event_id: relation } } } : {}) }, transaction);
      }
      // An ordinary pause must not discard a known receipt and create another
      // SDK local echo. Account/device changes still prevent any vault write.
      assertIdentity(); if (!sent.event_id?.startsWith('$')) throw new Error('The encrypted migration returned no server receipt.');
      state.pendingMain = { ...(state.pendingMain || { identity, channel: channelId }), eventId: sent.event_id };
      await savePrivate('slack-import', key, state, expected);
      assert(); const confirmed = await secure.getDecryptedEvent(channelId, sent.event_id);
      if (!acceptedMain(confirmed, c, currentRoom.roomId, options, archive.digest, source.id, message.ts, body, relation)) throw new Error('The encrypted migration acknowledgement could not be verified.');
      if (roots.has(message.ts)) state.events[source.id + '\0' + message.ts] = sent.event_id;
      for (let index = 0; index < message.files.length; index++) {
        assert(); const file = message.files[index], bytes = archive.read(file.path), requestId = 'slack-' + await slackDigest(identity + '\0' + index + '\0' + file.path);
        await secureFileProvider.upload(channelId, new File([new Uint8Array(bytes)], file.name.replace(/^.*[\\/]/, '') || 'attachment', { type: file.mime }), { requestId });
      }
      delete state.pendingMain;
      state.result.messages++; state.result.files += message.files.length; state.result.missing_files += message.missing; state.processed++;
      state.messageIndex++; await savePrivate('slack-import', key, state, expected); state.messageIndex--; onProgress(state.processed, total);
    }
    state.messageIndex = 0; state.events = {}; // only current channel roots are needed
    state.channelIndex++; await savePrivate('slack-import', key, state, expected); state.channelIndex--;
  }
  state.completed = true; await savePrivate('slack-import', key, state, expected); if (state.jobId) await finishSecure(workspace, state.jobId, archive.digest, options); onProgress(total, total); return state.result;
}

function acceptedMain(event: MatrixEvent, c: MatrixClient, roomId: string, options: string, archive: string, source: string, timestamp: string, body: string, parent?: string) {
  const content = event.getContent(), value = content[META], relation = content['m.relates_to'], wire = event.getWireContent();
  return event.isEncrypted() && event.getWireType() === 'm.room.encrypted' && event.getType() === 'm.room.message'
    && (event.status === null || event.status === 'sent') && !!event.getId()?.startsWith('$')
    && event.getRoomId() === roomId && event.getSender() === c.getUserId() && !event.isRedacted()
    && (wire.device_id === undefined || wire.device_id === c.getDeviceId())
    && content.msgtype === 'm.text' && content.body === body && (parent ? relation?.rel_type === 'm.thread' && relation.event_id === parent : !relation)
    && value?.version === 1 && value.source === 'slack' && value.migration_digest === options && value.archive_digest === archive && value.source_id === source && value.source_timestamp === timestamp;
}
async function recoverAcceptedMain(c: MatrixClient, room: Room, options: string, archive: string, source: string, timestamp: string, body: string, parent: string | undefined, transaction: string, assert: () => void) {
  for (let attempt = 0; attempt < 24; attempt++) {
    assert(); const matches = new Set<string>();
    const pending = room.getEventForTxnId(transaction);
    for (const event of [...room.getLiveTimeline().getEvents().slice(-256), ...(pending ? [pending] : [])]) {
      if (!event.isEncrypted() || event.getSender() !== c.getUserId() || !event.getId()?.startsWith('$') || event.isRedacted()) continue;
      await c.decryptEventIfNeeded(event).catch(() => undefined);
      if (acceptedMain(event, c, room.roomId, options, archive, source, timestamp, body, parent)) matches.add(event.getId()!);
    }
    if (matches.size > 1) throw new Error('Multiple authenticated source events need reconciliation before continuing.');
    if (matches.size === 1) return { event_id: [...matches][0] };
    if (room.getEventForTxnId(transaction)?.status === 'not_sent') return undefined;
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  return undefined;
}

export async function readSlackHistory(channelId: string, messageId: string, encrypted: boolean): Promise<SlackHistoricalDetails | null> {
  if (!encrypted) return api.request('/channels/' + encodeURIComponent(channelId) + '/messages/' + encodeURIComponent(messageId) + '/slack-history');
  const secure = await getSecureMessaging(), event = await secure.getDecryptedEvent(channelId, messageId), value = event.getContent()[META];
  if (!value || value.version !== 1 || value.source !== 'slack') return null;
  if (!Array.isArray(value.parts)) return validateDetails(value);
  if (!value.parts.length || value.parts.length > 64) throw new Error('Invalid imported history manifest');
  const chunks: string[] = [];
  for (let index = 0; index < value.parts.length; index++) {
    const part = await secure.getDecryptedEvent(channelId, value.parts[index]), data = part.getContent();
    if (part.getType() !== SLACK_HISTORY_EVENT || part.getSender() !== event.getSender() || part.isRedacted() || data.migration !== value.migration_digest || data.record !== value.source_timestamp || data.index !== index || data.total !== value.parts.length || typeof data.data !== 'string' || data.data.length > 6000) throw new Error('Imported history parts could not be verified');
    chunks.push(data.data);
  }
  const text = chunks.join(''); if (await slackDigest(text) !== value.hash) throw new Error('Imported history commitment did not match'); return validateDetails(JSON.parse(text));
}
function validateDetails(value: unknown): SlackHistoricalDetails {
  const data = value as SlackHistoricalDetails, stamp = (v: unknown) => typeof v === 'string' && /^\d{1,12}\.\d{1,6}$/.test(v);
  const person = (v: unknown) => !!v && typeof v === 'object' && 'id' in v && typeof v.id === 'string' && v.id.length <= 80 && 'name' in v && typeof v.name === 'string' && v.name.length <= 200;
  if (!data || data.version !== 1 || data.source !== 'slack' || !stamp(data.source_timestamp) || data.edited_at !== null && !stamp(data.edited_at) || !person(data.author) || typeof data.current_text !== 'string' || data.current_text.length > 18000 || !Array.isArray(data.history) || data.history.length > 25 || !Array.isArray(data.reactions) || data.reactions.length > 100 || data.history.some(row => typeof row.text !== 'string' || row.text.length > 18000 || !stamp(row.timestamp) || !person(row.author)) || data.reactions.some(row => typeof row.name !== 'string' || !/^[A-Za-z0-9_+\-]{1,100}$/.test(row.name) || !Number.isSafeInteger(row.count) || row.count < 0 || row.count > 1000000 || !Array.isArray(row.users) || row.users.length > 5000 || row.count < row.users.length || row.users.some(user => !person(user))) || new TextEncoder().encode(JSON.stringify(data)).length > 128 * 1024) throw new Error('Invalid imported Slack history');
  return data;
}

async function finishSecure(workspace: string, job: string, archive: string, options: string) {
  return api.request('/workspaces/' + encodeURIComponent(workspace) + '/slack-imports/' + encodeURIComponent(job) + '/complete-secure', { method: 'POST', body: JSON.stringify({ archive_digest: archive, options_digest: options }) });
}
export async function discardSecureSlackMigration(workspace: string, archive: string) {
  const expected = owner();
  for (const row of await listPrivate<Checkpoint>('slack-import', expected)) {
    if (row.value.workspace !== workspace || row.value.archive !== archive || row.value.completed) continue;
    if (row.value.jobId) await api.request('/workspaces/' + encodeURIComponent(workspace) + '/slack-imports/' + encodeURIComponent(row.value.jobId), { method: 'DELETE' });
    await removePrivate('slack-import', row.id, expected);
  }
}
