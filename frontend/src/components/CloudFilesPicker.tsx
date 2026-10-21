import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ArrowLeft, Check, Cloud, Copy, ExternalLink, File, Folder, Link, Loader2, Plus, RefreshCw, X } from 'lucide-react';
import { useI18n } from '../i18n';
import { api } from '../lib/api';
import { cloudFilesApi, type CloudApplication, type CloudConnection, type CloudEntry, type CloudLink, type CloudOverview, type CloudProvider } from '../lib/cloud-files-api';
import { listChannelFiles, type ManagedFile } from '../lib/files-api';
import { followOIDCAuthorization } from '../lib/integrations-api';
import { customerAppConsoles, freshCloudApplication, useSetupFormSignals, validSetupCallback, type SetupFormSignals } from '../lib/connection-form-setup';
import '../styles/cloud-files.css';

interface Props extends SetupFormSignals { workspaceId: string; channelId: string; onImported?: (file: ManagedFile) => void; onClose?: () => void; initialProvider?: 'dropbox' | 'box'; setupMode?: boolean }
const providers: Record<CloudProvider, string> = { google: 'Google Drive', microsoft: 'OneDrive', dropbox: 'Dropbox', box: 'Box' };
export function CloudFilesPicker(props: Props) {
  return <CloudFilesPickerContent key={[props.workspaceId, props.channelId, props.initialProvider || '', !!props.setupMode].join(':')} {...props} />;
}
function CloudFilesPickerContent({ workspaceId, channelId, onImported, onClose, initialProvider, setupMode = false, onGuardChange, onDirtyChange }: Props) {
  const { t, locale } = useI18n();
  const freshApplication = () => freshCloudApplication(initialProvider, !!initialProvider);
  const baseline = useRef(JSON.stringify(freshApplication()));
  const [overview, setOverview] = useState<CloudOverview | null>(null), [tab, setTab] = useState<'browse' | 'links' | 'connect'>(initialProvider ? 'connect' : 'browse');
  const [connections, setConnections] = useState<CloudConnection[]>([]), [connectionId, setConnectionId] = useState('');
  const [items, setItems] = useState<CloudEntry[]>([]), [folder, setFolder] = useState<string | null>(null), [trail, setTrail] = useState<{ id: string | null; name: string }[]>([]);
  const [page, setPage] = useState<string | null>(null), [selection, setSelection] = useState<CloudEntry | null>(null), [query, setQuery] = useState('');
  const [confirmed, setConfirmed] = useState(false), [files, setFiles] = useState<ManagedFile[]>([]), [target, setTarget] = useState('');
  const [links, setLinks] = useState<CloudLink[]>([]), [olderLinks, setOlderLinks] = useState<string | null>(null);
  const [linkDraft, setLinkDraft] = useState({ provider: 'google' as CloudProvider, title: '', url: '', consent: false });
  const [application, setApplication] = useState(freshApplication), [authorization, setAuthorization] = useState<{ connection: CloudConnection; action: 'connect' | 'disconnect' } | null>(null);
  const [password, setPassword] = useState(''), [code, setCode] = useState(''), [busy, setBusy] = useState(''), [loading, setLoading] = useState(true), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const generation = useRef(0), browsing = useRef(0), token = useRef(api.getAccessToken()), importId = useRef(crypto.randomUUID());
  const errorRef = useRef<HTMLDivElement>(null);
  const current = (request: number) => generation.current === request && token.current === api.getAccessToken();
  const connection = connections.find(row => row.id === connectionId);
  const visibleConnections = connections.filter(row => !setupMode || !initialProvider || row.provider === initialProvider);
  const callback = validSetupCallback(overview ? application.provider === 'google' || application.provider === 'microsoft' ? overview.sync_callback_url : overview.callback_url : '');
  const dirty = JSON.stringify(application) !== baseline.current || !!password || !!code || !!linkDraft.title || !!linkDraft.url || linkDraft.consent || !!selection;
  useSetupFormSignals(!!busy || loading, dirty, { onGuardChange, onDirtyChange });
  async function load(request = generation.current) {
    setLoading(true); setError('');
    try {
      const [state, references] = await Promise.all([cloudFilesApi.connections(workspaceId, channelId), cloudFilesApi.links(workspaceId, channelId)]);
      if (!current(request)) return;
      setOverview(state); setConnections(state.connections); setLinks(references.items); setOlderLinks(references.next_before);
      if (state.secure || !state.can_manage) setTab('links');
      if (!state.secure && state.can_manage) {
        const material = await listChannelFiles(channelId); if (current(request)) setFiles(material);
      }
    } catch (failure) { if (current(request)) setError(failure instanceof Error ? failure.message : 'Cloud files could not be loaded.'); }
    finally { if (current(request)) setLoading(false); }
  }
  useEffect(() => {
    const request = ++generation.current; token.current = api.getAccessToken();
    baseline.current = JSON.stringify(freshApplication());
    setOverview(null); setConnections([]); setConnectionId(''); setLinks([]); setItems([]); setSelection(null); setFiles([]); setAuthorization(null); setApplication(freshApplication()); setPassword(''); setCode(''); setLinkDraft({ provider: initialProvider || 'google', title: '', url: '', consent: false });
    void load(request);
    const changed = () => { ++generation.current; ++browsing.current; baseline.current = JSON.stringify(freshApplication()); setFolder(null); setTrail([]); setPage(null); setQuery(''); setTarget(''); setConfirmed(false); setOlderLinks(null); importId.current = crypto.randomUUID(); setOverview(null); setConnections([]); setItems([]); setLinks([]); setFiles([]); setSelection(null); setAuthorization(null); setApplication(freshApplication()); setPassword(''); setCode(''); setLinkDraft({ provider: initialProvider || 'google', title: '', url: '', consent: false }); setLoading(false); setBusy(''); setError(''); setNotice(''); };
    window.addEventListener('freecollab:auth-changed', changed);
    return () => { ++generation.current; ++browsing.current; window.removeEventListener('freecollab:auth-changed', changed); };
  }, [workspaceId, channelId]);
  useEffect(() => { if (error) errorRef.current?.focus(); }, [error]);
  async function run(key: string, operation: (request: number) => Promise<void>) {
    if (busy) return; const request = generation.current; setBusy(key); setError(''); setNotice('');
    try { await operation(request); } catch (failure) { if (current(request)) setError(failure instanceof Error ? failure.message : 'The cloud file action could not be completed.'); }
    finally { if (current(request)) setBusy(''); }
  }
  async function browse(id: string, nextFolder: string | null, nextTrail: { id: string | null; name: string }[], nextPage: string | null = null) {
    const attempt = ++browsing.current;
    await run('browse', async request => {
      const displayed = connections.find(row => row.id === id);
      if (!displayed) throw new Error('Reload and select a file from the current cloud connection.');
      const result = await cloudFilesApi.browse(workspaceId, channelId, displayed, nextFolder, nextPage);
      if (!current(request) || attempt !== browsing.current) return;
      setConnectionId(id); setFolder(nextFolder); setTrail(nextTrail); setItems(previous => nextPage ? [...previous, ...result.items].slice(0, 1000) : result.items);
      setPage(result.next_page_token); setSelection(null); setConfirmed(false); setQuery('');
      if (setupMode) setNotice('The selected provider collection was read successfully. No files were imported.');
    });
  }
  function select(entry: CloudEntry) { setSelection(entry); setConfirmed(false); setTarget(''); importId.current = crypto.randomUUID(); }
  async function importSelection() {
    if (!selection || !confirmed || !connection) return;
    await run('import', async request => {
      const file = await cloudFilesApi.importFile(workspaceId, channelId, connection.id, selection, importId.current, files.find(row => row.document_id === target));
      if (!current(request)) return;
      setSelection(null); setConfirmed(false); setNotice('The selected file is now available in this channel.');
      const material = await listChannelFiles(channelId); if (current(request)) { setFiles(material); onImported?.(file); }
    });
  }
  function addLink(event: FormEvent) {
    event.preventDefault(); if (!linkDraft.consent) return;
    void run('link', async request => {
      const row = await cloudFilesApi.addLink(workspaceId, channelId, { provider: linkDraft.provider, title: linkDraft.title, url: linkDraft.url, external_access_acknowledged: true });
      if (!current(request)) return;
      setLinks(previous => [row, ...previous]); setLinkDraft({ provider: 'google', title: '', url: '', consent: false }); setNotice('Provider link shared.');
    });
  }
  function saveApplication(event: FormEvent) {
    event.preventDefault(); if (!application.consent) return;
    void run('application', async request => {
      const row = await cloudFilesApi.create(workspaceId, channelId, { ...application, consent: true } as CloudApplication);
      if (!current(request)) return;
      setConnections(previous => [...previous, row]); baseline.current = JSON.stringify(freshApplication()); setApplication(freshApplication()); setTab(setupMode ? 'connect' : 'browse'); setNotice('Application saved. Select Connect to review the provider consent screen.');
    });
  }
  function authorize(event: FormEvent) {
    event.preventDefault(); if (!authorization) return;
    void run('authorize', async request => {
      const values = { password, ...(code ? { code } : {}) };
      if (authorization.action === 'connect') {
        const result = await cloudFilesApi.authorize(workspaceId, channelId, authorization.connection, values);
        if (current(request)) { setPassword(''); setCode(''); followOIDCAuthorization(result.authorization_url); }
      } else {
        await cloudFilesApi.disconnect(workspaceId, channelId, authorization.connection, values);
        if (current(request)) { setAuthorization(null); setPassword(''); setCode(''); setConnectionId(''); setItems([]); await load(request); }
      }
    });
  }
  const providerOptions = Object.entries(providers).map(([id, name]) => <option key={id} value={id}>{name}</option>);
  function changeProvider(provider: CloudProvider) {
    const draft = freshCloudApplication(provider, !!initialProvider); baseline.current = JSON.stringify(draft);
    setApplication(draft); setAuthorization(null); setPassword(''); setCode('');
  }
  return <section className="cloud-picker" aria-label={t('Cloud files')}>
    <header className="cloud-picker-heading"><div><span className="cloud-picker-eyebrow">{t('Connected collections')}</span><h2><Cloud size={21} aria-hidden="true" />{t('Cloud files')}</h2><p>{t('Choose a file from your own cloud, or share a provider link.')}</p></div><div className="cloud-heading-actions"><button type="button" className="cloud-icon" disabled={!!busy || loading} aria-label={t('Reload cloud connections')} onClick={() => { ++browsing.current; setConnectionId(''); setItems([]); setSelection(null); setConfirmed(false); setFolder(null); setTrail([]); setPage(null); void load(); }}><RefreshCw size={17} /></button>{onClose && <button type="button" className="cloud-icon" aria-label={t('Close cloud files')} disabled={!!busy} onClick={onClose}><X size={18} /></button>}</div></header>
    {error && <div className="cloud-error" role="alert" tabIndex={-1} ref={errorRef}>{t(error)}</div>}
    {notice && <p className="cloud-notice" role="status"><Check size={16} />{t(notice)}</p>}
    {loading ? <p className="cloud-loading"><Loader2 className="spin" size={18} />{t('Loading cloud files…')}</p> : overview && <>
      {!setupMode && <div className="cloud-tabs" role="tablist" aria-label={t('Cloud file actions')}>
        {!overview.secure && overview.can_manage && <button role="tab" aria-selected={tab === 'browse'} disabled={!!busy} onClick={() => setTab('browse')}>{t('Browse and import')}</button>}
        <button role="tab" aria-selected={tab === 'links'} disabled={!!busy} onClick={() => setTab('links')}>{t('Provider links')}</button>
        {!overview.secure && overview.can_manage && <button role="tab" aria-selected={tab === 'connect'} disabled={!!busy} onClick={() => setTab('connect')}><Plus size={14} />{t('Connect application')}</button>}
      </div>}
      {overview.secure && <div className="cloud-mode-note"><strong>{t('Encrypted provider links only')}</strong><p>{t('Select a file in your provider, then paste its sharing link. The link is encrypted on your verified device. Downloading and importing cloud bytes through this server is unavailable in encrypted channels.')}</p></div>}
      {(tab === 'browse' || setupMode) && !overview.secure && overview.can_manage && <div role="tabpanel">
        <div className="cloud-connection-list">{visibleConnections.map(row => <div className="cloud-connection" key={row.id}><span className="cloud-provider-mark" aria-hidden="true">{providers[row.provider].slice(0, 1)}</span><div><strong>{row.name}</strong><small>{providers[row.provider]} · {row.connected ? t('Connected') : t('Authorization required')}</small></div><div className="cloud-connection-actions">{row.connected && <button disabled={!!busy} onClick={() => void browse(row.id, null, [])}>{t(setupMode ? 'Test selected collection' : 'Browse')}</button>}<button disabled={!!busy} onClick={() => { setPassword(''); setCode(''); setAuthorization({ connection: row, action: row.connected ? 'disconnect' : 'connect' }); }}>{row.connected ? t('Disconnect') : t('Connect')}</button></div></div>)}</div>
        {!visibleConnections.length && <div className="cloud-empty"><Cloud size={30} /><strong>{t('Your cloud, your application')}</strong><p>{t('Add a customer-owned OAuth application to browse a selected collection. Nothing connects until you approve the provider consent screen.')}</p><button disabled={!!busy} onClick={() => setTab('connect')}><Plus size={16} />{t('Connect application')}</button></div>}
        {connection && !setupMode && <div className="cloud-browser"><div className="cloud-browser-heading"><div>{trail.length > 0 && <button disabled={!!busy} aria-label={t('Parent folder')} onClick={() => { const previous = trail[trail.length - 1]; void browse(connection.id, previous.id, trail.slice(0, -1)); }}><ArrowLeft size={16} /></button>}<strong>{trail.length ? items[0]?.parent_id === folder ? trail[trail.length - 1]?.name : t('Folder') : connection.name}</strong></div><button disabled={!!busy} aria-label={t('Reload selected folder')} onClick={() => void browse(connection.id, folder, trail)}><RefreshCw size={16} /></button></div>
          <label className="cloud-search">{t('Find in loaded files')}<input value={query} onChange={event => setQuery(event.target.value)} placeholder={t('File or folder name')} /></label>
          <div className="cloud-file-list" aria-label={t('Provider files')}>{items.filter(row => row.name.toLocaleLowerCase(locale).includes(query.toLocaleLowerCase(locale))).map(row => <button key={row.id} className={'cloud-file-row' + (selection?.id === row.id ? ' selected' : '')} disabled={!!busy} aria-pressed={!row.folder ? selection?.id === row.id : undefined} onClick={() => row.folder ? void browse(connection.id, row.id, [...trail, { id: folder, name: row.name }]) : select(row)}>{row.folder ? <Folder size={20} /> : <File size={20} />}<span><strong>{row.name}</strong><small>{row.folder ? t('Folder') : row.size === undefined ? row.mime_type : t('{count} KB', { count: Math.ceil(row.size / 1024) })}</small></span>{selection?.id === row.id && <Check size={18} />}</button>)}</div>
          {!items.length && <p className="cloud-empty-copy">{t('This folder contains no supported files.')}</p>}
          {page && items.length < 1000 && <button className="cloud-load-more" disabled={!!busy} onClick={() => void browse(connection.id, folder, trail, page)}>{t('Load more files')}</button>}
        </div>}
        {selection && !setupMode && <div className="cloud-selection"><strong>{t('Import selected file')}</strong><p>{selection.name}</p><label>{t('Destination')}<select aria-label={t('Destination')} value={target} disabled={!!busy} onChange={event => { setTarget(event.target.value); importId.current = crypto.randomUUID(); setConfirmed(false); }}><option value="">{t('New channel file')}</option>{files.filter(row => row.document_id && row.document_owner_id === api.getCurrentUserId()).map(row => <option value={row.document_id} key={row.document_id}>{t('New version of {name}', { name: row.filename })}</option>)}</select></label><label className="cloud-check"><input type="checkbox" checked={confirmed} disabled={!!busy} onChange={event => setConfirmed(event.target.checked)} /><span>{t('Copy this selected revision into the channel. Channel members will have access to the imported copy.')}</span></label><button className="cloud-primary" disabled={!!busy || !confirmed} onClick={() => void importSelection()}>{busy === 'import' ? <Loader2 className="spin" size={16} /> : <Plus size={16} />}{t('Import file')}</button></div>}
      </div>}
      {tab === 'links' && !setupMode && <div role="tabpanel"><form className="cloud-form" onSubmit={addLink}><h3><Link size={17} />{t('Share a selected provider file')}</h3><label>{t('Provider')}<select aria-label={t('Provider')} value={linkDraft.provider} disabled={!!busy} onChange={event => setLinkDraft({ provider: event.target.value as CloudProvider, title: '', url: '', consent: false })}>{providerOptions}</select></label><label>{t('File title')}<input required maxLength={300} value={linkDraft.title} onChange={event => setLinkDraft({ ...linkDraft, title: event.target.value })} /></label><label>{t('Selected file sharing link')}<input required type="url" maxLength={8192} value={linkDraft.url} placeholder="https://…" onChange={event => setLinkDraft({ ...linkDraft, url: event.target.value })} /></label><label className="cloud-check"><input type="checkbox" checked={linkDraft.consent} onChange={event => setLinkDraft({ ...linkDraft, consent: event.target.checked })} /><span>{t('I selected this file. The provider controls access and may reveal my visit to the file owner. Sharing this link does not grant provider access.')}</span></label><button className="cloud-primary" disabled={!!busy || !linkDraft.consent}>{t('Share provider link')}</button></form>
        <div className="cloud-links">{links.map(row => <article key={row.id}>{row.fields ? <><span className="cloud-provider-mark">{providers[row.fields.provider].slice(0, 1)}</span><div><a href={row.fields.url} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">{row.fields.title}<ExternalLink size={13} /></a><small>{providers[row.fields.provider]} · {t('External permissions apply')}</small></div></> : <p>{t(row.resolution_error || 'Unlock recovery to read this provider link.')}</p>}{(row.created_by === api.getCurrentUserId() || overview.can_manage) && <button disabled={!!busy} aria-label={t('Remove provider link')} onClick={() => void run('remove', async request => { await cloudFilesApi.removeLink(workspaceId, channelId, row.id); if (current(request)) setLinks(previous => previous.filter(item => item.id !== row.id)); })}><X size={15} /></button>}</article>)}</div>{olderLinks && <button disabled={!!busy} onClick={() => void run('older', async request => { const result = await cloudFilesApi.links(workspaceId, channelId, olderLinks); if (current(request)) { setLinks(previous => [...previous, ...result.items]); setOlderLinks(result.next_before); } })}>{t('Load older links')}</button>}
      </div>}
      {(tab === 'connect' || setupMode) && !overview.secure && overview.can_manage && <form className="cloud-form" onSubmit={saveApplication} aria-label={t('Configure customer file provider')}>
        <h3>{t('Connect your own OAuth application')}</h3><p>{t('Use an application registered by your organization. Client secrets are encrypted, and authorization belongs to this channel.')}</p>
        <label>{t('Provider')}<select aria-label={t('Provider')} value={application.provider} disabled={!!busy || (setupMode && !!initialProvider)} onChange={event => changeProvider(event.target.value as CloudProvider)}>{providerOptions}</select></label>
        <div className="cloud-mode-note"><strong>{t('Prepare in your provider console')}</strong><p>{t('Register an OAuth application owned by your organisation, add the exact callback below, and allow access only to the selected collection. Saving here does not authorize the provider.')}</p><a href={customerAppConsoles[application.provider]} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">{t('Open provider developer console')} <ExternalLink size={13} /></a><p>{application.provider === 'box' ? t('Box requires a client ID and client secret. Folder 0 is the account root; choose a narrower folder ID when possible. The selected account must be allowed to read it.') : application.provider === 'dropbox' ? t('Dropbox uses an OAuth client ID. A client secret is optional with PKCE. Leave the root path empty for the app folder, or enter a selected folder path such as /Team.') : t('Use a customer OAuth client ID and the selected folder ID. Microsoft also requires your tenant ID and drive ID. Provider consent is a separate explicit step.')}</p></div>
        {(['name', 'client_id', 'client_secret', 'folder_id'] as const).map(key => <label key={key}>{t(({ name: 'Connection name', client_id: 'Application client ID', client_secret: 'Application client secret', folder_id: application.provider === 'dropbox' ? 'Selected root folder path' : 'Selected root folder ID' } as const)[key])}<input type={key === 'client_secret' ? 'password' : 'text'} autoComplete="off" disabled={!!busy} required={key !== 'client_secret' && !(key === 'folder_id' && application.provider === 'dropbox') || key === 'client_secret' && application.provider === 'box'} value={application[key]} onChange={event => setApplication({ ...application, [key]: event.target.value })} /></label>)}
        {application.provider === 'microsoft' && <><label>{t('Customer tenant ID')}<input required disabled={!!busy} value={application.tenant_id} onChange={event => setApplication({ ...application, tenant_id: event.target.value })} /></label><label>{t('OneDrive drive ID')}<input required disabled={!!busy} value={application.drive_id} onChange={event => setApplication({ ...application, drive_id: event.target.value })} /></label></>}
        <label>{t('OAuth callback URL')}<input readOnly value={callback} /></label><button type="button" disabled={!!busy || !callback} onClick={() => void run('copy-callback', async request => { await navigator.clipboard.writeText(callback); if (current(request)) setNotice('Exact callback copied.'); })}><Copy size={14} />{t('Copy exact callback')}</button>{!callback && <p className="cloud-mode-note">{t('The callback is unavailable. Ask the operator to configure the public API URL before authorizing this app.')}</p>}
        <label>{t('Your FreeCollab password')}<input required type="password" autoComplete="current-password" disabled={!!busy} value={application.password} onChange={event => setApplication({ ...application, password: event.target.value })} /></label><label>{t('Authenticator or recovery code, if enabled')}<input autoComplete="one-time-code" disabled={!!busy} value={application.code} onChange={event => setApplication({ ...application, code: event.target.value })} /></label><label className="cloud-check"><input type="checkbox" checked={application.consent} disabled={!!busy} onChange={event => setApplication({ ...application, consent: event.target.checked })} /><span>{t('I authorize this customer application for this selected collection and channel. I will review the provider consent screen separately.')}</span></label>{(application.provider === 'google' || application.provider === 'microsoft') && !overview.sync_enabled && <p className="cloud-mode-note">{t('An operator must enable remote synchronization before connecting Google Drive or OneDrive.')}</p>}<button className="cloud-primary" disabled={!!busy || !application.consent || (application.provider === 'google' || application.provider === 'microsoft') && !overview.sync_enabled}>{t('Save application')}</button>
      </form>}
      {authorization && <form className="cloud-reauth cloud-form" onSubmit={authorize}><div className="cloud-form-heading"><h3>{authorization.action === 'connect' ? t('Review provider authorization') : t('Disconnect provider')}</h3><button type="button" aria-label={t('Cancel provider authorization')} disabled={!!busy} onClick={() => { setAuthorization(null); setPassword(''); setCode(''); }}><X size={17} /></button></div><p>{authorization.connection.name} · {providers[authorization.connection.provider]}</p><label>{t('Your FreeCollab password')}<input required type="password" autoComplete="current-password" disabled={!!busy} value={password} onChange={event => setPassword(event.target.value)} /></label><label>{t('Authenticator or recovery code, if enabled')}<input value={code} disabled={!!busy} autoComplete="one-time-code" onChange={event => setCode(event.target.value)} /></label><button className="cloud-primary" disabled={!!busy}>{authorization.action === 'connect' ? t('Continue to provider consent') : t('Disconnect')}</button></form>}
    </>}
  </section>;
}
