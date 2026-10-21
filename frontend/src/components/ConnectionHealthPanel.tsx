import { useCallback, useEffect, useRef, useState } from 'react';
import { Activity, ArrowUpRight, Loader2, RefreshCw, ShieldCheck } from 'lucide-react';
import { useI18n } from '../i18n';
import { api, type Channel } from '../lib/api';
import { connectionSetupApi, type ConnectionSetup } from '../lib/connection-setup-api';
import { connectionHealthRows, connectionHealthSummary, connectionNextAction, connectionStatusLabels, type ConnectionHealthFilter } from '../lib/connection-health';
import '../styles/pilot.css';

interface Props { workspaceId: string; channels: Channel[]; initialChannelId?: string | null; onOpenSetup: (recipeId?: string, channelId?: string) => void }
export function ConnectionHealthPanel({ workspaceId, channels, initialChannelId, onOpenSetup }: Props) {
  const { t, locale } = useI18n();
  const manageable = channels.filter(row => row.workspace_id === workspaceId && row.role === 'admin' && row.type !== 'direct');
  const [channelId, setChannelId] = useState(() => manageable.some(row => row.id === initialChannelId) ? initialChannelId! : '');
  const [filter, setFilter] = useState<ConnectionHealthFilter>('configured');
  const [catalog, setCatalog] = useState<ConnectionSetup | null>(null), [checkedAt, setCheckedAt] = useState('');
  const [loading, setLoading] = useState(true), [error, setError] = useState(''), [expired, setExpired] = useState(false);
  const owner = useRef({ token: api.getAccessToken(), user: api.getCurrentUserId(), workspaceId });
  const mounted = useRef(true), generation = useRef(0), controller = useRef<AbortController | null>(null);
  const current = useCallback(() => mounted.current && owner.current.token === api.getAccessToken() && owner.current.user === api.getCurrentUserId() && owner.current.workspaceId === workspaceId, [workspaceId]);
  const load = useCallback(async () => {
    if (!current()) return;
    controller.current?.abort(); const abort = new AbortController(); controller.current = abort;
    const request = ++generation.current; setLoading(true); setCatalog(null); setError(''); setCheckedAt('');
    try {
      const result = await connectionSetupApi.get(workspaceId, channelId || null, abort.signal);
      if (current() && request === generation.current && !abort.signal.aborted) { setCatalog(result); setCheckedAt(new Date().toISOString()); }
    } catch (failure) { if (current() && request === generation.current && !abort.signal.aborted) setError(failure instanceof Error ? failure.message : 'Connection status could not be loaded.'); }
    finally { if (current() && request === generation.current && !abort.signal.aborted) setLoading(false); }
  }, [workspaceId, channelId, current]);
  useEffect(() => { mounted.current = true; void load(); return () => { mounted.current = false; ++generation.current; controller.current?.abort(); }; }, [load]);
  useEffect(() => {
    const invalidate = () => { ++generation.current; controller.current?.abort(); setCatalog(null); setCheckedAt(''); setLoading(false); setExpired(true); };
    window.addEventListener('freecollab:auth-changed', invalidate);
    return () => window.removeEventListener('freecollab:auth-changed', invalidate);
  }, []);
  const scoped = catalog?.workspace_id === workspaceId && catalog.channel_id === (channelId || null) && !expired ? catalog : null;
  const summary = connectionHealthSummary(scoped?.recipes || []), rows = connectionHealthRows(scoped?.recipes || [], filter);
  return <div className="fc-pilot-section fc-connection-health" aria-busy={loading}>
    <header className="fc-pilot-heading"><div><span className="fc-kicker">{t('Stay connected')}</span><h3>{t('Connections at a glance.')}</h3><p>{t('Check saved access, resolve setup issues and choose what to test next.')}</p></div><button className="fc-button" type="button" disabled={loading || expired} onClick={() => void load()}><RefreshCw size={15} />{t('Refresh status')}</button></header>
    {expired ? <p className="fc-alert" role="status">{t('Your session changed. Reopen this panel to continue.')}</p> : <>
      <div className="fc-pilot-controls"><label>{t('Connection channel')}<select className="fc-input" value={channelId} onChange={event => setChannelId(event.target.value)}><option value="">{t('Workspace connections only')}</option>{manageable.map(row => <option key={row.id} value={row.id}>{row.name || t('Unnamed channel')}</option>)}</select></label><label>{t('Show connections')}<select className="fc-input" value={filter} onChange={event => setFilter(event.target.value as ConnectionHealthFilter)}><option value="configured">{t('With saved configuration')}</option><option value="follow_up">{t('Need a next step')}</option><option value="all">{t('All supported services')}</option></select></label></div>
      {error && <p className="fc-alert" role="alert">{t(error)}</p>}
      {loading ? <p className="fc-account-loading" role="status"><Loader2 size={17} className="fc-account-spinner" />{t('Checking saved connections…')}</p> : scoped && <>
        <dl className="fc-pilot-stats"><div><dt>{t('Saved services')}</dt><dd>{summary.configured}</dd></div><div><dt>{t('Configuration ready')}</dt><dd>{summary.ready}</dd></div><div><dt>{t('Need a next step')}</dt><dd>{summary.followUp}</dd></div><div><dt>{t('Paused')}</dt><dd>{summary.paused}</dd></div></dl>
        {checkedAt && <p className="fc-account-muted fc-pilot-small">{t('Saved state checked: {date}', { date: new Date(checkedAt).toLocaleString(locale, { dateStyle: 'medium', timeStyle: 'short' }) })}</p>}
        <ul className="fc-pilot-checks">{rows.map(row => <li key={row.id}><Activity size={18} /><div><strong>{t(row.name)}</strong><span className="fc-connection-status" data-status={row.status}>{t(connectionStatusLabels[row.status])}</span>{row.blockers.length > 0 && <p>{t(row.blockers[0])}</p>}{row.connections.map(connection => <small key={connection.id}>{connection.name}{connection.verified_at ? ' · ' + t(row.target === 'people' ? 'Last directory request: {date}' : row.target === 'webhooks' ? 'Last successful delivery: {date}' : 'Last explicit verification: {date}', { date: new Date(connection.verified_at).toLocaleString(locale, { dateStyle: 'medium', timeStyle: 'short' }) }) : ''}</small>)}<button type="button" className="fc-account-link" onClick={() => { if (current()) onOpenSetup(row.id, channelId || undefined); }}>{t(connectionNextAction(row.status))}<ArrowUpRight size={14} /></button></div></li>)}</ul>
        {!rows.length && <div className="fc-pilot-empty"><ShieldCheck size={24} /><h4>{t(filter === 'follow_up' ? 'No saved setup steps need attention here.' : 'Prepare your first connection.')}</h4><p>{t('Choose a channel to check its services, or open the guide to connect a customer account.')}</p><button type="button" className="fc-button" onClick={() => { if (current()) onOpenSetup(undefined, channelId || undefined); }}>{t('Open connection setup')}<ArrowUpRight size={15} /></button></div>}
      </>}
    </>}
    <p className="fc-pilot-boundary"><ShieldCheck size={16} />{t('This overview checks saved configuration and current access. Test provider connectivity explicitly in the connection guide. Unknown expiry dates are not treated as valid future access.')}</p>
  </div>;
}
