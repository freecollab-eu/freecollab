import { api } from './api';
import type { CopilotCitation, CopilotSource } from './copilot-api';

export interface AppArgument { name: string; label: string; required: boolean; max_length: number }
export interface AppCommand { name: string; description: string; protocol: string; arguments: AppArgument[] }
export interface AppManifest { schema_version: number; app_id: string; name: string; version: string; description: string; scopes: string[]; commands: AppCommand[] }
export interface AppRecipe { id: string; manifest: AppManifest; endpoint_example: string; setup: string; credential_required: boolean }
export interface InstalledApp { id: string; app_id: string; bot_id: string; generation: number; manifest: AppManifest; channel_ids: string[]; origin: string; enabled: boolean; revoked_at: string | null }
export interface AppJob { id: string; command: string; status: string; attempts: number; http_status: number | null; failure: string | null; created_at: string }
export interface AppCard { id: string; bot_id: string; bot_name: string; created_at: string; citations?: CopilotCitation[]; card: { title: string; text: string; url?: string | null; fields: { label: string; value: string }[] } }
export interface AppAIDraft { id: string; kind: 'summary' | 'workflow' | 'actions'; status: string; text?: string; citations?: CopilotCitation[]; fields?: unknown; actions?: { installation_id: string; command: string; arguments: Record<string, string>; generation: number }[] }
export interface RecapSchedule { id: string; enabled: boolean; interval_hours: number; next_run_at: string; last_status: string | null }
const workspace = (id: string) => '/workspaces/' + encodeURIComponent(id);
const base = (id: string) => workspace(id) + '/apps';
const channel = (id: string, room: string) => workspace(id) + '/channels/' + encodeURIComponent(room) + '/apps';
const json = (data: unknown, method = 'POST') => ({ method, body: JSON.stringify(data) });
export const appPlatformApi = {
  catalog: (id: string) => api.request<{ recipes: AppRecipe[] }>(base(id) + '/catalog'),
  list: (id: string) => api.request<{ installations: InstalledApp[]; worker_ready: boolean }>(base(id)),
  install: (id: string, data: { manifest: AppManifest; channel_ids: string[]; endpoint: string; authorization: string; expected_generation: number }, installation?: string) => api.request<{ installation: InstalledApp; signing_secret: string; callback_path: string }>(base(id) + (installation ? '/' + encodeURIComponent(installation) : ''), json(data, installation ? 'PUT' : 'POST')),
  revoke: (id: string, installation: string) => api.request(base(id) + '/' + encodeURIComponent(installation), { method: 'DELETE' }),
  jobs: (id: string, installation: string) => api.request<{ jobs: AppJob[] }>(base(id) + '/' + encodeURIComponent(installation) + '/jobs'),
  retry: (id: string, installation: string, job: string) => api.request(base(id) + '/' + encodeURIComponent(installation) + '/jobs/' + encodeURIComponent(job) + '/retry', json({ confirm_receiver_has_not_processed: true })),
  invoke: (id: string, installation: string, data: { request_id: string; channel_id: string; command: string; arguments: Record<string, string>; confirm: boolean; disclose_to_app: boolean }) => api.request<AppJob>(base(id) + '/' + encodeURIComponent(installation) + '/commands', json(data)),
  cards: (id: string, room: string) => api.request<{ cards: AppCard[] }>(channel(id, room) + '/cards'),
  drafts: (id: string, room: string) => api.request<{ drafts: AppAIDraft[] }>(channel(id, room) + '/ai/drafts'),
  generate: (id: string, room: string, data: { expected_config_revision: number; kind: AppAIDraft['kind']; prompt: string; sources: CopilotSource[]; installation_ids: string[]; recipient_ids: string[]; disclose_to_provider: boolean }) => api.request<AppAIDraft>(channel(id, room) + '/ai/drafts', json(data)),
  approve: (id: string, room: string, draft: string, password?: string, code?: string) => api.request(channel(id, room) + '/ai/drafts/' + encodeURIComponent(draft) + '/approve', json({ confirm: true, ...(password ? { password } : {}), ...(code ? { code } : {}) })),
  deleteDraft: (id: string, room: string, draft: string) => api.request(channel(id, room) + '/ai/drafts/' + encodeURIComponent(draft), { method: 'DELETE' }),
  recaps: (id: string, room: string) => api.request<{ schedules: RecapSchedule[] }>(channel(id, room) + '/ai/recaps'),
  schedule: (id: string, room: string, hours: number, reviewedConfigRevision: number) => api.request(channel(id, room) + '/ai/recaps', json({ interval_hours: hours, expected_config_revision: reviewedConfigRevision, enabled: true, disclose_future_native_messages: true })),
  stopRecap: (id: string, room: string, schedule: string) => api.request(channel(id, room) + '/ai/recaps/' + encodeURIComponent(schedule), { method: 'DELETE' }),
};
