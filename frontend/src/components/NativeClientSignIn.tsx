import { useEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { AlertCircle, ArrowUpRight, Laptop, Loader2, ShieldCheck, X } from 'lucide-react';
import { t, useI18n } from '../i18n';
import { api } from '../lib/api';
import { nativeContextKey, validateNativeContext, type NativeClientContext } from '../lib/native-client-context';
import { useModalFocus } from '../lib/useModalFocus';
import '../styles/native-clients.css';

interface Callback { code: string; code_verifier: string; redirect_uri: string }
interface NativeBridge { kind: 'desktop' | 'mobile'; signIn(): Promise<Callback>; cancelSignIn?(): Promise<void> }
declare global {
  interface Window {
    freecollabNative?: NativeBridge;
    Capacitor?: { isNativePlatform?(): boolean; registerPlugin?(name: string): { signIn(): Promise<Callback>; cancelSignIn(): Promise<void> } };
  }
}
let mobileBridge: NativeBridge | null = null;
function nativeBridge(): NativeBridge | null {
  if (window.freecollabNative?.kind === 'desktop' && typeof window.freecollabNative.signIn === 'function') return window.freecollabNative;
  if (window.Capacitor?.isNativePlatform?.() && window.Capacitor.registerPlugin) {
    if (!mobileBridge) {
      const plugin = window.Capacitor.registerPlugin('FreeCollabNative');
      mobileBridge = { kind: 'mobile', signIn: () => plugin.signIn(), cancelSignIn: () => plugin.cancelSignIn() };
    }
    return mobileBridge;
  }
  return null;
}
function capture(): { context: NativeClientContext | null; failed: boolean } {
  try {
    const fragment = location.hash;
    if (fragment.startsWith('#native-login=')) {
      if (fragment.length > 2048) throw new Error();
      const raw = fragment.slice(14).replace(/-/g, '+').replace(/_/g, '/');
      const context = validateNativeContext(JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(raw), c => c.charCodeAt(0)))));
      // Public PKCE challenge/routing only. The verifier stays in the native process.
      sessionStorage.setItem(nativeContextKey, JSON.stringify(context));
      history.replaceState(null, '', location.pathname + location.search);
      return { context, failed: false };
    }
    const saved = sessionStorage.getItem(nativeContextKey);
    return { context: saved ? validateNativeContext(JSON.parse(saved)) : null, failed: false };
  } catch { sessionStorage.removeItem(nativeContextKey); return { context: null, failed: true }; }
}
// Capture before the ordinary App consumes its SSO fragment, once under StrictMode.
const route = typeof window === 'undefined' ? { context: null, failed: false } : capture();

export function NativeClientSignIn({ children }: { children: ReactNode }) {
  useI18n();
  const [context, setContext] = useState(route.context), [signedIn, setSignedIn] = useState(api.isAuthenticated());
  const [busy, setBusy] = useState(false), [error, setError] = useState(route.failed ? 'Invalid native client connection. Start again from your app.' : '');
  const [accountName, setAccountName] = useState('');
  const mounted = useRef(true), dialog = useRef<HTMLElement>(null), inFlight = useRef(false);
  const bridge = nativeBridge();
  const close = () => { if (inFlight.current) return; sessionStorage.removeItem(nativeContextKey); setContext(null); setError(''); };
  useModalFocus(dialog, close, Boolean(context && signedIn));
  useEffect(() => {
    mounted.current = true;
    const changed = () => setSignedIn(api.isAuthenticated());
    window.addEventListener('freecollab:auth-changed', changed);
    return () => { mounted.current = false; window.removeEventListener('freecollab:auth-changed', changed); };
  }, []);
  useEffect(() => {
    if (!context || !signedIn) return;
    const token = api.getAccessToken();
    void api.getMe().then(me => { if (mounted.current && token === api.getAccessToken()) setAccountName(me.name); }).catch(() => {});
  }, [context, signedIn]);
  async function connect() {
    if (!context || !signedIn || inFlight.current) return;
    const token = api.getAccessToken(); inFlight.current = true; setBusy(true); setError('');
    try {
      validateNativeContext(context);
      const grant = await api.request<{ code: string; redirect_uri: string }>('/native-clients/connect', { method: 'POST', body: JSON.stringify({
        code_challenge: context.codeChallenge, redirect_uri: context.redirectUri, client_kind: context.clientKind }), signal: AbortSignal.timeout(20000) });
      if (!mounted.current || token !== api.getAccessToken()) throw new Error();
      validateNativeContext(context);
      if (grant.redirect_uri !== context.redirectUri || !/^[A-Za-z0-9_-]{43}$/.test(grant.code)) throw new Error();
      const callback = new URL(context.redirectUri); callback.searchParams.set('code', grant.code); callback.searchParams.set('state', context.state);
      sessionStorage.removeItem(nativeContextKey); location.assign(callback.href);
    } catch { if (mounted.current) { setError('The connection could not be completed. Start again from your app.'); setBusy(false); inFlight.current = false; } }
  }
  async function browserSignIn() {
    if (!bridge || inFlight.current) return;
    const oldToken = api.getAccessToken(); inFlight.current = true; setBusy(true); setError('');
    try {
      const callback = await bridge.signIn();
      if (!mounted.current || oldToken !== api.getAccessToken()) throw new Error();
      if (!/^[A-Za-z0-9_-]{43}$/.test(callback.code) || !/^[A-Za-z0-9._~-]{43,128}$/.test(callback.code_verifier)) throw new Error();
      const result = await api.request<Parameters<typeof api.applySession>[0]>('/native-clients/exchange', {
        method: 'POST', body: JSON.stringify(callback), signal: AbortSignal.timeout(20000) });
      if (!mounted.current || oldToken !== api.getAccessToken()) throw new Error();
      api.applySession(result); window.dispatchEvent(new HashChangeEvent('hashchange'));
    } catch { if (mounted.current) setError('The connection could not be completed. Start again from your app.'); }
    finally { if (mounted.current) { setBusy(false); inFlight.current = false; } }
  }
  return <>{children}
    {bridge && !signedIn && <aside className="fc-native-signin" aria-label={t('Native app sign-in')}><ShieldCheck size={18} /><span>{t('Use your browser for organization sign-in.')}</span><button type="button" className="fc-button fc-button-primary" disabled={busy} onClick={() => void browserSignIn()}>{busy ? <Loader2 size={16} className="fc-account-spinner" /> : <ArrowUpRight size={16} />}{t('Sign in with browser')}</button>{busy && bridge.cancelSignIn && <button type="button" className="fc-button" onClick={() => void bridge.cancelSignIn!().catch(() => {})}>{t('Cancel')}</button>}{error && <p role="alert">{t(error)}</p>}</aside>}
    {context && signedIn && createPortal(<div className="fc-account-overlay"><section ref={dialog} className="fc-account-panel fc-native-approval" role="dialog" aria-modal="true" aria-label={t('Connect your app')} tabIndex={-1}>
      <header className="fc-account-header"><div><span className="fc-kicker">FreeCollab</span><h2>{t('Connect your app')}</h2></div><button type="button" className="fc-icon-button" aria-label={t('Cancel')} disabled={busy} onClick={close}><X size={20} /></button></header>
      <div className="fc-account-body"><Laptop size={32} /><p>{t('Connect the app you just opened to this FreeCollab installation.')}</p><p><strong>{accountName}</strong><br />{location.origin}</p><p>{t('Approve only if you started this connection. Your organization’s sign-in and security requirements still apply.')}</p><p className="fc-account-muted">{t('Signing out of this browser session also disconnects the linked app. Encryption keys stay on each trusted device.')}</p>
        {error && <div className="fc-alert fc-account-error" role="alert"><AlertCircle size={17} /><p>{t(error)}</p></div>}
        <button type="button" className="fc-button fc-button-primary" disabled={busy} onClick={() => void connect()}>{busy && <Loader2 size={16} className="fc-account-spinner" />}{t('Connect this app')}</button>
      </div></section></div>, document.body)}
    {!bridge && !context && error && <aside className="fc-native-signin" role="alert"><AlertCircle size={18} /><p>{t(error)}</p><button type="button" className="fc-icon-button" aria-label={t('Close')} onClick={() => setError('')}><X size={18} /></button></aside>}
  </>;
}
