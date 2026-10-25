/** Global encrypted-room references are distinct from installation-local IDs. */
export interface EncryptedReference { kind: 'participant' | 'resource' | 'message'; id: string; label: string; start: number; end: number; resource_kind?: string }
export type EncryptedFieldReferences = Record<string, EncryptedReference[]>;
const REFERENCE_KINDS = ['collection', 'feed', 'decision', 'linked-file', 'comment', 'workflow', 'workflow-run', 'task', 'project', 'organization', 'document', 'calendar'];
const object = (value: unknown): Record<string, unknown> => { if (!value || typeof value !== 'object' || Array.isArray(value)) throw Error('Invalid encrypted reference object.'); return value as Record<string, unknown>; };
const exact = (value: Record<string, unknown>, keys: string[]) => { if (Object.keys(value).some(key => !keys.includes(key))) throw Error('Unsupported encrypted reference fields.'); };
const text = (value: unknown, limit: number, required = false): string => { if (typeof value !== 'string' || value.length > limit || required && !value.trim()) throw Error('Invalid encrypted reference text.'); return value; };
const identifier = (value: unknown) => { const result = text(value, 1024, true); if (/[\u0000-\u001f\u007f]/.test(result)) throw Error('Invalid encrypted reference identity.'); return result; };
const matrixIdentity = (value: string) => value.length <= 512 && /^@fc_[A-Za-z0-9_-]{1,128}:[a-z0-9][a-z0-9.-]{1,251}$/.test(value);
const eventId = (value: string) => value.startsWith('$') && value.length <= 1024;
const resourceIdValid = (value: string, connection: string) => /^[a-f0-9]{32}$/.test(connection) && value.startsWith(connection + ':') && /^[a-f0-9]{16}:[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/.test(value.slice(connection.length + 1));
export function rebaseEncryptedReferences(before: string, after: string, references: EncryptedReference[]) {
  let start = 0, suffix = 0;
  while (start < before.length && start < after.length && before[start] === after[start]) start++;
  while (suffix < before.length - start && suffix < after.length - start && before[before.length - suffix - 1] === after[after.length - suffix - 1]) suffix++;
  const end = before.length - suffix, delta = after.length - before.length;
  const matches = (reference: EncryptedReference) => after.slice(reference.start, reference.end) === '@' + reference.label
    && !(reference.start > 0 && /[\p{L}\p{N}\p{M}_@]/u.test(after[reference.start - 1]))
    && !(reference.end < after.length && /[\p{L}\p{N}\p{M}_]/u.test(after[reference.end]));
  return references.flatMap(reference => {
    if (reference.end <= start) return matches(reference) ? [{ ...reference }] : [];
    if (reference.start >= end) { const next = { ...reference, start: reference.start + delta, end: reference.end + delta }; return matches(next) ? [next] : []; }
    return [];
  });
}
export function encryptedReferenceTexts(fields: Record<string, unknown>): Record<string,string> {
  const result: Record<string,string> = {};
  for(const key of ['title','body','description','location']) if(typeof fields[key] === 'string') result[key]=fields[key] as string;
  if(Array.isArray(fields.checklist)) for(const item of fields.checklist) if(item && typeof item.id==='string' && typeof item.title==='string') result['checklist:'+item.id]=item.title;
  if(Array.isArray(fields.steps)) for(const step of fields.steps) if(step && typeof step.id==='string') for(const key of ['title','body']) if(typeof step[key]==='string') result['step:'+step.id+':'+key]=step[key];
  return result;
}
/** Only exact rebased spans from verified prior content retain historical access. */
export function addedEncryptedReferences(before: Record<string,string>, oldReferences: EncryptedFieldReferences | undefined, after: Record<string,string>, references: EncryptedFieldReferences | undefined): EncryptedFieldReferences | undefined {
  validateEncryptedReferences(before,oldReferences);
  const result: EncryptedFieldReferences = {};
  const signature=(row:EncryptedReference)=>JSON.stringify([row.kind,row.id,row.label,row.start,row.end,row.resource_kind || null]);
  for(const [field, rows] of Object.entries(references || {})) {
    const remaining = rebaseEncryptedReferences(before[field] || '',after[field] || '',oldReferences?.[field] || []).map(signature);
    const added = rows.filter(row=>{const index=remaining.indexOf(signature(row));if(index<0)return true;remaining.splice(index,1);return false;});
    if(added.length) result[field]=added;
  }
  return Object.keys(result).length?result:undefined;
}
export function validateEncryptedReferences(texts: Record<string, string>, raw: unknown, connection?: string): EncryptedFieldReferences | undefined {
  if (raw === undefined) return undefined;
  const input = object(raw), result: EncryptedFieldReferences = {};
  if (Object.keys(input).length > 80) throw Error('Too many encrypted reference fields.');
  for (const [field, values] of Object.entries(input)) {
    if (!Object.prototype.hasOwnProperty.call(texts,field) || !Array.isArray(values) || values.length > 50) throw Error('Invalid encrypted reference field.');
    let end = 0;
    result[field] = values.map(raw => {
      const row = object(raw); exact(row, ['kind', 'id', 'label', 'start', 'end', 'resource_kind']);
      if (!['participant', 'resource', 'message'].includes(String(row.kind)) || !Number.isSafeInteger(row.start) || !Number.isSafeInteger(row.end)
          || Number(row.start) < end || Number(row.end) <= Number(row.start) || Number(row.end) > texts[field].length) throw Error('Invalid encrypted reference span.');
      const id = identifier(row.id), label = text(row.label, 200, true);
      if (texts[field].slice(Number(row.start), Number(row.end)) !== '@' + label
          || Number(row.start) > 0 && /[\p{L}\p{N}\p{M}_@]/u.test(texts[field][Number(row.start) - 1])
          || Number(row.end) < texts[field].length && /[\p{L}\p{N}\p{M}_]/u.test(texts[field][Number(row.end)])) throw Error('The encrypted reference no longer matches its selected text.');
      if (row.kind === 'participant' && !matrixIdentity(id) || row.kind === 'message' && !eventId(id)
          || row.kind === 'resource' && !resourceIdValid(id, connection || id.split(':')[0])) throw Error('Use a verified room identity for this reference.');
      if (row.resource_kind !== undefined && (row.kind !== 'resource' || !REFERENCE_KINDS.includes(String(row.resource_kind)))) throw Error('Invalid encrypted resource reference kind.');
      if (row.kind === 'resource' && !REFERENCE_KINDS.includes(String(row.resource_kind))) throw Error('Choose the exact encrypted resource kind.');
      end = Number(row.end);
      return { kind: row.kind as EncryptedReference['kind'], id, label, start: Number(row.start), end,
        ...(row.resource_kind ? { resource_kind: String(row.resource_kind) } : {}) };
    });
    if (!result[field].length) delete result[field];
  }
  return Object.keys(result).length ? result : undefined;
}
