import { useEffect, useRef, useState } from 'react';
import { api } from '../lib/api';
import { enterpriseSearchApi, type ExternalSearchHit, type SearchConnection } from '../lib/enterprise-search';
import { t, useI18n } from '../i18n';
import { SemanticSearchPanel } from './SemanticSearchPanel';
import { PersonalOAuthPanel } from './PersonalOAuthPanel';

/** Separate explicit provider query; never receives the local history query. */
export function EnterpriseSearchPanel({ workspaceIds }: { workspaceIds: string[] }) {
  useI18n();
  const [workspace, setWorkspace] = useState(workspaceIds[0] || '');
  const workspaceRef = useRef(workspace); workspaceRef.current = workspace;
  const accessRevision = useRef(0);
  const current = (owner: string | null, target: string, version?: number) => owner === api.getAccessToken() && workspaceRef.current === target && (version === undefined || version === accessRevision.current);
  const [connections, setConnections] = useState<SearchConnection[]>([]), [selected, setSelected] = useState('');
  const [query, setQuery] = useState(''), [hits, setHits] = useState<ExternalSearchHit[]>([]);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [incomplete, setIncomplete] = useState(false);
  const [adding, setAdding] = useState(false), [provider, setProvider] = useState<SearchConnection['provider']>('google');
  const [name, setName] = useState(''), [token, setToken] = useState(''), [folder, setFolder] = useState(''), [drive, setDrive] = useState('');
  const [endpoint, setEndpoint] = useState(''), [resolveEndpoint, setResolveEndpoint] = useState(''), [subject, setSubject] = useState('');
  useEffect(() => { if (!connections.some(row => row.id === selected)) setSelected(connections[0]?.id || ''); }, [connections, selected]);
  const scope = workspaceIds.join(',');
  useEffect(() => { if (!workspaceIds.includes(workspace)) { setWorkspace(workspaceIds[0] || ''); setToken(''); } }, [scope, workspace]);
  useEffect(() => {
    let live = true, serial = 0; const owner = api.getAccessToken(); setHits([]); setConnections([]); setSelected(''); setError(''); setBusy(false);
    const load = () => {
      const version = ++serial; accessRevision.current++; setHits([]);
      if (workspace) void enterpriseSearchApi.connections(workspace).then(rows => { if (live && version === serial && current(owner, workspace)) { setConnections(rows); setSelected(old => rows.some(row => row.id === old) ? old : rows[0]?.id || ''); } })
        .catch(failure => { if (live && version === serial && current(owner, workspace)) setError(failure instanceof Error ? failure.message : 'Provider connections unavailable.'); });
    };
    const changed = (event: Event) => { if ((event as CustomEvent<{ workspaceId: string }>).detail?.workspaceId === workspace) load(); };
    load(); window.addEventListener('freecollab:provider-accounts-changed', changed); window.addEventListener('focus', load);
    return () => { live = false; serial++; window.removeEventListener('freecollab:provider-accounts-changed', changed); window.removeEventListener('focus', load); };
  }, [workspace]);
  if (!workspaceIds.length) return null;
  async function connect(event: React.FormEvent) {
    event.preventDefault(); const owner = api.getAccessToken(), target = workspace; setBusy(true); setError('');
    const credentials = token; setToken('');
    try {
      const config: Record<string, string> = provider === 'google' ? { folder_id: folder } : provider === 'microsoft' ? { folder_id: folder, drive_id: drive }
        : { search_url: endpoint, resolve_url: resolveEndpoint, external_subject: subject };
      const result = await enterpriseSearchApi.connect(workspace, { provider, name, config, access_token: credentials, delegated_access_acknowledged: true });
      if (!current(owner, target)) return;
      setConnections(old => [...old, result]); setSelected(result.id); setAdding(false);
    } catch (failure) { if (current(owner, target)) setError(failure instanceof Error ? failure.message : 'Connection failed.'); }
    finally { if (current(owner, target)) setBusy(false); }
  }
  async function search(event: React.FormEvent) {
    event.preventDefault(); const connection = connections.find(item => item.id === selected); if (!connection) return;
    const owner = api.getAccessToken(), target = workspace, version = accessRevision.current; setBusy(true); setError(''); setHits([]); setIncomplete(false);
    try { const result = await enterpriseSearchApi.query(workspace, connection, query); if (!current(owner, target, version)) return; setHits(result.items); setIncomplete(!result.complete); }
    catch (failure) { if (current(owner, target, version)) setError(failure instanceof Error ? failure.message : 'Search failed.'); }
    finally { if (current(owner, target)) setBusy(false); }
  }
  async function open(hit: ExternalSearchHit) {
    const owner = api.getAccessToken(), target = workspace, version = accessRevision.current; setBusy(true); setError('');
    // Open a blank window synchronously to preserve browser popup activation;
    // no provider URL is used until live provider authorization succeeds.
    const page = window.open('about:blank', '_blank'); if (page) page.opener = null;
    try {
      const latest = await enterpriseSearchApi.resolve(hit.reference);
      if (!current(owner, target, version)) { page?.close(); return; }
      const url = new URL(latest.url); if (url.protocol !== 'https:' || url.username || url.password) throw new Error('Invalid provider destination.');
      if (page) page.location.replace(url.href); else throw new Error('Allow a new window to open this provider result.');
    } catch (failure) { page?.close(); if (current(owner, target)) { setHits(old => old.filter(item => item.reference !== hit.reference)); setError(failure instanceof Error ? failure.message : 'Access changed.'); } }
    finally { if (current(owner, target)) setBusy(false); }
  }
  return <><SemanticSearchPanel key={workspace} workspaceId={workspace} /><details className="fc-enterprise-search"><summary>{t('Search connected services')}</summary>
    <p className="fc-chat-muted">{t('This separate query is sent only to your selected personal provider. Local history and decrypted channel content stay on this device.')}</p>
    {workspaceIds.length > 1 && <label>{t('Workspace')}<select className="fc-input" disabled={busy} value={workspace} onChange={event => setWorkspace(event.target.value)}>{workspaceIds.map(id => <option key={id} value={id}>{id}</option>)}</select></label>}
    {connections.length > 0 && <form onSubmit={event => void search(event)} className="fc-enterprise-search-form">
      <label>{t('Personal provider')}<select className="fc-input" disabled={busy} value={selected} onChange={event => { setSelected(event.target.value); setHits([]); }}>{connections.map(item => <option key={item.id} value={item.id}>{item.name} · {item.provider}</option>)}</select></label>
      <label>{t('Provider query')}<input className="fc-input" value={query} onChange={event => setQuery(event.target.value)} maxLength={500} required /></label>
      <button className="fc-button fc-button-primary" disabled={busy || !query.trim()}>{busy ? t('Searching…') : t('Send query to provider')}</button>
      <button type="button" className="fc-button" disabled={busy} onClick={() => { const owner = api.getAccessToken(); setBusy(true); void enterpriseSearchApi.disconnect(workspace, selected).then(() => { if (owner === api.getAccessToken()) { setConnections(old => old.filter(row => row.id !== selected)); setSelected(''); setHits([]); } }).catch(failure => { if (owner === api.getAccessToken()) setError(String(failure)); }).finally(() => { if (owner === api.getAccessToken()) setBusy(false); }); }}>{t('Disconnect')}</button>
    </form>}
    <details><summary>{t('Connect with Google or Microsoft OAuth')}</summary><PersonalOAuthPanel key={workspace} workspaceId={workspace} /></details>
    <button type="button" className="fc-chat-text-button" onClick={() => { setAdding(old => !old); setToken(''); }}>{t('Add a manually managed provider token')}</button>
    {adding && <form onSubmit={event => void connect(event)} className="fc-enterprise-search-form">
      <p>{t('Use a read-only delegated token issued by your organization for your own account. Credentials are encrypted on the server; disconnect removes the delegation. Expired tokens must be replaced.')}</p>
      <label>{t('Name')}<input className="fc-input" required maxLength={80} value={name} onChange={event => setName(event.target.value)} /></label>
      <label>{t('Provider')}<select className="fc-input" value={provider} onChange={event => setProvider(event.target.value as SearchConnection['provider'])}><option value="google">Google Drive</option><option value="microsoft">Microsoft 365</option><option value="custom">{t('Custom HTTPS connector')}</option></select></label>
      {provider !== 'custom' && <label>{t('Folder ID')}<input className="fc-input" required value={folder} onChange={event => setFolder(event.target.value)} /></label>}
      {provider === 'microsoft' && <label>{t('Drive ID')}<input className="fc-input" required value={drive} onChange={event => setDrive(event.target.value)} /></label>}
      {provider === 'custom' && <><label>{t('HTTPS search endpoint')}<input className="fc-input" type="url" required value={endpoint} onChange={event => setEndpoint(event.target.value)} /></label><label>{t('HTTPS resolve endpoint')}<input className="fc-input" type="url" required value={resolveEndpoint} onChange={event => setResolveEndpoint(event.target.value)} /></label><label>{t('External account subject')}<input className="fc-input" required value={subject} onChange={event => setSubject(event.target.value)} /></label></>}
      <label>{t('Your delegated access token')}<input className="fc-input" type="password" autoComplete="off" required maxLength={16000} value={token} onChange={event => setToken(event.target.value)} /></label>
      <label><input type="checkbox" required /> {t('This delegation belongs to my own provider account and has read-only access.')}</label>
      <button className="fc-button" disabled={busy}>{t('Connect personal search')}</button>
    </form>}
    {error && <p className="fc-alert" role="alert">{error}</p>}
    {incomplete && <p role="status">{t('Provider results are limited. Refine this query for more precise results.')}</p>}
    {hits.map(hit => <button type="button" className="fc-history-result" key={hit.reference} disabled={busy} onClick={() => void open(hit)}><span><strong>{hit.title}</strong><small>{hit.provider} · {t('Personal provider access verified')}</small></span>{hit.text && <p>{hit.text}</p>}</button>)}
  </details></>;
}
