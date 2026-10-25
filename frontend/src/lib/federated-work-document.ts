import * as Y from 'yjs';
import { validateEncryptedReferences, type EncryptedFieldReferences, type EncryptedReference } from './encrypted-work-references';
const codePointBoundary = (text: string, index: number) => !(index > 0 && index < text.length && /[\uD800-\uDBFF]/.test(text[index - 1]) && /[\uDC00-\uDFFF]/.test(text[index]));
export interface FederatedDocumentState { title: string; body: string; update_base64: string; update_ids: string[]; references?: EncryptedFieldReferences }
export const encodeWorkUpdate = (bytes: Uint8Array) => { let text = ''; for (let offset = 0; offset < bytes.length; offset += 8192) text += String.fromCharCode(...bytes.subarray(offset, offset + 8192)); return btoa(text); };
export function decodeWorkUpdate(value: unknown, maximum = 256 * 1024, minimum = 2) {
  if (typeof value !== 'string' || value.length > Math.ceil(maximum / 3) * 4 || !/^[A-Za-z0-9+/]+={0,2}$/.test(value)) throw new Error('Invalid encrypted Yjs update.');
  let text: string; try { text = atob(value); } catch { throw new Error('Invalid encrypted Yjs update.'); }
  const result = Uint8Array.from(text, char => char.charCodeAt(0));
  if (result.length < minimum || result.length > maximum || encodeWorkUpdate(result) !== value) throw new Error('Unsupported encrypted Yjs update size or encoding.');
  return result;
}
export function validateWorkDocument(doc: Y.Doc) {
  if ([...doc.share.keys()].some(key => !['title', 'body', 'references'].includes(key))) throw new Error('Unsupported shared document structure.');
  if (doc.getText('title').length > 200 || doc.getText('body').length > 50000 || Y.encodeStateAsUpdate(doc).byteLength > 512 * 1024) throw new Error('Shared document exceeds its supported text or state size.');
  for (const key of ['title', 'body']) if (doc.getText(key).toDelta().some((part: { insert: unknown; attributes?: unknown }) => typeof part.insert !== 'string' || part.attributes)) throw new Error('Shared documents support plain text and Markdown.');
  workDocumentReferences(doc);
}
interface StoredReference { field: 'title' | 'body'; reference: Omit<EncryptedReference, 'start' | 'end'>; start_relative: string; end_relative: string }
function storedReferences(doc: Y.Doc) {
  const map = doc.getMap<string>('references');
  if (map.size > 200) throw Error('Too many shared-document reference anchors.');
  const result: Array<{key: string; field: 'title' | 'body'; reference: EncryptedReference}> = [];
  for (const [key, encoded] of map) {
    if (typeof encoded !== 'string' || encoded.length > 3000) throw Error('Invalid shared-document reference encoding.');
    const raw = JSON.parse(encoded) as StoredReference;
    if (!/^[A-Za-z0-9_-]{1,80}$/.test(key) || !raw || typeof raw !== 'object' || Array.isArray(raw)
        || Object.keys(raw).some(key => !['field', 'reference', 'start_relative', 'end_relative'].includes(key))
        || !['title', 'body'].includes(raw.field) || typeof raw.start_relative !== 'string' || typeof raw.end_relative !== 'string') throw Error('Invalid shared-document reference anchor.');
    const text = doc.getText(raw.field), start = Y.createAbsolutePositionFromRelativePosition(Y.decodeRelativePosition(decodeWorkUpdate(raw.start_relative, 512, 1)), doc),
      end = Y.createAbsolutePositionFromRelativePosition(Y.decodeRelativePosition(decodeWorkUpdate(raw.end_relative, 512, 1)), doc);
    if (!start || !end) continue;
    if (start.type !== text || end.type !== text || start.index >= end.index) continue;
    const reference = { ...raw.reference, start: start.index, end: end.index };
    try { validateEncryptedReferences({ [raw.field]: text.toString() }, { [raw.field]: [reference] }); }
    catch { continue; } // Editing the selected label detaches that historical link.
    result.push({ key, field: raw.field, reference });
  }
  return result;
}
export function workDocumentReferences(doc: Y.Doc): EncryptedFieldReferences | undefined {
  const result: EncryptedFieldReferences = {};
  for (const row of storedReferences(doc).sort((a, b) => a.reference.start - b.reference.start || a.key.localeCompare(b.key))) {
    const values = result[row.field] ||= [];
    if (!values.some(previous => previous.end > row.reference.start && previous.start < row.reference.end)) values.push(row.reference);
  }
  return Object.keys(result).length ? result : undefined;
}
export function setWorkDocumentReferences(doc: Y.Doc, references: EncryptedFieldReferences | undefined, connection?: string) {
  const normalized = validateEncryptedReferences({ title: doc.getText('title').toString(), body: doc.getText('body').toString() }, references, connection) || {};
  const map = doc.getMap<string>('references'), previous = storedReferences(doc), retained = new Set<string>();
  doc.transact(() => {
    for (const field of ['title', 'body'] as const) for (const reference of normalized[field] || []) {
      const same = previous.find(row => !retained.has(row.key) && row.field === field && JSON.stringify(row.reference) === JSON.stringify(reference));
      if (same) { retained.add(same.key); continue; }
      const key = crypto.randomUUID(); retained.add(key);
      const { start, end, ...value } = reference;
      map.set(key, JSON.stringify({ field, reference: value,
        start_relative: encodeWorkUpdate(Y.encodeRelativePosition(Y.createRelativePositionFromTypeIndex(doc.getText(field), start, 0))),
        end_relative: encodeWorkUpdate(Y.encodeRelativePosition(Y.createRelativePositionFromTypeIndex(doc.getText(field), end, -1))) }));
    }
    for (const key of map.keys()) if (!retained.has(key)) map.delete(key);
  });
}
export function reconstructWorkDocument(initial: string, updates: Array<{ id: string; update_base64: string }>): FederatedDocumentState {
  if (updates.length > 500) throw new Error('This document exceeds the supported update count.');
  const doc = new Y.Doc();
  try {
    Y.applyUpdate(doc, decodeWorkUpdate(initial, 16384)); validateWorkDocument(doc);
    if (doc.getText('body').length) throw new Error('A document creation state must begin with an empty body.');
    for (const update of updates) { Y.applyUpdate(doc, decodeWorkUpdate(update.update_base64)); validateWorkDocument(doc); }
    const references = workDocumentReferences(doc);
    return { title: doc.getText('title').toString(), body: doc.getText('body').toString(), update_base64: encodeWorkUpdate(Y.encodeStateAsUpdate(doc)), update_ids: updates.map(update => update.id), ...(references ? { references } : {}) };
  } finally { doc.destroy(); }
}
export function replaceWorkText(doc: Y.Doc, field: 'title' | 'body', value: string) {
  const text = doc.getText(field), previous = text.toString();
  let prefix = 0, suffix = 0;
  while (prefix < value.length && prefix < previous.length && value[prefix] === previous[prefix]) prefix++;
  // Match complete codepoints before mutating Y.Text; splitting a shared high
  // or low surrogate corrupts both the insertion and the retained neighbour.
  while (prefix && (!codePointBoundary(previous, prefix) || !codePointBoundary(value, prefix))) prefix--;
  while (suffix < value.length - prefix && suffix < previous.length - prefix && value[value.length - 1 - suffix] === previous[previous.length - 1 - suffix]) suffix++;
  while (suffix && (!codePointBoundary(previous, previous.length - suffix) || !codePointBoundary(value, value.length - suffix))) suffix--;
  doc.transact(() => { const removed = previous.length - prefix - suffix; if (removed) text.delete(prefix, removed); const added = value.slice(prefix, value.length - suffix); if (added) text.insert(prefix, added); });
}
