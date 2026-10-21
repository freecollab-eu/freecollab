import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Globe2, RefreshCw, ShieldCheck, Unplug, UserPlus } from 'lucide-react';
import { t, useI18n } from '../i18n';
import { api } from '../lib/api';
import { federationApi, type FederationConfiguration, type FederatedConnection, type FederatedMember } from '../lib/connect-federation-api';
import { workspaceApi, type WorkspaceMember } from '../lib/workspace-api';

interface Props { workspaceId: string; onChanged?: () => void; onGuardChange?: (guard: 'busy' | 'secret' | null) => void }
export function FederatedConnectPanel({ workspaceId, onChanged, onGuardChange }: Props) {
  useI18n();
  const [config, setConfig] = useState<FederationConfiguration | null>(null);
  const [connections, setConnections] = useState<FederatedConnection[]>([]);
  const [people, setPeople] = useState<WorkspaceMember[]>([]);
  const [members, setMembers] = useState<FederatedMember[]>([]);
  const [selected, setSelected] = useState(''), [person, setPerson] = useState('');
  const [peer, setPeer] = useState(''), [target, setTarget] = useState(''), [name, setName] = useState('');
  const [acceptPeer, setAcceptPeer] = useState(''), [token, setToken] = useState(''), [issued, setIssued] = useState('');
  const [busy, setBusy] = useState(false), [loading, setLoading] = useState(true), [detailsLoading, setDetailsLoading] = useState(false);
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [confirm, setConfirm] = useState(false);
  const epoch = useRef(0), memberEpoch = useRef(0), mounted = useRef(false), pending = useRef(false);
  const owner = useRef(api.getAccessToken()), workspace = useRef(workspaceId);
  const callbacks = useRef({ onChanged, onGuardChange }); callbacks.current = { onChanged, onGuardChange };
  const current = () => mounted.current && owner.current === api.getAccessToken() && workspace.current === workspaceId;
  const chosen = connections.find(item => item.id === selected);
  useEffect(() => { callbacks.current.onGuardChange?.(busy ? 'busy' : issued ? 'secret' : null); }, [busy, issued]);
  useEffect(() => {
    mounted.current = true; owner.current = api.getAccessToken(); workspace.current = workspaceId; epoch.current++;
    const clear = () => {
      epoch.current++; memberEpoch.current++; pending.current = false;
      setConfig(null); setConnections([]); setPeople([]); setMembers([]); setSelected(''); setPerson('');
      setPeer(''); setTarget(''); setName(''); setAcceptPeer(''); setToken(''); setIssued('');
      setError(''); setNotice(''); setBusy(false); setLoading(false); setDetailsLoading(false); setConfirm(false);
      callbacks.current.onGuardChange?.(null);
    };
    clear(); setLoading(true); void load();
    window.addEventListener('freecollab:auth-changed', clear);
    return () => { mounted.current = false; epoch.current++; memberEpoch.current++; window.removeEventListener('freecollab:auth-changed', clear); callbacks.current.onGuardChange?.(null); };
  }, [workspaceId]);
  useEffect(() => { setPerson(''); setConfirm(false); if (selected) void loadMembers(selected); else setMembers([]); }, [selected]);
  async function load() {
    const version = ++epoch.current; setLoading(true); setError('');
    const results = await Promise.allSettled([federationApi.configuration(workspaceId), federationApi.list(workspaceId), workspaceApi.members(workspaceId)]);
    if (!current() || version !== epoch.current) return;
    const [configuration, rows, users] = results;
    if (configuration.status === 'fulfilled') setConfig(configuration.value); else setError(failure(configuration.reason));
    if (rows.status === 'fulfilled') { setConnections(rows.value); setSelected(old => rows.value.some(row => row.id === old) ? old : rows.value[0]?.id || ''); } else setError(failure(rows.reason));
    if (users.status === 'fulfilled') setPeople(users.value); else setError(failure(users.reason));
    setLoading(false);
  }
  function failure(value: unknown) { return value instanceof Error ? value.message : t('The shared channel could not be updated.'); }
  async function loadMembers(id: string) {
    const version = ++memberEpoch.current; setDetailsLoading(true); setMembers([]);
    try { const rows = await federationApi.members(workspaceId, id); if (current() && version === memberEpoch.current) setMembers(rows); }
    catch (reason) { if (current() && version === memberEpoch.current) setError(failure(reason)); }
    finally { if (current() && version === memberEpoch.current) setDetailsLoading(false); }
  }
  async function act(action: () => Promise<void>) {
    if (!current() || pending.current) return;
    pending.current = true; setBusy(true); setError(''); setNotice('');
    try { await action(); } catch (reason) { if (current()) setError(failure(reason)); }
    finally { if (current()) { pending.current = false; setBusy(false); } }
  }
  function remember(row: FederatedConnection) { setConnections(old => [row, ...old.filter(item => item.id !== row.id)]); setSelected(row.id); callbacks.current.onChanged?.(); }
  function offer(event: FormEvent) {
    event.preventDefault();
    void act(async () => {
      const row = await federationApi.offer(workspaceId, name.trim(), peer, target.trim());
      if (!current()) return;
      remember(row); setIssued(row.invitation_token); setName(''); setTarget('');
      setNotice(t('Send the invitation privately to the administrator of the pinned installation.'));
    });
  }
  function accept(event: FormEvent) {
    event.preventDefault();
    void act(async () => { const row = await federationApi.accept(workspaceId, acceptPeer, token.trim()); if (current()) { remember(row); setToken(''); setNotice(t('Connection accepted. Choose the people from your organization who may join.')); } });
  }
  const blocked = busy || Boolean(issued);
  return <section className="fc-connect" aria-label={t('Across installations')}>
    <div className="fc-connect-intro"><span className="fc-connect-symbol"><Globe2 size={23} /></span><div><span className="fc-kicker">FreeCollab Connect</span><h3>{t('Your installation. A shared conversation.')}</h3><p>{t('Connect independent FreeCollab installations. Each organization keeps its own identity provider, administrators and encrypted devices.')}</p></div></div>
    <p className="fc-connect-scope"><ShieldCheck size={16} /><span>{t('Both operators pin installation keys and enable Matrix federation. Both workspace administrators approve, then grant their own participants. Verify remote devices before sending.')}</span></p>
    {error && <p className="fc-alert fc-account-error" role="alert">{error}</p>}
    {notice && <p className="fc-alert fc-account-notice" role="status">{notice}</p>}
    {loading ? <p role="status">{t('Loading shared channels…')}</p> : !config?.enabled ? <div className="fc-connect-details"><h4>{t('Operator setup required')}</h4><p>{t('Configure this installation’s public HTTPS address, retain its signing identity, pin the partner’s public key and Matrix domain, and enable federation on both homeservers.')}</p><p className="fc-account-muted">{t('Open docs/CONNECT_FEDERATION.md in your installation source for the setup and verification steps.')}</p></div> : <>
      <details className="fc-connect-details"><summary>{t('Installation identity')}</summary><p>{config.origin}</p><label htmlFor="federation-public-key">{t('Public signing key')}</label><input id="federation-public-key" className="fc-input" readOnly value={config.public_key} onFocus={event => event.currentTarget.select()} /><p className="fc-account-muted">{t('Compare this public key through a separate trusted channel. It is not an identity-provider credential.')}</p><p>{t('Matrix server')}: {config.matrix_server_name}</p></details>
      {issued && <section className="fc-connect-token"><h4>{t('Your invitation is ready.')}</h4><p>{t('The invitation is targeted to the pinned installation and workspace. It expires after 24 hours and never grants a participant seat by itself.')}</p><label htmlFor="federation-issued">{t('Invitation token')}</label><input id="federation-issued" className="fc-input" readOnly autoComplete="off" value={issued} onFocus={event => event.currentTarget.select()} /><button type="button" className="fc-button" onClick={() => { setIssued(''); setNotice(''); }}>{t('I have stored the invitation')}</button></section>}
      <div className="fc-connect-forms">
        <form className="fc-connect-form" onSubmit={offer}><h4>{t('Invite another installation')}</h4><label htmlFor="federation-peer">{t('Trusted partner installation')}</label><select id="federation-peer" className="fc-input" value={peer} onChange={event => setPeer(event.target.value)} required disabled={blocked}><option value="">{t('Choose an installation')}</option>{config.trusted_origins.map(url => <option key={url}>{url}</option>)}</select><label htmlFor="federation-name">{t('Shared channel name')}</label><input id="federation-name" className="fc-input" value={name} onChange={event => setName(event.target.value)} maxLength={100} required disabled={blocked} /><label htmlFor="federation-workspace">{t('Partner workspace ID')}</label><input id="federation-workspace" className="fc-input" value={target} onChange={event => setTarget(event.target.value)} maxLength={100} required disabled={blocked} /><button type="submit" className="fc-button fc-button-primary" disabled={blocked || !peer || !name.trim() || !target.trim()}>{t('Create invitation')}</button></form>
        <form className="fc-connect-form" onSubmit={accept}><h4>{t('Accept an invitation')}</h4><label htmlFor="federation-accept-peer">{t('Inviting installation')}</label><select id="federation-accept-peer" className="fc-input" value={acceptPeer} onChange={event => setAcceptPeer(event.target.value)} required disabled={blocked}><option value="">{t('Choose an installation')}</option>{config.trusted_origins.map(url => <option key={url}>{url}</option>)}</select><label htmlFor="federation-token">{t('Invitation token')}</label><textarea id="federation-token" className="fc-input" value={token} onChange={event => setToken(event.target.value)} maxLength={128} autoComplete="off" rows={3} required disabled={blocked} /><button type="submit" className="fc-button" disabled={blocked || !acceptPeer || !token.trim()}>{t('Accept connection')}</button></form>
      </div>
    </>}
    <div className="fc-connect-heading"><h4>{t('Organization connections')}</h4><button type="button" className="fc-button" disabled={blocked || loading} onClick={() => { void load(); if (selected) void loadMembers(selected); }}><RefreshCw size={14} />{t('Refresh')}</button></div>
    {!loading && !connections.length && <p className="fc-connect-empty">{t('No connections across installations yet.')}</p>}
    {!!connections.length && <div className="fc-connect-directory"><nav aria-label={t('Federated connections')}>{connections.map(row => <button type="button" key={row.id} aria-pressed={selected === row.id} disabled={blocked} onClick={() => setSelected(row.id)}><span><strong>{row.name}</strong><small>{row.peer_origin}</small></span><span className="fc-badge">{row.status === 'active' ? t('Active') : row.status === 'revoked' ? t('Revoked') : t('Awaiting acceptance')}</span></button>)}</nav>
      {chosen && <section className="fc-connect-details"><h4>{chosen.name}</h4><p>{chosen.peer_origin} · {chosen.peer_workspace_id}</p><p className="fc-account-muted">{t('Only explicitly granted local participants can open this channel. Remote users remain accounts of their own installation.')}</p><p>{t('End-to-end encrypted channel')}</p>
        {detailsLoading ? <p role="status">{t('Loading participants…')}</p> : <ul className="fc-connect-members">{members.filter(row => row.active).map(row => <li key={row.user_id}><span>{row.name}</span><button type="button" className="fc-button" disabled={blocked || chosen.status === 'revoked'} onClick={() => { void act(async () => { await federationApi.remove(workspaceId, chosen.id, row.user_id); if (current()) { await loadMembers(chosen.id); callbacks.current.onChanged?.(); } }); }}>{t('Remove')}</button></li>)}</ul>}
        {chosen.status === 'active' && <><form className="fc-connect-grant" onSubmit={event => { event.preventDefault(); void act(async () => { await federationApi.grant(workspaceId, chosen.id, person); if (current()) { await loadMembers(chosen.id); setPerson(''); callbacks.current.onChanged?.(); } }); }}><label htmlFor="federation-person">{t('Add someone from your organization')}</label><select id="federation-person" className="fc-input" value={person} onChange={event => setPerson(event.target.value)} disabled={blocked || detailsLoading}><option value="">{t('Choose a person')}</option>{people.filter(row => !members.some(member => member.active && member.user_id === row.user_id)).map(row => <option key={row.user_id} value={row.user_id}>{row.name}</option>)}</select><button type="submit" className="fc-button" disabled={blocked || !person}><UserPlus size={15} />{t('Grant channel access')}</button></form><button type="button" className="fc-button" disabled={blocked || !config?.enabled} onClick={() => { void act(async () => { const row = await federationApi.sync(workspaceId, chosen.id); if (current()) { remember(row); setNotice(t('Encrypted membership synchronized.')); } }); }}><RefreshCw size={14} />{t('Retry encrypted synchronization')}</button></>}
        {chosen.status === 'revoked' && <button type="button" className="fc-button" disabled={blocked} onClick={() => { void act(async () => { const result = await federationApi.revoke(workspaceId, chosen.id); if (current()) { remember(result); setNotice(result.remote_cleanup_pending ? t('Local access ended. The peer or Matrix service is unavailable; retry revocation to complete remote cleanup.') : t('Remote cleanup completed.')); } }); }}><RefreshCw size={14} />{t('Retry remote cleanup')}</button>}
        {chosen.status !== 'revoked' && <div className="fc-connect-revoke">{confirm ? <><p>{t('End shared access for both organizations? Participants immediately lose access to this shared channel. Previously downloaded content cannot be recalled.')}</p><button type="button" className="fc-button" disabled={blocked} onClick={() => { void act(async () => { const result = await federationApi.revoke(workspaceId, chosen.id); if (current()) { remember(result); setConfirm(false); setNotice(result.remote_cleanup_pending ? t('Local access ended. The peer or Matrix service is unavailable; retry revocation to complete remote cleanup.') : t('Shared channel access has ended for both organizations.')); } }); }}><Unplug size={14} />{t('End shared access')}</button><button type="button" className="fc-button" disabled={blocked} onClick={() => setConfirm(false)}>{t('Cancel')}</button></> : <button type="button" className="fc-button" disabled={blocked} onClick={() => setConfirm(true)}><Unplug size={14} />{t('Revoke connection')}</button>}</div>}
      </section>}
    </div>}
  </section>;
}
