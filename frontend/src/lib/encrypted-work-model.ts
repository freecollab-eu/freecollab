import { validateEncryptedReferences, type EncryptedFieldReferences } from './encrypted-work-references';
import { validateDocumentAnchor, type DocumentCommentAnchor } from './document-anchors';
export { validateEncryptedReferences } from './encrypted-work-references';
export type { EncryptedReference, EncryptedFieldReferences } from './encrypted-work-references';
import { authorNamespace, matrixIdentity, resourceIdValid, type VerifiedWorkEvent, type WorkContext } from './federated-work-model';

export const ENCRYPTED_WORK_PROTOCOL = 'freecollab-encrypted-work/2';
export const ENCRYPTED_WORK_REVISION = 'eu.freecollab.encrypted_work.revision';
export const ENCRYPTED_WORK_KINDS = ['collection', 'feed', 'decision', 'linked-file', 'comment', 'workflow', 'workflow-run', 'task', 'project', 'organization'] as const;
export type EncryptedWorkKind = typeof ENCRYPTED_WORK_KINDS[number];
export interface EncryptedWorkContext extends WorkContext {
  mode: 'local' | 'federated'; encrypted_work_protocol: string; encrypted_work_supported: boolean;
  work_protocols: string[]; resource_kinds: string[];
  room_protocols: string[];
}
export interface EncryptedCollectionItem { kind: 'document' | 'file' | 'linked-document' | 'task' | 'feed' | 'message' | 'project' | 'decision' | 'calendar' | 'clip' | 'workflow'; id: string; parent_id?: string }
export interface EncryptedWorkflowStep { id: string; kind: 'task' | 'feed' | 'decision'; title: string; body: string; project_id?: string | null }
export interface EncryptedWorkFields {
  title: string; body: string; references?: EncryptedFieldReferences;
  items?: EncryptedCollectionItem[]; status?: string; url?: string; target_id?: string;
  resolved?: boolean; steps?: EncryptedWorkflowStep[]; workflow_id?: string; template_event?: string;
  anchor?: DocumentCommentAnchor;
  target_kind?: EncryptedWorkKind|'document'|'calendar';
  execution_owner?: string; completed?: Array<{ step_id: string; id: string }>;
  assignee_matrix_id?: string | null; priority?: 'low' | 'normal' | 'high';
  project_id?: string | null; due_date?: string | null; color?: 'sage' | 'blue' | 'sand' | 'rose';
  checklist?: Array<{id: string; title: string; done: boolean}>; file_ids?: string[]; decision_ids?: string[];
  origin_run?: { id: string; step_id: string };
}
export interface EncryptedWorkRevision {
  event_id: string; sender: string; timestamp: number; id: string; kind: EncryptedWorkKind;
  parents: string[]; archived: boolean; fields: EncryptedWorkFields;
}
export interface EncryptedWorkRecord {
  id: string; kind: EncryptedWorkKind; root_event_id: string; created_by: string;
  heads: EncryptedWorkRevision[]; selected: EncryptedWorkRevision; history: EncryptedWorkRevision[];
  conflicts: boolean; invalid: string[]; quarantined?: string[];
}
const object = (value: unknown): Record<string, unknown> => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw Error('Invalid encrypted work object.');
  return value as Record<string, unknown>;
};
const exact = (value: Record<string, unknown>, keys: string[]) => { if (Object.keys(value).some(key => !keys.includes(key))) throw Error('Unsupported encrypted work fields.'); };
const text = (value: unknown, limit: number, required = false): string => {
  if (typeof value !== 'string' || value.length > limit || required && !value.trim()) throw Error('Invalid encrypted work text.');
  return value;
};
const identifier = (value: unknown): string => {
  const result = text(value, 1024, true);
  if (/[\u0000-\u001f\u007f]/.test(result)) throw Error('Invalid encrypted resource identifier.');
  return result;
};
const eventId = (value: unknown): value is string => typeof value === 'string' && value.startsWith('$') && value.length <= 1024;
export function validateEncryptedFields(kind: EncryptedWorkKind, raw: unknown, connection?: string): EncryptedWorkFields {
  const f = object(raw), base = ['title', 'body', 'references', 'origin_run'];
  const fields: EncryptedWorkFields = { title: text(f.title, 200, true), body: text(f.body, kind === 'organization' ? 40000 : 12000) };
  const keys: Record<EncryptedWorkKind, string[]> = {
    collection: ['items'], feed: [], decision: ['status'], 'linked-file': ['url', 'target_id'],
    comment: ['target_id', 'target_kind', 'resolved', 'anchor'], workflow: ['steps'],
    'workflow-run': ['workflow_id', 'template_event', 'execution_owner', 'steps', 'completed', 'status'],
    task: ['status', 'priority', 'assignee_matrix_id', 'project_id', 'due_date', 'checklist', 'file_ids', 'decision_ids'],
    project: ['color'], organization: [],
  };
  if (!ENCRYPTED_WORK_KINDS.includes(kind)) throw Error('Unsupported encrypted work kind.');
  exact(f, [...base, ...keys[kind]]);
  if (f.origin_run !== undefined) {
    const origin = object(f.origin_run); exact(origin, ['id', 'step_id']);
    fields.origin_run = { id: identifier(origin.id), step_id: text(origin.step_id, 80, true) };
    if (connection && !resourceIdValid(fields.origin_run.id, connection)) throw Error('The workflow run belongs to another room.');
  }
  if (kind === 'collection') {
    if (!Array.isArray(f.items) || f.items.length > 200) throw Error('An encrypted collection supports at most 200 items.');
    fields.items = f.items.map(raw => { const row = object(raw); exact(row, ['kind', 'id', 'parent_id']);
      if (!['document', 'file', 'linked-document', 'task', 'feed', 'message', 'project', 'decision', 'calendar', 'clip', 'workflow'].includes(String(row.kind))) throw Error('Invalid encrypted collection item.');
      return { kind: row.kind as EncryptedCollectionItem['kind'], id: identifier(row.id), ...(row.parent_id !== undefined ? { parent_id: identifier(row.parent_id) } : {}) };
    });
    if (new Set(fields.items.map(row => row.kind + '\0' + row.id + '\0' + (row.parent_id || ''))).size !== fields.items.length) throw Error('Duplicate encrypted collection items.');
  }
  if (kind === 'decision') {
    if (!['proposed', 'accepted', 'superseded'].includes(String(f.status))) throw Error('Invalid encrypted decision status.');
    fields.status = String(f.status);
  }
  if (kind === 'task') {
    if (!['todo', 'doing', 'done'].includes(String(f.status)) || !['low', 'normal', 'high'].includes(String(f.priority))
        || f.assignee_matrix_id !== null && !matrixIdentity(f.assignee_matrix_id)) throw Error('Invalid encrypted task.');
    fields.status = String(f.status); fields.priority = f.priority as EncryptedWorkFields['priority']; fields.assignee_matrix_id = f.assignee_matrix_id as string | null;
    const project = f.project_id === undefined ? null : f.project_id, due = f.due_date === undefined ? null : f.due_date;
    if (project !== null && (typeof project !== 'string' || connection && !resourceIdValid(project, connection))) throw Error('Choose an encrypted project from this room.');
    if (due !== null && (typeof due !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(due) || !Number.isFinite(Date.parse(due)) || new Date(due).toISOString().slice(0, 10) !== due)) throw Error('Invalid encrypted task due date.');
    fields.project_id = project as string | null; fields.due_date = due as string | null;
    const checklist = f.checklist === undefined ? [] : f.checklist;
    if (!Array.isArray(checklist) || checklist.length > 40) throw Error('Choose up to forty encrypted checklist items.');
    fields.checklist = checklist.map(raw => { const row = object(raw); exact(row, ['id', 'title', 'done']);
      if (typeof row.done !== 'boolean' || typeof row.id !== 'string' || !/^[A-Za-z0-9_-]{1,80}$/.test(row.id)) throw Error('Invalid encrypted checklist item.');
      return { id: row.id, title: text(row.title, 200, true), done: row.done };
    });
    if (new Set(fields.checklist.map(row => row.id)).size !== fields.checklist.length) throw Error('Duplicate encrypted checklist identity.');
    for (const key of ['file_ids', 'decision_ids'] as const) {
      const ids = f[key] === undefined ? [] : f[key];
      if (!Array.isArray(ids) || ids.length > 20 || ids.some(id => typeof id !== 'string' || !(connection && resourceIdValid(id, connection))) || new Set(ids).size !== ids.length) throw Error('Choose current encrypted resource links.');
      fields[key] = ids;
    }
  }
  if (kind === 'project') {
    if (!['sage', 'blue', 'sand', 'rose'].includes(String(f.color))) throw Error('Invalid encrypted project color.');
    fields.color = f.color as EncryptedWorkFields['color'];
  }
  if (kind === 'linked-file') {
    const url = text(f.url, 2048), target = text(f.target_id, 1024);
    if (!url && !target) throw Error('Choose an encrypted file event or an HTTPS link.');
    if (url) { const parsed = new URL(url); if (parsed.protocol !== 'https:' || parsed.username || parsed.password) throw Error('Use an HTTPS file link without credentials.'); }
    if (target && !eventId(target) && !(connection && resourceIdValid(target, connection))) throw Error('Choose a verified encrypted resource or Matrix file event.');
    fields.url = url; fields.target_id = target;
  }
  if (kind === 'comment') {
    fields.target_id = identifier(f.target_id); if (typeof f.resolved !== 'boolean') throw Error('Invalid encrypted comment status.'); fields.resolved = f.resolved;
    if(![...ENCRYPTED_WORK_KINDS,'document','calendar'].includes(f.target_kind as EncryptedWorkKind))throw Error('Choose the exact encrypted comment target kind.');fields.target_kind=f.target_kind as EncryptedWorkFields['target_kind'];
    if (connection && !resourceIdValid(fields.target_id, connection)) throw Error('The comment belongs to another room.');
    if(f.anchor!==undefined){const anchor=validateDocumentAnchor(f.anchor,fields.target_id);if(fields.target_kind!=='document'||anchor.source_sequence>500||anchor.start_relative.length>1024||anchor.end_relative.length>1024)throw Error('The encrypted document anchor exceeds its bound or names another resource kind.');fields.anchor={version:1,document_id:anchor.document_id,field:'body',start_relative:anchor.start_relative,end_relative:anchor.end_relative,quote:anchor.quote,source_sequence:anchor.source_sequence};}
  }
  if (kind === 'workflow' || kind === 'workflow-run') {
    if (!Array.isArray(f.steps) || !f.steps.length || f.steps.length > 12) throw Error('Choose one to twelve reviewed encrypted workflow steps.');
    fields.steps = f.steps.map(raw => { const row = object(raw); exact(row, ['id', 'kind', 'title', 'body', 'project_id']);
      const id = text(row.id, 80, true); if (!/^[A-Za-z0-9_-]+$/.test(id) || !['task', 'feed', 'decision'].includes(String(row.kind))) throw Error('Invalid encrypted workflow operation.');
      if (row.project_id !== undefined && (row.kind !== 'task' || row.project_id !== null && (typeof row.project_id !== 'string' || connection && !resourceIdValid(row.project_id, connection)))) throw Error('Invalid encrypted workflow project.');
      return { id, kind: row.kind as EncryptedWorkflowStep['kind'], title: text(row.title, 200, true), body: text(row.body, 12000), ...(row.project_id !== undefined ? { project_id: row.project_id as string | null } : {}) };
    });
    if (new Set(fields.steps.map(row => row.id)).size !== fields.steps.length) throw Error('Duplicate encrypted workflow step.');
  }
  const referenceTexts: Record<string, string> = { title: fields.title, body: fields.body };
  for (const row of fields.steps || []) { referenceTexts['step:' + row.id + ':title'] = row.title; referenceTexts['step:' + row.id + ':body'] = row.body; }
  for (const row of fields.checklist || []) referenceTexts['checklist:' + row.id] = row.title;
  const references = validateEncryptedReferences(referenceTexts, f.references, connection);
  if (references) fields.references = references;
  if (kind === 'workflow-run') {
    fields.workflow_id = identifier(f.workflow_id); fields.template_event = identifier(f.template_event);
    if (connection && !resourceIdValid(fields.workflow_id, connection) || !eventId(fields.template_event) || !matrixIdentity(f.execution_owner)) throw Error('Invalid encrypted workflow ownership.');
    fields.execution_owner = f.execution_owner;
    if (!['queued', 'running', 'paused', 'completed', 'cancelled'].includes(String(f.status))) throw Error('Invalid encrypted workflow run status.');
    fields.status = String(f.status);
    if (!Array.isArray(f.completed) || f.completed.length > fields.steps!.length) throw Error('Invalid encrypted workflow receipts.');
    fields.completed = f.completed.map(raw => { const row = object(raw); exact(row, ['step_id', 'id']);
      const step_id = text(row.step_id, 80, true), id = identifier(row.id);
      if (!fields.steps!.some(step => step.id === step_id) || connection && !resourceIdValid(id, connection)) throw Error('Invalid encrypted workflow receipt.');
      return { step_id, id };
    });
    if (new Set(fields.completed.map(row => row.step_id)).size !== fields.completed.length || fields.status === 'completed' && fields.completed.length !== fields.steps!.length) throw Error('Incomplete or duplicate encrypted workflow receipts.');
  }
  if (new TextEncoder().encode(JSON.stringify(fields)).length > 40 * 1024) throw Error('Encrypted work exceeds its supported event bound.');
  return fields;
}
export async function parseEncryptedRevision(event: VerifiedWorkEvent, context: WorkContext): Promise<EncryptedWorkRevision> {
  const value = event.content; exact(value, ['protocol', 'connection_id', 'id', 'kind', 'parents', 'archived', 'fields']);
  if (event.type !== ENCRYPTED_WORK_REVISION || value.protocol !== ENCRYPTED_WORK_PROTOCOL || value.connection_id !== context.connection_id
      || !resourceIdValid(value.id, context.connection_id) || !ENCRYPTED_WORK_KINDS.includes(value.kind as EncryptedWorkKind)
      || !Array.isArray(value.parents) || value.parents.length > 8 || value.parents.some(parent => !eventId(parent))
      || new Set(value.parents).size !== value.parents.length || typeof value.archived !== 'boolean') throw Error('Invalid encrypted work revision.');
  if (value.kind === 'organization') {
    if (value.id !== await organizationResourceId(context.connection_id)) throw Error('Invalid encrypted organization identity.');
  } else if (value.id.split(':')[1] === '0000000000000000' || !value.parents.length && value.id.split(':')[1] !== await authorNamespace(event.sender)) throw Error('Encrypted resource creator does not own this identity namespace.');
  const fields = validateEncryptedFields(value.kind as EncryptedWorkKind, value.fields, context.connection_id);
  if (value.kind === 'workflow-run' && fields.execution_owner !== event.sender && !value.parents.length) throw Error('Only the execution owner may start this run.');
  return { event_id: event.event_id, sender: event.sender, timestamp: event.timestamp, id: value.id,
    kind: value.kind as EncryptedWorkKind, parents: value.parents as string[], archived: value.archived, fields };
}
export function reduceEncryptedRevisions(revisions: EncryptedWorkRevision[]): EncryptedWorkRecord[] {
  if (revisions.length > 10000) throw Error('Encrypted work exceeds its retained revision bound.');
  const groups = new Map<string, EncryptedWorkRevision[]>();
  for (const row of revisions) groups.set(row.id, [...(groups.get(row.id) || []), row]);
  return [...groups].map(([id, history]) => {
    const sorted = [...history].sort((a, b) => a.event_id.localeCompare(b.event_id)), byId = new Map(sorted.map(row => [row.event_id, row]));
    const roots = sorted.filter(row => !row.parents.length), root = roots[0], invalid: string[] = [], quarantined:string[]=[];
    if (!root) return { id, kind: sorted[0].kind, root_event_id: '', created_by: '', heads: [], selected: sorted[0], history: sorted, conflicts: false, invalid: sorted.map(row => row.event_id) };
    const valid = new Set(roots.filter(row => row.kind === root.kind && (row.kind !== 'workflow-run' || row.sender === root.sender)).map(row => row.event_id));
    invalid.push(...roots.filter(row => !valid.has(row.event_id)).map(row => row.event_id));
    let pending = sorted.filter(row => row.parents.length), progress = true;
    while (pending.length && progress) { progress = false; pending = pending.filter(row => {
      if(row.kind!==root.kind||['workflow-run','comment'].includes(row.kind)&&row.sender!==root.sender||row.kind==='workflow-run'&&row.fields.execution_owner!==root.sender){quarantined.push(row.event_id);return false;}
      if(row.parents.some(parent=>!byId.has(parent))){invalid.push(row.event_id);return false;}
      if (!row.parents.every(parent => valid.has(parent))) return true;
      if (JSON.stringify(row.fields.origin_run) !== JSON.stringify(root.fields.origin_run)) { invalid.push(row.event_id); return false; }
      if(row.kind==='comment'&&(row.sender!==root.sender||JSON.stringify([row.fields.title,row.fields.body,row.fields.target_id,row.fields.target_kind,row.fields.anchor,row.fields.references])!==JSON.stringify([root.fields.title,root.fields.body,root.fields.target_id,root.fields.target_kind,root.fields.anchor,root.fields.references]))){invalid.push(row.event_id);return false;}
      valid.add(row.event_id); progress = true; return false;
    }); }
    invalid.push(...pending.map(row => row.event_id));
    const applicable = sorted.filter(row => valid.has(row.event_id)), parents = new Set(applicable.flatMap(row => row.parents));
    const heads = applicable.filter(row => !parents.has(row.event_id));
    return { id, kind: root.kind, root_event_id: root.event_id, created_by: root.sender, heads,
      selected: heads[0] || root, history: applicable, conflicts: heads.length > 1, invalid, quarantined };
  }).sort((a, b) => b.selected.timestamp - a.selected.timestamp || a.id.localeCompare(b.id));
}

export async function deterministicWorkUuid(seed: string) {
  const bytes = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(seed))).slice(0, 16);
  bytes[6] = bytes[6] & 15 | 64; bytes[8] = bytes[8] & 63 | 128;
  const hex = [...bytes].map(value => value.toString(16).padStart(2, '0')).join('');
  return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join('-');
}
export async function organizationResourceId(connection: string) {
  if (!/^[a-f0-9]{32}$/.test(connection)) throw Error('Invalid encrypted organization scope.');
  return connection + ':0000000000000000:' + await deterministicWorkUuid('organization:' + connection);
}
