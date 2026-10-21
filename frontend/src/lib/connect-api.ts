import { api } from './api';

export interface SharedConnection {
  id: string;
  channel_id: string;
  name: string;
  source_workspace_id: string;
  target_workspace_id: string;
  source_workspace_name: string;
  target_workspace_name: string;
  status: 'pending' | 'active' | 'revoked';
  created_at: string;
  expires_at: string;
  accepted_at: string | null;
  revoked_at: string | null;
  transport: 'local';
  secure: boolean;
  secure_sync_pending: boolean;
}

export interface SharedMember {
  user_id: string;
  name: string;
  workspace_id: string;
  active: boolean;
}

export interface ConnectCapabilities {
  local_shared_channels: boolean;
  cross_installation: boolean;
  encryption_configured: boolean;
  transport: 'local';
  external_requirement: string;
}

const scope = (workspaceId: string) => `/connect/workspaces/${encodeURIComponent(workspaceId)}`;
const connection = (workspaceId: string, connectionId: string) => `${scope(workspaceId)}/connections/${encodeURIComponent(connectionId)}`;

export const connectApi = {
  capabilities: () => api.request<ConnectCapabilities>('/connect/capabilities'),
  list: (workspaceId: string) => api.request<SharedConnection[]>(`${scope(workspaceId)}/connections`),
  create: (workspaceId: string, name: string, target_workspace_id: string) => api.request<SharedConnection & { invitation_token: string }>(`${scope(workspaceId)}/connections`, {
    method: 'POST', body: JSON.stringify({ name, target_workspace_id, expires_in_hours: 24 }),
  }),
  accept: (workspaceId: string, token: string) => api.request<SharedConnection>(`${scope(workspaceId)}/accept`, { method: 'POST', body: JSON.stringify({ token }) }),
  members: (workspaceId: string, connectionId: string) => api.request<SharedMember[]>(`${connection(workspaceId, connectionId)}/members`),
  grant: (workspaceId: string, connectionId: string, user_id: string) => api.request<SharedMember>(`${connection(workspaceId, connectionId)}/members`, { method: 'POST', body: JSON.stringify({ user_id }) }),
  remove: (workspaceId: string, connectionId: string, userId: string) => api.request(`${connection(workspaceId, connectionId)}/members/${encodeURIComponent(userId)}`, { method: 'DELETE' }),
  revoke: (workspaceId: string, connectionId: string) => api.request<SharedConnection>(`${connection(workspaceId, connectionId)}`, { method: 'DELETE' }),
  retry: (workspaceId: string, connectionId: string) => api.request<SharedConnection>(`${connection(workspaceId, connectionId)}/retry-sync`, { method: 'POST' }),
};
