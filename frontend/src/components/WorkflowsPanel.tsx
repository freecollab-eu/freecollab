import { useScopedWritingGuard } from '../lib/useScopedWritingGuard';
import { ScopedCollaborationInput as CollaborationInput } from './ScopedCollaborationInput';
import { FieldReferenceText } from './FieldReferenceText';
import type { MessageReference } from '../lib/message-references';
import { t, useI18n, currentLocale } from '../i18n';
import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { ArrowDown, Bell, Check, CheckCircle2, ChevronRight, Clock, History, Loader2, Pencil, Play, Plus, RefreshCw, ShieldCheck, Trash2, Workflow, X } from 'lucide-react';
import { api, type ChannelMember } from '../lib/api';
import { accountApi, type AccountSecurity } from '../lib/account-api';
import type { SSOReauthentication } from '../lib/integrations-api';
import { projectTasksApi, type ProjectRecord } from '../lib/project-tasks-api';
import { workflowsApi, type WorkflowDefinition, type WorkflowFields, type WorkflowList, type WorkflowRevision, type WorkflowRun, type WorkflowStep, type WorkflowTrigger } from '../lib/workflows-api';
import { EnterpriseAlerts, NativeConfirmation } from './EnterpriseControls';
import '../styles/workflows.css';

interface Props { instanceId?: string; workspaceId: string; channelId: string; isAdmin?: boolean }
const triggers: { value: WorkflowTrigger; label: string }[] = [
  { value: 'manual', label: 'Started by a channel member' }, { value: 'file_uploaded', label: 'File uploaded or version added' },
  { value: 'document_created', label: 'Shared document created' }, { value: 'decision_updated', label: 'Decision created or revised' },
  { value: 'task_updated', label: 'Task created or revised' },
];
const newStep = (person = ''): WorkflowStep => ({ kind: 'approval', recipients: person ? [person] : [], quorum: 1,
  reminder_minutes: 60, timeout_minutes: 1440, max_reminders: 2, escalation_recipients: [], project_id: null, task_title: null });
const blank = (person = ''): WorkflowFields => ({ name: '', trigger: 'manual', conditions: { actor: 'any', status: null }, steps: [newStep(person)] });
const date = (value?: string | null) => value ? new Date(/(?:Z|[+-]\d{2}:\d{2})$/.test(value) ? value : value + 'Z').toLocaleString(currentLocale(), { dateStyle: 'medium', timeStyle: 'short' }) : 'Not scheduled';
const human = (value: string) => value.replace(/_/g, ' ').replace(/^./, char => char.toUpperCase());
const errorText = (error: unknown) => error instanceof Error ? error.message : 'The workflow action could not be completed.';
const terminal = new Set(['completed', 'rejected', 'cancelled', 'timed_out', 'failed']);

export function WorkflowsPanel({ instanceId, workspaceId, channelId }: Props) {
  useI18n();
  const writingInstanceId = useId();
  const fieldId = (name: string) => instanceId ? instanceId + "-" + name : name;
  const activeWritingPalettes = useRef(new Set<string>());
  const voteScope = (run: WorkflowRun) => `${instanceId || writingInstanceId}:${channelId}:workflow:${run.id}:${run.step_index}:vote`;
  const [noteReferences, setNoteReferences] = useState<Record<string, MessageReference[]>>({});
  const [settings, setSettings] = useState<WorkflowList | null>(null), [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [members, setMembers] = useState<ChannelMember[]>([]), [projects, setProjects] = useState<ProjectRecord[]>([]);
  const [security, setSecurity] = useState<AccountSecurity | null>(null);
  const [editor, setEditor] = useState<{ fields: WorkflowFields; enabled: boolean; previous?: WorkflowDefinition } | null>(null);
  const [confirmation, setConfirmation] = useState<{ kind: 'save' } | { kind: 'disable'; value: WorkflowDefinition } | { kind: 'cancel'; value: WorkflowRun } | null>(null);
  const [selectedRun, setSelectedRun] = useState<WorkflowRun | null>(null), [revisions, setRevisions] = useState<{ name: string; items: WorkflowRevision[] } | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(''), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const mounted = useRef(true), token = useRef(api.getAccessToken()), generation = useRef(0), request = useRef(0);
  const foregroundRequest = useRef(false);
  const busyRef = useRef(''); busyRef.current = busy;
  const current = (epoch: number) => mounted.current && token.current === api.getAccessToken() && epoch === generation.current;
  const names = new Map(members.map(member => [member.user_id, member.name]));

  const editorDirty = !!editor && (JSON.stringify(editor.fields) !== JSON.stringify(editor.previous?.fields || blank(api.getCurrentUserId() || '')) || editor.enabled !== (editor.previous?.enabled ?? true));
  useScopedWritingGuard(channelId, editorDirty || Object.values(notes).some(note => !!note.trim()), !!busy, { workspaceId });
  async function load(quiet = false) {
    if (quiet && foregroundRequest.current) return;
    const epoch = generation.current, query = ++request.current;
    if (!quiet) { foregroundRequest.current = true; setLoading(true); setError(''); }
    try {
      const [state, history, people] = await Promise.all([workflowsApi.definitions(workspaceId, channelId), workflowsApi.runs(workspaceId, channelId), api.getChannelMembers(channelId)]);
      if (!current(epoch) || query !== request.current) return;
      setSettings(state); setRuns(history); setMembers(people);
      if (state.can_manage && !quiet) {
        const [account, boards] = await Promise.all([accountApi.security(), state.secure_channel ? Promise.resolve([]) : projectTasksApi.projects(channelId)]);
        if (!current(epoch) || query !== request.current) return;
        setSecurity(account); setProjects(boards.filter(project => !project.archived && !!project.fields));
      }
    } catch (failure) { if (current(epoch) && query === request.current && !quiet) setError(errorText(failure)); }
    finally { if (current(epoch) && query === request.current && !quiet) { foregroundRequest.current = false; setLoading(false); } }
  }
  useEffect(() => {
    mounted.current = true; token.current = api.getAccessToken(); ++generation.current; foregroundRequest.current = false;
    setSettings(null); setRuns([]); setEditor(null); setSelectedRun(null); setRevisions(null); setNotes({}); setNoteReferences({}); setConfirmation(null); setSecurity(null); setProjects([]);
    void load();
    const timer = window.setInterval(() => { if (!busyRef.current) void load(true); }, 5000);
    const signOut = () => { ++generation.current; setRuns([]); setSettings(null); setEditor(null); setSelectedRun(null); setRevisions(null); setNotes({}); setNoteReferences({}); setConfirmation(null); setSecurity(null); setProjects([]); };
    window.addEventListener('freecollab:auth-changed', signOut);
    return () => { mounted.current = false; ++generation.current; window.clearInterval(timer); window.removeEventListener('freecollab:auth-changed', signOut); };
  }, [workspaceId, channelId]);

  async function act(key: string, operation: (epoch: number) => Promise<void>) {
    if (busy || !current(generation.current)) return;
    const epoch = generation.current; setBusy(key); setError(''); setNotice('');
    try { await operation(epoch); } catch (failure) { if (current(epoch)) setError(errorText(failure)); }
    finally { if (current(epoch)) setBusy(''); }
  }
  function field<K extends keyof WorkflowFields>(name: K, value: WorkflowFields[K]) {
    setEditor(previous => previous ? { ...previous, fields: { ...previous.fields, [name]: value } } : previous);
  }
  function step(index: number, patch: Partial<WorkflowStep>) {
    if (!editor) return;
    field('steps', editor.fields.steps.map((value, number) => number === index ? { ...value, ...patch } : value));
  }
  function people(index: number, key: 'recipients' | 'escalation_recipients', person: string, checked: boolean) {
    if (!editor) return;
    const values = editor.fields.steps[index][key];
    step(index, { [key]: checked ? [...values, person] : values.filter(value => value !== person) });
  }
  function save(event: FormEvent) { event.preventDefault(); setConfirmation({ kind: 'save' }); }
  async function confirmed(values: SSOReauthentication) {
    const pending = confirmation; if (!pending) return;
    await act('configuration', async epoch => {
      if (pending.kind === 'save' && editor) await workflowsApi.save(workspaceId, channelId, editor.fields, editor.enabled, values, editor.previous);
      else if (pending.kind === 'disable') await workflowsApi.disable(workspaceId, channelId, pending.value.id, values);
      else if (pending.kind === 'cancel') await workflowsApi.cancel(workspaceId, channelId, pending.value.id, values);
      if (!current(epoch)) return;
      setConfirmation(null); if (pending.kind === 'save') setEditor(null);
      setNotice(pending.kind === 'save' ? t("Workflow saved. Its current administrator grant is checked before each action.") : pending.kind === 'disable' ? t("Workflow paused and its pending runs cancelled.") : t("Workflow run cancelled."));
      await load();
    });
  }
  async function vote(run: WorkflowRun, choice: 'approve' | 'reject') {
    if (activeWritingPalettes.current.has(voteScope(run))) return;
    await act(run.id, async epoch => {
      await workflowsApi.vote(workspaceId, channelId, run, choice, notes[run.id] || '', noteReferences[run.id]?.length ? { note: noteReferences[run.id] } : undefined);
      if (!current(epoch)) return;
      setNotes(previous => ({ ...previous, [run.id]: '' })); setNoteReferences(previous => { const next = { ...previous }; delete next[run.id]; return next; }); setNotice(t("Your choice is recorded once. The worker will update the next step.")); await load();
    });
  }

  return <div className="fc-workflows">
    <section className="fc-account-section"><div className="fc-account-section-heading"><div><span className="fc-kicker">{t("A little less administration")}</span><h3>{t("Workflows & approvals")}</h3></div><button type="button" className="fc-icon-button" aria-label={t("Refresh workflows")} disabled={loading || !!busy} onClick={() => void load()}><RefreshCw size={16} /></button></div>
      <p className="fc-account-muted">{t("Connect an event to deliberate actions. Approvals, reminders and generic in-app notices stay inside this channel’s current membership.")}</p>
      <EnterpriseAlerts error={error} notice={notice} />
      {loading ? <p role="status" className="fc-account-loading"><Loader2 size={17} className="fc-account-spinner" />{t("Loading workflows…")}</p> : settings && <>
        {!settings.enabled && <p className="fc-alert">{t("Workflow processing is paused on this installation. Existing definitions and history remain available.")}</p>}
        {settings.secure_channel && <div className="fc-integration-privacy"><ShieldCheck size={18} /><p>{t("Encrypted content stays on trusted devices. Workflows use activity metadata, generic notices and approval choices. Approval notes are encrypted on your device; content filters and server-created tasks are unavailable.")}</p></div>}
        {settings.can_manage ? <button type="button" className="fc-button fc-button-primary" disabled={!!busy || !security} onClick={() => { setEditor({ fields: blank(api.getCurrentUserId() || ''), enabled: true }); setRevisions(null); }}><Plus size={16} />{t("Create workflow")}</button> : <p className="fc-account-muted">{t("A personal workspace and channel administrator configures workflows with password/MFA confirmation. Members can start manual runs and respond to their own approvals.")}</p>}
        <div className="fc-workflow-definitions">{settings.definitions.map(definition => <article className="fc-workflow-card" key={definition.id}><div><Workflow size={20} /><div><h4>{definition.fields.name}</h4><p>{triggers.find(trigger => trigger.value === definition.fields.trigger)?.label} · {definition.fields.steps.length} {definition.fields.steps.length === 1 ? t("action") : t("actions")}</p><small>{t("Revision ")}{definition.revision} · {definition.enabled ? t("Active") : t("Paused")}</small></div></div><ol className="fc-workflow-mini-steps">{definition.fields.steps.map((action, index) => <li key={index}>{action.kind === 'approval' ? <ShieldCheck size={14} /> : action.kind === 'notify' ? <Bell size={14} /> : <CheckCircle2 size={14} />}<span>{action.kind === 'approval' ? t(action.quorum === 1 ? '{count} approval' : '{count} approvals', { count: action.quorum }) : action.kind === 'notify' ? t("Notify people") : t("Create task")}</span>{index + 1 < definition.fields.steps.length && <ChevronRight size={13} />}</li>)}</ol><div className="fc-account-actions">{definition.enabled && definition.fields.trigger === 'manual' && <button type="button" className="fc-button" disabled={!!busy || !settings.enabled} onClick={() => void act(definition.id, async epoch => { await workflowsApi.start(workspaceId, channelId, definition.id); if (current(epoch)) { setNotice(t("Workflow queued. Its actions run in the background.")); await load(); } })}><Play size={14} />{t("Start workflow")}</button>}<button type="button" className="fc-account-link" disabled={!!busy} onClick={() => void act('history', async epoch => { const result = await workflowsApi.revisions(workspaceId, channelId, definition.id); if (current(epoch)) setRevisions({ name: definition.fields.name, items: result.revisions }); })}><History size={14} />{t("Versions")}</button>{settings.can_manage && <><button type="button" className="fc-account-link" disabled={!!busy} onClick={() => setEditor({ previous: definition, fields: structuredClone(definition.fields), enabled: definition.enabled })}><Pencil size={14} />{t("Edit")}</button>{definition.enabled && <button type="button" className="fc-account-link" disabled={!!busy} onClick={() => setConfirmation({ kind: 'disable', value: definition })}>{t("Pause")}</button>}</>}</div></article>)}{!settings.definitions.length && <p className="fc-account-muted">{t("No workflows yet. Start with a manual approval or a review after a file upload.")}</p>}</div>
      </>}
    </section>

    {editor && <form className="fc-account-section fc-workflow-editor" onSubmit={save} aria-label={t("Workflow editor")}><div className="fc-account-section-heading"><h3>{editor.previous ? t("Edit workflow") : t("Create workflow")}</h3><button type="button" className="fc-icon-button" aria-label={t("Close workflow editor")} disabled={!!busy} onClick={() => { setEditor(null); setConfirmation(null); }}><X size={17} /></button></div><div className="fc-account-form"><label htmlFor={fieldId("workflow-name")}>{t("Workflow name")}</label><CollaborationInput as="input" allowReferences={false} channelId={channelId} contextId={`${instanceId || writingInstanceId}:${channelId}:workflow:${editor.previous?.id || "new"}:name`} id={fieldId("workflow-name")} className="fc-input" value={editor.fields.name} maxLength={100} required disabled={!!busy} onValueChange={value => field("name", value)} /><label htmlFor={fieldId("workflow-trigger")}>{t("When")}</label><select id={fieldId("workflow-trigger")} className="fc-input" value={editor.fields.trigger} disabled={!!busy} onChange={event => { field('trigger', event.target.value as WorkflowTrigger); setEditor(previous => previous ? { ...previous, fields: { ...previous.fields, trigger: event.target.value as WorkflowTrigger, conditions: { ...previous.fields.conditions, status: null } } } : previous); }}>{triggers.filter(trigger => !settings?.secure_channel || trigger.value !== 'file_uploaded').map(trigger => <option key={trigger.value} value={trigger.value}>{trigger.label}</option>)}</select><label htmlFor={fieldId("workflow-actor-condition")}>{t("Created by")}</label><select id={fieldId("workflow-actor-condition")} className="fc-input" value={editor.fields.conditions.actor} disabled={!!busy} onChange={event => field('conditions', { ...editor.fields.conditions, actor: event.target.value as 'any' | 'creator' })}><option value="any">{t("Any current channel member")}</option><option value="creator">{t("Only the administrator who saves this workflow")}</option></select>{['task_updated', 'decision_updated'].includes(editor.fields.trigger) && !settings?.secure_channel && <><label htmlFor={fieldId("workflow-status-condition")}>{t(editor.fields.trigger === 'task_updated' ? 'Only when task status is' : 'Only when decision status is')}</label><select id={fieldId("workflow-status-condition")} className="fc-input" value={editor.fields.conditions.status || ''} disabled={!!busy} onChange={event => field('conditions', { ...editor.fields.conditions, status: event.target.value ? event.target.value as WorkflowFields['conditions']['status'] : null })}><option value="">{t("Any status")}</option>{editor.fields.trigger === 'task_updated' ? <><option value="todo">{t("To do")}</option><option value="doing">{t("In progress")}</option><option value="done">{t("Done")}</option></> : <><option value="proposed">{t('Proposed')}</option><option value="accepted">{t('Accepted')}</option><option value="superseded">{t('Superseded')}</option></>}</select></>}</div>
      <div className="fc-workflow-canvas"><div className="fc-workflow-trigger"><Workflow size={17} /><span>{triggers.find(trigger => trigger.value === editor.fields.trigger)?.label}</span></div>{editor.fields.steps.map((action, index) => <div className="fc-workflow-step-wrap" key={index}><ArrowDown size={17} aria-hidden="true" /><fieldset className="fc-workflow-step"><legend>{t("Action ")}{index + 1}</legend><div className="fc-workflow-step-heading"><select className="fc-input" aria-label={t("Action {value0} type", { value0: String(index + 1) })} value={action.kind} disabled={!!busy} onChange={event => { const kind = event.target.value as WorkflowStep['kind']; step(index, { ...newStep(api.getCurrentUserId() || ''), kind, recipients: kind === 'create_task' ? [] : [api.getCurrentUserId() || ''], project_id: kind === 'create_task' ? projects[0]?.id || null : null, task_title: kind === 'create_task' ? '' : null }); }}><option value="approval">{t("Request approval")}</option><option value="notify">{t("Notify people in the app")}</option>{!settings?.secure_channel && <option value="create_task">{t("Create a task")}</option>}</select><button type="button" className="fc-icon-button" aria-label={t("Remove action {value0}", { value0: String(index + 1) })} disabled={!!busy || editor.fields.steps.length <= 1} onClick={() => field('steps', editor.fields.steps.filter((_, number) => number !== index))}><Trash2 size={15} /></button></div>{action.kind !== 'create_task' ? <fieldset className="fc-workflow-people"><legend>{action.kind === 'approval' ? t("Approvers") : t("Recipients")}</legend>{members.map(member => <label key={member.user_id}><input type="checkbox" disabled={!!busy} checked={action.recipients.includes(member.user_id)} onChange={event => people(index, 'recipients', member.user_id, event.target.checked)} />{member.name}</label>)}</fieldset> : <div className="fc-account-form"><label htmlFor={fieldId(`workflow-project-${index}`)}>{t("Project")}</label><select id={fieldId(`workflow-project-${index}`)} className="fc-input" value={action.project_id || ''} disabled={!!busy} required onChange={event => step(index, { project_id: event.target.value || null })}><option value="">{t("Choose a project")}</option>{projects.map(project => <option key={project.id} value={project.id}>{project.fields!.title}</option>)}</select><label htmlFor={fieldId(`workflow-task-title-${index}`)}>{t("Task title")}</label><CollaborationInput as="input" allowReferences={false} channelId={channelId} contextId={`${instanceId || writingInstanceId}:${channelId}:workflow:${editor.previous?.id || "new"}:task-template:${index}`} id={fieldId(`workflow-task-title-${index}`)} className="fc-input" value={action.task_title || ''} maxLength={200} required disabled={!!busy} onValueChange={value => step(index, { task_title: value })} /><p className="fc-account-muted">{t("Creates one To do task per run. It does not trigger another workflow.")}</p></div>}{action.kind === 'approval' && <><div className="fc-workflow-options">{([{ key: 'quorum', label: t("Approvals needed"), min: 1, max: 20 }, { key: 'reminder_minutes', label: t("Reminder every (minutes)"), min: 1, max: 10080 }, { key: 'timeout_minutes', label: t("Expire after (minutes)"), min: 1, max: 43200 }, { key: 'max_reminders', label: t("Maximum reminders"), min: 0, max: 3 }] as const).map(option => <label key={option.key}>{option.label}<input type="number" className="fc-input" min={option.min} max={option.max} value={action[option.key]} required disabled={!!busy} onChange={event => step(index, { [option.key]: Number(event.target.value) })} /></label>)}</div><fieldset className="fc-workflow-people"><legend>{t("Escalate after the last reminder (optional)")}</legend>{members.map(member => <label key={member.user_id}><input type="checkbox" checked={action.escalation_recipients.includes(member.user_id)} disabled={!!busy} onChange={event => people(index, 'escalation_recipients', member.user_id, event.target.checked)} />{member.name}</label>)}</fieldset><p className="fc-account-muted">{t("Escalated people may approve after escalation. A rejection stops the run. Removed or blocked people cannot vote.")}</p></>}</fieldset></div>)}</div><div className="fc-account-actions"><button type="button" className="fc-button" disabled={!!busy || editor.fields.steps.length >= 5} onClick={() => field('steps', [...editor.fields.steps, newStep(api.getCurrentUserId() || '')])}><Plus size={15} />{t("Add action")}</button><label className="fc-account-checkbox"><input type="checkbox" checked={editor.enabled} disabled={!!busy} onChange={event => setEditor({ ...editor, enabled: event.target.checked })} />{t("Enable after saving")}</label></div><p className="fc-account-muted">{t("Workflow names, steps and routing choices are administrator configuration. The worker reads no encrypted messages or documents and sends no external email.")}</p><div className="fc-account-actions"><button type="button" className="fc-button" disabled={!!busy} onClick={() => setEditor(null)}>{t("Cancel editing")}</button><button type="submit" className="fc-button fc-button-primary" disabled={!!busy || !security}><ShieldCheck size={15} />{t("Save workflow")}</button></div></form>}

    {confirmation && <NativeConfirmation id={fieldId("workflow-confirm")} title={confirmation.kind === 'save' ? t("Confirm workflow configuration") : confirmation.kind === 'disable' ? t("Pause workflow?") : t("Cancel this workflow run?")} mfa={!!security?.mfa_enabled} busy={!!busy} onCancel={() => setConfirmation(null)} onConfirm={confirmed}><p>{confirmation.kind === 'save' ? t("The worker acts only while the saving administrator retains both workspace and channel approval.") : t("Completed actions and retained history remain. Pending actions will stop.")}</p></NativeConfirmation>}

    <section className="fc-account-section"><div className="fc-account-section-heading"><div><span className="fc-kicker">{t("A clear next step")}</span><h3>{t("Approvals & run history")}</h3></div><Clock size={18} /></div><p className="fc-account-muted">{t("Recent runs refresh every five seconds. One choice per approver and step; changes in membership and administrator grants are checked again by the worker.")}</p><ul className="fc-workflow-runs">{runs.map(run => <li key={run.id}><div className="fc-workflow-run-heading"><div><h4>{run.workflow_name}</h4><p>{human(run.event_kind)} {t(" · Action ")}{Math.min(run.step_index + 1, run.step_count)} {t(" of ")}{run.step_count} · {date(run.created_at)}</p>{run.deadline_at && !terminal.has(run.status) && <small>{t("Approval deadline ")}{date(run.deadline_at)} · {run.reminder_count} {t(" reminders")}{run.escalated ? t(" · Escalated") : ''}</small>}{run.failure && <small>{human(run.failure)}</small>}</div><span className={`fc-badge ${run.status === 'completed' ? 'fc-account-success' : ''}`}>{human(run.status)}</span></div>{run.may_vote && <div className="fc-workflow-vote"><label htmlFor={fieldId(`workflow-note-${run.id}`)}>{t("Approval note (optional)")}</label><CollaborationInput as="textarea" toolbar channelId={channelId} submissionScope={voteScope(run)} onPaletteActiveChange={active => { if (active) activeWritingPalettes.current.add(voteScope(run)); else activeWritingPalettes.current.delete(voteScope(run)); }} contextId={`${instanceId || writingInstanceId}:${channelId}:workflow:${run.id}:note:${run.step_index}`} references={noteReferences[run.id] || []} id={fieldId(`workflow-note-${run.id}`)} className="fc-input" value={notes[run.id] || ''} maxLength={2000} disabled={!!busy} onValueChange={(value, refs) => { setNotes(previous => ({ ...previous, [run.id]: value })); setNoteReferences(previous => ({ ...previous, [run.id]: refs })); }} /><div className="fc-account-actions"><button type="button" data-collaboration-submit={voteScope(run)} className="fc-button fc-button-primary" disabled={!!busy} onClick={() => void vote(run, 'approve')}><Check size={15} />{t("Approve")}</button><button type="button" data-collaboration-submit={voteScope(run)} className="fc-button" disabled={!!busy} onClick={() => void vote(run, 'reject')}><X size={15} />{t("Reject")}</button></div></div>}<div className="fc-account-actions"><button type="button" className="fc-account-link" disabled={!!busy} onClick={() => void act('run-history', async epoch => { const result = await workflowsApi.run(workspaceId, channelId, run.id); if (current(epoch)) setSelectedRun(result); })}><History size={14} />{t("Run details")}</button>{settings?.can_manage && !terminal.has(run.status) && <button type="button" className="fc-account-link" disabled={!!busy} onClick={() => setConfirmation({ kind: 'cancel', value: run })}>{t("Cancel run")}</button>}</div></li>)}{!runs.length && !loading && <li className="fc-account-muted">{t("No workflow runs yet.")}</li>}</ul></section>
    {selectedRun && <section className="fc-account-section" aria-label={t("Workflow run details")}><div className="fc-account-section-heading"><h3>{selectedRun.workflow_name}</h3><button type="button" className="fc-icon-button" aria-label={t("Close workflow run details")} onClick={() => setSelectedRun(null)}><X size={17} /></button></div><h4>{t("Approval choices")}</h4><ul className="fc-workflow-history">{selectedRun.votes.map(vote => <li key={vote.step_index + vote.user_id}><strong>{names.get(vote.user_id) || t("Former channel member")} · {human(vote.choice)}</strong><span>{t("Action ")}{vote.step_index + 1} · {date(vote.created_at)}</span>{vote.note && <p><FieldReferenceText value={vote.note} references={vote.field_references?.note} channelId={channelId} /></p>}{vote.resolution_error && <p>{vote.resolution_error}</p>}</li>)}{!selectedRun.votes.length && <li>{t("No choices recorded.")}</li>}</ul><h4>{t("Activity")}</h4><ul className="fc-workflow-history">{selectedRun.activity?.map((event, index) => <li key={index}><strong>{human(event.kind)}</strong><span>{t("Action ")}{Math.min(event.step_index + 1, selectedRun.step_count)} · {date(event.created_at)}</span></li>)}</ul></section>}
    {revisions && <section className="fc-account-section" aria-label={t("Workflow version history")}><div className="fc-account-section-heading"><h3>{revisions.name} {t(" · versions")}</h3><button type="button" className="fc-icon-button" aria-label={t("Close workflow versions")} onClick={() => setRevisions(null)}><X size={17} /></button></div><ol className="fc-workflow-history">{revisions.items.map(revision => <li key={revision.revision}><strong>{t("Revision ")}{revision.revision}</strong><span>{date(revision.created_at)} · {revision.fields.steps.length} {t(" actions · ")}{triggers.find(trigger => trigger.value === revision.fields.trigger)?.label}</span></li>)}</ol></section>}
  </div>;
}
