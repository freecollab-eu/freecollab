import { api } from './api';
import { REFERENCE_KINDS, referenceKey, type MessageReference, type ReferenceCandidate, type ResolvedReference } from './message-references';

export interface NotificationGroup {
  id: string; channel_id: string; name: string; member_ids: string[]; version: number;
  created_by?: string; created_at?: string; updated_at?: string;
}

const base = (channelId: string) => `/mentions/channels/${encodeURIComponent(channelId)}`;
let authorityVersion = 0;
if (typeof window !== 'undefined') {
  for (const event of ['freecollab:auth-changed', 'freecollab:workspace-changed']) window.addEventListener(event, () => { authorityVersion++; });
}
const owner = () => ({ token: api.getAccessToken(), actor: api.getCurrentUserId(), version: authorityVersion });
function current(request: ReturnType<typeof owner>, signal?: AbortSignal) {
  if (signal?.aborted) throw new DOMException('Reference request cancelled.', 'AbortError');
  if (!request.token || request.token !== api.getAccessToken() || request.actor !== api.getCurrentUserId() || request.version !== authorityVersion)
    throw new Error('The signed-in account or workspace changed.');
}
function candidate(row: ReferenceCandidate, channelId: string): ReferenceCandidate {
  if (!row || !REFERENCE_KINDS.includes(row.kind) || typeof row.id !== 'string' || !row.id || row.id.length > 128 || /[\s\u0000-\u001f\u007f]/.test(row.id)
    || typeof row.label !== 'string' || !row.label.trim() || row.label.length > 1000 || /[\r\n\u0000-\u001f]/.test(row.label)
    || row.channel_id && row.channel_id !== channelId) throw new Error('This reference is not available in the current conversation.');
  return { kind: row.kind, id: row.id, label: row.label.slice(0, 200).replace(/[\uD800-\uDBFF]$/, ''), description: typeof row.description === 'string' ? row.description.slice(0, 400) : undefined,
    channel_id: channelId, project_id: typeof row.project_id === 'string' ? row.project_id : undefined,
    member_count: Number.isSafeInteger(row.member_count) && row.member_count! >= 0 ? row.member_count : undefined };
}
function group(row: NotificationGroup, channelId: string): NotificationGroup {
  if (!row || row.channel_id !== channelId || !row.id || !row.name || row.name.length > 80 || !Number.isSafeInteger(row.version) || row.version < 1
    || !Array.isArray(row.member_ids) || row.member_ids.length > 50 || row.member_ids.some(id => typeof id !== 'string' || !id))
    throw new Error('This group is not available in the current conversation.');
  return { ...row, member_ids: [...new Set(row.member_ids)] };
}
const matches = (value: string, query: string) => {
  const normalize = (text: string) => text.normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  return normalize(query).trim().split(/\s+/).every(word => normalize(value).includes(word));
};

/** Secure resource labels are resolved only through the existing verified-device clients. */
async function secureCandidates(channelId: string, query: string, request: ReturnType<typeof owner>, signal?: AbortSignal, targets?: Pick<ResolvedReference, 'kind' | 'id' | 'project_id'>[]): Promise<ReferenceCandidate[]> {
  const [{ getSecureMessaging }, { sharedDocumentsApi }, { projectTasksApi }, { listChannelFiles }] = await Promise.all([
    import('./secure-client'), import('./shared-documents-api'), import('./project-tasks-api'), import('./files-api'),
  ]);
  current(request, signal);
  const secure = await getSecureMessaging();
  if (!await secure.handles(channelId)) throw new Error('This encrypted conversation could not be verified.');
  current(request, signal);
  const wants = (kind: MessageReference['kind']) => !targets || targets.some(row => row.kind === kind);
  const projectTargets = targets ? [...new Set(targets.filter(row => row.kind === 'task' && row.project_id).map(row => row.project_id!))].slice(0, 8) : null;
  const [documents, projects, files] = await Promise.allSettled([
    wants('document') ? sharedDocumentsApi.list(channelId) : Promise.resolve([]),
    wants('task') && !projectTargets ? projectTasksApi.projects(channelId) : Promise.resolve([]),
    wants('file') ? listChannelFiles(channelId) : Promise.resolve([]),
  ]);
  current(request, signal);
  const result: ReferenceCandidate[] = [];
  if (documents.status === 'fulfilled') for (const row of documents.value) {
    if (row.channel_id === channelId && !row.archived && !row.resolution_error && row.title && matches(row.title, query))
      result.push({ kind: 'document', id: row.id, label: row.title, channel_id: channelId });
  }
  if (files.status === 'fulfilled') for (const row of files.value) {
    if (row.channel_id === channelId && row.filename && matches(row.filename, query)) result.push({ kind: 'file', id: row.id, label: row.filename, channel_id: channelId });
  }
  if (projects.status === 'fulfilled') {
    const projectIds = projectTargets || projects.value.filter(row => row.channel_id === channelId && !row.archived && row.fields).slice(0, 8).map(row => row.id);
    const rows = await Promise.allSettled(projectIds.map(id => projectTasksApi.tasks(channelId, id)));
    current(request, signal);
    for (const page of rows) if (page.status === 'fulfilled') for (const row of page.value) {
      if (row.channel_id === channelId && !row.archived && row.fields && matches(row.fields.title, query))
        result.push({ kind: 'task', id: row.id, label: row.fields.title, channel_id: channelId, project_id: row.project_id || undefined });
    }
  }
  return result.filter(row => !targets || targets.some(target => referenceKey(target) === referenceKey(row))).map(row => candidate(row, channelId));
}

export const messageReferencesApi = {
  async suggestions(channelId: string, query = '', signal?: AbortSignal, secure = false): Promise<ReferenceCandidate[]> {
    const request = owner(); current(request, signal);
    const parameters = new URLSearchParams({ query: query.slice(0, 80) });
    const rows = await api.request<ReferenceCandidate[]>(`${base(channelId)}/suggestions?${parameters}`, { signal });
    current(request, signal);
    if (!Array.isArray(rows) || rows.length > 100) throw new Error('Invalid reference suggestions.');
    const verified = rows.filter(row => !secure || !['document', 'task', 'file'].includes(row.kind)).map(row => candidate(row, channelId));
    if (secure) verified.push(...await secureCandidates(channelId, query, request, signal));
    current(request, signal);
    const seen = new Set<string>();
    return verified.filter(row => { const key = referenceKey(row); if (seen.has(key)) return false; seen.add(key); return true; }).slice(0, 50);
  },
  async resolve(channelId: string, references: Pick<MessageReference, 'kind' | 'id'>[], signal?: AbortSignal, secure = false): Promise<ResolvedReference[]> {
    const request = owner(); current(request, signal);
    if (references.length > 50) throw new Error('Too many message references.');
    const unique = [...new Map(references.map(row => [referenceKey(row), { kind: row.kind, id: row.id }])).values()];
    if (!unique.length) return [];
    // Matrix file event identities only exist on verified devices, not the native file index.
    const serverTargets = unique.filter(row => !secure || row.kind !== 'file');
    const result = serverTargets.length ? await api.request<{ items: ResolvedReference[] }>(`${base(channelId)}/references/resolve`, { method: 'POST', body: JSON.stringify({ references: serverTargets }), signal }) : { items: [] };
    current(request, signal);
    if (!result || !Array.isArray(result.items) || result.items.length > serverTargets.length) throw new Error('Invalid reference response.');
    const byId = new Map<string, ResolvedReference>();
    for (const row of result.items) {
      const key = referenceKey(row);
      if (!serverTargets.some(item => referenceKey(item) === key) || byId.has(key) || typeof row.available !== 'boolean') throw new Error('Invalid reference response.');
      if (row.channel_id && row.channel_id !== channelId) throw new Error('Invalid reference response.');
      if (!row.available || secure && ['document', 'task', 'file'].includes(row.kind)) byId.set(key, { kind: row.kind, id: row.id, label: '', available: false, project_id: typeof row.project_id === 'string' ? row.project_id : undefined });
      else {
        const verified = candidate(row, channelId);
        if (row.user_ids && (!Array.isArray(row.user_ids) || row.user_ids.length > 50 || row.user_ids.some(id => typeof id !== 'string' || !id))) throw new Error('Invalid reference recipients.');
        byId.set(key, { ...verified, available: true, user_ids: row.user_ids ? [...new Set(row.user_ids)] : undefined });
      }
    }
    if (secure && unique.some(row => ['document', 'task', 'file'].includes(row.kind))) {
      const local = await secureCandidates(channelId, '', request, signal, unique.map(row => byId.get(referenceKey(row)) || { ...row, label: '', available: false })); current(request, signal);
      for (const row of local) if (unique.some(item => referenceKey(item) === referenceKey(row))) byId.set(referenceKey(row), { ...row, available: true });
      const historicalFiles = unique.filter(row => row.kind === 'file' && !byId.get(referenceKey(row))?.available);
      if (historicalFiles.length) {
        const { verifiedSecureFileReferenceIds } = await import('./secure/files'); current(request, signal);
        try {
          const verified = await verifiedSecureFileReferenceIds(channelId, historicalFiles.map(row => row.id)); current(request, signal);
          for (const row of historicalFiles) if (verified.has(row.id)) byId.set(referenceKey(row), { ...row, available: true, label: 'Encrypted file', channel_id: channelId });
        } catch { current(request, signal); }
      }
    }
    return unique.map(row => byId.get(referenceKey(row)) || { ...row, available: false, label: '' });
  },
  async groups(channelId: string, signal?: AbortSignal): Promise<NotificationGroup[]> {
    const request = owner(); current(request, signal);
    const rows = await api.request<NotificationGroup[]>(`${base(channelId)}/groups`, { signal }); current(request, signal);
    if (!Array.isArray(rows) || rows.length > 100) throw new Error('Invalid groups response.');
    return rows.map(row => group(row, channelId));
  },
  async createGroup(channelId: string, input: { name: string; member_ids: string[] }): Promise<NotificationGroup> {
    const request = owner(); current(request);
    const row = await api.request<NotificationGroup>(`${base(channelId)}/groups`, { method: 'POST', body: JSON.stringify(input) }); current(request);
    return group(row, channelId);
  },
  async updateGroup(channelId: string, id: string, input: { expected_version: number; name?: string; member_ids?: string[] }): Promise<NotificationGroup> {
    const request = owner(); current(request);
    const row = await api.request<NotificationGroup>(`${base(channelId)}/groups/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(input) }); current(request);
    if (row.id !== id) throw new Error('This group is no longer available.'); return group(row, channelId);
  },
  async removeGroup(channelId: string, id: string, expectedVersion: number): Promise<void> {
    const request = owner(); current(request);
    await api.request(`${base(channelId)}/groups/${encodeURIComponent(id)}?expected_version=${expectedVersion}`, { method: 'DELETE' }); current(request);
  },
};
