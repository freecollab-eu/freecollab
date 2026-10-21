import { useEffect, useRef, useState, type FormEvent } from 'react';
import { AlertCircle, ArrowRight, Check, Link2, Loader2, RefreshCw, ShieldCheck, Unplug, UserPlus, Users } from 'lucide-react';
import { t, useI18n } from '../i18n';
import { api } from '../lib/api';
import { connectApi, type ConnectCapabilities, type SharedConnection, type SharedMember } from '../lib/connect-api';
import { workspaceApi, type WorkspaceMember } from '../lib/workspace-api';
import '../styles/connect.css';

interface Props {
  workspaceId: string;
  onChanged?: () => void;
  onGuardChange?: (guard: 'busy' | 'secret' | null) => void;
}
const failureText = (value: unknown) => value instanceof Error ? value.message : t('The shared channel could not be updated.');

export function ConnectPanel({ workspaceId, onChanged, onGuardChange }: Props) {
  useI18n();
  const [connections, setConnections] = useState<SharedConnection[]>([]);
  const [people, setPeople] = useState<WorkspaceMember[]>([]);
  const [grants, setGrants] = useState<SharedMember[]>([]);
  const [capabilities, setCapabilities] = useState<ConnectCapabilities | null>(null);
  const [selectedId, setSelectedId] = useState('');
  const [memberId, setMemberId] = useState('');
  const [name, setName] = useState('');
  const [targetId, setTargetId] = useState('');
  const [acceptToken, setAcceptToken] = useState('');
  const [issuedToken, setIssuedToken] = useState('');
  const [issuedConnection, setIssuedConnection] = useState('');
  const [confirmRevoke, setConfirmRevoke] = useState(false);
  const [loading, setLoading] = useState(true);
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const mounted = useRef(true);
  const currentWorkspace = useRef(workspaceId);
  const owner = useRef(api.getAccessToken());
  const identity = useRef(api.getCurrentUserId());
  const generation = useRef(0);
  const detailGeneration = useRef(0);
  const actionPending = useRef(false);
  const callbacks = useRef({ onChanged, onGuardChange }); callbacks.current = { onChanged, onGuardChange };
  const selected = connections.find(value => value.id === selectedId);
  const current = () => mounted.current && currentWorkspace.current === workspaceId && owner.current === api.getAccessToken() && identity.current === api.getCurrentUserId();
  const available = people.filter(person => !grants.some(grant => grant.active && grant.user_id === person.user_id));

  useEffect(() => {
    mounted.current = true; currentWorkspace.current = workspaceId; owner.current = api.getAccessToken(); identity.current = api.getCurrentUserId();
    generation.current++; detailGeneration.current++; actionPending.current = false;
    setConnections([]); setPeople([]); setGrants([]); setCapabilities(null); setSelectedId(''); setMemberId(''); setName(''); setTargetId('');
    setAcceptToken(''); setIssuedToken(''); setIssuedConnection(''); setConfirmRevoke(false); setBusy(false); setError(''); setNotice('');
    void load();
    return () => { mounted.current = false; generation.current++; detailGeneration.current++; callbacks.current.onGuardChange?.(null); };
  }, [workspaceId]);
  useEffect(() => {
    callbacks.current.onGuardChange?.(busy ? 'busy' : issuedToken ? 'secret' : null);
  }, [busy, issuedToken]);
  useEffect(() => {
    if (selectedId) void loadMembers(selectedId);
    else { detailGeneration.current++; setGrants([]); setDetailsLoading(false); }
    setMemberId(''); setConfirmRevoke(false);
  }, [workspaceId, selectedId]);
  useEffect(() => {
    const clear = () => {
      generation.current++; detailGeneration.current++; actionPending.current = false;
      setConnections([]); setPeople([]); setGrants([]); setCapabilities(null); setSelectedId(''); setMemberId(''); setIssuedToken(''); setIssuedConnection('');
      setAcceptToken(''); setName(''); setTargetId(''); setBusy(false); setLoading(false); setDetailsLoading(false); setConfirmRevoke(false); setError(''); setNotice('');
      callbacks.current.onGuardChange?.(null);
    };
    window.addEventListener('freecollab:auth-changed', clear);
    return () => window.removeEventListener('freecollab:auth-changed', clear);
  }, []);

  async function load() {
    const request = ++generation.current; setLoading(true); setError('');
    const results = await Promise.allSettled([connectApi.list(workspaceId), workspaceApi.members(workspaceId), connectApi.capabilities()]);
    if (!current() || request !== generation.current) return;
    const [shared, members, features] = results;
    if (shared.status === 'fulfilled') {
      setConnections(shared.value);
      setSelectedId(previous => shared.value.some(item => item.id === previous) ? previous : shared.value[0]?.id || '');
    } else setError(failureText(shared.reason));
    if (members.status === 'fulfilled') setPeople(members.value);
    else setError(failureText(members.reason));
    if (features.status === 'fulfilled') setCapabilities(features.value);
    else { setCapabilities(null); setError(failureText(features.reason)); }
    setLoading(false);
  }
  async function loadMembers(id: string) {
    const request = ++detailGeneration.current; setDetailsLoading(true); setGrants([]);
    try {
      const result = await connectApi.members(workspaceId, id);
      if (current() && request === detailGeneration.current) setGrants(result);
    } catch (failure) { if (current() && request === detailGeneration.current) setError(failureText(failure)); }
    finally { if (current() && request === detailGeneration.current) setDetailsLoading(false); }
  }
  async function act(action: () => Promise<void>) {
    if (!current() || actionPending.current) return;
    actionPending.current = true; setBusy(true); setError(''); setNotice('');
    try { await action(); }
    catch (failure) { if (current()) setError(failureText(failure)); }
    finally { if (current()) { actionPending.current = false; setBusy(false); } }
  }
  function remember(result: SharedConnection) {
    setConnections(previous => [result, ...previous.filter(item => item.id !== result.id)]);
    setSelectedId(result.id); callbacks.current.onChanged?.();
  }
  function create(event: FormEvent) {
    event.preventDefault();
    if (!name.trim() || !targetId.trim() || issuedToken || !capabilities?.encryption_configured) return;
    void act(async () => {
      const result = await connectApi.create(workspaceId, name.trim(), targetId.trim());
      if (!current()) return;
      const { invitation_token, ...connection } = result;
      remember(connection); setIssuedToken(invitation_token); setIssuedConnection(connection.id); setName(''); setTargetId('');
      setNotice(t('Send this invitation privately to an administrator of the other workspace.'));
    });
  }
  function accept(event: FormEvent) {
    event.preventDefault(); if (!acceptToken.trim() || issuedToken) return;
    const token = acceptToken.trim();
    void act(async () => {
      const result = await connectApi.accept(workspaceId, token);
      if (!current()) return;
      remember(result); setAcceptToken('');
      setNotice(t('Connection accepted. Choose the people from your organization who may join.'));
    });
  }
  function grant(event: FormEvent) {
    event.preventDefault(); if (!selected || !memberId || issuedToken) return;
    const connectionId = selected.id;
    void act(async () => {
      await connectApi.grant(workspaceId, connectionId, memberId);
      if (!current()) return;
      await loadMembers(connectionId); if (!current()) return;
      setMemberId(''); setNotice(t('Channel access granted.')); callbacks.current.onChanged?.();
    });
  }
  function remove(userId: string) {
    if (!selected || issuedToken) return;
    const connectionId = selected.id;
    void act(async () => {
      await connectApi.remove(workspaceId, connectionId, userId);
      if (!current()) return;
      await loadMembers(connectionId); if (!current()) return;
      setNotice(t('Channel access removed.')); callbacks.current.onChanged?.();
    });
  }
  function revoke() {
    if (!selected || issuedToken || !confirmRevoke) return;
    const connectionId = selected.id;
    void act(async () => {
      const result = await connectApi.revoke(workspaceId, connectionId);
      if (!current()) return;
      setConnections(previous => previous.map(item => item.id === connectionId ? { ...item, ...result, status: 'revoked', revoked_at: result.revoked_at || new Date().toISOString() } : item));
      setConfirmRevoke(false); setGrants([]); setNotice(t('Shared channel access has ended for both organizations.')); callbacks.current.onChanged?.();
    });
  }
  function retry() {
    if (!selected || issuedToken) return;
    const connectionId = selected.id;
    void act(async () => {
      const result = await connectApi.retry(workspaceId, connectionId);
      if (!current()) return;
      remember(result); setNotice(t('Encrypted membership synchronized.'));
    });
  }
  function clearIssuedToken() { setIssuedToken(''); setIssuedConnection(''); setNotice(''); }

  return <section className="fc-connect" aria-label={t('Shared channels')}>
    <div className="fc-connect-intro"><span className="fc-connect-symbol"><Link2 size={23} /></span><div><span className="fc-kicker">FreeCollab Connect</span><h3>{t('A shared space. Your own boundaries.')}</h3><p>{t('Work with another organization while each team keeps its own sign-in, administrators and member access.')}</p></div></div>
    <p className="fc-connect-scope"><ShieldCheck size={16} /><span>{t('Connect workspaces on this FreeCollab installation. Each invitation creates a new empty channel; existing conversation history stays private.')}</span></p>
    {!loading && capabilities && !capabilities.encryption_configured && <p className="fc-alert" role="status">{t('Shared channels require end-to-end encryption. Ask your installation operator to configure Matrix before creating an invitation.')}</p>}
    {error && <div className="fc-alert fc-account-error" role="alert"><AlertCircle size={17} /><p>{error}</p></div>}
    {notice && <p className="fc-alert fc-account-notice" role="status"><Check size={16} />{notice}</p>}
    {issuedToken && <section className="fc-connect-token" aria-label={t('One-time shared channel invitation')}><h4>{t('Your invitation is ready.')}</h4><p>{t('The invitation token is shown once and expires after 24 hours. Only an administrator of the invited workspace can accept it.')}</p><label htmlFor="connect-issued-token">{t('Invitation token')}</label><input id="connect-issued-token" className="fc-input" value={issuedToken} readOnly autoComplete="off" spellCheck={false} onFocus={event => event.currentTarget.select()} /><p className="fc-account-muted">{t('Copy the token and send it through a private channel. It is kept only in this view until you dismiss it.')}</p><div className="fc-connect-actions"><button type="button" className="fc-button fc-button-primary" onClick={clearIssuedToken}>{t('I have stored the invitation')}</button><button type="button" className="fc-button" onClick={clearIssuedToken}>{t('Discard invitation token')}</button></div><small>{t('Connection ID')}: {issuedConnection}</small></section>}
    <div className="fc-connect-forms">
      <form className="fc-connect-form" onSubmit={create}><h4>{t('Invite an organization')}</h4><p>{t('Ask the other administrator for their workspace ID. Shared conversations use end-to-end encryption.')}</p><label htmlFor="connect-channel-name">{t('Shared channel name')}</label><input id="connect-channel-name" className="fc-input" value={name} onChange={event => setName(event.target.value)} maxLength={100} required disabled={busy || Boolean(issuedToken)} /><label htmlFor="connect-target-id">{t('Partner workspace ID')}</label><input id="connect-target-id" className="fc-input" value={targetId} onChange={event => setTargetId(event.target.value)} maxLength={100} autoComplete="off" required disabled={busy || Boolean(issuedToken)} /><button type="submit" className="fc-button fc-button-primary" disabled={busy || loading || Boolean(issuedToken) || !capabilities?.encryption_configured || !name.trim() || !targetId.trim()}><Link2 size={15} />{t('Create invitation')}</button></form>
      <form className="fc-connect-form" onSubmit={accept}><h4>{t('Accept an invitation')}</h4><p>{t('Accepting connects the organizations. People join only when their own administrator grants access.')}</p><label htmlFor="connect-accept-token">{t('Invitation from another organization')}</label><textarea id="connect-accept-token" className="fc-input" value={acceptToken} onChange={event => setAcceptToken(event.target.value)} rows={3} maxLength={256} autoComplete="off" spellCheck={false} required disabled={busy || Boolean(issuedToken)} /><button type="submit" className="fc-button" disabled={busy || loading || Boolean(issuedToken) || !acceptToken.trim()}><ArrowRight size={15} />{t('Accept connection')}</button></form>
    </div>
    <div className="fc-connect-heading"><h4>{t('Organization connections')}</h4><button type="button" className="fc-button" aria-label={t('Refresh shared channels')} onClick={() => { void load(); if (selectedId) void loadMembers(selectedId); }} disabled={busy || loading || Boolean(issuedToken)}><RefreshCw size={14} />{t('Refresh')}</button></div>
    {loading ? <p className="fc-account-loading" role="status"><Loader2 className="fc-account-spinner" size={17} />{t('Loading shared channels…')}</p> : !connections.length ? <p className="fc-connect-empty">{t('No shared channels yet. Start with an invitation, or accept one from a partner.')}</p> : <div className="fc-connect-directory"><nav aria-label={t('Shared channel connections')}>{connections.map(item => <button type="button" key={item.id} aria-pressed={selectedId === item.id} disabled={busy || Boolean(issuedToken)} onClick={() => { setSelectedId(item.id); setError(''); setNotice(''); }}><span><strong>{item.name}</strong><small>{item.source_workspace_id === workspaceId ? item.target_workspace_name : item.source_workspace_name}</small></span><span className="fc-badge">{item.status === 'active' ? t('Active') : item.status === 'revoked' ? t('Revoked') : t('Awaiting acceptance')}</span></button>)}</nav>
      {selected && <section className="fc-connect-details" aria-label={t('Selected shared channel')}><div className="fc-connect-heading"><h4>{selected.name}</h4><Users size={18} /></div><p className="fc-account-muted">{selected.source_workspace_name} <ArrowRight size={12} /> {selected.target_workspace_name}</p><p>{selected.secure ? t('End-to-end encrypted channel') : t('Standard channel — end-to-end encryption is not enabled.')}</p>{selected.secure_sync_pending && <div className="fc-alert" role="status"><p>{t('Encrypted membership synchronization is pending. Retry synchronization before sending encrypted content.')}</p><button type="button" className="fc-button" disabled={busy || Boolean(issuedToken)} onClick={retry}><RefreshCw size={14} />{t('Retry encrypted synchronization')}</button></div>}<p className="fc-account-muted">{t('Manage only your own organization’s participants here. Administrators of the other organization control their own team.')}</p>
        {detailsLoading ? <p role="status">{t('Loading participants…')}</p> : <ul className="fc-connect-members" aria-label={t('Your organization’s channel participants')}>{grants.filter(item => item.active).map(item => <li key={item.user_id}><span>{item.name}</span>{selected.status !== 'revoked' && <button type="button" className="fc-button" disabled={busy || Boolean(issuedToken)} aria-label={`${t('Remove channel access')}: ${item.name}`} onClick={() => remove(item.user_id)}>{t('Remove')}</button>}</li>)}</ul>}
        {selected.status === 'active' && <form className="fc-connect-grant" onSubmit={grant}><label htmlFor="connect-member">{t('Add someone from your organization')}</label><select id="connect-member" className="fc-input" value={memberId} onChange={event => setMemberId(event.target.value)} disabled={busy || detailsLoading || Boolean(issuedToken)}><option value="">{t('Choose a person')}</option>{available.map(person => <option key={person.user_id} value={person.user_id}>{person.name}</option>)}</select><button type="submit" className="fc-button" disabled={!memberId || busy || detailsLoading || Boolean(issuedToken)}><UserPlus size={15} />{t('Grant channel access')}</button></form>}
        {selected.status === 'pending' && <p className="fc-connect-waiting">{t('The partner administrator must accept the invitation before you can add more participants.')}</p>}
        {selected.status !== 'revoked' && <div className="fc-connect-revoke">{confirmRevoke ? <><p>{t('End shared access for both organizations? Participants immediately lose access to this shared channel. Previously downloaded content cannot be recalled.')}</p><div className="fc-connect-actions"><button type="button" className="fc-button" disabled={busy} onClick={revoke}><Unplug size={15} />{t('End shared access')}</button><button type="button" className="fc-button" disabled={busy} onClick={() => setConfirmRevoke(false)}>{t('Cancel')}</button></div></> : <button type="button" className="fc-button" disabled={busy || Boolean(issuedToken)} onClick={() => setConfirmRevoke(true)}><Unplug size={15} />{t('Revoke connection')}</button>}</div>}
      </section>}
    </div>}
  </section>;
}
