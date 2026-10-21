import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowUpRight, Building2, CalendarDays, Check, CheckCircle2, Copy, Download, FolderOpen, Loader2, RefreshCw, ShieldCheck, Sparkles, Video, Webhook } from 'lucide-react';
import { useI18n } from '../i18n';
import { api, type Channel } from '../lib/api';
import type { AccountSecurity } from '../lib/account-api';
import { connectionRecipeDownload, connectionSetupApi, type ConnectionCategory, type ConnectionRecipe, type ConnectionSetup, type ConnectionStatus } from '../lib/connection-setup-api';
import { WorkspaceSSOSettings } from './WorkspaceSSOSettings';
import { SyncSettings } from './SyncSettings';
import { CloudFilesPicker } from './CloudFilesPicker';
import { ExternalMeetingsPanel } from './ExternalMeetingsPanel';
import { CopilotPanel } from './CopilotPanel';
import { SCIMSettings } from './SCIMSettings';
import { WebhookConnections } from './WebhookConnections';
import '../styles/connection-setup.css';

type Guard = 'busy' | 'secret' | null;
type Step = 'prepare' | 'configure' | 'status';
interface Props { workspaceId: string; channels: Channel[]; security: AccountSecurity | null; isOwner?: boolean; initialRecipeId?: string; initialChannelId?: string; onGuardChange: (guard: Guard) => void }
const categories = { identity: 'Identity & people', files: 'Files', calendar: 'Calendar', meetings: 'Meetings', ai: 'AI assistance', automation: 'Automation' } as const;
const icons = { identity: Building2, files: FolderOpen, calendar: CalendarDays, meetings: Video, ai: Sparkles, automation: Webhook };
const statuses: Record<ConnectionStatus, string> = {
  not_configured: 'Not configured', draft: 'Draft saved', authorization_required: 'Account authorization needed',
  verification_required: 'Explicit test needed', ready: 'Configuration ready', paused: 'Paused', attention: 'Needs attention', unavailable: 'Unavailable',
};
const steps: { id: Step; label: string }[] = [{ id: 'prepare', label: 'Prepare' }, { id: 'configure', label: 'Configure' }, { id: 'status', label: 'Status' }];
const supported = (recipe: ConnectionRecipe) => recipe.target === 'sso' && recipe.provider === 'oidc'
  || recipe.target === 'sync' && ['google', 'microsoft', 'nextcloud', 'caldav'].includes(recipe.provider)
  || recipe.target === 'cloud_files' && ['dropbox', 'box'].includes(recipe.provider)
  || recipe.target === 'external_meetings' && ['google_meet', 'microsoft_teams', 'zoom'].includes(recipe.provider)
  || recipe.target === 'copilot' && recipe.provider === 'openai_compatible'
  || recipe.target === 'people' && recipe.provider === 'scim'
  || recipe.target === 'webhooks' && recipe.provider === 'webhook';

/** Guides deliberate setup; provider authorization, tests and writes stay explicit. */
export function ConnectionSetupPanel({ workspaceId, channels, security, isOwner = false, initialRecipeId, initialChannelId, onGuardChange }: Props) {
  const { t, locale } = useI18n();
  const [catalog, setCatalog] = useState<ConnectionSetup | null>(null);
  const [recipeId, setRecipeId] = useState<string | null>(null), [category, setCategory] = useState<ConnectionCategory | 'all'>('all');
  const [channelId, setChannelId] = useState(''), [needsChannel, setNeedsChannel] = useState(false), [step, setStep] = useState<Step>('prepare');
  const [loading, setLoading] = useState(true), [error, setError] = useState(''), [notice, setNotice] = useState(''), [expired, setExpired] = useState(false);
  const [dirty, setDirty] = useState(false), [childGuard, setChildGuard] = useState<Guard>(null);
  const dirtyRef = useRef(false), childGuardRef = useRef<Guard>(null), loadingRef = useRef(true);
  const owner = useRef({ workspaceId, token: api.getAccessToken(), user: api.getCurrentUserId() });
  const mounted = useRef(true), sequence = useRef(0), controller = useRef<AbortController | null>(null), heading = useRef<HTMLHeadingElement>(null);
  const urls = useRef(new Set<string>()), timers = useRef(new Set<ReturnType<typeof setTimeout>>());
  const initialSelection = useRef({ recipeId: initialRecipeId, channelId: initialChannelId, applied: false });
  const scopeChannel = needsChannel ? channelId || null : null;
  const current = useCallback(() => mounted.current && owner.current.workspaceId === workspaceId && owner.current.token === api.getAccessToken() && owner.current.user === api.getCurrentUserId(), [workspaceId]);
  const childGuardChanged = useCallback((value: Guard) => { childGuardRef.current = value; setChildGuard(value); }, []);
  const dirtyChanged = useCallback((value: boolean) => { dirtyRef.current = value; setDirty(value); }, []);
  const guard: Guard = loading || childGuard === 'busy' ? 'busy' : dirty || childGuard === 'secret' ? 'secret' : null;

  const load = useCallback(async () => {
    if (!current()) return;
    controller.current?.abort(); const abort = new AbortController(); controller.current = abort;
    const request = ++sequence.current; loadingRef.current = true; setLoading(true); setError(''); setCatalog(null);
    try {
      const value = await connectionSetupApi.get(workspaceId, scopeChannel, abort.signal);
      if (current() && request === sequence.current && !abort.signal.aborted) setCatalog(value);
    } catch (failure) { if (current() && request === sequence.current && !abort.signal.aborted) setError(failure instanceof Error ? failure.message : 'Connection setup could not be loaded.'); }
    finally { if (current() && request === sequence.current && !abort.signal.aborted) { loadingRef.current = false; setLoading(false); } }
  }, [workspaceId, scopeChannel, current]);
  useEffect(() => {
    mounted.current = true; owner.current = { workspaceId, token: api.getAccessToken(), user: api.getCurrentUserId() };
    setRecipeId(null); setNeedsChannel(false); setChannelId(''); setStep('prepare'); dirtyRef.current = false; childGuardRef.current = null; setDirty(false); setChildGuard(null); setExpired(false);
    return () => { mounted.current = false; ++sequence.current; controller.current?.abort(); };
  }, [workspaceId]);
  useEffect(() => { void load(); return () => { ++sequence.current; controller.current?.abort(); }; }, [load]);
  useEffect(() => {
    const start = initialSelection.current;
    if (start.applied || !catalog || catalog.workspace_id !== workspaceId || !current()) return;
    start.applied = true;
    if (channels.some(row => row.id === start.channelId && row.workspace_id === workspaceId && row.role === 'admin' && row.type !== 'direct')) setChannelId(start.channelId!);
    const requested = catalog.recipes.find(row => row.id === start.recipeId);
    if (requested) {
      setRecipeId(requested.id); setNeedsChannel(requested.requires_channel);
    }
  }, [catalog, channels, current, workspaceId]);
  useEffect(() => { onGuardChange(guard); }, [guard, onGuardChange]);
  useEffect(() => () => onGuardChange(null), [onGuardChange]);
  useEffect(() => {
    const invalidate = () => { ++sequence.current; controller.current?.abort(); dirtyRef.current = false; childGuardRef.current = null; loadingRef.current = false; setCatalog(null); setLoading(false); setExpired(true); setDirty(false); setChildGuard(null); };
    window.addEventListener('freecollab:auth-changed', invalidate);
    return () => { window.removeEventListener('freecollab:auth-changed', invalidate); for (const timer of timers.current) clearTimeout(timer); for (const url of urls.current) URL.revokeObjectURL(url); };
  }, []);
  useEffect(() => { if (recipeId && !loading) heading.current?.focus({ preventScroll: true }); }, [recipeId, loading]);

  function mayLeave() {
    if (!current() || expired || loadingRef.current || childGuardRef.current === 'busy') return false;
    if (childGuardRef.current === 'secret') { setNotice(t('Store the displayed secret and confirm it is saved before changing this guide.')); return false; }
    if (dirtyRef.current && !window.confirm(t('Discard unsaved connection settings?'))) return false;
    dirtyRef.current = false; childGuardRef.current = null; setDirty(false); setChildGuard(null); setNotice(''); return true;
  }
  function choose(recipe: ConnectionRecipe | null) {
    if (recipe?.id === recipeId || !mayLeave()) return;
    setRecipeId(recipe?.id || null); setNeedsChannel(!!recipe?.requires_channel); setStep('prepare'); setError('');
  }
  function chooseChannel(value: string) { if (value !== channelId && mayLeave()) { setChannelId(value); setStep('prepare'); } }
  function chooseStep(value: Step) {
    if (value === step || !mayLeave()) return;
    setStep(value); if (value === 'status') void load();
  }
  function download(recipe: ConnectionRecipe) {
    if (!current() || expired) return;
    try {
      const file = connectionRecipeDownload(recipe), url = URL.createObjectURL(new Blob([file.json], { type: 'application/json;charset=utf-8' }));
      urls.current.add(url); const anchor = document.createElement('a'); anchor.href = url; anchor.download = file.filename; document.body.append(anchor); anchor.click(); anchor.remove();
      const timer = setTimeout(() => { URL.revokeObjectURL(url); urls.current.delete(url); timers.current.delete(timer); }, 1000); timers.current.add(timer);
      setNotice(t('Setup JSON downloaded. It contains instructions only, without account sessions or saved provider credentials.'));
    } catch (failure) { setError(failure instanceof Error ? failure.message : t('Setup JSON could not be downloaded.')); }
  }
  async function copyCallback(value: string) {
    if (!current() || expired) return;
    try { await navigator.clipboard.writeText(value); if (current()) setNotice(t('Exact callback address copied.')); }
    catch { if (current()) setNotice(t('Copy is unavailable here. Select and copy the callback address below.')); }
  }

  const scopedCatalog = catalog?.workspace_id === workspaceId && catalog.channel_id === scopeChannel ? catalog : null;
  const recipe = scopedCatalog?.recipes.find(value => value.id === recipeId), room = channels.find(value => value.id === channelId && value.workspace_id === workspaceId && value.role === 'admin');
  const manageable = channels.filter(value => value.workspace_id === workspaceId && value.role === 'admin' && value.type !== 'direct');
  const ownerOnly = recipe?.target === 'copilot' || recipe?.target === 'people';
  const canConfigure = !!recipe && recipe.enabled && recipe.can_configure && supported(recipe) && (!ownerOnly || isOwner)
    && (!recipe.requires_channel || !!room && (!room.secure || recipe.secure_supported))
    && (!['sso', 'sync', 'people'].includes(recipe.target) || !!security);
  const locked = loading || childGuard === 'busy';
  const configureKey = `${workspaceId}:${scopeChannel || 'workspace'}:${recipeId}`;
  function configuration(value: ConnectionRecipe) {
    if (!canConfigure) return null;
    const common = { onGuardChange: childGuardChanged, onDirtyChange: dirtyChanged };
    switch (value.target) {
      case 'sso': return <WorkspaceSSOSettings workspaceId={workspaceId} security={security} {...common} />;
      case 'sync': return <SyncSettings workspaceId={workspaceId} channels={room ? [room] : []} security={security} setupMode initialProvider={value.provider as 'google' | 'microsoft' | 'nextcloud' | 'caldav'} initialKind={value.kind || undefined} initialChannelId={channelId} {...common} />;
      case 'cloud_files': return <CloudFilesPicker workspaceId={workspaceId} channelId={channelId} setupMode initialProvider={value.provider as 'dropbox' | 'box'} {...common} />;
      case 'external_meetings': return <ExternalMeetingsPanel workspaceId={workspaceId} channelId={channelId} isAdmin={!!room} setupMode initialProvider={value.provider as 'google_meet' | 'microsoft_teams' | 'zoom'} {...common} />;
      case 'copilot': return <CopilotPanel workspaceId={workspaceId} channelId={channelId} isAdmin={isOwner} setupMode {...common} />;
      case 'people': return <SCIMSettings workspaceId={workspaceId} security={security} isOwner={isOwner} displayChannels={channels} {...common} />;
      case 'webhooks': return <WebhookConnections workspaceId={workspaceId} channels={room ? [room] : []} {...common} />;
    }
  }
  if (expired || owner.current.workspaceId !== workspaceId || !current()) return <section className="fc-connection-setup"><p className="fc-alert" role="alert">{t('Your session changed. Reopen connection setup to continue.')}</p></section>;
  return <div className="fc-connection-setup" aria-busy={loading}>
    <header className="fc-connection-intro"><span className="fc-kicker">{t('Your services. Your control.')}</span><h3>{t('Connections, made clear.')}</h3><p>{t('Prepare the exact settings, connect your own provider account, then check its current configuration.')}</p></header>
    {error && <div className="fc-alert fc-connection-alert" role="alert"><p>{t(error)}</p><button type="button" className="fc-button" disabled={locked} onClick={() => void load()}><RefreshCw size={15} />{t('Retry connection setup')}</button></div>}
    {notice && <p className="fc-alert" role="status">{notice}</p>}
    {loading ? <p className="fc-account-loading" role="status"><Loader2 size={18} className="fc-account-spinner" />{t('Loading connection guides…')}</p> : scopedCatalog && <>
      {!recipeId ? <>
        <label className="fc-connection-filter"><span>{t('Connection type')}</span><select className="fc-input" value={category} onChange={event => setCategory(event.target.value as ConnectionCategory | 'all')}><option value="all">{t('All connection types')}</option>{Object.entries(categories).map(([id, name]) => <option key={id} value={id}>{t(name)}</option>)}</select></label>
        <div className="fc-connection-cards">{scopedCatalog.recipes.filter(value => category === 'all' || value.category === category).map(value => { const Icon = icons[value.category]; return <button type="button" key={value.id} data-connection-recipe={value.id} className="fc-connection-card" aria-label={t('Set up {provider}', { provider: t(value.name) })} onClick={() => choose(value)}><div><Icon size={21} /><span className="fc-connection-status" data-status={value.status}>{t(statuses[value.status])}</span></div><strong>{t(value.name)}</strong><p>{t(value.description)}</p><span className="fc-connection-card-action">{t('View setup guide')}<ArrowUpRight size={15} /></span></button>; })}</div>
      </> : recipe ? <section className="fc-connection-guide" aria-label={t('Connection setup guide')}>
        <div className="fc-connection-choice"><button type="button" className="fc-account-link" disabled={locked} onClick={() => choose(null)}><ArrowLeft size={15} />{t('All connections')}</button><label><span className="fc-connection-recipe-label">{t('Connection recipe')}</span><select aria-label={t('Connection recipe')} className="fc-input" value={recipe.id} disabled={locked} onChange={event => choose(scopedCatalog.recipes.find(value => value.id === event.target.value) || null)}>{scopedCatalog.recipes.map(value => <option key={value.id} value={value.id}>{t(value.name)}</option>)}</select></label></div>
        <div className="fc-connection-title"><div><span className="fc-kicker">{t(categories[recipe.category])}</span><h3 ref={heading} tabIndex={-1}>{t(recipe.name)}</h3><p>{t(recipe.description)}</p></div><span className="fc-connection-status" data-status={recipe.status}>{t(statuses[recipe.status])}</span></div>
        {recipe.requires_channel && <label className="fc-connection-channel"><span>{t('Connection channel')}</span><select aria-label={t('Connection channel')} className="fc-input" value={channelId} disabled={locked} onChange={event => chooseChannel(event.target.value)}><option value="">{t('Choose a channel')}</option>{manageable.map(value => <option key={value.id} value={value.id}>{value.name || t('Channel')}{value.secure ? t(' · encrypted') : ''}</option>)}</select><small>{t('The grant applies only to this selected channel. Changing it starts a separate setup.')}</small></label>}
        <nav className="fc-connection-steps" aria-label={t('Connection setup steps')}>{steps.map((item, index) => <button type="button" key={item.id} data-connection-step={item.id} aria-current={step === item.id ? 'step' : undefined} disabled={locked || item.id === 'configure' && !canConfigure} onClick={() => chooseStep(item.id)}><span aria-hidden="true">{index + 1}</span>{t(item.label)}</button>)}</nav>
        {!!recipe.blockers.length && <section className="fc-connection-blockers" aria-label={t('Before you can connect')}><strong>{t('Before you can connect')}</strong><ul>{recipe.blockers.map((value, index) => <li key={index}>{t(value)}</li>)}</ul></section>}
        {ownerOnly && !isOwner && <p className="fc-alert" role="status">{t('A workspace owner must configure this service. You can still prepare and download its setup guide.')}</p>}
        {step === 'prepare' && <div className="fc-connection-prepare">
          <section className="fc-connection-section"><h4>{t('1. Prepare your provider')}</h4><ul className="fc-connection-checklist">{recipe.prerequisites.map((value, index) => <li key={index}><Check size={15} aria-hidden="true" /><span>{t(value)}</span></li>)}</ul>
            {!!recipe.fields.length && <><h5>{t('Provider application fields')}</h5><ul className="fc-connection-fields">{recipe.fields.map(field => <li key={field.key}><span>{t(field.label)}</span><small>{t(field.required ? 'Required' : 'Optional')}{field.secret ? ' · ' + t('Secret value entered only during configuration') : ''}</small></li>)}</ul></>}
            {recipe.docs_url && <a className="fc-account-link" href={recipe.docs_url} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">{t('Open provider documentation')}<ArrowUpRight size={14} /></a>}
          </section>
          <section className="fc-connection-section"><h4>{t('2. Register exact access')}</h4>{scopedCatalog.origin_status !== 'valid' && <p className="fc-account-muted">{t(scopedCatalog.origin_status === 'local_development' ? 'This is a local development address. Your provider may require the installation’s public HTTPS address.' : 'The installation’s public address is unavailable. An operator must configure it before registering a callback.')}</p>}
            {recipe.callback_url ? <><label className="fc-connection-callback"><span>{t('Exact callback address')}</span><input className="fc-input" readOnly value={recipe.callback_url} onFocus={event => event.currentTarget.select()} /></label><button type="button" className="fc-button" onClick={() => void copyCallback(recipe.callback_url!)}><Copy size={15} />{t('Copy callback')}</button></> : <p className="fc-account-muted">{t('This recipe does not provide an OAuth callback address.')}</p>}
            <h5>{t('Provider scopes')}</h5>{recipe.scopes.length ? <ul className="fc-connection-scopes">{recipe.scopes.map(value => <li key={value}><code>{value}</code></li>)}</ul> : <p className="fc-account-muted">{t('No OAuth scopes are required by this recipe.')}</p>}
          </section>
          <section className="fc-connection-section"><h4>{t('3. Follow the connection steps')}</h4><ol className="fc-connection-instructions">{recipe.steps.map((value, index) => <li key={index}>{t(value)}</li>)}</ol><button type="button" className="fc-button" onClick={() => download(recipe)}><Download size={15} />{t('Download setup JSON')}</button><p className="fc-connection-export-note">{t('The download contains provider instructions, callback and scopes. It excludes sessions, connection records and saved credentials.')}</p></section>
          <div className="fc-connection-next"><span>{canConfigure ? t('Have your provider application details ready?') : t('Resolve the requirements above to configure this service.')}</span><button type="button" className="fc-button fc-button-primary" disabled={!canConfigure} onClick={() => chooseStep('configure')}>{t('Continue to configuration')}<ArrowUpRight size={15} /></button></div>
        </div>}
        {step === 'configure' && <div className="fc-connection-configure" key={configureKey}>{canConfigure ? <><p className="fc-connection-context"><ShieldCheck size={17} /><span>{t('Use your organization’s own application and credentials. Saving, authorization, provider tests and enablement each require an explicit action.')}</span></p>{configuration(recipe)}{dirty && childGuard !== 'secret' && <button type="button" className="fc-account-link" disabled={locked} onClick={() => { if (mayLeave()) setStep('prepare'); }}>{t('Discard changes and return to preparation')}</button>}</> : <p className="fc-alert" role="status">{t('Current permissions or installation settings do not allow configuration. Refresh the status after resolving the requirements.')}</p>}</div>}
        {step === 'status' && <section className="fc-connection-section fc-connection-current"><div className="fc-connection-status-heading"><h4>{t('Current saved configuration')}</h4><button type="button" className="fc-button" disabled={locked} onClick={() => void load()}><RefreshCw size={15} />{t('Refresh connection status')}</button></div><p className="fc-connection-context"><CheckCircle2 size={17} /><span>{t('This status reflects saved configuration and current access grants. Provider connectivity is verified only by an explicit test in the configuration step.')}</span></p>
          <span className="fc-connection-status" data-status={recipe.status}>{t(statuses[recipe.status])}</span>{recipe.connections.length ? <ul className="fc-connection-records">{recipe.connections.map(value => <li key={value.id}><div><strong>{value.name}</strong><span>{t('Revision {count}', { count: value.revision })} · {t(value.enabled ? 'Enabled' : 'Paused')}</span></div><span>{t(statuses[value.status as ConnectionStatus] || value.status)}</span>{value.verified_at && <small>{t(recipe.target === 'people' ? 'Last directory request: {date}' : recipe.target === 'webhooks' ? 'Last successful delivery: {date}' : 'Last explicit verification: {date}', { date: new Date(value.verified_at).toLocaleString(locale, { dateStyle: 'medium', timeStyle: 'short' }) })}</small>}</li>)}</ul> : <p className="fc-account-muted">{t('No saved connection for this provider and scope.')}</p>}
        </section>}
      </section> : <p className="fc-alert" role="alert">{t('This connection guide is no longer available. Refresh to check your current access.')}</p>}
    </>}
    <footer className="fc-connection-privacy"><ShieldCheck size={17} /><p>{t('Preparing a guide and refreshing status contact only this installation. External provider calls and remote changes start only from an explicit action in configuration.')}</p></footer>
  </div>;
}
