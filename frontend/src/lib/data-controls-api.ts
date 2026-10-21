import { api } from './api';

export interface Reauthentication { password: string; code?: string }
export interface RetentionPolicy { enabled: boolean; hold: boolean; retention_days: number; revision: number; automatic_purge: false }
export interface DataChannel { id: string; name: string | null; secure: boolean; is_admin: boolean }
export interface DataJob {
  id: string; kind: 'export' | 'purge'; status: 'queued' | 'processing' | 'ready' | 'erasing_files' | 'completed' | 'failed' | 'expired';
  counts: Record<string, number>; created_at: string; finished_at: string | null; expires_at: string;
  artifact_size: number | null; sha256: string | null; failure: string | null; download_available: boolean;
}
export interface DataState { policy: RetentionPolicy; channels: DataChannel[]; jobs: DataJob[]; limits: { channels: number; records: number; archive_bytes: number } }
export interface DataPreview { id: string; counts: Record<string, number>; channels: { channel_id: string; counts: Record<string, number> }[]; cutoff: string; expires_at: string; policy: RetentionPolicy; confirmation: string; scope: string }
export interface ClosurePreview {
  can_erase_identity: boolean; fingerprint: string; confirmation: 'ERASE MY ACCOUNT'; effect: string;
  blocking_workspaces: string[]; blocking_channels: string[]; blocking_installation_admin: boolean; active_data_jobs: string[];
  memberships: [string, string][]; channels: [string, string][]; empty_personal_workspaces: string[];
}
const base = (workspace: string) => `/workspaces/${encodeURIComponent(workspace)}/data-controls`;
const body = (data: unknown, method = 'POST') => ({ method, body: JSON.stringify(data) });
export const dataControlsApi = {
  state: (workspace: string) => api.request<DataState>(base(workspace)),
  policy: (workspace: string, data: Reauthentication & { enabled: boolean; hold: boolean; retention_days: number; expected_revision: number }) => api.request<RetentionPolicy>(base(workspace) + '/policy', body(data, 'PUT')),
  preview: (workspace: string, data: Reauthentication & { channel_ids: string[] }) => api.request<DataPreview>(base(workspace) + '/previews', body(data)),
  export: (workspace: string, data: Reauthentication & { channel_ids: string[]; acknowledge_plaintext_export: true }) => api.request<DataJob>(base(workspace) + '/exports', body(data)),
  purge: (workspace: string, preview: string, data: Reauthentication & { confirmation: string; acknowledge_backups_and_provider_copies: true }) => api.request<DataJob>(base(workspace) + `/purges/${encodeURIComponent(preview)}`, body(data)),
  run: (workspace: string, job: string, data: Reauthentication) => api.request<DataJob>(base(workspace) + `/jobs/${encodeURIComponent(job)}/run`, body(data)),
  retry: (workspace: string, job: string, data: Reauthentication) => api.request<DataJob>(base(workspace) + `/jobs/${encodeURIComponent(job)}/retry`, body(data)),
  download: (workspace: string, job: string, data: Reauthentication) => api.requestBlob(base(workspace) + `/exports/${encodeURIComponent(job)}/download`, { ...body(data), headers: { 'Content-Type': 'application/json' }, cache: 'no-store' }),
  closure: () => api.request<ClosurePreview>('/account/data-closure'),
  eraseIdentity: (data: Reauthentication & { confirmation: 'ERASE MY ACCOUNT'; preview_fingerprint: string; acknowledge_retained_shared_authorship_and_backups: true }) => api.request<{ identity_erased: true }>('/account/data-closure', body(data)),
};
