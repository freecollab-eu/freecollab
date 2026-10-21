import { t, useI18n } from '../i18n';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ArrowUpRight, CalendarDays, Cloud, FolderSync, Loader2, Pause, Play, Plus, RefreshCw, ShieldCheck, Unplug, X } from 'lucide-react';
import { api, type Channel } from '../lib/api';
import { syncApi, type SyncActivity, type SyncConnection, type SyncConflict, type SyncKind, type SyncProvider } from '../lib/enterprise-api';
import type { AccountSecurity } from '../lib/account-api';
import type { SSOReauthentication } from '../lib/integrations-api';
import { matchingSyncRecipe, stableDraft, syncRecipeDraft } from '../lib/connection-presets';
import { EnterpriseAlerts, NativeConfirmation, enterpriseDate, enterpriseFailure, type IntegrationGuard } from './EnterpriseControls';

type Action = { kind: 'save' } | { kind: 'oauth' | 'test' | 'enable' | 'disable' | 'run' | 'disconnect'; connection: SyncConnection } | { kind: 'resolve'; connection: SyncConnection; conflict: SyncConflict; choice: 'local' | 'remote' };
const names: Record<SyncProvider, string> = { google: 'Google', microsoft: 'Microsoft', nextcloud: 'Nextcloud', caldav: 'CalDAV' };
const readable = (value: string) => value.replace(/[._-]/g, ' ');
function connectionStatus(value: SyncConnection) {
  if (value.conflict_count) return 'Review conflicts';
  if (value.last_error) return 'Needs attention';
  if (value.enabled) return 'Active';
  if (!value.has_credentials) return value.provider === 'google' || value.provider === 'microsoft' ? 'Connect account' : 'Credentials needed';
  return value.status === 'tested' || value.status === 'ready' ? 'Ready to enable' : 'Paused';
}
interface Props { workspaceId: string; channels: Channel[]; security: AccountSecurity | null; onGuardChange: (guard: IntegrationGuard) => void; initialConnectionId?: string | null; initialProvider?: SyncProvider; initialKind?: SyncKind; initialChannelId?: string; setupMode?: boolean; onDirtyChange?: (dirty: boolean) => void }
export function SyncSettings({ workspaceId, channels, security, onGuardChange, initialConnectionId, initialProvider, initialKind, initialChannelId, setupMode = false, onDirtyChange }: Props) {
  useI18n();
  const [connections, setConnections] = useState<SyncConnection[]>([]);
  const [enabled, setEnabled] = useState(false);
  const [callbackURL, setCallbackURL] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [activity, setActivity] = useState<SyncActivity[]>([]);
  const [conflicts, setConflicts] = useState<SyncConflict[]>([]);
  const [loading, setLoading] = useState(true);
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<SyncConnection | 'new' | null>(null);
  const [action, setAction] = useState<Action | null>(null);
  const [name, setName] = useState('');
  const [provider, setProvider] = useState<SyncProvider>('google');
  const [kind, setKind] = useState<SyncKind>('files');
  const [channelId, setChannelId] = useState('');
  const [config, setConfig] = useState<Record<string, string>>({});
  const [clientSecret, setClientSecret] = useState('');
  const [username, setUsername] = useState('');
  const [appPassword, setAppPassword] = useState('');
  const [consent, setConsent] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const owner = useRef(api.getAccessToken());
  const mounted = useRef(true);
  const generation = useRef(0);
  const detailsGeneration = useRef(0);
  const draftBaseline = useRef('');
  const appliedRecipe = useRef('');
  const currentWorkspace = useRef(workspaceId);
  const callbacks = useRef({ onGuardChange, onDirtyChange }); callbacks.current = { onGuardChange, onDirtyChange };
  const recipe = { provider: initialProvider, kind: initialProvider === 'caldav' ? 'calendar' as const : initialKind, channelId: initialChannelId };
  const current = () => mounted.current && currentWorkspace.current === workspaceId && owner.current === api.getAccessToken();
  const nativeChannels = channels.filter(channel => channel.role === 'admin' && channel.type !== 'direct' && !channel.secure && (!setupMode || !initialChannelId || channel.id === initialChannelId));
  const visibleConnections = setupMode ? connections.filter(connection => matchingSyncRecipe(connection, recipe)) : connections;
  const selected = visibleConnections.find(value => value.id === selectedId) || null;
  const draft = stableDraft({ name, provider, kind, channelId, config });
  const dirty = Boolean(action || editing && (draft !== draftBaseline.current || clientSecret || username || appPassword || consent));
  useEffect(() => { mounted.current = true; owner.current = api.getAccessToken(); currentWorkspace.current = workspaceId; appliedRecipe.current = ''; setBusy(false); setEditing(null); setAction(null); setClientSecret(''); setUsername(''); setAppPassword(''); setConsent(false); setSelectedId(null); setConnections([]); void load(); return () => { mounted.current = false; generation.current++; detailsGeneration.current++; callbacks.current.onGuardChange(null); callbacks.current.onDirtyChange?.(false); }; }, [workspaceId]);
  useEffect(() => { onGuardChange(busy ? 'busy' : null); }, [busy, onGuardChange]);
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => { const clear = () => { generation.current++; detailsGeneration.current++; setBusy(false); setEditing(null); setAction(null); setClientSecret(''); setUsername(''); setAppPassword(''); setConsent(false); setConnections([]); setActivity([]); setConflicts([]); setLoading(false); }; window.addEventListener('freecollab:auth-changed', clear); return () => window.removeEventListener('freecollab:auth-changed', clear); }, []);
  useEffect(() => {
    if (!setupMode || loading || busy || editing || action || !current()) return;
    const key = stableDraft({ workspaceId, ...recipe });
    if (appliedRecipe.current === key) return;
    if (!visibleConnections.length && (!nativeChannels.length || !security)) return;
    appliedRecipe.current = key;
    setSelectedId(visibleConnections[0]?.id || null);
    if (!visibleConnections.length && nativeChannels.length && security) edit('new');
  }, [setupMode, workspaceId, initialProvider, initialKind, initialChannelId, loading, busy, editing, action, nativeChannels.length, security, connections]);
  useEffect(() => { if (selectedId) void loadDetails(selectedId); else { detailsGeneration.current++; setActivity([]); setConflicts([]); } }, [selectedId]);
  useEffect(() => {
    const interval = window.setInterval(() => { if (!busy && !editing && !action && document.visibilityState === 'visible') { void refresh(); if (selectedId) void loadDetails(selectedId, true); } }, 15000);
    return () => window.clearInterval(interval);
  }, [workspaceId, busy, editing, action, selectedId]);
  async function refresh() {
    const token = owner.current;
    try { const result = await syncApi.list(workspaceId); if (current() && token === api.getAccessToken()) { setConnections(result.connections); setEnabled(result.enabled); setCallbackURL(result.callback_url); } }
    catch { /* The explicit refresh control reports failures without interrupting editing. */ }
  }
  async function load() {
    const request = ++generation.current; setLoading(true); setError('');
    try {
      const result = await syncApi.list(workspaceId);
      if (!current() || request !== generation.current) return;
      setConnections(result.connections); setEnabled(result.enabled); setCallbackURL(result.callback_url);
      const visible = setupMode ? result.connections.filter(connection => matchingSyncRecipe(connection, recipe)) : result.connections;
      setSelectedId(previous => visible.some(connection => connection.id === previous) ? previous : visible.some(connection => connection.id === initialConnectionId) ? initialConnectionId! : visible[0]?.id || null);
    } catch (failure) { if (current() && request === generation.current) setError(enterpriseFailure(failure)); }
    finally { if (current() && request === generation.current) setLoading(false); }
  }
  async function loadDetails(id: string, quiet = false) {
    const request = ++detailsGeneration.current; if (!quiet) setDetailsLoading(true);
    try {
      const [history, pending] = await Promise.all([syncApi.activity(workspaceId, id), syncApi.conflicts(workspaceId, id)]);
      if (!current() || request !== detailsGeneration.current) return;
      setActivity(history.events); setConflicts(pending.conflicts);
    } catch (failure) { if (current() && request === detailsGeneration.current && !quiet) setError(enterpriseFailure(failure)); }
    finally { if (current() && request === detailsGeneration.current) setDetailsLoading(false); }
  }
  function edit(value: SyncConnection | 'new') {
    if (!current()) return;
    setEditing(value); setError(''); setNotice(''); setAction(null); setConsent(false);
    const preset = syncRecipeDraft(setupMode ? recipe : {}, nativeChannels.map(channel => channel.id));
    const next = value === 'new' ? { name: setupMode ? t(preset.name) : '', provider: preset.provider, kind: preset.kind, channelId: preset.channelId, config: setupMode ? preset.config : {} } : { name: value.name, provider: value.provider, kind: value.kind, channelId: value.channel_id, config: value.config };
    draftBaseline.current = stableDraft(next);
    setName(next.name); setProvider(next.provider); setKind(next.kind); setChannelId(next.channelId); setConfig(next.config);
    setClientSecret(''); setUsername(''); setAppPassword('');
  }
  function cancelEdit() { setEditing(null); setAction(null); setClientSecret(''); setAppPassword(''); setUsername(''); setConsent(false); }
  function changeConfig(key: string, value: string) { setConfig(previous => ({ ...previous, [key]: value })); if (['client_id', 'tenant_id', 'base_url'].includes(key) && config[key] !== value) { setClientSecret(''); setUsername(''); setAppPassword(''); setConsent(false); setAction(null); } }
  function chooseProvider(value: SyncProvider) { setProvider(value); setConfig({}); setClientSecret(''); setUsername(''); setAppPassword(''); setConsent(false); if (value === 'caldav') setKind('calendar'); }
  function requestSave(event: FormEvent) { event.preventDefault(); if (!consent || !current()) return; setAction({ kind: 'save' }); setError(''); }
  async function confirm(values: SSOReauthentication) {
    if (!action || busy || !current()) return;
    const pending = action; setBusy(true); onGuardChange('busy'); setError(''); setNotice('');
    try {
      if (pending.kind === 'save') {
        if (!editing || !consent) return;
        const credentials = provider === 'google' || provider === 'microsoft' ? { ...(clientSecret ? { client_secret: clientSecret } : {}) } : { ...(username.trim() ? { username: username.trim() } : {}), ...(appPassword ? { app_password: appPassword } : {}) };
        const body = { ...values, name: name.trim(), provider, kind, channel_id: channelId, config: Object.fromEntries(Object.entries(config).map(([key, value]) => [key, value.trim()])), credentials, consent: true as const };
        const result = editing === 'new' ? await syncApi.create(workspaceId, body) : await syncApi.update(workspaceId, editing.id, body);
        if (!current()) return; setSelectedId(result.id); cancelEdit(); await load();
        if (current()) setNotice(t("Connection saved as a paused draft. Connect the provider account if needed, test it, then enable synchronization."));
      } else if (pending.kind === 'oauth') {
        const result = await syncApi.authorize(workspaceId, pending.connection.id, values);
        if (!current()) return;
        const url = new URL(result.authorization_url);
        if (url.protocol !== 'https:' || url.username || url.password) throw new Error(t("The provider returned an unsupported authorization address."));
        setClientSecret(''); setAppPassword(''); window.location.assign(url.href);
      } else if (pending.kind === 'resolve') {
        await syncApi.resolve(workspaceId, pending.connection.id, pending.conflict.id, { ...values, choice: pending.choice });
        if (!current()) return; await loadDetails(pending.connection.id); await refresh();
        if (current()) setNotice(t("Conflict resolution queued. Check activity to confirm the selected version has synchronized."));
      } else {
        await syncApi.action(workspaceId, pending.connection.id, pending.kind, values);
        if (!current()) return; await load(); await loadDetails(pending.connection.id);
        if (current()) setNotice({ test: 'Connection test completed. You can enable this saved configuration for one hour.', enable: 'Synchronization enabled for the selected channel and provider resource.', disable: 'Synchronization paused. Existing local and remote copies remain.', run: 'Synchronization queued. Check activity for its result; queued is separate from completed.', disconnect: 'Connection paused and locally stored provider credentials removed. Existing provider copies and upstream authorizations are unchanged.' }[pending.kind]);
      }
      if (current()) setAction(null);
    } catch (failure) { if (current()) setError(enterpriseFailure(failure)); }
    finally { if (current()) { setBusy(false); onGuardChange(null); } }
  }
  function actionTitle(value: Action) {
    return value.kind === 'save' ? 'Confirm cloud synchronization' : value.kind === 'oauth' ? 'Connect your provider account?' : value.kind === 'test' ? 'Test this saved connection?' : value.kind === 'enable' ? 'Enable two-way synchronization?' : value.kind === 'disable' ? 'Pause synchronization?' : value.kind === 'run' ? 'Run synchronization now?' : value.kind === 'disconnect' ? 'Disconnect this provider?' : 'Resolve this synchronization conflict?';
  }
  const oauthProvider = provider === 'google' || provider === 'microsoft';
  const sameSavedApp = editing && editing !== 'new' && provider === editing.provider && config.client_id === editing.config.client_id && config.tenant_id === editing.config.tenant_id;
  const newDavGrant = editing === 'new' || !!editing && (channelId !== editing.channel_id || config.base_url !== editing.config.base_url);
  return <div className="fc-enterprise-sync"><section className="fc-account-section"><div className="fc-account-section-heading"><div><span className="fc-kicker">{t("Useful connections, explicit access")}</span><h3>{t("Files and calendar sync")}</h3></div><button type="button" className="fc-icon-button" aria-label={t("Refresh cloud synchronization")} disabled={loading || busy} onClick={() => void load()}><RefreshCw size={16} /></button></div><p className="fc-account-muted">{t("Keep a selected channel’s files or calendar in sync with your organization’s provider. You control the account, remote folder or calendar, and the channel it can access.")}</p><EnterpriseAlerts error={error} notice={notice} />
      <div className="fc-integration-privacy"><ShieldCheck size={19} /><p>{t("Background sync supports channels with server-side encryption. End-to-end encrypted channels keep their content on trusted devices and cannot use this server sync. Their document links and explicit calendar export remain available.")}</p></div>
      {loading ? <p className="fc-account-loading" role="status"><Loader2 size={18} className="fc-account-spinner" />{t("Loading cloud connections…")}</p> : <>
        {!enabled && <p className="fc-alert" role="status">{t("Background synchronization is disabled on this installation. You can prepare a connection; an installation operator must enable its worker before it can run.")}</p>}
        <ul className="fc-integration-hook-list">{visibleConnections.map(connection => <li key={connection.id}><button type="button" className="fc-integration-hook-select" aria-pressed={selectedId === connection.id} disabled={busy || !!editing} onClick={() => { setSelectedId(connection.id); setAction(null); setError(''); }}>{connection.kind === 'files' ? <FolderSync size={20} /> : <CalendarDays size={20} />}<span><strong>{connection.name}</strong><small>{names[connection.provider]} · {connection.kind === 'files' ? t("Files") : t("Calendar")} · {channels.find(channel => channel.id === connection.channel_id)?.name || t("Channel")}</small></span><span className="fc-badge">{connectionStatus(connection)}</span></button></li>)}</ul>
        {!editing && <button type="button" className="fc-button fc-button-primary" disabled={busy || !nativeChannels.length || !security} onClick={() => edit('new')}><Plus size={15} />{t("Add cloud connection")}</button>}
        {!nativeChannels.length && <p className="fc-account-muted">{t("Choose a non-direct channel you administer with server-side encryption. No encrypted channel content is decrypted for background synchronization.")}</p>}
      </>}
    </section>
    {editing && <section className="fc-account-section"><div className="fc-account-section-heading"><h3>{editing === 'new' ? t("New cloud connection") : t("Edit cloud connection")}</h3><button type="button" className="fc-icon-button" disabled={busy} aria-label={t("Cancel cloud connection editing")} onClick={cancelEdit}><X size={16} /></button></div><form className="fc-account-form" aria-label={t("Cloud synchronization configuration")} onSubmit={requestSave}>
      {setupMode && <p className="fc-account-muted">{t("This recipe fills safe defaults only. Supply your own provider application and selected resource. Nothing is saved, authorized or tested automatically.")}</p>}<label htmlFor="sync-name">{t("Connection name")}</label><input id="sync-name" className="fc-input" value={name} onChange={event => setName(event.target.value)} maxLength={80} required disabled={busy} placeholder={t("Studio project files")} />
      <div className="fc-enterprise-form-grid"><div><label htmlFor="sync-provider">{t("Provider")}</label><select id="sync-provider" className="fc-input" value={provider} onChange={event => chooseProvider(event.target.value as SyncProvider)} disabled={busy || editing !== 'new' || setupMode && !!initialProvider}><option value="google">{t("Google Drive / Calendar")}</option><option value="microsoft">{t("Microsoft OneDrive / SharePoint / Outlook")}</option><option value="nextcloud">{t("Nextcloud")}</option><option value="caldav">{t("CalDAV")}</option></select></div><div><label htmlFor="sync-kind">{t("What to synchronize")}</label><select id="sync-kind" className="fc-input" value={kind} onChange={event => setKind(event.target.value as SyncKind)} disabled={busy || editing !== 'new' || provider === 'caldav' || setupMode && !!initialKind}><option value="files">{t("Files")}</option><option value="calendar">{t("Calendar")}</option></select></div></div>
      <label htmlFor="sync-channel">{t("Channel granted to this connection")}</label><select id="sync-channel" className="fc-input" value={channelId} onChange={event => setChannelId(event.target.value)} required disabled={busy || setupMode && !!initialChannelId}><option value="">{t("Choose a channel")}</option>{nativeChannels.map(channel => <option key={channel.id} value={channel.id}>{channel.name || t("Channel")}</option>)}</select>
      {oauthProvider ? <>
        <label htmlFor="sync-callback">{t("OAuth callback URI")}</label><input id="sync-callback" className="fc-input" value={callbackURL} readOnly /><p className="fc-account-muted">{t("Register this exact redirect URI in your own provider application. Cloud synchronization uses its own application permissions, separate from SSO.")}</p>
        {setupMode && <p className="fc-account-muted">{t("Required: your OAuth client ID and the selected folder or calendar ID. Microsoft also requires your directory tenant ID and, for files, drive ID. Add the app secret if your client requires one.")}</p>}<label htmlFor="sync-client-id">{t("OAuth client ID")}</label><input id="sync-client-id" className="fc-input" value={config.client_id || ''} onChange={event => changeConfig('client_id', event.target.value)} autoComplete="off" required disabled={busy} />
        <label htmlFor="sync-client-secret">{t("OAuth client secret")}</label><input id="sync-client-secret" className="fc-input" type="password" value={clientSecret} onChange={event => setClientSecret(event.target.value)} autoComplete="new-password" disabled={busy} placeholder={sameSavedApp ? t("Leave blank to retain the saved secret") : t("Your application’s client secret")} /><p className="fc-account-muted">{t("Saved credentials are encrypted and never displayed. Disconnect the connection to erase its locally stored credentials.")}</p>
        {provider === 'microsoft' && <><label htmlFor="sync-tenant-id">{t("Microsoft directory tenant ID")}</label><input id="sync-tenant-id" className="fc-input" value={config.tenant_id || ''} onChange={event => changeConfig('tenant_id', event.target.value)} required disabled={busy} placeholder={t("Your organization’s tenant ID")} /></>}
        {kind === 'files' ? <>{provider === 'microsoft' && <><label htmlFor="sync-drive-id">{t("Drive ID")}</label><input id="sync-drive-id" className="fc-input" value={config.drive_id || ''} onChange={event => changeConfig('drive_id', event.target.value)} required disabled={busy} /></>}<label htmlFor="sync-folder-id">{t("Remote folder ID")}</label><input id="sync-folder-id" className="fc-input" value={config.folder_id || ''} onChange={event => changeConfig('folder_id', event.target.value)} required disabled={busy} /><p className="fc-account-muted">{t("Only the selected folder is granted. Google Docs exports may be read-only; review conflicts before replacing either copy.")}</p></> : <>{setupMode && provider === 'google' && <p className="fc-account-muted">{t("Google calendar defaults to primary. Replace it with a specific calendar ID if needed before consenting.")}</p>}<label htmlFor="sync-calendar-id">{t("Remote calendar ID")}</label><input id="sync-calendar-id" className="fc-input" value={config.calendar_id || ''} onChange={event => changeConfig('calendar_id', event.target.value)} required disabled={busy} /></>}
      </> : <>
        {setupMode && <p className="fc-account-muted">{t("Required: your exact collection URL, provider username and an app-specific password. The example address is a hint, not a saved destination.")}</p>}<label htmlFor="sync-base-url">{kind === 'files' ? t("WebDAV folder URL") : t("CalDAV calendar collection URL")}</label><input id="sync-base-url" className="fc-input" type="url" value={config.base_url || ''} onChange={event => changeConfig('base_url', event.target.value)} required disabled={busy} placeholder={kind === 'files' ? 'https://cloud.example.org/remote.php/dav/files/you/project/' : 'https://cloud.example.org/remote.php/dav/calendars/you/team/'} /><p className="fc-account-muted">{t("Use the exact public HTTPS collection URL on port 443. Private-network destinations are refused.")}</p>
        <label htmlFor="sync-username">{t("Provider username")}</label><input id="sync-username" className="fc-input" value={username} onChange={event => { setUsername(event.target.value); setAppPassword(''); setConsent(false); }} autoComplete="off" disabled={busy} required={newDavGrant} placeholder={newDavGrant ? t("Your provider username") : t("Leave blank to retain the saved username")} />
        <label htmlFor="sync-app-password">{t("Provider app password")}</label><input id="sync-app-password" className="fc-input" type="password" value={appPassword} onChange={event => setAppPassword(event.target.value)} autoComplete="new-password" disabled={busy} required={newDavGrant || Boolean(username)} placeholder={newDavGrant || username ? t("An app-specific password") : t("Leave blank to retain the saved credential")} /><p className="fc-account-muted">{t("Create an app-specific credential at your provider. It is stored encrypted and is never returned here.")}</p>
      </>}
      <label className="fc-account-checkbox fc-enterprise-consent"><input type="checkbox" checked={consent} onChange={event => setConsent(event.target.checked)} disabled={busy} required />{t("I authorize sharing existing and future channel ")}{kind === 'files' ? t("files") : t("events")} {t(" with this provider resource. Changes and deletions can synchronize in both directions.")}</label>
      <p className="fc-account-muted">{t("Saving pauses the connection until it is tested and enabled again. Historical local versions are retained; remote copies remain subject to the provider’s permissions and retention.")}</p>
      <div className="fc-account-actions"><button type="button" className="fc-button" disabled={busy} onClick={cancelEdit}>{t("Cancel")}</button><button type="submit" className="fc-button fc-button-primary" disabled={busy || !consent || !security}>{t("Save paused connection")}</button></div>
    </form></section>}
    {selected && !editing && <section className="fc-account-section" aria-label={t("Cloud connection details")}><div className="fc-account-section-heading"><div><span className="fc-kicker">{t("Connection details")}</span><h3>{selected.name}</h3></div><Cloud size={21} /></div><p className="fc-account-muted">{names[selected.provider]} · {selected.kind === 'files' ? t("Two-way file sync") : t("Two-way calendar sync")} · {channels.find(channel => channel.id === selected.channel_id)?.name || t("Channel")}</p><dl className="fc-integration-status-grid"><div><dt>{t("Status")}</dt><dd>{connectionStatus(selected)}</dd></div><div><dt>{t("Last completed sync")}</dt><dd>{enterpriseDate(selected.last_synced_at)}</dd></div><div><dt>{t("Next scheduled sync")}</dt><dd>{enterpriseDate(selected.next_run_at)}</dd></div><div><dt>{t("Conflicts to review")}</dt><dd>{selected.conflict_count}</dd></div></dl>
      {selected.last_error && <p className="fc-alert" role="status">{t("The last synchronization did not finish. Check the provider connection, review activity and retry when it is available.")}</p>}
      <div className="fc-integration-action-grid"><button type="button" className="fc-button" disabled={busy || !security} onClick={() => edit(selected)}>{t("Edit connection")}</button>{(selected.provider === 'google' || selected.provider === 'microsoft') && <button type="button" className="fc-button" disabled={busy || !security} onClick={() => setAction({ kind: 'oauth', connection: selected })}><ArrowUpRight size={15} />{selected.has_credentials ? t("Reconnect provider") : t("Connect provider account")}</button>}<button type="button" className="fc-button" disabled={busy || !security || !selected.has_credentials} onClick={() => setAction({ kind: 'test', connection: selected })}>{t("Test connection")}</button><button type="button" className="fc-button" disabled={busy || !security || !selected.has_credentials || !enabled || !selected.enabled && !selected.tested} onClick={() => setAction({ kind: selected.enabled ? 'disable' : 'enable', connection: selected })}>{selected.enabled ? <Pause size={14} /> : <Play size={14} />}{selected.enabled ? t("Pause sync") : t("Enable sync")}</button><button type="button" className="fc-button" disabled={busy || !security || !enabled || !selected.enabled} onClick={() => setAction({ kind: 'run', connection: selected })}><RefreshCw size={14} />{t("Sync now")}</button><button type="button" className="fc-account-link fc-account-danger" disabled={busy || !security} onClick={() => setAction({ kind: 'disconnect', connection: selected })}><Unplug size={14} />{t("Disconnect")}</button></div>
      <div className="fc-account-section-heading fc-integration-delivery-heading"><h3>{t("Conflicts")}</h3><button type="button" className="fc-icon-button" aria-label={t("Refresh sync activity and conflicts")} disabled={busy || detailsLoading} onClick={() => void loadDetails(selected.id)}><RefreshCw size={16} /></button></div><p className="fc-account-muted">{t("If both copies changed, synchronization pauses that item rather than silently replacing either version.")}</p>
      {conflicts.length ? <ul className="fc-enterprise-conflicts">{conflicts.map(conflict => <li key={conflict.id}><strong>{t("Two versions need a decision")}</strong><p>{enterpriseDate(conflict.created_at)}{conflict.read_only ? t(" · Provider export is read-only") : ''}</p><div className="fc-account-actions"><button type="button" className="fc-button" disabled={busy || !security || conflict.read_only} onClick={() => setAction({ kind: 'resolve', connection: selected, conflict, choice: 'local' })}>{t("Use channel version")}</button><button type="button" className="fc-button" disabled={busy || !security} onClick={() => setAction({ kind: 'resolve', connection: selected, conflict, choice: 'remote' })}>{t("Use provider version")}</button></div></li>)}</ul> : <p className="fc-account-muted">{t("No unresolved conflicts.")}</p>}
      <h3 className="fc-integration-delivery-heading">{t("Sync activity")}</h3>{detailsLoading ? <p className="fc-account-loading" role="status"><Loader2 size={17} className="fc-account-spinner" />{t("Loading activity…")}</p> : activity.length ? <ul className="fc-integration-deliveries">{activity.map(event => <li key={event.id}><div><strong>{readable(event.event)}</strong><span>{enterpriseDate(event.created_at)}</span></div><span className="fc-badge">{readable(event.status)}</span></li>)}</ul> : <p className="fc-account-muted">{t("No synchronization activity yet.")}</p>}
    </section>}
    {action && <NativeConfirmation key={action.kind + ('connection' in action ? action.connection.id : '')} id="sync-confirm" title={actionTitle(action)} mfa={!!security?.mfa_enabled} busy={busy} onCancel={() => setAction(null)} onConfirm={confirm}>{action.kind === 'save' || action.kind === 'enable' ? <p>{t("Existing and future channel ")}{action.kind === 'save' ? kind === 'files' ? t("files") : t("events") : action.connection.kind === 'files' ? t("files") : t("events")} {t(" can be shared with this provider. The server can read content in this channel’s server-encryption mode.")}</p> : action.kind === 'resolve' ? <p>{t("The ")}{action.choice === 'local' ? t("channel") : t("provider")} {t(" version will be used for the next synchronization. Review this choice before replacing the other copy.")}</p> : action.kind === 'disconnect' ? <p>{t("This removes credentials stored by FreeCollab and pauses synchronization. Revoke the application at your provider if you also want to remove its upstream authorization.")}</p> : undefined}</NativeConfirmation>}
  </div>;
}
