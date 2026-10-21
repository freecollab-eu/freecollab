import { api, type Token } from './api';

export interface AccountSecurity {
  email: string;
  email_verified: boolean;
  mfa_enabled: boolean;
  mail_delivery_available: boolean;
  session_count: number;
  content_recovery_separate: true;
}

export interface AccountSession {
  id: string;
  created_at: string;
  expires_at: string;
  user_agent: string | null;
  client_address: string | null;
  current: boolean;
}

export interface MFAChallenge {
  mfa_required: true;
  challenge_token: string;
  expires_in: number;
}

export type LoginResult = Token | MFAChallenge;
export const needsSecondFactor = (result: LoginResult): result is MFAChallenge => 'mfa_required' in result && result.mfa_required === true;

export const accountApi = {
  register: (name: string, email: string, password: string) => api.request<Token>('/auth/register', { method: 'POST', body: JSON.stringify({ name, email, password }) }),
  login: (email: string, password: string) => api.request<LoginResult>('/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) }),
  verifyMFA: (challenge_token: string, code: string) => api.request<Token>('/auth/mfa/verify', { method: 'POST', body: JSON.stringify({ challenge_token, code }) }),
  forgotPassword: (email: string) => api.request<{ message: string }>('/auth/forgot-password', { method: 'POST', body: JSON.stringify({ email }) }),
  resetPassword: (token: string, new_password: string) => api.request<{ password_reset: true; requires_login: true }>('/auth/reset-password', { method: 'POST', body: JSON.stringify({ token, new_password }) }),
  verifyEmail: (token: string) => api.request<{ verified: true }>('/account/verification/confirm', { method: 'POST', body: JSON.stringify({ token }) }),
  requestVerification: () => api.request<{ message: string }>('/account/verification/request', { method: 'POST', body: '{}' }),
  security: () => api.request<AccountSecurity>('/account/security'),
  sessions: () => api.request<AccountSession[]>('/account/sessions'),
  revokeSession: (id: string, password: string, code?: string) => api.request(`/account/sessions/${encodeURIComponent(id)}`, { method: 'DELETE', body: JSON.stringify({ password, code: code || undefined }) }),
  revokeAll: (password: string, code?: string) => api.request('/account/sessions/revoke-all', { method: 'POST', body: JSON.stringify({ password, code: code || undefined }) }),
  changePassword: (current_password: string, new_password: string, code?: string) => api.request('/account/password', { method: 'POST', body: JSON.stringify({ current_password, new_password, code: code || undefined }) }),
  setupMFA: (password: string) => api.request<{ secret: string; otpauth_uri: string; expires_at: string }>('/account/mfa/setup', { method: 'POST', body: JSON.stringify({ password }) }),
  confirmMFA: (code: string) => api.request<{ enabled: true; recovery_codes: string[]; session: Token }>('/account/mfa/confirm', { method: 'POST', body: JSON.stringify({ code }) }),
  disableMFA: (password: string, code: string) => api.request<{ enabled: false; session: Token }>('/account/mfa/disable', { method: 'POST', body: JSON.stringify({ password, code }) }),
  replaceRecoveryCodes: (password: string, code: string) => api.request<{ recovery_codes: string[] }>('/account/mfa/recovery-codes', { method: 'POST', body: JSON.stringify({ password, code }) }),
};
