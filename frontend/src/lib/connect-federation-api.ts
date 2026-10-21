import { api } from './api';
export interface FederationConfiguration {
  enabled: boolean; protocol: string; origin: string; public_key: string;
  matrix_server_name: string; trusted_origins: string[]; requirement?: string;
}
export interface FederatedConnection {
  id: string; channel_id: string; workspace_id: string; name: string;
  peer_origin: string; peer_workspace_id: string; role: 'host' | 'guest';
  status: 'pending' | 'active' | 'revoked'; created_at: string; expires_at: string;
  revoked_at: string | null; secure: true; transport: 'federated-matrix'; room_ready: boolean;
}
export interface FederatedMember { user_id: string; name: string; active: boolean }
const root = (workspace: string) => '/connect/federation/workspaces/' + encodeURIComponent(workspace);
const path = (workspace: string, id: string) => root(workspace) + '/connections/' + encodeURIComponent(id);
export const federationApi = {
  configuration: (workspace: string) => api.request<FederationConfiguration>(root(workspace) + '/configuration'),
  list: (workspace: string) => api.request<FederatedConnection[]>(root(workspace) + '/connections'),
  offer: (workspace: string, name: string, peer_origin: string, peer_workspace_id: string) => api.request<FederatedConnection & { invitation_token: string }>(root(workspace) + '/connections', { method: 'POST', body: JSON.stringify({ name, peer_origin, peer_workspace_id }) }),
  accept: (workspace: string, peer_origin: string, invitation_token: string) => api.request<FederatedConnection>(root(workspace) + '/accept', { method: 'POST', body: JSON.stringify({ peer_origin, invitation_token }) }),
  members: (workspace: string, id: string) => api.request<FederatedMember[]>(path(workspace, id) + '/members'),
  grant: (workspace: string, id: string, user_id: string) => api.request(path(workspace, id) + '/members', { method: 'POST', body: JSON.stringify({ user_id }) }),
  remove: (workspace: string, id: string, member: string) => api.request(path(workspace, id) + '/members/' + encodeURIComponent(member), { method: 'DELETE' }),
  sync: (workspace: string, id: string) => api.request<FederatedConnection>(path(workspace, id) + '/sync', { method: 'POST' }),
  revoke: (workspace: string, id: string) => api.request<FederatedConnection & { remote_cleanup_pending: boolean }>(path(workspace, id), { method: 'DELETE' }),
};
