import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { CalendarDays, CheckSquare, FileText, Globe2, Plus, RefreshCw, ShieldCheck } from 'lucide-react';
import { t, useI18n, formatDate } from '../i18n';
import { api } from '../lib/api';
import { calendarWallInput, expandCalendarOccurrences, exportCalendar, zonedInstant, type CalendarFields } from '../lib/calendar';
import { FederatedWorkClient, type FederatedDocumentState, type SharedTaskFields, type SharedWorkFields, type SharedWorkSnapshot, type WorkRecord, type WorkRevision } from '../lib/federated-work-api';
import { useCollaborationContext } from '../lib/collaboration-context';
import { CollaborationInput } from './CollaborationInput';
import { EncryptedWorkPanel } from './EncryptedWorkPanel';
import { FederatedDocumentComments } from './FederatedDocumentComments';
import { EncryptedWorkInput, type EncryptedReferenceCandidate } from './EncryptedWorkInput';
import { type EncryptedFieldReferences, type EncryptedReference } from '../lib/encrypted-work-references';
import { ReviewedResourceCopilot } from './ReviewedResourceCopilot';
import '../styles/federated-work.css';

type Tab = 'task' | 'calendar' | 'document';
interface Props { channelId: string; initialResourceId?: string; initialResourceKind?: string; onPendingChange?: (pending: boolean) => void }
const emptyTask = (): SharedTaskFields => ({ title: '', description: '', status: 'todo', priority: 'normal', assignee_matrix_id: null, due_date: null, checklist: [] });
function emptyCalendar(): CalendarFields {
  const start = new Date(); start.setMinutes(0, 0, 0); start.setHours(start.getHours() + 1);
  return { title: '', description: '', location: '', starts_at: start.toISOString(), ends_at: new Date(+start + 3600000).toISOString(), timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC', all_day: false, reminder_minutes: null, external_uid: null };
}
const failure = (value: unknown) => value instanceof Error ? value.message : t('Shared work could not be refreshed.');
function downloadCalendar(record: WorkRecord) {
  const blob = new Blob([exportCalendar([{ id: record.id, fields: record.selected.fields as CalendarFields }])], { type: 'text/calendar;charset=utf-8' });
  const url = URL.createObjectURL(blob), link = document.createElement('a'); link.href = url; link.download = 'shared-calendar.ics'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** A trusted-device view of encrypted room events. Native local indexes are not used. */
export function FederatedWorkPanel({ channelId, initialResourceId, initialResourceKind, onPendingChange }: Props) {
  useI18n();
  const collaboration = useCollaborationContext(), instanceId = useId();
  const [tab, setTab] = useState<Tab>('task'), [snapshot, setSnapshot] = useState<SharedWorkSnapshot | null>(null);
  const [selection, setSelection] = useState<WorkRecord | null>(null), [fields, setFields] = useState<SharedWorkFields | null>(null);
  const [doc, setDoc] = useState<FederatedDocumentState | null>(null), [docTitle, setDocTitle] = useState(''), [body, setBody] = useState('');
  const [docReferences, setDocReferences] = useState<EncryptedFieldReferences>({});
  const [mergeHeads, setMergeHeads] = useState<string[]>([]);
  const [creating, setCreating] = useState(false), [dirty, setDirty] = useState(false), [busy, setBusy] = useState(false), [loading, setLoading] = useState(true), [locked, setLocked] = useState(true);
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [archive, setArchive] = useState(false), [reviewed, setReviewed] = useState(false), [showArchived, setShowArchived] = useState(false);
  const [extensionOpen, setExtensionOpen] = useState(false), [extensionMounted, setExtensionMounted] = useState(false), [extensionPending, setExtensionPending] = useState(false);
  const [commentsPending,setCommentsPending]=useState(false);
  const pendingInitial = useRef(initialResourceId);
  const commentLeave=useRef<(()=>boolean)|null>(null);
  const client = useRef<FederatedWorkClient | null>(null), generation = useRef(0), mounted = useRef(false), pending = useRef(false), dirtyRef = useRef(false);
  const owner = useRef(api.getAccessToken()), chosen = useRef<WorkRecord | null>(null), callback = useRef(onPendingChange); callback.current = onPendingChange;
  dirtyRef.current = dirty; chosen.current = selection;
  const current = (epoch: number) => mounted.current && generation.current === epoch && owner.current === api.getAccessToken();
  const clearEditor = () => { setSelection(null); setFields(null); setDoc(null); setDocTitle(''); setBody(''); setDocReferences({}); setCreating(false); setDirty(false); dirtyRef.current = false; setReviewed(false); setMergeHeads([]); setArchive(false); };
  useEffect(() => {
    mounted.current = true; owner.current = api.getAccessToken(); const epoch = ++generation.current;
    const connection = new FederatedWorkClient(channelId); client.current = connection;
    setSnapshot(null); clearEditor(); setLocked(true); setLoading(true); setError(''); setNotice(''); pending.current = false;
    const invalidate = () => { generation.current++; connection.stop(); setSnapshot(null); clearEditor(); setLocked(true); setLoading(false); setBusy(false); setError(t('The signed-in shared-work account changed.')); callback.current?.(false); };
    const refresh = async () => {
      if (pending.current || !current(epoch)) return;
      try {
        const view = await connection.load(); if (!current(epoch)) return;
        setSnapshot(view); setLocked(false); setError('');
        const previous = chosen.current;
        if (previous && !dirtyRef.current) {
          const record = view.records.find(row => row.id === previous.id);
          if (record) {
            setSelection(record); setFields(record.selected.fields); setArchive(record.selected.archived);
            if (record.kind === 'document') { const state = await connection.document(record); if (current(epoch) && !dirtyRef.current && chosen.current?.id === record.id) { setDoc(state); setDocTitle(state.title); setBody(state.body); setDocReferences(state.references || {}); } }
          } else clearEditor();
        }
      } catch (reason) { if (current(epoch)) { if (!connection.hasCurrentScope()) { setLocked(true); setSnapshot(null); } setError(failure(reason)); } }
      finally { if (current(epoch)) setLoading(false); }
    };
    void refresh(); const timer = window.setInterval(() => { void refresh(); }, 10000);
    window.addEventListener('freecollab:auth-changed', invalidate); window.addEventListener('freecollab:session-expired', invalidate);
    return () => { mounted.current = false; generation.current++; connection.stop(); window.clearInterval(timer); window.removeEventListener('freecollab:auth-changed', invalidate); window.removeEventListener('freecollab:session-expired', invalidate); callback.current?.(false); };
  }, [channelId]);
  useEffect(() => { callback.current?.(dirty || busy || extensionPending || commentsPending); const leaving = (event: BeforeUnloadEvent) => { if (dirty || busy || extensionPending || commentsPending) { event.preventDefault(); event.returnValue = ''; } }; window.addEventListener('beforeunload', leaving); return () => window.removeEventListener('beforeunload', leaving); }, [dirty, busy, extensionPending,commentsPending]);
  const mayLeave = () => !extensionPending&&(!commentsPending||commentLeave.current?.()===true)&&(!dirty || window.confirm(t('Changes have not saved yet. Leaving may discard them. Leave anyway?')));
  async function act(operation: (connection: FederatedWorkClient, epoch: number) => Promise<void>) {
    const connection = client.current, epoch = generation.current; if (!connection || pending.current || !current(epoch)) return;
    pending.current = true; setBusy(true); setError(''); setNotice('');
    try { await operation(connection, epoch); }
    catch (reason) { if (current(epoch)) { setError(failure(reason)); if (!connection.hasCurrentScope()) { setLocked(true); setSnapshot(null); } } }
    finally { if (current(epoch)) { pending.current = false; setBusy(false); } }
  }
  async function refresh() { await act(async (connection, epoch) => { const view = await connection.load(); if (current(epoch)) { setSnapshot(view); setLocked(false); } }); }
  function switchTab(next: Tab) { pendingInitial.current = undefined; if (busy || !mayLeave()) return; clearEditor(); setTab(next); setNotice(''); }
  function create() { pendingInitial.current = undefined; if (busy || !mayLeave()) return; clearEditor(); setCreating(true); setFields(tab === 'task' ? emptyTask() : tab === 'calendar' ? emptyCalendar() : null); }
  function edit(record: WorkRecord, revision?: WorkRevision) {
    if (busy || !mayLeave()) return;
    clearEditor(); setSelection(record); setMergeHeads(record.heads.slice(0, 8).map(row => row.event_id)); setFields((revision || record.selected).fields); setArchive((revision || record.selected).archived);
    if (record.kind === 'document') void act(async (connection, epoch) => { const state = await connection.document(record); if (current(epoch)) { setDoc(state); setDocTitle(state.title); setBody(state.body); setDocReferences(state.references || {}); } });
  }
  useEffect(() => { pendingInitial.current = initialResourceId; }, [initialResourceId, initialResourceKind, channelId]);
  useEffect(() => {
    if (!snapshot || !pendingInitial.current) return;
    const record = snapshot.records.find(row => row.id === pendingInitial.current && (!initialResourceKind || row.kind === initialResourceKind));
    if (record) { pendingInitial.current = undefined; setTab(record.kind); edit(record); }
    else if (snapshot.context.work_protocols?.includes('freecollab-encrypted-work/2')) { pendingInitial.current = undefined; setExtensionMounted(true); setExtensionOpen(true); }
  }, [snapshot, initialResourceId, initialResourceKind]);
  function change(values: Partial<SharedTaskFields & CalendarFields>) { setFields(previous => previous ? { ...previous, ...values } as SharedWorkFields : previous); setDirty(true); }
  function save(event: FormEvent) {
    event.preventDefault(); if (locked) return;
    void act(async (connection, epoch) => {
      if (tab === 'document') {
        if (!selection) {
          const record = await connection.createDocument(docTitle, Object.keys(docReferences).length ? docReferences : undefined); if (!current(epoch)) return;
          const state = await connection.document(record); if (!current(epoch)) return; setSelection(record); setDoc(state); setDocTitle(state.title); setBody(state.body); setDocReferences(state.references || {});
        } else if (doc) { const state = await connection.saveDocument(selection, doc, docTitle, body, docReferences); if (!current(epoch)) return; setDoc(state); setDocTitle(state.title); setBody(state.body); setDocReferences(state.references || {}); }
        else throw new Error(t('Open the verified document before editing.'));
      } else {
        if (!fields) return;
        const parents = selection?.conflicts ? reviewed ? mergeHeads : undefined : undefined;
        const record = await connection.save(tab, fields, selection || undefined, parents, archive); if (!current(epoch)) return; setSelection(record); setMergeHeads(record.heads.slice(0, 8).map(row => row.event_id)); setFields(record.selected.fields); setArchive(record.selected.archived);
      }
      const view = await connection.load(); if (!current(epoch)) return; setSnapshot(view); setLocked(false); setCreating(false); setDirty(false); dirtyRef.current = false; setReviewed(false); setNotice(t('Encrypted shared changes saved.'));
    });
  }
  const records = snapshot?.records.filter(row => row.kind === tab && (showArchived || !row.selected.archived || row.conflicts || row.invalid.length)) || [];
  const task = tab === 'task' ? fields as SharedTaskFields | null : null, calendar = tab === 'calendar' ? fields as CalendarFields | null : null;
  const editing = creating || Boolean(selection), blocked = busy || locked || Boolean(selection?.invalid.length);
  const commandsReady = !blocked && !!client.current?.hasCurrentScope() && !!collaboration?.secure && !!collaboration.commandContext.sharedWork
    && collaboration.channelId === channelId && snapshot?.context.channel_id === channelId && collaboration.workspaceId === snapshot.context.workspace_id;
  const fieldId = (field: string) => `${field}-${instanceId}`;
  const inputContext = (field: string) => JSON.stringify(['shared-work', collaboration?.surfaceId, instanceId, channelId, tab, selection?.id || 'new', field]);
  const referencesReady = snapshot?.context.work_protocols?.includes('freecollab-federated-work/2');
  const candidates: EncryptedReferenceCandidate[] = snapshot ? [...snapshot.context.members.map(member => ({kind: 'participant' as const, id: member.matrix_user_id, label: member.name})), ...[...snapshot.records,...(snapshot.reference_records || [])].filter(row => !row.invalid.length && !row.conflicts && !row.selected.archived).map(row => ({kind: 'resource' as const, id: row.id, label: row.selected.fields.title, resource_kind: row.kind}))] : [];
  const savedReferences=Object.values(tab==='document'?docReferences:fields&&'references' in fields?fields.references || {}:{}).flat();
  const referenceLinks=[...new Map(savedReferences.map(reference=>[JSON.stringify([reference.kind,reference.resource_kind,reference.id]),reference])).values()];
  function openReference(reference:EncryptedReference){
    if(blocked||dirty||commentsPending||extensionPending||!snapshot||!client.current?.hasCurrentScope())return;
    if(reference.kind==='participant'){const person=snapshot.context.members.find(row=>row.matrix_user_id===reference.id);setNotice(person?person.name+' · '+person.matrix_user_id:t('This participant is no longer available.'));return;}
    if(reference.kind==='message'){window.dispatchEvent(new CustomEvent('freecollab:open-search-source',{detail:{channel_id:channelId,kind:'message',id:reference.id}}));return;}
    const target=[...snapshot.records,...(snapshot.reference_records || [])].find(row=>row.id===reference.id&&row.kind===reference.resource_kind&&!row.invalid.length&&!row.conflicts&&!row.selected.archived);
    if(target)window.dispatchEvent(new CustomEvent('freecollab:open-encrypted-resource',{detail:{channel_id:channelId,kind:target.kind,id:target.id}}));else setNotice(t('This encrypted reference is no longer available.'));
  }
  function referenceChange(field: string, value: string, references: EncryptedReference[]) {
    if (tab === 'document') { if (field === 'title') setDocTitle(value); else setBody(value); setDocReferences(previous => ({...previous, [field]: references})); setDirty(true); return; }
    setFields(previous => {
      if (!previous || !('description' in previous)) return previous;
      const next = {...previous, references: {...previous.references, [field]: references}};
      if (field.startsWith('checklist:') && 'checklist' in next) next.checklist = next.checklist.map(item => item.id === field.slice(10) ? {...item, title: value} : item);
      else Object.assign(next, {[field]: value});
      return next;
    }); setDirty(true);
  }
  function sharedText(field: string, label: string, value: string, onValueChange: (value: string) => void, options: {id?: string; maxLength?: number; multiline?: boolean; required?: boolean; rows?: number; className?: string} = {}) {
    if (referencesReady) return <EncryptedWorkInput channelId={channelId} {...options} id={options.id} hideLabel={!!options.id} label={label} contextId={inputContext(field)} value={value} references={(tab === 'document' ? docReferences : fields && 'references' in fields ? fields.references : undefined)?.[field]} candidates={candidates} disabled={blocked} onChange={(next, refs) => referenceChange(field,next,refs)} />;
    return <CollaborationInput id={options.id} as={options.multiline === false ? 'input' : 'textarea'} contextId={inputContext(field)} allowCommands={commandsReady} allowReferences={false} className={options.className || 'fc-input'} required={options.required} maxLength={options.maxLength} rows={options.rows} disabled={blocked} value={value} aria-label={label} onValueChange={onValueChange} />;
  }
  function calendarTime(key: 'starts_at' | 'ends_at', value: string) { if (!calendar || !value) return; try { change({ [key]: calendar.all_day ? value : zonedInstant(value, calendar.timezone) }); } catch (reason) { setError(failure(reason)); } }
  let occurrences: ReturnType<typeof expandCalendarOccurrences> = [];
  if (calendar?.recurrence) { try { occurrences = expandCalendarOccurrences(calendar, '1000-01-01T00:00:00Z', '9999-12-31T23:59:59Z', 366).slice(0, 12); } catch { /* The save validator explains incomplete date edits. */ } }
  const statusLabel = (value: string) => t(value === 'todo' ? 'To do' : value === 'doing' ? 'In progress' : 'Done');
  return <section className="fc-federated-work" aria-label={t('Shared work across installations')}>
    <header className="fc-fw-intro"><Globe2 size={24} /><div><span className="fc-kicker">FreeCollab Connect</span><h3>{t('Good work. Across organizations.')}</h3><p>{t('Shared tasks, calendar and documents stay encrypted between verified participant devices. Each organization keeps its own accounts.')}</p></div><ShieldCheck size={21} /></header>
    <p className="fc-fw-scope">{t('This shared view is synchronized across both installations. Each organization keeps its own installation settings and provider credentials.')}</p>
    {commandsReady && <p className="fc-account-muted">{t('Use / for tools and @ for verified people and shared material.')}</p>}
    <nav className="fc-fw-tabs" aria-label={t('Shared work type')}>{([{ id: 'task', label: 'Tasks', icon: CheckSquare }, { id: 'calendar', label: 'Calendar', icon: CalendarDays }, { id: 'document', label: 'Documents', icon: FileText }] as const).map(({ id, label, icon: Icon }) => <button type="button" key={id} className="fc-button" aria-pressed={tab === id} disabled={busy} onClick={() => switchTab(id)}><Icon size={16} />{t(label)}</button>)}</nav>
    {error && <p className="fc-alert fc-account-error" role="alert">{error}</p>}{notice && <p className="fc-alert fc-account-notice" role="status">{notice}</p>}
    <div className="fc-fw-toolbar"><span className="fc-account-muted">{snapshot ? t('{count} shared participants', { count: snapshot.context.members.length }) : t('Current participant access required')}</span><button className="fc-button" type="button" disabled={busy} onClick={() => { void refresh(); }}><RefreshCw size={14} />{t('Refresh')}</button><button className="fc-button fc-button-primary" type="button" disabled={busy || locked || loading} onClick={create}><Plus size={15} />{t(tab === 'task' ? 'New shared task' : tab === 'calendar' ? 'New shared event' : 'New shared document')}</button></div>
    {loading && <p role="status">{t('Loading encrypted shared work…')}</p>}
    {!loading && locked && <p className="fc-fw-empty">{t('The current shared connection and verified devices must be available. Refresh after restoring access or verifying the author’s device.')}</p>}
    {!locked && <>
      {!!snapshot?.warnings.length && <p className="fc-fw-warning" role="status">{snapshot.quarantined_event_ids.length ? t('{count} encrypted events are quarantined. Available verified history may be incomplete. Refresh after restoring keys or verifying devices.', { count: snapshot.quarantined_event_ids.length }) : t('Some shared revisions need review. Open the marked item to inspect competing versions.')}</p>}
      {tab !== 'document' && <label className="fc-fw-toggle"><input type="checkbox" checked={showArchived} onChange={event => setShowArchived(event.target.checked)} />{t('Show archived items')}</label>}
      <div className="fc-fw-layout"><div className="fc-fw-directory" aria-label={t('Shared items')}>{records.length ? records.map(record => <button type="button" key={record.id} className="fc-fw-record" aria-pressed={selection?.id === record.id} disabled={busy} onClick={() => edit(record)}><strong>{record.kind === 'document' ? record.selected.fields.title : record.selected.fields.title}</strong><small>{record.kind === 'task' ? statusLabel((record.selected.fields as SharedTaskFields).status) : record.kind === 'calendar' ? formatDate((record.selected.fields as CalendarFields).starts_at, { dateStyle: 'medium', timeStyle: (record.selected.fields as CalendarFields).all_day ? undefined : 'short', timeZone: (record.selected.fields as CalendarFields).all_day ? 'UTC' : (record.selected.fields as CalendarFields).timezone }) : t('Encrypted collaborative document')}</small>{!!record.invalid.length && <span className="fc-badge">{t('Incomplete history')}</span>}{record.conflicts && <span className="fc-badge">{t('Review competing versions')}</span>}{record.selected.archived && <span className="fc-badge">{t('Archived')}</span>}</button>) : <p className="fc-fw-empty">{t('A clear space for your next shared idea.')}</p>}</div>
      {editing ? <form className="fc-fw-editor" onSubmit={save}><h4>{t(tab === 'task' ? 'Shared task' : tab === 'calendar' ? 'Shared calendar event' : 'Shared document')}</h4>
        {selection?.conflicts && <fieldset className="fc-fw-conflicts"><legend>{t('Review competing versions')}</legend><p>{t('Choose a starting version, review every competing revision, then save an explicit merge. No version is silently overwritten.')}</p>{selection.heads.map((revision, index) => <div key={revision.event_id}><label><input type="checkbox" aria-label={t('Merge revision {number}', { number: index + 1 })} checked={mergeHeads.includes(revision.event_id)} disabled={busy || !mergeHeads.includes(revision.event_id) && mergeHeads.length >= 8} onChange={event => { setMergeHeads(previous => event.target.checked ? [...previous, revision.event_id] : previous.filter(id => id !== revision.event_id)); setReviewed(false); setDirty(true); }} />{t('Merge revision {number}', { number: index + 1 })}</label><button type="button" className="fc-button" disabled={busy} onClick={() => { setFields(revision.fields); setArchive(revision.archived); setReviewed(false); setDirty(true); }}>{t('Use version {number}', { number: index + 1 })} · {revision.sender}</button></div>)}<label><input type="checkbox" checked={reviewed} onChange={event => { setReviewed(event.target.checked); setDirty(true); }} />{t('I reviewed the selected versions and will merge their current revisions.')}</label></fieldset>}
        {!!selection?.invalid.length && <p className="fc-fw-warning" role="alert">{t('This item has incomplete or invalid revision ancestry. Editing is blocked until its verified history is repaired.')}</p>}
        <label htmlFor={fieldId('shared-work-title')}>{t('Title')}</label>{sharedText('title', t('Title'), tab === 'document' ? docTitle : fields?.title || '', value => { if (tab === 'document') { setDocTitle(value); setDirty(true); } else change({title:value}); }, {id:fieldId('shared-work-title'),maxLength:200,multiline:false,required:true})}
        {task && <><label htmlFor={fieldId('shared-task-description')}>{t('Description')}</label>{sharedText('description',t('Description'),task.description,value=>change({description:value}),{id:fieldId('shared-task-description'),maxLength:12000,rows:4})}<div className="fc-fw-grid"><label>{t('Status')}<select className="fc-input" disabled={blocked} value={task.status} onChange={event => change({ status: event.target.value as SharedTaskFields['status'] })}><option value="todo">{t('To do')}</option><option value="doing">{t('In progress')}</option><option value="done">{t('Done')}</option></select></label><label>{t('Priority')}<select className="fc-input" disabled={blocked} value={task.priority} onChange={event => change({ priority: event.target.value as SharedTaskFields['priority'] })}>{['low', 'normal', 'high'].map(value => <option key={value} value={value}>{t(value === 'low' ? 'Low' : value === 'normal' ? 'Normal' : 'High')}</option>)}</select></label></div>
          <label htmlFor={fieldId('shared-task-assignee')}>{t('Assigned participant')}</label><select id={fieldId('shared-task-assignee')} className="fc-input" value={task.assignee_matrix_id || ''} disabled={blocked} onChange={event => change({ assignee_matrix_id: event.target.value || null })}><option value="">{t('Unassigned')}</option>{task.assignee_matrix_id && !snapshot?.context.members.some(member => member.matrix_user_id === task.assignee_matrix_id) && <option value={task.assignee_matrix_id}>{t('Former participant')} · {task.assignee_matrix_id}</option>}{snapshot?.context.members.map(member => <option key={member.matrix_user_id} value={member.matrix_user_id}>{member.name} · {member.matrix_user_id.split(':')[1]}</option>)}</select>
          <label htmlFor={fieldId('shared-task-due')}>{t('Due date')}</label><input type="date" id={fieldId('shared-task-due')} className="fc-input" value={task.due_date || ''} disabled={blocked} onChange={event => change({ due_date: event.target.value || null })} />
          <fieldset className="fc-fw-checklist"><legend>{t('Checklist')}</legend>{task.checklist.map((item, index) => <div key={item.id}><input type="checkbox" aria-label={t('Complete checklist item {number}', { number: index + 1 })} disabled={blocked} checked={item.done} onChange={event => change({ checklist: task.checklist.map(row => row.id === item.id ? { ...row, done: event.target.checked } : row) })} />{sharedText('checklist:'+item.id,t('Checklist item {number}',{number:index+1}),item.title,value=>change({checklist:task.checklist.map(row=>row.id===item.id?{...row,title:value}:row)}),{maxLength:200,multiline:false,required:true})}<button type="button" className="fc-button" disabled={blocked} onClick={() => change({ checklist: task.checklist.filter(row => row.id !== item.id) })}>{t('Remove')}</button></div>)}<button type="button" className="fc-button" disabled={blocked || task.checklist.length >= 40} onClick={() => change({ checklist: [...task.checklist, { id: crypto.randomUUID(), title: '', done: false }] })}>{t('Add checklist item')}</button></fieldset></>}
        {calendar && <><label htmlFor={fieldId('shared-event-description')}>{t('Description')}</label>{sharedText('description',t('Description'),calendar.description,value=>change({description:value}),{id:fieldId('shared-event-description'),maxLength:10000,rows:3})}<label htmlFor={fieldId('shared-event-location')}>{t('Location')}</label>{sharedText('location',t('Location'),calendar.location,value=>change({location:value}),{id:fieldId('shared-event-location'),maxLength:1000,multiline:false})}<label className="fc-fw-toggle"><input type="checkbox" disabled={blocked} checked={calendar.all_day} onChange={event => { const all_day = event.target.checked; change({ all_day, starts_at: all_day ? calendar.starts_at.slice(0, 10) : calendar.starts_at + 'T09:00:00Z', ends_at: all_day ? new Date(Date.parse(calendar.starts_at) + 86400000).toISOString().slice(0, 10) : calendar.ends_at + 'T09:00:00Z', exceptions: [] }); }} />{t('All day')}</label><label htmlFor="shared-event-zone">{t('Time zone')}</label><input id="shared-event-zone" className="fc-input" maxLength={100} disabled={blocked} value={calendar.timezone} onChange={event => change({ timezone: event.target.value })} />
          <div className="fc-fw-grid">{(['starts_at', 'ends_at'] as const).map(key => { let value = calendar[key]; if (!calendar.all_day) { try { value = calendarWallInput(value, calendar.timezone); } catch { value = ''; } } return <label key={key}>{t(key === 'starts_at' ? 'Starts' : 'Ends')}<input className="fc-input" type={calendar.all_day ? 'date' : 'datetime-local'} required disabled={blocked} value={value} onChange={event => calendarTime(key, event.target.value)} /></label>; })}</div>
          <label htmlFor={fieldId('shared-event-repeat')}>{t('Repeats')}</label><select id={fieldId('shared-event-repeat')} className="fc-input" disabled={blocked} value={calendar.recurrence?.frequency || ''} onChange={event => change({ recurrence: event.target.value ? { frequency: event.target.value as 'daily' | 'weekly' | 'monthly', interval: 1, count: 10 } : null, exceptions: [] })}><option value="">{t('Does not repeat')}</option><option value="daily">{t('Daily')}</option><option value="weekly">{t('Weekly')}</option><option value="monthly">{t('Monthly')}</option></select>
          {calendar.recurrence && <><div className="fc-fw-grid"><label>{t('Repeat interval')}<input className="fc-input" type="number" min={1} max={30} required disabled={blocked} value={calendar.recurrence.interval} onChange={event => change({ recurrence: { ...calendar.recurrence!, interval: Number(event.target.value) } })} /></label><label>{t('Number of occurrences')}<input className="fc-input" type="number" min={1} max={366} required disabled={blocked} value={calendar.recurrence.count} onChange={event => change({ recurrence: { ...calendar.recurrence!, count: Number(event.target.value) } })} /></label></div><details><summary>{t('Preview and skip occurrences')}</summary><p>{t('Showing up to twelve occurrences. Skipped dates are encrypted exceptions in this shared series.')}</p><ul className="fc-fw-occurrences">{occurrences.map(item => <li key={item.original_start}><span>{formatDate(item.starts_at, { dateStyle: 'medium', timeStyle: calendar.all_day ? undefined : 'short', timeZone: calendar.all_day ? 'UTC' : calendar.timezone })}</span><button type="button" className="fc-button" disabled={blocked || (calendar.exceptions?.length || 0) >= 100} onClick={() => change({ exceptions: [...(calendar.exceptions || []).filter(row => row.original_start !== item.original_start), { original_start: item.original_start, cancelled: true }] })}>{t('Skip')}</button></li>)}</ul>{!!calendar.exceptions?.length && <button type="button" className="fc-button" disabled={blocked} onClick={() => change({ exceptions: [] })}>{t('Restore all skipped occurrences')}</button>}</details></>}
          <p className="fc-account-muted">{t('This encrypted shared series does not send invitations or copy data to an external calendar provider.')}</p>{selection && <button type="button" className="fc-button" disabled={busy || selection.conflicts} onClick={() => { try { downloadCalendar(selection); } catch (reason) { setError(failure(reason)); } }}>{t('Export saved calendar')}</button>}</>}
        {tab === 'document' && <>{selection ? doc ? <><label htmlFor={fieldId('shared-document-body')}>{t('Document text')}</label>{sharedText('body',t('Document text'),body,value=>{setBody(value);setDirty(true);},{id:fieldId('shared-document-body'),maxLength:50000,rows:16,className:'fc-input fc-fw-document'})}<p className="fc-account-muted">{t('Concurrent text changes merge with Yjs on verified devices. Save to share your current changes.')}</p></> : <p role="status">{t('Reconstructing encrypted document…')}</p> : <p className="fc-account-muted">{t('Create the encrypted document, then add text. Its title and body can be edited by participants on either installation.')}</p>}</>}
        {tab !== 'document' && <label className="fc-fw-toggle"><input type="checkbox" disabled={blocked} checked={archive} onChange={event => { setArchive(event.target.checked); setDirty(true); }} />{t('Archived')}</label>}
        <div className="fc-fw-actions"><button className="fc-button fc-button-primary" type="submit" disabled={blocked || Boolean(selection?.invalid.length) || Boolean(selection?.conflicts && (!reviewed || !mergeHeads.length || mergeHeads.length > 8)) || tab === 'document' && Boolean(selection && !doc)}>{busy ? t('Saving…') : selection?.conflicts ? t('Save reviewed merge') : t('Save encrypted changes')}</button><button type="button" className="fc-button" disabled={busy} onClick={() => { if (mayLeave()) clearEditor(); }}>{t('Close')}</button>{dirty && <span className="fc-account-muted">{t('Unsaved changes')}</span>}</div>
      </form> : <div className="fc-fw-start"><ShieldCheck size={28} /><p>{t('Choose an item or create something together.')}</p></div>}</div>
    </>}
    {!locked&&selection&&referenceLinks.length>0&&<nav className="fc-fw-actions" aria-label={t('Referenced material')}>{referenceLinks.map(reference=><button key={JSON.stringify([reference.kind,reference.resource_kind,reference.id])} className="fc-button" type="button" disabled={blocked||dirty||commentsPending||extensionPending} onClick={()=>openReference(reference)}>@{reference.label}</button>)}</nav>}
    {snapshot?.context.work_protocols?.includes('freecollab-encrypted-work/2') && <details open={extensionOpen} onToggle={event => {
      setExtensionOpen(event.currentTarget.open); if (event.currentTarget.open) setExtensionMounted(true);
    }}><summary>{t('Projects, feed, decisions, collections and workflows')}</summary>{extensionMounted && <EncryptedWorkPanel channelId={channelId} initialResourceId={initialResourceId} initialResourceKind={initialResourceKind} onPendingChange={setExtensionPending} />}</details>}
    {snapshot && selection && collaboration?.secure && collaboration.channelId === channelId && snapshot.context.workspace_id === collaboration.workspaceId && <ReviewedResourceCopilot
      workspaceId={snapshot.context.workspace_id} channelId={channelId} secureOnly label={selection.selected.fields.title}
      snapshot={JSON.stringify([selection.id, selection.heads.map(head => head.event_id), api.getCurrentUserId(), api.getAccessToken(), doc?.update_ids])}
      text={selection.kind === 'document' ? (doc?.title || '') + '\n\n' + (doc?.body || '') : selection.selected.fields.title + '\n\n' + (selection.kind === 'task' ? (selection.selected.fields as SharedTaskFields).description : (selection.selected.fields as CalendarFields).description)} />}
    {!locked&&selection?.kind==='document'&&doc&&snapshot?.context.work_protocols?.includes('freecollab-encrypted-work/2')&&<FederatedDocumentComments key={selection.id} channelId={channelId} documentId={selection.id} documentState={doc} textareaId={fieldId('shared-document-body')} dirty={dirty||busy} onPendingChange={setCommentsPending} leaveController={commentLeave} />}
  </section>;
}
