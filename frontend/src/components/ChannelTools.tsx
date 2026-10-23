import { t, useI18n, currentLocale } from '../i18n';
import { useEffect, useRef, useState, type SetStateAction } from 'react';
import { CheckCircle2, Copy, Link, Paperclip, RefreshCw, ShieldCheck, Users, X } from 'lucide-react';
import { createPortal } from 'react-dom';
import { api, Channel, ChannelInvite, ChannelMember } from '../lib/api';
import { getSocket } from '../lib/socket';
import { listChannelFiles, type ManagedFile } from '../lib/files-api';
import { ChannelFiles } from './ChannelFiles';
import { Avatar } from './Avatar';
import { useModalFocus } from '../lib/useModalFocus';
import '../styles/panels.css';

interface Props {
  channel: Channel;
  tab: 'members' | 'files';
  onClose: () => void;
  onLeave: () => void;
  onRoleChange: (role: 'admin' | 'member') => void;
  onManage?: () => void;
  focusMemberId?: string;
}

const button = 'fc-button fc-panel-button';
const describe = (error: unknown) => error instanceof Error ? error.message : 'Something went wrong. Please try again.';

export function ChannelTools({ channel, tab, onClose, onLeave, onRoleChange, onManage, focusMemberId }: Props) {
  useI18n();
  const [members, setMembers] = useState<ChannelMember[]>([]);
  const [files, setFiles] = useState<ManagedFile[]>([]);
  const [invites, setInvites] = useState<ChannelInvite[]>([]);
  const [inviteLink, setInviteLink] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const generation = useRef(0);
  const fileRevision = useRef(0);
  const memberScope = useRef<{ channelId: string; token: string | null; userId: string | null } | null>(null);
  const focusedMember = useRef('');
  const dialog = useRef<HTMLDivElement>(null);
  useModalFocus(dialog, onClose);
  const currentUser = api.getCurrentUserId();
  const ownMember = members.find((member) => member.user_id === currentUser);
  const isAdmin = ownMember?.role === 'admin' || (!ownMember && channel.role === 'admin');
  const adminCount = members.filter((member) => member.role === 'admin').length;
  const activeInvites = invites.filter(invite => !invite.revoked_at && !invite.used_at && !invite.accepted_at && new Date(invite.expires_at).getTime() > Date.now());

  useEffect(() => {
    ++generation.current;
    memberScope.current = null; focusedMember.current = '';
    setInviteLink(''); setNotice(''); setError(''); setMembers([]); setFiles([]); setInvites([]);
    void load();
    const changed = (data: { channel_id: string }) => { if (data.channel_id === channel.id) void load(); };
    const socket = getSocket();
    if (tab === 'members') socket.on('members_changed', changed);
    return () => { ++generation.current; socket.off('members_changed', changed); };
  }, [channel.id, tab, currentUser]);

  useEffect(() => {
    const scope = memberScope.current;
    if (tab !== 'members' || loading || !focusMemberId || !scope || scope.channelId !== channel.id ||
      scope.token !== api.getAccessToken() || scope.userId !== currentUser || !members.some(member => member.user_id === focusMemberId)) return;
    const target = JSON.stringify([scope.channelId, scope.token, focusMemberId]);
    if (focusedMember.current === target) return;
    const row = [...(dialog.current?.querySelectorAll<HTMLLIElement>('[data-member-id]') || [])]
      .find(element => element.dataset.memberId === focusMemberId);
    if (!row) return;
    focusedMember.current = target;
    row.scrollIntoView({ block: 'nearest' }); row.focus({ preventScroll: true });
  }, [channel.id, tab, currentUser, focusMemberId, loading, members]);

  async function load() {
    const current = generation.current;
    const token = api.getAccessToken();
    const revision = fileRevision.current;
    const valid = () => current === generation.current && token === api.getAccessToken();
    setLoading(true); setError('');
    try {
      if (tab === 'files') {
        const data = await listChannelFiles(channel.id);
        if (valid() && revision === fileRevision.current) setFiles(data);
      } else {
        memberScope.current = null;
        const data = await api.getChannelMembers(channel.id);
        if (!valid()) return;
        memberScope.current = { channelId: channel.id, token, userId: currentUser };
        setMembers(data);
        const own = data.find((member) => member.user_id === currentUser);
        if (own?.role) onRoleChange(own.role as 'admin' | 'member');
        if (own?.role === 'admin') {
          const records = await api.getChannelInvites(channel.id);
          if (valid()) setInvites(records);
        }
      }
    } catch (err) { if (valid()) setError(describe(err)); }
    finally { if (valid()) setLoading(false); }
  }

  function updateFiles(update: SetStateAction<ManagedFile[]>) {
    // A list fetched before an acknowledged upload/delete must not replace its
    // newer local result when the older response finally arrives.
    ++fileRevision.current;
    setFiles(update);
  }

  async function run(id: string, operation: () => Promise<void>) {
    const current = generation.current;
    setBusy(id); setError(''); setNotice('');
    try { await operation(); }
    catch (err) { if (current === generation.current) setError(describe(err)); }
    finally { if (current === generation.current) setBusy(''); }
  }

  async function createInvite() {
    const current = generation.current;
    await run('invite', async () => {
      const invite = await api.createChannelInvite(channel.id);
      if (current !== generation.current) return;
      if (!invite.token) throw new Error(t("The server did not return an invitation link. Please try again."));
      setInviteLink(`${window.location.origin}${window.location.pathname}#invite=${encodeURIComponent(invite.token)}`);
      setInvites((records) => [invite, ...records]);
      setNotice(t("Link created. Share it privately with one person; it expires in 24 hours."));
    });
  }

  async function copyInvite() {
    try {
      await navigator.clipboard.writeText(inviteLink);
      setNotice(t("Invitation link copied."));
    } catch { setNotice(t("Select and copy the invitation link below.")); }
  }

  return createPortal(
    <div ref={dialog} className="fc-channel-layer" tabIndex={-1} role="dialog" aria-modal="true" aria-label={tab === 'members' ? t("Channel members") : t("Channel files")} onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="fc-channel-panel" aria-busy={loading}>
        <header className="fc-panel-header fc-channel-header">
          <div className="fc-channel-heading">
            <span className="fc-kicker">{channel.name || t("Channel")}</span>
            <h2>{tab === 'members' ? t("Channel members.") : t("Shared files.")}</h2>
            <p>{tab === 'members' ? t("Everyone who shares this conversation.") : t("Useful files, kept with the conversation.")}</p>
          </div>
          <button data-autofocus type="button" className="fc-icon-button" aria-label={t("Close channel panel")} title={t("Close · Esc")} onClick={onClose}><X size={18} /></button>
        </header>
        <div className="fc-panel-body">
          <div className="fc-panel-toolbar">
            {tab === 'members' && onManage && !['direct', 'group_direct'].includes(channel.type) && <button type="button" className={button} onClick={onManage}>{t(isAdmin ? 'Manage channel' : 'Channel details')}</button>}
            {tab === 'files' && channel.workspace_id && <button type="button" className="fc-button fc-panel-button" onClick={() => { window.dispatchEvent(new CustomEvent('freecollab:open-workspace-tools', { detail: { channel, tab: 'cloud' } })); onClose(); }}>{t('Cloud files')}</button>}
            <span className="fc-panel-collection"><span className="fc-panel-collection-icon">{tab === 'members' ? <Users size={16} /> : <Paperclip size={16} />}</span><span>{tab === 'members' ? t("Members") : t("Files")}<strong>{loading && tab === 'members' && !members.length ? '…' : tab === 'members' ? members.length : files.length}</strong></span></span>
            <button type="button" className="fc-text-button" disabled={loading || !!busy} onClick={() => void load()}><RefreshCw size={13} className={loading ? 'fc-refreshing' : ''} />{t("Refresh")}</button>
          </div>
          {error && <div role="alert" className="fc-alert fc-panel-error">{t(error)}<button type="button" className="fc-text-button block mt-2" onClick={() => void load()}>{t(tab === 'members' ? 'Refresh members' : 'Refresh files')}</button></div>}
          {notice && <div role="status" className="fc-panel-notice"><CheckCircle2 size={16} aria-hidden="true" /><p>{notice}</p></div>}
          {loading && tab === 'members' && !members.length ? <div className="fc-panel-loading" role="status"><span className="fc-panel-spinner" /><span>{t('Loading members…')}</span></div> : tab === 'members' ? <>
            <ul className="fc-member-list" aria-label={t("Channel member list")}>
              {members.map(member => {
                const lastAdmin = member.role === 'admin' && adminCount <= 1;
                const self = member.user_id === currentUser;
                return <li key={member.user_id} data-member-id={member.user_id} tabIndex={-1} className="fc-member-row">
                  <div className="fc-member-identity">
                    <span className={`fc-member-avatar ${self ? 'is-self' : ''}`} aria-hidden="true"><Avatar userId={member.user_id} name={member.name} workspaceId={channel.workspace_id} /></span>
                    <div className="fc-member-copy"><p>{member.name}{self ? t(" (you)") : ''}</p><span>{member.role === 'admin' ? <><ShieldCheck size={12} />{t("Channel admin")}</> : t("Member")}</span></div>
                  </div>
                  {isAdmin && <div className="fc-member-actions">
                    <button type="button" className={button} disabled={!!busy || (member.role === 'admin' && lastAdmin)} title={lastAdmin ? t("At least one channel admin is required.") : undefined} onClick={() => void run(member.user_id, async () => { const role = member.role === 'admin' ? 'member' : 'admin'; await api.changeMemberRole(channel.id, member.user_id, role); await load(); setNotice(t(role === 'admin' ? '{name} is now a channel admin.' : '{name} is now a member.', { name: member.name })); })}>{member.role === 'admin' ? t("Make member") : t("Make admin")}</button>
                    {!self && <button type="button" className={`${button} fc-danger-action`} disabled={!!busy || lastAdmin} onClick={() => { if (window.confirm(t('Remove {name} from this channel?', { name: member.name }))) void run(member.user_id, async () => { await api.removeChannelMember(channel.id, member.user_id); await load(); setNotice(t('{name} removed from this channel.', { name: member.name })); }); }}>{t("Remove")}</button>}
                  </div>}
                </li>;
              })}
            </ul>
            {isAdmin && <section className="fc-panel-section fc-invitation-section" aria-labelledby="invite-heading">
              <div className="fc-panel-section-heading"><span className="fc-panel-section-icon"><Link size={17} /></span><div><h3 id="invite-heading">{t("Invite a teammate")}</h3><p>{t("A small team starts with one invitation.")}</p></div></div>
              <p className="fc-panel-description">{t("Create a single-use link. They sign in or create an account before joining.")}</p>
              <button type="button" className={`${button} fc-button-primary fc-panel-wide`} disabled={!!busy} onClick={() => void createInvite()}><Link size={15} />{busy === 'invite' ? t("Creating…") : t("Create invitation link")}</button>
              {inviteLink && <div className="fc-created-invite"><label htmlFor="invite-link">{t("Invitation link")}</label><input id="invite-link" value={inviteLink} readOnly onFocus={event => event.target.select()} className="fc-input fc-invite-input" /><button type="button" className={`${button} fc-panel-wide`} onClick={() => void copyInvite()}><Copy size={14} />{t("Copy link")}</button><p>{t("Share privately with one person. Valid for 24 hours.")}</p></div>}
              {activeInvites.length > 0 && <div className="fc-pending-invites"><h4 className="fc-kicker">{t("Pending invitations · ")}{activeInvites.length}</h4>{activeInvites.map(invite => <div key={invite.id} className="fc-invite-record"><div><span>{t("Single-use link")}</span><small>{t("Expires ")}<time dateTime={invite.expires_at}>{new Date(invite.expires_at).toLocaleString(currentLocale(), { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</time></small></div><button type="button" className="fc-text-button" disabled={!!busy} onClick={() => void run(invite.id, async () => { await api.revokeChannelInvite(channel.id, invite.id); setInvites(records => records.filter(record => record.id !== invite.id)); setInviteLink(''); setNotice(t("Invitation revoked. The link can no longer be used.")); })}>{t("Revoke")}</button></div>)}</div>}
            </section>}
            <footer className="fc-panel-section fc-leave-section"><h3>{t("Your membership")}</h3><p>{ownMember?.role === 'admin' && adminCount <= 1 ? t("Make another member an admin before leaving.") : t("You will need a new invitation to return after leaving.")}</p><button type="button" className={`${button} fc-danger-action`} disabled={!!busy || (ownMember?.role === 'admin' && adminCount <= 1)} onClick={() => { if (window.confirm(t('Leave this channel? You will need a new invitation to rejoin.'))) void run('leave', async () => { await api.leaveChannel(channel.id); onLeave(); }); }}>{t("Leave channel")}</button></footer>
          </> : <ChannelFiles key={`${channel.id}:${currentUser}`} channelId={channel.id} files={files} setFiles={updateFiles} loading={loading} onNotice={setNotice} onError={setError} />}
        </div>
      </section>
    </div>, document.body
  );
}
