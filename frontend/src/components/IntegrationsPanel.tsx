import { t, useI18n } from '../i18n';
import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Activity, AlertCircle, ArrowUpRight, Blocks, Building2, CalendarDays, CheckCircle2, FileCheck2, FolderOpen, FolderSync, Loader2, Plug, RefreshCw, ShieldCheck, Users, Webhook, X } from 'lucide-react';
import { api, type Channel } from '../lib/api';
import { accountApi, type AccountSecurity } from '../lib/account-api';
import { workspaceApi, type Workspace } from '../lib/workspace-api';
import { useModalFocus } from '../lib/useModalFocus';
import { ModalContinuationProvider, useCollaborationModal } from '../lib/useCollaborationModal';
import { DocumentLinks } from './DocumentLinks';
import { CalendarPanel } from './CalendarPanel';
import { SSOConnections } from './SSOConnections';
import { WebhookConnections } from './WebhookConnections';
import { OperationsPanel } from './OperationsPanel';
import { WorkspaceSSOSettings } from './WorkspaceSSOSettings';
import { PeopleSettings } from './PeopleSettings';
import { SyncSettings } from './SyncSettings';
import { ConnectionSetupPanel } from './ConnectionSetupPanel';
import { ConnectionHealthPanel } from './ConnectionHealthPanel';
import { EmbedApplicationsSettings } from './EmbedApplicationsSettings';
import { ConnectPanel } from './ConnectPanel';
import { AppsPanel } from './AppsPanel';
import { EnterpriseGovernancePanel } from './EnterpriseGovernancePanel';
import { SlackImportPanel } from './SlackImportPanel';
import { IntegrationLibraryPanel } from './IntegrationLibraryPanel';
import { PersonalOAuthPanel } from './PersonalOAuthPanel';
import '../styles/account.css';
import '../styles/integrations.css';

type Tab = 'overview' | 'connections' | 'health' | 'documents' | 'calendar' | 'webhooks' | 'account' | 'people' | 'sync' | 'operations' | 'embedding' | 'connect' | 'apps' | 'governance' | 'slack-import' | 'library' | 'provider-accounts';
interface Props { workspaceId: string; channelId?: string | null; channels?: Channel[]; onClose: () => void; initialTab?: 'sync' | 'connections' | 'health' | 'operations' | 'calendar' | 'provider-accounts'; initialConnectionId?: string | null; initialRecipeId?: string }
interface Capabilities { integrations?: { oidc?: boolean; webhooks?: boolean; document_links?: boolean; calendar_ics?: boolean; otrust?: boolean; workforce?: boolean; scim?: boolean; sync?: boolean } }
export function IntegrationsPanel({ workspaceId, channelId, channels: displayChannels = [], onClose, initialTab, initialConnectionId, initialRecipeId }: Props) {
  useI18n();
  const writingLeave = useRef<(() => boolean) | null>(null);
  const dialog = useRef<HTMLElement>(null);
  const overlay = useRef<HTMLDivElement>(null);
  const modal = useCollaborationModal(overlay, t('Integrations'), `integrations:${workspaceId}`);
  const tabNavigation = useRef<HTMLElement>(null);
  const errorRef = useRef<HTMLDivElement>(null);
  const [tab, setTab] = useState<Tab>(initialTab || 'overview');
  const tabRef = useRef<Tab>(tab); tabRef.current = tab;
  const guardOwner = useRef<Tab>('webhooks');
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [selectedChannel, setSelectedChannel] = useState(channelId || '');
  const [security, setSecurity] = useState<AccountSecurity | null>(null);
  const [operator, setOperator] = useState(false);
  const [scopedSession, setScopedSession] = useState(false);
  const [managedAccount, setManagedAccount] = useState(false);
  const [ssoVersion, setSSOVersion] = useState(0);
  const [connectionStart, setConnectionStart] = useState<{ recipeId?: string; channelId?: string; revision: number }>({ revision: 0, recipeId: initialRecipeId });
  const [capabilities, setCapabilities] = useState<Capabilities>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [guard, setGuard] = useState<'busy' | 'secret' | null>(null);
  const mounted = useRef(true);
  const owner = useRef(api.getAccessToken());
  const identity = useRef(api.getCurrentUserId());
  const generation = useRef(0);
  const current = () => mounted.current && owner.current === api.getAccessToken() && identity.current === api.getCurrentUserId();
  const manages = workspace?.role === 'owner' || workspace?.role === 'admin';
  const onGuardChange = useCallback((next: 'busy' | 'secret' | null) => { if (next) guardOwner.current = tabRef.current; setGuard(next); if (next === null) setNotice(''); }, []);
  const guardNotice = (leaving: boolean) => guardOwner.current === 'connections'
    ? t("Return to connection setup and finish or discard pending changes before leaving.")
    : guardOwner.current === 'connect'
      ? t('Store the shared-channel invitation and confirm it is saved before leaving.')
    : guardOwner.current === 'apps' || guardOwner.current === 'library'
      ? t('Store the app signing secret and confirm it is saved before leaving.')
    : guardOwner.current === 'webhooks'
      ? leaving ? t("Save your webhook signing secret and confirm it is stored before leaving.") : t("Save your webhook signing secret and confirm it is stored before changing sections.")
      : leaving ? t("Store the displayed provisioning token and confirm it is saved before leaving.") : t("Store the displayed provisioning token and confirm it is saved before changing sections.");
  const close = () => {
    if (guard === 'busy') return;
    if (guard === 'secret') { setTab(guardOwner.current); setNotice(guardNotice(true)); return; }
    if (writingLeave.current && !writingLeave.current()) return;
    onClose();
  };
  useModalFocus(dialog, close, true, { suspended: modal.parked });
  useEffect(() => { mounted.current = true; void load(); return () => { mounted.current = false; generation.current++; }; }, [workspaceId]);
  useEffect(() => { if (error) errorRef.current?.focus(); }, [error]);
  useEffect(() => { const nav = tabNavigation.current; const active = nav?.querySelector<HTMLElement>('[aria-current="page"]'); if (nav && active && nav.scrollWidth > nav.clientWidth) nav.scrollTo({ left: Math.max(0, active.offsetLeft - nav.offsetLeft - nav.clientWidth / 2 + active.offsetWidth / 2), behavior: 'auto' }); }, [tab, loading]);
  useEffect(() => { if (!loading && (tab === 'people' || tab === 'sync' || tab === 'connections' || tab === 'health' || tab === 'embedding') && (!manages || scopedSession)) { setTab('overview'); setNotice(t("Use a personal administrator account to manage people and cloud connections.")); } }, [loading, tab, manages, scopedSession]);
  async function load() {
    const request = ++generation.current; setLoading(true); setError('');
    let scoped = false;
    try { const me = await api.getMe(); if (!current() || request !== generation.current) return; scoped = me.session_scope === 'workspace'; setScopedSession(scoped); setManagedAccount(me.account_type === 'managed'); }
    catch (failure) { if (current() && request === generation.current) { setError(failure instanceof Error ? failure.message : t("Your session could not be loaded.")); setLoading(false); } return; }
    const results = await Promise.allSettled([workspaceApi.list(), api.getChannels(workspaceId), scoped ? Promise.resolve(null) : accountApi.security(), scoped ? Promise.resolve({ is_admin: false }) : api.request<{ is_admin: boolean }>('/admin/me'), api.request<Capabilities>('/capabilities')]);
    if (!current() || request !== generation.current) return;
    const [workspaces, rooms, state, role, features] = results;
    if (workspaces.status === 'fulfilled') setWorkspace(workspaces.value.find(value => value.id === workspaceId) || null);
    else setError(workspaces.reason instanceof Error ? workspaces.reason.message : t("Your workspace could not be loaded."));
    if (rooms.status === 'fulfilled') {
      setChannels(rooms.value.map(room => ({ ...room, name: displayChannels.find(hint => hint.id === room.id)?.name || room.name })));
      setSelectedChannel(previous => rooms.value.some(room => room.id === previous) ? previous : rooms.value[0]?.id || '');
    } else setError(rooms.reason instanceof Error ? rooms.reason.message : t("Your channels could not be loaded."));
    if (state.status === 'fulfilled') setSecurity(state.value);
    if (role.status === 'fulfilled') setOperator(role.value.is_admin);
    if (features.status === 'fulfilled') setCapabilities(features.value);
    setLoading(false);
  }
  function select(next: Tab) {
    if (next === tab) return;
    if (writingLeave.current && !writingLeave.current()) return;
    if (guard === 'busy') return;
    if (guard === 'secret' && next !== guardOwner.current) { setNotice(guardNotice(false)); return; }
    setTab(next); setError(''); setNotice('');
  }
  const room = channels.find(value => value.id === selectedChannel);
  const tabs = [
    { id: 'overview' as const, label: t("Overview"), Icon: Plug },
    ...(manages && !scopedSession ? [{ id: 'connections' as const, label: t("Connection setup"), Icon: Blocks }] : []),
    ...(manages && !scopedSession ? [{ id: 'health' as const, label: t("Connection health"), Icon: Activity }] : []),
    ...(manages && !scopedSession ? [{ id: 'embedding' as const, label: t('Embedding'), Icon: Blocks }] : []),
    ...(manages ? [{ id: 'connect' as const, label: t('Shared channels'), Icon: Users }] : []),
    ...(manages && !scopedSession ? [{ id: 'apps' as const, label: t('Apps'), Icon: Blocks }] : []),
    { id: 'provider-accounts' as const, label: t('Provider accounts'), Icon: Plug },
    ...(manages && !scopedSession ? [{ id: 'library' as const, label: t('Integration library'), Icon: Blocks }, { id: 'slack-import' as const, label: t('Import from Slack'), Icon: FolderSync }] : []),
    ...(workspace?.role === 'owner' && !scopedSession ? [{ id: 'governance' as const, label: t('Organization & policies'), Icon: ShieldCheck }] : []),
    { id: 'documents' as const, label: t("Documents"), Icon: FolderOpen },
    { id: 'calendar' as const, label: t("Calendar"), Icon: CalendarDays },
    ...(manages ? [{ id: 'webhooks' as const, label: t("Webhooks"), Icon: Webhook }] : []),
    { id: 'account' as const, label: t("Sign-in"), Icon: Building2 },
    ...(manages && !scopedSession ? [{ id: 'people' as const, label: t("People"), Icon: Users }, { id: 'sync' as const, label: t("Sync"), Icon: FolderSync }] : []),
    ...(operator ? [{ id: 'operations' as const, label: t("Operations"), Icon: Activity }] : []),
  ];
  const features = capabilities.integrations || {};
  return createPortal(<ModalContinuationProvider modalId={modal.modalId}><div ref={overlay} hidden={modal.parked} style={modal.parked ? { display: 'none' } : undefined} className="fc-account-overlay" onMouseDown={event => { if (event.target === event.currentTarget) close(); }}>
    <section ref={dialog} className="fc-account-panel fc-integrations-panel" data-section={tab} role="dialog" aria-modal="true" aria-label={t("Integrations")} tabIndex={-1}>
      <header className="fc-account-header"><div><span className="fc-kicker">{workspace?.name || t("Your shared workspace")}</span><h2>{t("Connected, thoughtfully.")}</h2><p>{t("Your documents, calendars and workflows. Access stays deliberate.")}</p></div><button type="button" data-autofocus className="fc-icon-button" aria-label={t("Close integrations")} onClick={close} disabled={guard === 'busy'}><X size={20} /></button></header>
      <nav ref={tabNavigation} className="fc-account-tabs" aria-label={t("Integration sections")}>{tabs.map(({ id, label, Icon }) => <button key={id} type="button" aria-current={tab === id ? 'page' : undefined} disabled={guard === 'busy'} onClick={() => select(id)}><Icon size={15} />{label}</button>)}</nav>
      <div className="fc-account-body">
        {error && <div ref={errorRef} tabIndex={-1} className="fc-alert fc-account-error" role="alert"><AlertCircle size={17} /><p>{error}</p><button type="button" className="fc-icon-button" aria-label={t("Retry integration settings")} onClick={() => void load()}><RefreshCw size={15} /></button></div>}
        {notice && <div className="fc-alert fc-account-notice" role="status"><CheckCircle2 size={17} /><p>{notice}</p></div>}
        {loading ? <p className="fc-account-loading" role="status"><Loader2 size={18} className="fc-account-spinner" />{t("Loading your connections…")}</p> : <>
          {tab === 'overview' && <section className="fc-account-section"><div className="fc-integration-intro"><span className="fc-integration-intro-icon"><Blocks size={24} /></span><div><h3>{t("A few useful connections.")}</h3><p>{t("Keep the conversation here. Bring in the tools your team already uses.")}</p></div></div><div className="fc-integration-cards">
            {manages && !scopedSession && <button type="button" onClick={() => select('connections')}><Blocks size={23} /><span className="fc-badge">{t("Guided setup")}</span><strong>{t("Connect your tools")}</strong><p>{t("Choose a service, copy its setup details, then connect and verify your own account.")}</p><span className="fc-integration-card-action">{t("Open connection setup")}<ArrowUpRight size={15} /></span></button>}
            <button type="button" onClick={() => select('documents')}><FolderOpen size={23} /><span className="fc-badge">{t("Available")}</span><strong>{t("Documents")}</strong><p>{t("Keep Nextcloud, Google Drive and SharePoint links with their conversation.")}</p><span className="fc-integration-card-action">{t("Open channel documents")}<ArrowUpRight size={15} /></span></button>
            <button type="button" onClick={() => select('calendar')}><CalendarDays size={23} /><span className="fc-badge">{t("Available")}</span><strong>{t("Calendar")}</strong><p>{t("Plan together, then import or export an event for your own calendar.")}</p><span className="fc-integration-card-action">{t("Open channel calendar")}<ArrowUpRight size={15} /></span></button>
            {manages && <button type="button" onClick={() => select('webhooks')}><Webhook size={23} /><span className="fc-badge">{features.webhooks ? t("Enabled") : t("Not configured")}</span><strong>{t("Webhooks & n8n")}</strong><p>{t("Connect signed activity events to workflows, with explicit channel grants.")}</p><span className="fc-integration-card-action">{t("Manage connections")}<ArrowUpRight size={15} /></span></button>}
            <button type="button" onClick={() => select('account')}><Building2 size={23} /><span className="fc-badge">{t("Workspace owned")}</span><strong>{t("Organization sign-in")}</strong><p>{manages ? t("Configure your workspace’s own identity provider, then invite your team to connect.") : t("Connect your identity to the provider chosen by this workspace.")}</p><span className="fc-integration-card-action">{t("Your sign-in connections")}<ArrowUpRight size={15} /></span></button>
            {manages && !scopedSession && <><button type="button" onClick={() => select('people')}><Users size={23} /><span className="fc-badge">{t("Workspace owned")}</span><strong>{t("People & directory")}</strong><p>{t("Welcome people through your organization’s SSO and keep membership aligned with your directory.")}</p><span className="fc-integration-card-action">{t("Manage onboarding and SCIM")}<ArrowUpRight size={15} /></span></button><button type="button" onClick={() => select('sync')}><FolderSync size={23} /><span className="fc-badge">{t("Explicit access")}</span><strong>{t("Cloud synchronization")}</strong><p>{t("Connect a channel’s files or calendar to Google, Microsoft, Nextcloud or CalDAV.")}</p><span className="fc-integration-card-action">{t("Manage cloud connections")}<ArrowUpRight size={15} /></span></button></>}
            <div className="fc-integration-info-card"><FileCheck2 size={23} /><span className="fc-badge">{features.otrust ? t("Configured") : t("Not configured")}</span><strong>{t("Version proofs")}</strong><p>{t("Request an optional OTRUST timestamp from an exact file or decision version. Only its hash is submitted.")}</p><span className="fc-integration-card-note">{t("Open a file version or decision to begin.")}</span></div>
            {operator && <button type="button" onClick={() => select('operations')}><Activity size={23} /><span className="fc-badge">{t("Operator access")}</span><strong>{t("Email & operations")}</strong><p>{t("Check private mail delivery, worker health and activity queues.")}</p><span className="fc-integration-card-action">{t("Open operations")}<ArrowUpRight size={15} /></span></button>}
          </div><div className="fc-integration-privacy"><ShieldCheck size={19} /><p>{t("Encrypted channel content stays on your trusted devices. Workflow events carry identifiers; opening an external document shares access with that document’s provider.")}</p></div></section>}
          {(tab === 'documents' || tab === 'calendar') && <>
            <section className="fc-integration-channel-select"><label htmlFor="integration-channel">{t("Channel")}</label><select id="integration-channel" className="fc-input" value={selectedChannel} onChange={event => { if (event.target.value !== selectedChannel && (!writingLeave.current || writingLeave.current())) setSelectedChannel(event.target.value); }}>{channels.map(channel => <option key={channel.id} value={channel.id}>{channel.name || t("Channel")}{channel.secure ? t(" · encrypted") : ''}</option>)}</select>{room?.secure && <span className="fc-account-muted"><ShieldCheck size={13} />{t("End-to-end encrypted")}</span>}</section>
            {!room ? <div className="fc-integration-empty"><FolderOpen size={24} /><h3>{t("Choose a channel to get started.")}</h3><p>{t("Your channel memberships determine which documents and events you can access.")}</p></div> : tab === 'documents' ? <DocumentLinks workspaceId={workspaceId} leaveController={writingLeave} key={`${room.id}:documents`} channelId={room.id} isAdmin={room.role === 'admin'} /> : <CalendarPanel workspaceId={workspaceId} leaveController={writingLeave} key={`${room.id}:calendar`} channelId={room.id} isAdmin={room.role === 'admin'} />}
          </>}
          {tab === 'connections' && manages && !scopedSession && <ConnectionSetupPanel key={connectionStart.revision} workspaceId={workspaceId} channels={channels} security={security} isOwner={workspace?.role === 'owner'} initialRecipeId={connectionStart.recipeId} initialChannelId={connectionStart.channelId || channelId || undefined} onGuardChange={onGuardChange} />}
          {tab === 'health' && manages && !scopedSession && <ConnectionHealthPanel workspaceId={workspaceId} channels={channels} initialChannelId={channelId} onOpenSetup={(recipeId, selectedId) => { setConnectionStart(previous => ({ recipeId, channelId: selectedId, revision: previous.revision + 1 })); select('connections'); }} />}
          {tab === 'embedding' && manages && !scopedSession && <EmbedApplicationsSettings key={workspaceId} workspaceId={workspaceId} channels={channels} security={security} onGuardChange={onGuardChange} />}
          {tab === 'connect' && manages && <ConnectPanel workspaceId={workspaceId} onGuardChange={onGuardChange} />}
          {tab === 'apps' && manages && !scopedSession && <AppsPanel workspaceId={workspaceId} channels={channels} onGuardChange={onGuardChange} />}
          {tab === 'provider-accounts' && <PersonalOAuthPanel key={workspaceId} workspaceId={workspaceId} />}
          {tab === 'library' && manages && !scopedSession && <IntegrationLibraryPanel key={workspaceId} workspaceId={workspaceId} channels={channels} onGuardChange={onGuardChange} />}
          {tab === 'slack-import' && manages && !scopedSession && <SlackImportPanel key={workspaceId} workspaceId={workspaceId} onGuardChange={onGuardChange} />}
          {tab === 'governance' && workspace?.role === 'owner' && !scopedSession && <EnterpriseGovernancePanel workspaceId={workspaceId} mfaEnabled={security?.mfa_enabled} onGuardChange={onGuardChange} />}
          {tab === 'webhooks' && manages && <WebhookConnections workspaceId={workspaceId} channels={channels} onGuardChange={onGuardChange} />}
          {tab === 'account' && <>{manages && !scopedSession && <WorkspaceSSOSettings workspaceId={workspaceId} security={security} onGuardChange={onGuardChange} onChanged={() => setSSOVersion(value => value + 1)} />}<SSOConnections key={`${workspaceId}:${ssoVersion}`} workspaceId={workspaceId} scopedSession={scopedSession} managedAccount={managedAccount} security={security || undefined} /></>}
          {tab === 'people' && manages && !scopedSession && <PeopleSettings workspaceId={workspaceId} security={security} isOwner={workspace?.role === 'owner'} channels={channels} onGuardChange={onGuardChange} />}
          {tab === 'sync' && manages && !scopedSession && <SyncSettings workspaceId={workspaceId} channels={channels} security={security} onGuardChange={onGuardChange} initialConnectionId={initialConnectionId} />}
          {tab === 'operations' && operator && <OperationsPanel security={security} />}
        </>}
      </div>
      <footer className="fc-account-footer"><ShieldCheck size={15} /><span>{t("Connections follow your workspace and channel access.")}</span><button type="button" className="fc-button" disabled={guard === 'busy'} onClick={close}>{t("Done")}</button></footer>
    </section>
  </div></ModalContinuationProvider>, document.body);
}
