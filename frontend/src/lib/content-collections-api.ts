import { api } from './api';
import { getSecureMessaging } from './secure-client';
import { documentOrganizationApi } from './document-organization-api';
import { sharedDocumentsApi } from './shared-documents-api';
import { listChannelFiles } from './files-api';
import { documentCalendarApi } from './document-calendar-api';
import { projectTasksApi } from './project-tasks-api';
import { activityFeedApi } from './activity-feed-api';
import { collaborationApi } from './collaboration-api';
import { clipsApi } from './clips-api';
import { workflowsApi } from './workflows-api';

export interface CollectionItem { kind: 'document' | 'file' | 'linked-document' | 'task' | 'feed' | 'message' | 'project' | 'decision' | 'calendar' | 'clip' | 'workflow'; id: string; parent_id?: string | null; title?: string | null; available?: boolean; encrypted_resource?: boolean; encrypted_kind?: string; source_version?: string }
export interface ContentCollection { id: string; channel_id?: string; title: string; version: number; generation: string; created_by: string; created_at: string; items: CollectionItem[] }
export interface CollectionDraft { id?: string; title: string; items: CollectionItem[] }
const sourceRevision = (row: {id: string; revision?: number; matrix_event_id?: string | null}) => JSON.stringify([row.revision || row.id, row.matrix_event_id || null]);
const path = (channel: string) => '/channels/' + encodeURIComponent(channel) + '/content-collections';
const same = (owner: string | null) => { if (!owner || owner !== api.getAccessToken()) throw new Error('The signed-in account changed.'); };
async function encrypted(channel: string, owner: string | null) { same(owner); const client = await getSecureMessaging(); same(owner); const result = await client.handles(channel); same(owner); return result; }
async function secureApi() { return (await import('./encrypted-collections-api')).encryptedCollectionsApi; }

export async function collectionCatalog(channelId: string): Promise<CollectionItem[]> {
  const owner = api.getAccessToken(); same(owner);
  const channel = await api.getChannel(channelId); same(owner);
  const [documents, files, links, projects, feed, decisions, calendar, clips, workflows, messages] = await Promise.all([sharedDocumentsApi.list(channelId), listChannelFiles(channelId), documentCalendarApi.documents(channelId), projectTasksApi.projects(channelId), activityFeedApi.posts(channelId), collaborationApi.decisions(channelId), documentCalendarApi.calendar(channelId), clipsApi.list(channelId), workflowsApi.definitions(channel.workspace_id!, channelId), api.getMessages(channelId, { limit: 100 })]);
  same(owner);
  const result: CollectionItem[] = [
    ...documents.filter(row => !row.archived && !row.resolution_error).map(row => ({ kind: 'document' as const, id: row.id, title: row.title || 'Untitled document', source_version: JSON.stringify([row.version, row.update_count, row.summary_update_id, row.updated_at]) })),
    ...files.map(row => ({ kind: 'file' as const, id: row.id, title: row.filename, source_version: row.id })),
    ...links.filter(row => row.fields).map(row => ({ kind: 'linked-document' as const, id: row.id, title: row.fields!.title, source_version: sourceRevision(row) })),
    ...feed.items.filter(row => row.fields).map(row => ({ kind: 'feed' as const, id: row.id, title: row.fields!.title, source_version: sourceRevision(row) })),
    ...projects.filter(row => !row.archived && row.fields).map(row => ({ kind: 'project' as const, id: row.id, title: row.fields!.title, source_version: sourceRevision(row) })),
    ...decisions.filter(row => row.title && !row.resolution_error).map(row => ({ kind: 'decision' as const, id: row.id, title: row.title, source_version: JSON.stringify([row.revision, row.revision_id, row.matrix_event_id]) })),
    ...calendar.filter(row => row.fields).map(row => ({ kind: 'calendar' as const, id: row.id, title: row.fields!.title, source_version: sourceRevision(row) })),
    ...clips.filter(row => row.fields).map(row => ({ kind: 'clip' as const, id: row.id, title: row.fields!.title, source_version: sourceRevision(row) })),
    ...workflows.definitions.map(row => ({ kind: 'workflow' as const, id: row.id, title: row.fields.name, source_version: JSON.stringify([row.revision, row.updated_at]) })),
    ...messages.filter(row => !row.deleted).map(row => ({ kind: 'message' as const, id: row.id, title: row.content.slice(0, 120), parent_id: row.parent_id, source_version: JSON.stringify([row.edited_at, row.content, row.deleted]) })),
  ];
  for (const project of projects.filter(row => !row.archived)) {
    const tasks = await projectTasksApi.tasks(channelId, project.id); same(owner);
    result.push(...tasks.filter(row => !row.archived && row.fields).map(row => ({ kind: 'task' as const, id: row.id, parent_id: project.id, title: row.fields!.title, source_version: sourceRevision(row) })));
  }
  if (await encrypted(channelId, owner)) {
    const { EncryptedWorkClient } = await import('./encrypted-work-api'), client = new EncryptedWorkClient(channelId);
    try { const view = await client.load(); same(owner); for (const row of [...view.records, ...view.legacyRecords].filter(row => !row.selected.archived && !row.conflicts && !row.invalid.length)) { const kind = row.kind === 'linked-file' ? 'linked-document' : row.kind; if (['task', 'decision', 'workflow', 'feed', 'document', 'calendar', 'project', 'linked-document'].includes(kind) && !result.some(item => item.kind === kind && item.id === row.id)) result.push({ kind: kind as CollectionItem['kind'], id: row.id, parent_id: 'project_id' in row.selected.fields ? row.selected.fields.project_id : null, title: row.selected.fields.title, source_version: JSON.stringify(row.heads.map(head => head.event_id).sort()), encrypted_resource: true, encrypted_kind: row.kind }); } }
    finally { client.stop(); }
  }
  const catalog = await Promise.all(result.map(async item => ({ ...item, source_version: [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(item.source_version || item.id)))].map(byte => byte.toString(16).padStart(2, '0')).join('') }))); same(owner); return catalog;
}

async function verifiedItems(channel: string, items: CollectionItem[], owner: string | null) {
  if (items.length > 100 || new Set(items.map(item => item.kind + ':' + item.id)).size !== items.length) throw new Error('Choose up to 100 distinct collection items.');
  if (!items.length) return [];
  if (!await encrypted(channel, owner)) return items.map(item => ({ kind: item.kind, id: item.id, ...(item.parent_id ? { parent_id: item.parent_id } : {}) }));
  const catalog = await collectionCatalog(channel); same(owner);
  return items.map(item => {
    const found = catalog.find(row => row.kind === item.kind && row.id === item.id && (!item.parent_id || row.parent_id === item.parent_id));
    if (!found) throw new Error('A collection item is no longer available in this conversation.');
    return { kind: found.kind, id: found.id, ...(found.parent_id ? { parent_id: found.parent_id } : {}) };
  });
}

export const contentCollectionsApi = {
  async list(channel: string): Promise<ContentCollection[]> {
    const owner = api.getAccessToken();
    const rows = await encrypted(channel, owner) ? await (await secureApi()).list(channel) : await api.request<ContentCollection[]>(path(channel));
    same(owner); return rows;
  },
  async create(channel: string, draft: CollectionDraft): Promise<ContentCollection> {
    const owner = api.getAccessToken(), items = await verifiedItems(channel, draft.items, owner), body = { id: draft.id || crypto.randomUUID(), title: draft.title, items };
    const row = await encrypted(channel, owner) ? await (await secureApi()).create(channel, body) : await api.request<ContentCollection>(path(channel), { method: 'POST', body: JSON.stringify(body) });
    same(owner); return row;
  },
  async add(channel: string, row: ContentCollection, item: CollectionItem): Promise<ContentCollection> {
    const owner = api.getAccessToken(), [verified] = await verifiedItems(channel, [item], owner);
    const result = await encrypted(channel, owner) ? await (await secureApi()).addItem(channel, row, verified) : await api.request<ContentCollection>(path(channel) + '/' + encodeURIComponent(row.id) + '/items', { method: 'POST', body: JSON.stringify({ expected_version: row.version, generation: row.generation, item: verified }) });
    same(owner); return result;
  },
  async remove(channel: string, row: ContentCollection, item: CollectionItem): Promise<ContentCollection> {
    const owner = api.getAccessToken(), body = { kind: item.kind, id: item.id, ...(item.parent_id ? { parent_id: item.parent_id } : {}) };
    const result = await encrypted(channel, owner) ? await (await secureApi()).removeItem(channel, row, body) : await api.request<ContentCollection>(path(channel) + '/' + encodeURIComponent(row.id) + '/items', { method: 'DELETE', body: JSON.stringify({ expected_version: row.version, generation: row.generation, item: body }) });
    same(owner); return result;
  },
  async rename(channel: string, row: ContentCollection, title: string): Promise<void> {
    const owner = api.getAccessToken();
    if (await encrypted(channel, owner)) await (await secureApi()).rename(channel, row, title);
    else await documentOrganizationApi.renameStack(channel, { ...row, channel_id: channel, updated_at: row.created_at }, title);
    same(owner);
  },
  async removeCollection(channel: string, row: ContentCollection): Promise<void> {
    const owner = api.getAccessToken();
    if (await encrypted(channel, owner)) await (await secureApi()).removeCollection(channel, row);
    else await documentOrganizationApi.deleteStack(channel, { ...row, channel_id: channel, updated_at: row.created_at });
    same(owner);
  },
};
