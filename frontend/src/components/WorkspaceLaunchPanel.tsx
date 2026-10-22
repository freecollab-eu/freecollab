import { useEffect, useRef, useState, type FormEvent } from 'react';
import { createPortal } from 'react-dom';
import { ArrowUpRight, Check, Copy, Hash, ImagePlus, Link2, Loader2, MessageCircle, Palette, Plug, RefreshCw, ShieldCheck, Users, X } from 'lucide-react';
import { LanguagePicker, useI18n } from '../i18n';
import { api, type Channel, type User } from '../lib/api';
import { workspaceApi, type Workspace, type WorkspaceInvite } from '../lib/workspace-api';
import { connectionSetupApi, type ConnectionSetup } from '../lib/connection-setup-api';
import { FONT_OPTIONS, normalizeAppearance, type AppearancePreferences } from '../lib/appearance';
import { activeLaunchInvites, canLaunchWorkspace, launchGuard, nativeLaunchChannels, prepareLaunchInvite, summarizeLaunchConnections, type WorkspaceLaunchGuard } from '../lib/workspace-launch';
import { useModalFocus } from '../lib/useModalFocus';
import { WorkspaceAvatar } from './WorkspaceAvatar';
import '../styles/workspace-launch.css';

export interface WorkspaceLaunchPanelProps {
  workspace: Workspace; channels: Channel[]; onClose: () => void;
  onChanged: () => Promise<void> | void;
  onChannelsChanged: (created?: Channel) => Promise<void> | void;
  onOpenAppearance: () => void; onOpenConnections: (channelId?: string) => void;
  onSelectChannel: (channel: Channel) => void;
  onOpenChannelMembers: (channel: Channel) => void;
  onGuardChange?: (guard: WorkspaceLaunchGuard) => void;
}

/** Five deliberate steps. Completion is derived from current saved resources. */
export function WorkspaceLaunchPanel({ workspace, channels, onClose, onChanged, onChannelsChanged, onOpenAppearance, onOpenConnections, onSelectChannel, onOpenChannelMembers, onGuardChange }: WorkspaceLaunchPanelProps) {
  const { t, locale } = useI18n();
  const [savedWorkspace, setSavedWorkspace] = useState(workspace), [name, setName] = useState(workspace.name);
  const [user, setUser] = useState<User | null>(null), [rooms, setRooms] = useState(channels);
  const [channelName, setChannelName] = useState(''), [channelId, setChannelId] = useState('');
  const [invites, setInvites] = useState<WorkspaceInvite[]>([]), [inviteRole, setInviteRole] = useState<'member' | 'guest'>('member');
  const [invitation, setInvitation] = useState<{ id: string; url: string } | null>(null), [copied, setCopied] = useState(false);
  const [appearance, setAppearance] = useState<AppearancePreferences | null>(null), [connections, setConnections] = useState<ConnectionSetup | null>(null);
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(''), [expired, setExpired] = useState(false);
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [connectionError, setConnectionError] = useState(false), [uncertainInvite, setUncertainInvite] = useState(false);
  const dialog = useRef<HTMLElement>(null), logoInput = useRef<HTMLInputElement>(null);
  const context = useRef({ workspaceId: workspace.id, userId: api.getCurrentUserId(), token: api.getAccessToken() });
  const liveWorkspace = useRef(workspace); liveWorkspace.current = workspace;
  const mounted = useRef(true), generation = useRef(0), loadSequence = useRef(0), busyRef = useRef('');
  const callbacks = useRef({ onGuardChange }); callbacks.current = { onGuardChange };
  const controller = useRef<AbortController | null>(null);
  const refresh = useRef<() => void>(() => {});
  const current = () => mounted.current && context.current.workspaceId === workspace.id && context.current.workspaceId === liveWorkspace.current.id &&
    context.current.token === api.getAccessToken() && context.current.userId === api.getCurrentUserId() && ['owner', 'admin'].includes(liveWorkspace.current.role);
  const nativeChannels = nativeLaunchChannels(workspace.id, rooms);
  const selectedChannel = nativeChannels.find(room => room.id === channelId) || nativeChannels[0];
  const scopeChannel = selectedChannel?.id || null;
  const pendingInvites = activeLaunchInvites(workspace.id, invites);
  const dirty = name !== savedWorkspace.name || !!channelName;
  const guard = expired || !current() ? null : launchGuard(!!busy, dirty, !!invitation);
  const allowed = canLaunchWorkspace(user, savedWorkspace) && ['owner', 'admin'].includes(workspace.role) && !expired && current();
  const locked = loading || !!busy || !allowed;
  const summary = connections && connections.channel_id === scopeChannel ? summarizeLaunchConnections(connections, workspace.id, scopeChannel) : null;
  refresh.current = () => { if (!loading && !guard && current()) void load(); };

  function mayLeave() {
    if (busyRef.current) { setNotice('Wait for the current workspace action to finish.'); return false; }
    if (invitation) { setNotice('Save this invitation privately and acknowledge it, or revoke it before leaving.'); return false; }
    if (dirty && !window.confirm(t('Discard the unsaved workspace name or channel draft?'))) return false;
    setName(savedWorkspace.name); setChannelName(''); return true;
  }
  function close() { if (expired || !current() || mayLeave()) onClose(); }
  useModalFocus(dialog, close);
  useEffect(() => { callbacks.current.onGuardChange?.(guard); }, [guard, onGuardChange]);
  useEffect(() => {
    mounted.current = true; void load();
    const invalidate = () => { generation.current++; loadSequence.current++; controller.current?.abort(); setInvitation(null); setCopied(false); setExpired(true); setUser(null); setLoading(false); setBusy(''); busyRef.current = ''; callbacks.current.onGuardChange?.(null); };
    window.addEventListener('freecollab:auth-changed', invalidate);
    const refreshWhenSafe = () => refresh.current(); window.addEventListener('freecollab:launch-refresh', refreshWhenSafe);
    return () => { mounted.current = false; generation.current++; loadSequence.current++; controller.current?.abort(); window.removeEventListener('freecollab:auth-changed', invalidate); window.removeEventListener('freecollab:launch-refresh', refreshWhenSafe); callbacks.current.onGuardChange?.(null); };
  }, []);
  useEffect(() => { if (workspace.id === context.current.workspaceId) setRooms(channels); }, [channels, workspace.id]);
  useEffect(() => {
    if (!guard) return;
    const beforeUnload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', beforeUnload); return () => window.removeEventListener('beforeunload', beforeUnload);
  }, [guard]);
  useEffect(() => {
    if (!allowed || loading) return;
    const abort = new AbortController(), request = ++loadSequence.current;
    setConnections(null); setConnectionError(false);
    void connectionSetupApi.get(workspace.id, scopeChannel, abort.signal).then(value => {
      if (current() && request === loadSequence.current && !abort.signal.aborted) setConnections(value);
    }).catch(() => { if (current() && request === loadSequence.current && !abort.signal.aborted) setConnectionError(true); });
    return () => abort.abort();
  }, [scopeChannel, allowed, loading]);

  async function validateAdmin() {
    if (!current()) throw new Error(t('Your session changed. Reopen the workspace guide.'));
    const version = generation.current;
    const [me, workspaces] = await Promise.all([api.getMe(), workspaceApi.list()]);
    if (!current() || version !== generation.current || me.id !== context.current.userId) throw new Error(t('Your session changed. Reopen the workspace guide.'));
    const latest = workspaces.find(value => value.id === workspace.id);
    if (!canLaunchWorkspace(me, latest)) { setUser(null); setInvitation(null); setCopied(false); setExpired(true); throw new Error(t('Only a current workspace administrator using FreeCollab sign-in can use this guide.')); }
    return { me, workspace: latest! };
  }
  async function load() {
    if (!current() || busyRef.current) return;
    const request = ++loadSequence.current, version = generation.current;
    setLoading(true); setError(''); controller.current?.abort(); const abort = new AbortController(); controller.current = abort;
    try {
      const checked = await validateAdmin();
      const result = await Promise.allSettled([api.getChannels(workspace.id), workspaceApi.invites(workspace.id), api.request<AppearancePreferences>('/preferences/appearance', { signal: abort.signal })]);
      if (!current() || version !== generation.current || request !== loadSequence.current || abort.signal.aborted) return;
      if (result[0].status === 'rejected' || result[1].status === 'rejected') throw new Error(t('Workspace setup state could not be refreshed. Try again.'));
      setUser(checked.me); setSavedWorkspace(checked.workspace); if (!dirty) setName(checked.workspace.name);
      setRooms(result[0].value); setInvites(result[1].value.map(({ token: _token, ...metadata }) => metadata));
      setAppearance(result[2].status === 'fulfilled' ? normalizeAppearance(result[2].value) : null); setUncertainInvite(false);
    } catch (failure) { if (current() && request === loadSequence.current) setError(failure instanceof Error ? failure.message : t('Workspace setup state could not be refreshed. Try again.')); }
    finally { if (current() && request === loadSequence.current) setLoading(false); }
  }
  async function run(key: string, action: () => Promise<void>) {
    if (!current() || loading || busyRef.current || !allowed) return;
    const version = generation.current; busyRef.current = key; setBusy(key); setError(''); setNotice('');
    try { await validateAdmin(); if (!current() || version !== generation.current) return; await action(); }
    catch (failure) { if (current() && version === generation.current) setError(failure instanceof Error ? failure.message : t('The workspace action could not be completed.'));
    } finally { if (current() && version === generation.current) { busyRef.current = ''; setBusy(''); } }
  }
  async function saveName(event: FormEvent) {
    event.preventDefault(); const value = name.trim(); if (!value || value === savedWorkspace.name) return;
    await run('name', async () => { const next = await workspaceApi.update(workspace.id, value); if (!current()) return; setSavedWorkspace(next); setName(next.name); await onChanged(); if (current()) setNotice('Workspace name saved.'); });
  }
  async function uploadLogo(file: File) {
    if (!['image/png', 'image/jpeg'].includes(file.type) || file.size > 1024 * 1024) { setError(t('Choose a PNG or JPEG image smaller than 1 MB.')); return; }
    await run('logo', async () => { const next = await workspaceApi.uploadLogo(workspace.id, file); if (!current()) return; setSavedWorkspace(next); await onChanged(); if (current()) { setNotice('Workspace logo saved.'); if (logoInput.current) logoInput.current.value = ''; } });
  }
  async function createChannel(event: FormEvent) {
    event.preventDefault(); const value = channelName.trim(); if (!value) return;
    await run('channel', async () => {
      const created = await api.createChannel(value, 'group', [], workspace.id);
      if (!current()) return;
      if (nativeLaunchChannels(workspace.id, [created]).length !== 1) throw new Error(t('The new channel could not be verified for this workspace. Refresh before trying again.'));
      setRooms(previous => [...previous.filter(room => room.id !== created.id), created]); setChannelId(created.id); setChannelName('');
      await onChannelsChanged(created); if (current()) setNotice('Channel created. Open it when you are ready to write your first message.');
    });
  }
  async function createInvitation() {
    if (invitation || uncertainInvite) return;
    await run('invite', async () => {
      try {
        const result = await workspaceApi.invite(workspace.id, inviteRole);
        if (!current()) return;
        const prepared = prepareLaunchInvite(result, workspace.id, inviteRole, location.origin, location.pathname);
        setInvites(previous => [prepared.metadata, ...previous]); setInvitation({ id: prepared.metadata.id, url: prepared.url }); setCopied(false);
      } catch (failure) { if (current()) setUncertainInvite(true); throw failure; }
    });
  }
  async function copyInvitation() {
    if (!invitation) return;
    const value = invitation;
    await run('copy', async () => {
      try { await navigator.clipboard.writeText(value.url); if (current()) { setCopied(true); setNotice('Invitation copied. Acknowledge that you have saved it before leaving.'); } }
      catch { if (current()) setNotice('Clipboard access is unavailable. Select and copy the invitation link, then acknowledge it.'); }
    });
  }
  async function revokeInvitation(id: string) {
    await run('revoke', async () => { await workspaceApi.revokeInvite(workspace.id, id); if (!current()) return; setInvites(previous => previous.filter(invite => invite.id !== id)); if (invitation?.id === id) { setInvitation(null); setCopied(false); } setNotice('Invitation revoked.'); });
  }
  function navigate(action: () => void) { if (allowed && mayLeave()) action(); }

  return createPortal(<div className="fc-account-overlay" onMouseDown={event => { if (event.target === event.currentTarget) close(); }}>
    <section ref={dialog} className="fc-account-panel fc-launch-panel" role="dialog" aria-modal="true" aria-label={t('Workspace launch guide')} tabIndex={-1}>
      <header className="fc-account-header"><div><span className="fc-kicker">{workspace.name}</span><h2>{t('A considered start.')}</h2><p>{t('Five small steps to make this workspace yours. Continue at your own pace.')}</p></div><button data-autofocus type="button" className="fc-icon-button" aria-label={t('Close workspace launch guide')} onClick={close}><X size={20} /></button></header>
      <div className="fc-account-body fc-launch-body" aria-busy={loading || !!busy}>
        {error && <p className="fc-alert fc-account-error" role="alert">{t(error)}</p>}{notice && <p className="fc-alert fc-account-notice" role="status">{t(notice)}</p>}
        {loading ? <p className="fc-account-loading" role="status"><Loader2 size={17} className="fc-account-spinner" />{t('Checking your workspace’s current setup…')}</p> : !allowed ? <p className="fc-alert" role="alert">{t(expired ? 'Your session changed. Reopen the workspace guide.' : 'Only a current workspace administrator using FreeCollab sign-in can use this guide.')}</p> : <>
          <div className="fc-launch-current"><ShieldCheck size={17} /><p>{t('This guide reflects saved workspace state. Optional steps stay optional.')}</p><button type="button" className="fc-icon-button" aria-label={t('Refresh workspace launch state')} disabled={locked || !!invitation || dirty} onClick={() => void load()}><RefreshCw size={16} /></button></div>
          <section className="fc-launch-step" aria-labelledby="launch-identity-title"><div className="fc-launch-step-head"><span className="fc-launch-number">01</span><div><h3 id="launch-identity-title">{t('Give your workspace an identity')}</h3><p>{t('A clear name helps your team find its shared place. A logo is optional.')}</p></div><span className="fc-launch-state"><Check size={14} />{t('Name saved')}</span></div>
            <div className="fc-launch-identity"><WorkspaceAvatar workspace={savedWorkspace} large /><div><strong>{savedWorkspace.name}</strong><span>{t(savedWorkspace.logo_url ? 'Custom logo saved' : 'Initials in use · logo optional')}</span></div></div>
            <form className="fc-account-form" aria-label={t('Workspace identity')} onSubmit={saveName}><label htmlFor="launch-workspace-name">{t('Workspace name')}</label><div className="fc-launch-inline"><input id="launch-workspace-name" className="fc-input" value={name} maxLength={120} required disabled={locked} onChange={event => setName(event.target.value)} /><button type="submit" className="fc-button" disabled={locked || !name.trim() || name.trim() === savedWorkspace.name}>{t('Save name')}</button></div></form>
            <label className="fc-launch-logo"><ImagePlus size={16} /><span>{t('Upload workspace logo')}</span><input ref={logoInput} type="file" aria-label={t('Upload workspace logo')} accept="image/png,image/jpeg" disabled={locked} onChange={event => { const file = event.target.files?.[0]; if (file) void uploadLogo(file); }} /></label><p className="fc-launch-note">{t('PNG or JPEG, up to 1 MB. Shared with workspace members.')}</p>
          </section>
          <section className="fc-launch-step" aria-labelledby="launch-personal-title"><div className="fc-launch-step-head"><span className="fc-launch-number">02</span><div><h3 id="launch-personal-title">{t('Make your own space comfortable')}</h3><p>{t('Your language, colors and typography are personal. Everyone can choose their own.')}</p></div></div>
            <div className="fc-launch-row"><span>{locale === 'sv' ? 'Svenska' : 'English'}{appearance ? ' · ' + (FONT_OPTIONS.find(font => font.id === appearance.font_family)?.label || '') + ' · ' + t(appearance.mode === 'system' ? 'System appearance' : appearance.mode === 'dark' ? 'Dark appearance' : 'Light appearance') : ''}</span><button type="button" className="fc-button" disabled={locked} onClick={() => navigate(onOpenAppearance)}><Palette size={15} />{t('Language & appearance')}</button></div>
            <LanguagePicker compact />
          </section>
          <section className="fc-launch-step" aria-labelledby="launch-channel-title"><div className="fc-launch-step-head"><span className="fc-launch-number">03</span><div><h3 id="launch-channel-title">{t('Create a place to begin')}</h3><p>{t('Start with a shared channel for one real conversation. You choose what to write.')}</p></div><span className="fc-launch-state">{t('{count} shared channels', { count: nativeChannels.length })}</span></div>
            {nativeChannels.length > 0 && <label className="fc-launch-channel"><span>{t('First conversation channel')}</span><select className="fc-input" value={selectedChannel?.id || ''} disabled={locked} onChange={event => setChannelId(event.target.value)}>{nativeChannels.map(room => <option key={room.id} value={room.id}>{room.name || t('Channel')}</option>)}</select></label>}
            <form className="fc-account-form" aria-label={t('Create a shared channel')} onSubmit={createChannel}><label htmlFor="launch-channel-name">{t(nativeChannels.length ? 'Another channel name' : 'First channel name')}</label><div className="fc-launch-inline"><input id="launch-channel-name" className="fc-input" value={channelName} maxLength={100} required disabled={locked} placeholder={t('Team room')} onChange={event => setChannelName(event.target.value)} /><button type="submit" className="fc-button" disabled={locked || !channelName.trim()}><Hash size={15} />{t('Create channel')}</button></div></form>
            <p className="fc-launch-note">{t('This creates a server-encrypted channel. End-to-end encryption can be configured separately. Existing encrypted conversations are not inspected by this guide.')}</p>
          </section>
          <section className="fc-launch-step" aria-labelledby="launch-team-title"><div className="fc-launch-step-head"><span className="fc-launch-number">04</span><div><h3 id="launch-team-title">{t('Invite one person to join you')}</h3><p>{t('Create a single-use invitation and share it privately. No email is sent.')}</p></div><span className="fc-launch-state"><Users size={14} />{t('{count} people', { count: savedWorkspace.member_count })}</span></div>
            <h4 className="fc-launch-subtitle">{t('First, join the workspace')}</h4>
            <p className="fc-launch-note">{t('{count} active invitations', { count: pendingInvites.length })}</p>
            {!invitation && <div className="fc-launch-inline"><label><span>{t('Invitation role')}</span><select className="fc-input" value={inviteRole} disabled={locked} onChange={event => setInviteRole(event.target.value as 'member' | 'guest')}><option value="member">{t('Member')}</option><option value="guest">{t('Guest')}</option></select></label><button type="button" className="fc-button" disabled={locked || uncertainInvite} onClick={() => void createInvitation()}><Link2 size={15} />{t('Create single-use invitation')}</button></div>}
            <p className="fc-launch-note">{t('Invitations expire after 24 hours. Guests only see channels they are explicitly invited to; workspace invitations do not add people to existing channels.')}</p>
            {uncertainInvite && <p className="fc-alert" role="status">{t('Invitation creation could not be confirmed. Refresh the setup state and review pending invitations before trying again.')}</p>}
            {invitation && <div className="fc-launch-invitation"><label htmlFor="launch-invitation-link">{t('Single-use invitation link')}</label><input id="launch-invitation-link" className="fc-input" readOnly value={invitation.url} onFocus={event => event.currentTarget.select()} /><p>{t('This link is shown only now. Keep it private and use a separate invitation for each person.')}</p><div className="fc-launch-invite-actions"><button type="button" className="fc-button" disabled={locked} onClick={() => void copyInvitation()}><Copy size={15} />{t(copied ? 'Copy again' : 'Copy invitation')}</button><button type="button" className="fc-account-link" disabled={locked} onClick={() => void revokeInvitation(invitation.id)}>{t('Revoke this invitation')}</button></div><button type="button" className="fc-button fc-button-primary" disabled={locked} onClick={() => { setInvitation(null); setCopied(false); setNotice('Invitation acknowledged. You can continue when you are ready.'); }}>{t('I have saved this invitation privately')}</button></div>}
            {pendingInvites.length > 0 && <ul className="fc-launch-invites">{pendingInvites.slice(0, 5).map(invite => <li key={invite.id}><span>{t(invite.role === 'guest' ? 'Guest' : invite.role === 'admin' ? 'Admin' : 'Member')} · {t('Expires {date}', { date: new Date(invite.expires_at).toLocaleString(locale, { dateStyle: 'short', timeStyle: 'short' }) })}</span><button type="button" className="fc-account-link" disabled={locked} onClick={() => void revokeInvitation(invite.id)}>{t('Revoke invitation')}</button></li>)}</ul>}
            <div className="fc-launch-channel-access" aria-labelledby="launch-channel-access-title">
              <h4 id="launch-channel-access-title" className="fc-launch-subtitle">{t('Then, join the conversation')}</h4>
              {selectedChannel ? <>
                <p className="fc-launch-note">{t('Workspace membership does not grant channel access. A channel admin creates a separate invitation to {channel}; your teammate chooses to accept it.', { channel: selectedChannel.name || t('Channel') })}</p>
                {selectedChannel.role !== 'admin' && <p className="fc-launch-note">{t('You are a member of this channel. Ask one of its channel admins to create the invitation.')}</p>}
                <button type="button" className="fc-button" disabled={locked} onClick={() => navigate(() => onOpenChannelMembers(selectedChannel))}><Users size={15} />{t(selectedChannel.role === 'admin' ? 'Open channel invitations' : 'View channel members')}<ArrowUpRight size={14} /></button>
                <p className="fc-launch-note">{t('Ready to start? Open {channel} and write your own first message. No invitation or message is created automatically.', { channel: selectedChannel.name || t('Channel') })}</p>
                <button type="button" className="fc-button fc-button-primary" disabled={locked} onClick={() => navigate(() => onSelectChannel(selectedChannel))}><MessageCircle size={15} />{t('Write the first message')}<ArrowUpRight size={14} /></button>
              </> : <p className="fc-launch-note">{t('Create a shared channel above to invite someone into a conversation. Workspace invitations can be prepared independently.')}</p>}
            </div>
          </section>
          <section className="fc-launch-step" aria-labelledby="launch-connections-title"><div className="fc-launch-step-head"><span className="fc-launch-number">05</span><div><h3 id="launch-connections-title">{t('Bring the tools you already trust')}</h3><p>{t('Connections are optional. Prepare your own providers when your team needs them.')}</p></div></div>
            <p className="fc-launch-note">{summary ? t('{ready} saved connections ready · {drafts} awaiting setup', { ready: summary.ready, drafts: summary.drafts }) : t(connectionError ? 'Connection status is unavailable. Open Connections to check the current requirements.' : 'Checking saved connection state…')}</p>
            {selectedChannel && <p className="fc-launch-note">{t('Channel connections are scoped to {channel}.', { channel: selectedChannel.name || t('Channel') })}</p>}
            <button type="button" className="fc-button" disabled={locked} onClick={() => navigate(() => onOpenConnections(selectedChannel?.id))}><Plug size={15} />{t('Open connection guides')}<ArrowUpRight size={14} /></button><p className="fc-launch-note">{t('Saved readiness is not a live provider test. Authorization, tests and enablement each require a separate action.')}</p>
          </section>
        </>}
      </div><footer className="fc-account-footer"><ShieldCheck size={15} /><span>{t('Your pace. Your workspace.')}</span><button type="button" className="fc-button" onClick={close}>{t('Continue later')}</button></footer>
    </section>
  </div>, document.body);
}
