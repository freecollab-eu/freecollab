import { createClient, ClientEvent, RoomEvent, MatrixEventEvent, EventType, MsgType, Method, Direction, type MatrixClient, MatrixEvent } from 'matrix-js-sdk';
import { CryptoEvent } from 'matrix-js-sdk/lib/crypto-api/CryptoEvent';
import { decodeRecoveryKey } from 'matrix-js-sdk/lib/crypto-api/recovery-key';
import { storageKeyFor, loadPendingNotices, savePendingNotices } from './device-store';
import { sendOrResumeEncryptedTransaction } from './transaction-retry';
import { EncryptedPublishGuards, type SecurePublishOptions } from './publish-guard';
export type { SecurePublishOptions } from './publish-guard';
import { encryptedMessageContent, encryptedContentReferences, prepareMessageReferences, type ReferenceResolution } from './message-content';
import type { MessageReference } from '../message-references';
import { APIError, type Message, type Reaction, type ReadReceipt } from '../api';
import { channelMetadataFields, legacyChannelName, verifiedChannelMetadata, type ChannelMetadata, type ChannelMetadataRecord } from '../channel-metadata';
const quietLogger = { trace() {}, debug() {}, info() {}, warn() {}, error() {}, getChild() { return this; } };
interface Configuration { baseUrl: string; getAccessToken: () => string | null; getUserId: () => string | null }
export interface SecureBinding { channel_id: string; room_id: string | null; status: string; federated_peer_server?: string }
export interface SecureDevice { user_id: string; matrix_user_id: string; device_id: string; fingerprint: string; verified: boolean; name?: string }
type Listener = (event: string, payload: Record<string, unknown>) => void;
const METADATA = 'eu.freecollab.channel.metadata', REACTION = 'eu.freecollab.reaction', RECEIPT = 'eu.freecollab.receipt';
let configuration: Configuration | null = null, client: MatrixClient | null = null, starting: Promise<MatrixClient> | null = null, ownerToken: string | null = null;
let bindings = new Map<string, SecureBinding>(), bindingsLoaded = false;
const listeners = new Set<Listener>(), names = new Map<string, string>();
const channelDetails = new Map<string, ChannelMetadata>();
const legacyMetadataOrder = new Map<string, { time: number; id: string }>();
let generation = 0;
const transactionEpochs = new WeakMap<MatrixClient, Map<string, string>>();
const publicationGuards = new WeakMap<MatrixClient, EncryptedPublishGuards>();
interface RevokedDevice { user_id: string; matrix_user_id: string; device_id: string }
const revokedDevices = new Map<string, RevokedDevice>();
const deviceKey = (user: string, device: string) => user + '\0' + device;
function rememberRevocations(rows: RevokedDevice[]) { for (const row of rows) revokedDevices.set(deviceKey(row.matrix_user_id, row.device_id), row); }
async function refreshRevocations(channelId?: string) {
  const result = await request<{ revoked_devices: RevokedDevice[] }>('/secure/devices/revocations' + (channelId ? '?channel_id=' + encodeURIComponent(channelId) : ''));
  rememberRevocations(result.revoked_devices); return result.revoked_devices;
}
interface Notice { channelId: string; eventId: string; mentions: string[]; parentId?: string }
let noticeTimer: ReturnType<typeof setTimeout> | undefined, noticesOwner: string | null = null, flushingNotices = false;
const notices = new Map<string, Notice>();
async function flushNotices() {
  const owner = configuration?.getUserId(), token = configuration?.getAccessToken();
  if (!owner || !token || flushingNotices) return;
  flushingNotices = true;
  try {
    if (noticesOwner !== owner) {
      notices.clear(); noticesOwner = owner;
      for (const value of await loadPendingNotices(owner)) { const n = value as Notice; if (typeof n.channelId === 'string' && typeof n.eventId === 'string' && Array.isArray(n.mentions)) notices.set(n.eventId, n); }
    }
    for (const [id, notice] of notices) {
      if (configuration?.getAccessToken() !== token || configuration?.getUserId() !== owner) break;
      const response = await fetch(configuration!.baseUrl + '/notifications/channels/' + encodeURIComponent(notice.channelId) + '/encrypted-events', { method: 'POST', headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json' }, body: JSON.stringify({ event_id: notice.eventId, mentions: notice.mentions, ...(notice.parentId ? { parent_event_id: notice.parentId } : {}) }) });
      if (configuration?.getAccessToken() !== token || configuration?.getUserId() !== owner) break;
      if (response.ok || [400, 403, 404, 409].includes(response.status)) notices.delete(id);
      else break;
      await savePendingNotices(owner, [...notices.values()]);
    }
  } catch { /* Matrix content already succeeded; retry only its opaque notice. */ }
  finally {
    flushingNotices = false;
    if (notices.size && configuration?.getUserId() === owner && configuration?.getAccessToken() === token) { clearTimeout(noticeTimer); noticeTimer = setTimeout(() => { void flushNotices(); }, 5000); }
  }
}
async function queueNotice(notice: Notice) {
  const owner = configuration?.getUserId(); if (!owner) return;
  if (noticesOwner !== owner) { notices.clear(); noticesOwner = owner; for (const value of await loadPendingNotices(owner)) { const n = value as Notice; if (typeof n.eventId === 'string') notices.set(n.eventId, n); } }
  if (configuration?.getUserId() !== owner) return;
  notices.set(notice.eventId, notice); await savePendingNotices(owner, [...notices.values()]); void flushNotices();
}
let secretStorageKey: Uint8Array<ArrayBuffer> | null = null;
const accountId = (id: string) => {
  const localDomain = client?.getUserId()?.split(':').slice(1).join(':');
  const domain = id.split(':').slice(1).join(':');
  // Remote identities retain their homeserver to avoid cross-installation ID
  // collisions and never masquerade as a local account.
  return localDomain && domain !== localDomain ? id : /^@fc_([^:]+):/.exec(id)?.[1] || id;
};
const emit = (event: string, payload: Record<string, unknown>) => listeners.forEach(fn => fn(event, payload));
export function configureSecureMessaging(next: Configuration) { configuration = next; }
async function request<T>(path: string, body?: unknown, method?: 'PATCH'): Promise<T> {
  if (!configuration?.getAccessToken()) throw new Error('Sign in to unlock encrypted channels.');
  const requestToken = configuration.getAccessToken(), requestGeneration = generation;
  const response = await fetch(configuration.baseUrl + path, { method: method ?? (body === undefined ? 'GET' : 'POST'), headers: { Authorization: 'Bearer ' + requestToken, 'Content-Type': 'application/json' }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  if (!response.ok) { const data = await response.json().catch(() => null); if (response.status === 401 && requestToken === configuration?.getAccessToken() && requestGeneration === generation) window.dispatchEvent(new Event('freecollab:session-expired')); throw new APIError(typeof data?.detail === 'string' ? data.detail : typeof data?.detail?.message === 'string' ? data.detail.message : 'Encrypted channel is unavailable.', response.status, typeof data?.detail?.code === 'string' ? data.detail.code : undefined); }
  const data = await response.json();
  if (requestToken !== configuration?.getAccessToken() || requestGeneration !== generation) throw new Error('The signed-in account changed.');
  return data as T;
}
function asMessage(channelId: string, e: MatrixEvent): Message {
  const outer = e.getContent(), relation = e.getOriginalContent()['m.relates_to'];
  const content = relation?.rel_type === 'm.replace' && outer['m.new_content'] && typeof outer['m.new_content'] === 'object' ? outer['m.new_content'] : outer;
  const body = e.isDecryptionFailure() ? 'Encrypted content is unavailable on this device. Verify the device or unlock recovery.' : typeof content.body === 'string' ? content.body : '';
  return { id: e.getId()!, channel_id: channelId, author_id: accountId(e.getSender()!), content: body, encrypted: true, created_at: new Date(e.getTs()).toISOString(), deleted: e.isRedacted(), ...(e.isDecryptionFailure() ? { locked: true } : {}), ...(relation?.rel_type === 'm.thread' ? { parent_id: relation.event_id } : {}), mentions: Array.isArray(content['eu.freecollab.mentions']) ? content['eu.freecollab.mentions'] : [], references: e.isDecryptionFailure() || e.isRedacted() ? [] : encryptedContentReferences(body, content) };
}
async function processEvent(e: MatrixEvent, c: MatrixClient) {
  const eventGeneration = generation;
  if (!configuration?.getAccessToken() || ownerToken !== configuration.getAccessToken() || accountId(c.getUserId()!) !== configuration.getUserId()) return;
  const binding = [...bindings.values()].find(b => b.room_id === e.getRoomId());
  if (client && c !== client) return;
  if (!binding || !e.isEncrypted()) return;
  try {
    await c.decryptEventIfNeeded(e); if (eventGeneration !== generation || e.isDecryptionFailure()) return;
    const content = e.getContent(), relation = e.getOriginalContent()['m.relates_to'];
    if (e.getType() === METADATA) {
      const record = await request<ChannelMetadataRecord>('/channels/' + encodeURIComponent(binding.channel_id) + '/metadata');
      if (eventGeneration !== generation || c !== client) return;
      if (record.authoritative) {
        if (record.matrix_event_id !== e.getId()) return;
        publishChannelMetadata(verifiedChannelMetadata(record, binding.room_id!, c.getUserId()!, metadataEvent(e)));
      } else {
        const name = legacyChannelName(content), previous = legacyMetadataOrder.get(binding.channel_id);
        if (name === null || e.isRedacted() || !await validMetadataSender(binding.channel_id, e) || eventGeneration !== generation || c !== client) return;
        const order = { time: e.getTs(), id: e.getId() || '' };
        if (previous && (previous.time > order.time || previous.time === order.time && previous.id >= order.id)) return;
        legacyMetadataOrder.set(binding.channel_id, order);
        publishChannelMetadata({ ...record, name, description: '' });
      }
    }
    else if (e.getType() === EventType.RoomMessage) { const message = asMessage(binding.channel_id, e); if (relation?.rel_type === 'm.replace') { const original = await secureMessaging.getDecryptedEvent(binding.channel_id, relation.event_id!); if (original.getSender() !== e.getSender()) return; emit('message_updated', { ...message, id: relation.event_id, content: content['m.new_content']?.body || content.body }); } else if (message.parent_id) emit('thread_reply', { parent_id: message.parent_id, reply: message, thread_count: 0 }); else emit('new_message', message as unknown as Record<string, unknown>); }
    else if (e.getType() === REACTION) emit('reaction_updated', { channel_id: binding.channel_id, message_id: content.event_id });
    else if (e.getType() === RECEIPT) emit('read_receipt', { channel_id: binding.channel_id, user_id: accountId(e.getSender()!), last_read_message_id: content.event_id, read_at: new Date(e.getTs()).toISOString() });
    else emit('secure_event', { channel_id: binding.channel_id, event_id: e.getId(), type: e.getType() });
  } catch { /* Missing keys remain unavailable. */ }
}
async function validMetadataSender(channelId: string, event: MatrixEvent) {
  const members = await request<{ user_id: string; role: string }[]>('/channels/' + encodeURIComponent(channelId) + '/members');
  const domain = client?.getUserId()?.split(':').slice(1).join(':');
  return members.some(member => member.role === 'admin' && event.getSender() === '@fc_' + member.user_id + ':' + domain);
}
function metadataEvent(event: MatrixEvent) {
  return { id: event.getId(), roomId: event.getRoomId(), sender: event.getSender(), type: event.getType(),
    encrypted: event.isEncrypted(), redacted: event.isRedacted(), failed: event.isDecryptionFailure(), content: event.getContent() };
}
function publishChannelMetadata(metadata: ChannelMetadata) {
  const previous = channelDetails.get(metadata.channel_id);
  if (previous && (previous.authoritative && !metadata.authoritative || previous.version > metadata.version)) return;
  channelDetails.set(metadata.channel_id, metadata); names.set(metadata.channel_id, metadata.name);
  if (!previous || previous.name !== metadata.name || previous.description !== metadata.description || previous.version !== metadata.version) {
    emit('secure_metadata', { channel_id: metadata.channel_id, name: metadata.name, description: metadata.description, metadata_version: metadata.version });
  }
}
async function readChannelMetadata(channelId: string, supplied?: ChannelMetadataRecord): Promise<ChannelMetadata> {
  const token = configuration?.getAccessToken();
  const { c, room, roomId } = await roomFor(channelId);
  const attempt = generation;
  const current = () => { if (token !== configuration?.getAccessToken() || attempt !== generation || c !== client) throw new Error('The signed-in encryption session changed.'); };
  current();
  const record = supplied ?? await request<ChannelMetadataRecord>('/channels/' + encodeURIComponent(channelId) + '/metadata');
  current();
  if (record.channel_id !== channelId || !record.secure) throw new Error('Encrypted channel details could not be verified.');
  if (record.authoritative) {
    if (!record.matrix_event_id) throw new Error('Encrypted channel details could not be verified.');
    const event = await secureMessaging.getDecryptedEvent(channelId, record.matrix_event_id); current();
    const metadata = verifiedChannelMetadata(record, roomId, c.getUserId()!, metadataEvent(event));
    publishChannelMetadata(metadata); return metadata;
  }
  // Only the old name-only format can bootstrap a channel without a registered
  // metadata row. A new upload whose compare-and-swap failed is never eligible.
  for (;;) {
    const rows = await events(channelId); current();
    for (const event of [...rows].reverse()) {
      if (event.getType() !== METADATA || event.isRedacted() || event.isDecryptionFailure()) continue;
      const name = legacyChannelName(event.getContent());
      if (name === null || !await validMetadataSender(channelId, event)) continue;
      current();
      const metadata = { ...record, name, description: '' };
      publishChannelMetadata(metadata); return metadata;
    }
    const previous = room.oldState.paginationToken;
    if (!previous) break;
    await c.scrollback(room, 100); current();
    if (previous === room.oldState.paginationToken) break;
  }
  throw new Error('Encrypted channel details are unavailable. Verify this device or unlock recovery.');
}
async function writeChannelMetadata(channelId: string, name: string, description: string, expectedVersion: number): Promise<ChannelMetadata> {
  const fields = channelMetadataFields(name, description), token = configuration?.getAccessToken();
  const record = await request<ChannelMetadataRecord>('/channels/' + encodeURIComponent(channelId) + '/metadata');
  if (token !== configuration?.getAccessToken()) throw new Error('The signed-in encryption session changed.');
  if (!record.secure || record.channel_id !== channelId || !record.can_manage) throw new Error('Channel admin access required');
  if (record.version !== expectedVersion) throw new Error('Channel details changed; reload before saving');
  const result = await sendEvent(channelId, METADATA, { ...fields, channel_id: channelId, version: expectedVersion + 1, registration_required: true });
  if (token !== configuration?.getAccessToken()) throw new Error('The signed-in encryption session changed.');
  const saved = await request<ChannelMetadataRecord>('/channels/' + encodeURIComponent(channelId) + '/metadata', { expected_version: expectedVersion, matrix_event_id: result.event_id }, 'PATCH');
  if (token !== configuration?.getAccessToken()) throw new Error('The signed-in encryption session changed.');
  return readChannelMetadata(channelId, saved);
}
async function refreshBindings() {
  const token = configuration?.getAccessToken(), attempt = generation;
  const rows = await request<SecureBinding[]>('/secure/channels');
  if (token !== configuration?.getAccessToken() || attempt !== generation) throw new Error('The signed-in account changed.');
  bindings = new Map(rows.map(row => [row.channel_id, row])); bindingsLoaded = true;
  return rows;
}
async function handlesChannel(channelId: string): Promise<boolean> {
  const token = configuration?.getAccessToken(), actor = configuration?.getUserId();
  let invalidated = false;
  const invalidate = () => { invalidated = true; };
  const current = () => {
    if (invalidated || !token || !actor || token !== configuration?.getAccessToken() || actor !== configuration?.getUserId()) throw new Error('The signed-in account changed.');
  };
  const events = typeof window === 'undefined' ? undefined : window;
  events?.addEventListener?.('freecollab:auth-changed', invalidate);
  try {
    current();
    const rows = await refreshBindings(); current();
    // First-client initialization may clear the shared cache after this read.
    // A verified positive classification must not become a native fallback.
    if (rows.some(row => row.channel_id === channelId)) return true;
    // Missing bindings can also mean revoked access. Confirm an authorized
    // native channel before any caller sends plaintext to a native endpoint.
    const channel = await request<{ id: string; secure: boolean }>('/channels/' + encodeURIComponent(channelId)); current();
    if (!channel || channel.id !== channelId || typeof channel.secure !== 'boolean') throw new Error('Channel encryption could not be verified.');
    return channel.secure;
  } finally { events?.removeEventListener?.('freecollab:auth-changed', invalidate); }
}
async function ensureSession(): Promise<MatrixClient> {
  const token = configuration?.getAccessToken(); if (!token) throw new Error('Sign in to unlock encrypted channels.');
  if (ownerToken !== token) { generation++; if (client) { transactionEpochs.delete(client); publicationGuards.get(client)?.stop(); } client?.stopClient(); client = null; starting = null; bindings.clear(); bindingsLoaded = false; names.clear(); channelDetails.clear(); legacyMetadataOrder.clear(); revokedDevices.clear(); secretStorageKey?.fill(0); secretStorageKey = null; ownerToken = token; }
  if (client) return client; if (starting) return starting;
  const attempt = generation;
  const assertCurrent = (c?: MatrixClient) => { if (generation !== attempt || configuration?.getAccessToken() !== token) { c?.stopClient(); throw new Error("The signed-in account changed."); } };
  starting = (async () => {
    const auth = await request<{ user_id: string; access_token: string; device_id: string }>('/secure/session', {}); assertCurrent();
    const c: MatrixClient = createClient({ fetchFn: (input, init) => {
      const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url);
      const match = /\/rooms\/([^/]+)\/send\/m\.room\.encrypted\/([^/]+)$/.exec(url.pathname);
      if (!match) return fetch(input, init);
      if (generation !== attempt || configuration?.getAccessToken() !== token || ownerToken !== token) throw new Error('The signed-in encryption session changed.');
      const epoch = transactionEpochs.get(c)?.get(decodeURIComponent(match[1]) + '\0' + decodeURIComponent(match[2]));
      const headers = new Headers(init?.headers); if (epoch) headers.set('X-FreeCollab-Membership-Epoch', epoch);
      let guards = publicationGuards.get(c); if (!guards) { guards = new EncryptedPublishGuards(); publicationGuards.set(c, guards); }
      return guards.send(decodeURIComponent(match[1]), decodeURIComponent(match[2]), init?.body, init?.signal || undefined, signal => fetch(input, { ...init, headers, signal }));
    }, logger: quietLogger, timelineSupport: true, disableVoip: true, baseUrl: new URL(configuration!.baseUrl, location.origin).href.replace(/\/$/, ''), userId: auth.user_id, deviceId: auth.device_id, accessToken: auth.access_token, cryptoCallbacks: { getSecretStorageKey: async ({ keys }) => { if (attempt !== generation || token !== configuration?.getAccessToken()) return null; if (!secretStorageKey) return null; for (const id of Object.keys(keys)) if (await c.secretStorage.checkKey(secretStorageKey, keys[id])) return [id, secretStorageKey]; throw new Error('The recovery key does not match this account.'); } } });
    c.on(CryptoEvent.KeyBackupSessionsRemaining, remaining => { backupRemaining.set(c, remaining); });
    const storageKey = await storageKeyFor(auth.user_id + ':' + auth.device_id);
    await c.initRustCrypto({ useIndexedDB: true, cryptoDatabasePrefix: 'freecollab-' + auth.user_id + '-' + auth.device_id, storageKey }); storageKey.fill(0); assertCurrent(c);
    c.getCrypto()!.globalBlacklistUnverifiedDevices = true;
    await refreshBindings(); assertCurrent(c); c.on(RoomEvent.Timeline, e => { void processEvent(e, c); });
    c.on(RoomEvent.Timeline, e => { e.once(MatrixEventEvent.Decrypted, () => { void processEvent(e, c); }); });
    c.on(RoomEvent.Redaction, e => { const b = [...bindings.values()].find(item => item.room_id === e.getRoomId()); if (b) emit('message_deleted', { channel_id: b.channel_id, message_id: e.getAssociatedId() }); });
    await new Promise<void>((resolve, reject) => { const timer = setTimeout(() => reject(new Error('Encrypted sync timed out. Please retry.')), 45000); c.on(ClientEvent.Sync, state => { if (state === 'PREPARED') { clearTimeout(timer); resolve(); } }); void c.startClient({ initialSyncLimit: 100 }).catch(reject); });
    assertCurrent(c); client = c; void flushNotices(); for (const b of bindings.values()) if (b.room_id) for (const e of c.getRoom(b.room_id)?.getLiveTimeline().getEvents() || []) void processEvent(e, c); return c;
  })().catch(error => { if (generation === attempt) { client?.stopClient(); client = null; starting = null; } throw error; }); return starting;
}
async function roomFor(channelId: string, writing = false) {
  const token = configuration?.getAccessToken(), c = await ensureSession(), attempt = generation;
  const assertClient = () => { if (token !== configuration?.getAccessToken() || attempt !== generation || c !== client) throw new Error('The signed-in encryption session changed.'); };
  assertClient();
  if (!bindingsLoaded || !bindings.has(channelId)) await refreshBindings();
  const binding = bindings.get(channelId); if (!binding?.room_id || binding.status !== 'active') throw new Error('This encrypted channel is not ready.');
  const assertCurrent = () => {
    assertClient();
    const current = bindings.get(channelId);
    if (current?.room_id !== binding.room_id || current.status !== 'active') throw new Error('The encrypted channel binding changed.');
  };
  let room = c.getRoom(binding.room_id);
  const joining = !room || room.getMyMembership() !== 'join';
  // A newly granted Connect member has a product seat before Matrix invites
  // exist. Synchronize that current authorized seat before the first read/join.
  const snapshot = writing || joining ? await request<{ membership_epoch: string; revoked_devices: RevokedDevice[]; federated_operators?: string[] }>('/secure/channels/' + encodeURIComponent(channelId) + '/sync', {}) : undefined;
  if (joining) { await c.joinRoom(binding.room_id); room = c.getRoom(binding.room_id); }
  if (!room || !c.isRoomEncrypted(binding.room_id)) throw new Error('Encrypted room state is unavailable. Sending is blocked.');
  if (writing) {
    if (!snapshot || !Array.isArray(snapshot.revoked_devices)) throw new Error('Encrypted device snapshot is unavailable.');
    rememberRevocations(snapshot.revoked_devices);
    const state = await c.roomState(binding.room_id);
    room.currentState.setStateEvents(state.map(event => new MatrixEvent(event)));
    // getUserDeviceInfo(true) does not force a query for tracked users. Inspect
    // the actual Rust recipient cache and fail closed until normal SDK /sync
    // processes the removal. Never merely filter a revoked key out of the UI.
    const crypt = c.getCrypto()!, removed = snapshot.revoked_devices;
    if (removed.some(row => row.matrix_user_id === c.getUserId())) await crypt.userHasCrossSigningKeys(c.getUserId()!, true);
    const keys = removed.length ? await crypt.getUserDeviceInfo([...new Set(removed.map(row => row.matrix_user_id))], true) : new Map();
    if (removed.some(row => keys.get(row.matrix_user_id)?.has(row.device_id))) throw new Error('Device keys are refreshing after removal. Retry when encrypted sync finishes.');
    await c.getCrypto()!.forceDiscardSession(binding.room_id);
  }
  assertCurrent();
  return { c, room, roomId: binding.room_id, assertCurrent, membershipEpoch: snapshot?.membership_epoch, federatedOperators: snapshot?.federated_operators,
    federatedPeerServer: binding.federated_peer_server };
}
async function events(channelId: string, paginate = false) {
  const { c, room } = await roomFor(channelId); if (paginate && room.oldState.paginationToken) await c.scrollback(room, 100);
  const rows = room.getLiveTimeline().getEvents(); await Promise.all(rows.filter(e => e.isEncrypted()).map(e => c.decryptEventIfNeeded(e).catch(() => undefined))); return rows.filter(e => e.isEncrypted());
}
async function requireVerifiedRecipients(channelId: string, room: ReturnType<MatrixClient['getRoom']>, operators?: string[]) {
  const members = await request<{ user_id: string; name: string; matrix_user_id?: string }[]>('/channels/' + encodeURIComponent(channelId) + '/members');
  if (operators?.length) {
    const domain = client?.getUserId()?.split(':').slice(1).join(':');
    const approved = new Set([...operators, ...members.map(member => member.matrix_user_id || '@fc_' + member.user_id + ':' + domain)]);
    if (room?.getJoinedMembers().some(member => !approved.has(member.userId))) throw new Error('Unexpected encrypted room occupants. Refresh the approved participants before sending.');
  }
  const devices = await getDevices(channelId);
  for (const member of members) {
    if (member.user_id === configuration?.getUserId()) continue;
    const known = devices.filter(device => device.user_id === member.user_id);
    if (!known.some(device => device.verified) || !known.some(device => room?.getMember(device.matrix_user_id)?.membership === 'join'))
      throw new Error('Verify a joined device for ' + member.name + ' before sending encrypted content.');
  }
}
async function sendEvent(channelId: string, type: string, content: Record<string, unknown>, transactionId?: string, options: SecurePublishOptions = {}) {
  const token = configuration?.getAccessToken(), activeClient = await ensureSession(), attempt = generation;
  const assertCurrent = () => { if (generation !== attempt || configuration?.getAccessToken() !== token || client !== activeClient) throw new Error('The signed-in encryption session changed.'); };
  const { c, room, roomId, membershipEpoch, federatedOperators } = await roomFor(channelId, true);
  if (type !== METADATA || federatedOperators?.length) await requireVerifiedRecipients(channelId, room, federatedOperators);
  assertCurrent();
  const transaction = transactionId || c.makeTxnId();
  if (!membershipEpoch) throw new Error('Encrypted membership snapshot is unavailable.');
  if (!room) throw new Error('The encrypted room is unavailable.');
  assertCurrent();
  let guards = publicationGuards.get(c); if (!guards) { guards = new EncryptedPublishGuards(); publicationGuards.set(c, guards); }
  const release = guards.register(roomId, transaction, {...options, ...(options.authorize ? {authorize:async () => {
    assertCurrent(); const fresh = await roomFor(channelId, true); assertCurrent();
    if (fresh.c !== c || fresh.roomId !== roomId || fresh.membershipEpoch !== membershipEpoch) throw new Error('The encrypted publishing membership changed.');
    await options.authorize!(); assertCurrent();
  }} : {})}, assertCurrent);
  try {
    const result = await sendOrResumeEncryptedTransaction(c, room, roomId, type, content, transaction, membershipEpoch, async existing => {
    assertCurrent();
    let epochs = transactionEpochs.get(c);
    if (!epochs) { epochs = new Map(); transactionEpochs.set(c, epochs); }
    const transactionKey = roomId + '\0' + transaction;
    epochs.set(transactionKey, membershipEpoch);
    try { return await (existing ? c.resendEvent(existing, room) : c.sendEvent(roomId, type as never, content as never, transaction)); }
    finally { epochs.delete(transactionKey); }
  });
  assertCurrent();
  return result;
  } finally { release(); }
}

async function getMessages(channelId: string, before?: string, limit = 100, rootsOnly = false): Promise<Message[]> {
  const { c, room } = await roomFor(channelId);
  const boundedLimit = Math.max(1, Math.min(limit, 200));
  let rows = await events(channelId);
  const messageEvents = () => rows.filter(e => (e.getType() === EventType.RoomMessage || e.isDecryptionFailure()) && e.getOriginalContent()['m.relates_to']?.rel_type !== 'm.replace'
    && (!rootsOnly || e.getOriginalContent()['m.relates_to']?.rel_type !== 'm.thread'));
  const older = () => { const messages = messageEvents(); const index = before ? messages.findIndex(e => e.getId() === before) : messages.length; return index < 0 ? [] : messages.slice(0, index); };
  while (older().length < boundedLimit && room.oldState.paginationToken) {
    const previous = room.oldState.paginationToken;
    await c.scrollback(room, 100);
    rows = await events(channelId);
    if (previous === room.oldState.paginationToken) break;
  }
  const changes = new Map<string, MatrixEvent>();
  for (const e of rows) { const relation = e.getOriginalContent()['m.relates_to']; if (e.getType() === EventType.RoomMessage && relation?.rel_type === 'm.replace' && relation.event_id) changes.set(relation.event_id, e); }
  return older().slice(-boundedLimit).reverse().map(e => {
    const item = asMessage(channelId, e), edit = changes.get(item.id);
    if (edit && edit.getSender() === e.getSender()) { const replacement = asMessage(channelId, edit); item.content = replacement.content; item.references = replacement.references; item.mentions = replacement.mentions; item.edited_at = new Date(edit.getTs()).toISOString(); }
    item.thread_count = rows.filter(reply => reply.getOriginalContent()['m.relates_to']?.rel_type === 'm.thread' && reply.getOriginalContent()['m.relates_to']?.event_id === item.id).length;
    return item;
  });
}

/** One current room snapshot: a reloaded device's cached /sync can still be behind. */
async function currentRoomEvents(c: MatrixClient, roomId: string, limit: number, current: () => void): Promise<{ events: MatrixEvent[]; atStart: boolean }> {
  current();
  const snapshot = await c.createMessagesRequest(roomId, null, limit, Direction.Backward);
  current();
  if (!Array.isArray(snapshot.chunk)) throw new Error('Encrypted room history is unavailable.');
  const chunk = snapshot.chunk.slice(0, limit);
  const atStart = snapshot.chunk.length <= limit && (snapshot.end === undefined || chunk.some(event => event.type === EventType.RoomCreate));
  const rows = chunk.map(event => {
    if (!event || typeof event !== 'object' || event.room_id && event.room_id !== roomId) throw new Error('The encrypted room history changed.');
    return new MatrixEvent({ ...event, room_id: roomId });
  }).reverse().filter(event => event.isEncrypted());
  let next = 0;
  await Promise.all(Array.from({ length: Math.min(4, rows.length) }, async () => {
    while (next < rows.length) { current(); const event = rows[next++]; await c.decryptEventIfNeeded(event).catch(() => undefined); current(); }
  }));
  current(); return { events: rows, atStart };
}

/** Bounded current ciphertext inspection; never paginates or marks read. */
async function getRecentMessages(channelId: string, options: { limit?: number; eventLimit?: number; signal?: AbortSignal; isCurrent?: () => boolean } = {}): Promise<Message[]> {
  const token = configuration?.getAccessToken(), actor = configuration?.getUserId();
  const limit = options.limit ?? 100, eventLimit = options.eventLimit ?? 200;
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 100 || !Number.isSafeInteger(eventLimit) || eventLimit < 1 || eventLimit > 200) throw new Error('Choose bounded recent message limits.');
  const identity = () => { if (!token || !actor || token !== configuration?.getAccessToken() || actor !== configuration?.getUserId()
    || options.signal?.aborted || options.isCurrent && !options.isCurrent()) throw new Error('The encrypted conversation changed.'); };
  identity(); const initialClient = await ensureSession(); identity();
  // The first legitimate session bootstrap advances generation. Bind only after
  // that guarded initialization, then reject every later client/generation change.
  const attempt = generation;
  const sameOwner = () => { identity(); if (attempt !== generation || initialClient !== client) throw new Error('The encrypted conversation changed.'); };
  sameOwner(); await refreshBindings(); sameOwner();
  const { c, roomId, assertCurrent } = await roomFor(channelId); sameOwner();
  const current = () => { sameOwner(); assertCurrent(); if (c !== client) throw new Error('The encrypted conversation changed.'); };
  current();
  const { events: rows } = await currentRoomEvents(c, roomId, eventLimit, current);
  await refreshBindings(); current();
  const changes = new Map<string, MatrixEvent>();
  for (const event of rows) {
    const relation = event.getOriginalContent()['m.relates_to'];
    if (!event.isRedacted() && !event.isDecryptionFailure() && event.getType() === EventType.RoomMessage
        && relation?.rel_type === 'm.replace' && typeof relation.event_id === 'string') changes.set(relation.event_id, event);
  }
  return rows.filter(event => (event.getType() === EventType.RoomMessage || event.isDecryptionFailure())
    && event.getOriginalContent()['m.relates_to']?.rel_type !== 'm.replace').slice(-limit).reverse().map(event => {
    const item = asMessage(channelId, event), edit = changes.get(item.id);
    if (edit && edit.getSender() === event.getSender()) { const replacement = asMessage(channelId, edit);
      item.content = replacement.content; item.references = replacement.references; item.mentions = replacement.mentions; item.edited_at = new Date(edit.getTs()).toISOString(); }
    return item;
  });
}

async function getReadReceipts(channelId: string): Promise<ReadReceipt[]> {
  const token = configuration?.getAccessToken(), actor = configuration?.getUserId();
  const identity = () => { if (!token || !actor || token !== configuration?.getAccessToken() || actor !== configuration?.getUserId()) throw new Error('The encrypted conversation changed.'); };
  identity(); const initialClient = await ensureSession(); identity();
  const attempt = generation;
  const sameOwner = () => { identity(); if (attempt !== generation || initialClient !== client) throw new Error('The encrypted conversation changed.'); };
  sameOwner(); await refreshBindings(); sameOwner();
  const { c, roomId, assertCurrent } = await roomFor(channelId); sameOwner();
  const current = () => { sameOwner(); assertCurrent(); if (c !== client) throw new Error('The encrypted conversation changed.'); };
  const { events: rows, atStart } = await currentRoomEvents(c, roomId, 200, current);
  await refreshBindings(); current();
  const latest = new Map<string, ReadReceipt>();
  let ownReceiptIndex = -1;
  for (const [index, event] of rows.entries()) {
    if (event.isRedacted() || event.isDecryptionFailure() || event.getType() !== RECEIPT || typeof event.getContent().event_id !== 'string' || !event.getContent().event_id) continue;
    latest.set(event.getSender()!, { user_id: accountId(event.getSender()!), last_read_message_id: event.getContent().event_id, read_at: new Date(event.getTs()).toISOString() });
    if (accountId(event.getSender()!) === actor) ownReceiptIndex = index;
  }
  // A newer unreadable own event may itself be a receipt. A bounded page with
  // no own receipt also cannot establish that this member has never read.
  if ((ownReceiptIndex < 0 && !atStart) || rows.some((event, index) => index > ownReceiptIndex && !event.isRedacted()
      && (event.isDecryptionFailure() || event.getType() === 'm.room.encrypted') && accountId(event.getSender()!) === actor)) throw new Error('Unlock encrypted history to verify your reading position.');
  return [...latest.values()];
}

/** Resolve one root/reply by its real encrypted event, including an actual root parent. */
async function getMessage(channelId: string, eventId: string): Promise<Message> {
  const token = configuration?.getAccessToken(), actor = configuration?.getUserId();
  const validId = (value: unknown): value is string => typeof value === 'string' && !!value && value.length <= 1024 && !/[\s\u0000-\u001f\u007f]/.test(value);
  const unavailable = () => new Error('This encrypted source message could not be verified.');
  if (!token || !actor || !validId(eventId)) throw unavailable();
  const identity = () => { if (token !== configuration?.getAccessToken() || actor !== configuration?.getUserId()) throw unavailable(); };
  const initialClient = await ensureSession(); identity();
  const attempt = generation;
  const sameOwner = () => { identity(); if (generation !== attempt || initialClient !== client) throw unavailable(); };
  sameOwner();
  await refreshBindings(); sameOwner();
  const { c, roomId, assertCurrent } = await roomFor(channelId); sameOwner();
  const current = () => { sameOwner(); assertCurrent(); if (c !== client) throw unavailable(); };
  const verify = (event: MatrixEvent, id: string, root = false) => {
    current();
    if (event.getId() !== id || event.getRoomId() !== roomId || !event.isEncrypted() || event.getType() !== EventType.RoomMessage
      || event.isRedacted() || event.isDecryptionFailure() || !validId(event.getSender()) || !Number.isFinite(event.getTs())
      || typeof event.getContent().body !== 'string' || 'm.new_content' in event.getContent()) throw unavailable();
    const relation = event.getOriginalContent()['m.relates_to'];
    if (relation != null && (typeof relation !== 'object' || Array.isArray(relation) || relation.rel_type !== 'm.thread'
      || root || !validId(relation.event_id) || relation.event_id === id)) throw unavailable();
    return relation?.event_id as string | undefined;
  };
  const event = await secureMessaging.getDecryptedEvent(channelId, eventId); current();
  const parentId = verify(event, eventId);
  let parent: MatrixEvent | undefined;
  if (parentId) { parent = await secureMessaging.getDecryptedEvent(channelId, parentId); current(); verify(parent, parentId, true); }
  // Even a cached Matrix timeline must not outlive current product membership.
  await refreshBindings(); current();
  if (verify(event, eventId) !== parentId) throw unavailable();
  if (parent && parentId) verify(parent, parentId, true);
  return asMessage(channelId, event);
}

async function messageRecipients(channelId: string, content: string, references: MessageReference[], mentions: string[]) {
  const token = configuration?.getAccessToken(), owner = configuration?.getUserId();
  const prepared = await prepareMessageReferences(content, references, mentions, async identities => {
    const fileIds = identities.filter(identity => identity.kind === 'file').map(identity => identity.id);
    const other = identities.filter(identity => identity.kind !== 'file');
    const resolved = other.length ? await request<ReferenceResolution>('/mentions/channels/' + encodeURIComponent(channelId) + '/references/resolve', { references: other }) : { items: [] };
    // Secure files have no native index. Verify their genuine encrypted events
    // locally instead of claiming that a server can validate their contents.
    if (fileIds.length) {
      const available = await (await import('./files')).verifiedSecureFileReferenceIds(channelId, fileIds);
      resolved.items.push(...fileIds.map(id => ({ kind: 'file' as const, id, available: available.has(id) })));
    }
    return resolved;
  });
  if (!token || token !== configuration?.getAccessToken() || owner !== configuration?.getUserId()) throw new Error('The signed-in account changed.');
  const c = await ensureSession();
  if (token !== configuration?.getAccessToken() || owner !== configuration?.getUserId()) throw new Error('The signed-in account changed.');
  const domain = c.getUserId()!.split(':').slice(1).join(':');
  return { ...prepared, matrixUserIds: prepared.mentions.map(id => id.startsWith('@') ? id : '@fc_' + id + ':' + domain) };
}
async function sendMessage(channelId: string, content: string, parentId?: string, mentions: string[] = [], references: MessageReference[] = [], transactionId?: string): Promise<Message> {
  const prepared = await messageRecipients(channelId, content, references, mentions);
  const result = await sendEvent(channelId, EventType.RoomMessage, encryptedMessageContent(content, prepared.mentions, prepared.references, parentId, prepared.matrixUserIds), transactionId);
  void queueNotice({ channelId, eventId: result.event_id, mentions: prepared.mentions, parentId }).catch(() => undefined);
  return { id: result.event_id, channel_id: channelId, author_id: configuration!.getUserId()!, content, references: prepared.references, mentions: prepared.mentions, encrypted: true, created_at: new Date().toISOString(), ...(parentId ? { parent_id: parentId } : {}) };
}
async function getReactions(channelId: string, messageId: string): Promise<Reaction[]> {
  const rows = (await events(channelId)).filter(e => e.getType() === REACTION && !e.isRedacted() && e.getContent().event_id === messageId), grouped = new Map<string, Set<string>>();
  for (const e of rows) { const emoji = e.getContent().emoji; if (typeof emoji !== 'string') continue; const users = grouped.get(emoji) || new Set<string>(); users.add(accountId(e.getSender()!)); grouped.set(emoji, users); }
  return [...grouped].map(([emoji, users]) => ({ emoji, count: users.size, users: [...users], user_reacted: users.has(configuration!.getUserId()!) }));
}
type RecoveryDraft = Awaited<ReturnType<NonNullable<ReturnType<MatrixClient['getCrypto']>>['createRecoveryKeyFromPassphrase']>>;
let pendingRecovery: { owner: string; generated: RecoveryDraft; backupCreated: boolean } | null = null;
const backupRemaining = new WeakMap<MatrixClient, number>();
const recoverySnapshots = new WeakSet<MatrixClient>();
async function hydrateRecoveryMetadata(c: MatrixClient) {
  if (recoverySnapshots.has(c)) return;
  const token = ownerToken;
  // PREPARED can expose a partial account-data snapshot. The SDK then reads
  // account data only from its cache. Obtain a complete encrypted-secret snapshot
  // before deciding that recovery is absent; no private keys enter this request.
  const read = async (type: string) => {
    try {
      return await c.http.authedRequest<Record<string,unknown>>(Method.Get,
        '/user/' + encodeURIComponent(c.getUserId()!) + '/account_data/' + encodeURIComponent(type));
    } catch (error) {
      if (error && typeof error === 'object' && 'errcode' in error && error.errcode === 'M_NOT_FOUND') return null;
      throw error;
    }
  };
  const defaultKey = await read('m.secret_storage.default_key');
  const types = ['m.cross_signing.master','m.cross_signing.self_signing','m.cross_signing.user_signing','m.megolm_backup.v1'];
  if (typeof defaultKey?.key === 'string') types.push('m.secret_storage.key.' + defaultKey.key);
  else if (defaultKey && Object.keys(defaultKey).length) throw new Error('Invalid encryption recovery metadata.');
  const rows = await Promise.all(types.map(async type => ({type,content:await read(type)})));
  if (client !== c || token !== configuration?.getAccessToken()) throw new Error('The signed-in account changed.');
  const events = rows.filter((row): row is {type:string;content:Record<string,unknown>} => row.content !== null);
  if (defaultKey) events.push({type:'m.secret_storage.default_key',content:defaultKey});
  c.store.storeAccountDataEvents(events.map(c.getEventMapper()));
  recoverySnapshots.add(c);
}
async function setupRecovery(): Promise<string> {
  const c = await ensureSession(); await hydrateRecoveryMetadata(c);
  const crypt = c.getCrypto()!, owner = c.getUserId()!, attempt = generation;
  if (!pendingRecovery || pendingRecovery.owner !== owner) {
    // Existing SSSS must never be reset merely because this device has not yet
    // downloaded the public cross-signing identity or unlocked private secrets.
    if (await c.secretStorage.getKey()) throw new Error('Recovery already exists. Unlock it with your existing key.');
    pendingRecovery = { owner, generated: await crypt.createRecoveryKeyFromPassphrase(), backupCreated: false };
  }
  const draft = pendingRecovery;
  secretStorageKey = new Uint8Array(draft.generated.privateKey);
  const stillCurrent = () => { if (attempt !== generation || c !== client) throw new Error("The signed-in account changed."); };
  let complete = draft.backupCreated && backupRemaining.get(c) === 0;
  let resolveBackup: () => void = () => undefined;
  const backupComplete = new Promise<void>(resolve => { resolveBackup = resolve; });
  const onRemaining = (remaining: number) => { if (remaining === 0) { complete = true; resolveBackup(); } };
  c.on(CryptoEvent.KeyBackupSessionsRemaining, onRemaining);
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    await crypt.userHasCrossSigningKeys(owner, true); stillCurrent();
    await crypt.bootstrapCrossSigning({ authUploadDeviceSigningKeys: makeRequest => makeRequest({}) }); stillCurrent();
    await crypt.bootstrapSecretStorage({ setupNewKeyBackup: !draft.backupCreated, createSecretStorageKey: async () => draft.generated }); stillCurrent();
    draft.backupCreated = true;
    // Keep the original device running until the initial encrypted backup ACKs.
    // Stopping immediately can otherwise cancel the SDK's randomized upload loop.
    if (!complete) await Promise.race([backupComplete, new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error('Encrypted backup is still uploading. Keep this page open and retry recovery setup.')), 45000); })]);
    if (attempt !== generation || c !== client) throw new Error('The signed-in account changed.');
    const encoded = draft.generated.encodedPrivateKey!;
    pendingRecovery = null;
    return encoded;
  } finally { clearTimeout(timer); c.off(CryptoEvent.KeyBackupSessionsRemaining, onRemaining); if (pendingRecovery !== draft) draft.generated.privateKey.fill(0); }
}

async function unlockRecovery(encoded: string) {
  const c = await ensureSession(); await hydrateRecoveryMetadata(c);
  const decoded = decodeRecoveryKey(encoded), key = await c.secretStorage.getKey();
  if (!key) { decoded.fill(0); throw new Error('Recovery metadata is not available after initial sync.'); }
  if (!await c.secretStorage.checkKey(decoded, key[1])) { decoded.fill(0); throw new Error('The recovery key does not match this account.'); }
  secretStorageKey?.fill(0); secretStorageKey = decoded;
  if (!await c.getCrypto()!.userHasCrossSigningKeys(c.getUserId()!, true)) throw new Error("Public recovery identity is unavailable; retry after synchronization.");
  await c.getCrypto()!.bootstrapCrossSigning({ authUploadDeviceSigningKeys: makeRequest => makeRequest({}) }); await c.getCrypto()!.loadSessionBackupPrivateKeyFromSecretStorage(); await c.getCrypto()!.checkKeyBackupAndEnable(); await c.getCrypto()!.restoreKeyBackup();
}
async function getDevices(channelId?: string): Promise<SecureDevice[]> {
  const c = await ensureSession(); let users = [c.getUserId()!]; if (channelId) { const { room } = await roomFor(channelId); await room.loadMembersIfNeeded(); users = room.getJoinedMembers().map(m => m.userId).filter(id => id.startsWith('@fc_')); }
  await refreshRevocations(channelId);
  const info = await c.getCrypto()!.getUserDeviceInfo(users, true), result: SecureDevice[] = [];
  for (const [user, devices] of info) for (const [id, device] of devices) { if (revokedDevices.has(deviceKey(user, id))) continue; const trust = await c.getCrypto()!.getDeviceVerificationStatus(user, id); result.push({ user_id: accountId(user), matrix_user_id: user, device_id: id, fingerprint: device.getFingerprint()!, verified: !!trust?.isVerified(), name: device.displayName }); } return result;
}
export const secureMessaging = {
  configure: configureSecureMessaging,
  ensureSession, refreshBindings, getMessages, getRecentMessages, getMessage, getReadReceipts, sendMessage, getReactions, setupRecovery, unlockRecovery, getDevices, events, sendEvent, roomFor,
  async revokeDevice(deviceId: string, password: string, code?: string, managed = false): Promise<{ revoked: true; current: boolean }> {
    const c = await ensureSession();
    const result = await request<{ revoked: true; current: boolean }>('/secure/devices/' + encodeURIComponent(deviceId) + (managed ? '/revoke-managed' : '/revoke'), managed ? { confirmed: true } : { password, ...(code ? { code } : {}) });
    if (result.current) return result;
    // A confirmed remote deletion remains successful if refresh is temporarily
    // offline. The mandatory fresh snapshot/cache guard fences subsequent sends.
    try {
      await refreshRevocations();
      await c.getCrypto()!.userHasCrossSigningKeys(c.getUserId()!, true);
      for (const binding of bindings.values()) if (binding.room_id && c.getRoom(binding.room_id)?.getMyMembership() === 'join') await c.getCrypto()!.forceDiscardSession(binding.room_id);
    } catch { /* Normal encrypted sync and the next send refresh recipient state. */ }
    return result;
  },
  subscribe(listener: Listener) { listeners.add(listener); return () => { listeners.delete(listener); }; },
  handles: handlesChannel,
  async enableChannel(channelId: string, title: string) { const binding = await request<SecureBinding>('/secure/channels/' + encodeURIComponent(channelId) + '/enable', {}); bindings.set(channelId, binding); bindingsLoaded = true; await sendEvent(channelId, METADATA, { name: title }); names.set(channelId, title); return binding; },
  getChannelName(channelId: string) { return names.get(channelId); },
  getChannelMetadata: readChannelMetadata,
  async getChannelTitle(channelId: string) { return (await readChannelMetadata(channelId)).name; },
  setChannelMetadata: writeChannelMetadata,
  async setChannelName(channelId: string, name: string) { const current = await readChannelMetadata(channelId); return writeChannelMetadata(channelId, name, current.description, current.version); },
  async editMessage(channelId: string, messageId: string, content: string, references: MessageReference[] = []) {
    const prepared = await messageRecipients(channelId, content, references, []);
    const next = encryptedMessageContent(content, prepared.mentions, prepared.references, undefined, prepared.matrixUserIds);
    const result = await sendEvent(channelId, EventType.RoomMessage, { msgtype: MsgType.Text, body: '* ' + content, 'm.new_content': next, 'm.relates_to': { rel_type: 'm.replace', event_id: messageId } });
    void queueNotice({ channelId, eventId: result.event_id, mentions: prepared.mentions }).catch(() => undefined);
    return { references: prepared.references, mentions: prepared.mentions };
  },
  async deleteMessage(channelId: string, messageId: string) { const { c, roomId } = await roomFor(channelId, true); await c.redactEvent(roomId, messageId); },
  async getThread(channelId: string, parentId: string) {
    const { c, room } = await roomFor(channelId);
    while (room.oldState.paginationToken) {
      const previous = room.oldState.paginationToken;
      await c.scrollback(room, 100);
      if (previous === room.oldState.paginationToken) break;
    }
    const rows = await events(channelId), edits = new Map<string, MatrixEvent>();
    for (const event of rows) { const relation = event.getOriginalContent()['m.relates_to']; if (event.getType() === EventType.RoomMessage && relation?.rel_type === 'm.replace' && relation.event_id) edits.set(relation.event_id, event); }
    return rows.filter(event => event.getType() === EventType.RoomMessage && event.getOriginalContent()['m.relates_to']?.rel_type === 'm.thread' && event.getOriginalContent()['m.relates_to']?.event_id === parentId).map(event => {
      const message = asMessage(channelId, event), edit = edits.get(message.id);
      if (edit && edit.getSender() === event.getSender()) { const replacement = asMessage(channelId, edit); message.content = replacement.content; message.references = replacement.references; message.mentions = replacement.mentions; message.edited_at = new Date(edit.getTs()).toISOString(); }
      return message;
    });
  },
  async addReaction(channelId: string, messageId: string, emoji: string) { await sendEvent(channelId, REACTION, { event_id: messageId, emoji }); return getReactions(channelId, messageId); },
  async removeReaction(channelId: string, messageId: string, emoji: string) { const { c, roomId } = await roomFor(channelId, true); for (const e of await events(channelId)) if (e.getType() === REACTION && e.getSender() === c.getUserId() && e.getContent().event_id === messageId && e.getContent().emoji === emoji && !e.isRedacted()) await c.redactEvent(roomId, e.getId()!); return getReactions(channelId, messageId); },
  async markRead(channelId: string, messageId: string) { await sendEvent(channelId, RECEIPT, { event_id: messageId }); },
  async verifyDevice(device: SecureDevice, fingerprint: string) { const c = await ensureSession(); await refreshRevocations(); if (revokedDevices.has(deviceKey(device.matrix_user_id, device.device_id))) throw new Error('This encryption device was removed.'); const info = await c.getCrypto()!.getUserDeviceInfo([device.matrix_user_id], true), current = info.get(device.matrix_user_id)?.get(device.device_id); if (!current || current.getFingerprint() !== fingerprint.replace(/\s/g, '')) throw new Error('Fingerprint mismatch. Verify it through a separate trusted channel.'); await c.getCrypto()!.setDeviceVerified(device.matrix_user_id, device.device_id, true); for (const [channelId, title] of names) { const binding = bindings.get(channelId); if (binding?.room_id && c.getRoom(binding.room_id)?.getMember(device.matrix_user_id)?.membership === 'join') await sendEvent(channelId, METADATA, { name: title }); } },
  async recoveryStatus() { const c = await ensureSession(); await hydrateRecoveryMetadata(c); return { ready: !!await c.secretStorage.getKey() && !pendingRecovery, unlocked: !!secretStorageKey && !pendingRecovery, device_id: c.getDeviceId(), fingerprint: (await c.getCrypto()!.getOwnDeviceKeys()).ed25519 }; },
  async getDecryptedEvent(channelId: string, eventId: string) { const { c, room } = await roomFor(channelId), timeline = await c.getEventTimeline(room.getUnfilteredTimelineSet(), eventId), e = timeline?.getEvents().find(item => item.getId() === eventId); if (!e || !e.isEncrypted()) throw new Error('Encrypted event not found.'); await c.decryptEventIfNeeded(e); if (e.isDecryptionFailure()) throw new Error('Verify this device or unlock recovery to read this event.'); return e; },
  stop() { clearTimeout(noticeTimer); noticeTimer = undefined; notices.clear(); noticesOwner = null; pendingRecovery = null; generation++; if (client) { transactionEpochs.delete(client); publicationGuards.get(client)?.stop(); } client?.stopClient(); client = null; starting = null; ownerToken = null; bindingsLoaded = false; bindings.clear(); names.clear(); channelDetails.clear(); legacyMetadataOrder.clear(); revokedDevices.clear(); secretStorageKey?.fill(0); secretStorageKey = null; },
};
