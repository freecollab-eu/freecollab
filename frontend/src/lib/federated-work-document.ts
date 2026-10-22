import * as Y from 'yjs';
export interface FederatedDocumentState { title: string; body: string; update_base64: string; update_ids: string[] }
export const encodeWorkUpdate = (bytes: Uint8Array) => { let text = ''; for (let offset = 0; offset < bytes.length; offset += 8192) text += String.fromCharCode(...bytes.subarray(offset, offset + 8192)); return btoa(text); };
export function decodeWorkUpdate(value: unknown, maximum = 256 * 1024, minimum = 2) {
  if (typeof value !== 'string' || value.length > Math.ceil(maximum / 3) * 4 || !/^[A-Za-z0-9+/]+={0,2}$/.test(value)) throw new Error('Invalid encrypted Yjs update.');
  let text: string; try { text = atob(value); } catch { throw new Error('Invalid encrypted Yjs update.'); }
  const result = Uint8Array.from(text, char => char.charCodeAt(0));
  if (result.length < minimum || result.length > maximum || encodeWorkUpdate(result) !== value) throw new Error('Unsupported encrypted Yjs update size or encoding.');
  return result;
}
export function validateWorkDocument(doc: Y.Doc) {
  if ([...doc.share.keys()].some(key => !['title', 'body'].includes(key))) throw new Error('Unsupported shared document structure.');
  if (doc.getText('title').length > 200 || doc.getText('body').length > 50000 || Y.encodeStateAsUpdate(doc).byteLength > 512 * 1024) throw new Error('Shared document exceeds its supported text or state size.');
  for (const key of ['title', 'body']) if (doc.getText(key).toDelta().some((part: { insert: unknown; attributes?: unknown }) => typeof part.insert !== 'string' || part.attributes)) throw new Error('Shared documents support plain text and Markdown.');
}
export function reconstructWorkDocument(initial: string, updates: Array<{ id: string; update_base64: string }>): FederatedDocumentState {
  if (updates.length > 500) throw new Error('This document exceeds the supported update count.');
  const doc = new Y.Doc();
  try {
    Y.applyUpdate(doc, decodeWorkUpdate(initial, 3072)); validateWorkDocument(doc);
    if (doc.getText('body').length) throw new Error('A document creation state must begin with an empty body.');
    for (const update of updates) { Y.applyUpdate(doc, decodeWorkUpdate(update.update_base64)); validateWorkDocument(doc); }
    return { title: doc.getText('title').toString(), body: doc.getText('body').toString(), update_base64: encodeWorkUpdate(Y.encodeStateAsUpdate(doc)), update_ids: updates.map(update => update.id) };
  } finally { doc.destroy(); }
}
export function replaceWorkText(doc: Y.Doc, field: 'title' | 'body', value: string) {
  const text = doc.getText(field), previous = text.toString();
  let prefix = 0, suffix = 0;
  while (prefix < value.length && prefix < previous.length && value[prefix] === previous[prefix]) prefix++;
  while (suffix < value.length - prefix && suffix < previous.length - prefix && value[value.length - 1 - suffix] === previous[previous.length - 1 - suffix]) suffix++;
  doc.transact(() => { const removed = previous.length - prefix - suffix; if (removed) text.delete(prefix, removed); const added = value.slice(prefix, value.length - suffix); if (added) text.insert(prefix, added); });
}
