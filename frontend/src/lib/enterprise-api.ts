import { api } from './api';
import type { SSOReauthentication } from './integrations-api';

export type IdentityMode = 'linked' | 'provisioned' | 'invited' | 'jit';
export type IdentityClaim = 'sub' | 'oid' | 'email' | 'preferred_username';
export interface IdentityPolicy { mode: IdentityMode; identity_claim: IdentityClaim; revision: number; managed_accounts: boolean; invitation_required: boolean }
export interface WorkforcePerson { user_id: string; external_id: string; name: string; email: string; active: boolean; bound: boolean }
export interface WorkforceInvitation { id: string; role: 'member' | 'guest'; expires_at: string; used_at?: string | null; revoked_at?: string | null }
export interface NewWorkforceInvitation { token: string; invitation_id: string; login_url: string; expires_at: string }
const workspace = (id: string) => '/workspaces/' + encodeURIComponent(id);
const post = (body: unknown) => ({ method: 'POST', body: JSON.stringify(body) });
const put = (body: unknown) => ({ method: 'PUT', body: JSON.stringify(body) });
export const workforceApi = {
  policy: (id: string) => api.request<IdentityPolicy>(workspace(id) + '/identity-policy'),
  updatePolicy: (id: string, body: SSOReauthentication & Pick<IdentityPolicy, 'mode' | 'identity_claim'> & { expected_revision?: number }) => api.request<IdentityPolicy>(workspace(id) + '/identity-policy', put(body)),
  people: (id: string) => api.request<WorkforcePerson[]>(workspace(id) + '/workforce'),
  invite: (id: string, body: SSOReauthentication & { role: 'member' | 'guest'; expires_in_hours: number }) => api.request<NewWorkforceInvitation>(workspace(id) + '/workforce/invites', post(body)),
  invitations: (id: string) => api.request<{ invitations: WorkforceInvitation[] }>(workspace(id) + '/workforce/invites'),
  revokeInvitation: (id: string, invite: string, body: SSOReauthentication) => api.request(workspace(id) + '/workforce/invites/' + encodeURIComponent(invite) + '/revoke', post(body)),
};

export interface SCIMStatus {
  directory_id: string; endpoint_url: string; enabled: boolean; configured: boolean; default_role: 'member' | 'guest';
  credential: { version: number; rotated_at: string; last_used_at: string | null; requires_rotation?: boolean } | null;
  counts: { users: number; active_users: number; groups: number };
}
export interface SCIMMapping { group_id: string; group_name?: string; workspace_role: 'guest' | 'member' | 'admin' | null; channels: { channel_id: string; role: 'member' | 'admin'; requires_reapproval?: boolean }[] }
export interface SCIMMappings { default_role: 'member' | 'guest'; mappings: SCIMMapping[]; groups: { id: string; display_name: string; member_count: number }[]; channels: { id: string; name: string | null; secure: boolean; requires_reapproval?: boolean }[] }
export interface SCIMAuditEvent { id: string; action: string; resource_type: string; resource_id: string; created_at: string }
const scim = (id: string) => workspace(id) + '/scim';
export const scimApi = {
  status: (id: string) => api.request<SCIMStatus>(scim(id)),
  token: (id: string, body: SSOReauthentication) => api.request<SCIMStatus & { token: string }>(scim(id) + '/token', post(body)),
  disable: (id: string, body: SSOReauthentication) => api.request<SCIMStatus>(scim(id) + '/disable', post(body)),
  mappings: (id: string) => api.request<SCIMMappings>(scim(id) + '/mappings'),
  saveMappings: (id: string, body: SSOReauthentication & { default_role: 'member' | 'guest'; mappings: SCIMMapping[] }) => api.request<SCIMMappings>(scim(id) + '/mappings', put(body)),
  audit: (id: string) => api.request<{ events: SCIMAuditEvent[] }>(scim(id) + '/audit'),
};

export type SyncProvider = 'google' | 'microsoft' | 'nextcloud' | 'caldav';
export type SyncKind = 'files' | 'calendar';
export interface SyncConnection {
  id: string; name: string; provider: SyncProvider; kind: SyncKind; channel_id: string;
  config: Record<string, string>; status: string; enabled: boolean; revision: number; has_credentials: boolean;
  tested: boolean;
  last_synced_at: string | null; last_error: string | null; next_run_at: string | null; conflict_count: number;
}
export interface SyncActivity { id: string; event: string; status: string; created_at: string; item_id?: string | null }
export interface SyncConflict { id: string; item_id: string; local_id: string | null; remote_id: string; reason: string; created_at: string; read_only?: boolean }
export interface SyncInput extends SSOReauthentication {
  name: string; provider: SyncProvider; kind: SyncKind; channel_id: string; config: Record<string, string>;
  credentials?: { client_secret?: string | null; username?: string; app_password?: string | null }; consent: true;
}
const sync = (id: string) => workspace(id) + '/sync';
const connection = (id: string, value: string) => sync(id) + '/' + encodeURIComponent(value);
export const syncApi = {
  list: (id: string) => api.request<{ enabled: boolean; callback_url: string; connections: SyncConnection[] }>(sync(id)),
  create: (id: string, body: SyncInput) => api.request<SyncConnection>(sync(id), post(body)),
  update: (id: string, value: string, body: SyncInput) => api.request<SyncConnection>(connection(id, value), put(body)),
  action: (id: string, value: string, action: 'test' | 'enable' | 'disable' | 'run' | 'disconnect', body: SSOReauthentication) => api.request(connection(id, value) + '/' + action, post(body)),
  authorize: (id: string, value: string, body: SSOReauthentication) => api.request<{ authorization_url: string }>(connection(id, value) + '/oauth/start', post(body)),
  activity: (id: string, value: string) => api.request<{ events: SyncActivity[] }>(connection(id, value) + '/activity'),
  conflicts: (id: string, value: string) => api.request<{ conflicts: SyncConflict[] }>(connection(id, value) + '/conflicts'),
  resolve: (id: string, value: string, conflict: string, body: SSOReauthentication & { choice: 'local' | 'remote' }) => api.request(connection(id, value) + '/conflicts/' + encodeURIComponent(conflict) + '/resolve', post(body)),
  channelStatus: (id: string) => api.request<{ connections: Pick<SyncConnection, 'id' | 'name' | 'provider' | 'kind' | 'channel_id' | 'enabled'>[] }>('/channels/' + encodeURIComponent(id) + '/sync-status'),
};
