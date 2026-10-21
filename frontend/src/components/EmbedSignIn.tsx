import { useEffect, useRef, useState, type FormEvent } from 'react';
import { AlertCircle, ArrowUpRight, Loader2, ShieldCheck } from 'lucide-react';
import { t, useI18n, applyLocale } from '../i18n';
import { api } from '../lib/api';
import { Brand } from './Brand';
import '../styles/account.css';

const protocol = 'freecollab-embed-signin-v1';
const storageKey = 'freecollab.embed-signin.context.v1';
interface Context { protocol: string; requestId: string; applicationId: string; workspaceId: string; channelId: string; parentOrigin: string; codeChallenge: string; locale: 'en' | 'sv'; expiresAt: number }
interface Route { context?: Context; code?: string; failure?: boolean }
function validated(value: unknown): Context {
  if (!value || typeof value !== 'object') throw new Error();
  const v = value as Context, origin = new URL(v.parentOrigin);
  if (v.protocol !== protocol || !/^[A-Za-z0-9_-]{32,128}$/.test(v.requestId) || ![v.applicationId, v.workspaceId, v.channelId].every(item => typeof item === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(item))
    || !/^[A-Za-z0-9_-]{43}$/.test(v.codeChallenge) || origin.origin !== v.parentOrigin || origin.username || origin.password
    || (origin.protocol !== 'https:' && !(origin.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(origin.hostname)))
    || !Number.isFinite(v.expiresAt) || v.expiresAt <= Date.now() || v.expiresAt > Date.now() + 601_000) throw new Error();
  return { ...v, locale: v.locale === 'sv' ? 'sv' : 'en' };
}
function captureRoute(): Route | null {
  if (typeof window === 'undefined') return null;
  const fragment = window.location.hash;
  if (fragment.startsWith('#embed-login=')) {
    try {
      if (!window.opener || window.opener.closed || fragment.length > 8192) throw new Error();
      const encoded = fragment.slice('#embed-login='.length).replace(/-/g, '+').replace(/_/g, '/');
      const context = validated(JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(encoded), c => c.charCodeAt(0)))));
      // Only public identifiers, PKCE challenge and routing survive the IdP redirect. No token, handoff code or verifier is persisted.
      sessionStorage.setItem(storageKey, JSON.stringify(context));
      history.replaceState(null, '', location.pathname + location.search);
      return { context };
    } catch { sessionStorage.removeItem(storageKey); return { failure: true }; }
  }
  if (fragment.startsWith('#sso=') || fragment.startsWith('#sso_error=')) {
    const saved = sessionStorage.getItem(storageKey); if (!saved) return null;
    try {
      const context = validated(JSON.parse(saved));
      if (!window.opener || window.opener.closed) throw new Error();
      const code = fragment.startsWith('#sso=') ? decodeURIComponent(fragment.slice(5)) : undefined;
      if (code && !/^[A-Za-z0-9_-]{20,256}$/.test(code)) throw new Error();
      history.replaceState(null, '', location.pathname + location.search);
      return { context, code, failure: !code };
    } catch { sessionStorage.removeItem(storageKey); return { failure: true }; }
  }
  return null;
}
// Captured once so React StrictMode never consumes a fragment twice.
const route = captureRoute();
export function isEmbedSignInRoute() { return route !== null; }

interface Application { application_id: string; workspace_id: string; parent_origin: string; name: string; login_id: string }
interface Session { access_token?: string; workspace_id?: string; mfa_required?: boolean; challenge_token?: string }
async function request<T>(path: string, body?: unknown, token?: string): Promise<T> {
  const base = new URL(api.getBaseUrl().replace(/\/$/, '') + '/', window.location.origin);
  if (base.origin !== location.origin) throw new Error('Embedding requires the API on the trusted application origin.');
  const response = await fetch(base.href.replace(/\/$/, '') + path, { method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin',
    cache: 'no-store', redirect: 'error', headers: { ...(body === undefined ? {} : { 'Content-Type': 'application/json' }), ...(token ? { Authorization: 'Bearer ' + token } : {}) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  if (!response.ok) { void response.body?.cancel().catch(() => undefined); throw new Error('Organization sign-in could not be completed. Start again.'); }
  return response.json();
}

export function EmbedSignIn() {
  useI18n();
  const context = route?.context, pending = useRef<Promise<Application> | null>(null), exchange = useRef<Promise<Session> | null>(null);
  const [application, setApplication] = useState<Application | null>(null), [error, setError] = useState(route?.failure ? 'Organization sign-in could not be completed. Start again.' : '');
  const [busy, setBusy] = useState(Boolean(route?.code)), [done, setDone] = useState(false), [challenge, setChallenge] = useState(''), [factor, setFactor] = useState('');
  const mounted = useRef(true), delivered = useRef(false);
  const launching = useRef<Promise<void> | null>(null);
  const notifyError = () => { if (context && window.opener && !window.opener.closed) window.opener.postMessage({ protocol, type: 'error', requestId: context.requestId }, context.parentOrigin); };
  const finish = async (session: Session) => {
    if (!context || !mounted.current || delivered.current) return;
    if (session.mfa_required && session.challenge_token) { setChallenge(session.challenge_token); setBusy(false); return; }
    if (!session.access_token || (session.workspace_id && session.workspace_id !== context.workspaceId) || context.expiresAt <= Date.now()) throw new Error('Organization sign-in could not be completed. Start again.');
    launching.current ||= (async () => {
    const grant = await request<{ code: string }>('/workspaces/' + encodeURIComponent(context.workspaceId) + '/embed/launch', {
      application_id: context.applicationId, channel_id: context.channelId, parent_origin: context.parentOrigin,
      code_challenge: context.codeChallenge, code_challenge_method: 'S256',
    }, session.access_token);
    if (!mounted.current || delivered.current || !window.opener || window.opener.closed || context.expiresAt <= Date.now()) return;
    delivered.current = true; sessionStorage.removeItem(storageKey); setChallenge(''); setFactor('');
    window.opener.postMessage({ protocol, type: 'grant', requestId: context.requestId, code: grant.code }, context.parentOrigin);
    setDone(true); setBusy(false);
    })();
    await launching.current;
  };
  useEffect(() => {
    mounted.current = true;
    if (!context || route?.failure) return () => { mounted.current = false; };
    applyLocale(context.locale);
    pending.current ||= request<Application>('/embed/applications/' + encodeURIComponent(context.applicationId) + '?parent_origin=' + encodeURIComponent(context.parentOrigin));
    void pending.current.then(async result => {
      if (!mounted.current) return;
      if (result.workspace_id !== context.workspaceId || result.application_id !== context.applicationId || result.parent_origin !== context.parentOrigin || !result.login_id) throw new Error('Organization sign-in could not be completed. Start again.');
      setApplication(result);
      if (route?.code) { exchange.current ||= request<Session>('/auth/oidc/exchange', { code: route.code }); await finish(await exchange.current); }
    }).catch(() => { if (mounted.current) { setError('Organization sign-in could not be completed. Start again.'); setBusy(false); sessionStorage.removeItem(storageKey); notifyError(); } });
    return () => { mounted.current = false; };
  }, []);
  async function start() {
    if (!application || !context || busy || context.expiresAt <= Date.now()) return;
    setBusy(true); setError('');
    try {
      sessionStorage.setItem(storageKey, JSON.stringify(context));
      const result = await request<{ authorization_url: string }>('/auth/oidc/start', { login_id: application.login_id });
      if (!mounted.current) return;
      const url = new URL(result.authorization_url);
      if (url.protocol !== 'https:' || url.username || url.password) throw new Error();
      window.location.assign(url.href);
    } catch { if (mounted.current) { setError('Organization sign-in could not be completed. Start again.'); setBusy(false); notifyError(); } }
  }
  async function verify(event: FormEvent) {
    event.preventDefault(); if (busy || !challenge) return;
    setBusy(true); setError('');
    try { await finish(await request<Session>('/auth/mfa/verify', { challenge_token: challenge, code: factor.trim() })); }
    catch { if (mounted.current) { setError('The authentication code could not be verified. Try again.'); setBusy(false); } }
  }
  return <main className="fc-authentication-flow"><header><Brand /><button className="fc-account-link" type="button" onClick={() => window.close()}>{t('Close')}</button></header>
    <section className="fc-authentication-card" aria-labelledby="embed-signin-title"><span className="fc-account-feature-icon"><ShieldCheck size={28} /></span>
      <p className="fc-kicker">{t('Your organization, your account')}</p><h1 id="embed-signin-title">{t(done ? 'Your workspace is connected.' : 'Connect your workspace.')}</h1>
      {done ? <p role="status">{t('Return to your application. Your encrypted keys stay on this trusted origin.')}</p> : <>
        {application && <><p>{t('Continue with your organization to connect:')} <strong>{application.name}</strong></p><p className="fc-account-muted">{context?.parentOrigin}</p><p className="fc-account-muted">{t('Your application receives a one-time connection code. It does not receive your password or encryption keys.')}</p></>}
        {error && <div className="fc-alert fc-account-error" role="alert"><AlertCircle size={17} /><p>{t(error)}</p></div>}
        {challenge ? <form onSubmit={verify}><label htmlFor="embed-mfa">{t('Authentication or recovery code')}</label><input id="embed-mfa" className="fc-input" autoComplete="one-time-code" value={factor} onChange={event => setFactor(event.target.value)} disabled={busy} required /><button type="submit" className="fc-button fc-button-primary" disabled={busy}>{t('Verify')}</button></form>
          : application && !route?.code && !error && <button type="button" className="fc-button fc-button-primary" onClick={() => void start()} disabled={busy}>{busy ? <Loader2 size={16} className="fc-account-spinner" /> : <ArrowUpRight size={16} />}{t('Continue to organization')}</button>}
        {busy && <p className="fc-account-loading" role="status"><Loader2 size={16} className="fc-account-spinner" />{t('Checking your identity securely…')}</p>}
      </>}
    </section></main>;
}
