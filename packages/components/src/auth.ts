import { SDKError, type TokenFactory } from './types.js';
import type { EmbedGrantContext } from './embed.js';

const protocol = 'freecollab-embed-signin-v1';
const identifier = /^[A-Za-z0-9_-]{1,128}$/;
function originOf(raw: string): string {
  const url = new URL(raw);
  if (url.username || url.password || url.search || url.hash || url.pathname !== '/'
    || (url.protocol !== 'https:' && !(url.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)))) {
    throw new SDKError('invalid-origin', 'Use an exact HTTPS application origin, or HTTP on loopback for development.');
  }
  return url.origin;
}
function randomId() { return Array.from(crypto.getRandomValues(new Uint8Array(24)), item => item.toString(16).padStart(2, '0')).join(''); }
function encode(value: unknown) { return btoa(String.fromCharCode(...new TextEncoder().encode(JSON.stringify(value)))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, ''); }

export interface EmbedSignInConfig {
  appUrl: string;
  applicationId: string;
  locale?: 'en' | 'sv';
  /** Includes the time spent completing customer SSO. Maximum ten minutes. */
  timeoutMs?: number;
}
export interface EmbedSignIn {
  /** Call synchronously from the user's click, before mounting or reauthenticating. */
  open(): void;
  grantFactory(context: EmbedGrantContext): Promise<{ code: string }>;
  destroy(): void;
}

/** Customer-owned OIDC in a trusted-origin popup. Only a short-lived PKCE grant reaches the host. */
export function createEmbedSignIn(config: EmbedSignInConfig): EmbedSignIn {
  const appOrigin = originOf(config.appUrl);
  if (!identifier.test(config.applicationId)) throw new SDKError('invalid-scope', 'Provide a registered embedding application identifier.');
  const timeout = config.timeoutMs ?? 300_000;
  if (!Number.isFinite(timeout) || timeout < 1_000 || timeout > 600_000) throw new SDKError('invalid-timeout', 'Sign-in timeout must be between one second and ten minutes.');
  let popup: Window | null = null, destroyed = false, cancel: (() => void) | null = null;
  return {
    open() {
      if (destroyed) throw new SDKError('destroyed', 'This sign-in helper has been destroyed.');
      cancel?.(); popup?.close();
      popup = window.open('about:blank', '_blank', 'popup,width=520,height=760');
      if (!popup) throw new SDKError('popup-blocked', 'Allow the sign-in window and start again from the sign-in button.');
    },
    grantFactory(context) {
      if (destroyed) return Promise.reject(new SDKError('destroyed', 'This sign-in helper has been destroyed.'));
      if (cancel) return Promise.reject(new SDKError('sign-in-busy', 'A sign-in attempt is already in progress.'));
      if (!popup || popup.closed) return Promise.reject(new SDKError('sign-in-required', 'Open sign-in from a user click before connecting the embedded workspace.'));
      if (context.applicationId !== config.applicationId || originOf(context.parentOrigin) !== window.location.origin
        || !identifier.test(context.workspaceId) || !identifier.test(context.channelId) || !/^[A-Za-z0-9_-]{43}$/.test(context.codeChallenge)) {
        popup.close(); popup = null;
        return Promise.reject(new SDKError('scope-mismatch', 'The sign-in request does not match this application and origin.'));
      }
      if (context.signal.aborted) { popup.close(); popup = null; return Promise.reject(new DOMException('Sign-in cancelled.', 'AbortError')); }
      const requestId = randomId(), owner = popup;
      return new Promise((resolve, reject) => {
        let finished = false;
        const finish = (error?: Error, code?: string) => {
          if (finished) return; finished = true;
          clearTimeout(timer); clearInterval(closed); window.removeEventListener('message', message);
          context.signal.removeEventListener('abort', aborted); cancel = null;
          owner.close(); if (popup === owner) popup = null;
          if (error) reject(error); else resolve({ code: code! });
        };
        const aborted = () => finish(new DOMException('Sign-in cancelled.', 'AbortError'));
        const message = (event: MessageEvent) => {
          const value = event.data;
          if (event.origin !== appOrigin || event.source !== owner || !value || value.protocol !== protocol || value.requestId !== requestId) return;
          if (value.type === 'grant' && typeof value.code === 'string' && /^[A-Za-z0-9_-]{32,256}$/.test(value.code)) finish(undefined, value.code);
          else if (value.type === 'error') finish(new SDKError('sign-in-failed', 'Organization sign-in could not be completed. Start again.'));
        };
        const timer = setTimeout(() => finish(new SDKError('sign-in-expired', 'Organization sign-in timed out. Start again.')), timeout);
        const closed = setInterval(() => { if (owner.closed) finish(new SDKError('sign-in-cancelled', 'The sign-in window was closed.')); }, 250);
        cancel = aborted; context.signal.addEventListener('abort', aborted, { once: true }); window.addEventListener('message', message);
        const fragment = encode({ protocol, requestId, applicationId: context.applicationId, workspaceId: context.workspaceId,
          channelId: context.channelId, parentOrigin: context.parentOrigin, codeChallenge: context.codeChallenge,
          locale: config.locale || 'en', expiresAt: Date.now() + timeout });
        try { owner.location.href = appOrigin + '/#embed-login=' + fragment; }
        catch { finish(new SDKError('popup-blocked', 'The sign-in window could not be opened. Start again.')); }
      });
    },
    destroy() { if (destroyed) return; destroyed = true; cancel?.(); popup?.close(); popup = null; },
  };
}

export interface SessionTokenFactoryConfig {
  /** Your own same-origin backend endpoint. It must authorize the current cookie session and requested scope. */
  endpoint: string;
  fetch?: typeof globalThis.fetch;
}
/** Adapter for an existing host session backend. Does not accept arbitrary external IdP JWTs. */
export function createSessionTokenFactory(config: SessionTokenFactoryConfig): TokenFactory {
  const endpoint = new URL(config.endpoint, window.location.href);
  if (endpoint.origin !== window.location.origin || endpoint.username || endpoint.password || endpoint.hash || endpoint.search) {
    throw new SDKError('invalid-session-endpoint', 'The session endpoint must be on your own application origin without query parameters.');
  }
  originOf(endpoint.origin);
  const fetcher = config.fetch || globalThis.fetch.bind(globalThis);
  return async ({ scope, signal }) => {
    const response = await fetcher(endpoint.href, { method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal,
      headers: { 'Content-Type': 'application/json', 'X-FreeCollab-Session': '1' }, body: JSON.stringify({ workspace_id: scope.workspaceId, channel_id: scope.channelId }) });
    if (signal.aborted) throw new DOMException('Session changed.', 'AbortError');
    if (response.status === 401) { void response.body?.cancel().catch(() => undefined); return null; }
    if (!response.ok) { void response.body?.cancel().catch(() => undefined); throw new SDKError('session-unavailable', 'Your application could not authorize this collaboration session.', response.status); }
    const value: unknown = await response.json();
    if (signal.aborted) throw new DOMException('Session changed.', 'AbortError');
    if (!value || typeof value !== 'object' || !('access_token' in value) || (value.access_token !== null && (typeof value.access_token !== 'string' || !value.access_token || value.access_token.length > 16_384))) {
      throw new SDKError('invalid-session', 'The session endpoint must return a FreeCollab user access_token or null.');
    }
    return value.access_token as string | null;
  };
}
