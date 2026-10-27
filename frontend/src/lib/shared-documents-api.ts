import * as Y from 'yjs';
import { api, APIError } from './api';
import { getSecureMessaging } from './secure-client';
import { reconcileReferences, validateMessageReferences, type MessageReference } from './message-references';
import { getSocket } from './socket';
import { DOCUMENT_REFERENCE_MAP, readDocumentReferences, restoreDocumentReferences, validateDocumentReferences, validateDocumentReferenceWrite, writeDocumentReferences } from './document-references';
import { validateDocumentAnchor, type DocumentCommentAnchor } from './document-anchors';

export interface SharedDocumentRecord {
  id: string; channel_id: string; created_by: string; created_at: string; updated_at: string;
  archived: boolean; version: number; update_count: number; title?: string;
  summary_event_id: string | null; summary_update_id: string | null; summary_by: string | null;
  resolution_error?: string;
  /** Verified peer metadata; must open the actual federated reader, never a native document request. */
  encrypted_resource?: boolean; encrypted_kind?: 'document'; encrypted_source_version?: string;
}
export interface SharedRecord {
  id: string; document_id: string; channel_id: string; created_by: string; created_at: string;
  content_mode: 'server' | 'matrix'; matrix_event_id: string | null; sequence?: number;
  covers_sequence?: number; resolved?: boolean; version?: number;
  fields?: { update_base64?: string; name?: string; text?: string; title?: string | null; references?: MessageReference[]; anchor?: DocumentCommentAnchor };
}
export interface SharedComment extends SharedRecord { fields: { text: string; references?: MessageReference[]; anchor?: DocumentCommentAnchor }; resolved: boolean; version: number }
export interface SharedSnapshot extends SharedRecord { fields: { name: string; update_base64: string }; covers_sequence: number }
interface Page { document: SharedDocumentRecord; items: SharedRecord[]; has_more: boolean; next_sequence: number }
interface PendingUpdate { id: string; bytes: Uint8Array; title: string; eventId?: string; parts?: string[]; sending?: boolean }
export interface DocumentState { title: string; body: string; references?: MessageReference[]; titleReferences?: MessageReference[]; pending: number; ready: boolean; locked: boolean; error: string; sequence: number }

const REMOTE = Symbol('remote-shared-document');
const eventType = (kind: string) => 'eu.freecollab.shared_document.' + kind;
const codePointBoundary = (text: string, index: number) => !(index > 0 && index < text.length && /[\uD800-\uDBFF]/.test(text[index - 1]) && /[\uDC00-\uDFFF]/.test(text[index]));
const segment = encodeURIComponent;
const path = (channelId: string, documentId?: string) => '/channels/' + segment(channelId) + '/shared-documents' + (documentId ? '/' + segment(documentId) : '');
const current = (token: string | null) => { if (!token || token !== api.getAccessToken()) throw new Error('The signed-in account changed.'); };
const trimmedReferences = (text: string, references: MessageReference[]) => {
  const valid = validateMessageReferences(text, references), leading = text.length - text.trimStart().length, end = leading + text.trim().length;
  return valid.filter(reference => reference.start >= leading && reference.end <= end).map(reference => ({ ...reference, start: reference.start - leading, end: reference.end - leading }));
};
const describe = (failure: unknown) => failure instanceof Error ? failure.message : 'Shared document synchronization failed.';
class DocumentIntegrityError extends Error {}

export function encodeDocumentUpdate(update: Uint8Array): string {
  let binary = ''; for (let index = 0; index < update.byteLength; index += 16384) binary += String.fromCharCode(...update.subarray(index, index + 16384));
  return btoa(binary);
}
export function decodeDocumentUpdate(value: unknown, maximum = 256 * 1024, minimum = 2): Uint8Array {
  if (typeof value !== 'string' || value.length > Math.ceil(maximum / 3) * 4 || !/^[A-Za-z0-9+/]+={0,2}$/.test(value)) throw new DocumentIntegrityError('Invalid shared document update.');
  let decoded: string; try { decoded = atob(value); } catch { throw new DocumentIntegrityError('Invalid shared document update.'); }
  if (decoded.length < minimum || decoded.length > maximum) throw new DocumentIntegrityError('Shared document update exceeds its supported size.');
  const result = Uint8Array.from(decoded, char => char.charCodeAt(0));
  if (encodeDocumentUpdate(result) !== value) throw new DocumentIntegrityError('Invalid shared document update encoding.');
  return result;
}

function validateDocument(doc: Y.Doc) {
  if ([...doc.share.keys()].some(key => key !== 'title' && key !== 'body' && key !== DOCUMENT_REFERENCE_MAP)) throw new DocumentIntegrityError('Unsupported shared document structure.');
  if (!(doc.getText('title') instanceof Y.Text) || !(doc.getText('body') instanceof Y.Text)
      || doc.getText('title').length > 200 || doc.getText('body').length > 50000
      || Y.encodeStateAsUpdate(doc).byteLength > 512 * 1024) throw new DocumentIntegrityError('This document exceeds its supported text or state limit.');
  validateDocumentReferences(doc);
  for (const name of ['title', 'body']) if (doc.getText(name).toDelta().some((part: { insert: unknown; attributes?: unknown }) => typeof part.insert !== 'string' || part.attributes))
    throw new DocumentIntegrityError('This document contains unsupported rich content.');
}

function applyChecked(doc: Y.Doc, bytes: Uint8Array) {
  const candidate = new Y.Doc();
  try {
    Y.applyUpdate(candidate, Y.encodeStateAsUpdate(doc));
    Y.applyUpdate(candidate, bytes); validateDocument(candidate);
    Y.applyUpdate(doc, bytes, REMOTE);
  } catch (failure) { throw failure instanceof DocumentIntegrityError ? failure : new DocumentIntegrityError('A shared document update could not be decoded.');
  } finally { candidate.destroy(); }
}

async function sha256(bytes: Uint8Array) {
  const hash = new Uint8Array(await crypto.subtle.digest('SHA-256', new Uint8Array(bytes).buffer));
  return [...hash].map(byte => byte.toString(16).padStart(2, '0')).join('');
}

async function encryptedFields(channelId: string, documentId: string, id: string, kind: 'update' | 'snapshot',
                               fields: { update_base64: string; [key: string]: unknown }, cache: { eventId?: string; parts?: string[] }, owner: string, extra: Record<string, unknown> = {}) {
  current(owner); const secure = await getSecureMessaging(); current(owner);
  if (cache.eventId) return cache.eventId;
  const bytes = decodeDocumentUpdate(fields.update_base64, kind === 'snapshot' ? 512 * 1024 : 256 * 1024);
  let content: Record<string, unknown> = fields;
  if (bytes.length > 16384) {
    const count = Math.ceil(bytes.length / 16384), stateHash = await sha256(bytes); current(owner); cache.parts ||= [];
    for (let index = cache.parts.length; index < count; index++) {
      current(owner);
      const event = await secure.sendEvent(channelId, eventType(kind + '_part'), { id, document_id: documentId,
        index, count, state_hash: stateHash, part_base64: encodeDocumentUpdate(bytes.slice(index * 16384, (index + 1) * 16384)) }, id + '-part-' + index);
      current(owner); cache.parts.push(event.event_id);
    }
    content = { ...fields, parts: cache.parts, byte_size: bytes.length, state_hash: stateHash };
    delete content.update_base64;
  }
  current(owner); const event = await secure.sendEvent(channelId, eventType(kind), { id, document_id: documentId, fields: content, ...extra }, id);
  current(owner); cache.eventId = event.event_id; return event.event_id;
}

async function verifiedContent(record: SharedRecord, kind: 'update' | 'snapshot' | 'comment', summaryOnly = false) {
  if (record.content_mode === 'server') {
    if (!record.fields || typeof record.fields !== 'object') throw new DocumentIntegrityError('Shared document content is unavailable.');
    return record.fields;
  }
  if (!record.matrix_event_id) throw new DocumentIntegrityError('Encrypted document reference is unavailable.');
  const secure = await getSecureMessaging(), event = await secure.getDecryptedEvent(record.channel_id, record.matrix_event_id);
  const { c, roomId } = await secure.roomFor(record.channel_id);
  const content = event.getContent(), domain = c.getUserId()!.split(':').slice(1).join(':');
  if (event.getRoomId() !== roomId || event.getType() !== eventType(kind) || event.isRedacted()
      || event.getSender() !== `@fc_${record.created_by}:${domain}` || content.document_id !== record.document_id
      || content.id !== record.id || (kind === 'snapshot' && content.covers_sequence !== record.covers_sequence))
    throw new DocumentIntegrityError('This encrypted document entry could not be verified.');
  const fields = content.fields;
  if (!fields || typeof fields !== 'object' || Array.isArray(fields)) throw new DocumentIntegrityError('Invalid encrypted document fields.');
  if (!summaryOnly && fields.parts !== undefined && kind !== 'comment') {
    const maximum = kind === 'snapshot' ? 512 * 1024 : 256 * 1024;
    if (!Array.isArray(fields.parts) || fields.parts.length < 2 || fields.parts.length > 32 || new Set(fields.parts).size !== fields.parts.length
        || !fields.parts.every((part: unknown) => typeof part === 'string' && part.length <= 1024)
        || !Number.isInteger(fields.byte_size) || fields.byte_size < 16385 || fields.byte_size > maximum
        || typeof fields.state_hash !== 'string' || !/^[a-f0-9]{64}$/.test(fields.state_hash)) throw new DocumentIntegrityError('Invalid encrypted document parts.');
    const bytes = new Uint8Array(fields.byte_size); let offset = 0;
    for (let index = 0; index < fields.parts.length; index++) {
      const part = await secure.getDecryptedEvent(record.channel_id, fields.parts[index]);
      const payload = part.getContent();
      if (part.getRoomId() !== roomId || part.getType() !== eventType(kind + '_part') || part.isRedacted()
          || part.getSender() !== event.getSender() || payload.id !== record.id || payload.document_id !== record.document_id
          || payload.index !== index || payload.count !== fields.parts.length || payload.state_hash !== fields.state_hash
          || typeof payload.part_base64 !== 'string') throw new DocumentIntegrityError('An encrypted document part could not be verified.');
      const partBytes = decodeDocumentUpdate(payload.part_base64, 16384, 1);
      if (offset + partBytes.length > bytes.length || index < fields.parts.length - 1 && partBytes.length !== 16384) throw new DocumentIntegrityError('Invalid encrypted document part size.');
      bytes.set(partBytes, offset); offset += partBytes.length;
    }
    if (offset !== bytes.length || await sha256(bytes) !== fields.state_hash) throw new DocumentIntegrityError('Encrypted document parts do not match their commitment.');
    return { ...fields, update_base64: encodeDocumentUpdate(bytes) } as SharedRecord['fields'];
  }
  return fields as SharedRecord['fields'];
}

async function resolveSummary(row: SharedDocumentRecord): Promise<SharedDocumentRecord> {
  if (!row.summary_event_id) return row;
  try {
    const fields = await verifiedContent({ id: row.summary_update_id!, document_id: row.id, channel_id: row.channel_id,
      created_by: row.summary_by!, created_at: row.updated_at, content_mode: 'matrix', matrix_event_id: row.summary_event_id }, 'update', true);
    if (typeof fields?.title !== 'string' || fields.title.length > 200) throw new Error('The encrypted document title is unavailable.');
    return { ...row, title: fields.title || 'Untitled document' };
  } catch (failure) { return { ...row, title: undefined, resolution_error: describe(failure) }; }
}

async function writeUpdate(channelId: string, documentId: string, item: PendingUpdate, token: string, creating = false): Promise<SharedRecord | SharedDocumentRecord> {
  current(token);
  const secure = await getSecureMessaging(), encrypted = await secure.handles(channelId); current(token);
  const fields = { update_base64: encodeDocumentUpdate(item.bytes), title: item.title || 'Untitled document' };
  let body: Record<string, unknown> = { update_id: item.id, ...fields };
  if (encrypted) {
    if (!item.eventId) {
      item.eventId = await encryptedFields(channelId, documentId, item.id, 'update', fields, item, token);
    }
    current(token); body = { update_id: item.id, matrix_event_id: item.eventId };
  }
  if (creating) body.id = documentId;
  const result = await api.request<SharedRecord | SharedDocumentRecord>(path(channelId, creating ? undefined : documentId) + (creating ? '' : '/updates'), { method: 'POST', body: JSON.stringify(body) });
  current(token); return result;
}

export const sharedDocumentsApi = {
  /** Metadata plus one verified encrypted title per changed document; no Yjs body reads. */
  async encryptedSummaries(channelId: string, since: number, signal?: AbortSignal) {
    const token = api.getAccessToken();
    const check = () => { current(token); if (signal?.aborted) throw new Error('The document summary was cancelled.'); };
    check();
    const rows = await api.request<SharedDocumentRecord[]>(path(channelId) + '?archived=false', { signal }); check();
    let incomplete = rows.length >= 50;
    const candidates = rows.filter(row => row.channel_id === channelId && !row.archived && Date.parse(/(?:z|[+-]\d\d:\d\d)$/i.test(row.updated_at) ? row.updated_at : row.updated_at + 'Z') > since);
    const items: SharedDocumentRecord[] = [];
    // A secure summary never accepts a plaintext title supplied by this endpoint.
    for (const row of candidates.slice(0, 50)) {
      check();
      if (!row.summary_event_id || !row.summary_update_id || !row.summary_by) { incomplete = true; continue; }
      const resolved = await resolveSummary({ ...row, title: undefined }); check();
      if (resolved.resolution_error) incomplete = true; else items.push(resolved);
    }
    return { items, incomplete };
  },
  async list(channelId: string, archived = false) {
    const token = api.getAccessToken(), rows = await api.request<SharedDocumentRecord[]>(path(channelId) + '?archived=' + archived);
    const result = await Promise.all(rows.map(resolveSummary)); current(token); return result;
  },
  async create(channelId: string, title: string, initialBody = '', references: { body?: MessageReference[]; title?: MessageReference[] } = {}) {
    const token = api.getAccessToken(); current(token);
    const doc = new Y.Doc(), identifier = crypto.randomUUID();
    try {
      const cleanTitle = title.trim() || 'Untitled document';
      doc.getText('title').insert(0, cleanTitle); doc.getText('body').insert(0, initialBody);
      writeDocumentReferences(doc, 'title', trimmedReferences(title, references.title || []));
      writeDocumentReferences(doc, 'body', references.body || []); validateDocument(doc);
      const item = { id: crypto.randomUUID(), bytes: Y.encodeStateAsUpdate(doc), title: doc.getText('title').toString() };
      return await writeUpdate(channelId, identifier, item, token!, true) as SharedDocumentRecord;
    } finally { doc.destroy(); }
  },
  async archive(record: SharedDocumentRecord, archived: boolean) {
    return api.request<SharedDocumentRecord>(path(record.channel_id, record.id), { method: 'PATCH', body: JSON.stringify({ expected_version: record.version, archived }) });
  },
  async comments(channelId: string, documentId: string): Promise<SharedComment[]> {
    const token = api.getAccessToken(), rows = await api.request<SharedRecord[]>(path(channelId, documentId) + '/comments');
    const result = await Promise.all(rows.map(async row => {
      if (row.channel_id !== channelId || row.document_id !== documentId) throw new DocumentIntegrityError('A document comment belongs to a different conversation.');
      const fields = await verifiedContent(row, 'comment');
      if (typeof fields?.text !== 'string' || fields.text.length > 2000) throw new Error('A document comment could not be verified.');
      if (fields.references !== undefined) validateMessageReferences(fields.text, fields.references);
      if (fields.anchor !== undefined) validateDocumentAnchor(fields.anchor, documentId);
      return { ...row, fields } as SharedComment;
    })); current(token); return result;
  },
  async comment(channelId: string, documentId: string, text: string, references: MessageReference[] = [], anchor?: DocumentCommentAnchor) {
    const token = api.getAccessToken(); current(token);
    const cleanText = text.trim(), bound = trimmedReferences(text, references);
    const id = crypto.randomUUID(), fields = { text: cleanText, ...(bound.length ? { references: bound } : {}), ...(anchor ? { anchor: validateDocumentAnchor(anchor, documentId) } : {}) };
    if (!fields.text || fields.text.length > 2000) throw new Error('Write a comment of at most 2,000 characters.');
    const secure = await getSecureMessaging(), encrypted = await secure.handles(channelId); current(token);
    let body: Record<string, unknown> = { id, ...fields };
    if (encrypted) { const event = await secure.sendEvent(channelId, eventType('comment'), { id, document_id: documentId, fields }); current(token); body = { id, matrix_event_id: event.event_id }; }
    await api.request(path(channelId, documentId) + '/comments', { method: 'POST', body: JSON.stringify(body) }); current(token);
  },
  async resolveComment(channelId: string, documentId: string, row: SharedComment) {
    await api.request(path(channelId, documentId) + '/comments/' + segment(row.id), { method: 'PATCH', body: JSON.stringify({ expected_version: row.version, resolved: !row.resolved }) });
  },
  async snapshots(channelId: string, documentId: string): Promise<SharedSnapshot[]> {
    const token = api.getAccessToken(), rows = await api.request<SharedRecord[]>(path(channelId, documentId) + '/snapshots');
    const result = await Promise.all(rows.map(async row => {
      const fields = await verifiedContent(row, 'snapshot');
      if (typeof fields?.name !== 'string' || fields.name.length > 100 || typeof fields.update_base64 !== 'string') throw new Error('A saved document revision could not be verified.');
      decodeDocumentUpdate(fields.update_base64, 512 * 1024);
      return { ...row, fields } as SharedSnapshot;
    })); current(token); return result;
  },
};

/** Read-only, in-memory reconstruction for local search; no query is uploaded. */
export async function readSharedDocumentForSearch(channelId: string, documentId: string, signal?: AbortSignal) {
  const token = api.getAccessToken(), doc = new Y.Doc(); let sequence = 0, metadata: SharedDocumentRecord | undefined;
  try {
    for (let pageNumber = 0; pageNumber < 50; pageNumber++) {
      current(token); if (signal?.aborted) throw new DOMException('Cancelled', 'AbortError');
      const page = await api.request<Page>(path(channelId, documentId) + '/updates?after_sequence=' + sequence + '&limit=200', { signal });
      current(token); metadata = page.document;
      if (metadata.id !== documentId || metadata.channel_id !== channelId) throw new DocumentIntegrityError('This document belongs to a different conversation.');
      if (metadata.archived) throw new Error('This document was archived.');
      for (const row of page.items) {
        if (row.document_id !== documentId || row.channel_id !== channelId || row.sequence !== sequence + 1) throw new DocumentIntegrityError('Shared document history has a sequence gap.');
        const fields = await verifiedContent(row, 'update'); current(token);
        if (signal?.aborted) throw new DOMException('Cancelled', 'AbortError');
        applyChecked(doc, decodeDocumentUpdate(fields?.update_base64)); sequence = row.sequence!;
      }
      if (!page.has_more) {
        if (sequence !== metadata.update_count) throw new DocumentIntegrityError('Shared document changed during search. Refresh the search.');
        return { document: metadata, sequence, title: doc.getText('title').toString(), text: doc.getText('body').toString(), references: readDocumentReferences(doc), titleReferences: readDocumentReferences(doc, 'title') };
      }
      if (!page.items.length) throw new DocumentIntegrityError('Shared document pagination did not advance.');
    }
    throw new Error('This document exceeds the local search synchronization limit.');
  } finally { doc.destroy(); }
}

export class SharedDocumentSession {
  readonly doc = new Y.Doc();
  private readonly owner = api.getAccessToken();
  private readonly actor = api.getCurrentUserId();
  private listeners = new Set<(state: DocumentState) => void>();
  private pending: PendingUpdate[] = [];
  private stopped = false;
  private activeSync: Promise<void> | undefined;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private started = false;
  private idlePasses = 0;
  private removeWake: (() => void) | undefined;
  private sequence = 0;
  private ready = false;
  private locked = false;
  private error = '';
  private known = new Set<string>();
  metadata: SharedDocumentRecord;
  constructor(readonly channelId: string, record: SharedDocumentRecord) {
    if (record.channel_id !== channelId) throw new DocumentIntegrityError('This document belongs to a different conversation.');
    this.metadata = record; this.doc.getText('title'); this.doc.getText('body');
    this.doc.on('update', (bytes: Uint8Array, origin: unknown) => {
      if (origin !== REMOTE && !this.stopped) {
        this.idlePasses = 0;
        const title = this.doc.getText('title').toString() || 'Untitled document', last = this.pending[this.pending.length - 1];
        const merged = last && !last.sending && !last.eventId && !last.parts ? Y.mergeUpdates([last.bytes, bytes]) : undefined;
        if (last && merged && merged.byteLength <= 256 * 1024) { last.bytes = merged; last.title = title; }
        else this.pending.push({ id: crypto.randomUUID(), bytes: new Uint8Array(bytes), title });
        this.schedule(250);
      }
      this.emit();
    });
  }
  private assertCurrent() { current(this.owner); if (this.actor !== api.getCurrentUserId()) throw new Error('The signed-in account changed.'); if (this.stopped) throw new Error('This document session was closed.'); }
  state(): DocumentState { return { title: this.doc.getText('title').toString(), body: this.doc.getText('body').toString(), references: readDocumentReferences(this.doc), titleReferences: readDocumentReferences(this.doc, 'title'), pending: this.pending.length, ready: this.ready, locked: this.locked || this.metadata.archived, error: this.error, sequence: this.sequence }; }
  subscribe(listener: (state: DocumentState) => void) { this.listeners.add(listener); listener(this.state()); return () => { this.listeners.delete(listener); }; }
  private emit() { if (!this.stopped) for (const listener of this.listeners) listener(this.state()); }
  private schedule(delay = 1000) { if (this.timer) clearTimeout(this.timer); if (!this.stopped && !this.locked) this.timer = setTimeout(() => { void this.sync(); }, delay); }
  start() {
    if (this.started || this.stopped) return;
    this.started = true;
    const socket = getSocket();
    const changed = (hint: { channel_id?: string; document_id?: string; part?: string }) => {
      if (hint?.channel_id === this.channelId && hint.document_id === this.metadata.id && hint.part !== 'comments') this.wake();
    };
    const wake = () => this.wake();
    socket.on('shared_document_changed', changed); window.addEventListener('focus', wake); window.addEventListener('online', wake);
    this.removeWake = () => { socket.off('shared_document_changed', changed); window.removeEventListener('focus', wake); window.removeEventListener('online', wake); };
    void this.sync();
  }
  /** Hints carry no document content; every wake rechecks the authorized history. */
  wake() { if (!this.stopped && !this.locked && this.owner === api.getAccessToken() && this.actor === api.getCurrentUserId()) { this.idlePasses = 0; this.schedule(0); } }
  stop() { if (this.stopped) return; this.stopped = true; this.removeWake?.(); if (this.timer) clearTimeout(this.timer); this.listeners.clear(); this.pending = []; this.doc.destroy(); }
  replace(field: 'title' | 'body', value: string, references?: MessageReference[]) {
    this.assertCurrent(); if (!this.ready || this.locked || this.metadata.archived) throw new Error('Wait for a verified, writable document before editing.');
    if (value.length > (field === 'title' ? 200 : 50000)) throw new Error('This text exceeds the document limit.');
    const text = this.doc.getText(field), previous = text.toString();
    const bound = validateDocumentReferenceWrite(this.doc, value, references === undefined ? reconcileReferences(previous, value, readDocumentReferences(this.doc, field)) : references);
    if (value === previous && references === undefined) return;
    let prefix = 0, suffix = 0;
    while (prefix < value.length && prefix < previous.length && value[prefix] === previous[prefix]) prefix++;
    // Y.Text must never delete/insert between a surrogate pair, even when two
    // different emoji share their first or last UTF-16 code unit.
    while (prefix && (!codePointBoundary(previous, prefix) || !codePointBoundary(value, prefix))) prefix--;
    while (suffix < value.length - prefix && suffix < previous.length - prefix && value[value.length - 1 - suffix] === previous[previous.length - 1 - suffix]) suffix++;
    while (suffix && (!codePointBoundary(previous, previous.length - suffix) || !codePointBoundary(value, value.length - suffix))) suffix--;
    this.doc.transact(() => { const removed = previous.length - prefix - suffix; if (removed) text.delete(prefix, removed); const added = value.slice(prefix, value.length - suffix); if (added) text.insert(prefix, added); writeDocumentReferences(this.doc, field, bound); });
  }
  async sync() {
    if (this.activeSync) return this.activeSync;
    const pending = this.synchronize(); this.activeSync = pending;
    try { await pending; } finally { if (this.activeSync === pending) this.activeSync = undefined; }
  }
  private async synchronize() {
    if (this.stopped || this.locked) return;
    const previousSequence = this.sequence, hadPending = !!this.pending.length;
    try {
      this.assertCurrent();
      let more = true, pages = 0;
      while (more) {
        if (++pages > 100) throw new Error('This document needs another synchronization pass.');
        const page = await api.request<Page>(path(this.channelId, this.metadata.id) + '/updates?after_sequence=' + this.sequence + '&limit=200'); this.assertCurrent();
        if (page.document.id !== this.metadata.id || page.document.channel_id !== this.channelId) throw new DocumentIntegrityError('This document history belongs to a different conversation.');
        this.metadata = page.document;
        for (const row of page.items) {
          if (row.channel_id !== this.channelId || row.document_id !== this.metadata.id) throw new DocumentIntegrityError('This document edit belongs to a different conversation.');
          if (row.sequence !== this.sequence + 1) throw new DocumentIntegrityError('Shared document history has a sequence gap.');
          if (!this.known.has(row.id)) {
            const fields = await verifiedContent(row, 'update'); this.assertCurrent();
            const bytes = decodeDocumentUpdate(fields?.update_base64); applyChecked(this.doc, bytes); this.known.add(row.id);
          }
          this.sequence = row.sequence;
        }
        more = page.has_more;
      }
      this.ready = true; this.error = '';
      while (this.pending.length && !this.metadata.archived) {
        const item = this.pending[0]; item.sending = true; await writeUpdate(this.channelId, this.metadata.id, item, this.owner!); this.assertCurrent();
        this.known.add(item.id); this.pending.shift();
      }
      if (this.metadata.archived && this.pending.length) this.error = 'This document was archived; unsent edits remain only in this open browser.';
    } catch (failure) {
      if (this.stopped || this.owner !== api.getAccessToken() || this.actor !== api.getCurrentUserId()) return;
      this.error = describe(failure);
      if ((failure instanceof APIError && [401, 403, 404].includes(failure.status)) || failure instanceof DocumentIntegrityError) this.locked = true;
    } finally {
      if (this.owner !== api.getAccessToken() || this.actor !== api.getCurrentUserId()) { this.stop(); return; }
      this.idlePasses = hadPending || this.sequence !== previousSequence || this.pending.length ? 0 : this.idlePasses + 1;
      this.emit(); this.schedule(this.pending.length ? 250 : [1000, 1000, 3000, 5000, 10000, 15000][Math.min(this.idlePasses, 5)]);
    }
  }
  async saveSnapshot(name: string) {
    this.assertCurrent(); await this.sync(); this.assertCurrent();
    if (this.pending.length || !this.ready || this.locked || this.metadata.archived) throw new Error('Synchronize all edits before saving a revision.');
    // A POST acknowledgement need not be the latest contiguous history cursor.
    await this.sync(); this.assertCurrent();
    if (this.pending.length || !this.ready || this.locked || this.metadata.archived) throw new Error('Synchronize all edits before saving a revision.');
    const coversSequence = this.sequence, id = crypto.randomUUID(), update = Y.encodeStateAsUpdate(this.doc), fields = { name: name.trim(), update_base64: encodeDocumentUpdate(update) };
    if (!fields.name || fields.name.length > 100 || update.byteLength > 512 * 1024) throw new Error('Use a revision name of at most 100 characters and a supported document size.');
    const secure = await getSecureMessaging(), encrypted = await secure.handles(this.channelId); this.assertCurrent();
    let body: Record<string, unknown> = { id, covers_sequence: coversSequence, ...fields };
    if (encrypted) { const eventId = await encryptedFields(this.channelId, this.metadata.id, id, 'snapshot', fields, {}, this.owner!, { covers_sequence: coversSequence }); this.assertCurrent(); body = { id, covers_sequence: coversSequence, matrix_event_id: eventId }; }
    await api.request(path(this.channelId, this.metadata.id) + '/snapshots', { method: 'POST', body: JSON.stringify(body) }); this.assertCurrent();
  }
  restoreSnapshot(snapshot: SharedSnapshot) {
    this.assertCurrent(); const old = new Y.Doc();
    try { Y.applyUpdate(old, decodeDocumentUpdate(snapshot.fields.update_base64, 512 * 1024)); validateDocument(old); this.doc.transact(() => { this.replace('title', old.getText('title').toString(), []); this.replace('body', old.getText('body').toString(), []); restoreDocumentReferences(this.doc, old); }); }
    finally { old.destroy(); }
  }
}

// A retained surface may reveal the same page again. Keep one CRDT writer and
// poller for that exact signed-in actor/document, and release it with its last view.
const sharedSessions = new Map<string, { session: SharedDocumentSession; leases: number }>();
let poolWatchesIdentity = false;
export function acquireSharedDocumentSession(channelId: string, record: SharedDocumentRecord) {
  if (!poolWatchesIdentity && typeof window !== 'undefined') {
    poolWatchesIdentity = true;
    window.addEventListener('freecollab:auth-changed', () => { for (const entry of sharedSessions.values()) entry.session.stop(); sharedSessions.clear(); });
  }
  const key = JSON.stringify([api.getCurrentUserId(), api.getAccessToken(), channelId, record.id]);
  let entry = sharedSessions.get(key);
  if (!entry) { entry = { session: new SharedDocumentSession(channelId, record), leases: 0 }; sharedSessions.set(key, entry); }
  entry.leases++;
  let released = false;
  return { session: entry.session, release() {
    if (released) return; released = true;
    if (--entry.leases === 0) { entry.session.stop(); if (sharedSessions.get(key) === entry) sharedSessions.delete(key); }
  } };
}
