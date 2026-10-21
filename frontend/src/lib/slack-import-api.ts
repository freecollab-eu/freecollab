import { api } from './api';
export interface SlackImportResult { channels: { id: string; name: string; source_id: string }[]; messages: number; files: number; missing_files: number }
export interface SlackPreview { id: string; status: string; channels?: { id: string; name: string; member_ids: string[]; message_count: number }[]; users?: { id: string; name: string }[]; bundled_files?: number; missing_files?: number; result?: SlackImportResult }
const base = (id: string) => '/workspaces/' + encodeURIComponent(id) + '/slack-imports';
export const slackImportApi = {
  preview: (id: string, file: File) => { const body = new FormData(); body.append('file', file); return api.uploadMultipart<SlackPreview>(base(id) + '/preview', body); },
  confirm: (id: string, job: string, data: { channel_ids: string[]; user_mapping: Record<string, string>; channel_prefix: string; acknowledge_native_archive: boolean }) => api.request<SlackImportResult>(base(id) + '/' + encodeURIComponent(job) + '/confirm', { method: 'POST', body: JSON.stringify(data) }),
  discard: (id: string, job: string) => api.request(base(id) + '/' + encodeURIComponent(job), { method: 'DELETE' }),
};
