import * as Y from 'yjs';
import { MatrixEvent, type MatrixClient } from 'matrix-js-sdk';
import { api } from './api';
import { getSecureMessaging } from './secure-client';
import { authorNamespace, matrixIdentity, parseRevision, reduceWorkRevisions, resourceIdValid, validateSharedFields, WORK_DOCUMENT_PART, WORK_DOCUMENT_UPDATE, WORK_PROTOCOL, WORK_PROTOCOL_V2, WORK_REVISION, type SharedDocumentFields, type SharedTaskFields, type SharedWorkFields, type SharedWorkKind, type VerifiedWorkEvent, type WorkContext, type WorkRecord, type WorkRevision } from './federated-work-model';
import { decodeWorkUpdate, encodeWorkUpdate, replaceWorkText, setWorkDocumentReferences, validateWorkDocument, type FederatedDocumentState } from './federated-work-document';
import { addedEncryptedReferences, encryptedReferenceTexts, rebaseEncryptedReferences, validateEncryptedReferences, type EncryptedFieldReferences } from './encrypted-work-references';
import { pendingWorkPointers, pointerSessionDigest, removeWorkPointer, storeWorkPointer, type PendingWorkPointer } from './federated-work-pointers';
import { acceptableWorkEncryptionOrigin } from './federated-work-trust';
import { ENCRYPTED_WORK_PROTOCOL, ENCRYPTED_WORK_REVISION, parseEncryptedRevision, reduceEncryptedRevisions, type EncryptedWorkRecord, type EncryptedWorkRevision } from './encrypted-work-model';

export * from './federated-work-model';
export type { FederatedDocumentState } from './federated-work-document';
interface Pointer { sequence: number; matrix_event_id: string; matrix_user_id: string }
interface PointerPage { connection_id: string; status: string; items: Pointer[]; next_after: number; has_more: boolean }
interface IndexPage { connection_id: string; local: PointerPage; remote: PointerPage }
interface DocumentUpdate { id: string; resource_id: string; root_event_id: string; sender: string; update_base64?: string; parts?: string[]; byte_size?: number; state_hash?: string }
interface Dependencies {
  request?: <T>(path: string, options?: RequestInit) => Promise<T>;
  token?: () => string | null;
  secure?: typeof getSecureMessaging;
  reconstruct?: (initial: string, updates: Array<{ id: string; update_base64: string }>) => Promise<FederatedDocumentState>;
}
export interface SharedWorkSnapshot { context: WorkContext; records: WorkRecord[]; reference_records?:EncryptedWorkRecord[]; warnings: string[]; pending_pointers: number; quarantined_event_ids: string[] }
export class CandidateFault extends Error {}
class SharedWorkDraftError extends Error {}
export class FederatedWorkResourceError extends Error { constructor(readonly resourceId: string, message: string) { super(message); this.name = 'FederatedWorkResourceError'; } }
const base = (channel: string) => '/connect/federation/channels/' + encodeURIComponent(channel);
const MAX_EVENTS = 4000, MAX_PLAIN_BYTES = 8 * 1024 * 1024;
const MAX_PART = 16384;
const strict = (value: Record<string, unknown>, keys: string[]) => { if (Object.keys(value).some(key => !keys.includes(key))) throw new Error('Unsupported encrypted document fields.'); };
const hash = async (bytes: Uint8Array) => [...new Uint8Array(await crypto.subtle.digest('SHA-256', new Uint8Array(bytes).buffer))].map(value => value.toString(16).padStart(2, '0')).join('');
function reconstructInWorker(initial: string, updates: Array<{ id: string; update_base64: string }>): Promise<FederatedDocumentState> {
  return new Promise((resolve, reject) => {
    const worker = new Worker(new URL('./federated-work-document.worker.ts', import.meta.url), { type: 'module' });
    let timer = setTimeout(() => { worker.terminate(); reject(new Error('The shared document worker could not load.')); }, 10000);
    const finish = () => { clearTimeout(timer); worker.terminate(); };
    worker.onmessage = event => {
      if (event.data.ready) { clearTimeout(timer); timer = setTimeout(() => { worker.terminate(); reject(new Error('Encrypted document reconstruction exceeded its device processing limit.')); }, 1500); worker.postMessage({ initial, updates }); return; }
      finish(); if (event.data.error) reject(new Error(event.data.error)); else resolve(event.data.state as FederatedDocumentState);
    };
    worker.onerror = () => { finish(); reject(new Error('Encrypted document reconstruction could not run on this device.')); };
  });
}
export function validateContext(value: WorkContext & {mode?: string}, channelId: string) {
  if (value.protocol !== WORK_PROTOCOL || !/^[a-f0-9]{32}$/.test(value.connection_id) || value.channel_id !== channelId || typeof value.room_id !== 'string' || !value.room_id.startsWith('!') || !matrixIdentity(value.matrix_user_id) || value.matrix_user_id.split(':', 2)[1] !== value.local_server || typeof value.peer_server !== 'string' || (value.mode === 'local' ? value.peer_server !== value.local_server : value.peer_server === value.local_server) || !Array.isArray(value.members) || value.members.length > 200 || value.members.some(member => !matrixIdentity(member.matrix_user_id) || ![value.local_server, value.peer_server].includes(member.matrix_user_id.split(':', 2)[1]) || typeof member.name !== 'string' || member.name.length > 200) || !value.members.some(member => member.matrix_user_id === value.matrix_user_id)) throw new Error('The current encrypted shared-work scope could not be verified.');
  return value;
}
export async function trustedEvent(c: MatrixClient, context: WorkContext, pointer: Pointer, revoked: Set<string>): Promise<VerifiedWorkEvent> {
  const raw = await c.fetchRoomEvent(context.room_id, pointer.matrix_event_id).catch(error => { if (error?.httpStatus === 404) throw new CandidateFault('The indexed event is unavailable.'); throw error; }), event = new MatrixEvent(raw);
  await c.decryptEventIfNeeded(event).catch(() => { throw new CandidateFault('The indexed event could not be decrypted.'); });
  const sender = event.getSender();
  if (!event.isEncrypted() || event.isRedacted() || event.isDecryptionFailure() || event.getRoomId() !== context.room_id || event.getId() !== pointer.matrix_event_id || sender !== pointer.matrix_user_id || !matrixIdentity(sender) || ![context.local_server, context.peer_server].includes(sender.split(':', 2)[1])) throw new CandidateFault('A shared-work event failed its room, sender or encrypted-content check.');
  const crypt = c.getCrypto();
  if (!crypt) throw new Error('A trusted encryption device is required.');
  const encryption = await crypt.getEncryptionInfoForEvent(event);
  // Manual fingerprint verification is supported without cross-signing. The
  // SDK may still flag an unsigned/un-cross-signed identity; accept that only
  // after matching this event to an exact current verified device below.
  // Forwarded/unauthentic keys, changed identities and sender mismatches remain
  // rejected, independently of a cached manual device verification flag.
  if (!acceptableWorkEncryptionOrigin(encryption)) throw new CandidateFault('A shared-work event has an untrusted encryption origin.');
  const curve = event.getSenderKey(), ed = event.getClaimedEd25519Key();
  let trusted = false, verifiedDevice: string | undefined;
  if (sender === c.getUserId() && !revoked.has(sender + '\0' + c.getDeviceId())) { const own = await crypt.getOwnDeviceKeys(); trusted = own.curve25519 === curve && own.ed25519 === ed; if (trusted) verifiedDevice = c.getDeviceId() || undefined; }
  if (!trusted) {
    const devices = await crypt.getUserDeviceInfo([sender], true);
    for (const [id, device] of devices.get(sender) || []) if (!revoked.has(sender + '\0' + id) && device.getIdentityKey() === curve && device.getFingerprint() === ed && (await crypt.getDeviceVerificationStatus(sender, id))?.isVerified()) {
      if (verifiedDevice && verifiedDevice !== id) throw new CandidateFault('This encrypted event has an ambiguous verified device identity.');
      trusted = true; verifiedDevice = id;
    }
  }
  if (!trusted) throw new CandidateFault('Verify the author’s exact encryption device before applying shared work.');
  const content = event.getContent();
  if (!content || typeof content !== 'object' || Array.isArray(content) || new TextEncoder().encode(JSON.stringify(content)).length > 48 * 1024) throw new CandidateFault('A shared-work event exceeds its content bound.');
  return { event_id: pointer.matrix_event_id, sender, sender_device_id: verifiedDevice, timestamp: event.getTs(), type: event.getType(), content };
}

export class FederatedWorkClient {
  private readonly request: <T>(path: string, options?: RequestInit) => Promise<T>;
  private readonly token: () => string | null;
  private readonly secure: typeof getSecureMessaging;
  private readonly reconstruct: NonNullable<Dependencies['reconstruct']>;
  private readonly owner: string | null;
  private readonly actor: string | null;
  private readonly invalidate = () => this.stop();
  private binding = '';
  private localCursor = 0;
  private remoteCursor = 0;
  private pointers = new Map<string, Pointer>();
  private pending = new Map<string, PendingWorkPointer>();
  private events = new Map<string, VerifiedWorkEvent>();
  private latest: SharedWorkSnapshot | null = null;
  private active: Promise<SharedWorkSnapshot> | undefined;
  private stopped = false;
  private usable = false;
  constructor(readonly channelId: string, dependencies: Dependencies = {}) {
    this.request = dependencies.request || ((path, options) => api.request(path, options));
    this.token = dependencies.token || (() => api.getAccessToken()); this.owner = this.token(); this.actor = api.getCurrentUserId();
    this.secure = dependencies.secure || getSecureMessaging; this.reconstruct = dependencies.reconstruct || reconstructInWorker;
    if (typeof window !== 'undefined') { window.addEventListener('freecollab:auth-changed', this.invalidate); window.addEventListener('freecollab:session-expired', this.invalidate); }
  }
  stop() { this.stopped = true; this.usable = false; this.pointers.clear(); this.events.clear(); this.pending.clear(); this.latest = null; if (typeof window !== 'undefined') { window.removeEventListener('freecollab:auth-changed', this.invalidate); window.removeEventListener('freecollab:session-expired', this.invalidate); } }
  hasCurrentScope() { return this.usable && !this.stopped && Boolean(this.owner) && this.owner === this.token() && this.actor === api.getCurrentUserId(); }
  private current() { if (this.stopped || !this.owner || this.owner !== this.token() || this.actor !== api.getCurrentUserId()) throw new Error('The signed-in shared-work account changed.'); }
  private async context() {
    this.current(); const context = validateContext(await this.request<WorkContext>(base(this.channelId) + '/work-context'), this.channelId); this.current();
    const binding = context.connection_id + '\0' + context.room_id + '\0' + context.matrix_user_id;
    if (this.binding && this.binding !== binding) { this.stop(); throw new Error('The encrypted shared-work room or identity changed.'); }
    this.binding = binding; return context;
  }
  private async flush(context: WorkContext) {
    const digest = await pointerSessionDigest(this.owner!); this.current();
    try { for (const row of await pendingWorkPointers(context.matrix_user_id, digest)) if (row.channel_id === this.channelId && row.connection_id === context.connection_id && Date.now() - row.created_at < 7 * 86400000) this.pending.set(row.matrix_event_id, row); }
    catch { /* An open window retains its opaque retry even if IndexedDB fails. */ }
    for (const [id, row] of this.pending) {
      this.current(); await this.request(base(this.channelId) + '/work-index', { method: 'POST', body: JSON.stringify({ matrix_event_id: id }) }); this.current();
      this.pending.delete(id); await removeWorkPointer(row.key).catch(() => undefined); this.current();
    }
  }
  async load() {
    if (this.active) return this.active;
    this.active = this.read();
    try { return await this.active; } catch (error) { this.usable = false; throw error; } finally { this.active = undefined; }
  }
  private async read(): Promise<SharedWorkSnapshot> {
    const context = await this.context();
    await this.flush(context); this.current();
    let more = true, passes = 0;
    while (more) {
      if (++passes > 41) throw new Error('Encrypted shared work exceeds its bounded discovery pages.');
      const page = await this.request<IndexPage>(base(this.channelId) + '/work-index?local_after=' + this.localCursor + '&remote_after=' + this.remoteCursor); this.current();
      if (page.connection_id !== context.connection_id) throw new Error('The encrypted work index changed connection.');
      for (const [source, after, sourceServer] of [[page.local, this.localCursor,context.local_server], [page.remote, this.remoteCursor,context.peer_server]] as const) {
        if (!source || source.connection_id !== context.connection_id || source.status !== 'active' || !Array.isArray(source.items) || source.items.length > 50 || !Number.isSafeInteger(source.next_after) || source.next_after < after || typeof source.has_more !== 'boolean') throw new Error('The current shared-work index is invalid.');
        let previous = after;
        for (const row of source.items) {
          if (!Number.isSafeInteger(row.sequence) || row.sequence <= previous || !row.matrix_event_id?.startsWith('$') || row.matrix_event_id.length > 1024 || !matrixIdentity(row.matrix_user_id)||row.matrix_user_id.split(':',2)[1]!==sourceServer) throw new Error('An encrypted work pointer is invalid.');
          const known = this.pointers.get(row.matrix_event_id);
          if (known && known.matrix_user_id !== row.matrix_user_id) throw new Error('A shared-work pointer changed authenticated author.');
          this.pointers.set(row.matrix_event_id, row); previous = row.sequence;
        }
        if (previous !== source.next_after || source.has_more && !source.items.length) throw new Error('The shared-work index cursor did not advance.');
      }
      this.localCursor = page.local.next_after; this.remoteCursor = page.remote.next_after;
      more = page.local.has_more || page.remote.has_more;
      if (this.pointers.size > MAX_EVENTS) throw new Error('The shared-work discovery index exceeds its supported event limit.');
    }
    const secure = await this.secure(); this.current();
    const { c, roomId } = await secure.roomFor(this.channelId); this.current();
    if (roomId !== context.room_id || c.getUserId() !== context.matrix_user_id) throw new Error('The encryption device belongs to another shared-work scope.');
    const revocations = await this.request<{ revoked_devices: Array<{ matrix_user_id: string; device_id: string }> }>('/secure/devices/revocations?channel_id=' + encodeURIComponent(this.channelId)); this.current();
    if (!Array.isArray(revocations.revoked_devices) || revocations.revoked_devices.length > 20000 || revocations.revoked_devices.some(row => !matrixIdentity(row.matrix_user_id) || typeof row.device_id !== 'string' || !row.device_id || row.device_id.length > 128)) throw new Error('The current shared-work device revocation snapshot is invalid.');
    const revoked = new Set(revocations.revoked_devices.map(row => row.matrix_user_id + '\0' + row.device_id));
    if (revoked.has(context.matrix_user_id + '\0' + c.getDeviceId())) throw new Error('This shared-work encryption device was removed.');
    const revisions: WorkRevision[] = [], referenceRevisions:EncryptedWorkRevision[]=[], warnings: string[] = [], quarantined = new Set<string>(), rejectedByResource = new Map<string, string[]>(); let bytes = 0;
    this.events.clear();
    for (const pointer of this.pointers.values()) {
      let event: VerifiedWorkEvent;
      try { event = await trustedEvent(c, context, pointer, revoked); }
      catch (error) { this.current(); if (!(error instanceof CandidateFault)) throw error; quarantined.add(pointer.matrix_event_id); continue; }
      this.current();
      bytes += new TextEncoder().encode(JSON.stringify(event.content)).length;
      if (bytes > MAX_PLAIN_BYTES) throw new Error('The decrypted shared-work view exceeds its device memory bound.');
      if(event.type===ENCRYPTED_WORK_REVISION){if(context.work_protocols?.includes(ENCRYPTED_WORK_PROTOCOL)){try{referenceRevisions.push(await parseEncryptedRevision(event,context));}catch{quarantined.add(event.event_id);}}continue;}
      if (![WORK_REVISION, WORK_DOCUMENT_UPDATE, WORK_DOCUMENT_PART].includes(event.type)) continue;
      if (!(event.content.protocol === WORK_PROTOCOL || event.content.protocol === WORK_PROTOCOL_V2 && context.work_protocols?.includes(WORK_PROTOCOL_V2)) || event.content.connection_id !== context.connection_id || !resourceIdValid(event.content.id, context.connection_id)) { quarantined.add(event.event_id); continue; }
      if (event.type === WORK_REVISION) {
        try { revisions.push(await parseRevision(event, context)); }
        catch {
          quarantined.add(event.event_id);
          // Only an authenticated recognized revision may taint its resource.
          // An unverifiable event cannot nominate arbitrary unrelated IDs.
          if (['task', 'calendar', 'document'].includes(String(event.content.kind)) && Array.isArray(event.content.parents) && (event.content.parents.length || event.content.id.split(':')[1] === await authorNamespace(event.sender))) rejectedByResource.set(event.content.id, [...(rejectedByResource.get(event.content.id) || []), event.event_id]);
          continue;
        }
      }
      this.events.set(event.event_id, event);
    }
    const records = reduceWorkRevisions(revisions, rejectedByResource), byId = new Map(records.map(row => [row.id, row]));
    for (const event of this.events.values()) if (event.type === WORK_DOCUMENT_UPDATE && byId.get(String(event.content.id))?.kind !== 'document') quarantined.add(event.event_id);
    for (const record of records) if (record.kind === 'document' && !record.invalid.length) {
      try { await this.documentUpdates(record); }
      catch { const failed = [...this.events.values()].filter(event => event.type === WORK_DOCUMENT_UPDATE && event.content.id === record.id).map(event => event.event_id); record.invalid.push(...failed); failed.forEach(id => quarantined.add(id)); }
    }
    for (const row of records) { if (row.invalid.length) warnings.push('A shared resource contains unresolved or invalid revision ancestry.'); if (row.conflicts) warnings.push('Concurrent shared-work revisions need explicit review.'); }
    if (quarantined.size) warnings.push('Some indexed events are quarantined. This view contains available verified history and may be incomplete.');
    const finalContext = await this.context(); this.current();
    if (JSON.stringify(context.members) !== JSON.stringify(finalContext.members) || JSON.stringify(context.work_protocols) !== JSON.stringify(finalContext.work_protocols)) throw new Error('The current shared-work authority changed. Refresh before continuing.');
    const finalRevocations = await this.request<typeof revocations>('/secure/devices/revocations?channel_id=' + encodeURIComponent(this.channelId)); this.current();
    if (JSON.stringify(finalRevocations) !== JSON.stringify(revocations)) throw new Error('The current shared-work device authority changed. Refresh before continuing.');
    const reference_records=reduceEncryptedRevisions(referenceRevisions).filter(row=>!row.invalid.length&&!row.conflicts&&!row.selected.archived&&row.kind!=='organization');
    this.usable = true; this.latest = { context: finalContext, records, reference_records, warnings, pending_pointers: this.pending.size, quarantined_event_ids: [...quarantined] }; return this.latest;
  }
  private async publish(context: WorkContext, type: string, content: Record<string, unknown>) {
    try { return await this.publishConfirmed(context, type, content); } catch (error) { if(!(error instanceof SharedWorkDraftError))this.usable = false; throw error; }
  }
  private async publishConfirmed(context: WorkContext, type: string, content: Record<string, unknown>) {
    const payload={protocol: context.work_protocols?.includes(WORK_PROTOCOL_V2) ? WORK_PROTOCOL_V2 : WORK_PROTOCOL, connection_id: context.connection_id, ...content};
    if(new TextEncoder().encode(JSON.stringify(payload)).length>48*1024)throw new SharedWorkDraftError('This change is too large. Shorten the text before sharing.');
    this.current(); const secure = await this.secure(); this.current();
    const fresh = await this.context(); this.current();
    if (JSON.stringify(fresh.members) !== JSON.stringify(context.members) || JSON.stringify(fresh.work_protocols) !== JSON.stringify(context.work_protocols)) throw new Error('The shared-work publishing authority changed. Refresh before saving.');
    const result = await secure.sendEvent(this.channelId, type, payload, 'shared-work-' + crypto.randomUUID()); this.current();
    const digest = await pointerSessionDigest(this.owner!); this.current();
    const pending: PendingWorkPointer = { key: context.matrix_user_id + ':' + digest + ':' + result.event_id, owner: context.matrix_user_id, session_digest: digest, channel_id: this.channelId, connection_id: context.connection_id, matrix_event_id: result.event_id, created_at: Date.now() };
    this.pending.set(result.event_id, pending);
    await storeWorkPointer(pending).catch(() => undefined); this.current();
    await this.flush(context); return result.event_id;
  }
  async save(kind: SharedWorkKind, fields: SharedWorkFields, previous?: WorkRecord, mergeParents?: string[], archived = false) {
    const view = await this.load(); this.current();
    const normalized = validateSharedFields(kind, fields, view.context.work_protocols?.includes(WORK_PROTOCOL_V2) ? WORK_PROTOCOL_V2 : WORK_PROTOCOL, view.context.connection_id);
    if ('references' in normalized) { const known=view.records.find(row=>row.id===previous?.id&&row.kind===kind),prior=known?.selected.fields;
      await this.validateReferences(view,addedEncryptedReferences(prior?encryptedReferenceTexts(prior as unknown as Record<string,unknown>):{},prior&&'references' in prior?prior.references:undefined,encryptedReferenceTexts(normalized as unknown as Record<string,unknown>),normalized.references)); }
    if (kind === 'task') { const assignee = (normalized as SharedTaskFields).assignee_matrix_id; if (assignee && !view.context.members.some(member => member.matrix_user_id === assignee)) throw new Error('Choose a current shared-channel participant.'); }
    let parents: string[] = [], id: string;
    if (previous) {
      const current = view.records.find(row => row.id === previous.id);
      if (!current || current.kind !== kind || current.invalid.length) throw new Error('Refresh the verified resource before editing.');
      const expected = previous.heads.map(row => row.event_id).sort(), actual = current.heads.map(row => row.event_id).sort();
      if (JSON.stringify(expected) !== JSON.stringify(actual)) throw new Error('This shared item changed. Refresh and review the latest revision.');
      if (current.conflicts && !mergeParents) throw new Error('Review competing revisions and explicitly merge their parent references.');
      parents = mergeParents || expected;
      if (!parents.length || parents.length > 8 || parents.some(parent => !actual.includes(parent)) || new Set(parents).size !== parents.length) throw new Error('Choose at most eight current competing revisions to merge.');
      id = previous.id;
    } else id = view.context.connection_id + ':' + await authorNamespace(view.context.matrix_user_id) + ':' + crypto.randomUUID();
    this.current();
    const eventId = await this.publish(view.context, WORK_REVISION, { id, kind, parents, archived, fields: normalized });
    const result = await this.load();
    const record = result.records.find(row => row.id === id);
    if (!record || !record.history.some(row => row.event_id === eventId)) throw new Error('The encrypted revision acknowledgement could not be verified.');
    return record;
  }
  private async validateReferences(view: SharedWorkSnapshot, references?: EncryptedFieldReferences) {
    const values = Object.values(references || {}).flat();
    if (values.some(ref => ref.kind === 'participant' && !view.context.members.some(member => member.matrix_user_id === ref.id))) throw new Error('Choose a current verified room participant.');
    const unknown = values.filter(ref => ref.kind === 'resource' && !view.records.some(row => row.id === ref.id && (!ref.resource_kind || row.kind === ref.resource_kind) && !row.invalid.length && !row.conflicts && !row.selected.archived));
    if (unknown.length || values.some(ref => ref.kind === 'message')) {
      const { EncryptedWorkClient, verifyEncryptedRoomEvent } = await import('./encrypted-work-api'); this.current();
      if (unknown.length) { const generic = new EncryptedWorkClient(this.channelId); try { const snapshot = await generic.load(); this.current(); if (snapshot.context.connection_id !== view.context.connection_id || snapshot.context.room_id !== view.context.room_id || unknown.some(ref => !snapshot.records.some(row => row.id === ref.id && (!ref.resource_kind || row.kind === ref.resource_kind) && !row.invalid.length && !row.conflicts && !row.selected.archived))) throw new Error('The selected encrypted resource is no longer available.'); } finally { generic.stop(); } }
      for (const ref of values.filter(ref => ref.kind === 'message')) { await verifyEncryptedRoomEvent(this.channelId, ref.id, { requireCurrentParticipant: false }); this.current(); }
    }
  }
  async createDocument(title: string, references?: EncryptedFieldReferences) {
    const view = await this.load(); this.current();
    if (references && !view.context.work_protocols?.includes(WORK_PROTOCOL_V2)) throw new Error('Both installations must support encrypted document references.');
    await this.validateReferences(view, references);
    const doc = new Y.Doc();
    try { doc.getText('title').insert(0, title); doc.getText('body'); if (references) setWorkDocumentReferences(doc, references, view.context.connection_id); validateWorkDocument(doc); return await this.save('document', { title, initial_update_base64: encodeWorkUpdate(Y.encodeStateAsUpdate(doc)) }); }
    finally { doc.destroy(); }
  }
  private async documentUpdates(record: WorkRecord) {
    const updates: Array<{ id: string; update_base64: string }> = [];
    for (const event of this.events.values()) {
      if (event.type !== WORK_DOCUMENT_UPDATE || event.content.id !== record.id) continue;
      strict(event.content, ['protocol', 'connection_id', 'id', 'root_event_id', 'update_base64', 'parts', 'byte_size', 'state_hash']);
      const value = event.content as unknown as DocumentUpdate;
      if (value.root_event_id !== record.root_event_id) throw new Error('A shared document update refers to a different creation revision.');
      if (value.parts !== undefined) {
        if (value.update_base64 !== undefined || !Array.isArray(value.parts) || value.parts.length < 2 || value.parts.length > 16 || new Set(value.parts).size !== value.parts.length || !Number.isInteger(value.byte_size) || value.byte_size! <= MAX_PART || value.byte_size! > 256 * 1024 || typeof value.state_hash !== 'string' || !/^[a-f0-9]{64}$/.test(value.state_hash)) throw new Error('Invalid encrypted document fragment commitment.');
        const bytes = new Uint8Array(value.byte_size!); let offset = 0;
        for (let index = 0; index < value.parts.length; index++) {
          const part = this.events.get(value.parts[index]);
          if (!part || part.type !== WORK_DOCUMENT_PART || part.sender !== event.sender || part.content.id !== record.id || part.content.root_event_id !== record.root_event_id || part.content.index !== index || part.content.count !== value.parts.length || part.content.state_hash !== value.state_hash) throw new Error('An encrypted document fragment failed its author or commitment check.');
          strict(part.content, ['protocol', 'connection_id', 'id', 'root_event_id', 'index', 'count', 'state_hash', 'part_base64']);
          const block = decodeWorkUpdate(part.content.part_base64, MAX_PART, 1);
          if (offset + block.length > bytes.length || index < value.parts.length - 1 && block.length !== MAX_PART) throw new Error('Invalid document fragment length.');
          bytes.set(block, offset); offset += block.length;
        }
        if (offset !== bytes.length || await hash(bytes) !== value.state_hash) throw new Error('The encrypted document fragments do not match their hash commitment.');
        updates.push({ id: event.event_id, update_base64: encodeWorkUpdate(bytes) });
      } else {
        if (value.byte_size !== undefined || value.state_hash !== undefined) throw new Error('Invalid inline encrypted document update.');
        decodeWorkUpdate(value.update_base64, MAX_PART); updates.push({ id: event.event_id, update_base64: value.update_base64! });
      }
    }
    return updates.sort((a, b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
  }
  async document(record: WorkRecord): Promise<FederatedDocumentState> {
    this.current(); await this.load();
    if (!this.hasCurrentScope()) throw new Error('Refresh current shared-work access before opening a document.');
    const current = this.latest?.records.find(row => row.id === record.id);
    if (!current || current.kind !== 'document' || current.conflicts || current.invalid.length) throw new FederatedWorkResourceError(record.id, 'A verified, complete and conflict-free shared document is required.');
    let state: FederatedDocumentState;
    try { state = await this.reconstruct((current.selected.fields as SharedDocumentFields).initial_update_base64, await this.documentUpdates(current)); }
    catch (error) { this.current(); current.invalid.push(current.root_event_id); throw new FederatedWorkResourceError(record.id, error instanceof Error ? error.message : 'The shared document could not be reconstructed.'); }
    this.current();
    if (state.references) { if (!this.latest?.context.work_protocols?.includes(WORK_PROTOCOL_V2)) throw new FederatedWorkResourceError(record.id, 'This document requires encrypted reference support.'); validateEncryptedReferences({ title: state.title, body: state.body }, state.references, this.latest.context.connection_id); }
    return state;
  }
  async saveDocument(record: WorkRecord, original: FederatedDocumentState, title: string, body: string, references?: EncryptedFieldReferences) {
    const initialView = await this.load(); this.current();
    const nextReferences = references || Object.fromEntries((['title', 'body'] as const).map(field => [field, rebaseEncryptedReferences(original[field], field === 'title' ? title : body, original.references?.[field] || [])]));
    const normalized = validateEncryptedReferences({ title, body }, nextReferences, initialView.context.connection_id);
    if (normalized && !initialView.context.work_protocols?.includes(WORK_PROTOCOL_V2)) throw new Error('Both installations must support encrypted document references.');
    const verifiedOriginal=await this.document(record); this.current();
    await this.validateReferences(this.latest!,addedEncryptedReferences({title:verifiedOriginal.title,body:verifiedOriginal.body},verifiedOriginal.references,{title,body},normalized));
    const baseDoc = new Y.Doc(); let update: Uint8Array;
    try { Y.applyUpdate(baseDoc, decodeWorkUpdate(original.update_base64, 512 * 1024)); const vector = Y.encodeStateVector(baseDoc); replaceWorkText(baseDoc, 'title', title); replaceWorkText(baseDoc, 'body', body); if (normalized || original.references) setWorkDocumentReferences(baseDoc, normalized, initialView.context.connection_id); validateWorkDocument(baseDoc); update = Y.encodeStateAsUpdate(baseDoc, vector); }
    finally { baseDoc.destroy(); }
    if (update.length <= 2) return original;
    const view = await this.load(); const current = view.records.find(row => row.id === record.id);
    if (!current || current.kind !== 'document' || current.invalid.length || current.conflicts) throw new Error('Refresh the verified shared document before editing.');
    const updates = await this.documentUpdates(current);
    await this.reconstruct((current.selected.fields as SharedDocumentFields).initial_update_base64, [...updates, { id: 'pending', update_base64: encodeWorkUpdate(update) }]); this.current();
    const common = { id: current.id, root_event_id: current.root_event_id };
    if (update.length > MAX_PART) {
      const count = Math.ceil(update.length / MAX_PART), stateHash = await hash(update), parts: string[] = [];
      if (count > 16) throw new Error('Save a smaller document change at a time.');
      for (let index = 0; index < count; index++) parts.push(await this.publish(view.context, WORK_DOCUMENT_PART, { ...common, index, count, state_hash: stateHash, part_base64: encodeWorkUpdate(update.slice(index * MAX_PART, (index + 1) * MAX_PART)) }));
      await this.publish(view.context, WORK_DOCUMENT_UPDATE, { ...common, parts, byte_size: update.length, state_hash: stateHash });
    } else await this.publish(view.context, WORK_DOCUMENT_UPDATE, { ...common, update_base64: encodeWorkUpdate(update) });
    await this.load(); return this.document(current);
  }
}
