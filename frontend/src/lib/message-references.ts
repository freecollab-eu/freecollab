/** Typed references bind an explicit choice to a UTF-16 range in the message. */
export type ReferenceKind = 'person' | 'group' | 'document' | 'task' | 'file' | 'channel';
export interface MessageReference { kind: ReferenceKind; id: string; start: number; end: number }
export interface ReferenceCandidate {
  kind: ReferenceKind; id: string; label: string; description?: string;
  member_count?: number; channel_id?: string; project_id?: string;
}
export interface ResolvedReference extends ReferenceCandidate { available: boolean; user_ids?: string[] }
export interface ReferenceQuery { query: string; start: number; end: number }
export interface ReferenceEdit { start: number; end: number }
export const REFERENCE_KINDS: readonly ReferenceKind[] = ['person', 'group', 'document', 'task', 'file', 'channel'];
const identifier = (value: unknown): value is string => typeof value === 'string' && value.length > 0 && value.length <= 128 && !/[\s\u0000-\u001f\u007f]/.test(value);
const boundary = (text: string, index: number) => !(index > 0 && index < text.length && /[\uD800-\uDBFF]/.test(text[index - 1]) && /[\uDC00-\uDFFF]/.test(text[index]));
const startsMention = (text: string, index: number) => index === 0 || /[\s([{"'“‘]/u.test(text[index - 1]);
const endsMention = (text: string, index: number) => index === text.length || !/[\p{L}\p{N}_]/u.test(text[index]);

export function referenceKey(reference: Pick<MessageReference, 'kind' | 'id'>): string { return reference.kind + ':' + reference.id; }
export function referenceOptionId(listId: string, candidate: Pick<ReferenceCandidate, 'kind' | 'id'>): string { return listId + '-' + candidate.kind + '-' + encodeURIComponent(candidate.id); }

/** Strict validation is for writes. Invalid bindings must not become notifications. */
export function validateMessageReferences(content: string, value: unknown): MessageReference[] {
  if (!Array.isArray(value) || value.length > 50) throw new Error('Invalid message references.');
  const result: MessageReference[] = [];
  for (const candidate of value) {
    if (!candidate || typeof candidate !== 'object' || Array.isArray(candidate)) throw new Error('Invalid message references.');
    const reference = candidate as MessageReference;
    if (!REFERENCE_KINDS.includes(reference.kind) || !identifier(reference.id) || !Number.isSafeInteger(reference.start) || !Number.isSafeInteger(reference.end)
      || reference.start < 0 || reference.end <= reference.start + 1 || reference.end > content.length || reference.end - reference.start > 201
      || !boundary(content, reference.start) || !boundary(content, reference.end) || content[reference.start] !== '@'
      || /[\r\n\u2028\u2029]/.test(content.slice(reference.start, reference.end))
      || !startsMention(content, reference.start) || !endsMention(content, reference.end)) throw new Error('Invalid message references.');
    result.push({ kind: reference.kind, id: reference.id, start: reference.start, end: reference.end });
  }
  result.sort((left, right) => left.start - right.start || left.end - right.end);
  if (result.some((reference, index) => index > 0 && reference.start < result[index - 1].end)) throw new Error('Message references overlap.');
  return result;
}

/** Reading malformed or old metadata falls back to literal text, never guessed identities. */
export function normalizeMessageReferences(content: string, value: unknown): MessageReference[] {
  try { return validateMessageReferences(content, value ?? []); } catch { return []; }
}

/** Find the active @ query at the actual caret, not just the end of the draft. */
export function getReferenceQuery(content: string, caret: number): ReferenceQuery | null {
  if (!Number.isSafeInteger(caret) || caret < 0 || caret > content.length || !boundary(content, caret)) return null;
  const start = content.lastIndexOf('@', caret - 1);
  if (start < 0 || start >= caret || !startsMention(content, start)) return null;
  const query = content.slice(start + 1, caret);
  if (query.length > 80 || /[\r\n\t\u2028\u2029@\[\]{}<>,;:!?]/.test(query)) return null;
  return { query, start, end: caret };
}

/** Preserve untouched bindings and discard edited ones, including ambiguous repeated text. */
export function reconcileReferences(before: string, after: string, references: MessageReference[], edit?: ReferenceEdit): MessageReference[] {
  const valid = normalizeMessageReferences(before, references);
  if (before === after) return valid;
  let start = 0, end = before.length;
  const delta = after.length - before.length;
  const explicit = edit && Number.isSafeInteger(edit.start) && Number.isSafeInteger(edit.end) && edit.start >= 0 && edit.end >= edit.start && edit.end <= before.length
    && boundary(before, edit.start) && boundary(before, edit.end) && edit.end + delta >= edit.start
    && after.startsWith(before.slice(0, edit.start)) && after.endsWith(before.slice(edit.end));
  if (explicit) { start = edit.start; end = edit.end; }
  else {
    const minimum = Math.min(before.length, after.length);
    while (start < minimum && before[start] === after[start]) start++;
    let suffix = 0;
    while (suffix < minimum && before[before.length - suffix - 1] === after[after.length - suffix - 1]) suffix++;
    if (start === before.length) end = before.length;
    else if (suffix === before.length) { start = 0; end = 0; }
    else if (start + suffix > minimum) {
      end = Math.min(before.length, start + Math.max(0, -delta));
      start = Math.max(0, minimum - suffix);
    } else end = before.length - suffix;
  }
  const moved = valid.flatMap(reference => reference.end <= start ? [reference] : reference.start >= end
    ? [{ ...reference, start: reference.start + delta, end: reference.end + delta }] : []);
  // An inserted letter immediately after @Ada makes @AdaX literal text.
  return moved.filter(reference => normalizeMessageReferences(after, [reference]).length === 1);
}

export function insertReference(content: string, references: MessageReference[], query: ReferenceQuery, candidate: ReferenceCandidate): { text: string; references: MessageReference[]; caret: number } {
  if (!REFERENCE_KINDS.includes(candidate.kind) || !identifier(candidate.id) || typeof candidate.label !== 'string' || !candidate.label.trim()
    || candidate.label.length > 200 || /[\r\n\u0000-\u001f\u007f]/.test(candidate.label)
    || getReferenceQuery(content, query.end)?.start !== query.start || content.slice(query.start + 1, query.end) !== query.query) throw new Error('This reference choice is no longer current.');
  const label = '@' + candidate.label.trim();
  const next = content.slice(0, query.start) + label + ' ' + content.slice(query.end);
  const retained = reconcileReferences(content, next, references, { start: query.start, end: query.end });
  const reference = { kind: candidate.kind, id: candidate.id, start: query.start, end: query.start + label.length };
  return { text: next, references: validateMessageReferences(next, [...retained, reference]), caret: reference.end + 1 };
}
