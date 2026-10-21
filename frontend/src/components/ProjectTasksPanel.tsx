import { t, useI18n, currentLocale } from '../i18n';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Archive, CheckSquare, Folder, History, Loader2, Pencil, Plus, RefreshCw, X } from 'lucide-react';
import { api, type ChannelMember } from '../lib/api';
import { collaborationApi, type Decision } from '../lib/collaboration-api';
import { downloadChannelFile, listChannelFiles, type ManagedFile } from '../lib/files-api';
import { projectTasksApi, type ProjectFields, type ProjectRecord, type ProjectTaskRecord, type TaskFields, type TaskRecord } from '../lib/project-tasks-api';
import '../styles/project-tasks.css';

interface Props { workspaceId: string; channelId: string; isAdmin?: boolean; initialProjectId?: string; focusTaskId?: string }
type ProjectEditor = { kind: 'project'; id: string; fields: ProjectFields; previous?: ProjectRecord };
type TaskEditor = { kind: 'task'; id: string; fields: TaskFields; previous?: TaskRecord };
type Editor = ProjectEditor | TaskEditor;
const statuses = { todo: 'To do', doing: 'In progress', done: 'Done' } as const;
const freshTask = (): TaskFields => ({ title: '', description: '', status: 'todo', priority: 'normal', assignee_user_id: null, due_date: null, checklist: [], file_ids: [], decision_ids: [] });
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T;
const describe = (value: unknown) => value instanceof Error ? value.message : 'This project action could not be completed.';
const due = (value: string) => new Date(value + 'T00:00:00Z').toLocaleDateString(currentLocale(), { timeZone: 'UTC', dateStyle: 'medium' });

export function ProjectTasksPanel({ workspaceId, channelId, isAdmin = false, initialProjectId, focusTaskId }: Props) {
  useI18n();
  const [projects, setProjects] = useState<ProjectRecord[]>([]), [selected, setSelected] = useState('');
  const [tasks, setTasks] = useState<TaskRecord[]>([]), [members, setMembers] = useState<ChannelMember[]>([]);
  const [files, setFiles] = useState<ManagedFile[]>([]), [decisions, setDecisions] = useState<Decision[]>([]);
  const [loading, setLoading] = useState(true), [tasksLoading, setTasksLoading] = useState(false);
  const [busy, setBusy] = useState(''), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [editor, setEditor] = useState<Editor | null>(null), [search, setSearch] = useState(''), [mine, setMine] = useState(false), [showArchived, setShowArchived] = useState(false);
  const [archiving, setArchiving] = useState<ProjectRecord | TaskRecord | null>(null);
  const [history, setHistory] = useState<{ kind: 'project' | 'task'; title: string; rows: ProjectTaskRecord<ProjectFields | TaskFields>[] } | null>(null);
  const [linkedDecision, setLinkedDecision] = useState<Decision | null>(null);
  const generation = useRef(0), taskGeneration = useRef(0), token = useRef(api.getAccessToken());
  const errorRef = useRef<HTMLDivElement>(null), editorTitle = useRef<HTMLInputElement>(null);
  const decisionDetail = useRef<HTMLElement>(null), decisionTrigger = useRef<HTMLElement | null>(null);
  const focusedTask = useRef<string | null>(null);
  const transfers = useRef(new Set<AbortController>()), objectUrls = useRef(new Set<string>());
  const current = (request: number) => generation.current === request && token.current === api.getAccessToken();
  const project = projects.find(item => item.id === selected);
  const canArchive = (row: ProjectRecord | TaskRecord) => isAdmin || row.created_by === api.getCurrentUserId();
  function clearTransfers() {
    for (const transfer of transfers.current) transfer.abort();
    transfers.current.clear();
    for (const url of objectUrls.current) URL.revokeObjectURL(url);
    objectUrls.current.clear();
  }

  async function load(request = generation.current) {
    setLoading(true); setError('');
    const results = await Promise.allSettled([projectTasksApi.projects(channelId), api.getChannelMembers(channelId), listChannelFiles(channelId), collaborationApi.decisions(channelId)]);
    if (!current(request)) return;
    const [boards, people, material, choices] = results;
    if (boards.status === 'fulfilled') { setProjects(boards.value); setSelected(previous => boards.value.some(item => item.id === previous) ? previous : boards.value.find(item => item.id === initialProjectId && !item.archived)?.id || boards.value.find(item => !item.archived)?.id || boards.value[0]?.id || ''); }
    else setError(describe(boards.reason));
    if (people.status === 'fulfilled') setMembers(people.value); else setMembers([]);
    if (material.status === 'fulfilled') setFiles(material.value); else setFiles([]);
    if (choices.status === 'fulfilled') setDecisions(choices.value); else setDecisions([]);
    if (boards.status === 'fulfilled' && results.slice(1).some(item => item.status === 'rejected')) setNotice(t("Some member or linked-material choices are unavailable. Reload to try again."));
    setLoading(false);
  }
  async function loadTasks(projectId = selected, request = generation.current) {
    if (!projectId) { setTasks([]); return; }
    const attempt = ++taskGeneration.current; setTasksLoading(true);
    try { const rows = await projectTasksApi.tasks(channelId, projectId); if (current(request) && attempt === taskGeneration.current) setTasks(rows); }
    catch (failure) { if (current(request) && attempt === taskGeneration.current) setError(describe(failure)); }
    finally { if (current(request) && attempt === taskGeneration.current) setTasksLoading(false); }
  }
  useEffect(() => {
    const request = ++generation.current; token.current = api.getAccessToken(); ++taskGeneration.current;
    setProjects([]); setSelected(''); setTasks([]); setMembers([]); setFiles([]); setDecisions([]); setEditor(null); setHistory(null); setLinkedDecision(null); setArchiving(null); setError(''); setNotice(''); setBusy(''); setSearch(''); setMine(false); setShowArchived(false);
    void load(request);
    const changed = () => { ++generation.current; ++taskGeneration.current; clearTransfers(); setProjects([]); setTasks([]); setMembers([]); setFiles([]); setDecisions([]); setEditor(null); setHistory(null); setLinkedDecision(null); setArchiving(null); setLoading(false); setTasksLoading(false); };
    window.addEventListener('freecollab:auth-changed', changed);
    return () => { ++generation.current; ++taskGeneration.current; clearTransfers(); window.removeEventListener('freecollab:auth-changed', changed); };
  }, [workspaceId, channelId]);
  useEffect(() => { setTasks([]); setEditor(null); setHistory(null); setLinkedDecision(null); setArchiving(null); void loadTasks(selected); }, [selected]);
  useEffect(() => {
    if (!focusTaskId || focusedTask.current === focusTaskId || tasksLoading || !tasks.some(row => row.id === focusTaskId)) return;
    const target = Array.from(document.querySelectorAll<HTMLElement>('[data-task-id]')).find(node => node.dataset.taskId === focusTaskId);
    if (target) { focusedTask.current = focusTaskId; target.scrollIntoView({ block: 'nearest', behavior: 'instant' }); target.focus({ preventScroll: true }); }
  }, [focusTaskId, tasks, tasksLoading]);
  useEffect(() => { if (error) errorRef.current?.focus(); }, [error]);
  useEffect(() => { if (editor) editorTitle.current?.focus(); }, [editor?.id, editor?.kind]);
  useEffect(() => { if (linkedDecision) decisionDetail.current?.focus(); }, [linkedDecision]);

  async function run(key: string, action: (request: number) => Promise<void>) {
    if (busy) return; const request = generation.current; setBusy(key); setError(''); setNotice('');
    try { await action(request); } catch (failure) { if (current(request)) setError(describe(failure)); }
    finally { if (current(request)) setBusy(''); }
  }
  async function save(event: FormEvent) {
    event.preventDefault(); if (!editor) return; const draft = editor;
    await run('save', async request => {
      if (draft.kind === 'project') {
        const saved = await projectTasksApi.saveProject(channelId, draft.fields, draft.previous, { id: draft.id });
        if (!current(request)) return; if (!saved.fields) throw new Error(saved.resolution_error);
        setProjects(rows => [...rows.filter(item => item.id !== saved.id), saved]); setSelected(saved.id);
      } else {
        const saved = await projectTasksApi.saveTask(channelId, selected, draft.fields, draft.previous, { id: draft.id });
        if (!current(request)) return; if (!saved.fields) throw new Error(saved.resolution_error);
        setTasks(rows => [...rows.filter(item => item.id !== saved.id), saved]);
      }
      setEditor(null); setHistory(null); setNotice(draft.kind === 'project' ? t("Project saved.") : t("Task saved. Its previous revisions are retained."));
    });
  }
  async function updateTask(row: TaskRecord, fields: TaskFields) {
    await run('task:' + row.id, async request => {
      const saved = await projectTasksApi.saveTask(channelId, selected, fields, row);
      if (!current(request)) return; if (!saved.fields) throw new Error(saved.resolution_error);
      setTasks(rows => rows.map(item => item.id === saved.id ? saved : item)); setNotice(t("Task updated."));
    });
  }
  async function archive() {
    const row = archiving; if (!row?.fields) return;
    await run('archive', async request => {
      if (row.kind === 'project') {
        const saved = await projectTasksApi.saveProject(channelId, row.fields as ProjectFields, row as ProjectRecord, { archived: !row.archived });
        if (!current(request)) return; if (!saved.fields) throw new Error(saved.resolution_error);
        setProjects(rows => rows.map(item => item.id === saved.id ? saved : item));
      } else {
        const saved = await projectTasksApi.saveTask(channelId, selected, row.fields as TaskFields, row as TaskRecord, { archived: !row.archived });
        if (!current(request)) return; if (!saved.fields) throw new Error(saved.resolution_error);
        setTasks(rows => rows.map(item => item.id === saved.id ? saved : item));
      }
      setArchiving(null); setNotice(row.archived ? t("Item reopened.") : t("Item archived. Its history is retained."));
    });
  }
  async function showHistory(row: ProjectRecord | TaskRecord) {
    await run('history', async request => {
      const rows = await projectTasksApi.history<ProjectFields | TaskFields>(channelId, row.kind, row.kind === 'project' ? row.id : selected, row.kind === 'task' ? row.id : undefined);
      if (current(request)) setHistory({ kind: row.kind, title: row.fields?.title || 'Encrypted item', rows });
    });
  }
  async function openFile(id: string) {
    await run('file:' + id, async request => {
      const controller = new AbortController(); transfers.current.add(controller);
      try {
        const latest = await listChannelFiles(channelId);
        if (!current(request) || controller.signal.aborted) return;
        const file = latest.find(item => item.id === id && item.channel_id === channelId);
        setFiles(latest);
        if (!file) throw new Error(t("This linked file is no longer available in the channel."));
        const blob = await downloadChannelFile(file, { signal: controller.signal });
        if (!current(request) || controller.signal.aborted) return;
        const url = URL.createObjectURL(blob); objectUrls.current.add(url);
        const anchor = document.createElement('a'); anchor.href = url; anchor.download = file.filename;
        document.body.append(anchor); anchor.click(); anchor.remove();
        window.setTimeout(() => { URL.revokeObjectURL(url); objectUrls.current.delete(url); }, 1000);
        setNotice(`${file.filename} downloaded.`);
      } finally { transfers.current.delete(controller); }
    });
  }
  async function openDecision(id: string, trigger: HTMLElement) {
    await run('decision:' + id, async request => {
      setLinkedDecision(null); decisionTrigger.current = trigger;
      const latest = await collaborationApi.decisions(channelId);
      if (!current(request)) return;
      setDecisions(latest);
      const decision = latest.find(item => item.id === id && item.channel_id === channelId);
      if (!decision || decision.resolution_error || !decision.title || typeof decision.content !== 'string')
        throw new Error(decision?.resolution_error || t("This linked decision is no longer available in the channel."));
      setLinkedDecision(decision);
    });
  }
  function closeDecision() {
    setLinkedDecision(null);
    if (decisionTrigger.current?.isConnected) decisionTrigger.current.focus();
  }
  function changeTask(fields: Partial<TaskFields>) { if (editor?.kind === 'task') setEditor({ ...editor, fields: { ...editor.fields, ...fields } }); }
  const visible = tasks.filter(row => (showArchived || !row.archived) && (!mine || row.fields?.assignee_user_id === api.getCurrentUserId()) && (!search || `${row.fields?.title || ''} ${row.fields?.description || ''}`.toLowerCase().includes(search.toLowerCase())));
  return <section className="fc-projects" aria-label={t("Projects and tasks")}>
    <header className="fc-project-heading"><div><span className="fc-kicker">{t("Make progress, together")}</span><h3>{t("Projects & tasks")}</h3><p>{t("Give work a clear owner, a next step and a place in the conversation.")}</p></div><button className="fc-icon-button" type="button" aria-label={t("Refresh projects and tasks")} disabled={!!busy} onClick={() => void run('refresh', async () => { await load(); await loadTasks(); })}><RefreshCw size={17} /></button></header>
    {error && <div ref={errorRef} tabIndex={-1} className="fc-alert fc-project-error" role="alert"><p>{error}</p>{editor && <p>{t("Your draft is still open. Reloading closes it and discards unsaved edits.")}</p>}<button className="fc-text-button" type="button" onClick={() => void run('reload', async () => { setEditor(null); setHistory(null); setLinkedDecision(null); setArchiving(null); await load(); await loadTasks(); })}>{t("Reload current revisions")}</button></div>}
    {notice && <p className="fc-project-notice" role="status">{notice}</p>}
    <div className="fc-project-toolbar"><label>{t("Project")}<select className="fc-input" aria-label={t("Choose project")} value={selected} disabled={loading || !!busy} onChange={event => setSelected(event.target.value)}><option value="">{t("Choose a project")}</option>{projects.map(row => <option key={row.id} value={row.id}>{row.fields?.title || t("Encrypted project")}{row.archived ? t(" · Archived") : ''}</option>)}</select></label><button className="fc-button" type="button" disabled={!!busy} onClick={() => { setEditor({ kind: 'project', id: crypto.randomUUID(), fields: { title: '', description: '', color: 'sage' } }); setHistory(null); }}><Plus size={15} />{t("New project")}</button></div>
    {loading ? <p role="status" className="fc-project-loading"><Loader2 size={17} className="fc-project-spinner" />{t("Loading projects…")}</p> : project ? <>
      <div className={`fc-project-summary fc-project-color-${project.fields?.color || 'sage'}`}><div><Folder size={19} /><h4>{project.fields?.title || t("Encrypted project")}</h4>{project.archived && <span className="fc-badge">{t("Archived")}</span>}</div><p>{project.fields?.description || project.resolution_error || t("A shared space for this channel’s work.")}</p><div className="fc-project-actions"><button className="fc-text-button" type="button" disabled={!!busy} onClick={() => void showHistory(project)}><History size={14} />{t("Project history")}</button>{project.fields && <button className="fc-text-button" type="button" disabled={!!busy || project.archived} onClick={() => setEditor({ kind: 'project', id: project.id, fields: clone(project.fields!), previous: project })}><Pencil size={14} />{t("Edit project")}</button>}{project.fields && canArchive(project) && <button className="fc-text-button" type="button" disabled={!!busy} onClick={() => setArchiving(project)}><Archive size={14} />{project.archived ? t("Reopen project") : t("Archive project")}</button>}</div></div>
      <div className="fc-project-toolbar fc-project-task-toolbar"><button className="fc-button fc-button-primary" type="button" disabled={!!busy || project.archived || !project.fields} onClick={() => { setEditor({ kind: 'task', id: crypto.randomUUID(), fields: freshTask() }); setHistory(null); }}><Plus size={15} />{t("New task")}</button><input className="fc-input" type="search" aria-label={t("Search project tasks")} placeholder={t("Find a task…")} value={search} onChange={event => setSearch(event.target.value)} /><label className="fc-project-check"><input type="checkbox" checked={mine} onChange={event => setMine(event.target.checked)} />{t("Assigned to me")}</label><label className="fc-project-check"><input type="checkbox" checked={showArchived} onChange={event => setShowArchived(event.target.checked)} />{t("Show archived tasks")}</label></div>
      {project.archived && <p className="fc-project-caption">{t("This project is archived. Its creator or a channel administrator can reopen it.")}</p>}
    </> : !editor && <div className="fc-project-empty"><CheckSquare size={28} /><p>{t("Create a project to organize the next steps for this channel.")}</p></div>}
    {editor && <form className="fc-project-editor" aria-label={editor.kind === 'project' ? editor.previous ? t("Edit project") : t("New project") : editor.previous ? t("Edit task") : t("New task")} onSubmit={save}>
      <div className="fc-project-heading"><h4>{editor.previous ? t("Edit ") : t("New ")}{editor.kind}</h4><button className="fc-icon-button" type="button" aria-label={t("Cancel task or project editing")} disabled={!!busy} onClick={() => setEditor(null)}><X size={17} /></button></div>
      <label htmlFor="project-item-title">{editor.kind === 'project' ? t("Project title") : t("Task title")}</label><input ref={editorTitle} id="project-item-title" className="fc-input" value={editor.fields.title} maxLength={editor.kind === 'project' ? 160 : 200} required disabled={!!busy} onChange={event => setEditor({ ...editor, fields: { ...editor.fields, title: event.target.value } } as Editor)} />
      <label htmlFor="project-item-description">{t("Description")}</label><textarea id="project-item-description" className="fc-input" rows={4} maxLength={editor.kind === 'project' ? 4000 : 12000} disabled={!!busy} value={editor.fields.description} onChange={event => setEditor({ ...editor, fields: { ...editor.fields, description: event.target.value } } as Editor)} />
      {editor.kind === 'project' ? <><label htmlFor="project-color">{t("Project color")}</label><select id="project-color" className="fc-input" value={editor.fields.color} disabled={!!busy} onChange={event => setEditor({ ...editor, fields: { ...editor.fields, color: event.target.value as ProjectFields['color'] } })}><option value="sage">{t("Sage")}</option><option value="blue">{t("Blue")}</option><option value="sand">{t("Sand")}</option><option value="rose">{t("Rose")}</option></select></> : <>
        <div className="fc-project-editor-grid"><label>{t("Stage")}<select className="fc-input" aria-label={t("Stage")} value={editor.fields.status} disabled={!!busy} onChange={event => changeTask({ status: event.target.value as TaskFields['status'] })}>{Object.entries(statuses).map(([key, label]) => <option key={key} value={key}>{t(label)}</option>)}</select></label><label>{t("Priority")}<select className="fc-input" aria-label={t("Priority")} value={editor.fields.priority} disabled={!!busy} onChange={event => changeTask({ priority: event.target.value as TaskFields['priority'] })}><option value="low">{t("Low")}</option><option value="normal">{t("Normal")}</option><option value="high">{t("High")}</option></select></label><label>{t("Responsible person")}<select className="fc-input" aria-label={t("Responsible person")} value={editor.fields.assignee_user_id || ''} disabled={!!busy} onChange={event => changeTask({ assignee_user_id: event.target.value || null })}><option value="">{t("Unassigned")}</option>{members.map(member => <option key={member.user_id} value={member.user_id}>{member.name}</option>)}{editor.fields.assignee_user_id && !members.some(member => member.user_id === editor.fields.assignee_user_id) && <option value={editor.fields.assignee_user_id}>{t("Former channel member · choose someone else")}</option>}</select></label><label>{t("Due date")}<input className="fc-input" type="date" value={editor.fields.due_date || ''} disabled={!!busy} onChange={event => changeTask({ due_date: event.target.value || null })} /></label></div>
        <fieldset className="fc-project-fieldset"><legend>{t("Checklist")}</legend>{editor.fields.checklist.map((item, index) => <div className="fc-project-checklist-edit" key={item.id}><input type="checkbox" aria-label={t("Complete checklist item {value0}", { value0: String(index + 1) })} checked={item.done} disabled={!!busy} onChange={event => changeTask({ checklist: editor.fields.checklist.map(row => row.id === item.id ? { ...row, done: event.target.checked } : row) })} /><input className="fc-input" aria-label={t("Checklist item {value0}", { value0: String(index + 1) })} value={item.title} maxLength={200} required disabled={!!busy} onChange={event => changeTask({ checklist: editor.fields.checklist.map(row => row.id === item.id ? { ...row, title: event.target.value } : row) })} /><button className="fc-icon-button" type="button" aria-label={t("Remove checklist item {value0}", { value0: String(index + 1) })} disabled={!!busy} onClick={() => changeTask({ checklist: editor.fields.checklist.filter(row => row.id !== item.id) })}><X size={15} /></button></div>)}<button className="fc-text-button" type="button" disabled={!!busy || editor.fields.checklist.length >= 40} onClick={() => changeTask({ checklist: [...editor.fields.checklist, { id: crypto.randomUUID(), title: '', done: false }] })}><Plus size={14} />{t("Add checklist item")}</button></fieldset>
        <fieldset className="fc-project-fieldset"><legend>{t("Linked channel files")}</legend>{files.map(file => <label className="fc-project-check" key={file.id}><input type="checkbox" disabled={!!busy} checked={editor.fields.file_ids.includes(file.id)} onChange={event => changeTask({ file_ids: event.target.checked ? [...editor.fields.file_ids, file.id] : editor.fields.file_ids.filter(id => id !== file.id) })} />{file.filename}</label>)}{editor.fields.file_ids.filter(id => !files.some(file => file.id === id)).map(id => <label className="fc-project-check" key={id}><input type="checkbox" checked disabled={!!busy} onChange={() => changeTask({ file_ids: editor.fields.file_ids.filter(value => value !== id) })} />{t("Unavailable file · remove this link")}</label>)}{!files.length && <p className="fc-project-caption">{t("Upload files in this channel to link them here.")}</p>}</fieldset>
        <fieldset className="fc-project-fieldset"><legend>{t("Linked channel decisions")}</legend>{decisions.map(decision => <label className="fc-project-check" key={decision.id}><input type="checkbox" disabled={!!busy} checked={editor.fields.decision_ids.includes(decision.id)} onChange={event => changeTask({ decision_ids: event.target.checked ? [...editor.fields.decision_ids, decision.id] : editor.fields.decision_ids.filter(id => id !== decision.id) })} />{decision.title || t("Encrypted decision")}</label>)}{editor.fields.decision_ids.filter(id => !decisions.some(decision => decision.id === id)).map(id => <label className="fc-project-check" key={id}><input type="checkbox" checked disabled={!!busy} onChange={() => changeTask({ decision_ids: editor.fields.decision_ids.filter(value => value !== id) })} />{t("Unavailable decision · remove this link")}</label>)}{!decisions.length && <p className="fc-project-caption">{t("Record a channel decision to connect it to this work.")}</p>}</fieldset>
      </>}
      <div className="fc-project-actions"><button className="fc-button" type="button" disabled={!!busy} onClick={() => setEditor(null)}>{t("Cancel")}</button><button className="fc-button fc-button-primary" type="submit" disabled={!!busy}>{busy === 'save' && <Loader2 size={15} className="fc-project-spinner" />}{t("Save ")}{editor.kind}</button></div>
    </form>}
    {archiving && <div className="fc-project-confirm" role="group" aria-label={t("Confirm archive change")}><h4>{archiving.archived ? t("Reopen") : t("Archive")} {archiving.kind}?</h4><p>{archiving.archived ? t("This item will be available for active work again.") : t("Keep its history while removing it from active work.")}</p><div className="fc-project-actions"><button className="fc-button" type="button" disabled={!!busy} onClick={() => setArchiving(null)}>{t("Cancel")}</button><button className="fc-button" type="button" disabled={!!busy} onClick={() => void archive()}>{archiving.archived ? t("Reopen item") : t("Archive item")}</button></div></div>}
    {history && <section className="fc-project-history" aria-label={t("Project or task revision history")}><div className="fc-project-heading"><h4>{history.title} {t(" · history")}</h4><button className="fc-icon-button" type="button" aria-label={t("Close revision history")} onClick={() => setHistory(null)}><X size={16} /></button></div><ol>{history.rows.map(row => <li key={row.revision}><span>{t("Version ")}{row.revision} · {new Date(row.revised_at.endsWith('Z') ? row.revised_at : row.revised_at + 'Z').toLocaleString(currentLocale())}</span><strong>{row.fields?.title || row.resolution_error || t("Encrypted revision unavailable")}{row.archived ? t(" · Archived") : ''}</strong>{row.fields?.description && <p>{row.fields.description}</p>}{row.kind === 'task' && row.fields && <small>{t(statuses[(row.fields as TaskFields).status])} · {(row.fields as TaskFields).checklist.filter(item => item.done).length}/{(row.fields as TaskFields).checklist.length} {t(" checklist items")}</small>}</li>)}</ol></section>}
    {linkedDecision && <section ref={decisionDetail} tabIndex={-1} className="fc-project-decision" aria-label={t("Linked decision details")}><div className="fc-project-heading"><div><span className="fc-kicker">{t("Linked channel decision · Version ")}{linkedDecision.revision}</span><h4>{linkedDecision.title}</h4></div><button className="fc-icon-button" type="button" aria-label={t("Close linked decision")} onClick={closeDecision}><X size={16} /></button></div><p className="fc-project-caption">{linkedDecision.status}{linkedDecision.decision_date ? t(" · {value0}", { value0: String(due(linkedDecision.decision_date)) }) : ''}{linkedDecision.responsible_user_id ? t(" · {value0}", { value0: String(members.find(member => member.user_id === linkedDecision.responsible_user_id)?.name || 'Former channel member') }) : ''}</p><p className="fc-project-decision-content">{linkedDecision.content}</p></section>}
    {selected && (tasksLoading ? <p className="fc-project-loading" role="status"><Loader2 size={17} className="fc-project-spinner" />{t("Loading tasks…")}</p> : <div className="fc-project-board" aria-label={t("Task Kanban board")}>{Object.entries(statuses).map(([status, label]) => <section className="fc-project-column" key={status} aria-label={t(label)}><h4>{t(label)}<span>{visible.filter(row => row.fields?.status === status).length}</span></h4>{visible.filter(row => row.fields?.status === status || !row.fields && status === 'todo').map(row => <article data-task-id={row.id} tabIndex={-1} className={`fc-project-card ${row.archived ? 'is-archived' : ''}`} key={row.id}>
      {row.fields ? <><div className="fc-project-card-meta"><span className={`fc-project-priority fc-project-priority-${row.fields.priority}`}>{t(({ low: 'Low', normal: 'Normal', high: 'High' })[row.fields.priority])} {t(" priority")}</span>{row.archived && <span>{t("Archived")}</span>}</div><h5>{row.fields.title}</h5>{row.fields.description && <p className="fc-project-card-description">{row.fields.description}</p>}<p className="fc-project-card-owner">{row.fields.assignee_user_id ? members.find(member => member.user_id === row.fields!.assignee_user_id)?.name || t("Former channel member") : t("Unassigned")}{row.fields.due_date && <span className={row.fields.status !== 'done' && row.fields.due_date < new Date().toISOString().slice(0, 10) ? 'is-overdue' : ''}>{t("Due ")}{due(row.fields.due_date)}</span>}</p>{row.fields.checklist.length > 0 && <ul className="fc-project-checklist">{row.fields.checklist.map(item => <li key={item.id}><label><input type="checkbox" checked={item.done} disabled={!!busy || project?.archived || row.archived} onChange={event => void updateTask(row, { ...row.fields!, checklist: row.fields!.checklist.map(check => check.id === item.id ? { ...check, done: event.target.checked } : check) })} /><span>{item.title}</span></label></li>)}</ul>}{(row.fields.file_ids.length > 0 || row.fields.decision_ids.length > 0) && <div className="fc-project-links">{row.fields.file_ids.map(id => { const file = files.find(item => item.id === id); return file ? <button key={id} className="fc-text-button" type="button" disabled={!!busy} aria-label={t("Download file {value0}", { value0: String(file.filename) })} onClick={() => void openFile(id)}>{t("File · ")}{file.filename}</button> : <span key={id}>{t("Unavailable file")}</span>; })}{row.fields.decision_ids.map(id => { const decision = decisions.find(item => item.id === id); return decision?.title && !decision.resolution_error ? <button key={id} className="fc-text-button" type="button" disabled={!!busy} aria-label={t("Open decision {value0}", { value0: String(decision.title) })} onClick={event => void openDecision(id, event.currentTarget)}>{t("Decision · ")}{decision.title}</button> : <span key={id}>{t("Unavailable decision")}</span>; })}</div>}<label className="fc-project-move">{t("Move task")}<select className="fc-input" aria-label={t("Move task {value0}", { value0: String(row.fields.title) })} value={row.fields.status} disabled={!!busy || project?.archived || row.archived} onChange={event => void updateTask(row, { ...row.fields!, status: event.target.value as TaskFields['status'] })}>{Object.entries(statuses).map(([key, title]) => <option key={key} value={key}>{t(title)}</option>)}</select></label></> : <p className="fc-project-caption">{row.resolution_error || t("Unlock encryption recovery to read this task.")}</p>}
      <div className="fc-project-actions"><button className="fc-text-button" type="button" disabled={!!busy} onClick={() => void showHistory(row)}><History size={13} />{t("History")}</button>{row.fields && <button className="fc-text-button" type="button" disabled={!!busy || project?.archived || row.archived} onClick={() => { setEditor({ kind: 'task', id: row.id, fields: clone(row.fields!), previous: row }); setHistory(null); }}><Pencil size={13} />{t("Edit")}</button>}{row.fields && canArchive(row) && <button className="fc-text-button" type="button" disabled={!!busy || project?.archived} onClick={() => setArchiving(row)}><Archive size={13} />{row.archived ? t("Reopen") : t("Archive")}</button>}</div>
    </article>)}{!visible.some(row => row.fields?.status === status || !row.fields && status === 'todo') && <p className="fc-project-column-empty">{t("No tasks here yet.")}</p>}</section>)}</div>)}
  </section>;
}
