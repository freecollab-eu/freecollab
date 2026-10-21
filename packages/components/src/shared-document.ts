import * as Y from 'yjs';
import type {CollaborationClient} from './client.js';
import {SDKError, type SharedDocument} from './types.js';
const REMOTE = Symbol('remote-document');
export function encodeUpdate(bytes: Uint8Array): string { let value = ''; for (let index = 0; index < bytes.length; index += 16384) value += String.fromCharCode(...bytes.subarray(index, index + 16384)); return btoa(value); }
function decode(value: string) {
  if (typeof value !== 'string' || value.length > 350000 || !/^[A-Za-z0-9+/]+={0,2}$/.test(value)) throw new SDKError('invalid-document', 'Invalid shared document update.');
  const raw = atob(value), bytes = Uint8Array.from(raw, char => char.charCodeAt(0));
  if (bytes.length < 2 || bytes.length > 256 * 1024 || encodeUpdate(bytes) !== value) throw new SDKError('invalid-document', 'Unsupported shared document update.'); return bytes;
}
function checked(doc: Y.Doc) {
  if ([...doc.share.keys()].some(key => key !== 'title' && key !== 'body') || doc.getText('title').length > 200 || doc.getText('body').length > 50000 || Y.encodeStateAsUpdate(doc).byteLength > 512 * 1024) throw new SDKError('invalid-document', 'Unsupported document structure or size.');
  for (const key of ['title', 'body']) if (doc.getText(key).toDelta().some((part: {insert: unknown; attributes?: unknown}) => typeof part.insert !== 'string' || part.attributes)) throw new SDKError('invalid-document', 'Unsupported document content.');
}
export function initialDocument(title: string) { const doc = new Y.Doc(); try {if (!title.trim()) throw new SDKError('invalid-content', 'Write a document title.'); doc.getText('title').insert(0, title.trim()); doc.getText('body'); checked(doc); return encodeUpdate(Y.encodeStateAsUpdate(doc));} finally {doc.destroy();} }
export interface DocumentState {title: string; body: string; ready: boolean; pending: number; archived: boolean}
/** Memory-only native Yjs editor. Remote structure is checked in an isolated candidate before applying. */
export class NativeDocumentSession {
  private doc = new Y.Doc(); private sequence = 0; private stopped = false; private ready = false;
  private pending: Array<{id: string; bytes: Uint8Array; title: string}> = [];
  private controller = new AbortController(); private active: Promise<void> | null = null;
  private timer: ReturnType<typeof setTimeout> | undefined; private unsubscribe: () => void;
  constructor(private client: CollaborationClient, private record: SharedDocument, private update: (state: DocumentState) => void, private error: (failure: unknown) => void) {
    this.doc.getText('title'); this.doc.getText('body');
    this.unsubscribe = client.on('state', state => {if (state.status !== 'ready') this.destroy();});
    this.doc.on('update', (bytes: Uint8Array, origin: unknown) => {if (origin !== REMOTE && !this.stopped) this.pending.push({id: crypto.randomUUID(), bytes: new Uint8Array(bytes), title: this.doc.getText('title').toString() || 'Untitled document'});});
  }
  start() {void this.sync();}
  private state() {return {title: this.doc.getText('title').toString(), body: this.doc.getText('body').toString(), ready: this.ready, pending: this.pending.length, archived: this.record.archived};}
  replace(key: 'title' | 'body', value: string) {
    if (this.stopped || !this.ready || this.record.archived) return;
    if (value.length > (key === 'title' ? 200 : 50000)) throw new SDKError('invalid-document', 'The document text is too long.');
    const text = this.doc.getText(key), previous = text.toString(); if (value === previous) return;
    let prefix = 0, suffix = 0; while (prefix < value.length && prefix < previous.length && value[prefix] === previous[prefix]) prefix++;
    while (suffix < value.length - prefix && suffix < previous.length - prefix && value[value.length - 1 - suffix] === previous[previous.length - 1 - suffix]) suffix++;
    this.doc.transact(() => {const removed = previous.length - prefix - suffix; if (removed) text.delete(prefix, removed); const added = value.slice(prefix, value.length - suffix); if (added) text.insert(prefix, added);});
  }
  async sync(): Promise<void> {
    if (this.active) return this.active; if (this.stopped) return;
    const operation = this.synchronize(); this.active = operation; try {await operation;} finally {if (this.active === operation) this.active = null;}
  }
  private async synchronize() {
    clearTimeout(this.timer);
    try {
      let more = true;
      for (let page = 0; more; page++) {
        if (page >= 50) throw new SDKError('document-limit', 'This document needs another synchronization pass.');
        const result = await this.client.documentUpdates(this.record.id, this.sequence, this.controller.signal); if (this.stopped) return;
        this.record = result.document;
        for (const row of result.items) {
          if (row.sequence !== this.sequence + 1) throw new SDKError('invalid-document', 'Document history contains a sequence gap.');
          const candidate = new Y.Doc();
          try {Y.applyUpdate(candidate, Y.encodeStateAsUpdate(this.doc)); const bytes = decode(row.fields.update_base64); Y.applyUpdate(candidate, bytes); checked(candidate); Y.applyUpdate(this.doc, bytes, REMOTE);} catch (failure) {throw failure instanceof SDKError ? failure : new SDKError('invalid-document', 'The shared document history could not be verified.');} finally {candidate.destroy();}
          this.sequence = row.sequence;
        } more = result.has_more;
      }
      this.ready = true;
      while (!this.stopped && this.pending.length && !this.record.archived) {
        const item = this.pending[0]; await this.client.appendDocument(this.record.id, item.title, encodeUpdate(item.bytes), this.controller.signal, item.id); if (this.stopped) return; this.pending.shift();
      }
      if (!this.stopped) this.update(this.state());
    } catch (failure) {if (!this.stopped && !(failure instanceof Error && failure.name === 'AbortError')) {this.error(failure); if (failure instanceof SDKError && ['invalid-document', 'scope-mismatch', 'unauthenticated', 'access-denied'].includes(failure.code)) {this.ready = false; this.update(this.state()); this.destroy();}}}
    finally {if (!this.stopped) this.timer = setTimeout(() => void this.sync(), 2000);}
  }
  destroy() {if (this.stopped) return; this.stopped = true; clearTimeout(this.timer); this.controller.abort(); this.unsubscribe(); this.pending = []; this.doc.destroy();}
}
