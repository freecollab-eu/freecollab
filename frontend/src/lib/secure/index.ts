import { createClient, ClientEvent, RoomEvent, MatrixEventEvent, EventType, MsgType, Method, type MatrixClient, MatrixEvent } from 'matrix-js-sdk';
import { CryptoEvent } from 'matrix-js-sdk/lib/crypto-api/CryptoEvent';
import { decodeRecoveryKey } from 'matrix-js-sdk/lib/crypto-api/recovery-key';
import { storageKeyFor, loadPendingNotices, savePendingNotices } from './device-store';
import { APIError, type Message, type Reaction, type ReadReceipt } from '../api';
const quietLogger = { trace() {}, debug() {}, info() {}, warn() {}, error() {}, getChild() { return this; } };
interface Configuration { baseUrl: string; getAccessToken: () => string | null; getUserId: () => string | null }
export interface SecureBinding { channel_id: string; room_id: string | null; status: string }
export interface SecureDevice { user_id: string; matrix_user_id: string; device_id: string; fingerprint: string; verified: boolean; name?: string }
type Listener = (event: string, payload: Record<string, unknown>) => void;
const METADATA = 'eu.freecollab.channel.metadata', REACTION = 'eu.freecollab.reaction', RECEIPT = 'eu.freecollab.receipt';
let configuration: Configuration | null = null, client: MatrixClient | null = null, starting: Promise<MatrixClient> | null = null, ownerToken: string | null = null;
let bindings = new Map<string, SecureBinding>(), bindingsLoaded = false;
const listeners = new Set<Listener>(), names = new Map<string, string>();
let generation = 0;
const transactionEpochs = new Map<string, string>();
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
const accountId = (id: string) => /^@fc_([^:]+):/.exec(id)?.[1] || id;
const emit = (event: string, payload: Record<string, unknown>) => listeners.forEach(fn => fn(event, payload));
export function configureSecureMessaging(next: Configuration) { configuration = next; }
async function request<T>(path: string, body?: unknown): Promise<T> {
  if (!configuration?.getAccessToken()) throw new Error('Sign in to unlock encrypted channels.');
  const requestToken = configuration.getAccessToken();
  const response = await fetch(configuration.baseUrl + path, { method: body === undefined ? 'GET' : 'POST', headers: { Authorization: 'Bearer ' + requestToken, 'Content-Type': 'application/json' }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  if (!response.ok) { const data = await response.json().catch(() => null); if (response.status === 401 && requestToken === configuration?.getAccessToken()) window.dispatchEvent(new Event('freecollab:session-expired')); throw new APIError(typeof data?.detail === 'string' ? data.detail : typeof data?.detail?.message === 'string' ? data.detail.message : 'Encrypted channel is unavailable.', response.status, typeof data?.detail?.code === 'string' ? data.detail.code : undefined); }
  if (requestToken !== configuration?.getAccessToken()) throw new Error('The signed-in account changed.');
  return response.json();
}
function asMessage(channelId: string, e: MatrixEvent): Message {
  const content = e.getContent(), relation = e.getOriginalContent()['m.relates_to'];
  return { id: e.getId()!, channel_id: channelId, author_id: accountId(e.getSender()!), content: e.isDecryptionFailure() ? 'Encrypted content is unavailable on this device. Verify the device or unlock recovery.' : typeof content.body === 'string' ? content.body : '', encrypted: true, created_at: new Date(e.getTs()).toISOString(), deleted: e.isRedacted(), ...(e.isDecryptionFailure() ? { locked: true } : {}), ...(relation?.rel_type === 'm.thread' ? { parent_id: relation.event_id } : {}), mentions: Array.isArray(content['eu.freecollab.mentions']) ? content['eu.freecollab.mentions'] : [] };
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
    if (e.getType() === METADATA && typeof content.name === 'string' && await validMetadataSender(binding.channel_id, e)) { names.set(binding.channel_id, content.name); emit('secure_metadata', { channel_id: binding.channel_id, name: content.name }); }
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
async function refreshBindings() { const rows = await request<SecureBinding[]>('/secure/channels'); bindings = new Map(rows.map(row => [row.channel_id, row])); bindingsLoaded = true; return rows; }
async function ensureSession(): Promise<MatrixClient> {
  const token = configuration?.getAccessToken(); if (!token) throw new Error('Sign in to unlock encrypted channels.');
  if (ownerToken !== token) { generation++; client?.stopClient(); client = null; starting = null; bindings.clear(); bindingsLoaded = false; names.clear(); revokedDevices.clear(); secretStorageKey?.fill(0); secretStorageKey = null; ownerToken = token; }
  if (client) return client; if (starting) return starting;
  const attempt = generation;
  const assertCurrent = (c?: MatrixClient) => { if (generation !== attempt || configuration?.getAccessToken() !== token) { c?.stopClient(); throw new Error("The signed-in account changed."); } };
  starting = (async () => {
    const auth = await request<{ user_id: string; access_token: string; device_id: string }>('/secure/session', {}); assertCurrent();
    const c = createClient({ fetchFn: (input, init) => {
      const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url);
      const match = /\/send\/m\.room\.encrypted\/([^/]+)$/.exec(url.pathname);
      if (!match) return fetch(input, init);
      const epoch = transactionEpochs.get(decodeURIComponent(match[1]));
      const headers = new Headers(init?.headers); if (epoch) headers.set('X-FreeCollab-Membership-Epoch', epoch);
      return fetch(input, { ...init, headers });
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
  const c = await ensureSession(); if (!bindingsLoaded || !bindings.has(channelId)) await refreshBindings();
  const binding = bindings.get(channelId); if (!binding?.room_id || binding.status !== 'active') throw new Error('This encrypted channel is not ready.');
  let room = c.getRoom(binding.room_id);
  const joining = !room || room.getMyMembership() !== 'join';
  // A newly granted Connect member has a product seat before Matrix invites
  // exist. Synchronize that current authorized seat before the first read/join.
  const snapshot = writing || joining ? await request<{ membership_epoch: string; revoked_devices: RevokedDevice[] }>('/secure/channels/' + encodeURIComponent(channelId) + '/sync', {}) : undefined;
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
  return { c, room, roomId: binding.room_id, membershipEpoch: snapshot?.membership_epoch };
}
async function events(channelId: string, paginate = false) {
  const { c, room } = await roomFor(channelId); if (paginate && room.oldState.paginationToken) await c.scrollback(room, 100);
  const rows = room.getLiveTimeline().getEvents(); await Promise.all(rows.filter(e => e.isEncrypted()).map(e => c.decryptEventIfNeeded(e).catch(() => undefined))); return rows.filter(e => e.isEncrypted());
}
async function requireVerifiedRecipients(channelId: string, room: ReturnType<MatrixClient['getRoom']>) {
  const members = await request<{ user_id: string; name: string }[]>('/channels/' + encodeURIComponent(channelId) + '/members');
  const devices = await getDevices(channelId);
  for (const member of members) {
    if (member.user_id === configuration?.getUserId()) continue;
    const known = devices.filter(device => device.user_id === member.user_id);
    if (!known.some(device => device.verified) || !known.some(device => room?.getMember(device.matrix_user_id)?.membership === 'join'))
      throw new Error('Verify a joined device for ' + member.name + ' before sending encrypted content.');
  }
}
async function sendEvent(channelId: string, type: string, content: Record<string, unknown>, transactionId?: string) {
  const { c, room, roomId, membershipEpoch } = await roomFor(channelId, true);
  if (type !== METADATA) await requireVerifiedRecipients(channelId, room);
  const transaction = transactionId || c.makeTxnId();
  if (!membershipEpoch) throw new Error('Encrypted membership snapshot is unavailable.');
  transactionEpochs.set(transaction, membershipEpoch);
  try { return await c.sendEvent(roomId, type as never, content as never, transaction); }
  finally { transactionEpochs.delete(transaction); }
}

async function getMessages(channelId: string, before?: string, limit = 100): Promise<Message[]> {
  const { c, room } = await roomFor(channelId);
  const boundedLimit = Math.max(1, Math.min(limit, 200));
  let rows = await events(channelId);
  const messageEvents = () => rows.filter(e => (e.getType() === EventType.RoomMessage || e.isDecryptionFailure()) && e.getOriginalContent()['m.relates_to']?.rel_type !== 'm.replace');
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
    if (edit && edit.getSender() === e.getSender()) { item.content = edit.getContent()['m.new_content']?.body || ''; item.edited_at = new Date(edit.getTs()).toISOString(); }
    item.thread_count = rows.filter(reply => reply.getOriginalContent()['m.relates_to']?.rel_type === 'm.thread' && reply.getOriginalContent()['m.relates_to']?.event_id === item.id).length;
    return item;
  });
}

async function sendMessage(channelId: string, content: string, parentId?: string, mentions: string[] = []): Promise<Message> {
  const result = await sendEvent(channelId, EventType.RoomMessage, { msgtype: MsgType.Text, body: content, 'eu.freecollab.mentions': mentions, ...(parentId ? { 'm.relates_to': { rel_type: 'm.thread', event_id: parentId, is_falling_back: true, 'm.in_reply_to': { event_id: parentId } } } : {}) });
  void queueNotice({ channelId, eventId: result.event_id, mentions, parentId }).catch(() => undefined);
  return { id: result.event_id, channel_id: channelId, author_id: configuration!.getUserId()!, content, encrypted: true, created_at: new Date().toISOString(), ...(parentId ? { parent_id: parentId } : {}) };
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
  ensureSession, refreshBindings, getMessages, sendMessage, getReactions, setupRecovery, unlockRecovery, getDevices, events, sendEvent, roomFor,
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
  async handles(channelId: string) { await refreshBindings(); return bindings.has(channelId); },
  async enableChannel(channelId: string, title: string) { const binding = await request<SecureBinding>('/secure/channels/' + encodeURIComponent(channelId) + '/enable', {}); bindings.set(channelId, binding); bindingsLoaded = true; await sendEvent(channelId, METADATA, { name: title }); names.set(channelId, title); return binding; },
  getChannelName(channelId: string) { return names.get(channelId); },
  async getChannelTitle(channelId: string) {
    const { c, room } = await roomFor(channelId);
    for (;;) {
      const rows = await events(channelId);
      for (const event of [...rows].reverse()) if (event.getType() === METADATA && typeof event.getContent().name === 'string' && event.getContent().name.length <= 100 && await validMetadataSender(channelId, event)) { names.set(channelId, event.getContent().name); return event.getContent().name as string; }
      const previous = room.oldState.paginationToken;
      if (!previous) break;
      await c.scrollback(room, 100);
      if (previous === room.oldState.paginationToken) break;
    }
    return names.get(channelId) || 'Encrypted channel';
  },
  async setChannelName(channelId: string, name: string) { await sendEvent(channelId, METADATA, { name }); },
  async editMessage(channelId: string, messageId: string, content: string) { await sendEvent(channelId, EventType.RoomMessage, { msgtype: MsgType.Text, body: '* ' + content, 'm.new_content': { msgtype: MsgType.Text, body: content }, 'm.relates_to': { rel_type: 'm.replace', event_id: messageId } }); },
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
      if (edit && edit.getSender() === event.getSender()) { message.content = edit.getContent()['m.new_content']?.body || ''; message.edited_at = new Date(edit.getTs()).toISOString(); }
      return message;
    });
  },
  async addReaction(channelId: string, messageId: string, emoji: string) { await sendEvent(channelId, REACTION, { event_id: messageId, emoji }); return getReactions(channelId, messageId); },
  async removeReaction(channelId: string, messageId: string, emoji: string) { const { c, roomId } = await roomFor(channelId, true); for (const e of await events(channelId)) if (e.getType() === REACTION && e.getSender() === c.getUserId() && e.getContent().event_id === messageId && e.getContent().emoji === emoji && !e.isRedacted()) await c.redactEvent(roomId, e.getId()!); return getReactions(channelId, messageId); },
  async markRead(channelId: string, messageId: string) { await sendEvent(channelId, RECEIPT, { event_id: messageId }); },
  async getReadReceipts(channelId: string): Promise<ReadReceipt[]> { const latest = new Map<string, ReadReceipt>(); for (const e of await events(channelId)) if (e.getType() === RECEIPT) latest.set(e.getSender()!, { user_id: accountId(e.getSender()!), last_read_message_id: e.getContent().event_id, read_at: new Date(e.getTs()).toISOString() }); return [...latest.values()]; },
  async verifyDevice(device: SecureDevice, fingerprint: string) { const c = await ensureSession(); await refreshRevocations(); if (revokedDevices.has(deviceKey(device.matrix_user_id, device.device_id))) throw new Error('This encryption device was removed.'); const info = await c.getCrypto()!.getUserDeviceInfo([device.matrix_user_id], true), current = info.get(device.matrix_user_id)?.get(device.device_id); if (!current || current.getFingerprint() !== fingerprint.replace(/\s/g, '')) throw new Error('Fingerprint mismatch. Verify it through a separate trusted channel.'); await c.getCrypto()!.setDeviceVerified(device.matrix_user_id, device.device_id, true); for (const [channelId, title] of names) { const binding = bindings.get(channelId); if (binding?.room_id && c.getRoom(binding.room_id)?.getMember(device.matrix_user_id)?.membership === 'join') await sendEvent(channelId, METADATA, { name: title }); } },
  async recoveryStatus() { const c = await ensureSession(); await hydrateRecoveryMetadata(c); return { ready: !!await c.secretStorage.getKey() && !pendingRecovery, unlocked: !!secretStorageKey && !pendingRecovery, device_id: c.getDeviceId(), fingerprint: (await c.getCrypto()!.getOwnDeviceKeys()).ed25519 }; },
  async getDecryptedEvent(channelId: string, eventId: string) { const { c, room } = await roomFor(channelId), timeline = await c.getEventTimeline(room.getUnfilteredTimelineSet(), eventId), e = timeline?.getEvents().find(item => item.getId() === eventId); if (!e || !e.isEncrypted()) throw new Error('Encrypted event not found.'); await c.decryptEventIfNeeded(e); if (e.isDecryptionFailure()) throw new Error('Verify this device or unlock recovery to read this event.'); return e; },
  stop() { clearTimeout(noticeTimer); noticeTimer = undefined; notices.clear(); noticesOwner = null; pendingRecovery = null; generation++; client?.stopClient(); client = null; starting = null; ownerToken = null; bindingsLoaded = false; bindings.clear(); names.clear(); revokedDevices.clear(); transactionEpochs.clear(); secretStorageKey?.fill(0); secretStorageKey = null; },
};
