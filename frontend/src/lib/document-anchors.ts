import * as Y from 'yjs';

export interface DocumentCommentAnchor { version: 1; document_id: string; field: 'body'; start_relative: string; end_relative: string; quote: string; source_sequence: number }
function encode(bytes: Uint8Array) { return btoa(String.fromCharCode(...bytes)); }
function decode(value: unknown) { if (typeof value !== 'string' || value.length > 4096 || !/^[A-Za-z0-9+/]+={0,2}$/.test(value)) throw new Error('Invalid document comment anchor.'); const bytes = Uint8Array.from(atob(value), char => char.charCodeAt(0)); if (!bytes.length || bytes.length > 2048 || encode(bytes) !== value) throw new Error('Invalid document comment anchor.'); return bytes; }
export function validateDocumentAnchor(value: unknown, documentId: string): DocumentCommentAnchor {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid document comment anchor.');
  const a = value as DocumentCommentAnchor;
  if (Object.keys(a).some(key => !['version', 'document_id', 'field', 'start_relative', 'end_relative', 'quote', 'source_sequence'].includes(key)) || a.version !== 1 || a.document_id !== documentId || a.field !== 'body' || !Number.isInteger(a.source_sequence) || a.source_sequence < 1 || typeof a.quote !== 'string' || !a.quote || a.quote.length > 2000) throw new Error('Invalid document comment anchor.');
  Y.decodeRelativePosition(decode(a.start_relative)); Y.decodeRelativePosition(decode(a.end_relative));
  return a;
}
export function createDocumentAnchor(doc: Y.Doc, documentId: string, sequence: number, start: number, end: number): DocumentCommentAnchor {
  const body = doc.getText('body'), text = body.toString();
  const boundary = (index: number) => !(index > 0 && index < text.length && /[\uD800-\uDBFF]/.test(text[index - 1]) && /[\uDC00-\uDFFF]/.test(text[index]));
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || !boundary(start) || !boundary(end) || end <= start || end > body.length || end - start > 2000 || !Number.isInteger(sequence) || sequence < 1) throw new Error('Select a synchronized passage of at most 2,000 characters.');
  return { version: 1, document_id: documentId, field: 'body', start_relative: encode(Y.encodeRelativePosition(Y.createRelativePositionFromTypeIndex(body, start, 0))), end_relative: encode(Y.encodeRelativePosition(Y.createRelativePositionFromTypeIndex(body, end, -1))), quote: body.toString().slice(start, end), source_sequence: sequence };
}
export function resolveDocumentAnchor(doc: Y.Doc, documentId: string, value: unknown): { start: number; end: number; quote: string; changed: boolean } | null {
  const anchor = validateDocumentAnchor(value, documentId), body = doc.getText('body');
  const start = Y.createAbsolutePositionFromRelativePosition(Y.decodeRelativePosition(decode(anchor.start_relative)), doc), end = Y.createAbsolutePositionFromRelativePosition(Y.decodeRelativePosition(decode(anchor.end_relative)), doc);
  if (!start || !end || start.type !== body || end.type !== body || start.index >= end.index || end.index > body.length) return null;
  const quote = body.toString().slice(start.index, end.index);
  return { start: start.index, end: end.index, quote, changed: quote !== anchor.quote };
}
