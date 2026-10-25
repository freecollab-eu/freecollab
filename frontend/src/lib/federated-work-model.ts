import { validateCalendar, type CalendarFields } from './calendar';
import { validateEncryptedReferences, type EncryptedFieldReferences } from './encrypted-work-references';

export const WORK_PROTOCOL = 'freecollab-federated-work/1';
export const WORK_PROTOCOL_V2 = 'freecollab-federated-work/2';
export const WORK_REVISION = 'eu.freecollab.federated_work.revision';
export const WORK_DOCUMENT_UPDATE = 'eu.freecollab.federated_work.document_update';
export const WORK_DOCUMENT_PART = 'eu.freecollab.federated_work.document_part';
export type SharedWorkKind = 'task' | 'calendar' | 'document';
export interface SharedTaskFields {
  references?: EncryptedFieldReferences;
  title: string; description: string; status: 'todo' | 'doing' | 'done'; priority: 'low' | 'normal' | 'high';
  assignee_matrix_id: string | null; due_date: string | null;
  checklist: Array<{ id: string; title: string; done: boolean }>;
}
export interface SharedDocumentFields { title: string; initial_update_base64: string }
export type SharedCalendarFields = CalendarFields & { references?: EncryptedFieldReferences };
export type SharedWorkFields = SharedTaskFields | SharedCalendarFields | SharedDocumentFields;
export interface WorkContext {
  protocol: string; connection_id: string; channel_id: string; workspace_id: string; room_id: string;
  matrix_user_id: string; local_server: string; peer_server: string;
  members: Array<{ matrix_user_id: string; name: string }>;
  work_protocols?: string[]; room_protocols?: string[]; mode?: string;
  limits: { index_events_per_installation: number; page_size: number; max_document_state_bytes: number };
}
export interface WorkRevision {
  event_id: string; sender: string; timestamp: number; id: string; kind: SharedWorkKind;
  parents: string[]; archived: boolean; fields: SharedWorkFields; protocol?: string;
}
export interface WorkRecord {
  id: string; kind: SharedWorkKind; root_event_id: string; created_by: string; heads: WorkRevision[];
  selected: WorkRevision; history: WorkRevision[]; conflicts: boolean; invalid: string[];
}
export interface VerifiedWorkEvent { event_id: string; sender: string; sender_device_id?: string; timestamp: number; type: string; content: Record<string, unknown> }
export const matrixIdentity = (value: unknown): value is string => typeof value === 'string' && value.length <= 512 && /^@fc_[A-Za-z0-9_-]{1,128}:[a-z0-9][a-z0-9.-]{1,251}$/.test(value);
const object = (value: unknown): Record<string, unknown> => { if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid encrypted shared-work object.'); return value as Record<string, unknown>; };
function exact(value: Record<string, unknown>, keys: string[]) { if (Object.keys(value).some(key => !keys.includes(key))) throw new Error('Unsupported encrypted shared-work fields.'); }
const text = (value: unknown, maximum: number, required = false) => { if (typeof value !== 'string' || value.length > maximum || required && !value.trim()) throw new Error('Invalid encrypted shared-work text.'); return value; };
const eventId = (value: unknown): value is string => typeof value === 'string' && value.startsWith('$') && value.length <= 1024;
export function validateSharedFields(kind: SharedWorkKind, raw: unknown, protocol = WORK_PROTOCOL, connection?: string): SharedWorkFields {
  const fields = object(raw);
  const referencesAllowed = protocol === WORK_PROTOCOL_V2;
  if (![WORK_PROTOCOL, WORK_PROTOCOL_V2].includes(protocol)) throw new Error('Unsupported shared work protocol.');
  if (kind === 'calendar') {
    exact(fields, ['title', 'description', 'location', 'starts_at', 'ends_at', 'all_day', 'timezone', 'reminder_minutes', 'external_uid', 'recurrence', 'exceptions', ...(referencesAllowed ? ['references'] : [])]);
    const normalized = validateCalendar(fields);
    if (normalized.external_uid !== null) throw new Error('Shared-room events do not borrow a local provider identity.');
    const references = validateEncryptedReferences({ title: fields.title as string, description: normalized.description, location: normalized.location }, fields.references, connection);
    return { ...normalized, ...(references?.title ? { title: fields.title as string } : {}), ...(references ? { references } : {}) };
  }
  if (kind === 'document') {
    exact(fields, ['title', 'initial_update_base64']);
    const title = text(fields.title, 200, true), initial_update_base64 = text(fields.initial_update_base64, referencesAllowed ? 22000 : 4096, true);
    if (!/^[A-Za-z0-9+/]+={0,2}$/.test(initial_update_base64)) throw new Error('Invalid shared document initial state.');
    return { title, initial_update_base64 };
  }
  exact(fields, ['title', 'description', 'status', 'priority', 'assignee_matrix_id', 'due_date', 'checklist', ...(referencesAllowed ? ['references'] : [])]);
  if (!['todo', 'doing', 'done'].includes(String(fields.status)) || !['low', 'normal', 'high'].includes(String(fields.priority))) throw new Error('Invalid shared task status.');
  if (fields.assignee_matrix_id !== null && !matrixIdentity(fields.assignee_matrix_id)) throw new Error('Use a complete shared participant identity.');
  const due = fields.due_date;
  if (due !== null && (typeof due !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(due) || !Number.isFinite(Date.parse(due)) || new Date(due).toISOString().slice(0, 10) !== due)) throw new Error('Invalid shared task due date.');
  if (!Array.isArray(fields.checklist) || fields.checklist.length > 40) throw new Error('A shared task supports at most40 checklist items.');
  const checklist = fields.checklist.map(raw => { const item = object(raw); exact(item, ['id', 'title', 'done']); if (typeof item.id !== 'string' || !/^[A-Za-z0-9_-]{1,80}$/.test(item.id) || typeof item.done !== 'boolean') throw new Error('Invalid shared checklist item.'); return { id: item.id, title: text(item.title, 200, true), done: item.done }; });
  if (new Set(checklist.map(item => item.id)).size !== checklist.length) throw new Error('Duplicate shared checklist identifiers.');
  const title = text(fields.title, 200, true), description = text(fields.description, 12000);
  const referenceTexts: Record<string, string> = { title, description };
  for (const item of checklist) referenceTexts['checklist:' + item.id] = item.title;
  const references = validateEncryptedReferences(referenceTexts, fields.references, connection);
  return { title, description, status: fields.status as SharedTaskFields['status'], priority: fields.priority as SharedTaskFields['priority'], assignee_matrix_id: fields.assignee_matrix_id as string | null, due_date: due as string | null, checklist, ...(references ? { references } : {}) };
}
export async function authorNamespace(sender: string) {
  const bytes = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(sender)));
  return [...bytes].map(byte => byte.toString(16).padStart(2, '0')).join('').slice(0, 16);
}
export function resourceIdValid(id: unknown, connection: string): id is string {
  return /^[a-f0-9]{32}$/.test(connection) && typeof id === 'string' && id.startsWith(connection + ':') && /^[a-f0-9]{16}:[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/.test(id.slice(connection.length + 1));
}
export async function parseRevision(event: VerifiedWorkEvent, context: WorkContext): Promise<WorkRevision> {
  const value = event.content;
  exact(value, ['protocol', 'connection_id', 'id', 'kind', 'parents', 'archived', 'fields']);
  if (event.type !== WORK_REVISION || !(value.protocol === WORK_PROTOCOL || value.protocol === WORK_PROTOCOL_V2 && context.work_protocols?.includes(WORK_PROTOCOL_V2)) || value.connection_id !== context.connection_id || !resourceIdValid(value.id, context.connection_id) || !['task', 'calendar', 'document'].includes(String(value.kind)) || !Array.isArray(value.parents) || value.parents.length > 8 || value.parents.some(parent => !eventId(parent)) || new Set(value.parents).size !== value.parents.length || typeof value.archived !== 'boolean') throw new Error('Invalid encrypted shared-work revision.');
  if (!value.parents.length && value.id.split(':')[1] !== await authorNamespace(event.sender)) throw new Error('Shared resource creator does not own this identity namespace.');
  if (value.kind === 'document' && (value.parents.length || value.archived)) throw new Error('Shared document metadata uses its immutable creation identity. Edit its title and body through encrypted Yjs updates.');
  const fields = validateSharedFields(value.kind as SharedWorkKind, value.fields, String(value.protocol), context.connection_id);
  if (new TextEncoder().encode(JSON.stringify(value)).length > 48 * 1024) throw new Error('Encrypted work revision exceeds its supported size.');
  return { event_id: event.event_id, sender: event.sender, timestamp: event.timestamp, id: value.id, kind: value.kind as SharedWorkKind, parents: value.parents as string[], archived: value.archived, fields, protocol: String(value.protocol) };
}
export function reduceWorkRevisions(revisions: WorkRevision[], rejectedByResource: ReadonlyMap<string, string[]> = new Map()): WorkRecord[] {
  if (revisions.length > 4000) throw new Error('Shared work exceeds the supported revision limit.');
  const groups = new Map<string, WorkRevision[]>();
  for (const row of revisions) { const previous = groups.get(row.id) || []; previous.push(row); groups.set(row.id, previous); }
  return [...groups].map(([id, history]) => {
    const sorted = [...history].sort((a, b) => a.event_id < b.event_id ? -1 : a.event_id > b.event_id ? 1 : 0), byId = new Map(sorted.map(row => [row.event_id, row]));
    const roots = sorted.filter(row => !row.parents.length);
    const root = roots[0];
    if (!root) return { id, kind: sorted[0].kind, root_event_id: '', created_by: '', heads: [], selected: sorted[0], history: sorted, conflicts: false, invalid: [...new Set([...sorted.map(row => row.event_id), ...(rejectedByResource.get(id) || [])])] };
    const invalid = roots.filter(row => row.kind !== root.kind).map(row => row.event_id), valid = new Set(roots.filter(row => row.kind === root.kind).map(row => row.event_id));
    let pending = sorted.filter(row => row.parents.length), progress = true;
    while (pending.length && progress) {
      progress = false;
      pending = pending.filter(row => {
        if (row.kind !== root.kind || row.parents.some(parent => !byId.has(parent))) { invalid.push(row.event_id); return false; }
        if (!row.parents.every(parent => valid.has(parent))) return true;
        valid.add(row.event_id); progress = true; return false;
      });
    }
    invalid.push(...pending.map(row => row.event_id), ...(rejectedByResource.get(id) || []));
    const applicable = sorted.filter(row => valid.has(row.event_id)), parents = new Set(applicable.flatMap(row => row.parents));
    const heads = applicable.filter(row => !parents.has(row.event_id));
    if (!heads.length) throw new Error('A shared revision graph has no verified head.');
    return { id, kind: root.kind, root_event_id: root.event_id, created_by: root.sender, heads, selected: heads[0], history: applicable, conflicts: heads.length > 1, invalid };
  }).sort((a, b) => b.selected.timestamp - a.selected.timestamp || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
}
