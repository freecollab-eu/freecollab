import { api } from './api';
import { getSecureMessaging } from './secure-client';
import type { ManagedFile } from './files-api';
import { syncApi, type SyncInput } from './enterprise-api';
import type { SSOReauthentication } from './integrations-api';

export type CloudProvider = 'google' | 'microsoft' | 'dropbox' | 'box';
export interface CloudConnection { id: string; name: string; provider: CloudProvider; revision: number; status: string; root_folder_id: string; connected: boolean }
export interface CloudEntry { connection_id: string; connection_revision: number; id: string; name: string; folder: boolean; parent_id: string; etag?: string; mime_type?: string; size?: number }
export interface CloudPage { connection_id: string; folder_id: string; items: CloudEntry[]; next_page_token: string | null; connection_revision: number }
export interface CloudOverview { secure: boolean; can_manage: boolean; sync_enabled: boolean; callback_url: string; sync_callback_url: string; connections: CloudConnection[] }
export interface CloudLinkFields { provider: CloudProvider; title: string; url: string; external_access_acknowledged: true }
export interface CloudLink { id: string; workspace_id: string; channel_id: string; created_by: string; created_at: string; content_mode: 'matrix' | 'server'; matrix_event_id: string | null; fields?: CloudLinkFields; resolution_error?: string }
export interface CloudApplication extends SSOReauthentication { name: string; provider: CloudProvider; client_id: string; client_secret?: string; folder_id: string; tenant_id?: string; drive_id?: string; consent: true }
const segment = encodeURIComponent;
const base = (workspace: string, channel: string) => `/workspaces/${segment(workspace)}/channels/${segment(channel)}/cloud-files`;
const eventType = 'eu.freecollab.cloud-file';
const body = (value: unknown) => ({ method: 'POST', body: JSON.stringify(value) });
function sessionGuard() { const token = api.getAccessToken(); return () => { if (!token || token !== api.getAccessToken()) throw new Error('The signed-in account changed.'); }; }
async function request<T>(path: string, init?: RequestInit) { const current = sessionGuard(); const result = await api.request<T>(path, init); current(); return result; }

export function validateCloudLink(value: unknown): CloudLinkFields {
  const v = value as Record<string, unknown>;
  if (!v || typeof v !== 'object' || Array.isArray(v) || Object.keys(v).some(key => !['provider', 'title', 'url', 'external_access_acknowledged'].includes(key))
      || !['google', 'microsoft', 'dropbox', 'box'].includes(String(v.provider)) || typeof v.title !== 'string' || !v.title.trim() || v.title.length > 300
      || typeof v.url !== 'string' || v.url.length > 8192 || /[\x00-\x20\x7f\\]/.test(v.url) || v.external_access_acknowledged !== true) throw new Error('Choose a provider file and acknowledge its external access permissions.');
  let url: URL; try { url = new URL(v.url); } catch { throw new Error('Choose an HTTPS file link from the selected provider.'); }
  const hosts: Record<string, string[]> = { google: ['drive.google.com', 'docs.google.com'], microsoft: ['onedrive.live.com', '1drv.ms'], dropbox: ['www.dropbox.com', 'dropbox.com'], box: ['app.box.com'] };
  const paths: Record<string, string[]> = { google: ['/file/d/', '/document/d/', '/spreadsheets/d/', '/presentation/d/', '/open'], microsoft: ['/'], dropbox: ['/s/', '/scl/fi/'], box: ['/file/', '/s/'] };
  const provider = String(v.provider);
  if (url.protocol !== 'https:' || url.username || url.password || url.port && url.port !== '443' || url.pathname === '/'
      || !hosts[provider].includes(url.hostname) && !(provider === 'microsoft' && url.hostname.endsWith('.sharepoint.com'))
      || !paths[provider].some(path => url.pathname.toLowerCase().startsWith(path))
      || [...url.searchParams.keys()].some(key => ['access_token', 'refresh_token', 'code', 'id_token', 'client_secret'].includes(key.toLowerCase()))) throw new Error('Choose an HTTPS file link from the selected provider.');
  return { provider: provider as CloudProvider, title: v.title.trim(), url: v.url, external_access_acknowledged: true };
}
async function resolveLink(row: CloudLink): Promise<CloudLink> {
  try {
    if (row.content_mode === 'server') return { ...row, fields: validateCloudLink(row.fields) };
    if (!row.matrix_event_id) throw new Error('The encrypted provider-link reference is missing.');
    const secure = await getSecureMessaging(), event = await secure.getDecryptedEvent(row.channel_id, row.matrix_event_id);
    const { c, roomId } = await secure.roomFor(row.channel_id), content = event.getContent();
    const domain = c.getUserId()!.split(':').slice(1).join(':');
    if (event.getRoomId() !== roomId || event.getType() !== eventType || event.isRedacted()
        || event.getSender() !== `@fc_${row.created_by}:${domain}` || content.id !== row.id || content.channel_id !== row.channel_id
        || content.workspace_id !== row.workspace_id || content.revision !== 1) throw new Error('This encrypted provider link could not be verified.');
    return { ...row, fields: validateCloudLink(content.fields) };
  } catch (failure) { return { ...row, fields: undefined, resolution_error: failure instanceof Error ? failure.message : 'Unlock recovery to read this provider link.' }; }
}

export const cloudFilesApi = {
  connections: (workspace: string, channel: string) => request<CloudOverview>(base(workspace, channel) + '/connections'),
  async browse(workspace: string, channel: string, connection: CloudConnection, folder: string | null = null, page: string | null = null) {
    const result = await request<CloudPage>(base(workspace, channel) + '/connections/' + segment(connection.id) + '/browse', body({ connection_revision: connection.revision, folder_id: folder, page_token: page }));
    if (result.connection_id !== connection.id || result.connection_revision !== connection.revision
        || result.items.some(entry => entry.connection_id !== connection.id || entry.connection_revision !== connection.revision)) throw new Error('The cloud connection changed; reload before continuing');
    return result;
  },
  importFile(workspace: string, channel: string, connection: string, entry: CloudEntry, requestId: string, target?: ManagedFile) {
    if (entry.connection_id !== connection || !Number.isSafeInteger(entry.connection_revision) || entry.connection_revision < 1) throw new Error('Reload and select a file from the current cloud connection.');
    return request<ManagedFile>(base(workspace, channel) + '/connections/' + segment(connection) + '/import', body({ connection_revision: entry.connection_revision,
      request_id: requestId, file_id: entry.id, parent_id: entry.parent_id, etag: entry.etag, consent: true,
      ...(target ? { document_id: target.document_id, expected_version: target.version_count } : {}) }));
  },
  async create(workspace: string, channel: string, draft: CloudApplication): Promise<CloudConnection> {
    const current = sessionGuard();
    if (draft.provider === 'google' || draft.provider === 'microsoft') {
      const config: Record<string, string> = { client_id: draft.client_id, folder_id: draft.folder_id };
      if (draft.provider === 'microsoft') { config.tenant_id = draft.tenant_id || ''; config.drive_id = draft.drive_id || ''; }
      const input: SyncInput = { name: draft.name, provider: draft.provider, kind: 'files', channel_id: channel, config,
        credentials: draft.client_secret ? { client_secret: draft.client_secret } : undefined, consent: true, password: draft.password, code: draft.code };
      const row = await syncApi.create(workspace, input); current();
      return { id: 'sync:' + row.id, name: row.name, provider: row.provider as CloudProvider, revision: row.revision, status: row.status, root_folder_id: draft.folder_id, connected: false };
    }
    return request<CloudConnection>(base(workspace, channel) + '/connections', body({ name: draft.name, provider: draft.provider, client_id: draft.client_id,
      client_secret: draft.client_secret || undefined, folder_id: draft.folder_id, password: draft.password, code: draft.code, consent: true }));
  },
  async authorize(workspace: string, channel: string, connection: CloudConnection, values: SSOReauthentication) {
    const current = sessionGuard();
    const result = connection.id.startsWith('sync:') ? await syncApi.authorize(workspace, connection.id.slice(5), values)
      : await request<{ authorization_url: string }>(base(workspace, channel) + '/connections/' + segment(connection.id.slice(6)) + '/oauth/start', body(values));
    current(); return result;
  },
  async disconnect(workspace: string, channel: string, connection: CloudConnection, values: SSOReauthentication) {
    const current = sessionGuard();
    if (connection.id.startsWith('sync:')) await syncApi.action(workspace, connection.id.slice(5), 'disconnect', values);
    else await request(base(workspace, channel) + '/connections/' + segment(connection.id.slice(6)) + '/disconnect', body(values));
    current();
  },
  async links(workspace: string, channel: string, before?: string) {
    const current = sessionGuard();
    const page = await request<{ items: CloudLink[]; next_before: string | null }>(base(workspace, channel) + '/links' + (before ? '?before=' + segment(before) : ''));
    const items = await Promise.all(page.items.map(resolveLink)); current(); return { items, next_before: page.next_before };
  },
  async addLink(workspace: string, channel: string, raw: CloudLinkFields, id = crypto.randomUUID()) {
    const current = sessionGuard(), fields = validateCloudLink(raw), secure = await getSecureMessaging(); current();
    const encrypted = await secure.handles(channel); current();
    let payload: Record<string, unknown> = { id, fields };
    if (encrypted) {
      const { c } = await secure.roomFor(channel, true); current();
      const trust = await c.getCrypto()!.getDeviceVerificationStatus(c.getUserId()!, c.getDeviceId()!); current();
      if (!trust?.isVerified()) throw new Error('Verify this device or unlock recovery before sharing an encrypted provider link.');
      const event = await secure.sendEvent(channel, eventType, { id, channel_id: channel, workspace_id: workspace, revision: 1, fields }); current();
      payload = { id, matrix_event_id: event.event_id };
    }
    const result = await resolveLink(await request<CloudLink>(base(workspace, channel) + '/links', body(payload))); current(); return result;
  },
  removeLink: (workspace: string, channel: string, id: string) => request(base(workspace, channel) + '/links/' + segment(id), { method: 'DELETE' }),
};
