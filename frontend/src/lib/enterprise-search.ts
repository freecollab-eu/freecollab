import { api, type Channel } from './api';
import { getSecureMessaging } from './secure-client';
import { readSharedDocumentForSearch, sharedDocumentsApi } from './shared-documents-api';
import { projectTasksApi } from './project-tasks-api';
import { activityFeedApi } from './activity-feed-api';
import { downloadChannelFile, listChannelFiles } from './files-api';

export type SearchKind = 'document' | 'project' | 'task' | 'feed' | 'file-content' | 'message' | 'decision' | 'calendar' | 'clip' | 'workflow' | 'linked-document';
export interface SearchSource { id: string; channel_id: string; kind: SearchKind; title: string; text: string;
  created_by: string; created_at: string; parent_id: string | null; truncated: boolean; version: string;
  reference?: string; secure?: boolean; encrypted_resource?: boolean; encrypted_kind?: string }
export interface SearchSources { items: SearchSource[]; complete: boolean; skipped: { kind: string; id: string; status: number }[] }
export interface SearchFilters { channelId?: string; author?: string; after?: string; before?: string; kind?: string; documentId?: string }
export interface SearchConnection { id: string; name: string; provider: 'google' | 'microsoft' | 'custom'; revision: number; config: Record<string, string>; scope: string }
export interface ExternalSearchHit { id: string; title: string; text: string; url: string; provider: string; reference: string; acl_checked: boolean }
const segment = encodeURIComponent;
const root = (workspaceId: string) => '/workspaces/' + segment(workspaceId) + '/enterprise-search/connections';
const current = (owner: string | null, signal?: AbortSignal) => {
  if (!owner || owner !== api.getAccessToken()) throw new Error('The signed-in account changed.');
  if (signal?.aborted) throw new DOMException('Cancelled', 'AbortError');
};

export function sourceMatches(source: SearchSource, query: string, filters: SearchFilters = {}): boolean {
  if (filters.channelId && source.channel_id !== filters.channelId || filters.author && source.created_by !== filters.author
    || filters.after && source.created_at.slice(0, 10) < filters.after || filters.before && source.created_at.slice(0, 10) > filters.before
    || filters.kind && source.kind !== filters.kind || filters.documentId && source.id !== filters.documentId) return false;
  const haystack = (source.title + '\n' + source.text).toLocaleLowerCase();
  return query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean).every(term => haystack.includes(term));
}

export async function loadSearchSources(channel: Channel, signal?: AbortSignal): Promise<SearchSources> {
  const owner = api.getAccessToken(), secure = await getSecureMessaging(); current(owner, signal);
  if (!await secure.handles(channel.id)) {
    current(owner, signal);
    const result = await api.request<SearchSources>('/channels/' + segment(channel.id) + '/search/sources', { signal });
    current(owner, signal); return result;
  }
  // Only opaque identifiers leave this browser. Existing API adapters verify
  // Matrix room, sender, event type and every content revision before use.
  const items: SearchSource[] = [], skipped: SearchSources['skipped'] = [];
  const add = (source: SearchSource) => { if (items.reduce((size, row) => size + row.text.length, 0) + source.text.length > 1500000) throw new Error('Local search text limit reached.'); items.push({ ...source, secure: true }); };
  const docs = await sharedDocumentsApi.list(channel.id); current(owner, signal);
  for (const doc of docs) {
    try { const value = await readSharedDocumentForSearch(channel.id, doc.id, signal); current(owner, signal);
      add({ id: doc.id, kind: 'document', channel_id: channel.id, title: value.title || 'Untitled document', text: value.text,
        created_by: doc.created_by, created_at: doc.created_at, parent_id: null, truncated: false, version: String(value.sequence) });
    } catch (error) { current(owner, signal); skipped.push({ kind: 'document', id: doc.id, status: 409 }); }
  }
  const projects = await projectTasksApi.projects(channel.id); current(owner, signal);
  for (const project of projects.filter(row => !row.archived)) {
    if (project.fields) add({ id: project.id, kind: 'project', channel_id: channel.id, title: project.fields.title, text: project.fields.description,
      created_by: project.created_by, created_at: project.created_at, parent_id: null, truncated: false, version: String(project.revision) });
    else skipped.push({ kind: 'project', id: project.id, status: 409 });
    const tasks = await projectTasksApi.tasks(channel.id, project.id); current(owner, signal);
    for (const task of tasks.filter(row => !row.archived)) {
      if (!task.fields) { skipped.push({ kind: 'task', id: task.id, status: 409 }); continue; }
      add({ id: task.id, kind: 'task', channel_id: channel.id, title: task.fields.title,
        text: task.fields.description + '\n' + task.fields.checklist.map(item => item.title).join('\n'),
        created_by: task.created_by, created_at: task.created_at, parent_id: project.id, truncated: false, version: String(task.revision) });
    }
  }
  let beforeId: string | undefined, complete = true;
  for (let pageNumber = 0; pageNumber < 20; pageNumber++) {
    const page = await activityFeedApi.posts(channel.id, { beforeId }); current(owner, signal);
    for (const post of page.items) {
      if (!post.fields) { skipped.push({ kind: 'feed', id: post.id, status: 409 }); continue; }
      add({ id: post.id, kind: 'feed', channel_id: channel.id, title: post.fields.title,
        text: post.fields.body + (post.fields.poll ? '\n' + post.fields.poll.question + '\n' + post.fields.poll.options.map(item => item.label).join('\n') : ''),
        created_by: post.created_by, created_at: post.created_at, parent_id: null, truncated: false, version: String(post.revision) });
    }
    if (!page.next_before_id) break;
    if (page.next_before_id === beforeId) throw new Error('Feed pagination did not advance.');
    beforeId = page.next_before_id; if (pageNumber === 19) complete = false;
  }
  // Download/decrypt supported small files through the existing verified file
  // path. PDF text is extracted in PDF.js's worker; no scripts/actions run.
  const files = await listChannelFiles(channel.id); current(owner, signal);
  const supported = files.filter(file => file.content_type.startsWith('text/') || file.content_type === 'application/json' || file.content_type === 'application/pdf');
  if (supported.length > 12) complete = false;
  for (const file of supported.slice(0, 12)) {
    if (file.size > 2 * 1024 * 1024) { skipped.push({kind:'file-content', id:file.id, status:413}); continue; }
    try {
      const blob = await downloadChannelFile(file, {signal}); current(owner, signal);
      if (blob.size > 2 * 1024 * 1024) throw new Error('File exceeds local search limit.');
      let text = '', truncated = false;
      if (file.content_type === 'application/pdf') {
        const [pdfjs, worker] = await Promise.all([import('pdfjs-dist'), import('pdfjs-dist/build/pdf.worker.min.mjs?url')]); current(owner, signal);
        pdfjs.GlobalWorkerOptions.workerSrc = worker.default;
        const task = pdfjs.getDocument({data: new Uint8Array(await blob.arrayBuffer()), disableFontFace:true, useSystemFonts:false, disableAutoFetch:true, stopAtErrors:true});
        const cancel = () => { void task.destroy(); }; signal?.addEventListener('abort', cancel, {once:true});
        try {
          const document = await task.promise; current(owner, signal);
          for (let page = 1; page <= Math.min(document.numPages, 40); page++) {
            const content = await (await document.getPage(page)).getTextContent(); current(owner, signal);
            text += content.items.map(item => 'str' in item ? item.str : '').join(' ') + '\n';
            if (text.length > 100000) { text = text.slice(0, 100000); truncated = true; break; }
          }
          truncated ||= document.numPages > 40;
        } finally { signal?.removeEventListener('abort', cancel); await task.destroy(); }
      } else {
        text = new TextDecoder('utf-8', {fatal:true}).decode(await blob.arrayBuffer()); current(owner, signal);
        if (text.includes('\0')) throw new Error('Binary text source.');
        truncated = text.length > 100000; text = text.slice(0, 100000);
      }
      add({id:file.id, channel_id:channel.id, kind:'file-content', title:file.filename, text, truncated,
        created_by:file.document_owner_id || file.uploader_id, created_at:file.created_at, parent_id:null, version:file.id});
      if (truncated) complete = false;
    } catch (error) { current(owner, signal); skipped.push({kind:'file-content', id:file.id, status:422}); }
  }
  const additional = await (await import('./encrypted-search-sources')).additionalEncryptedSources(channel, signal); current(owner, signal);
  for (const source of additional.items) if (!items.some(row => row.id === source.id && row.kind === source.kind)) add(source);
  skipped.push(...additional.skipped); complete &&= additional.complete;
  return { items, skipped, complete: complete && !skipped.length };
}

export async function verifySearchSource(source: SearchSource, channel: Channel, signal?: AbortSignal): Promise<SearchSource> {
  const owner = api.getAccessToken();
  if (source.reference && !source.secure) {
    const result = await api.request<SearchSource>('/search/resolve', { method: 'POST', signal, body: JSON.stringify({ reference: source.reference }) });
    current(owner, signal); if (result.channel_id !== channel.id || result.id !== source.id || result.kind !== source.kind || result.version !== source.version) throw new Error('This source changed or moved. Refresh search.'); return result;
  }
  // Re-fetch current membership and verify the current encrypted revision.
  const latest = await loadSearchSources(channel, signal); current(owner, signal);
  const result = latest.items.find(item => item.kind === source.kind && item.id === source.id && item.version === source.version);
  if (!result) throw new Error('This source changed or access was removed. Refresh search.');
  return result;
}

export const enterpriseSearchApi = {
  connections: (workspaceId: string) => api.request<SearchConnection[]>(root(workspaceId)),
  connect: (workspaceId: string, body: { name: string; provider: SearchConnection['provider']; config: Record<string, string>; access_token: string; delegated_access_acknowledged: boolean }) =>
    api.request<SearchConnection>(root(workspaceId), { method: 'POST', body: JSON.stringify(body) }),
  disconnect: (workspaceId: string, id: string) => api.request(root(workspaceId) + '/' + segment(id), { method: 'DELETE' }),
  query: (workspaceId: string, connection: SearchConnection, query: string) => api.request<{ items: ExternalSearchHit[]; complete: boolean }>(root(workspaceId) + '/' + segment(connection.id) + '/query',
    { method: 'POST', body: JSON.stringify({ query, connection_revision: connection.revision, limit: 10, share_query_with_provider: true }) }),
  resolve: (reference: string) => api.request<ExternalSearchHit>('/enterprise-search/resolve', { method: 'POST', body: JSON.stringify({ reference }) }),
};
