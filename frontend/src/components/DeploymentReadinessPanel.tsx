import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertCircle, CheckCircle2, HelpCircle, Loader2, RefreshCw, ShieldCheck } from 'lucide-react';
import { useI18n } from '../i18n';
import { api } from '../lib/api';
import '../styles/pilot.css';

type ReadinessState = 'ready' | 'attention' | 'unconfigured' | 'unknown';
interface ReadinessCheck { id: string; status: ReadinessState; label: string; detail: string; action: string }
interface Readiness { scope: 'local_configuration'; checked_at: string; status: ReadinessState; summary: string; public_origin: string | null; checks: ReadinessCheck[] }
const labels: Record<ReadinessState, string> = { ready: 'Configured', attention: 'Needs attention', unconfigured: 'Not configured', unknown: 'Needs external verification' };

export function DeploymentReadinessPanel() {
  const { t, locale } = useI18n();
  const [data, setData] = useState<Readiness | null>(null), [loading, setLoading] = useState(true), [error, setError] = useState(''), [expired, setExpired] = useState(false);
  const owner = useRef({ token: api.getAccessToken(), user: api.getCurrentUserId() });
  const mounted = useRef(true), generation = useRef(0), controller = useRef<AbortController | null>(null);
  const current = useCallback(() => mounted.current && owner.current.token === api.getAccessToken() && owner.current.user === api.getCurrentUserId(), []);
  const load = useCallback(async () => {
    if (!current()) return;
    controller.current?.abort(); const abort = new AbortController(); controller.current = abort;
    const request = ++generation.current; setData(null); setLoading(true); setError('');
    try {
      const result = await api.request<Readiness>('/operations/pilot-readiness', { signal: abort.signal, cache: 'no-store' });
      if (result.scope !== 'local_configuration' || !Array.isArray(result.checks) || result.checks.length > 50 || !Number.isFinite(Date.parse(result.checked_at)) || !result.checks.every(row => row && typeof row.id === 'string' && Object.prototype.hasOwnProperty.call(labels, row.status) && ['label', 'detail', 'action'].every(key => typeof row[key as keyof ReadinessCheck] === 'string'))) throw new Error('Deployment checks returned an invalid response.');
      if (current() && request === generation.current && !abort.signal.aborted) setData(result);
    } catch (failure) { if (current() && request === generation.current && !abort.signal.aborted) setError(failure instanceof Error ? failure.message : 'Deployment checks could not be loaded.'); }
    finally { if (current() && request === generation.current && !abort.signal.aborted) setLoading(false); }
  }, [current]);
  useEffect(() => { mounted.current = true; void load(); return () => { mounted.current = false; ++generation.current; controller.current?.abort(); }; }, [load]);
  useEffect(() => {
    const invalidate = () => { ++generation.current; controller.current?.abort(); setData(null); setLoading(false); setExpired(true); };
    window.addEventListener('freecollab:auth-changed', invalidate); return () => window.removeEventListener('freecollab:auth-changed', invalidate);
  }, []);
  return <section className="fc-account-section fc-pilot-section" aria-busy={loading} aria-label={t('Deployment readiness')}>
    <header className="fc-pilot-heading"><div><span className="fc-kicker">{t('Run it with confidence')}</span><h3>{t('Your installation, in view.')}</h3><p>{t('Review the controls this installation can check, and the work that still needs an operator.')}</p></div><button type="button" className="fc-button" disabled={loading || expired} onClick={() => void load()}><RefreshCw size={15} />{t('Refresh deployment checks')}</button></header>
    {expired ? <p className="fc-alert" role="status">{t('Your session changed. Reopen this panel to continue.')}</p> : error ? <p className="fc-alert" role="alert">{t(error)}</p> : loading ? <p className="fc-account-loading" role="status"><Loader2 size={17} className="fc-account-spinner" />{t('Checking this installation…')}</p> : data && <>
      <p className="fc-pilot-small">{t('Checked: {date}', { date: new Date(data.checked_at).toLocaleString(locale, { dateStyle: 'medium', timeStyle: 'short' }) })}</p>
      {data.public_origin && <p className="fc-account-muted"><strong>{data.public_origin}</strong></p>}
      <ul className="fc-pilot-checks">{data.checks.map(row => { const Icon = row.status === 'ready' ? CheckCircle2 : row.status === 'attention' || row.status === 'unconfigured' ? AlertCircle : HelpCircle; return <li key={row.id}><Icon size={18} /><div><strong>{t(row.label)}</strong><span className="fc-pilot-readiness-status" data-status={row.status}>{t(labels[row.status])}</span><p>{t(row.detail)}</p>{row.status !== 'ready' && row.action && <p>{t(row.action)}</p>}</div></li>; })}</ul>
      <p className="fc-pilot-boundary"><ShieldCheck size={16} />{t(data.summary)}</p>
    </>}
    <a className="fc-account-link" href="https://github.com/freecollab-eu/freecollab/blob/main/RAILWAY_DEPLOY.md" target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">{t('Railway and self-hosting guide')}</a>
  </section>;
}
