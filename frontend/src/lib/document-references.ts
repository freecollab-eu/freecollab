import * as Y from 'yjs';
import { normalizeMessageReferences, validateMessageReferences, type MessageReference, type ReferenceKind } from './message-references';

/** IDs and labels travel with the encrypted Yjs state, never a separate index. */
export const DOCUMENT_REFERENCE_MAP = 'references';
const MAX_STORED = 1024;
const MAX_VISIBLE = 50;
type Field = 'title' | 'body';
interface AnchoredReference { version: 1; field: Field; kind: ReferenceKind; id: string; label: string; start: string; end: string }
interface LocatedReference { key: string; record: AnchoredReference; reference: MessageReference }
const kinds = new Set(['person', 'group', 'document', 'task', 'file', 'channel']);
const identifier = (value: unknown): value is string => typeof value === 'string' && value.length > 0 && value.length <= 128 && !/[\s\u0000-\u001f\u007f]/.test(value);

function encode(position: Y.RelativePosition): string {
  return btoa(String.fromCharCode(...Y.encodeRelativePosition(position)));
}
function decode(value: unknown): Y.RelativePosition {
  if (typeof value !== 'string' || value.length > 1024 || !/^[A-Za-z0-9+/]+={0,2}$/.test(value)) throw new Error('Invalid document reference anchor.');
  const binary = atob(value), bytes = Uint8Array.from(binary, character => character.charCodeAt(0));
  if (btoa(binary) !== value) throw new Error('Invalid document reference anchor.');
  return Y.decodeRelativePosition(bytes);
}
function record(value: unknown): AnchoredReference {
  if (typeof value !== 'string' || value.length > 4096) throw new Error('Invalid document references.');
  try { value = JSON.parse(value); } catch { throw new Error('Invalid document references.'); }
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid document references.');
  const item = value as AnchoredReference;
  if (Object.keys(item).sort().join(',') !== 'end,field,id,kind,label,start,version' || item.version !== 1 || !['title', 'body'].includes(item.field)
      || !kinds.has(item.kind) || !identifier(item.id) || typeof item.label !== 'string' || !item.label.startsWith('@')
      || item.label.length < 2 || item.label.length > 201 || /[\r\n\u2028\u2029\u0000-\u001f\u007f]/.test(item.label)) throw new Error('Invalid document references.');
  decode(item.start); decode(item.end);
  return item;
}

/** Legacy documents need only title/body. Reject arbitrary new shared types. */
export function validateDocumentReferences(doc: Y.Doc): void {
  if (!doc.share.has(DOCUMENT_REFERENCE_MAP)) return;
  const map = doc.getMap<string>(DOCUMENT_REFERENCE_MAP);
  if (!(map instanceof Y.Map) || map.size > MAX_STORED) throw new Error('This document exceeds its supported reference limit.');
  for (const [key, value] of map.entries()) {
    if (!identifier(key)) throw new Error('Invalid document reference identifier.');
    record(value);
  }
}
function locate(doc: Y.Doc, key: string, value: unknown, field: Field): LocatedReference | null {
  try {
    const item = record(value);
    if (item.field !== field) return null;
    const text = doc.getText(field), start = Y.createAbsolutePositionFromRelativePosition(decode(item.start), doc), end = Y.createAbsolutePositionFromRelativePosition(decode(item.end), doc);
    if (!start || !end || start.type !== text || end.type !== text || start.index >= end.index || text.toString().slice(start.index, end.index) !== item.label) return null;
    const reference = { kind: item.kind, id: item.id, start: start.index, end: end.index };
    if (!normalizeMessageReferences(text.toString(), [reference]).length) return null;
    return { key, record: item, reference };
  } catch { return null; }
}
function located(doc: Y.Doc, field: Field): LocatedReference[] {
  if (!doc.share.has(DOCUMENT_REFERENCE_MAP)) return [];
  const map = doc.getMap<string>(DOCUMENT_REFERENCE_MAP);
  return [...map.entries()].map(([key, value]) => locate(doc, key, value, field)).filter((item): item is LocatedReference => !!item)
    .sort((a, b) => a.reference.start - b.reference.start || a.reference.end - b.reference.end || a.key.localeCompare(b.key));
}

/** Edited labels and conflicting concurrent identities render as ordinary text. */
export function readDocumentReferences(doc: Y.Doc, field: Field = 'body', maximum = MAX_VISIBLE): MessageReference[] {
  const values = located(doc, field), result: MessageReference[] = [];
  for (const item of values) {
    const ref = item.reference;
    if (values.some(other => other !== item && other.reference.start < ref.end && other.reference.end > ref.start
        && (other.reference.start !== ref.start || other.reference.end !== ref.end || other.reference.kind !== ref.kind || other.reference.id !== ref.id))) continue;
    if (result.some(other => other.start === ref.start && other.end === ref.end)) continue;
    result.push(ref);
    if (result.length >= maximum) break;
  }
  return result;
}

/** Write metadata for an explicit, structurally validated choice after text edit. */
export function writeDocumentReferences(doc: Y.Doc, field: Field, references: MessageReference[]): void {
  const text = doc.getText(field), value = text.toString(), desired = validateMessageReferences(value, references);
  const existing = doc.share.get(DOCUMENT_REFERENCE_MAP);
  if (!existing && !desired.length) return;
  const map = doc.getMap<string>(DOCUMENT_REFERENCE_MAP);
  const matches = new Set<number>();
  for (const [key, raw] of map.entries()) {
    const item = record(raw);
    if (item.field !== field) continue;
    const found = locate(doc, key, raw, field);
    if (!found) { map.delete(key); continue; }
    const ref = found.reference;
    const index = desired.findIndex(candidate => candidate.kind === ref.kind && candidate.id === ref.id && candidate.start === ref.start && candidate.end === ref.end);
    if (index >= 0 && !matches.has(index)) { matches.add(index); continue; }
    // Preserve unrelated references merged from another peer since this
    // editor's render; a replacement at the same range is an explicit choice.
    if (index >= 0 || desired.some(candidate => candidate.start < ref.end && candidate.end > ref.start)) map.delete(key);
  }
  for (let index = 0; index < desired.length; index++) {
    if (matches.has(index)) continue;
    const ref = desired[index];
    if (map.size >= MAX_STORED) throw new Error('This document exceeds its supported reference limit.');
    map.set(crypto.randomUUID(), JSON.stringify({ version: 1, field, kind: ref.kind, id: ref.id, label: value.slice(ref.start, ref.end),
      start: encode(Y.createRelativePositionFromTypeIndex(text, ref.start, 0)), end: encode(Y.createRelativePositionFromTypeIndex(text, ref.end, -1)) }));
  }
}

/** A caller can reject oversized metadata before changing the shared text. */
export function validateDocumentReferenceWrite(doc: Y.Doc, value: string, references: MessageReference[]): MessageReference[] {
  const result = validateMessageReferences(value, references);
  const map = doc.share.get(DOCUMENT_REFERENCE_MAP);
  if (map instanceof Y.Map && map.size + result.length > MAX_STORED) throw new Error('This document exceeds its supported reference limit.');
  return result;
}

/** Rebase an immutable revision's identities onto the newly restored text. */
export function restoreDocumentReferences(doc: Y.Doc, snapshot: Y.Doc): void {
  if (!doc.share.has(DOCUMENT_REFERENCE_MAP) && !snapshot.share.has(DOCUMENT_REFERENCE_MAP)) return;
  const references = (['title', 'body'] as const).map(field => ({ field, rows: readDocumentReferences(snapshot, field, MAX_STORED) }));
  doc.getMap(DOCUMENT_REFERENCE_MAP).clear();
  for (const { field, rows } of references) for (let offset = 0; offset < rows.length; offset += MAX_VISIBLE)
    writeDocumentReferences(doc, field, rows.slice(offset, offset + MAX_VISIBLE));
}
