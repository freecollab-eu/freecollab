import { api } from './api';
import { EncryptedWorkClient, getEncryptedWorkAuthority, type EncryptedWorkRecord, type EncryptedWorkRevision } from './encrypted-work-api';
import { FederatedWorkClient } from './federated-work-api';
import { sharedDocumentsApi } from './shared-documents-api';
import type { SharedDocumentRecord } from './shared-documents-api';
import type { DocumentOrganization, DocumentStack, DocumentPlacement, DocumentRelation, StackMessagePreview, StackMessageReference } from './document-organization-api';
import { authorNamespace, matrixIdentity } from './federated-work-model';
import { verifiedEncryptedMessagePreview } from './encrypted-message-preview';

interface Stack { id: string; title: string; author: string; created_at: string; updated_at: string; version: number; generation: string }
interface Placement { document_id: string; stack_id: string | null; version: number; author: string; updated_at: string }
interface Relation { id: string; first_document_id: string; second_document_id: string; author: string; created_at: string }
interface Message { id: string; stack_id: string; message_id: string; author: string; created_at: string }
interface State { version: 1; stacks: Stack[]; placements: Placement[]; relations: Relation[]; messages: Message[] }
const empty = (): State => ({ version: 1, stacks: [], placements: [], relations: [], messages: [] });
const validId = (value: unknown): value is string => typeof value === 'string' && !!value && value.length <= 1024 && !/[\u0000-\u001f\u007f]/.test(value);
const exact = (value: Record<string, unknown>, keys: string[]) => { if (Object.keys(value).some(key => !keys.includes(key))) throw Error('Unsupported encrypted organization state.'); };
const object = (value: unknown): Record<string, unknown> => { if (!value || typeof value !== 'object' || Array.isArray(value)) throw Error('Invalid encrypted organization state.'); return value as Record<string, unknown>; };
const same = (value: unknown, other: unknown) => JSON.stringify(value) === JSON.stringify(other);
export function validateEncryptedOrganization(body: string): State {
  if (typeof body !== 'string' || body.length > 40000) throw Error('Encrypted organization exceeds its retained size.');
  const state = object(JSON.parse(body)); exact(state, ['version', 'stacks', 'placements', 'relations', 'messages']);
  if (state.version !== 1) throw Error('Unsupported encrypted organization version.');
  const fields = { stacks: ['id', 'title', 'author', 'created_at', 'updated_at', 'version', 'generation'], placements: ['document_id', 'stack_id', 'version', 'author', 'updated_at'], relations: ['id', 'first_document_id', 'second_document_id', 'author', 'created_at'], messages: ['id', 'stack_id', 'message_id', 'author', 'created_at'] };
  for (const [name, keys] of Object.entries(fields)) {
    const rows = state[name];
    if (!Array.isArray(rows) || rows.length > (name === 'stacks' ? 30 : name === 'placements' ? 1000 : 200)) throw Error('Encrypted organization exceeds its retained limits.');
    for (const raw of rows) { const row = object(raw); exact(row, keys); for (const key of keys.filter(key => !['stack_id', 'version'].includes(key))) if (!validId(row[key])) throw Error('Invalid encrypted organization field.'); if (!matrixIdentity(row.author) || ['created_at', 'updated_at'].some(key => key in row && !Number.isFinite(Date.parse(String(row[key]))))) throw Error('Invalid encrypted organization author or date.'); if ('version' in row && (!Number.isSafeInteger(row.version) || Number(row.version) < 1)) throw Error('Invalid encrypted organization revision.'); if ('stack_id' in row && row.stack_id !== null && !validId(row.stack_id)) throw Error('Invalid encrypted stack destination.'); if (name === 'stacks' && (String(row.title).length > 100 || !String(row.title).trim() || !/^[a-f0-9]{32}$/.test(String(row.generation)))) throw Error('Invalid encrypted stack name or generation.'); }
    const ids = rows.map(raw => name === 'placements' ? raw.document_id : raw.id); if (new Set(ids).size !== ids.length) throw Error('Duplicate encrypted organization entry.');
  }
  const typed = state as unknown as State, ids = new Set(typed.stacks.map(stack => stack.id));
  if (typed.placements.some(row => row.stack_id !== null && !ids.has(row.stack_id)) || typed.messages.some(row => !ids.has(row.stack_id)) || typed.relations.some(row => row.first_document_id === row.second_document_id)) throw Error('Encrypted organization has an invalid destination.');
  return typed;
}

/** Three-way merge keeps independent approved edits; conflicting edits require review. */
export function mergeEncryptedOrganization(record: EncryptedWorkRecord): State {
  if (!record.conflicts) return validateEncryptedOrganization(record.selected.fields.body);
  const history = new Map(record.history.map(revision => [revision.event_id, revision]));
  function ancestors(head: EncryptedWorkRevision) { const result = new Set<string>(); const queue = [head.event_id]; while (queue.length) { const id = queue.pop()!; if (result.has(id)) continue; result.add(id); queue.push(...(history.get(id)?.parents || [])); } return result; }
  const sets = record.heads.map(ancestors), common = record.history.filter(row => sets.every(set => set.has(row.event_id))).sort((a, b) => b.timestamp - a.timestamp || b.event_id.localeCompare(a.event_id))[0];
  const base = common ? validateEncryptedOrganization(common.fields.body) : empty(), heads = record.heads.map(row => validateEncryptedOrganization(row.fields.body)), merged = empty();
  for (const kind of ['stacks', 'placements', 'relations', 'messages'] as const) {
    const key = (row: State[typeof kind][number]) => 'document_id' in row && kind === 'placements' ? row.document_id : (row as Stack | Relation | Message).id;
    const original = new Map(base[kind].map(row => [key(row), row] as const));
    const maps = heads.map(head => new Map(head[kind].map(row => [key(row), row] as const)));
    for (const id of new Set([...original.keys(), ...maps.flatMap(map => [...map.keys()])])) {
      const before = original.get(id), changed = maps.map(map => map.get(id)).filter(value => !same(value, before));
      if (changed.some(value => !same(value, changed[0]))) throw Error('Competing changes affect the same organization item. Review the encrypted versions before continuing.');
      const selected = changed.length ? changed[0] : before;
      if (selected) (merged[kind] as unknown[]).push(selected);
    }
  }
  return validateEncryptedOrganization(JSON.stringify(merged));
}

function transition(candidate: State, parents: State[], sender: string, moderator: boolean, namespace: string, deletions: Set<string>) {
  for (const kind of ['stacks', 'placements', 'relations', 'messages'] as const) {
    const key = (row: State[typeof kind][number]) => kind === 'placements' ? (row as Placement).document_id : (row as Stack | Relation | Message).id;
    const maps = parents.map(parent => new Map(parent[kind].map(row => [key(row), row] as const))), current = new Map(candidate[kind].map(row => [key(row), row] as const));
    for (const [id, row] of current) {
      const before = maps.map(map => map.get(id)).filter((value): value is typeof row => !!value);
      if (before.some(value => same(value, row))) continue; // A merge can retain an already-authorized version.
      if (!before.length) { if (row.author !== sender || kind !== 'placements' && !String(key(row)).startsWith(namespace + ':') || 'version' in row && row.version !== 1) throw Error('An encrypted organization creation has a forged author or namespace.'); continue; }
      if (kind === 'stacks') {
        const stack = row as Stack;
        if (before.some(value => { const old = value as Stack; return old.author !== stack.author || old.generation !== stack.generation || old.created_at !== stack.created_at; }) || stack.author !== sender && !moderator || stack.version !== Math.max(...before.map(value => (value as Stack).version)) + 1) throw Error('An encrypted stack change is not authorized by its immutable creator or publication authority.');
      } else if (kind === 'placements') {
        if (row.author !== sender || (row as Placement).version !== Math.max(...before.map(value => (value as Placement).version)) + 1) throw Error('An encrypted document move has a forged author or version.');
      } else throw Error('Encrypted organization reference identities are immutable.');
    }
    if (kind === 'stacks') for (const id of new Set(maps.flatMap(map => [...map.keys()]))) {
      const before = maps.map(map => map.get(id)).find((value): value is Stack => !!value) as Stack | undefined;
      if (!current.has(id) && before && before.author !== sender && !moderator && !deletions.has(id + ':' + before.generation)) throw Error('An encrypted stack deletion is not authorized by its creator or publication authority.');
    }
  }
}

/** Invalid nested-owner changes cannot hide or overwrite valid organization history. */
export function verifiedOrganizationHistory(record: EncryptedWorkRecord, moderatorEvents: Set<string> = new Set(), namespaces: Map<string, string> = new Map()): EncryptedWorkRecord | null {
  const pending = new Map(record.history.map(row => [row.event_id, row])), processed = new Set<string>(), effective = new Map<string, string[]>(), valid = new Map<string, EncryptedWorkRevision>(), states = new Map<string, State>(), removed = new Map<string, Set<string>>();
  while (pending.size) {
    let progressed = false;
    for (const [id, revision] of [...pending]) {
      if (revision.parents.some(parent => !processed.has(parent))) continue;
      const parents = [...new Set(revision.parents.flatMap(parent => effective.get(parent) || []))];
      try { const state = validateEncryptedOrganization(revision.fields.body), ancestors = parents.map(parent => states.get(parent)!), deletions = new Set(parents.flatMap(parent => [...(removed.get(parent) || [])])); transition(state, ancestors, revision.sender, moderatorEvents.has(id), namespaces.get(revision.sender) || revision.sender, deletions); for (const parent of ancestors) for (const stack of parent.stacks) if (!state.stacks.some(row => row.id === stack.id)) deletions.add(stack.id + ':' + stack.generation); valid.set(id, { ...revision, parents }); states.set(id, state); effective.set(id, [id]); removed.set(id, deletions); }
      catch { effective.set(id, parents); }
      processed.add(id); pending.delete(id); progressed = true;
    }
    if (!progressed) break;
  }
  const history = [...valid.values()], referenced = new Set(history.flatMap(row => row.parents)), heads = history.filter(row => !referenced.has(row.event_id)).sort((a, b) => a.timestamp - b.timestamp || a.event_id.localeCompare(b.event_id));
  if (!heads.length) return null;
  return { ...record, history, heads, selected: heads[heads.length - 1], conflicts: heads.length > 1 };
}

async function use<T>(channel: string, operation: (client: EncryptedWorkClient) => Promise<T>) { const client = new EncryptedWorkClient(channel); try { return await operation(client); } finally { client.stop(); } }
const authorityCache = new Map<string, Map<string, boolean>>();
if (typeof window !== 'undefined') window.addEventListener('freecollab:auth-changed', () => authorityCache.clear());
async function loaded(client: EncryptedWorkClient, review = false) {
  const view = await client.load(), record = view.records.find(row => row.kind === 'organization' && !row.selected.archived);
  const moderatorEvents = new Set<string>();
  if (record) {
    const cacheKey = JSON.stringify([api.getCurrentUserId(), api.getAccessToken(), client.channelId, view.context.connection_id]), cache = authorityCache.get(cacheKey) || new Map<string, boolean>(); authorityCache.set(cacheKey, cache);
    const history = new Map(record.history.map(row => [row.event_id, row]));
    const needs = record.history.filter(revision => {
      try { const state = validateEncryptedOrganization(revision.fields.body); return revision.parents.some(parent => { const previous = history.get(parent); if (!previous) return false; return validateEncryptedOrganization(previous.fields.body).stacks.some(stack => stack.author !== revision.sender && !same(stack, state.stacks.find(row => row.id === stack.id))); }); }
      catch { return false; }
    });
    for (let offset = 0; offset < needs.length; offset += 20) await Promise.all(needs.slice(offset, offset + 20).map(async revision => {
      if (!cache.has(revision.event_id)) { try { const proof = await getEncryptedWorkAuthority(client.channelId, revision.event_id); cache.set(revision.event_id, proof.event_id === revision.event_id && proof.room_id === view.context.room_id && proof.matrix_user_id === revision.sender && proof.role === 'admin'); } catch { cache.set(revision.event_id, false); } }
      if (cache.get(revision.event_id)) moderatorEvents.add(revision.event_id);
    }));
  }
  const namespaces = new Map(await Promise.all((record ? [...new Set(record.history.map(row => row.sender))] : []).map(async sender => [sender, await authorNamespace(sender)] as const)));
  const verified = record ? verifiedOrganizationHistory(record, moderatorEvents, namespaces) : null;
  let state = empty(), conflict = false;
  if (verified) { try { state = mergeEncryptedOrganization(verified); } catch (failure) { if (!review) throw failure; state = validateEncryptedOrganization(verified.selected.fields.body); conflict = true; } }
  return { view, record, verified, state, conflict };
}
async function save(client: EncryptedWorkClient, state: State, previous?: EncryptedWorkRecord) { const body = JSON.stringify(state); validateEncryptedOrganization(body); const old = new Set(previous?.history.map(row => row.event_id) || []); await client.save('organization', { title: 'Document organization', body }, previous, { id: await client.organizationId(), ...(previous?.conflicts ? { mergeParents: previous.heads.map(row => row.event_id) } : {}) }); const confirmed = await loaded(client); if (!confirmed.verified?.history.some(row => !old.has(row.event_id) && row.fields.body === body)) throw Error('The encrypted organization change did not have current publication authority. Reload and review it.'); }
async function mayManageStack(client: EncryptedWorkClient, author: string, matrix: string) { if (author === matrix) return true; const token = api.getAccessToken(), actor = api.getCurrentUserId(), channel = await api.getChannel(client.channelId); if (token !== api.getAccessToken() || actor !== api.getCurrentUserId() || channel.id !== client.channelId || channel.role !== 'admin') throw Error('Only the stack creator or a current channel administrator can manage this stack.'); return true; }
const hash = async (text: string) => [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text)))].map(value => value.toString(16).padStart(2, '0')).join('');
function stack(state: State, id: string) { const found = state.stacks.find(row => row.id === id); if (!found) throw Error('The encrypted stack is no longer available.'); return found; }
function user(matrix: string, current: string) { return matrix === current ? api.getCurrentUserId()! : matrix; }
function stackValue(channel: string, row: Stack, matrix: string): DocumentStack { return { id: row.id, channel_id: channel, title: row.title, created_by: user(row.author, matrix), created_at: row.created_at, updated_at: row.updated_at, version: row.version, generation: row.generation }; }
async function peerDocuments(channel: string): Promise<SharedDocumentRecord[]> {
  const owner = api.getAccessToken(), client = new EncryptedWorkClient(channel);
  try {
    const scope = await client.load();
    if (scope.context.mode !== 'federated' || !scope.legacyRecords.some(row => row.kind === 'document')) return [];
    const legacy = new FederatedWorkClient(channel);
    try {
      const view = await legacy.load(), result: SharedDocumentRecord[] = [];
      for (const row of view.records.filter(row => row.kind === 'document' && !row.selected.archived && !row.invalid.length && !row.conflicts)) {
        const state = await legacy.document(row);
        if (!owner || owner !== api.getAccessToken() || view.context.connection_id !== scope.context.connection_id || view.context.room_id !== scope.context.room_id) throw Error('The peer document scope changed.');
        result.push({id:row.id,channel_id:channel,created_by:user(row.created_by,scope.context.matrix_user_id),created_at:new Date(row.history[0].timestamp).toISOString(),updated_at:new Date(row.selected.timestamp).toISOString(),archived:false,version:row.history.length,update_count:state.update_ids.length+1,title:state.title,summary_event_id:null,summary_update_id:null,summary_by:null,encrypted_resource:true,encrypted_kind:'document',encrypted_source_version:await hash(JSON.stringify([row.heads.map(head=>head.event_id).sort(),state.update_ids]))});
      }
      const current = await client.load(); if (!owner || owner !== api.getAccessToken() || current.context.connection_id !== scope.context.connection_id || current.context.room_id !== scope.context.room_id || current.context.matrix_user_id !== scope.context.matrix_user_id) throw Error('The peer document scope changed.');
      return result;
    } finally { legacy.stop(); }
  } finally { client.stop(); }
}
async function activeDocument(channel: string, id: string) { const documents = await sharedDocumentsApi.list(channel); let row = documents.find(row => row.id === id && !row.archived && !row.resolution_error); if (!row) row = (await peerDocuments(channel)).find(row => row.id === id); if (!row) throw Error('Choose a current verified document in this channel.'); return row; }

const preview = verifiedEncryptedMessagePreview;

export const encryptedDocumentOrganizationApi = {
  list: (channel: string, archived = false): Promise<DocumentOrganization> => use(channel, async client => {
    const { view, state, conflict, verified } = await loaded(client, true), nativeDocuments = await sharedDocumentsApi.list(channel, archived), documents = [...nativeDocuments, ...(!archived ? await peerDocuments(channel) : [])], ids = new Set(documents.map(row => row.id));
    const stack_comments: DocumentOrganization['stack_comments'] = [];
    let comments_incomplete = nativeDocuments.length > 50;
    for (const document of nativeDocuments.slice(0, 50)) { if (stack_comments.length >= 100) { comments_incomplete = true; break; } const comments = await sharedDocumentsApi.comments(channel, document.id); if (stack_comments.length + comments.length > 100) comments_incomplete = true; for (const comment of comments.slice(0, 100 - stack_comments.length)) stack_comments.push({ id: comment.id, channel_id: channel, document_id: document.id, stack_id: state.placements.find(row => row.document_id === document.id)?.stack_id || null, text: comment.fields.text, references: comment.fields.references, created_by: comment.created_by, created_at: comment.created_at, resolved: comment.resolved, version: comment.version }); }
    for (const row of view.records.filter(row => row.kind === 'comment' && row.selected.fields.target_kind === 'document' && !row.selected.archived && !row.invalid.length && !row.conflicts && documents.some(document => document.encrypted_resource && document.id === row.selected.fields.target_id))) {
      if (stack_comments.length >= 100) { comments_incomplete = true; break; }
      stack_comments.push({id:row.id,channel_id:channel,document_id:row.selected.fields.target_id!,stack_id:state.placements.find(placement=>placement.document_id===row.selected.fields.target_id)?.stack_id || null,text:row.selected.fields.body,created_by:user(row.created_by,view.context.matrix_user_id),created_at:new Date(row.history[0].timestamp).toISOString(),resolved:!!row.selected.fields.resolved,version:row.history.length});
    }
    const messages: StackMessageReference[] = [];
    for (const ref of state.messages) { try { const source = await preview(channel, ref.message_id); messages.push({ ...ref, channel_id: channel, created_by: user(ref.author, view.context.matrix_user_id), available: true, excerpt: source.text.slice(0, 1000), author_id: source.author_id, source_edited_at: source.edited_at }); } catch { messages.push({ ...ref, channel_id: channel, created_by: user(ref.author, view.context.matrix_user_id), available: false, excerpt: null }); } }
    return { documents, supported: true, ...(conflict && verified ? { organization_conflicts: verified.heads.map(head => ({ event_id: head.event_id, author: head.sender, at: head.timestamp, stacks: validateEncryptedOrganization(head.fields.body).stacks.map(row => row.title) })) } : {}), stacks: state.stacks.map(row => stackValue(channel, row, view.context.matrix_user_id)), placements: state.placements.filter(row => ids.has(row.document_id)).map(row => ({ document_id: row.document_id, channel_id: channel, stack_id: row.stack_id, version: row.version, updated_by: user(row.author, view.context.matrix_user_id), updated_at: row.updated_at })), relations: state.relations.filter(row => ids.has(row.first_document_id) && ids.has(row.second_document_id)).map(row => ({ id: row.id, channel_id: channel, first_document_id: row.first_document_id, second_document_id: row.second_document_id, created_by: user(row.author, view.context.matrix_user_id), created_at: row.created_at })), message_references: messages, stack_comments: stack_comments.slice(-100), comments_incomplete };
  }),
  previewMessage: preview,
  createStack: (channel: string, title: string, id: string) => use(channel, async client => { const { view, record, state } = await loaded(client); id = (await authorNamespace(view.context.matrix_user_id)) + ':' + id; const previous = state.stacks.find(row => row.id === id); if (previous) { if (previous.title !== title.trim() || previous.author !== view.context.matrix_user_id) throw Error('This stack identifier is already used.'); return stackValue(channel, previous, view.context.matrix_user_id); } if (state.stacks.length >= 30 || state.stacks.some(row => row.title.toLocaleLowerCase() === title.trim().toLocaleLowerCase())) throw Error('Choose a unique stack name within the 30-stack limit.'); const now = new Date().toISOString(), row: Stack = { id, title: title.trim(), author: view.context.matrix_user_id, created_at: now, updated_at: now, version: 1, generation: crypto.randomUUID().replace(/-/g, '') }; state.stacks.push(row); await save(client, state, record); return stackValue(channel, row, view.context.matrix_user_id); }),
  renameStack: (channel: string, expected: DocumentStack, title: string) => use(channel, async client => { const { view, record, state } = await loaded(client), row = stack(state, expected.id); if (row.version !== expected.version || row.generation !== expected.generation) throw Error('The stack changed. Reload it before renaming.'); await mayManageStack(client, row.author, view.context.matrix_user_id); if (state.stacks.some(other => other.id !== row.id && other.title.toLocaleLowerCase() === title.trim().toLocaleLowerCase())) throw Error('A stack with this name already exists.'); row.title = title.trim(); row.version++; row.updated_at = new Date().toISOString(); await save(client, state, record); return stackValue(channel, row, view.context.matrix_user_id); }),
  deleteStack: (channel: string, expected: DocumentStack) => use(channel, async client => { const { view, record, state } = await loaded(client), row = stack(state, expected.id); if (row.version !== expected.version || row.generation !== expected.generation) throw Error('The stack changed. Reload it before deleting.'); await mayManageStack(client, row.author, view.context.matrix_user_id); if (state.placements.some(item => item.stack_id === row.id) || state.messages.some(item => item.stack_id === row.id)) throw Error('Remove every document and saved message before deleting this stack.'); state.stacks = state.stacks.filter(item => item !== row); await save(client, state, record); }),
  move: (channel: string, documentId: string, stackId: string | null, expectedVersion: number, stackGeneration: string | null): Promise<DocumentPlacement> => use(channel, async client => { await activeDocument(channel, documentId); const { view, record, state } = await loaded(client); if (stackId && stack(state, stackId).generation !== stackGeneration) throw Error('The destination stack was replaced.'); const previous = state.placements.find(row => row.document_id === documentId); if ((previous?.version || 0) !== expectedVersion) throw Error('The document moved. Reload before moving or undoing.'); const row: Placement = { document_id: documentId, stack_id: stackId, version: (previous?.version || 0) + 1, author: view.context.matrix_user_id, updated_at: new Date().toISOString() }; state.placements = [...state.placements.filter(item => item !== previous), row]; await save(client, state, record); return { document_id: documentId, channel_id: channel, stack_id: stackId, version: row.version, updated_by: api.getCurrentUserId()!, updated_at: row.updated_at }; }),
  relate: (channel: string, first: string, second: string, id: string): Promise<DocumentRelation> => use(channel, async client => { if (first === second) throw Error('Choose two different documents.'); await activeDocument(channel, first); await activeDocument(channel, second); const { view, record, state } = await loaded(client), ids = [first, second].sort(); id = (await authorNamespace(view.context.matrix_user_id)) + ':' + id; const prior = state.relations.find(row => row.id === id); if (prior) { if (prior.first_document_id !== ids[0] || prior.second_document_id !== ids[1]) throw Error('This relation identifier is already used.'); return { ...prior, channel_id: channel, created_by: user(prior.author, view.context.matrix_user_id) }; } if (state.relations.some(row => row.first_document_id === ids[0] && row.second_document_id === ids[1])) throw Error('These documents are already related.'); const row: Relation = { id, first_document_id: ids[0], second_document_id: ids[1], author: view.context.matrix_user_id, created_at: new Date().toISOString() }; state.relations.push(row); await save(client, state, record); return { ...row, channel_id: channel, created_by: api.getCurrentUserId()! }; }),
  unlink: (channel: string, id: string) => use(channel, async client => { const { record, state } = await loaded(client); state.relations = state.relations.filter(row => row.id !== id); await save(client, state, record); }),
  saveMessage: (channel: string, expected: DocumentStack, source: StackMessagePreview, id: string): Promise<StackMessageReference> => use(channel, async client => { const { view, record, state } = await loaded(client); if (stack(state, expected.id).generation !== expected.generation) throw Error('The destination stack was replaced.'); id = (await authorNamespace(view.context.matrix_user_id)) + ':' + id; const latest = await preview(channel, source.id); if (source.channel_id !== channel || latest.expected_text_sha256 !== source.expected_text_sha256) throw Error('The source message changed. Review it again.'); let row = state.messages.find(row => row.id === id || row.stack_id === expected.id && row.message_id === source.id); if (row && (row.stack_id !== expected.id || row.message_id !== source.id)) throw Error('This message reference identifier is already used.'); if (!row) { row = { id, stack_id: expected.id, message_id: source.id, author: view.context.matrix_user_id, created_at: new Date().toISOString() }; state.messages.push(row); await save(client, state, record); } return { id: row.id, channel_id: channel, stack_id: row.stack_id, message_id: row.message_id, created_by: user(row.author, view.context.matrix_user_id), created_at: row.created_at, available: true, excerpt: latest.text.slice(0, 1000), author_id: latest.author_id, source_edited_at: latest.edited_at }; }),
  removeMessage: (channel: string, id: string) => use(channel, async client => { const { record, state } = await loaded(client); state.messages = state.messages.filter(row => row.id !== id); await save(client, state, record); }),
  async conflicts(channel: string) { return use(channel, async client => { const { verified, conflict } = await loaded(client, true); return conflict && verified ? verified.heads.map(head => ({ event_id: head.event_id, author: head.sender, at: head.timestamp, state: validateEncryptedOrganization(head.fields.body) })) : []; }); },
  async resolveConflict(channel: string, selectedEventId: string) { return use(channel, async client => { const { record, verified, conflict } = await loaded(client, true); if (!record || !verified || !conflict) throw Error('The organization conflict changed. Refresh it.'); const selected = verified.heads.find(head => head.event_id === selectedEventId); if (!selected) throw Error('Choose a current verified version.'); await save(client, validateEncryptedOrganization(selected.fields.body), record); }); },
};
