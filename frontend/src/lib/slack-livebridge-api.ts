import { api } from './api';
export interface SlackBridge {
  id: string; channel_id: string; slack_team_id: string; slack_channel_id: string; slack_bot_user_id: string;
  generation: number; inbound: boolean; outbound: boolean; enabled: boolean; last_status: string | null; revoked_at: string | null;
}
export interface SlackBridgeJob { id: string; generation: number; direction: string; status: string; attempts: number; message_id: string | null; failure: string | null; created_at: string }
export interface SlackBridgeInput {
  channel_id: string; slack_team_id: string; slack_channel_id: string; slack_bot_user_id: string;
  bot_token?: string; signing_secret?: string; expected_generation: number; inbound: boolean; outbound: boolean;
  enabled: boolean; disclose_future_messages: boolean; password: string; code?: string;
}
const segment = encodeURIComponent;
const base = (workspace: string) => `/workspaces/${segment(workspace)}/slack-bridges`;
const body = (value: unknown, method = 'POST') => ({ method, body: JSON.stringify(value) });
export const slackLivebridgeApi = {
  list: (workspace: string, channel: string) => api.request<{ bridges: SlackBridge[]; can_manage: boolean; public_example: boolean }>(`/workspaces/${segment(workspace)}/channels/${segment(channel)}/slack-bridges`),
  save: (workspace: string, values: SlackBridgeInput, id?: string) => api.request<{ bridge: SlackBridge; events_path: string }>(base(workspace) + (id ? '/' + segment(id) : ''), body(values, id ? 'PUT' : 'POST')),
  test: (workspace: string, id: string) => api.request<{ verified: boolean }>(base(workspace) + '/' + segment(id) + '/test', body({})),
  pause: (workspace: string, id: string) => api.request<SlackBridge>(base(workspace) + '/' + segment(id) + '/pause', body({})),
  revoke: (workspace: string, id: string) => api.request(base(workspace) + '/' + segment(id), { method: 'DELETE' }),
  jobs: (workspace: string, id: string) => api.request<{ jobs: SlackBridgeJob[] }>(base(workspace) + '/' + segment(id) + '/jobs'),
  retry: (workspace: string, id: string, job: string) => api.request<SlackBridgeJob>(base(workspace) + '/' + segment(id) + '/jobs/' + segment(job) + '/retry', body({ confirm_slack_has_not_processed: true })),
};
