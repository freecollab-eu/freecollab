import { api } from './api';
export interface SlackImportResult { channels: { id: string; name: string; source_id: string }[]; messages: number; files: number; missing_files: number }
export interface SlackMigrationProgress { id: string; status: string; sequence: number | null; result?: SlackImportResult; processed?: number; total?: number }
export interface SlackPreview { id: string; status: string; channels?: { id: string; name: string; member_ids: string[]; message_count: number }[]; users?: { id: string; name: string }[]; bundled_files?: number; missing_files?: number; result?: SlackImportResult; progress?: SlackMigrationProgress }
const base = (id: string) => '/workspaces/' + encodeURIComponent(id) + '/slack-imports';
export const slackImportApi = {
  preview: (id: string, file: File) => { const body = new FormData(); body.append('file', file); return api.uploadMultipart<SlackPreview>(base(id) + '/preview', body); },
  confirm: (id: string, job: string, data: { channel_ids: string[]; user_mapping: Record<string, string>; channel_prefix: string; acknowledge_native_archive: boolean }) => api.request<SlackImportResult>(base(id) + '/' + encodeURIComponent(job) + '/confirm', { method: 'POST', body: JSON.stringify(data) }),
  begin: (id: string, job: string, data: { channel_ids: string[]; user_mapping: Record<string, string>; channel_prefix: string; acknowledge_native_archive: boolean }) => api.request<SlackMigrationProgress>(base(id) + '/' + encodeURIComponent(job) + '/begin', { method: 'POST', body: JSON.stringify(data) }),
  step: (id: string, job: string, sequence: number) => api.request<SlackMigrationProgress>(base(id) + '/' + encodeURIComponent(job) + '/step', { method: 'POST', body: JSON.stringify({ expected_sequence: sequence }) }),
  progress: (id: string, job: string) => api.request<SlackMigrationProgress>(base(id) + '/' + encodeURIComponent(job) + '/progress'),
  discard: (id: string, job: string) => api.request(base(id) + '/' + encodeURIComponent(job), { method: 'DELETE' }),
};
