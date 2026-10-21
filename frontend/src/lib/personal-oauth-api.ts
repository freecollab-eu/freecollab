import { api } from './api';

export type OAuthProvider = 'google' | 'microsoft';
export type OAuthPurpose = 'search' | 'calendar-action';
export interface PersonalOAuthClient { id: string; provider: OAuthProvider; purpose: OAuthPurpose; client_id: string; tenant_id: string; revision: number; enabled: boolean; has_secret: boolean; scope: string }
export interface PersonalOAuthGrant { id: string; provider: OAuthProvider; purpose: OAuthPurpose; name: string; target_id: string; status: string; scope: string; connected: boolean; expires_at: string | null; created_at: string }
export interface PersonalOAuthOverview { callback_url: string; can_manage: boolean; purposes: OAuthPurpose[]; clients: PersonalOAuthClient[]; grants: PersonalOAuthGrant[] }
const root = (id: string) => '/workspaces/' + encodeURIComponent(id) + '/personal-oauth';
const json = (body: unknown, method = 'POST') => ({ method, body: JSON.stringify(body) });
export const personalOAuthApi = {
  overview: (workspaceId: string) => api.request<PersonalOAuthOverview>(root(workspaceId)),
  configure: (workspaceId: string, provider: OAuthProvider, purpose: OAuthPurpose, body: { client_id: string; tenant_id: string; client_secret: string; password: string; code?: string; expected_revision: number; enabled: boolean }) =>
    api.request<PersonalOAuthClient>(root(workspaceId) + '/clients/' + provider + '/' + purpose, json(body, 'PUT')),
  start: (workspaceId: string, provider: OAuthProvider, body: { purpose: OAuthPurpose; name: string; config: Record<string, string>; installation_id?: string; installation_generation?: number; consent: boolean }) =>
    api.request<{ authorization_url: string; grant_id: string; scope: string }>(root(workspaceId) + '/' + provider + '/start', json(body)),
  disable: (workspaceId: string, provider: OAuthProvider, purpose: OAuthPurpose, body: { password: string; code?: string }) =>
    api.request(root(workspaceId) + '/clients/' + provider + '/' + purpose + '/disable', json(body)),
  revoke: (workspaceId: string, grantId: string) => api.request<{ revoked: boolean; local_credentials_erased: boolean; provider_revocation: string }>(root(workspaceId) + '/grants/' + encodeURIComponent(grantId), { method: 'DELETE' }),
};

/** Never navigate a provider-consent button to an arbitrary remote origin. */
export function providerAuthorizationUrl(raw: string, provider: OAuthProvider): string {
  const url = new URL(raw);
  const valid = provider === 'google' ? url.hostname === 'accounts.google.com' && url.pathname === '/o/oauth2/v2/auth'
    : url.hostname === 'login.microsoftonline.com' && /^\/[0-9a-f-]{36}\/oauth2\/v2\.0\/authorize$/i.test(url.pathname);
  const secret = [...url.searchParams.keys()].some(key => ['client_secret', 'access_token', 'refresh_token', 'id_token', 'token', 'code'].includes(key.toLowerCase()));
  if (url.protocol !== 'https:' || url.port || url.username || url.password || url.hash || !valid || secret) throw new Error('The provider consent destination is invalid.');
  return url.href;
}

export function providerConsentNeedsBrowser(context: { desktop?: boolean; mobile?: boolean; embedded?: boolean }): boolean {
  return Boolean(context.desktop || context.mobile || context.embedded);
}
