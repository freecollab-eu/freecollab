import { api, type Channel } from './api';

export type WorkspaceRole = 'owner' | 'admin' | 'member' | 'guest';
export interface Workspace {
  id: string;
  name: string;
  logo_url: string | null;
  role: WorkspaceRole;
  is_legacy: boolean;
  member_count: number;
}
export interface WorkspaceMember {
  user_id: string;
  name: string;
  role: WorkspaceRole;
}
export interface WorkspaceInvite {
  id: string;
  workspace_id: string;
  role: Exclude<WorkspaceRole, 'owner'>;
  token?: string;
  expires_at: string;
  revoked_at?: string | null;
  accepted_at?: string | null;
}
export interface DirectMessage extends Channel {
  other_user: { id: string; name: string };
  archived: boolean;
  blocked: boolean;
}

export const workspaceApi = {
  list: () => api.request<Workspace[]>('/workspaces'),
  create: (name: string) => api.request<Workspace>('/workspaces', { method: 'POST', body: JSON.stringify({ name }) }),
  update: (id: string, name: string) => api.request<Workspace>(`/workspaces/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify({ name }) }),
  uploadLogo: (id: string, file: File) => { const data = new FormData(); data.append('file', file); return api.uploadMultipart<Workspace>(`/workspaces/${encodeURIComponent(id)}/logo`, data); },
  removeLogo: (id: string) => api.request<Workspace>(`/workspaces/${encodeURIComponent(id)}/logo`, { method: 'DELETE' }),
  logo: (id: string) => api.requestBlob(`/workspaces/${encodeURIComponent(id)}/logo`),
  members: (id: string, query = '') => api.request<WorkspaceMember[]>(`/workspaces/${encodeURIComponent(id)}/members${query ? `?query=${encodeURIComponent(query)}` : ''}`),
  setRole: (id: string, userId: string, role: WorkspaceRole) => api.request(`/workspaces/${encodeURIComponent(id)}/members/${encodeURIComponent(userId)}`, { method: 'PATCH', body: JSON.stringify({ role }) }),
  removeMember: (id: string, userId: string) => api.request(`/workspaces/${encodeURIComponent(id)}/members/${encodeURIComponent(userId)}`, { method: 'DELETE' }),
  leave: (id: string) => api.request(`/workspaces/${encodeURIComponent(id)}/leave`, { method: 'POST' }),
  invites: (id: string) => api.request<WorkspaceInvite[]>(`/workspaces/${encodeURIComponent(id)}/invites`),
  invite: (id: string, role: Exclude<WorkspaceRole, 'owner'>) => api.request<WorkspaceInvite>(`/workspaces/${encodeURIComponent(id)}/invites`, { method: 'POST', body: JSON.stringify({ role, expires_in_hours: 24 }) }),
  revokeInvite: (id: string, inviteId: string) => api.request(`/workspaces/${encodeURIComponent(id)}/invites/${encodeURIComponent(inviteId)}`, { method: 'DELETE' }),
  acceptInvite: (token: string) => api.request<Workspace>('/workspaces/invites/accept', { method: 'POST', body: JSON.stringify({ token }) }),
  directMessages: (workspaceId: string) => api.request<DirectMessage[]>(`/direct-messages?workspace_id=${encodeURIComponent(workspaceId)}`),
  openDirectMessage: (workspace_id: string, user_id: string) => api.request<Channel & { created?: boolean }>('/direct-messages', { method: 'POST', body: JSON.stringify({ workspace_id, user_id }) }),
  archiveDirectMessage: (channelId: string, archived: boolean) => api.request(`/direct-messages/${encodeURIComponent(channelId)}`, { method: 'PATCH', body: JSON.stringify({ archived }) }),
  blockPerson: (userId: string) => api.request(`/direct-messages/blocks/${encodeURIComponent(userId)}`, { method: 'POST' }),
  unblockPerson: (userId: string) => api.request(`/direct-messages/blocks/${encodeURIComponent(userId)}`, { method: 'DELETE' }),
};
