import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { CheckSquare, FileText, Play, Plus, RefreshCw, ShieldCheck, Square } from 'lucide-react';
import { t, useI18n } from '../i18n';
import { api } from '../lib/api';
import { EncryptedWorkClient, type EncryptedWorkFields, type EncryptedWorkKind, type EncryptedWorkRecord, type EncryptedWorkSnapshot, type EncryptedReference } from '../lib/encrypted-work-api';
import { getSecureMessaging } from '../lib/secure-client';
import { useCollaborationContext } from '../lib/collaboration-context';
import { EncryptedWorkInput, encryptedReferenceCandidates } from './EncryptedWorkInput';
import { ReviewedResourceCopilot } from './ReviewedResourceCopilot';
import '../styles/federated-work.css';
import '../styles/encrypted-work.css';

interface Props { channelId: string; initialKind?: EncryptedWorkKind; initialResourceId?: string; initialResourceKind?: string; onPendingChange?: (pending: boolean) => void }
const labels: Record<EncryptedWorkKind, string> = { collection: 'Collections', feed: 'Activity feed', decision: 'Decisions', 'linked-file': 'Files and links',
  comment: 'Comments', workflow: 'Workflows', 'workflow-run': 'Workflow runs', task: 'Tasks', project: 'Projects', organization: 'Document organization' };
const selectable: EncryptedWorkKind[] = ['feed', 'project', 'task', 'decision', 'collection', 'linked-file', 'comment', 'workflow', 'workflow-run'];
const empty = (kind: EncryptedWorkKind): EncryptedWorkFields => ({ title: '', body: '',
  ...(kind === 'collection' ? { items: [] } : kind === 'decision' ? { status: 'proposed' } : kind === 'project' ? { color: 'sage' as const }
    : kind === 'task' ? { status: 'todo', priority: 'normal' as const, assignee_matrix_id: null, project_id: null, due_date: null, checklist: [], file_ids: [], decision_ids: [] }
      : kind === 'linked-file' ? { url: '', target_id: '' } : kind === 'comment' ? { target_id: '', resolved: false }
        : kind === 'workflow' ? { steps: [{ id: crypto.randomUUID(), kind: 'task' as const, title: '', body: '' }] } : {}) });
const failure = (reason: unknown) => reason instanceof Error ? reason.message : t('Encrypted work could not be refreshed.');

/** Local and cross-installation work use the same typed, device-encrypted events. */
export function EncryptedWorkPanel({ channelId, initialKind = 'feed', initialResourceId, initialResourceKind, onPendingChange }: Props) {
  useI18n(); const collaboration = useCollaborationContext(), id = useId();
  const [view, setView] = useState<EncryptedWorkSnapshot | null>(null), [kind, setKind] = useState<EncryptedWorkKind>(initialKind);
  const [selected, setSelected] = useState<EncryptedWorkRecord | null>(null), [fields, setFields] = useState<EncryptedWorkFields>(empty(initialKind));
  const [editing, setEditing] = useState(false), [dirty, setDirty] = useState(false), [busy, setBusy] = useState(false), [loading, setLoading] = useState(true);
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [archived, setArchived] = useState(false), [showArchived, setShowArchived] = useState(false);
  const [reviewHead, setReviewHead] = useState(''), [member, setMember] = useState('');
  const client = useRef<EncryptedWorkClient | null>(null), epoch = useRef(0), mounted = useRef(false), pending = useRef(false);
  const owner = useRef({ actor: api.getCurrentUserId(), token: api.getAccessToken() }), creatingId = useRef(crypto.randomUUID());
  const state = useRef({ selected, editing, dirty }); state.current = { selected, editing, dirty };
  const callback = useRef(onPendingChange); callback.current = onPendingChange;
  const pendingInitial = useRef(initialResourceId);
  const current = (generation: number) => mounted.current && generation === epoch.current && owner.current.actor === api.getCurrentUserId() && owner.current.token === api.getAccessToken();
  const clear = () => { setSelected(null); setEditing(false); setDirty(false); setReviewHead(''); setMember(''); setArchived(false); setFields(empty(kind)); };
  useEffect(() => {
    mounted.current = true; owner.current = { actor: api.getCurrentUserId(), token: api.getAccessToken() };
    const generation = ++epoch.current, connection = new EncryptedWorkClient(channelId); client.current = connection;
    setView(null); clear(); setLoading(true); setError(''); setNotice('');
    const read = async () => {
      if (pending.current || !current(generation)) return;
      try {
        const result = await connection.load(); if (!current(generation)) return;
        setView(result); setError('');
        if (state.current.selected && !state.current.editing) {
          const row = result.records.find(row => row.id === state.current.selected!.id);
          if (row) { setSelected(row); setFields(row.selected.fields); setArchived(row.selected.archived); }
          else clear();
        }
      } catch (reason) { if (current(generation)) { setError(failure(reason)); if (!connection.hasCurrentScope()) setView(null); } }
      finally { if (current(generation)) setLoading(false); }
    };
    const invalidate = () => { ++epoch.current; connection.stop(); setView(null); clear(); setBusy(false); setLoading(false); setError(t('The encrypted work account or room changed.')); callback.current?.(false); };
    void read(); const timer = window.setInterval(() => { if (document.visibilityState === 'visible') void read(); }, 15000);
    window.addEventListener('freecollab:auth-changed', invalidate); window.addEventListener('freecollab:session-expired', invalidate);
    return () => { mounted.current = false; ++epoch.current; connection.stop(); window.clearInterval(timer); callback.current?.(false);
      window.removeEventListener('freecollab:auth-changed', invalidate); window.removeEventListener('freecollab:session-expired', invalidate); };
  }, [channelId]);
  useEffect(() => { callback.current?.(dirty || busy); return () => callback.current?.(false); }, [dirty, busy]);
  const mayLeave = () => !dirty || window.confirm(t('Changes have not saved yet. Leaving may discard them. Leave anyway?'));
  useEffect(() => { pendingInitial.current = initialResourceId; }, [initialResourceId, initialResourceKind]);
  useEffect(() => {
    if (!pendingInitial.current || !view) return;
    const row = view.records.find(row => row.id === pendingInitial.current && (!initialResourceKind || row.kind === initialResourceKind));
    if (row) { pendingInitial.current = undefined; choose(row); }
  }, [view, initialResourceId, initialResourceKind]);
  async function act(operation: (connection: EncryptedWorkClient, generation: number) => Promise<void>) {
    const connection = client.current, generation = epoch.current;
    if (!connection || pending.current || !current(generation)) return;
    pending.current = true; setBusy(true); setError(''); setNotice('');
    try { await operation(connection, generation); }
    catch (reason) { if (current(generation)) { setError(failure(reason)); if (!connection.hasCurrentScope()) setView(null); } }
    finally { if (current(generation)) { pending.current = false; setBusy(false); } }
  }
  function choose(row: EncryptedWorkRecord) {
    if (!current(epoch.current) || !view || !client.current?.hasCurrentScope() || busy || !mayLeave()) return;
    pendingInitial.current = undefined;
    setKind(row.kind); setSelected(row); setFields(row.selected.fields); setArchived(row.selected.archived); setEditing(false); setDirty(false); setReviewHead(''); setNotice(''); setMember('');
  }
  function create() {
    if (busy || !mayLeave() || kind === 'workflow-run' || kind === 'organization') return;
    pendingInitial.current = undefined; creatingId.current = crypto.randomUUID(); setSelected(null); setFields(empty(kind)); setArchived(false); setEditing(true); setDirty(false); setReviewHead(''); setNotice('');
  }
  function update(values: Partial<EncryptedWorkFields>) { setFields(previous => ({ ...previous, ...values })); setDirty(true); }
  function text(field: string, value: string, references: EncryptedReference[]) {
    setFields(previous => { const next = { ...(previous.references || {}) }; if (references.length) next[field] = references; else delete next[field];
      return { ...previous, [field]: value, ...(Object.keys(next).length ? { references: next } : { references: undefined }) }; }); setDirty(true);
  }
  const candidates = view ? encryptedReferenceCandidates(view) : [];
  const available = (view?.records || []).filter(row => !row.invalid.length && !row.conflicts && !row.selected.archived && row.kind !== 'organization');
  const records = (view?.records || []).filter(row => row.kind === kind && (showArchived || !row.selected.archived || row.conflicts || row.invalid.length));
  const blocked = busy || !view || !client.current?.hasCurrentScope() || !!selected?.invalid.length;
  const scope = JSON.stringify([id, channelId, selected?.id || creatingId.current]);
  const input = (field: 'title' | 'body', label: string, multiline = true) => <EncryptedWorkInput channelId={channelId} contextId={scope + ':' + field} value={fields[field]}
    references={fields.references?.[field]} candidates={candidates} label={t(label)} multiline={multiline} maxLength={field === 'title' ? 200 : 12000}
    required={field === 'title'} disabled={blocked || kind === 'comment' && !!selected} onChange={(value, references) => text(field, value, references)} />;
  async function refresh() { await act(async (connection, generation) => { const result = await connection.load(); if (current(generation)) setView(result); }); }
  function save(event: FormEvent) {
    event.preventDefault(); if (blocked) return;
    void act(async (connection, generation) => {
      const row = await connection.save(kind, fields, selected || undefined, { id: selected ? undefined : creatingId.current,
        archived, ...(selected?.conflicts && reviewHead ? { mergeParents: selected.heads.map(head => head.event_id) } : {}) });
      if (!current(generation)) return;
      const snapshot = await connection.load(); if (!current(generation)) return;
      setView(snapshot); setSelected(row); setFields(row.selected.fields); setEditing(false); setDirty(false); setReviewHead(''); setNotice(t('Encrypted changes saved.'));
    });
  }
  function reference(reference: EncryptedReference) {
    if (!view || !client.current?.hasCurrentScope()) return;
    if (reference.kind === 'participant') {
      const person = view.context.members.find(member => member.matrix_user_id === reference.id);
      setMember(person ? person.name + ' · ' + person.matrix_user_id : t('This participant is no longer available.')); return;
    }
    if (reference.kind === 'message') { window.dispatchEvent(new CustomEvent('freecollab:open-search-source', {detail:{channel_id:channelId,kind:'message',id:reference.id}})); return; }
    const row = view.records.find(row => row.id === reference.id && row.kind === reference.resource_kind && !row.invalid.length && !row.conflicts && !row.selected.archived);
    if (row) choose(row);
    else {
      const original = view.legacyRecords.find(row => row.id === reference.id && row.kind === reference.resource_kind && !row.invalid.length && !row.conflicts && !row.selected.archived);
      if (original) window.dispatchEvent(new CustomEvent('freecollab:open-encrypted-resource', { detail: { channel_id: channelId, kind: original.kind, id: original.id } }));
      else setNotice(t('This encrypted reference is no longer available.'));
    }
  }
  function content(value: string, references: EncryptedReference[] = []) {
    let offset = 0; const parts = [];
    for (const item of references) { parts.push(value.slice(offset, item.start)); parts.push(<button key={item.start + item.id} type="button" className="fc-encrypted-reference" onClick={() => reference(item)}>{value.slice(item.start, item.end)}</button>); offset = item.end; }
    parts.push(value.slice(offset)); return parts;
  }
  async function execute() {
    if (!selected || selected.kind !== 'workflow') return;
    await act(async (connection, generation) => {
      const run = await connection.startWorkflow(selected); if (!current(generation)) return;
      setKind('workflow-run'); setSelected(run); setFields(run.selected.fields); setEditing(false);
      const result = await connection.resumeWorkflow(run, row => { if (current(generation)) { setSelected(row); setFields(row.selected.fields); } });
      if (!current(generation)) return; const snapshot = await connection.load(); if (!current(generation)) return;
      setView(snapshot); if (result) { setSelected(result); setFields(result.selected.fields); } setNotice(t('The reviewed encrypted workflow steps were completed.'));
    });
  }
  async function resume() {
    if (!selected) return;
    await act(async (connection, generation) => {
      const result = await connection.resumeWorkflow(selected, row => { if (current(generation)) { setSelected(row); setFields(row.selected.fields); } });
      if (!current(generation)) return; const snapshot = await connection.load(); if (!current(generation)) return;
      setView(snapshot); if (result) { setSelected(result); setFields(result.selected.fields); }
    });
  }
  async function stop(status: 'paused' | 'cancelled') {
    if (!selected || selected.kind !== 'workflow-run') return;
    const connection = new EncryptedWorkClient(channelId), generation = epoch.current;
    try {
      const snapshot = await connection.load(), latest = snapshot.records.find(row => row.id === selected.id);
      if (!latest || !current(generation)) return;
      const row = await connection.setRunStatus(latest, status); if (current(generation)) { setSelected(row); setFields(row.selected.fields); setNotice(t(status === 'paused' ? 'Encrypted run paused.' : 'Encrypted run cancelled.')); }
    } catch (reason) { if (current(generation)) setError(failure(reason)); } finally { connection.stop(); }
  }
  async function upload(file: File) {
    await act(async (connection, generation) => {
      await connection.load(); if (!current(generation)) return;
      await getSecureMessaging(); if (!current(generation)) return;
      const { secureFileProvider } = await import('../lib/secure/files');
      const saved = await secureFileProvider.upload(channelId, file, { requestId: creatingId.current + '-file' });
      if (!current(generation)) return; await connection.load(); if (!current(generation)) return;
      setFields(previous => ({ ...previous, title: previous.title || saved.filename, target_id: saved.id })); setDirty(true); setNotice(t('The encrypted file is uploaded. Save its shared link.'));
    });
  }
  async function download() {
    if (!selected?.selected.fields.target_id) return;
    await act(async (connection, generation) => {
      await connection.load(); if (!current(generation)) return;
      const { getVerifiedSecureFileReference, secureFileProvider } = await import('../lib/secure/files');
      const file = await getVerifiedSecureFileReference(channelId, selected.selected.fields.target_id!); if (!file) throw Error(t('This encrypted file is no longer available.'));
      const blob = await secureFileProvider.download(file); if (!current(generation)) return;
      const url = URL.createObjectURL(blob), link = document.createElement('a'); link.href = url; link.download = file.filename; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
  }
  const selectedFields = selected?.selected.fields;
  return <section className="fc-federated-work fc-encrypted-work" aria-label={t('Encrypted collaborative work')}>
    <header className="fc-fw-intro"><ShieldCheck size={24} /><div><span className="fc-kicker">{view?.context.mode === 'federated' ? 'FreeCollab Connect' : t('Verified device encryption')}</span><h3>{t('One room for shared work.')}</h3><p>{t('Content stays encrypted between the current participants’ verified devices.')}</p></div></header>
    {error && <p className="fc-alert fc-account-error" role="alert">{error}</p>}{notice && <p className="fc-account-muted" role="status">{notice}</p>}
    {member && <p role="status" className="fc-fw-scope">{member}</p>}
    <div className="fc-fw-toolbar"><label>{t('Work type')}<select className="fc-input" value={kind} disabled={busy} onChange={event => { if (mayLeave()) { pendingInitial.current = undefined; clear(); setKind(event.target.value as EncryptedWorkKind); setFields(empty(event.target.value as EncryptedWorkKind)); } }}>
      {selectable.map(value => <option key={value} value={value}>{t(labels[value])}</option>)}{kind === 'organization' && <option value="organization">{t(labels.organization)}</option>}</select></label>
      <button type="button" className="fc-button" disabled={busy} onClick={() => void refresh()}><RefreshCw size={15} />{t('Refresh')}</button>
      <button type="button" className="fc-button fc-button-primary" disabled={blocked || kind === 'workflow-run' || kind === 'organization'} onClick={create}><Plus size={15} />{t('New item')}</button></div>
    <label className="fc-fw-toggle"><input type="checkbox" checked={showArchived} onChange={event => setShowArchived(event.target.checked)} />{t('Show archived items')}</label>
    {loading && <p role="status">{t('Loading encrypted shared work…')}</p>}
    {!!view?.warnings.length && <p className="fc-fw-warning" role="status">{view.warnings.map(warning => t(warning)).join(' ')}</p>}
    {view && view.records.some(row => row.kind === 'organization' && row.conflicts) && <button type="button" className="fc-button" onClick={() => { const row = view.records.find(row => row.kind === 'organization' && row.conflicts); if (row) choose(row); }}>{t('Review organization versions')}</button>}
    <div className="fc-fw-layout"><div className="fc-fw-directory" aria-label={t('Encrypted items')}>{records.length ? records.map(row => <button key={row.id} type="button" className="fc-fw-record" aria-pressed={selected?.id === row.id} disabled={busy} onClick={() => choose(row)}><strong>{row.selected.fields.title}</strong><small>{row.selected.fields.status ? t(row.selected.fields.status) : t(labels[row.kind])}</small>{row.conflicts && <span className="fc-badge">{t('Review competing versions')}</span>}{row.invalid.length > 0 && <span className="fc-badge">{t('Incomplete history')}</span>}</button>) : <p className="fc-fw-empty">{t('A clear space for your next shared idea.')}</p>}</div>
      {editing && view ? <form className="fc-fw-editor" onSubmit={save} aria-label={t('Edit encrypted item')}>
        {input('title', 'Title', false)}{kind !== 'organization' && input('body', 'Description')}
        {kind === 'project' && <label>{t('Color')}<select className="fc-input" disabled={blocked} value={fields.color} onChange={event => update({ color: event.target.value as EncryptedWorkFields['color'] })}>{['sage', 'blue', 'sand', 'rose'].map(color => <option key={color} value={color}>{t(color)}</option>)}</select></label>}
        {kind === 'task' && <><div className="fc-fw-grid"><label>{t('Status')}<select className="fc-input" disabled={blocked} value={fields.status} onChange={event => update({ status: event.target.value })}>{['todo', 'doing', 'done'].map(status => <option key={status}>{status}</option>)}</select></label><label>{t('Priority')}<select className="fc-input" disabled={blocked} value={fields.priority} onChange={event => update({ priority: event.target.value as EncryptedWorkFields['priority'] })}>{['low', 'normal', 'high'].map(priority => <option key={priority}>{priority}</option>)}</select></label></div>
          <label>{t('Project')}<select className="fc-input" disabled={blocked} value={fields.project_id || ''} onChange={event => update({ project_id: event.target.value || null })}><option value="">{t('No project')}</option>{available.filter(row => row.kind === 'project').map(row => <option key={row.id} value={row.id}>{row.selected.fields.title}</option>)}</select></label>
          <label>{t('Assigned participant')}<select className="fc-input" disabled={blocked} value={fields.assignee_matrix_id || ''} onChange={event => update({ assignee_matrix_id: event.target.value || null })}><option value="">{t('Unassigned')}</option>{view?.context.members.map(person => <option value={person.matrix_user_id} key={person.matrix_user_id}>{person.name} · {person.matrix_user_id.split(':')[1]}</option>)}</select></label>
          <label>{t('Due date')}<input className="fc-input" type="date" disabled={blocked} value={fields.due_date || ''} onChange={event => update({ due_date: event.target.value || null })} /></label>
          <fieldset className="fc-fw-checklist"><legend>{t('Checklist')}</legend>{fields.checklist?.map(item => <div key={item.id}><input type="checkbox" aria-label={t('Complete checklist item')} disabled={blocked} checked={item.done} onChange={event => update({ checklist: fields.checklist!.map(row => row.id === item.id ? { ...row, done: event.target.checked } : row) })} /><EncryptedWorkInput channelId={channelId} contextId={scope + ':checklist:' + item.id} label={t('Checklist item')} multiline={false} disabled={blocked} maxLength={200} required candidates={candidates} value={item.title} references={fields.references?.['checklist:' + item.id]} onChange={(value, references) => { const next = { ...(fields.references || {}) }; if (references.length) next['checklist:' + item.id] = references; else delete next['checklist:' + item.id]; update({ checklist: fields.checklist!.map(row => row.id === item.id ? { ...row, title: value } : row), references: next }); }} /><button type="button" className="fc-button" disabled={blocked} onClick={() => update({ checklist: fields.checklist!.filter(row => row.id !== item.id) })}>{t('Remove')}</button></div>)}<button type="button" className="fc-button" disabled={blocked || (fields.checklist?.length || 0) >= 40} onClick={() => update({ checklist: [...(fields.checklist || []), { id: crypto.randomUUID(), title: '', done: false }] })}>{t('Add checklist item')}</button></fieldset>
          {(['file_ids', 'decision_ids'] as const).map(key => <fieldset key={key}><legend>{t(key === 'file_ids' ? 'Linked files' : 'Linked decisions')}</legend>{available.filter(row => row.kind === (key === 'file_ids' ? 'linked-file' : 'decision')).map(row => <label className="fc-fw-toggle" key={row.id}><input type="checkbox" disabled={blocked} checked={fields[key]?.includes(row.id) || false} onChange={event => update({ [key]: event.target.checked ? [...(fields[key] || []), row.id] : fields[key]!.filter(value => value !== row.id) })} />{row.selected.fields.title}</label>)}</fieldset>)}</>}
        {kind === 'decision' && <label>{t('Status')}<select className="fc-input" disabled={blocked} value={fields.status} onChange={event => update({ status: event.target.value })}>{['proposed', 'accepted', 'superseded'].map(status => <option key={status}>{status}</option>)}</select></label>}
        {kind === 'linked-file' && <><label>{t('HTTPS link')}<input className="fc-input" type="url" value={fields.url || ''} disabled={blocked} onChange={event => update({ url: event.target.value })} /></label><label>{t('Upload encrypted file')}<input type="file" disabled={blocked} onChange={event => { const file = event.target.files?.[0]; if (file) void upload(file); event.target.value = ''; }} /></label>{fields.target_id && <p className="fc-account-muted">{t('Encrypted file selected.')}</p>}</>}
        {kind === 'comment' && <><label>{t('Comment on')}<select className="fc-input" disabled={blocked || !!selected} required value={fields.target_id ? JSON.stringify([fields.target_kind,fields.target_id]) : ''} onChange={event => {const [target_kind,target_id]=event.target.value?JSON.parse(event.target.value):[];update({target_kind,target_id:target_id || ''});}}><option value="">{t('Choose an item')}</option>{[...available, ...(view?.legacyRecords || [])].filter(row => row.id !== selected?.id).map(row => <option key={row.kind+row.id} value={JSON.stringify([row.kind,row.id])}>{row.selected.fields.title}</option>)}</select></label><label className="fc-fw-toggle"><input type="checkbox" checked={fields.resolved} disabled={blocked} onChange={event => update({ resolved: event.target.checked })} />{t('Resolved')}</label></>}
        {kind === 'collection' && <fieldset><legend>{t('Collection items')}</legend>{[...available, ...(view?.legacyRecords || [])].filter(row => ['task', 'document', 'calendar', 'feed', 'decision', 'project', 'linked-file', 'workflow'].includes(row.kind)).map(row => {
          const itemKind = row.kind === 'linked-file' ? 'linked-document' : row.kind as 'task' | 'document' | 'calendar' | 'feed' | 'decision' | 'project' | 'workflow';
          const checked = fields.items?.some(item => item.id === row.id && item.kind === itemKind) || false;
          return <label className="fc-fw-toggle" key={row.id}><input type="checkbox" disabled={blocked} checked={checked} onChange={event => update({ items: event.target.checked ? [...(fields.items || []), { kind: itemKind, id: row.id }] : fields.items!.filter(item => item.id !== row.id || item.kind !== itemKind) })} />{row.selected.fields.title}</label>;
        })}</fieldset>}
        {kind === 'workflow' && <fieldset className="fc-fw-checklist"><legend>{t('Reviewed steps')}</legend>{fields.steps?.map((step, index) => <div key={step.id} className="fc-encrypted-step"><label>{t('Step {number}', { number: index + 1 })}<select className="fc-input" disabled={blocked} value={step.kind} onChange={event => update({ steps: fields.steps!.map(row => row.id === step.id ? { ...row, kind: event.target.value as 'task' | 'feed' | 'decision', project_id: undefined } : row) })}>{['task', 'feed', 'decision'].map(value => <option key={value} value={value}>{t(labels[value as EncryptedWorkKind])}</option>)}</select></label>{(['title', 'body'] as const).map(key => <EncryptedWorkInput channelId={channelId} key={key} contextId={scope + ':step:' + step.id + ':' + key} label={t(key === 'title' ? 'Step title' : 'Step text')} multiline={key === 'body'} disabled={blocked} maxLength={key === 'title' ? 200 : 12000} required={key === 'title'} value={step[key]} references={fields.references?.['step:' + step.id + ':' + key]} candidates={candidates} onChange={(value, references) => { const next = { ...(fields.references || {}) }, field = 'step:' + step.id + ':' + key; if (references.length) next[field] = references; else delete next[field]; update({ steps: fields.steps!.map(row => row.id === step.id ? { ...row, [key]: value } : row), references: next }); }} />)}{step.kind === 'task' && <label>{t('Project')}<select className="fc-input" disabled={blocked} value={step.project_id || ''} onChange={event => update({ steps: fields.steps!.map(row => row.id === step.id ? { ...row, project_id: event.target.value || null } : row) })}><option value="">{t('No project')}</option>{available.filter(row => row.kind === 'project').map(row => <option key={row.id} value={row.id}>{row.selected.fields.title}</option>)}</select></label>}<button type="button" className="fc-button" disabled={blocked || fields.steps!.length <= 1} onClick={() => update({ steps: fields.steps!.filter(row => row.id !== step.id) })}>{t('Remove step')}</button></div>)}<button className="fc-button" type="button" disabled={blocked || (fields.steps?.length || 0) >= 12} onClick={() => update({ steps: [...(fields.steps || []), { id: crypto.randomUUID(), kind: 'task', title: '', body: '' }] })}>{t('Add step')}</button></fieldset>}
        {selected?.conflicts && <fieldset className="fc-fw-conflicts"><legend>{t('Review competing versions')}</legend><p>{t('Choose the version to keep. Saving records an explicit merge of the current versions.')}</p>{selected.heads.map(head => <label key={head.event_id}><input type="radio" name={id + '-version'} checked={reviewHead === head.event_id} onChange={() => { setReviewHead(head.event_id); setFields(head.fields); setArchived(head.archived); setDirty(true); }} />{head.fields.title} · {new Date(head.timestamp).toLocaleString()}</label>)}</fieldset>}
        <label className="fc-fw-toggle"><input type="checkbox" disabled={blocked} checked={archived} onChange={event => { setArchived(event.target.checked); setDirty(true); }} />{t('Archived')}</label>
        <div className="fc-fw-actions"><button type="submit" className="fc-button fc-button-primary" disabled={blocked || kind === 'workflow-run' || !!selected?.conflicts && (!reviewHead || selected.heads.length > 8)}>{busy ? t('Saving…') : t('Save encrypted changes')}</button><button type="button" className="fc-button" disabled={busy} onClick={() => { if (mayLeave()) { setEditing(false); setDirty(false); if (selected) setFields(selected.selected.fields); else clear(); } }}>{t('Cancel')}</button></div>
      </form> : view && selected && selectedFields ? <article className="fc-fw-editor fc-encrypted-read" aria-label={t('Encrypted item')}><h4>{content(selectedFields.title, selectedFields.references?.title)}</h4>
        {selectedFields.anchor && <blockquote>{selectedFields.anchor.quote}</blockquote>}{selected.kind !== 'organization' && <p className="fc-encrypted-body">{content(selectedFields.body, selectedFields.references?.body)}</p>}
        {selected.kind === 'organization' && <p>{t('Document organization changes are applied in the document workspace. Review competing versions here before returning to that workspace.')}</p>}
        {selectedFields.status && <p>{t('Status')}: {t(selectedFields.status)}</p>}
        {selectedFields.items && <ul>{selectedFields.items.map(item => { const row = [...(view?.records || []), ...(view?.legacyRecords || [])].find(row => row.id === item.id && (row.kind === item.kind || row.kind === 'linked-file' && ['file','linked-document'].includes(item.kind))); return <li key={item.kind + item.id}>{row ? <button type="button" className="fc-button" onClick={() => reference({ kind: 'resource', id: row.id, label: row.selected.fields.title, start: 0, end: 1, resource_kind: row.kind })}>{row.selected.fields.title}</button> : t('Item unavailable in this view')}</li>; })}</ul>}
        {selectedFields.checklist && <ul>{selectedFields.checklist.map(item => <li key={item.id}><CheckSquare size={14} /> {item.done ? '✓ ' : ''}{content(item.title, selectedFields.references?.['checklist:' + item.id])}</li>)}</ul>}
        {selectedFields.steps && <ol>{selectedFields.steps.map(step => <li key={step.id}><strong>{content(step.title, selectedFields.references?.['step:' + step.id + ':title'])}</strong><p>{content(step.body, selectedFields.references?.['step:' + step.id + ':body'])}</p></li>)}</ol>}
        {selected.kind === 'linked-file' && <div className="fc-fw-actions">{selectedFields.url && <a className="fc-button" href={selectedFields.url} target="_blank" rel="noopener noreferrer">{t('Open HTTPS link')}</a>}{selectedFields.target_id && <button className="fc-button" type="button" disabled={busy} onClick={() => void download()}>{t('Download encrypted file')}</button>}</div>}
        {selected.kind !== 'workflow-run' && <div className="fc-fw-actions"><button type="button" className="fc-button" disabled={blocked || selected.kind === 'comment' && selected.created_by !== view.context.matrix_user_id} onClick={() => { setFields(selectedFields); setEditing(true); setDirty(false); }}>{selected.conflicts ? t('Review competing versions') : t('Edit')}</button>{selected.kind === 'workflow' && <button type="button" className="fc-button fc-button-primary" disabled={blocked || selected.conflicts || selected.selected.archived} onClick={() => void execute()}><Play size={15} />{t('Run reviewed steps')}</button>}</div>}
        {selected.kind === 'workflow-run' && <><p>{t('{done} of {total} steps acknowledged', { done: selectedFields.completed?.length || 0, total: selectedFields.steps?.length || 0 })}</p><p className="fc-account-muted">{t('Execution uses a verified device. Closing the app pauses future steps; acknowledged changes remain shared.')}</p><div className="fc-fw-actions"><button type="button" className="fc-button" disabled={busy || blocked || ['completed', 'cancelled'].includes(selectedFields.status || '') || selected.created_by !== view?.context.matrix_user_id} onClick={() => void resume()}><Play size={14} />{t('Continue on this device')}</button><button type="button" className="fc-button" disabled={!view || ['completed', 'cancelled'].includes(selectedFields.status || '') || selected.created_by !== view.context.matrix_user_id} onClick={() => void stop('paused')}><Square size={14} />{t('Pause')}</button><button type="button" className="fc-button" disabled={!view || ['completed', 'cancelled'].includes(selectedFields.status || '') || selected.created_by !== view.context.matrix_user_id} onClick={() => void stop('cancelled')}>{t('Cancel run')}</button></div></>}
        {selected.kind !== 'organization' && collaboration?.secure && collaboration.channelId === channelId && view?.context.workspace_id === collaboration.workspaceId && <ReviewedResourceCopilot workspaceId={view.context.workspace_id} channelId={channelId} secureOnly label={selectedFields.title} snapshot={JSON.stringify([selected.id, selected.heads.map(head => head.event_id), api.getCurrentUserId(), api.getAccessToken()])} text={selectedFields.title + '\n\n' + selectedFields.body} />}
        <details><summary>{t('Version history')}</summary><ol>{selected.history.map(revision => <li key={revision.event_id}>{revision.fields.title} · {revision.sender.split(':')[1]} · {new Date(revision.timestamp).toLocaleString()}</li>)}</ol></details>
      </article> : <div className="fc-fw-start"><FileText size={26} /><p>{t('Choose an item or create something together.')}</p></div>}
    </div>
  </section>;
}
