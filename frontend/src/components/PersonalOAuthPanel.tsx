import { useEffect, useRef, useState } from 'react';
import { ArrowUpRight, CheckCircle2, KeyRound, RefreshCw, ShieldCheck, Trash2 } from 'lucide-react';
import { api } from '../lib/api';
import { appPlatformApi, type InstalledApp } from '../lib/app-platform-api';
import { personalOAuthApi, providerAuthorizationUrl, providerConsentNeedsBrowser, type OAuthProvider, type OAuthPurpose, type PersonalOAuthOverview } from '../lib/personal-oauth-api';
import { t, useI18n } from '../i18n';
import '../styles/personal-oauth.css';

export function PersonalOAuthPanel({ workspaceId, installations }: { workspaceId: string; installations?: InstalledApp[] }) {
  useI18n();
  const [overview, setOverview] = useState<PersonalOAuthOverview | null>(null), [apps, setApps] = useState<InstalledApp[]>(installations || []);
  const [provider, setProvider] = useState<OAuthProvider>('google'), [purpose, setPurpose] = useState<OAuthPurpose>('search');
  const [name, setName] = useState(''), [folder, setFolder] = useState(''), [drive, setDrive] = useState(''), [app, setApp] = useState(''), [consent, setConsent] = useState(false);
  const [clientId, setClientId] = useState(''), [tenant, setTenant] = useState(''), [secret, setSecret] = useState(''), [password, setPassword] = useState(''), [code, setCode] = useState('');
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState(''), [revoking, setRevoking] = useState('');
  const generation = useRef(0), mounted = useRef(true), owner = useRef(api.getAccessToken()), identity = useRef(workspaceId);
  const current = (version: number) => mounted.current && generation.current === version && owner.current === api.getAccessToken() && identity.current === workspaceId;
  useEffect(() => {
    mounted.current = true; owner.current = api.getAccessToken(); identity.current = workspaceId;
    setOverview(null); setApps(installations || []); setError(''); setNotice('');
    setSecret(''); setPassword(''); setCode(''); setName(''); setFolder(''); setDrive(''); setConsent(false); setApp('');
    void load();
    return () => { mounted.current = false; generation.current++; };
  }, [workspaceId]);
  useEffect(() => {
    const changed = () => {
      if (owner.current === api.getAccessToken()) return;
      generation.current++; owner.current = api.getAccessToken();
      setSecret(''); setPassword(''); setCode(''); setOverview(null); setApps([]); setError(''); setNotice('');
      setConsent(false); setName(''); setFolder(''); setDrive(''); setApp(''); setBusy(false); setRevoking('');
      if (owner.current) void load();
    };
    window.addEventListener('freecollab:auth-changed', changed); window.addEventListener('storage', changed);
    return () => { window.removeEventListener('freecollab:auth-changed', changed); window.removeEventListener('storage', changed); };
  }, [workspaceId]);
  const registration = overview?.clients.find(row => row.provider === provider && row.purpose === purpose);
  const needsBrowser = providerConsentNeedsBrowser({ desktop: window.freecollabNative?.kind === 'desktop',
    mobile: Boolean(window.Capacitor?.isNativePlatform?.()), embedded: window.self !== window.top });
  useEffect(() => { setClientId(registration?.client_id || ''); setTenant(registration?.tenant_id || ''); setSecret(''); setPassword(''); setCode(''); setConsent(false); setApp(''); }, [provider, purpose, registration?.revision]);
  async function load() {
    const version = ++generation.current;
    const result = await Promise.allSettled([personalOAuthApi.overview(workspaceId), installations ? Promise.resolve({ installations }) : appPlatformApi.list(workspaceId)]);
    if (!current(version)) return;
    if (result[0].status === 'fulfilled') setOverview(result[0].value); else setError(result[0].reason instanceof Error ? result[0].reason.message : t('Provider accounts could not be loaded.'));
    if (result[1].status === 'fulfilled') setApps(result[1].value.installations);
  }
  async function action(run: () => Promise<void>) {
    if (busy) return;
    const version = generation.current; setBusy(true); setError(''); setNotice('');
    try { await run(); } catch (failure) { if (current(version)) setError(failure instanceof Error ? failure.message : t('The provider operation failed.')); }
    finally { if (mounted.current && owner.current === api.getAccessToken()) setBusy(false); }
  }
  async function configure() {
    const version = generation.current, clientSecret = secret, reauth = password, factor = code;
    setSecret(''); setPassword(''); setCode('');
    await personalOAuthApi.configure(workspaceId, provider, purpose, { client_id: clientId, tenant_id: tenant, client_secret: clientSecret,
      password: reauth, ...(factor ? { code: factor } : {}), expected_revision: registration?.revision || 0, enabled: true });
    if (current(version)) { window.dispatchEvent(new CustomEvent('freecollab:provider-accounts-changed', { detail: { workspaceId } })); setNotice(t('Customer application saved. Existing consents for this purpose require a fresh connection.')); await load(); }
  }
  async function disable() {
    const version = generation.current, reauth = password, factor = code;
    setSecret(''); setPassword(''); setCode('');
    await personalOAuthApi.disable(workspaceId, provider, purpose, { password: reauth, ...(factor ? { code: factor } : {}) });
    if (current(version)) { window.dispatchEvent(new CustomEvent('freecollab:provider-accounts-changed', { detail: { workspaceId } })); setNotice(t('Customer application disabled and its local provider credentials erased.')); await load(); }
  }
  async function copyBrowserAddress() {
    const version = generation.current;
    try {
      if (!navigator.clipboard) throw new Error();
      await navigator.clipboard.writeText(window.location.origin + '/');
      if (current(version)) setNotice(t('Browser address copied.'));
    } catch { if (current(version)) setError(t('Select and copy the browser address above.')); }
  }
  async function start() {
    if (needsBrowser) throw new Error(t('Open this installation directly in your system browser to connect a provider account.'));
    const version = generation.current;
    const target = apps.find(row => row.id === app);
    const config: Record<string, string> = purpose === 'search' ? provider === 'google' ? { folder_id: folder } : { folder_id: folder, drive_id: drive } : {};
    const result = await personalOAuthApi.start(workspaceId, provider, { purpose, name, config, consent,
      ...(purpose === 'calendar-action' && target ? { installation_id: target.id, installation_generation: target.generation } : {}) });
    if (current(version)) window.location.assign(providerAuthorizationUrl(result.authorization_url, provider));
  }
  const availableApps = apps.filter(row => row.enabled && !row.revoked_at && row.manifest.commands.length > 0 && row.manifest.commands.every(command => command.protocol === provider + '.calendar-event'));
  const providerName = provider === 'google' ? 'Google Workspace' : 'Microsoft 365';
  return <section className="fc-personal-oauth" aria-label={t('Provider accounts')}>
    <header><ShieldCheck size={26} aria-hidden="true" /><div><h3>{t('Your account. Your organization’s application.')}</h3><p>{t('Connect through your own Google or Microsoft consent screen. Search and write actions have separate scopes. FreeCollab supplies no shared identity provider.')}</p></div></header>
    {error && <p className="fc-alert" role="alert">{error}</p>}{notice && <p className="fc-alert" role="status"><CheckCircle2 size={16} />{notice}</p>}
    <div className="fc-oauth-selectors"><label>{t('Provider')}<select aria-label={t('Provider')} value={provider} onChange={event => setProvider(event.target.value as OAuthProvider)} disabled={busy}><option value="google">Google Workspace</option><option value="microsoft">Microsoft 365</option></select></label><label>{t('Delegation purpose')}<select aria-label={t('Delegation purpose')} value={purpose} onChange={event => setPurpose(event.target.value as OAuthPurpose)} disabled={busy}><option value="search">{t('Personal search · read only')}</option>{overview?.purposes.includes('calendar-action') && <option value="calendar-action">{t('Reviewed calendar actions · write')}</option>}</select></label></div>
    <p className="fc-oauth-scope"><span>{t('Exact provider scope')}</span><code>{registration?.scope || (provider === 'google' ? purpose === 'search' ? 'https://www.googleapis.com/auth/drive.readonly' : 'https://www.googleapis.com/auth/calendar.events' : purpose === 'search' ? 'Files.Read.All' : 'Calendars.ReadWrite')}</code></p>
    {overview?.can_manage && <p>{t('Use separate customer client applications for read-only search and calendar write actions, so their provider consent stays separate.')}</p>}
    {needsBrowser && <aside className="fc-oauth-browser-guidance" role="status"><strong>{t('Connect provider accounts in your system browser')}</strong><p>{t('Open this installation directly in your browser, sign in and choose Integrations → Provider accounts. Your personal delegations are available in this app while that browser sign-in remains valid. Provider consent cannot complete inside this app or an embedded frame.')}</p><label>{t('Browser address')}<input readOnly value={window.location.origin + '/'} onFocus={event => event.target.select()} /></label><button type="button" className="fc-button" onClick={() => void copyBrowserAddress()}>{t('Copy browser address')}</button></aside>}
    {overview?.can_manage && <details className="fc-oauth-registration"><summary><KeyRound size={16} />{t('Customer application registration')}</summary><p>{t('Create an OAuth web application in your organization’s Google Cloud or Microsoft Entra tenant. Add this exact redirect URI; enter its secret only here. Replacing the application invalidates its existing grants.')}</p><label>{t('Exact redirect URI')}<input readOnly value={overview.callback_url} onFocus={event => event.target.select()} /></label><form onSubmit={event => { event.preventDefault(); void action(configure); }}><fieldset disabled={busy}><label>{t('Application client ID')}<input required maxLength={1024} value={clientId} onChange={event => setClientId(event.target.value)} autoComplete="off" /></label>{provider === 'microsoft' && <label>{t('Microsoft tenant UUID')}<input required maxLength={100} value={tenant} onChange={event => setTenant(event.target.value)} /></label>}<label>{t('Application client secret')}<input required type="password" maxLength={4096} autoComplete="new-password" value={secret} onChange={event => setSecret(event.target.value)} /></label><label>{t('Your password')}<input required type="password" autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)} /></label><label>{t('Authenticator or recovery code, if enabled')}<input maxLength={64} autoComplete="one-time-code" value={code} onChange={event => setCode(event.target.value)} /></label><button className="fc-button" type="submit">{t('Save customer application')}</button>{registration?.enabled && <button type="button" className="fc-button" disabled={!password} onClick={() => void action(disable)}>{t('Disable customer application')}</button>}</fieldset></form></details>}
    <form className="fc-oauth-connect" onSubmit={event => { event.preventDefault(); void action(start); }}><h4>{t('Connect your personal provider account')}</h4><p>{t('The delegation belongs to you. Tokens stay encrypted on this installation, renew automatically while the original sign-in remains valid, and are erased when disconnected. Signing out requires new consent.')}</p><fieldset disabled={busy || !registration?.enabled || needsBrowser}><label>{t('Connection name')}<input required maxLength={80} value={name} onChange={event => setName(event.target.value)} /></label>{purpose === 'search' ? <><label>{t('Folder ID')}<input required maxLength={1024} value={folder} onChange={event => setFolder(event.target.value)} /></label>{provider === 'microsoft' && <label>{t('Drive ID')}<input required maxLength={1024} value={drive} onChange={event => setDrive(event.target.value)} /></label>}</> : <label>{t('Installed calendar app')}<select required value={app} onChange={event => { setApp(event.target.value); setConsent(false); }}><option value="">{t('Choose an installed app')}</option>{availableApps.map(row => <option key={row.id} value={row.id}>{row.manifest.name} · {t('Generation')} {row.generation}</option>)}</select></label>}<label className="fc-oauth-check"><input type="checkbox" required checked={consent} onChange={event => setConsent(event.target.checked)} />{purpose === 'search' ? t('I grant personal read-only search within the selected folder. My query is sent only when I explicitly search this provider.') : t('I grant this installed app permission to create explicitly reviewed events in my own provider calendar. Channel history is never sent.')}</label><button type="submit" className="fc-button fc-button-primary" disabled={!consent || purpose === 'calendar-action' && !app}><ArrowUpRight size={16} />{t('Continue to {provider}', { provider: providerName })}</button></fieldset>{!registration?.enabled && <p role="status">{t('A workspace administrator must configure the customer application for this provider and purpose first.')}</p>}{purpose === 'calendar-action' && registration?.enabled && !availableApps.length && <p>{t('Install the Google Calendar or Microsoft Calendar recipe in Apps before connecting its personal account.')}</p>}</form>
    <section className="fc-oauth-grants"><div><h4>{t('Your provider delegations')}</h4><button type="button" className="fc-button" disabled={busy} onClick={() => void action(load)}><RefreshCw size={14} />{t('Refresh')}</button></div>{overview?.grants.length === 0 && <p>{t('No personal OAuth delegations yet.')}</p>}{overview?.grants.map(grant => <article key={grant.id}><div><strong>{grant.name}</strong><small>{grant.provider} · {grant.purpose === 'search' ? t('Personal search · read only') : t('Reviewed calendar actions · write')}</small><span className={grant.connected ? 'fc-badge' : ''}>{grant.connected ? t('Connected') : t(grant.status)}</span></div>{grant.status !== 'revoked' && <button type="button" className="fc-button" disabled={busy} onClick={() => setRevoking(grant.id)}><Trash2 size={14} />{t('Disconnect')}</button>}{revoking === grant.id && <div className="fc-oauth-revoke"><p>{t('Disconnect erases this installation’s tokens immediately. Google revocation is also requested. Microsoft consent can additionally be removed in your organization’s account portal.')}</p><button type="button" className="fc-button" disabled={busy} onClick={() => void action(async () => { const version = generation.current; await personalOAuthApi.revoke(workspaceId, grant.id); if (current(version)) { window.dispatchEvent(new CustomEvent('freecollab:provider-accounts-changed', { detail: { workspaceId } })); setRevoking(''); setNotice(t('Personal delegation disconnected and local credentials erased.')); await load(); } })}>{t('Confirm disconnect')}</button><button type="button" className="fc-button" onClick={() => setRevoking('')}>{t('Cancel')}</button></div>}</article>)}</section>
  </section>;
}
