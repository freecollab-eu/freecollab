import { useEffect, useRef, useState } from 'react';
import { Loader2, Plus, RefreshCw, Save, Trash2, Users, X } from 'lucide-react';
import { api, type ChannelMember } from '../lib/api';
import { t, useI18n } from '../i18n';
import { messageReferencesApi, type NotificationGroup } from '../lib/message-references-api';
import { registerConversationNavigationGuard } from '../lib/conversation-navigation';
import '../styles/message-references.css';

export interface ReferenceGroupsPanelProps {
  channelId: string; workspaceId?: string; canManage: boolean; onClose: () => void; onChanged?: () => void;
  initialDraft?: { key: string; name: string; memberIds?: string[] };
  initialGroupId?: string;
}
type Editor = { id?: string; name: string; members: string[]; previous?: NotificationGroup };
const describe = (failure: unknown) => failure instanceof Error ? failure.message : 'The group could not be saved. Try again.';
const equalMembers = (left: string[], right: string[]) => JSON.stringify([...left].sort()) === JSON.stringify([...right].sort());

/** The group's roster is an explicit notification audience, never an access grant. */
export function ReferenceGroupsPanel({ channelId, workspaceId, canManage, onClose, onChanged, initialDraft, initialGroupId }: ReferenceGroupsPanelProps) {
  useI18n();
  const currentToken = api.getAccessToken(), currentActor = api.getCurrentUserId();
  const [groups, setGroups] = useState<NotificationGroup[]>([]), [members, setMembers] = useState<ChannelMember[]>([]);
  const [editor, setEditor] = useState<Editor | null>(null), [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const generation = useRef(0), owner = useRef({ token: api.getAccessToken(), actor: api.getCurrentUserId(), channelId, workspaceId });
  const busyRef = useRef(false), editorName = useRef<HTMLInputElement>(null);
  const appliedDraft = useRef(''), appliedGroupTarget = useRef('');
  const dirty = !!editor && (editor.name !== (editor.previous?.name || '') || !equalMembers(editor.members, editor.previous?.member_ids || []));
  const mayLeave = useRef<() => boolean>(() => true);
  mayLeave.current = () => !busyRef.current && (!dirty || window.confirm(t('Changes have not saved yet. Leaving may discard them. Leave anyway?')));
  const current = (request: number) => generation.current === request && owner.current.token === api.getAccessToken() && owner.current.actor === api.getCurrentUserId()
    && owner.current.channelId === channelId && owner.current.workspaceId === workspaceId;
  useEffect(() => {
    if (!workspaceId) return;
    return registerConversationNavigationGuard({ userId: api.getCurrentUserId(), credential: api.getAccessToken(), workspaceId }, () => mayLeave.current());
  }, [workspaceId, channelId, currentToken, currentActor]);
  useEffect(() => {
    const abort = new AbortController(); const request = ++generation.current;
    owner.current = { token: api.getAccessToken(), actor: api.getCurrentUserId(), channelId, workspaceId };
    busyRef.current = false; setBusy(false); setGroups([]); setMembers([]); setEditor(null); setError(''); setNotice(''); setLoading(true); appliedDraft.current = ''; appliedGroupTarget.current = '';
    const clear = () => { ++generation.current; abort.abort(); setGroups([]); setMembers([]); setEditor(null); };
    window.addEventListener('freecollab:auth-changed', clear); window.addEventListener('freecollab:workspace-changed', clear);
    void Promise.all([messageReferencesApi.groups(channelId, abort.signal), api.getChannelMembers(channelId)]).then(([rows, people]) => {
      if (!current(request)) return;
      setGroups(rows); setMembers(people);
    }).catch(failure => { if (current(request)) setError(describe(failure)); }).finally(() => { if (current(request)) setLoading(false); });
    return () => { ++generation.current; abort.abort(); window.removeEventListener('freecollab:auth-changed', clear); window.removeEventListener('freecollab:workspace-changed', clear); };
  }, [channelId, workspaceId, currentToken, currentActor]);
  useEffect(() => {
    if (loading || !initialGroupId || appliedGroupTarget.current === initialGroupId) return;
    appliedGroupTarget.current = initialGroupId;
    const target = groups.find(row => row.id === initialGroupId);
    if (target) { setEditor({ id: target.id, name: target.name, members: [...target.member_ids], previous: target }); setNotice(''); }
    else { setEditor(null); setNotice('This group is no longer available.'); }
  }, [initialGroupId, loading, groups]);
  useEffect(() => {
    if (!canManage || loading || initialGroupId || !initialDraft?.key || appliedDraft.current === initialDraft.key) return;
    appliedDraft.current = initialDraft.key;
    setEditor({ name: initialDraft.name.slice(0, 80), members: [...new Set(initialDraft.memberIds || [])].filter(id => members.some(member => member.user_id === id)).slice(0, 50) });
    requestAnimationFrame(() => editorName.current?.focus({ preventScroll: true }));
  }, [initialDraft?.key, initialGroupId, loading, canManage, members]);
  const changed = () => { onChanged?.(); window.dispatchEvent(new CustomEvent('freecollab:references-changed', { detail: { channelId } })); };
  const choose = (group?: NotificationGroup) => {
    if (!mayLeave.current()) return;
    setError(''); setNotice('');
    setEditor(group ? { id: group.id, name: group.name, members: [...group.member_ids], previous: group } : { name: '', members: [] });
    requestAnimationFrame(() => editorName.current?.focus({ preventScroll: true }));
  };
  const close = () => { if (mayLeave.current()) onClose(); };
  const refresh = async () => {
    if (!mayLeave.current()) return;
    const request = generation.current; setLoading(true); setError('');
    try {
      const [rows, people] = await Promise.all([messageReferencesApi.groups(channelId), api.getChannelMembers(channelId)]);
      if (current(request)) { setGroups(rows); setMembers(people); setEditor(null); }
    } catch (failure) { if (current(request)) setError(describe(failure)); }
    finally { if (current(request)) setLoading(false); }
  };
  const save = async () => {
    if (!canManage || !editor || busyRef.current || !editor.name.trim() || !editor.members.length) return;
    const request = generation.current; busyRef.current = true; setBusy(true); setError(''); setNotice('');
    const input = { name: editor.name.trim(), member_ids: [...editor.members] };
    try {
      const row = editor.previous ? await messageReferencesApi.updateGroup(channelId, editor.previous.id, { ...input, expected_version: editor.previous.version }) : await messageReferencesApi.createGroup(channelId, input);
      if (!current(request)) return;
      setGroups(previous => [...previous.filter(item => item.id !== row.id), row]);
      setEditor({ id: row.id, name: row.name, members: [...row.member_ids], previous: row }); setNotice('Group saved.'); changed();
    } catch (failure) { if (current(request)) setError(describe(failure)); }
    finally { if (current(request)) { busyRef.current = false; setBusy(false); } }
  };
  const remove = async () => {
    if (!canManage || !editor?.previous || busyRef.current || dirty || !window.confirm(t('Delete this group? Existing messages remain, and the group will no longer receive mentions.'))) return;
    const request = generation.current, previous = editor.previous; busyRef.current = true; setBusy(true); setError('');
    try {
      await messageReferencesApi.removeGroup(channelId, previous.id, previous.version);
      if (current(request)) { setGroups(rows => rows.filter(row => row.id !== previous.id)); setEditor(null); setNotice('Group deleted.'); changed(); }
    } catch (failure) { if (current(request)) setError(describe(failure)); }
    finally { if (current(request)) { busyRef.current = false; setBusy(false); } }
  };
  if (owner.current.token !== currentToken || owner.current.actor !== currentActor || owner.current.channelId !== channelId || owner.current.workspaceId !== workspaceId)
    return <section className="fc-reference-groups" role="region" aria-label={t('Groups in this conversation')}><p role="status">{t('Loading groups…')}</p></section>;
  return <section className="fc-reference-groups" role="region" aria-label={t('Groups in this conversation')} onKeyDown={event => { if (event.key === 'Escape' && !event.nativeEvent.isComposing) { event.preventDefault(); event.stopPropagation(); close(); } }}>
    <header><div><span className="fc-kicker">{t('The people you mean')}</span><h2>{t('Groups in this conversation')}</h2></div><button type="button" className="fc-icon-button" aria-label={t('Close groups')} onClick={close}><X size={20} /></button></header>
    <p className="fc-reference-groups-explanation">{t('Mentioning a group notifies its current members in this conversation. It does not add people or grant access.')}</p>
    {error && <p className="fc-alert" role="alert">{t(error)}</p>}{notice && <p role="status" className="fc-reference-list-status">{t(notice)}</p>}
    {loading ? <p className="fc-reference-list-status" role="status"><Loader2 size={15} className="fc-account-spinner" />{t('Loading groups…')}</p> : <>
      <div className="fc-reference-groups-list" aria-label={t('Current groups')}>{groups.map(group => <button type="button" key={group.id} disabled={busy} aria-pressed={editor?.id === group.id} data-reference-group-id={group.id} onClick={() => choose(group)}><Users size={16} aria-hidden="true" /><span><strong>{group.name}</strong><small>{t('{count} current members', { count: group.member_ids.length })}</small></span></button>)}</div>
      {!groups.length && <p className="fc-reference-list-status">{t('No groups yet.')}</p>}
      {!canManage && <p className="fc-reference-list-status">{t('A channel administrator can create and change groups.')}</p>}
      {editor && (canManage ? <form className="fc-reference-group-editor" onSubmit={event => { event.preventDefault(); void save(); }}><fieldset disabled={busy}>
        <label>{t('Group name')}<input ref={editorName} value={editor.name} maxLength={80} required onChange={event => setEditor(previous => previous ? { ...previous, name: event.target.value } : previous)} /></label>
        <fieldset className="fc-reference-group-members"><legend>{t('Group members')}</legend>{members.map(member => {
          const duplicate = members.some(other => other.user_id !== member.user_id && other.name === member.name);
          return <label key={member.user_id}><input type="checkbox" data-reference-member-id={member.user_id} checked={editor.members.includes(member.user_id)} disabled={!editor.members.includes(member.user_id) && editor.members.length >= 50}
            onChange={event => setEditor(previous => previous ? { ...previous, members: event.target.checked ? [...previous.members, member.user_id] : previous.members.filter(id => id !== member.user_id) } : previous)} /><span>{member.name}{duplicate && <small>{t('Account {id}', { id: member.user_id.slice(-8) })}</small>}</span></label>;
        })}</fieldset>
        <div className="fc-reference-group-editor-actions"><button type="submit" className="fc-button fc-button-primary" disabled={!editor.name.trim() || !editor.members.length || !dirty}><Save size={15} />{editor.previous ? t('Save group') : t('Create group')}</button><button type="button" className="fc-button" onClick={() => { if (mayLeave.current()) setEditor(null); }}>{t('Cancel')}</button>{editor.previous && <button type="button" className="fc-chat-text-button" disabled={dirty} onClick={() => void remove()}><Trash2 size={15} />{t('Delete group')}</button>}</div>
      </fieldset></form> : <div className="fc-reference-group-readonly"><h3>{editor.name}</h3><ul>{members.filter(member => editor.members.includes(member.user_id)).map(member => <li key={member.user_id}>{member.name}</li>)}</ul></div>)}
    </>}
    <footer>{canManage && <button type="button" className="fc-chat-text-button" disabled={loading || busy} onClick={() => choose()}><Plus size={15} />{t('New group')}</button>}<button type="button" className="fc-chat-text-button" disabled={loading || busy} onClick={() => void refresh()}><RefreshCw size={14} />{t('Refresh groups')}</button></footer>
  </section>;
}
