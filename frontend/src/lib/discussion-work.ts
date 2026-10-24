import type { Message } from './api';
import type { ConversationWorkPage, ConversationWorkReference } from './conversation-work-api';
import type { Decision } from './collaboration-api';
import type { SharedDocumentRecord } from './shared-documents-api';
import type { TaskRecord } from './project-tasks-api';

export interface ResolvedConversationWork { reference: ConversationWorkReference; title: string; status?: string }
export interface DiscussionWorkReader {
  current: () => boolean;
  references: (offset: number) => Promise<ConversationWorkPage>;
  documents: () => Promise<SharedDocumentRecord[]>;
  decisions: () => Promise<Decision[]>;
  tasks: (projectId: string) => Promise<TaskRecord[]>;
}
export interface DiscussionWorkSnapshot {
  rows: ResolvedConversationWork[];
  documents: SharedDocumentRecord[];
  partial: boolean;
}

const availableDocument = (row: SharedDocumentRecord, channelId: string) => row.channel_id === channelId && !row.archived
  && !row.summary_event_id && !row.resolution_error && typeof row.title === 'string' && !!row.title.trim();
const resourceKey = (row: ResolvedConversationWork) => `${row.reference.resource_kind}:${row.reference.resource_id}`;
const status = (error: unknown) => error && typeof error === 'object' && 'status' in error ? Number(error.status) : 0;
const unavailable = (): DiscussionWorkSnapshot => ({ rows: [], documents: [], partial: true });

/** Read one native channel's real resources once, never one load per message. */
export async function readDiscussionWork(channelId: string, reader: DiscussionWorkReader): Promise<DiscussionWorkSnapshot | null> {
  if (!reader.current()) return null;
  const collect = async () => {
    const rows: ConversationWorkReference[] = [];
    let offset: number | null = 0;
    // The server retains at most 2,000 routing rows and accepts pages of 100.
    for (let pageNumber = 0; offset !== null && pageNumber <= 20; pageNumber++) {
      if (!reader.current()) return [];
      const page = await reader.references(offset);
      if (!reader.current()) return [];
      rows.push(...page.items.filter(row => row.channel_id === channelId));
      const next: number | null = page.next_offset;
      if (next !== null && (!Number.isInteger(next) || next <= offset || next > 2000)) throw new Error('Invalid work-reference cursor');
      offset = next;
    }
    if (offset !== null) throw new Error('Incomplete work-reference page');
    return rows;
  };
  const [referencesResult, documentsResult, decisionsResult] = await Promise.allSettled([
    collect(), reader.documents(), reader.decisions(),
  ]);
  if (!reader.current()) return null;
  // A current authority/binding rejection invalidates the entire earlier load,
  // including siblings that completed before access was revoked.
  if ([referencesResult, documentsResult, decisionsResult].some(result => result.status === 'rejected'
    && [401, 403, 404, 409].includes(status(result.reason)))) return unavailable();
  let partial = [referencesResult, documentsResult, decisionsResult].some(result => result.status === 'rejected');
  const references = referencesResult.status === 'fulfilled' ? referencesResult.value : [];
  const documents = documentsResult.status === 'fulfilled' ? documentsResult.value.filter(row => availableDocument(row, channelId)) : [];
  const decisions = decisionsResult.status === 'fulfilled'
    ? decisionsResult.value.filter(row => row.channel_id === channelId && row.content_mode === 'server' && !row.matrix_event_id && !row.resolution_error && !!row.title?.trim()) : [];
  // Older servers can still supply direct source links, but cannot establish
  // complete unopened-thread counts without their derived routing metadata.
  if (references.some(row => row.discussion_message_id === undefined)
    || decisions.some(row => !!row.source_message_id && row.discussion_message_id === undefined)) partial = true;
  const projectIds = [...new Set(references.filter(row => row.resource_kind === 'task' && !!row.project_id).map(row => row.project_id!))];
  const tasks: TaskRecord[] = [];
  // Bound simultaneous requests, without omitting work beyond the first boards.
  for (let index = 0; index < projectIds.length; index += 4) {
    if (!reader.current()) return null;
    const results = await Promise.allSettled(projectIds.slice(index, index + 4).map(id => reader.tasks(id)));
    if (!reader.current()) return null;
    if (results.some(result => result.status === 'rejected' && [401, 403, 409].includes(status(result.reason)))) return unavailable();
    for (const result of results) {
      if (result.status === 'fulfilled') tasks.push(...result.value);
      else partial = true;
    }
  }
  const documentById = new Map(documents.map(row => [row.id, row]));
  const taskById = new Map(tasks.filter(row => row.channel_id === channelId && !row.archived && row.content_mode === 'server'
    && !row.matrix_event_id && !row.resolution_error && !!row.fields?.title.trim()).map(row => [row.id, row]));
  const decisionById = new Map(decisions.map(row => [row.id, row]));
  const rows: ResolvedConversationWork[] = [];
  for (const reference of references) {
    if (!reference.source_message_id || !reference.resource_id || reference.discussion_message_id === null) continue;
    if (reference.resource_kind === 'document') {
      const row = documentById.get(reference.resource_id);
      if (row?.title) rows.push({ reference, title: row.title });
    } else if (reference.resource_kind === 'task') {
      const row = taskById.get(reference.resource_id);
      if (row?.fields && row.project_id === reference.project_id) rows.push({ reference, title: row.fields.title, status: row.fields.status });
    } else if (reference.resource_kind === 'decision') {
      const row = decisionById.get(reference.resource_id);
      if (row?.title) rows.push({ reference, title: row.title, status: row.status });
    }
  }
  // A decision's canonical source remains authoritative; no second index write.
  for (const decision of decisions) {
    if (!decision.source_message_id || decision.discussion_message_id === null || rows.some(row => row.reference.resource_kind === 'decision'
      && row.reference.resource_id === decision.id && row.reference.source_message_id === decision.source_message_id)) continue;
    rows.push({ reference: { id: decision.id, channel_id: channelId, source_message_id: decision.source_message_id,
      discussion_message_id: decision.discussion_message_id, resource_kind: 'decision', resource_id: decision.id,
      project_id: null, created_by: decision.created_by, created_at: decision.created_at }, title: decision.title!, status: decision.status });
  }
  return reader.current() ? { rows, documents, partial } : null;
}

/** Aggregate actual thread work, once per resource, retaining its exact source. */
export function groupDiscussionWork(rows: readonly ResolvedConversationWork[], messages: readonly Pick<Message, 'id' | 'channel_id' | 'parent_id' | 'deleted' | 'locked'>[], channelId: string) {
  const known = new Map(messages.filter(message => message.channel_id === channelId).map(message => [message.id, message]));
  const grouped = new Map<string, ResolvedConversationWork[]>();
  const seen = new Map<string, Set<string>>();
  for (const row of rows) {
    if (row.reference.channel_id !== channelId || !row.title.trim() || row.reference.discussion_message_id === null) continue;
    let source = row.reference.source_message_id;
    let complete = false, invalid = false;
    const visited = new Set<string>();
    for (let depth = 0; depth < 64; depth++) {
      if (visited.has(source)) { invalid = true; break; }
      visited.add(source);
      const message = known.get(source);
      if (!message) break;
      if (message.deleted || message.locked) { invalid = true; break; }
      if (!message.parent_id) { complete = true; break; }
      source = message.parent_id;
      if (depth === 63) invalid = true;
    }
    const authoritative = row.reference.discussion_message_id;
    if (invalid || authoritative && complete && authoritative !== source) continue;
    const discussionId = authoritative || (complete ? source : row.reference.source_message_id);
    const root = known.get(discussionId);
    if (root?.deleted || root?.locked) continue;
    const keys = seen.get(discussionId) || new Set<string>();
    const key = resourceKey(row);
    if (keys.has(key)) continue;
    keys.add(key); seen.set(discussionId, keys);
    grouped.set(discussionId, [...(grouped.get(discussionId) || []), row]);
  }
  return grouped;
}

export function discussionWorkCounts(rows: readonly ResolvedConversationWork[]) {
  const seen = new Set<string>();
  const counts = { document: 0, task: 0, decision: 0 };
  for (const row of rows) {
    const key = resourceKey(row);
    if (!seen.has(key) && Object.prototype.hasOwnProperty.call(counts, row.reference.resource_kind)) {
      seen.add(key); counts[row.reference.resource_kind]++;
    }
  }
  return counts;
}
