import { normalizeMessageReferences, reconcileReferences, validateMessageReferences, type MessageReference } from './message-references';

/** Explicit identities remain coupled to the exact human-readable field text. */
export type FieldReferences = Record<string, MessageReference[]>;

export function validateFieldReferences(texts: Record<string, string>, value: unknown): FieldReferences | undefined {
  if (value === undefined) return undefined;
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).length > 50) throw new Error('Invalid field references.');
  const result: FieldReferences = {};
  let count = 0;
  for (const [key, references] of Object.entries(value)) {
    if (!Object.prototype.hasOwnProperty.call(texts, key)) throw new Error('This reference has no writing field.');
    const valid = validateMessageReferences(texts[key], references);
    count += valid.length;
    if (count > 50) throw new Error('Keep this item to at most 50 references.');
    if (valid.length) result[key] = valid;
  }
  return Object.keys(result).length ? result : undefined;
}

/** Normal edits retain untouched identities; removed fields cannot keep bindings. */
export function reconcileFieldReferences(before: Record<string, string>, after: Record<string, string>, previous?: FieldReferences, explicit?: FieldReferences): FieldReferences | undefined {
  const result: FieldReferences = {};
  for (const [key, text] of Object.entries(after)) {
    const refs = explicit && Object.prototype.hasOwnProperty.call(explicit, key) ? normalizeMessageReferences(text, explicit[key])
      : reconcileReferences(before[key] || '', text, previous?.[key] || []);
    if (refs.length) result[key] = refs;
  }
  return validateFieldReferences(after, result);
}

export function taskReferenceTexts(fields: { title: string; description: string; checklist?: { id: string; title: string }[] }): Record<string, string> {
  return { title: fields.title, description: fields.description, ...Object.fromEntries((fields.checklist || []).map(item => ['checklist:' + item.id, item.title])) };
}

export function feedReferenceTexts(fields: { title?: string; body: string; poll?: { question: string; options: { id: string; label: string }[] } | null }): Record<string, string> {
  return { ...(fields.title !== undefined ? { title: fields.title } : {}), body: fields.body,
    ...(fields.poll ? { 'poll.question': fields.poll.question, ...Object.fromEntries(fields.poll.options.map(option => ['poll.option:' + option.id, option.label])) } : {}) };
}

export function collectFieldReferences(value?: FieldReferences): MessageReference[] { return Object.values(value || {}).flat(); }

/** Revisions may retain an unchanged historical link after its target disappears. */
export function changedFieldReferences(texts: Record<string, string>, references?: FieldReferences, previousTexts?: Record<string, string>, previous?: FieldReferences): MessageReference[] {
  return Object.entries(references || {}).flatMap(([key, refs]) => texts[key] === previousTexts?.[key] && refs.length === (previous?.[key]?.length || 0) && refs.every((ref, index) => { const old = previous?.[key]?.[index]; return !!old && ref.kind === old.kind && ref.id === old.id && ref.start === old.start && ref.end === old.end; }) ? [] : refs);
}
