import { api } from './api';
import { getSecureMessaging } from './secure-client';
import { addedEncryptedReferences, encryptedReferenceTexts } from './encrypted-work-references';
import { CandidateFault, trustedEvent, validateContext } from './federated-work-api';
import { authorNamespace, matrixIdentity, parseRevision, reduceWorkRevisions, resourceIdValid, WORK_REVISION, type WorkRecord, type WorkRevision, type VerifiedWorkEvent } from './federated-work-model';
import { pendingWorkPointers, pointerSessionDigest, removeWorkPointer, storeWorkPointer, type PendingWorkPointer } from './federated-work-pointers';
import { deterministicWorkUuid, ENCRYPTED_WORK_PROTOCOL, ENCRYPTED_WORK_REVISION, organizationResourceId, parseEncryptedRevision,
  reduceEncryptedRevisions, validateEncryptedFields, type EncryptedWorkContext, type EncryptedWorkFields, type EncryptedWorkKind,
  type EncryptedWorkRecord, type EncryptedWorkRevision, type EncryptedWorkflowStep } from './encrypted-work-model';

export * from './encrypted-work-model';
export interface EncryptedWorkSnapshot {
  context: EncryptedWorkContext; records: EncryptedWorkRecord[]; legacyRecords: WorkRecord[];
  warnings: string[]; pending_pointers: number; quarantined_event_ids: string[];
}
interface Pointer { sequence: number; matrix_event_id: string; matrix_user_id: string; index_server?: string }
export interface VerifiedIndexedWorkEvent extends VerifiedWorkEvent {index_server:string;index_sequence:number}
interface Page { items: Pointer[]; next_after: number; has_more: boolean; status?: string; connection_id?: string }
interface Dependencies {
  request?: <T>(path: string, options?: RequestInit) => Promise<T>;
  token?: () => string | null; actor?: () => string | null; secure?: typeof getSecureMessaging;
}
export interface EncryptedSaveOptions { id?: string; mergeParents?: string[]; archived?: boolean }
const local = (channel: string) => '/channels/' + encodeURIComponent(channel) + '/encrypted-work';
const federated = (channel: string) => '/connect/federation/channels/' + encodeURIComponent(channel);
class EncryptedWorkDraftError extends Error {}

function checkedContext(value: EncryptedWorkContext, channelId: string) {
  validateContext(value, channelId);
  if (!['local', 'federated'].includes(value.mode) || value.encrypted_work_protocol !== ENCRYPTED_WORK_PROTOCOL
      || value.encrypted_work_supported !== true || !Array.isArray(value.work_protocols) || !value.work_protocols.includes(ENCRYPTED_WORK_PROTOCOL)
      || typeof value.workspace_id !== 'string' || !value.workspace_id || value.workspace_id.length > 128
      || !Array.isArray(value.room_protocols) || value.room_protocols.length > 16 || value.room_protocols.some(protocol => typeof protocol !== 'string' || protocol.length > 100)) throw Error('Both installations must support the encrypted work protocol before sharing these resources.');
  return value;
}
export async function loadEncryptedRoomContext(channelId: string): Promise<EncryptedWorkContext> {
  const token = api.getAccessToken(), actor = api.getCurrentUserId();
  let live = true;
  const cancel = () => { live = false; };
  window.addEventListener('freecollab:auth-changed', cancel); window.addEventListener('freecollab:session-expired', cancel);
  try {
    if (!token || !actor) throw Error('Sign in before opening encrypted work.');
    const value = await api.request<EncryptedWorkContext>(local(channelId) + '/context');
    if (!live || token !== api.getAccessToken() || actor !== api.getCurrentUserId()) throw Error('The encrypted work account changed.');
    return checkedContext(value, channelId);
  } finally { window.removeEventListener('freecollab:auth-changed', cancel); window.removeEventListener('freecollab:session-expired', cancel); }
}
export async function verifyEncryptedRoomEvent(channelId: string, eventId: string, options: {requireCurrentParticipant?: boolean} = {}): Promise<VerifiedWorkEvent> {
  const token = api.getAccessToken(), actor = api.getCurrentUserId(); let live = true;
  const cancel = () => { live = false; }, current = () => { if (!live || !token || token !== api.getAccessToken() || actor !== api.getCurrentUserId()) throw Error('The encrypted work account changed.'); };
  window.addEventListener('freecollab:auth-changed', cancel); window.addEventListener('freecollab:session-expired', cancel);
  try {
    current(); const context = await loadEncryptedRoomContext(channelId); current();
    const secure = await getSecureMessaging(); current(); const { c, roomId } = await secure.roomFor(channelId); current();
    if (roomId !== context.room_id || c.getUserId() !== context.matrix_user_id || !eventId.startsWith('$') || eventId.length > 1024) throw Error('The encrypted event belongs to another scope.');
    const raw = await c.fetchRoomEvent(roomId, eventId); current(); const sender = raw.sender;
    if (!matrixIdentity(sender) || options.requireCurrentParticipant !== false && !context.members.some(member => member.matrix_user_id === sender)) throw Error('A current verified room participant is required.');
    const revocations = await api.request<{revoked_devices: Array<{matrix_user_id: string; device_id: string}>}>('/secure/devices/revocations?channel_id=' + encodeURIComponent(channelId)); current();
    if (!Array.isArray(revocations.revoked_devices) || revocations.revoked_devices.length > 20000 || revocations.revoked_devices.some(row => !matrixIdentity(row.matrix_user_id) || typeof row.device_id !== 'string' || !row.device_id || row.device_id.length > 128)) throw Error('Invalid current encryption revocations.');
    const revoked = new Set(revocations.revoked_devices.map(row => row.matrix_user_id + '\0' + row.device_id));
    if (revoked.has(context.matrix_user_id + '\0' + c.getDeviceId())) throw Error('This encryption device was removed.');
    const event = await trustedEvent(c, context, { sequence: 0, matrix_event_id: eventId, matrix_user_id: sender }, revoked); current();
    const latest = await loadEncryptedRoomContext(channelId); current();
    if (latest.room_id !== context.room_id || latest.connection_id !== context.connection_id || latest.matrix_user_id !== context.matrix_user_id
        || options.requireCurrentParticipant !== false && !latest.members.some(member => member.matrix_user_id === sender)) throw Error('The encrypted room or participant access changed.');
    return event;
  } finally { window.removeEventListener('freecollab:auth-changed', cancel); window.removeEventListener('freecollab:session-expired', cancel); }
}
export async function publishEncryptedRoomPointer(channelId: string, eventId: string) {
  const client = new EncryptedWorkClient(channelId);
  try { return await client.publishPointer(eventId); } finally { client.stop(); }
}
export interface EncryptedWorkAuthority {
  protocol: 'freecollab-work-authority/1'; event_id: string; room_id: string;
  matrix_user_id: string; role: 'admin' | 'member'; issuer: string; published_at: string;
}
/** Callers compare this receipt's exact sender to their already verified revision. */
export async function getEncryptedWorkAuthority(channelId: string, eventId: string): Promise<EncryptedWorkAuthority> {
  const token = api.getAccessToken(), actor = api.getCurrentUserId(); let live = true;
  const cancel = () => { live = false; }, current = () => { if (!live || !token || token !== api.getAccessToken() || actor !== api.getCurrentUserId()) throw Error('The encrypted authority account changed.'); };
  window.addEventListener('freecollab:auth-changed', cancel); window.addEventListener('freecollab:session-expired', cancel);
  try {
    current(); const context = await loadEncryptedRoomContext(channelId); current();
    const proof = await api.request<EncryptedWorkAuthority>(local(channelId) + '/authority?event_id=' + encodeURIComponent(eventId)); current();
    if (!proof || Object.keys(proof).some(key => !['protocol', 'event_id', 'room_id', 'matrix_user_id', 'role', 'issuer', 'published_at'].includes(key))
        || proof.protocol !== 'freecollab-work-authority/1' || proof.event_id !== eventId || proof.room_id !== context.room_id
        || !matrixIdentity(proof.matrix_user_id) || !['member', 'admin'].includes(proof.role)
        || ![context.local_server, context.peer_server].includes(proof.issuer) || !proof.matrix_user_id.endsWith(':' + proof.issuer)
        || typeof proof.published_at !== 'string' || !Number.isFinite(Date.parse(proof.published_at))) throw Error('The publication-time authority receipt does not match this room and event.');
    const latest = await loadEncryptedRoomContext(channelId); current();
    if (latest.connection_id !== context.connection_id || latest.room_id !== context.room_id || latest.matrix_user_id !== context.matrix_user_id) throw Error('The encrypted authority scope changed.');
    return proof;
  } finally { window.removeEventListener('freecollab:auth-changed', cancel); window.removeEventListener('freecollab:session-expired', cancel); }
}

/** Resource content exists only in verified Matrix events and this device's memory. */
export class EncryptedWorkClient {
  private readonly request: NonNullable<Dependencies['request']>;
  private readonly token: NonNullable<Dependencies['token']>;
  private readonly actor: NonNullable<Dependencies['actor']>;
  private readonly secure: typeof getSecureMessaging;
  private readonly owner: string | null;
  private readonly ownerActor: string | null;
  private stopped = false; private usable = false; private binding = '';
  private cursor = 0; private peerCursor = 0;
  private pointers = new Map<string, Pointer>();
  private eventsById = new Map<string, VerifiedWorkEvent>();
  private eventTypes = new Map<string,string>();
  private pending = new Map<string, PendingWorkPointer>();
  private latest: EncryptedWorkSnapshot | null = null;
  private active: Promise<EncryptedWorkSnapshot> | undefined;
  private activeFiltered=false;
  private readonly invalidate = () => this.stop();
  constructor(readonly channelId: string, dependencies: Dependencies = {}) {
    this.request = dependencies.request || ((path, options) => api.request(path, options));
    this.token = dependencies.token || (() => api.getAccessToken());
    this.actor = dependencies.actor || (() => api.getCurrentUserId());
    this.secure = dependencies.secure || getSecureMessaging;
    this.owner = this.token(); this.ownerActor = this.actor();
    if (typeof window !== 'undefined') {
      window.addEventListener('freecollab:auth-changed', this.invalidate);
      window.addEventListener('freecollab:session-expired', this.invalidate);
    }
  }
  stop() {
    this.stopped = true; this.usable = false; this.latest = null; this.pointers.clear(); this.pending.clear(); this.eventsById.clear();this.eventTypes.clear();
    if (typeof window !== 'undefined') {
      window.removeEventListener('freecollab:auth-changed', this.invalidate);
      window.removeEventListener('freecollab:session-expired', this.invalidate);
    }
  }
  hasCurrentScope() { return this.usable && !this.stopped && !!this.owner && this.owner === this.token() && this.ownerActor === this.actor(); }
  private current() {
    if (this.stopped || !this.owner || !this.ownerActor || this.owner !== this.token() || this.ownerActor !== this.actor()) throw Error('The encrypted work account or session changed.');
  }
  private async context() {
    this.current();
    const value = await this.request<EncryptedWorkContext>(local(this.channelId) + '/context'); this.current();
    checkedContext(value, this.channelId);
    const scope = JSON.stringify([value.connection_id, value.room_id, value.matrix_user_id, value.workspace_id, value.mode]);
    if (this.binding && this.binding !== scope) { this.stop(); throw Error('The encrypted work room or identity changed.'); }
    this.binding = scope; return value;
  }
  private endpoint(context: EncryptedWorkContext) {
    return context.mode === 'federated' ? federated(this.channelId) + '/work-index' : local(this.channelId) + '/index';
  }
  private async flush(context: EncryptedWorkContext) {
    const digest = await pointerSessionDigest(this.owner!); this.current();
    try { for (const pointer of await pendingWorkPointers(context.matrix_user_id, digest))
      if (pointer.channel_id === this.channelId && pointer.connection_id === context.connection_id && Date.now() - pointer.created_at < 7 * 86400000) this.pending.set(pointer.matrix_event_id, pointer); }
    catch { /* Same-window retry remains available when private storage is unavailable. */ }
    for (const [id, row] of this.pending) {
      this.current(); await this.request(this.endpoint(context), { method: 'POST', body: JSON.stringify({ matrix_event_id: id }) }); this.current();
      this.pending.delete(id); await removeWorkPointer(row.key).catch(() => undefined); this.current();
    }
  }
  async load(): Promise<EncryptedWorkSnapshot> {
    this.current(); if (this.active) {const pending=this.active,filtered=this.activeFiltered,result=await pending;this.current();if(!filtered)return result;if(this.active===pending){this.active=undefined;this.activeFiltered=false;}return this.load();}
    const pending=this.read();this.active=pending;this.activeFiltered=false;
    try { return await pending; }
    catch (failure) { this.usable = false; throw failure; }
    finally { if(this.active===pending){this.active = undefined;this.activeFiltered=false;} }
  }
  private async loadEvents(types:string[]) {
    this.current();while(this.active){const pending=this.active;await pending;this.current();if(this.active===pending){this.active=undefined;this.activeFiltered=false;}}
    const pending=this.read(new Set(types));this.active=pending;this.activeFiltered=true;
    try{return await pending;}catch(failure){this.usable=false;throw failure;}finally{if(this.active===pending){this.active=undefined;this.activeFiltered=false;}}
  }
  private async read(types?:ReadonlySet<string>): Promise<EncryptedWorkSnapshot> {
    const context = await this.context(); await this.flush(context); this.current();
    let more = true, pages = 0;
    while (more) {
      if (++pages > 201) throw Error('Encrypted work exceeds its discovery bound.');
      let results: Page[];
      if (context.mode === 'federated') {
        const value = await this.request<{connection_id: string; local: Page; remote: Page}>(this.endpoint(context) + '?local_after=' + this.cursor + '&remote_after=' + this.peerCursor); this.current();
        if (value.connection_id !== context.connection_id) throw Error('The encrypted work index changed connection.');
        results = [value.local, value.remote];
      } else { results = [await this.request<Page>(this.endpoint(context) + '?after=' + this.cursor)]; this.current(); }
      for (let i = 0; i < results.length; i++) {
        const page = results[i], after = i ? this.peerCursor : this.cursor;
        if (!page || !Array.isArray(page.items) || page.items.length > 50 || typeof page.has_more !== 'boolean' || !Number.isSafeInteger(page.next_after)
            || page.next_after < after || context.mode === 'federated' && (page.status !== 'active' || page.connection_id !== context.connection_id)) throw Error('Invalid encrypted work index page.');
        let previous = after;
        for (const row of page.items) {
          const sourceServer=i?context.peer_server:context.local_server;
          if (!Number.isSafeInteger(row.sequence) || row.sequence <= previous || !row.matrix_event_id?.startsWith('$') || row.matrix_event_id.length > 1024 || !matrixIdentity(row.matrix_user_id)||row.matrix_user_id.split(':',2)[1]!==sourceServer) throw Error('Invalid encrypted work pointer.');
          const known = this.pointers.get(row.matrix_event_id);
          if (known && (known.matrix_user_id !== row.matrix_user_id||known.index_server!==sourceServer||known.sequence!==row.sequence)) throw Error('An encrypted event changed authenticated sender or index position.');
          this.pointers.set(row.matrix_event_id, {...row,index_server:sourceServer}); previous = row.sequence;
        }
        if (previous !== page.next_after || page.has_more && !page.items.length) throw Error('The encrypted work cursor did not advance.');
      }
      this.cursor = results[0].next_after; if (results[1]) this.peerCursor = results[1].next_after;
      more = results.some(page => page.has_more);
      if (this.pointers.size > 10000) throw Error('Encrypted work exceeds its retained event bound.');
    }
    const secure = await this.secure(); this.current();
    const { c, roomId } = await secure.roomFor(this.channelId); this.current();
    if (roomId !== context.room_id || c.getUserId() !== context.matrix_user_id) throw Error('This encryption device belongs to another room.');
    const revocations = await this.request<{revoked_devices: Array<{matrix_user_id: string; device_id: string}>}>('/secure/devices/revocations?channel_id=' + encodeURIComponent(this.channelId)); this.current();
    if (!Array.isArray(revocations.revoked_devices) || revocations.revoked_devices.length > 20000
        || revocations.revoked_devices.some(row => !matrixIdentity(row.matrix_user_id) || typeof row.device_id !== 'string' || !row.device_id || row.device_id.length > 128)) throw Error('Invalid current encryption-device revocation snapshot.');
    const revoked = new Set(revocations.revoked_devices.map(row => row.matrix_user_id + '\0' + row.device_id));
    if (revoked.has(context.matrix_user_id + '\0' + c.getDeviceId())) throw Error('This encryption device was removed.');
    const revisions: EncryptedWorkRevision[] = [], legacy: WorkRevision[] = [], quarantined: string[] = [], warnings: string[] = [], rejected = new Map<string,VerifiedWorkEvent[]>();
    this.eventsById.clear();
    let bytes = 0;
    for (const pointer of this.pointers.values()) {
      const hint=this.eventTypes.get(pointer.matrix_event_id);if(types&&hint&&!types.has(hint))continue;
      let event;
      try { event = await trustedEvent(c, context, pointer, revoked); }
      catch (failure) { this.current(); if (!(failure instanceof CandidateFault)) throw failure; quarantined.push(pointer.matrix_event_id); continue; }
      this.current(); bytes += new TextEncoder().encode(JSON.stringify(event.content)).length;
      if (bytes > 12 * 1024 * 1024) throw Error('Decrypted work exceeds this view’s memory bound.');
      this.eventTypes.set(event.event_id,event.type);if(types&&!types.has(event.type))continue;
      this.eventsById.set(event.event_id, event);
      try {
        if (event.type === ENCRYPTED_WORK_REVISION) revisions.push(await parseEncryptedRevision(event, context));
        else if (event.type === WORK_REVISION) legacy.push(await parseRevision(event, context));
      } catch {
        quarantined.push(event.event_id);
        if (event.type === ENCRYPTED_WORK_REVISION && resourceIdValid(event.content.id, context.connection_id)
            && Array.isArray(event.content.parents) && (event.content.parents.length || String(event.content.id).split(':')[1] === await authorNamespace(event.sender))) rejected.set(String(event.content.id), [...(rejected.get(String(event.content.id)) || []),event]);
      }
    }
    const records = reduceEncryptedRevisions(revisions), legacyRecords = reduceWorkRevisions(legacy);
    for (const row of records) { row.invalid.push(...(rejected.get(row.id) || []).filter(event=>event.content.kind===row.kind&&(!['workflow-run','comment'].includes(row.kind)||event.sender===row.created_by)).map(event=>event.event_id));quarantined.push(...(row.quarantined || [])); }
    if (records.some(row => row.invalid.length || row.conflicts)) warnings.push('Concurrent or incomplete encrypted work needs explicit review.');
    if (quarantined.length) warnings.push('Some encrypted events are unavailable or unverified. This history may be incomplete.');
    const fresh = await this.context(); this.current();
    if (JSON.stringify(context.members) !== JSON.stringify(fresh.members) || JSON.stringify(context.work_protocols) !== JSON.stringify(fresh.work_protocols)) throw Error('The current encrypted room authority changed. Refresh before continuing.');
    const finalRevocations = await this.request<typeof revocations>('/secure/devices/revocations?channel_id=' + encodeURIComponent(this.channelId)); this.current();
    if (JSON.stringify(finalRevocations) !== JSON.stringify(revocations)) throw Error('The current encryption device authority changed. Refresh before continuing.');
    this.usable = true;
    this.latest = { context:fresh, records, legacyRecords, warnings, pending_pointers: this.pending.size, quarantined_event_ids: quarantined };
    return this.latest;
  }
  async organizationId() { const view = await this.load(); this.current(); return organizationResourceId(view.context.connection_id); }
  async verifiedEvents(types: string[]): Promise<VerifiedWorkEvent[]> {
    if (!Array.isArray(types) || !types.length || types.length > 16 || types.some(type => typeof type !== 'string' || !type || type.length > 128)) throw Error('Choose bounded encrypted event types.');
    await this.loadEvents(types); this.current();
    return [...this.eventsById.values()].filter(event => types.includes(event.type))
      .map(event => ({ ...event, content: JSON.parse(JSON.stringify(event.content)) as Record<string, unknown> }));
  }
  async verifiedIndexedEvents(types:string[]):Promise<VerifiedIndexedWorkEvent[]> {
    const events=await this.verifiedEvents(types);this.current();
    return events.map(event=>{const pointer=this.pointers.get(event.event_id);if(!pointer?.index_server||pointer.matrix_user_id!==event.sender||!Number.isSafeInteger(pointer.sequence)||pointer.sequence<1)throw Error('The encrypted event has no authenticated durable index position.');return {...event,index_server:pointer.index_server,index_sequence:pointer.sequence};});
  }
  async publishPointer(eventId: string) {
    const context = await this.context(); this.current();
    if (typeof eventId !== 'string' || !eventId.startsWith('$') || eventId.length > 1024) throw Error('Choose an encrypted Matrix event.');
    const digest = await pointerSessionDigest(this.owner!); this.current();
    const pointer: PendingWorkPointer = { key: context.matrix_user_id + ':' + digest + ':' + eventId,
      owner: context.matrix_user_id, session_digest: digest, channel_id: this.channelId, connection_id: context.connection_id,
      matrix_event_id: eventId, created_at: Date.now() };
    this.pending.set(eventId, pointer); await storeWorkPointer(pointer).catch(() => undefined); this.current();
    await this.flush(context); return eventId;
  }
  private async send(context: EncryptedWorkContext, content: Record<string, unknown>) {
    const payload={protocol: ENCRYPTED_WORK_PROTOCOL, connection_id: context.connection_id, ...content};
    if(new TextEncoder().encode(JSON.stringify(payload)).length>40*1024)throw new EncryptedWorkDraftError('This change is too large. Shorten the text or save fewer workflow steps.');
    const secure = await this.secure(); this.current();
    const sent = await secure.sendEvent(this.channelId, ENCRYPTED_WORK_REVISION, payload, 'encrypted-work-' + crypto.randomUUID()); this.current();
    const digest = await pointerSessionDigest(this.owner!); this.current();
    const pointer: PendingWorkPointer = { key: context.matrix_user_id + ':' + digest + ':' + sent.event_id,
      owner: context.matrix_user_id, session_digest: digest, channel_id: this.channelId, connection_id: context.connection_id,
      matrix_event_id: sent.event_id, created_at: Date.now() };
    this.pending.set(sent.event_id, pointer); await storeWorkPointer(pointer).catch(() => undefined); this.current();
    await this.flush(context); return sent.event_id;
  }
  async save(kind: EncryptedWorkKind, fields: EncryptedWorkFields, previous?: EncryptedWorkRecord, options: EncryptedSaveOptions = {}) {
    try {
      const view = await this.load(); this.current();
      const normalized = validateEncryptedFields(kind, fields, view.context.connection_id), namespace = await authorNamespace(view.context.matrix_user_id); this.current();
      let id = previous?.id || options.id || view.context.connection_id + ':' + namespace + ':' + crypto.randomUUID();
      if (!id.startsWith(view.context.connection_id + ':') && /^[A-Za-z0-9_-]{1,128}$/.test(id)) id = view.context.connection_id + ':' + namespace + ':' + await deterministicWorkUuid(id);
      if (!resourceIdValid(id, view.context.connection_id) || kind === 'organization' && id !== await organizationResourceId(view.context.connection_id)
          || kind !== 'organization' && !previous && id.split(':')[1] !== namespace) throw Error('The encrypted resource belongs to another creator or room.');
      const known = view.records.find(row => row.id === id && row.kind === kind);
      const references = Object.values(addedEncryptedReferences(known ? encryptedReferenceTexts(known.selected.fields as unknown as Record<string,unknown>) : {},known?.selected.fields.references,encryptedReferenceTexts(normalized as unknown as Record<string,unknown>),normalized.references) || {}).flat();
      if (references.some(ref => ref.kind === 'participant' && !view.context.members.some(member => member.matrix_user_id === ref.id)
          || ref.kind === 'resource' && ![...view.records, ...view.legacyRecords].some(row => row.id === ref.id && row.kind === ref.resource_kind && !row.invalid.length && !row.conflicts && !row.selected.archived))) throw Error('A selected encrypted reference is no longer available.');
      if (kind === 'task' && normalized.assignee_matrix_id && !view.context.members.some(member => member.matrix_user_id === normalized.assignee_matrix_id)) throw Error('Choose a current verified participant.');
      if (kind === 'task' && normalized.project_id && !view.records.some(row => row.kind === 'project' && row.id === normalized.project_id && !row.invalid.length && !row.conflicts && !row.selected.archived)) throw Error('The encrypted project is no longer available.');
      if (kind === 'task' && (normalized.file_ids?.some(id => !view.records.some(row => row.id === id && row.kind === 'linked-file' && !row.invalid.length && !row.conflicts && !row.selected.archived))
          || normalized.decision_ids?.some(id => !view.records.some(row => row.id === id && row.kind === 'decision' && !row.invalid.length && !row.conflicts && !row.selected.archived)))) throw Error('An encrypted task link is no longer available.');
      if (kind === 'comment' && ![...view.records, ...view.legacyRecords].some(row => row.id === normalized.target_id && row.kind === normalized.target_kind && !row.invalid.length && !row.conflicts && !row.selected.archived)) throw Error('The encrypted comment target is no longer available.');
      if(kind==='comment'&&!previous&&normalized.anchor){
        const target=view.legacyRecords.find(row=>row.id===normalized.target_id&&row.kind==='document'&&!row.invalid.length&&!row.conflicts&&!row.selected.archived);
        if(!target||view.context.mode!=='federated')throw Error('Choose an actual verified shared Yjs document for this passage.');
        const {FederatedWorkClient}=await import('./federated-work-api'),reader=new FederatedWorkClient(this.channelId);
        try{const scope=await reader.load();this.current();if(scope.context.room_id!==view.context.room_id||scope.context.connection_id!==view.context.connection_id)throw Error('The anchored document belongs to another room.');const state=await reader.document(target);this.current();const Y=await import('yjs'),{resolveDocumentAnchor}=await import('./document-anchors'),doc=new Y.Doc();
          try{Y.applyUpdate(doc,Uint8Array.from(atob(state.update_base64),char=>char.charCodeAt(0)));const passage=resolveDocumentAnchor(doc,target.id,normalized.anchor);if(normalized.anchor.source_sequence!==state.update_ids.length||!passage||passage.changed)throw Error('The selected document passage changed. Select its verified text again.');}finally{doc.destroy();}
        }finally{reader.stop();}
      }
      for (const reference of references.filter(reference => reference.kind === 'message')) { await verifyEncryptedRoomEvent(this.channelId, reference.id, { requireCurrentParticipant: false }); this.current(); }
      if (kind === 'linked-file' && normalized.target_id?.startsWith('$')) {
        const event = await verifyEncryptedRoomEvent(this.channelId, normalized.target_id, { requireCurrentParticipant: false }); this.current();
        if (!(event.type === 'eu.freecollab.file' && event.content.file && event.content.deleted !== true)
            && !(event.type === 'm.room.message' && ['m.file', 'm.image', 'm.audio', 'm.video'].includes(String(event.content.msgtype)))) throw Error('Choose an actual encrypted room file.');
      }
      if(view.records.some(row=>row.id===id&&row.kind!==kind)) throw Error('This encrypted identifier is already used by another resource kind.');
      if (!previous && known) {
        if (known.kind === kind && !known.invalid.length && !known.conflicts && JSON.stringify(known.selected.fields) === JSON.stringify(normalized)) return known;
        throw Error('This encrypted creation identifier has already been used. Refresh the current item.');
      }
      let parents: string[] = [];
      if (previous) {
        if (!known || known.kind !== kind || known.invalid.length) throw Error('Refresh the current verified encrypted resource.');
        const expected = previous.heads.map(row => row.event_id).sort(), actual = known.heads.map(row => row.event_id).sort();
        if (JSON.stringify(expected) !== JSON.stringify(actual)) throw Error('This encrypted item changed. Refresh before saving.');
        if (known.conflicts && !options.mergeParents) throw Error('Review and explicitly merge competing encrypted versions.');
        parents = options.mergeParents || actual;
        if (!parents.length || parents.length > 8 || parents.some(parent => !actual.includes(parent)) || new Set(parents).size !== parents.length) throw Error('Select current verified versions to merge.');
        if (JSON.stringify(known.selected.fields.origin_run) !== JSON.stringify(normalized.origin_run)) throw Error('The workflow receipt identity is immutable.');
        if(kind==='comment'&&(known.created_by!==view.context.matrix_user_id||JSON.stringify([known.selected.fields.title,known.selected.fields.body,known.selected.fields.target_id,known.selected.fields.target_kind,known.selected.fields.anchor,known.selected.fields.references])!==JSON.stringify([normalized.title,normalized.body,normalized.target_id,normalized.target_kind,normalized.anchor,normalized.references])))throw Error('A comment retains its original author, text, target and anchor. Only its creator may resolve or archive it.');
      }
      if (kind === 'workflow-run' && (normalized.execution_owner !== view.context.matrix_user_id || known && known.created_by !== view.context.matrix_user_id)) throw Error('Only the execution owner may change this encrypted workflow run.');
      this.current(); const event = await this.send(view.context, { id, kind, parents, archived: options.archived ?? previous?.selected.archived ?? false, fields: normalized });
      const result = await this.load(), row = result.records.find(record => record.id === id);
      if (!row || !row.history.some(revision => revision.event_id === event)) throw Error('The encrypted acknowledgement could not be verified.');
      return row;
    } catch (failure) { if(!(failure instanceof EncryptedWorkDraftError))this.usable = false; throw failure; }
  }
  async startWorkflow(template: EncryptedWorkRecord, id = crypto.randomUUID()) {
    const view = await this.load(), current = view.records.find(row => row.id === template.id); this.current();
    if (!current || current.kind !== 'workflow' || current.conflicts || current.invalid.length || current.selected.archived
        || current.selected.event_id !== template.selected.event_id) throw Error('Refresh and review the saved encrypted workflow first.');
    return this.save('workflow-run', { title: current.selected.fields.title, body: current.selected.fields.body,
      workflow_id: current.id, template_event: current.selected.event_id, execution_owner: view.context.matrix_user_id,
      steps: current.selected.fields.steps!, ...(current.selected.fields.references ? { references: current.selected.fields.references } : {}), completed: [], status: 'queued' }, undefined, { id });
  }
  async setRunStatus(run: EncryptedWorkRecord, status: 'paused' | 'cancelled') {
    if (run.kind !== 'workflow-run') throw Error('Choose an encrypted workflow run.');
    return this.save('workflow-run', { ...run.selected.fields, status }, run);
  }
  /** Explicit foreground execution; each step is reauthorized on this trusted device. */
  async resumeWorkflow(run: EncryptedWorkRecord, onStep?: (run: EncryptedWorkRecord) => void) {
    let expected = run;
    for (let pass = 0; pass < 13; pass++) {
      const view = await this.load(); this.current();
      const current = view.records.find(row => row.id === run.id);
      if (!current || current.kind !== 'workflow-run' || current.conflicts || current.invalid.length || current.selected.archived
          || current.created_by !== view.context.matrix_user_id || ['cancelled', 'completed'].includes(current.selected.fields.status!)) return current;
      if (current.selected.event_id !== expected.selected.event_id) throw Error('The encrypted run changed on another device. Review its current state.');
      const template = view.records.find(row => row.id === current.selected.fields.workflow_id);
      if (!template || template.selected.event_id !== current.selected.fields.template_event || template.conflicts || template.invalid.length || template.selected.archived) throw Error('The reviewed workflow changed. Start a newly reviewed run.');
      const step = current.selected.fields.steps!.find(step => !current.selected.fields.completed!.some(receipt => receipt.step_id === step.id));
      if (!step) return this.save('workflow-run', { ...current.selected.fields, status: 'completed' }, current);
      expected = await this.save('workflow-run', { ...current.selected.fields, status: 'running' }, current); onStep?.(expected);
      const operationId = run.id.slice(0, run.id.lastIndexOf(':') + 1) + await deterministicWorkUuid(run.id + '\0' + step.id); this.current();
      const fields = this.stepFields(step, run.id, current.selected.fields);
      const refreshed = await this.load(), latest = refreshed.records.find(row => row.id === run.id); this.current();
      if (!latest || latest.conflicts || latest.invalid.length || latest.selected.event_id !== expected.selected.event_id || latest.selected.fields.status !== 'running') throw Error('The encrypted run was paused, cancelled or changed.');
      const existing = refreshed.records.find(row => row.id === operationId);
      const output = existing && existing.kind === step.kind && existing.selected.fields.origin_run?.id === run.id
        && existing.selected.fields.origin_run.step_id === step.id && !existing.conflicts && !existing.invalid.length
        ? existing : await this.save(step.kind, fields, undefined, { id: operationId });
      const after = await this.load(), next = after.records.find(row => row.id === run.id); this.current();
      if (!next || next.conflicts || next.invalid.length || next.selected.event_id !== expected.selected.event_id || next.selected.fields.status !== 'running') throw Error('The step was acknowledged; refresh the run before continuing.');
      expected = await this.save('workflow-run', { ...next.selected.fields, completed: [...next.selected.fields.completed!, { step_id: step.id, id: output.id }] }, next); onStep?.(expected);
    }
    throw Error('Encrypted workflow exceeded its reviewed step bound.');
  }
  private stepFields(step: EncryptedWorkflowStep, runId: string, run: EncryptedWorkFields): EncryptedWorkFields {
    const references = { ...(run.references?.['step:' + step.id + ':title'] ? { title: run.references['step:' + step.id + ':title'] } : {}),
      ...(run.references?.['step:' + step.id + ':body'] ? { body: run.references['step:' + step.id + ':body'] } : {}) };
    return { title: step.title, body: step.body, origin_run: { id: runId, step_id: step.id }, ...(Object.keys(references).length ? { references } : {}),
      ...(step.kind === 'task' ? { status: 'todo', priority: 'normal' as const, assignee_matrix_id: null, project_id: step.project_id || null, due_date: null, checklist: [], file_ids: [], decision_ids: [] }
        : step.kind === 'decision' ? { status: 'proposed' } : {}) };
  }
}
