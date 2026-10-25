import { api, APIError, type Token } from './api';
import type { MFAChallenge } from './account-api';

export interface OIDCProvider { id: string; name: string; issuer: string; workspace_id?: string | null; login_id?: string; onboarding_mode?: 'linked' | 'provisioned' | 'invited' | 'jit' }
export interface OIDCIdentity { id: string; issuer: string; provider: string; linked_at: string; workspace_id?: string | null }
export type SSOExchange = (Token | MFAChallenge | { linked: true; user_id: string; provider: string }) & { workspace_id?: string | null };

// An expired provider handoff must not sign out an otherwise valid native account.
async function oidcRequest<T>(path: string, body?: unknown): Promise<T> {
  const token = api.getAccessToken();
  const response = await fetch(api.getBaseUrl() + path, {
    method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: 'Bearer ' + token } : {}) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!response.ok) {
    const result = await response.json().catch(() => null);
    throw new APIError(typeof result?.detail === 'string' ? result.detail : 'Single sign-on could not be completed. Please try again.', response.status);
  }
  if (token !== api.getAccessToken()) throw new Error('The signed-in account changed. Please start again.');
  return response.json();
}

export const oidcApi = {
  providers: (context?: { loginId?: string; workspaceId?: string }) => oidcRequest<{ providers: OIDCProvider[] }>('/auth/oidc/providers' + (context?.loginId ? '?login_id=' + encodeURIComponent(context.loginId) : context?.workspaceId ? '?workspace_id=' + encodeURIComponent(context.workspaceId) : '')),
  start: (loginId?: string, invitationToken?: string) => oidcRequest<{ authorization_url: string; expires_in: number }>('/auth/oidc/start', { login_id: loginId || undefined, invitation_token: invitationToken || undefined }),
  exchange: (code: string) => oidcRequest<SSOExchange>('/auth/oidc/exchange', { code }),
  identities: () => api.request<OIDCIdentity[]>('/auth/oidc/identities'),
  link: (password: string, code?: string, workspaceId?: string) => oidcRequest<{ authorization_url: string; expires_in: number }>('/auth/oidc/link/start', { password, code: code || undefined, workspace_id: workspaceId }),
  unlink: (id: string, password: string, code?: string) => api.request<{ unlinked: true; requires_login: true }>('/auth/oidc/identities/' + encodeURIComponent(id), { method: 'DELETE', body: JSON.stringify({ password, code: code || undefined }) }),
};

export interface WorkspaceSSOProvider {
  workspace_id: string; login_id: string; name: string; issuer_url: string; client_id: string;
  scopes: string; has_secret: boolean; enabled: boolean; revision: number; tested: boolean;
  tested_at: string | null; login_url: string; callback_url: string;
}
export interface WorkspaceSSO { configured: boolean; callback_url: string; login_url: string | null; provider: WorkspaceSSOProvider | null }
export interface SSOReauthentication { password: string; code?: string }
export interface WorkspaceSSOInput extends SSOReauthentication { name: string; issuer_url: string; client_id: string; client_secret?: string | null; scopes: string }
const workspaceSSO = (workspace: string) => '/workspaces/' + encodeURIComponent(workspace) + '/sso';
export const workspaceSSOApi = {
  get: (workspace: string) => api.request<WorkspaceSSO>(workspaceSSO(workspace)),
  save: (workspace: string, body: WorkspaceSSOInput) => api.request<WorkspaceSSO>(workspaceSSO(workspace), { method: 'PUT', body: JSON.stringify(body) }),
  test: (workspace: string, body: SSOReauthentication) => api.request<{ valid: true }>(workspaceSSO(workspace) + '/test', { method: 'POST', body: JSON.stringify(body) }),
  enable: (workspace: string, body: SSOReauthentication) => api.request<WorkspaceSSO>(workspaceSSO(workspace) + '/enable', { method: 'POST', body: JSON.stringify(body) }),
  disable: (workspace: string, body: SSOReauthentication) => api.request<WorkspaceSSO>(workspaceSSO(workspace) + '/disable', { method: 'POST', body: JSON.stringify(body) }),
};

export function followOIDCAuthorization(raw: string) {
  const url = new URL(raw);
  if (url.protocol !== 'https:' || url.username || url.password) throw new Error('The identity provider returned an unsupported address.');
  window.location.assign(url.href);
}

export interface Webhook {
  id: string; workspace_id: string; name: string; origin: string;
  channel_ids: string[]; event_types: string[]; enabled: boolean; generation: number;
  created_at: string; updated_at: string;
}
export interface WebhookDelivery {
  id: string; event_id: string; event_type: string; channel_id: string | null;
  status: string; attempts: number; http_status: number | null; failure: string | null;
  created_at: string; next_attempt_at: string; delivered_at: string | null;
}
export interface WebhookInput { name: string; url: string; channel_ids: string[]; event_types: string[] }
const hooks = (workspace: string) => '/workspaces/' + encodeURIComponent(workspace) + '/webhooks';
const hook = (workspace: string, id: string) => hooks(workspace) + '/' + encodeURIComponent(id);
const post = (body: unknown = {}) => ({ method: 'POST', body: JSON.stringify(body) });
export const webhookApi = {
  list: (workspace: string) => api.request<{ enabled: boolean; event_types: string[]; webhooks: Webhook[] }>(hooks(workspace)),
  create: (workspace: string, data: WebhookInput) => api.request<{ webhook: Webhook; secret: string }>(hooks(workspace), post(data)),
  update: (workspace: string, id: string, data: Partial<WebhookInput> & { enabled?: boolean }) => api.request<Webhook>(hook(workspace, id), { method: 'PATCH', body: JSON.stringify(data) }),
  remove: (workspace: string, id: string) => api.request<{ deleted: true }>(hook(workspace, id), { method: 'DELETE' }),
  rotate: (workspace: string, id: string) => api.request<{ webhook: Webhook; secret: string }>(hook(workspace, id) + '/rotate', post()),
  test: (workspace: string, id: string) => api.request<{ queued: true; delivery_id: string }>(hook(workspace, id) + '/test', post()),
  deliveries: (workspace: string, id: string) => api.request<{ deliveries: WebhookDelivery[] }>(hook(workspace, id) + '/deliveries'),
  retry: (workspace: string, id: string, delivery: string) => api.request<{ queued: true; delivery_id: string }>(hook(workspace, id) + '/deliveries/' + encodeURIComponent(delivery) + '/retry', post()),
};

export interface OperationsStatus {
  mail: { mode: string; configured: boolean; tls: string | boolean; authentication_configured: boolean; sender_configured: boolean; public_url_https: boolean; issues: string[] };
  queue: { pending: number; processing: number; failed: number; delivered: number; oldest_pending_seconds: number | null };
  worker: { last_seen_at: string | null; status: string };
  database: { reachable: boolean };
  integration_worker?: { last_seen_at: string | null; status: string; enabled: boolean };
  ai_runtime_worker?: { last_seen_at: string | null; status: string; enabled: boolean };
  integration_queues?: Record<string, Record<string, number | null | undefined>>;
}
export interface MailDelivery { id: string; created_at: string; status: string; attempts: number; next_attempt_at: string; delivered_at: string | null; failure: string | null; retryable: boolean }
export type OperationalCheckStatus = 'pass' | 'attention' | 'disabled' | 'unknown';
export interface OperationalSecurity {
  checked_at: string;
  environment: string;
  checks: { id: string; status: OperationalCheckStatus }[];
  operators: { active: number; mfa_enabled: number; without_mfa: number; email_verified: number };
  storage: { status: OperationalCheckStatus; free_bytes: number | null; total_bytes: number | null; free_percent: number | null };
  blob_storage?: { backend: 'local' | 's3'; status: 'local' | 'configured_not_probed'; automatic_remote_probe: false };
  queues: Record<string, { enabled: boolean; due: number; expired_leases: number; failed: number }>;
}
export type SecurityEventOutcome = 'success' | 'denied' | 'error';
export interface SecurityEvent {
  id: string; created_at: string; actor_kind: 'account' | 'directory'; actor_id: string | null;
  workspace_id: string | null; target_id?: string | null; action: string; outcome: SecurityEventOutcome; status_code: number;
}
export interface SecurityEvents { items: SecurityEvent[]; next_cursor: string | null; retention_days: number; max_records?: number }
export const operationsApi = {
  status: () => api.request<OperationsStatus>('/operations/status'),
  deliveries: () => api.request<{ items: MailDelivery[] }>('/operations/mail/deliveries?limit=50'),
  security: () => api.request<OperationalSecurity>('/operations/security'),
  securityEvents: (options: { cursor?: string; outcome?: SecurityEventOutcome } = {}) => {
    const query = new URLSearchParams({ limit: '50' });
    if (options.cursor) query.set('cursor', options.cursor);
    if (options.outcome) query.set('outcome', options.outcome);
    return api.request<SecurityEvents>('/operations/security-events?' + query.toString());
  },
  test: (password: string, code?: string) => api.request<{ id: string; status: 'queued' }>('/operations/mail/test', post({ password, code: code || undefined })),
  retry: (id: string, password: string, code?: string) => api.request<MailDelivery>('/operations/mail/deliveries/' + encodeURIComponent(id) + '/retry', post({ password, code: code || undefined })),
};
