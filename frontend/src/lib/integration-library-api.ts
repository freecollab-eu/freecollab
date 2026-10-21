import { api } from './api';
import type { AppManifest } from './app-platform-api';
export interface IntegrationPackage { package_schema: number; manifest: AppManifest; publisher: string; license: string; source_url: string; setup: string; endpoint_example: string; tests: unknown[] }
export interface IntegrationRelease { id: string; app_id: string; version: string; package_digest: string; package: IntegrationPackage; status: string; checks: string[]; verification: string; reviewed_at?: string }
const workspace = (id: string) => '/workspaces/' + encodeURIComponent(id) + '/apps/library';
const json = (data: unknown) => ({ method: 'POST', body: JSON.stringify(data) });
export const integrationLibraryApi = {
  catalog: (query = '') => api.request<{ releases: IntegrationRelease[]; incomplete: boolean }>('/apps/library?q=' + encodeURIComponent(query)),
  own: (id: string) => api.request<{ releases: IntegrationRelease[] }>(workspace(id) + '/submissions'),
  submit: (id: string, data: unknown) => api.request<IntegrationRelease>(workspace(id) + '/submissions', json(data)),
  reviewQueue: () => api.request<{ releases: IntegrationRelease[] }>('/admin/integration-library'),
  review: (id: string, digest: string, approve: boolean) => api.request<IntegrationRelease>('/admin/integration-library/' + encodeURIComponent(id) + '/review', json({ package_digest: digest, approve })),
  install: (workspaceId: string, id: string, data: { package_digest: string; endpoint: string; authorization: string; channel_ids: string[]; confirm_scopes: boolean }) => api.request<{ signing_secret: string; callback_path: string }>(workspace(workspaceId) + '/' + encodeURIComponent(id) + '/install', json(data)),
};
