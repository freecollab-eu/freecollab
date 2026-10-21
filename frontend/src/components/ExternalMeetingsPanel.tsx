import { useEffect, useRef, useState, type FormEvent } from 'react';
import { CalendarPlus, Copy, ExternalLink, Loader2, RefreshCw, ShieldCheck, Unplug, Video, X } from 'lucide-react';
import { useI18n } from '../i18n';
import { api } from '../lib/api';
import { calendarWallInput, zonedInstant } from '../lib/calendar';
import { attachEncryptedMeetingCalendar, externalMeetingsApi, providerNames, removeEncryptedMeetingCalendar, validateProviderNavigation, type CalendarSource, type ExternalMeeting, type ExternalMeetingConnection, type ExternalMeetingList, type ExternalMeetingProvider, type MeetingFields, type ProviderDraft } from '../lib/external-meetings-api';
import { customerAppConsoles, freshMeetingApplication, meetingSetupCallback, useSetupFormSignals, type SetupFormSignals } from '../lib/connection-form-setup';
import '../styles/external-meetings.css';

interface Props extends SetupFormSignals { workspaceId: string; channelId: string; isAdmin?: boolean; onCalendarChanged?: () => void; initialProvider?: ExternalMeetingProvider; setupMode?: boolean }
const describe = (error: unknown) => error instanceof Error ? error.message : 'The external meeting action could not be completed.';
const initialFields = () => {
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  return { title: '', starts_at: calendarWallInput(new Date(Date.now() + 3600000).toISOString(), timezone), ends_at: calendarWallInput(new Date(Date.now() + 7200000).toISOString(), timezone), timezone };
};

export function ExternalMeetingsPanel(props: Props) {
  return <ExternalMeetingsPanelContent key={[props.workspaceId, props.channelId, !!props.isAdmin, props.initialProvider || '', !!props.setupMode].join(':')} {...props} />;
}
function ExternalMeetingsPanelContent({ workspaceId, channelId, isAdmin = false, onCalendarChanged, initialProvider, setupMode = false, onGuardChange, onDirtyChange }: Props) {
  const { t, locale } = useI18n();
  const freshApplication = () => freshMeetingApplication(initialProvider, !!initialProvider);
  const baseline = useRef(JSON.stringify(freshApplication()));
  const [data, setData] = useState<ExternalMeetingList>({ enabled: false, connections: [], meetings: [] });
  const [adminRows, setAdminRows] = useState<ExternalMeetingConnection[]>([]), [sources, setSources] = useState<CalendarSource[]>([]);
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(''), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [fields, setFields] = useState(initialFields), [selected, setSelected] = useState(''), [disclosure, setDisclosure] = useState(false), [actionDisclosure, setActionDisclosure] = useState(false);
  const [configure, setConfigure] = useState(!!initialProvider), [provider, setProvider] = useState<ExternalMeetingProvider>(initialProvider || 'google_meet'), [name, setName] = useState(freshApplication().name), [source, setSource] = useState('');
  const [clientId, setClientId] = useState(''), [clientSecret, setClientSecret] = useState(''), [calendarId, setCalendarId] = useState(freshApplication().calendarId), [tenantId, setTenantId] = useState('');
  const [password, setPassword] = useState(''), [code, setCode] = useState(''), [configDisclosure, setConfigDisclosure] = useState(false), [cancelId, setCancelId] = useState(''), [editing, setEditing] = useState<ExternalMeetingConnection | null>(null);
  const [callback, setCallback] = useState(''), [callbackLoading, setCallbackLoading] = useState(false);
  const draft = JSON.stringify({ name, provider, clientId, clientSecret: '', calendarId, tenantId, source, editingId: editing?.id || '' });
  const dirty = !!password || !!code || !!clientSecret || configDisclosure || (configure && draft !== baseline.current) || (!setupMode && (!!fields.title || disclosure || actionDisclosure));
  useSetupFormSignals(!!busy || loading || callbackLoading, dirty, { onGuardChange, onDirtyChange });
  const visibleAdminRows = adminRows.filter(row => !setupMode || !initialProvider || row.provider === initialProvider);
  const generation = useRef(0), owner = useRef(api.getAccessToken()), pending = useRef<{ id: string; hash: string } | null>(null);
  const oauthResult = useRef(new URLSearchParams(location.hash.slice(1)));
  const current = (version: number) => version === generation.current && owner.current === api.getAccessToken();
  const check = (version: number) => { if (!current(version)) throw new Error('The signed-in account changed.'); };
  function clearPrivate() { setPassword(''); setCode(''); setClientSecret(''); }
  function resetApplication(next: ExternalMeetingProvider = initialProvider || 'google_meet', preset = !!initialProvider) {
    const value = freshMeetingApplication(next, preset); baseline.current = JSON.stringify(value);
    setProvider(next); setName(value.name); setClientId(''); setCalendarId(value.calendarId); setTenantId(''); setSource(''); setEditing(null); setConfigDisclosure(false); setCallback(''); clearPrivate();
  }
  async function load(version = generation.current) {
    setLoading(true);
    try {
      const result = await externalMeetingsApi.list(channelId); if (!current(version)) return; setData(previous => ({ ...result, meetings: result.meetings.map(row => { const remembered = previous.meetings.find(item => item.id === row.id); return row.secure && row.state !== 'cancelled' && remembered?.join_url ? { ...row, join_url: remembered.join_url, provider_fields: remembered.provider_fields } : row; }) }));
      if (isAdmin) {
        const configured = await externalMeetingsApi.connections(workspaceId); if (!current(version)) return; setAdminRows(configured.filter(row => row.channel_id === channelId));
        try { const calendars = await externalMeetingsApi.calendarSources(workspaceId); if (current(version)) setSources(calendars.filter(row => row.channel_id === channelId && row.kind === 'calendar' && row.has_credentials)); } catch { if (current(version)) setSources([]); }
      }
    } catch (failure) { if (current(version)) setError(describe(failure)); }
    finally { if (current(version)) setLoading(false); }
  }
  useEffect(() => {
    const version = ++generation.current; owner.current = api.getAccessToken(); pending.current = null;
    setData({ enabled: false, connections: [], meetings: [] }); setAdminRows([]); setSources([]); setFields(initialFields()); setSelected(''); setDisclosure(false); setActionDisclosure(false); setConfigure(!!initialProvider); resetApplication(); setBusy(''); setError(''); setNotice(''); setCancelId(''); void load(version);
    const params = new URLSearchParams(location.hash.slice(1));
    if (oauthResult.current.has('external_meeting_connected')) { setNotice('Provider authorization saved. Test the connection before scheduling.'); params.delete('external_meeting_connected'); history.replaceState(null, '', location.pathname + location.search + (params.size ? '#' + params : '')); }
    if (oauthResult.current.has('external_meeting_error')) { setError('Provider authorization failed or expired. Start a new authorization.'); params.delete('external_meeting_error'); history.replaceState(null, '', location.pathname + location.search + (params.size ? '#' + params : '')); }
    queueMicrotask(() => { if (current(version)) oauthResult.current = new URLSearchParams(); });
    const changed = () => { ++generation.current; pending.current = null; setData({ enabled: false, connections: [], meetings: [] }); setAdminRows([]); setSources([]); setFields(initialFields()); setSelected(''); setConfigure(false); resetApplication(); setError(''); setNotice(''); setBusy(''); setLoading(false); setCallbackLoading(false); setDisclosure(false); setActionDisclosure(false); setCancelId(''); };
    window.addEventListener('freecollab:auth-changed', changed);
    return () => { ++generation.current; window.removeEventListener('freecollab:auth-changed', changed); };
  }, [workspaceId, channelId, isAdmin]);
  useEffect(() => {
    setCallback('');
    if (!configure || !isAdmin || source || !owner.current || owner.current !== api.getAccessToken()) { setCallbackLoading(false); return; }
    const version = generation.current; let stale = false; setCallbackLoading(true);
    void meetingSetupCallback(workspaceId, channelId, provider).then(value => { if (!stale && current(version)) setCallback(value); }).catch(() => { /* Read-only guidance may be unavailable on older servers. */ }).finally(() => { if (!stale && current(version)) setCallbackLoading(false); });
    return () => { stale = true; };
  }, [workspaceId, channelId, configure, isAdmin, provider, source]);
  async function run(key: string, operation: (version: number) => Promise<void>) {
    if (busy) return; const version = generation.current; setBusy(key); setError(''); setNotice('');
    try { await operation(version); } catch (failure) { if (current(version)) setError(describe(failure)); }
    finally { if (current(version)) setBusy(''); }
  }
  function remember(row: ExternalMeeting) { setData(previous => ({ ...previous, meetings: [row, ...previous.meetings.filter(value => value.id !== row.id)] })); }
  function calendarChanged() { onCalendarChanged?.(); window.dispatchEvent(new CustomEvent('freecollab:calendar-changed', { detail: { channelId } })); }
  async function finish(row: ExternalMeeting, version: number) {
    check(version); remember(row);
    if (row.state === 'awaiting_provider_link') { setNotice('The provider is preparing the meeting link. Retrieve this existing request when it is ready.'); return; }
    const saved = await attachEncryptedMeetingCalendar(row, () => check(version)); check(version); remember(saved); calendarChanged();
    setNotice(saved.secure ? 'Provider meeting saved with a verified encrypted calendar entry.' : 'Provider meeting and channel calendar entry created.');
  }
  async function schedule(event: FormEvent) {
    event.preventDefault(); if (!disclosure || !selected) return;
    await run('create', async version => {
      const snapshot: MeetingFields = { ...fields, starts_at: zonedInstant(fields.starts_at, fields.timezone), ends_at: zonedInstant(fields.ends_at, fields.timezone) };
      const chosen = data.connections.find(row => row.id === selected && row.enabled && row.tested);
      if (!chosen) throw new Error('The meeting provider connection changed or needs a successful test');
      const hash = JSON.stringify({ connection: selected, revision: chosen.revision, fields: snapshot });
      if (!pending.current || pending.current.hash !== hash) pending.current = { id: crypto.randomUUID(), hash };
      const requestId = pending.current.id;
      check(version);
      try { const row = await externalMeetingsApi.create(channelId, selected, snapshot, requestId, chosen.revision); check(version); await finish(row, version); pending.current = null; setDisclosure(false); await load(version); }
      catch (failure) { if (current(version)) await load(version); throw failure; }
    });
  }
  async function saveConfig(event: FormEvent) {
    event.preventDefault(); if (!configDisclosure || !data.enabled) return;
    const draft: ProviderDraft = { name, provider, channel_id: channelId, provider_disclosure: true, password, ...(code ? { code } : {}), config: source ? {} : { client_id: clientId, ...(provider !== 'zoom' ? { calendar_id: calendarId } : {}), ...(provider === 'microsoft_teams' ? { tenant_id: tenantId } : {}) }, ...(source ? { source_connection_id: source } : clientSecret ? { client_secret: clientSecret } : {}), ...(editing ? { expected_revision: editing.revision } : {}) };
    await run('configure', async version => { check(version); if (editing) await externalMeetingsApi.revise(workspaceId, editing.id, draft); else await externalMeetingsApi.configure(workspaceId, draft); check(version); resetApplication(); setConfigure(setupMode); setNotice('Customer provider configuration saved. Authorize and test it before scheduling.'); await load(version); });
  }
  function edit(row: ExternalMeetingConnection) {
    clearPrivate(); baseline.current = JSON.stringify({ name: row.name, provider: row.provider, clientId: row.config?.client_id || '', clientSecret: '', calendarId: row.config?.calendar_id || '', tenantId: row.config?.tenant_id || '', source: row.source_connection_id || '', editingId: row.id });
    setEditing(row); setConfigure(true); setName(row.name); setProvider(row.provider); setSource(row.source_connection_id || ''); setClientId(row.config?.client_id || ''); setTenantId(row.config?.tenant_id || ''); setCalendarId(row.config?.calendar_id || ''); setConfigDisclosure(false);
  }
  const choices = data.connections.filter(row => row.enabled && row.tested);
  const sourceChoices = sources.filter(row => row.provider === (provider === 'google_meet' ? 'google' : 'microsoft') && provider !== 'zoom');
  const stateLabel = (state: string) => ({ ready: t('Ready'), uncertain: t('Provider result uncertain'), awaiting_provider_link: t('Provider is preparing the link'), awaiting_secure_calendar: t('Encrypted calendar entry needed'), cancelling: t('Cancellation needs recovery'), cancelled: t('Cancelled') }[state] || t('Needs attention'));
  return <section className="fc-external-meetings" aria-label={t('External meeting providers')}>
    <div className="fc-external-meeting-heading"><div><h3>{t('Meet on your terms.')}</h3><p>{t('Schedule with your organisation’s Google Meet, Microsoft Teams or Zoom account.')}</p></div><button className="fc-icon-button" aria-label={t('Refresh external meetings')} disabled={!!busy || loading} onClick={() => void load()}><RefreshCw size={16} /></button></div>
    <p className="fc-external-meeting-disclosure"><ShieldCheck size={18} /><span>{t('External providers receive only the title and time you explicitly enter, plus the connected account. Channel messages and documents are never read. Provider privacy, recording and encryption policies apply. FreeCollab WebRTC calls are separate.')}</span></p>
    {error && <p className="fc-alert" role="alert">{t(error)}</p>}{notice && <p className="fc-external-meeting-notice" role="status">{t(notice)}</p>}
    {loading && <p role="status"><Loader2 size={16} className="animate-spin" />{t('Loading meeting providers…')}</p>}
    {!data.enabled && !loading && <p className="fc-account-muted">{t('An operator must enable external meeting providers before any provider is contacted.')}</p>}
    {data.enabled && !setupMode && <form className="fc-external-meeting-form" onSubmit={event => void schedule(event)} aria-label={t('Schedule external meeting')}>
      <label>{t('Meeting provider')}<select aria-label={t('Meeting provider')} className="fc-input" value={selected} disabled={!!busy} required onChange={event => { setSelected(event.target.value); pending.current = null; }}><option value="">{t('Choose a tested customer connection')}</option>{choices.map(row => <option key={row.id} value={row.id}>{providerNames[row.provider]} · {row.name}</option>)}</select></label>
      <label>{t('Meeting title')}<input className="fc-input" value={fields.title} maxLength={200} required disabled={!!busy} onChange={event => setFields({ ...fields, title: event.target.value })} /></label>
      <div className="fc-external-meeting-dates"><label>{t('Starts')}<input className="fc-input" type="datetime-local" value={fields.starts_at} required disabled={!!busy} onChange={event => setFields({ ...fields, starts_at: event.target.value })} /></label><label>{t('Ends')}<input className="fc-input" type="datetime-local" value={fields.ends_at} required disabled={!!busy} onChange={event => setFields({ ...fields, ends_at: event.target.value })} /></label></div>
      <label>{t('Time zone')}<input className="fc-input" value={fields.timezone} required maxLength={100} disabled={!!busy} onChange={event => setFields({ ...fields, timezone: event.target.value })} /></label>
      <label className="fc-external-meeting-check"><input type="checkbox" checked={disclosure} disabled={!!busy} onChange={event => setDisclosure(event.target.checked)} /><span>{t('I choose to disclose this meeting title, time and connected account to the selected provider. No participant invitations will be sent.')}</span></label>
      <button className="fc-button fc-button-primary" disabled={!!busy || !disclosure || !selected || !fields.title.trim()}><CalendarPlus size={16} />{t('Create provider meeting and calendar entry')}</button>
      {!choices.length && <p className="fc-account-muted">{t('A channel and workspace administrator must configure, authorize and test a customer connection first.')}</p>}
    </form>}
    {!setupMode && !!data.meetings.length && <><label className="fc-external-meeting-check"><input type="checkbox" checked={actionDisclosure} disabled={!!busy} onChange={event => setActionDisclosure(event.target.checked)} /><span>{t('I allow the lookup or cancellation I choose below to contact that meeting provider.')}</span></label><ul className="fc-external-meeting-list">{data.meetings.map(row => {
      const connection = data.connections.find(value => value.id === row.connection_id), canManage = row.created_by === api.getCurrentUserId() || isAdmin;
      const label = row.fields?.title || t('Encrypted meeting');
      return <li key={row.id}><div className="fc-external-meeting-item-heading"><div><strong>{label}</strong><span>{connection ? providerNames[connection.provider] : t('External provider')} · {stateLabel(row.state)}</span>{row.fields && <small>{new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short', timeZone: row.fields.timezone }).format(new Date(row.fields.starts_at))}</small>}</div>{row.join_url && row.state !== 'cancelled' && connection && <a className="fc-button" href={validateProviderNavigation(connection.provider, row.join_url)} target="_blank" rel="noopener noreferrer"><ExternalLink size={15} />{t('Open provider meeting')}</a>}</div>
        {row.last_error && <p className="fc-account-muted">{t('The last provider operation needs attention. Recover this request before creating another meeting.')}</p>}
        {canManage && row.state !== 'cancelled' && <div className="fc-external-meeting-actions"><button className="fc-button" disabled={!!busy || !actionDisclosure || row.state === 'cancelling'} onClick={() => void run('recover:' + row.id, async version => { const result = await externalMeetingsApi.recover(row); check(version); await finish(result, version); })}><RefreshCw size={14} />{t('Retrieve provider meeting')}</button><button className="fc-button" disabled={!!busy || !actionDisclosure || !row.calendar_item_id} onClick={() => setCancelId(row.id)}><X size={14} />{t('Cancel provider meeting')}</button></div>}
        {canManage && row.secure && row.state === 'cancelled' && row.calendar_item_id && <button className="fc-button" disabled={!!busy} onClick={() => void run('calendar-cleanup:' + row.id, async version => { const removed = await removeEncryptedMeetingCalendar(row, () => check(version)); check(version); remember(removed); calendarChanged(); setNotice('Encrypted calendar entry removed or already absent.'); })}>{t('Remove encrypted calendar entry')}</button>}
        {cancelId === row.id && <div className="fc-external-meeting-confirm"><p>{t('This cancels the external meeting and removes its channel calendar entry. Provider-side changes are checked first.')}</p><button className="fc-button" disabled={!!busy} onClick={() => setCancelId('')}>{t('Keep meeting')}</button><button className="fc-button fc-button-danger" disabled={!!busy || !actionDisclosure} onClick={() => void run('cancel:' + row.id, async version => { const result = await externalMeetingsApi.cancel(row); check(version); remember(result); setCancelId(''); setNotice('Provider meeting cancelled.'); const removed = await removeEncryptedMeetingCalendar(result, () => check(version)); check(version); remember(removed); calendarChanged(); })}>{t('Confirm cancellation')}</button></div>}
      </li>;
    })}</ul></>}
    {isAdmin && <section className="fc-external-meeting-admin" aria-label={t('Customer meeting connections')}><div className="fc-external-meeting-heading"><h4>{t('Customer-owned provider connections')}</h4><button className="fc-button" disabled={!!busy || !data.enabled} onClick={() => { resetApplication(); setConfigure(true); }}><Video size={15} />{t('Add meeting provider')}</button></div><p>{t('Use your own OAuth app. FreeCollab does not supply a provider account, licence or shared SSO. Calendar grants are reused only with matching calendar write consent.')}</p>
      {(configure || visibleAdminRows.length > 0) && <div className="fc-external-meeting-reauth"><label>{t('Your current FreeCollab password')}<input className="fc-input" type="password" autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)} disabled={!!busy} /></label><label>{t('Authenticator or recovery code, if enabled')}<input className="fc-input" autoComplete="one-time-code" value={code} maxLength={64} onChange={event => setCode(event.target.value)} disabled={!!busy} /></label></div>}
      {configure && <form className="fc-external-meeting-form" onSubmit={event => void saveConfig(event)} aria-label={t('Configure customer meeting provider')}><label>{t('Connection name')}<input className="fc-input" value={name} maxLength={80} required disabled={!!busy} onChange={event => setName(event.target.value)} /></label><label>{t('Provider')}<select aria-label={t('Provider')} className="fc-input" value={provider} disabled={!!busy || !!editing || (setupMode && !!initialProvider)} onChange={event => resetApplication(event.target.value as ExternalMeetingProvider, true)}>{Object.entries(providerNames).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
        {sourceChoices.length > 0 && <label>{t('OAuth authorization')}<select aria-label={t('OAuth authorization')} className="fc-input" value={source} disabled={!!busy} onChange={event => { setSource(event.target.value); setClientId(''); setTenantId(''); setCalendarId(provider === 'google_meet' ? 'primary' : ''); setConfigDisclosure(false); clearPrivate(); }}><option value="">{t('Authorize a customer OAuth app')}</option>{sourceChoices.map(row => <option value={row.id} key={row.id}>{t('Reuse calendar grant: {name}', { name: row.name })}</option>)}</select></label>}
        {!source && <><label>{t('Customer OAuth client ID')}<input className="fc-input" value={clientId} maxLength={1024} required disabled={!!busy} onChange={event => setClientId(event.target.value)} /></label><label>{t('Customer OAuth client secret')}<input className="fc-input" type="password" autoComplete="new-password" value={clientSecret} maxLength={4096} disabled={!!busy} required={provider === 'zoom'} onChange={event => setClientSecret(event.target.value)} /></label>{provider === 'microsoft_teams' && <label>{t('Microsoft tenant ID')}<input className="fc-input" value={tenantId} required disabled={!!busy} onChange={event => setTenantId(event.target.value)} /></label>}{provider !== 'zoom' && <label>{t('Selected calendar ID')}<input className="fc-input" value={calendarId} maxLength={1024} required disabled={!!busy} onChange={event => setCalendarId(event.target.value)} /></label>}</>}
        {!source && <div className="fc-account-muted"><strong>{t('Prepare in your provider console')}</strong><p>{t('Register an OAuth application owned by your organisation and add the exact callback below. Saving here does not authorize the provider.')}</p><a href={customerAppConsoles[provider]} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">{t('Open provider developer console')} <ExternalLink size={13} /></a><p>{provider === 'google_meet' ? t('Google Meet requires a client ID and a writable calendar ID. The primary calendar is prefilled. A client secret is optional with PKCE; Google Workspace and Meet availability depend on your account.') : provider === 'microsoft_teams' ? t('Teams requires a client ID, your organisation tenant ID and a writable calendar ID. Allow delegated calendar event write access. A client secret is optional with PKCE; the account must support Teams meetings.') : t('Zoom requires a client ID and client secret for a customer OAuth app, plus meeting create, read, list and delete scopes. A Zoom account, licence or administrator approval may be required.')}</p><label>{t('OAuth callback URL')}<input className="fc-input" readOnly value={callback} /></label><button className="fc-button" type="button" disabled={!!busy || !callback} onClick={() => void run('copy-callback', async version => { await navigator.clipboard.writeText(callback); check(version); setNotice('Exact callback copied.'); })}><Copy size={14} />{t('Copy exact callback')}</button>{!callback && !callbackLoading && <p>{t('The callback is unavailable. Ask the operator to configure the public API URL before authorizing this app.')}</p>}</div>}
        <label className="fc-external-meeting-check"><input type="checkbox" checked={configDisclosure} disabled={!!busy} onChange={event => setConfigDisclosure(event.target.checked)} /><span>{t('I authorize explicit meeting creation for this channel with this customer app. No background access to channel content is granted.')}</span></label><p className="fc-account-muted">{provider === 'zoom' ? t('Configure Zoom meeting create, read, list and delete scopes in your customer OAuth app. A paid account or administrator approval may be required.') : t('OAuth asks for calendar event write access. Meet or Teams availability also depends on the customer account and provider licence.')}</p>
        {editing && <p className="fc-account-muted">{t('Saving changes discards the previous delegated OAuth grant and requires authorization and a successful test again.')}</p>}<button className="fc-button fc-button-primary" disabled={!!busy || !configDisclosure || !password || !data.enabled}>{t('Save customer provider configuration')}</button><button className="fc-button" type="button" disabled={!!busy} onClick={() => { resetApplication(); setConfigure(setupMode); }}>{t('Cancel')}</button></form>}
      <ul className="fc-external-meeting-connections">{visibleAdminRows.map(row => <li key={row.id}><div><strong>{row.name}</strong><span>{providerNames[row.provider]} · {row.enabled && row.tested ? t('Ready') : row.status === 'connected' ? t('Authorization saved; test required') : row.status === 'disconnected' ? t('Disconnected') : t('OAuth authorization required')}</span></div><div className="fc-external-meeting-actions"><button className="fc-button" disabled={!!busy} onClick={() => edit(row)}>{t('Edit configuration')}</button>{!row.source_connection_id && <button className="fc-button" disabled={!!busy || !password || !data.enabled} onClick={() => void run('authorize:' + row.id, async version => { const result = await externalMeetingsApi.authorize(workspaceId, row.id, password, code); check(version); const destination = validateProviderNavigation(row.provider, result.authorization_url, true); clearPrivate(); window.location.assign(destination); })}>{t('Authorize customer account')}</button>}<button className="fc-button" disabled={!!busy || !password || !data.enabled} onClick={() => void run('test:' + row.id, async version => { await externalMeetingsApi.test(workspaceId, row.id, password, code); check(version); setNotice('Provider authorization and selected calendar tested. Explicit scheduling is now available.'); await load(version); })}>{t('Test provider connection')}</button><button className="fc-button" disabled={!!busy || !password} onClick={() => void run('disconnect:' + row.id, async version => { await externalMeetingsApi.disconnect(workspaceId, row.id, password, code); check(version); clearPrivate(); setNotice('Connection disconnected locally. Existing provider meetings remain at the provider; revoke the app there to end the provider grant.'); await load(version); })}><Unplug size={14} />{t('Disconnect')}</button></div></li>)}</ul>
    </section>}
  </section>;
}
