import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeft, Loader2, RotateCcw } from 'lucide-react';
import { api } from '../lib/api';
import { disconnectSocket } from '../lib/socket';
import { productDemoWebsiteURL, verifiedProductDemoSession, type ProductDemoSession as DemoSession } from '../lib/product-demo-session';
import { AccountProvider } from '../account/AccountProvider';
import { AppearanceProvider, useAppearance } from '../appearance/AppearanceProvider';
import { LocaleProvider, currentLocale, useI18n } from '../i18n';
import { Chat } from './Chat';
import '../styles/product-demo.css';

function DemoWorkspace({ session, onLogout }: { session: DemoSession; onLogout: () => void }) {
  const { openAppearance } = useAppearance();
  const [destination] = useState(() => new URLSearchParams(window.location.search));
  const section = destination.get('section'), provider = destination.get('provider');
  useEffect(() => { if (section === 'appearance') openAppearance(); }, [openAppearance, section]);
  const work = section === 'documents' ? 'documents' : section === 'projects' ? 'tasks' : undefined;
  const recipe = provider === 'microsoft' ? 'files.microsoft' : provider === 'google' ? 'files.google' : undefined;
  return <Chat onLogout={onLogout} initialChannelId={session.channel_id} initialWorkHubTab={work} initialIntegrationTab={section === 'connections' ? 'connections' : undefined} initialConnectionRecipeId={section === 'connections' ? recipe : undefined} />;
}

/** The demo has a session launcher, not a second implementation of collaboration. */
export function ProductDemoSession() {
  const { t, locale } = useI18n();
  const [session, setSession] = useState<DemoSession | null>(null);
  const [phase, setPhase] = useState<'loading' | 'ready' | 'failed' | 'expired' | 'ended'>('loading');
  const [error, setError] = useState('');
  const current = useRef<DemoSession | null>(null), busy = useRef(false), sequence = useRef(0), mounted = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const website = productDemoWebsiteURL(import.meta.env.VITE_DEMO_WEBSITE_URL);
  const embedded = window.parent !== window;

  const retire = useCallback((previous: DemoSession | null) => {
    if (!previous) return;
    // Use the captured sample token, never the replacement or a stored account.
    // Revocation is best effort; the server's bounded expiry remains authoritative.
    const abort = new AbortController(), timeout = window.setTimeout(() => abort.abort(), 5000);
    void fetch(api.getBaseUrl() + '/demo/session', {
      method: 'DELETE', credentials: 'omit', redirect: 'error', cache: 'no-store',
      headers: { Authorization: 'Bearer ' + previous.session.access_token }, signal: abort.signal,
    }).catch(() => {}).finally(() => window.clearTimeout(timeout));
  }, []);

  const start = useCallback(async () => {
    if (busy.current || !mounted.current) return;
    busy.current = true;
    const request = ++sequence.current, previous = current.current, choice = currentLocale();
    controller.current?.abort();
    const abort = new AbortController(); controller.current = abort;
    const timeout = window.setTimeout(() => abort.abort(), 45000);
    setPhase('loading'); setError(''); disconnectSocket();
    try {
      const response = await api.request<unknown>('/demo/session', { method: 'POST', body: '{}', signal: abort.signal });
      const next = verifiedProductDemoSession(response);
      if (!mounted.current || sequence.current !== request) { retire(next); return; }
      // Switching this memory-only account unmounts and recreates the real providers.
      api.applySession(next.session);
      try { await api.request('/preferences/language', { method: 'PUT', body: JSON.stringify({ locale: choice }), signal: abort.signal }); }
      catch { /* A preference failure must not discard a newly created workspace. */ }
      if (!mounted.current || sequence.current !== request) { retire(next); return; }
      current.current = next; setSession(next); setPhase('ready');
      if (previous && previous.visit_id !== next.visit_id) retire(previous);
    } catch (failure) {
      if (!mounted.current || sequence.current !== request) return;
      if (previous && Date.parse(previous.expires_at) > Date.now()) {
        api.applySession(previous.session); current.current = previous; setSession(previous); setPhase('ready');
        setError('Examples could not be reset. Your current workspace is still open.');
      } else {
        api.logout(); current.current = null; setSession(null); setPhase('failed');
        setError(failure instanceof Error && failure.name !== 'AbortError' ? failure.message : 'The example workspace could not be opened.');
      }
    } finally {
      clearTimeout(timeout);
      if (sequence.current === request) { busy.current = false; controller.current = null; }
    }
  }, [retire]);

  useEffect(() => {
    mounted.current = true;
    void start();
    const expired = () => {
      ++sequence.current; controller.current?.abort(); busy.current = false;
      disconnectSocket(); current.current = null; api.logout(); setSession(null); setPhase('expired'); setError('');
    };
    window.addEventListener('freecollab:session-expired', expired);
    return () => { mounted.current = false; ++sequence.current; controller.current?.abort(); disconnectSocket(); api.logout(); window.removeEventListener('freecollab:session-expired', expired); };
  }, [start]);

  useEffect(() => {
    if (!session || phase !== 'ready') return;
    const timer = window.setTimeout(() => {
      if (current.current?.visit_id !== session.visit_id) return;
      ++sequence.current; controller.current?.abort(); busy.current = false;
      disconnectSocket(); current.current = null; api.logout(); setSession(null); setPhase('expired'); setError('');
    }, Math.max(0, Date.parse(session.expires_at) - Date.now()));
    return () => window.clearTimeout(timer);
  }, [session, phase]);

  const reset = () => { if (!busy.current && window.confirm(t('Reset this example workspace? Your changes in this demo will be lost.'))) void start(); };
  const end = () => {
    const previous = current.current;
    ++sequence.current; controller.current?.abort(); busy.current = false;
    disconnectSocket(); current.current = null; api.logout(); setSession(null); setPhase('ended'); retire(previous);
  };

  return <div className="fc-product-demo" data-product-demo>
    <header className="fc-product-demo-banner" aria-label={t('Example workspace')}>
      <div><strong>{t('Demo')}</strong><span>{t('Use example data only. Do not enter real passwords or API keys.')}</span></div>
      <div className="fc-product-demo-controls">
        {session && phase === 'ready' && <span className="fc-product-demo-expiry">{t('Available until')} <time dateTime={session.expires_at}>{new Date(session.expires_at).toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' })}</time></span>}
        <button type="button" className="fc-button" disabled={phase === 'loading'} onClick={reset}><RotateCcw size={15} /><span>{t('Reset examples')}</span></button>
        {!embedded && <a href={website} className="fc-button"><ArrowLeft size={15} /><span>{t('Back to website')}</span></a>}
      </div>
    </header>
    {error && phase === 'ready' && <p role="alert" className="fc-product-demo-notice">{t(error)}</p>}
    <div className="fc-product-demo-content">
      {phase === 'ready' && session ? <LocaleProvider key={session.visit_id}><AppearanceProvider><AccountProvider><DemoWorkspace session={session} onLogout={end} /></AccountProvider></AppearanceProvider></LocaleProvider>
        : <section className="fc-product-demo-start" aria-live="polite">
          {phase === 'loading' ? <><Loader2 size={26} className="fc-product-demo-spinner" aria-hidden="true" /><h1>{t('Opening the example workspace…')}</h1></>
            : <><h1>{t(phase === 'expired' ? 'This example session has expired.' : phase === 'ended' ? 'You have left the example workspace.' : 'The example workspace could not be opened.')}</h1>
              <p>{t('The demo runs the real product with a separate, temporary example workspace.')}</p>
              {error && <p role="alert">{t(error)}</p>}
              <button type="button" className="fc-button fc-button-primary" onClick={() => void start()}>{t(phase === 'failed' ? 'Retry' : 'Start a new example workspace')}</button></>}
        </section>}
    </div>
  </div>;
}
