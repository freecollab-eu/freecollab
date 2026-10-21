import { t, useI18n, currentLocale } from '../i18n';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { createPortal } from 'react-dom';
import { AlertCircle, Check, CheckCircle2, Copy, ImagePlus, Link2, Loader2, LogOut, Plus, RefreshCw, Settings2, Trash2, UserPlus, Users, X } from 'lucide-react';
import { api } from '../lib/api';
import { workspaceApi, type Workspace, type WorkspaceInvite, type WorkspaceMember, type WorkspaceRole } from '../lib/workspace-api';
import { useModalFocus } from '../lib/useModalFocus';
import { WorkspaceAvatar } from './WorkspaceAvatar';

interface WorkspaceManagerProps {
  workspace: Workspace | null;
  onClose: () => void;
  onChanged: (selectedId?: string) => Promise<void> | void;
  initialSection?: 'general' | 'create';
}
type WorkspaceSection = 'general' | 'people' | 'invites' | 'create';
const describe = (failure: unknown) => failure instanceof Error ? failure.message : 'Please try again in a moment.';
const roleName = (role: WorkspaceRole) => role.charAt(0).toUpperCase() + role.slice(1);

export function WorkspaceManager({ workspace, onClose, onChanged, initialSection = 'general' }: WorkspaceManagerProps) {
  useI18n();
  const dialog = useRef<HTMLElement>(null);
  useModalFocus(dialog, onClose);
  const [section, setSection] = useState<WorkspaceSection>(workspace ? initialSection : 'create');
  const [name, setName] = useState(workspace?.name || '');
  const [newName, setNewName] = useState('');
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [invites, setInvites] = useState<WorkspaceInvite[]>([]);
  const [role, setRole] = useState<'admin' | 'member' | 'guest'>('member');
  const [inviteLink, setInviteLink] = useState('');
  const [busy, setBusy] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [removing, setRemoving] = useState<WorkspaceMember | null>(null);
  const [leaving, setLeaving] = useState(false);
  const [query, setQuery] = useState('');
  const identity = useRef(api.getCurrentUserId());
  const generation = useRef(0);
  const mounted = useRef(true);
  const logoInput = useRef<HTMLInputElement>(null);
  const isCurrent = () => mounted.current && identity.current === api.getCurrentUserId();
  const manages = workspace?.role === 'owner' || workspace?.role === 'admin';
  const ownerCount = members.filter(member => member.role === 'owner').length;
  const activeInvites = invites.filter(invite => !invite.revoked_at && !invite.accepted_at && new Date(invite.expires_at).getTime() > Date.now());

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; generation.current += 1; }; }, []);
  useEffect(() => { setName(workspace?.name || ''); setQuery(''); setInviteLink(''); setMembers([]); setInvites([]); setRemoving(null); setLeaving(false); setError(''); setNotice(''); if (!workspace) setSection('create'); }, [workspace?.id, workspace?.name]);
  useEffect(() => {
    const request = ++generation.current;
    if (!workspace || section === 'create' || section === 'general') return;
    const timer = window.setTimeout(() => void load(request), query ? 180 : 0);
    return () => window.clearTimeout(timer);
  }, [workspace?.id, section, query]);

  async function load(request = ++generation.current) {
    if (!workspace) return;
    setLoading(true); setError('');
    try {
      if (section === 'invites' && manages) { const records = await workspaceApi.invites(workspace.id); if (isCurrent() && request === generation.current) setInvites(records); }
      else { const records = await workspaceApi.members(workspace.id, query.trim()); if (isCurrent() && request === generation.current) setMembers(records); }
    } catch (failure) { if (isCurrent() && request === generation.current) setError(describe(failure)); }
    finally { if (isCurrent() && request === generation.current) setLoading(false); }
  }

  async function run(key: string, operation: () => Promise<void>) {
    if (busy) return;
    setBusy(key); setError(''); setNotice('');
    try { await operation(); }
    catch (failure) { if (isCurrent()) setError(describe(failure)); }
    finally { if (isCurrent()) setBusy(''); }
  }

  async function create(event: FormEvent) {
    event.preventDefault();
    await run('create', async () => {
      const created = await workspaceApi.create(newName.trim());
      if (!isCurrent()) return;
      await onChanged(created.id); if (!isCurrent()) return;
      setNewName(''); setSection('general'); setNotice(t("Workspace created. You can now invite your team."));
    });
  }

  async function saveName(event: FormEvent) {
    event.preventDefault();
    if (!workspace) return;
    await run('name', async () => { await workspaceApi.update(workspace.id, name.trim()); if (!isCurrent()) return; await onChanged(); if (isCurrent()) setNotice(t("Workspace name saved.")); });
  }

  async function uploadLogo(file: File) {
    if (!workspace) return;
    if (!['image/png', 'image/jpeg'].includes(file.type) || file.size > 1024 * 1024) { setError(t("Choose a PNG or JPEG image smaller than 1 MB.")); return; }
    await run('logo', async () => { await workspaceApi.uploadLogo(workspace.id, file); if (!isCurrent()) return; await onChanged(); if (isCurrent()) { setNotice(t("Workspace logo saved.")); if (logoInput.current) logoInput.current.value = ''; } });
  }

  async function createInvite() {
    if (!workspace) return;
    await run('invite', async () => {
      const invite = await workspaceApi.invite(workspace.id, role);
      if (!isCurrent()) return;
      if (!invite.token) throw new Error(t("No invitation link was returned. Try again."));
      setInviteLink(`${location.origin}${location.pathname}#workspace-invite=${encodeURIComponent(invite.token)}`);
      setInvites(records => [invite, ...records]); setNotice(t("Invitation created. Share this single-use link privately."));
    });
  }

  async function copyInvite() {
    try { await navigator.clipboard.writeText(inviteLink); if (isCurrent()) setNotice(t("Workspace invitation copied.")); }
    catch { if (isCurrent()) setNotice(t("Select and copy the invitation link below.")); }
  }

  return createPortal(<div className="fc-account-overlay" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <section ref={dialog} className="fc-account-panel fc-workspace-panel" role="dialog" aria-modal="true" aria-label={t("Workspace settings")} tabIndex={-1}>
      <header className="fc-account-header"><div><span className="fc-kicker">{section === 'create' ? t("A new shared place") : workspace?.name || t("Your workspaces")}</span><h2>{section === 'create' ? t("Start a workspace.") : t("Workspace settings.")}</h2><p>{section === 'create' ? t("Give your team a place of its own.") : t("People, identity and shared access.")}</p></div><button data-autofocus type="button" className="fc-icon-button" onClick={onClose} aria-label={t("Close workspace settings")}><X size={20} /></button></header>
      <nav className="fc-account-tabs" aria-label={t("Workspace sections")}>{workspace && <><button type="button" aria-current={section === 'general' ? 'page' : undefined} onClick={() => setSection('general')}><Settings2 size={16} />{t("General")}</button><button type="button" aria-current={section === 'people' ? 'page' : undefined} onClick={() => setSection('people')}><Users size={16} />{t("People")}</button>{manages && <button type="button" aria-current={section === 'invites' ? 'page' : undefined} onClick={() => setSection('invites')}><UserPlus size={16} />{t("Invitations")}</button>}</>}<button type="button" aria-current={section === 'create' ? 'page' : undefined} onClick={() => setSection('create')}><Plus size={16} />{t("New")}</button></nav>
      <div className="fc-account-body">
        {error && <div className="fc-alert fc-account-error" role="alert"><AlertCircle size={17} /><p>{error}</p></div>}{notice && <div className="fc-alert fc-account-notice" role="status"><CheckCircle2 size={17} /><p>{notice}</p></div>}
        {section === 'create' ? <form className="fc-account-form" onSubmit={create}><span className="fc-kicker">{t("Make room for your team")}</span><h3>{t("Create a workspace")}</h3><p className="fc-account-muted">{t("Your account can belong to several workspaces. Each has its own people and channels.")}</p><label htmlFor="workspace-new-name">{t("Workspace name")}</label><input className="fc-input" id="workspace-new-name" autoComplete="organization" maxLength={120} value={newName} onChange={event => setNewName(event.target.value)} placeholder={t("Studio North")} disabled={!!busy} required /><button type="submit" className="fc-button fc-button-primary" disabled={!!busy || !newName.trim()}>{busy === 'create' && <Loader2 size={16} className="fc-account-spinner" />}{t("Create workspace")}</button></form> : workspace && <>
          {section === 'general' && <>
            <section className="fc-account-section"><div className="fc-workspace-identity"><WorkspaceAvatar workspace={workspace} large /><div><h3>{workspace.name}</h3><p className="fc-account-muted">{roleName(workspace.role)} · {workspace.member_count} {workspace.member_count === 1 ? t("person") : t("people")}</p></div></div>
              {manages && <div className="fc-account-form"><label htmlFor="workspace-logo">{t("Workspace logo")}</label><input ref={logoInput} id="workspace-logo" className="fc-workspace-logo-input" type="file" accept="image/png,image/jpeg" disabled={!!busy} onChange={event => { const file = event.target.files?.[0]; if (file) void uploadLogo(file); }} /><p className="fc-account-muted">{t("PNG or JPEG, up to 1 MB. Your logo is shared with workspace members.")}</p>{workspace.logo_url && <button className="fc-account-link" type="button" disabled={!!busy} onClick={() => void run('remove-logo', async () => { await workspaceApi.removeLogo(workspace.id); if (!isCurrent()) return; await onChanged(); if (isCurrent()) setNotice(t("Workspace logo removed.")); })}><ImagePlus size={15} />{t("Use initials instead")}</button>}</div>}
            </section>
            <section className="fc-account-section"><form className="fc-account-form" onSubmit={saveName}><label htmlFor="workspace-name">{t("Workspace name")}</label><input className="fc-input" id="workspace-name" value={name} onChange={event => setName(event.target.value)} maxLength={120} disabled={!manages || !!busy} required />{manages && <button className="fc-button" type="submit" disabled={!!busy || !name.trim() || name.trim() === workspace.name}>{busy === 'name' && <Loader2 size={15} className="fc-account-spinner" />}{t("Save workspace name")}</button>}</form></section>
            <section className="fc-account-section"><h3>{t("Membership")}</h3><p className="fc-account-muted">{t("Leaving removes your access to this workspace. Content already saved on your devices cannot be taken back.")}</p>{leaving ? <div className="fc-account-inline-confirm"><p>{t("Leave ")}{workspace.name}?</p><div className="fc-account-actions"><button className="fc-button" type="button" disabled={!!busy} onClick={() => setLeaving(false)}>{t("Stay")}</button><button className="fc-button" type="button" disabled={!!busy} onClick={() => void run('leave', async () => { await workspaceApi.leave(workspace.id); if (!isCurrent()) return; await onChanged(); if (isCurrent()) onClose(); })}>{t("Leave workspace")}</button></div></div> : <button type="button" className="fc-account-link fc-account-danger" disabled={!!busy} onClick={() => setLeaving(true)}><LogOut size={15} />{t("Leave workspace")}</button>}</section>
          </>}
          {section === 'people' && <section className="fc-account-section"><div className="fc-account-section-heading"><div><span className="fc-kicker">{t("Shared access")}</span><h3>{t("Workspace people")}</h3></div><button className="fc-icon-button" type="button" aria-label={t("Refresh workspace people")} disabled={loading || !!busy} onClick={() => void load()}><RefreshCw size={16} /></button></div><p className="fc-account-muted">{workspace.role === 'guest' ? t("As a guest, you can see people who share your assigned channels.") : t("Workspace roles manage access. Encryption devices and keys are verified separately.")}</p><label htmlFor="workspace-person-search" className="fc-visually-hidden">{t("Find workspace people")}</label><input id="workspace-person-search" className="fc-input" type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder={t("Find a person…")} />{loading ? <p className="fc-account-loading" role="status"><Loader2 size={17} className="fc-account-spinner" />{t("Loading people…")}</p> : <ul className="fc-workspace-member-list">{members.map(member => {
            const current = member.user_id === identity.current;
            const lastOwner = member.role === 'owner' && ownerCount <= 1;
            const canManage = manages && (workspace.role === 'owner' || member.role !== 'owner');
            const roles: WorkspaceRole[] = workspace.role === 'owner' ? ['owner', 'admin', 'member', 'guest'] : ['admin', 'member', 'guest'];
            return <li key={member.user_id}><span className="fc-workspace-person-avatar" aria-hidden="true">{member.name.slice(0, 1).toUpperCase()}</span><div className="fc-workspace-person-copy"><strong>{member.name}{current ? t(" (you)") : ''}</strong>{canManage ? <label className="fc-workspace-role"><span className="fc-visually-hidden">{t("Role for ")}{member.name}</span><select value={member.role} disabled={!!busy || lastOwner} onChange={event => { const selected = event.target.value as WorkspaceRole; void run(member.user_id, async () => { await workspaceApi.setRole(workspace.id, member.user_id, selected); if (!isCurrent()) return; await load(); await onChanged(); if (isCurrent()) setNotice(t("Workspace role updated.")); }); }}>{roles.map(value => <option value={value} key={value}>{roleName(value)}</option>)}</select></label> : <span className="fc-account-muted">{roleName(member.role)}</span>}{lastOwner && current && <small>{t("Make someone else an owner before leaving.")}</small>}{removing?.user_id === member.user_id && <div className="fc-account-inline-confirm"><p>{t("Remove ")}{member.name} {t(" from this workspace?")}</p><div className="fc-account-actions"><button className="fc-button" type="button" disabled={!!busy} onClick={() => setRemoving(null)}>{t("Cancel")}</button><button className="fc-button" type="button" disabled={!!busy} onClick={() => void run('remove', async () => { await workspaceApi.removeMember(workspace.id, member.user_id); if (!isCurrent()) return; setRemoving(null); await load(); await onChanged(); if (isCurrent()) setNotice(t("Workspace access removed.")); })}>{t("Remove access")}</button></div></div>}</div>{canManage && !current && <button type="button" className="fc-icon-button fc-account-danger" aria-label={t("Remove {value0} from workspace", { value0: String(member.name) })} disabled={!!busy || lastOwner} onClick={() => setRemoving(member)}><Trash2 size={15} /></button>}</li>;
          })}{!members.length && <li className="fc-account-muted">{t("No matching people are visible in this workspace.")}</li>}</ul>}</section>}
          {section === 'invites' && manages && <>
            <section className="fc-account-section"><span className="fc-kicker">{t("Bring your people")}</span><h3>{t("Invite someone")}</h3><p className="fc-account-muted">{t("Create a single-use link that expires in 24 hours. A guest only sees channels they are explicitly invited to.")}</p><div className="fc-account-form"><label htmlFor="workspace-invite-role">{t("Workspace role")}</label><select className="fc-input" id="workspace-invite-role" value={role} disabled={!!busy} onChange={event => setRole(event.target.value as typeof role)}><option value="member">{t("Member")}</option><option value="guest">{t("Guest")}</option>{workspace.role === 'owner' && <option value="admin">{t("Admin")}</option>}</select><button className="fc-button fc-button-primary" type="button" disabled={!!busy} onClick={() => void createInvite()}>{busy === 'invite' ? <Loader2 size={15} className="fc-account-spinner" /> : <Link2 size={15} />}{t("Create workspace invitation")}</button></div>{inviteLink && <div className="fc-workspace-invite-link"><label htmlFor="workspace-invite-link">{t("Workspace invitation link")}</label><input className="fc-input" id="workspace-invite-link" value={inviteLink} readOnly onFocus={event => event.currentTarget.select()} /><button className="fc-button" type="button" onClick={() => void copyInvite()}><Copy size={15} />{t("Copy invitation")}</button></div>}</section>
            <section className="fc-account-section"><div className="fc-account-section-heading"><h3>{t("Pending invitations")}</h3><button className="fc-icon-button" type="button" aria-label={t("Refresh workspace invitations")} disabled={loading || !!busy} onClick={() => void load()}><RefreshCw size={16} /></button></div>{loading ? <p className="fc-account-loading" role="status"><Loader2 size={17} className="fc-account-spinner" />{t("Loading invitations…")}</p> : activeInvites.length ? <ul className="fc-workspace-invite-list">{activeInvites.map(invite => <li key={invite.id}><div><strong>{roleName(invite.role)} {t(" invitation")}</strong><span>{t("Expires ")}{new Date(invite.expires_at).toLocaleString(currentLocale())}</span></div><button type="button" className="fc-account-link fc-account-danger" disabled={!!busy} onClick={() => void run(invite.id, async () => { await workspaceApi.revokeInvite(workspace.id, invite.id); if (!isCurrent()) return; setInvites(previous => previous.filter(record => record.id !== invite.id)); setNotice(t("Invitation revoked.")); })}>{t("Revoke")}</button></li>)}</ul> : <p className="fc-account-muted">{t("No pending invitations. New links will appear here.")}</p>}</section>
          </>}
        </>}
      </div><footer className="fc-account-footer"><Check size={15} /><span>{t("Personal appearance stays your own.")}</span><button className="fc-button" type="button" onClick={onClose}>{t("Done")}</button></footer>
    </section>
  </div>, document.body);
}
