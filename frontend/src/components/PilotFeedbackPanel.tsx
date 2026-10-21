import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ArrowUpRight, Check, ClipboardList, Loader2, RefreshCw, ShieldCheck, Trash2 } from 'lucide-react';
import { useI18n } from '../i18n';
import { APIError, api } from '../lib/api';
import { pilotDraft, pilotFeedbackApi, pilotTaskIds, type PilotDraft, type PilotFeedback, type PilotOwn, type PilotSummary, type PilotTask } from '../lib/pilot-feedback-api';
import '../styles/pilot-feedback.css';

export type PilotFeedbackGuard = 'busy' | 'dirty' | null;
export interface PilotFeedbackPanelProps {
  workspaceId: string;
  isAdmin?: boolean;
  onGuardChange?: (guard: PilotFeedbackGuard) => void;
  onOpenLaunch?: () => void;
  onOpenConnections?: () => void;
}
const failureText = (failure: unknown) => failure instanceof Error ? failure.message : 'Workspace feedback could not be saved. Please try again.';

/** Only explicit participant submissions are persisted. No task activity is observed. */
export function PilotFeedbackPanel(props: PilotFeedbackPanelProps) {
  return <PilotFeedbackContent key={props.workspaceId + ':' + (props.isAdmin === false ? 'member' : 'authority')} {...props} />;
}
function PilotFeedbackContent({ workspaceId, isAdmin, onGuardChange, onOpenLaunch, onOpenConnections }: PilotFeedbackPanelProps) {
  const { t, locale } = useI18n();
  const [own, setOwn] = useState<PilotOwn | null>(null), [summary, setSummary] = useState<PilotSummary | null>(null);
  const [selected, setSelected] = useState<PilotTask>('first_message'), [draft, setDraft] = useState<PilotDraft>(pilotDraft);
  const [consent, setConsent] = useState(false), [deleting, setDeleting] = useState(false);
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [expired, setExpired] = useState(false);
  const [summaryLoading, setSummaryLoading] = useState(false), [needsReload, setNeedsReload] = useState(false);
  const [error, setError] = useState(''), [summaryError, setSummaryError] = useState(''), [notice, setNotice] = useState('');
  const baseline = useRef(JSON.stringify(pilotDraft())), owner = useRef({ token: api.getAccessToken(), user: api.getCurrentUserId() });
  const mounted = useRef(true), generation = useRef(0), controller = useRef<AbortController | null>(null);
  const busyRef = useRef(false), loadingRef = useRef(true), dirtyRef = useRef(false), guardCallback = useRef(onGuardChange);
  const errorRef = useRef<HTMLParagraphElement>(null); guardCallback.current = onGuardChange;
  const dirty = JSON.stringify(draft) !== baseline.current || consent || deleting;
  const current = (version = generation.current) => mounted.current && version === generation.current && owner.current.token === api.getAccessToken() && owner.current.user === api.getCurrentUserId();
  function publish(nextDirty = dirtyRef.current) { dirtyRef.current = nextDirty; guardCallback.current?.(loadingRef.current || busyRef.current ? 'busy' : nextDirty ? 'dirty' : null); }
  function reset(row?: PilotFeedback) { const next = pilotDraft(row); baseline.current = JSON.stringify(next); setDraft(next); setConsent(false); setDeleting(false); publish(false); }
  function invalidate() {
    ++generation.current; controller.current?.abort(); setOwn(null); setSummary(null); reset(); setExpired(true); setLoading(false); setSummaryLoading(false); setNeedsReload(false); setBusy(false); setError(''); setSummaryError(''); setNotice(''); loadingRef.current = false; busyRef.current = false; publish(false);
  }
  function accessFailure(failure: unknown) { if (failure instanceof APIError && (failure.status === 401 || failure.status === 403)) { invalidate(); return true; } return false; }
  async function readSummary(allowed: boolean, version: number, signal: AbortSignal) {
    setSummary(null); setSummaryError(''); setSummaryLoading(allowed); if (!allowed) return;
    try { const result = await pilotFeedbackApi.summary(workspaceId, signal); if (current(version) && !signal.aborted) setSummary(result); }
    catch (failure) {
      if (!current(version) || signal.aborted) return;
      if (failure instanceof APIError && failure.status === 403) { setOwn(value => value ? { ...value, can_manage: false } : value); return; }
      if (!accessFailure(failure)) setSummaryError('The workspace summary could not be loaded. Your own feedback is still available.');
    } finally { if (current(version) && !signal.aborted) setSummaryLoading(false); }
  }
  async function load() {
    if (!current() || busyRef.current) return;
    controller.current?.abort(); const abort = new AbortController(); controller.current = abort; const version = ++generation.current;
    loadingRef.current = true; setLoading(true); publish(); setError(''); setNotice(''); setOwn(null); setSummary(null);
    try {
      const result = await pilotFeedbackApi.get(workspaceId, abort.signal); if (!current(version) || abort.signal.aborted) return;
      setOwn(result); setNeedsReload(false); reset(result.feedback.find(row => row.task === selected)); await readSummary(result.can_manage && isAdmin !== false, version, abort.signal);
    } catch (failure) { if (current(version) && !abort.signal.aborted && !accessFailure(failure)) setError(failureText(failure)); }
    finally { if (current(version) && !abort.signal.aborted) { loadingRef.current = false; setLoading(false); publish(); } }
  }
  useEffect(() => {
    mounted.current = true; owner.current = { token: api.getAccessToken(), user: api.getCurrentUserId() }; void load();
    const changed = () => invalidate(); window.addEventListener('freecollab:auth-changed', changed); window.addEventListener('freecollab:session-expired', changed);
    return () => { mounted.current = false; ++generation.current; controller.current?.abort(); guardCallback.current?.(null); window.removeEventListener('freecollab:auth-changed', changed); window.removeEventListener('freecollab:session-expired', changed); };
  }, [workspaceId]);
  useEffect(() => { dirtyRef.current = dirty; publish(dirty); }, [dirty, busy, loading]);
  useEffect(() => { if (error) errorRef.current?.focus(); }, [error]);
  function update(next: PilotDraft) { if (!current() || busyRef.current || loadingRef.current) return; setDraft(next); publish(JSON.stringify(next) !== baseline.current || consent || deleting); }
  function mayLeave() {
    if (!current() || expired || loadingRef.current || busyRef.current) return false;
    if (dirtyRef.current && !window.confirm(t('Discard your unsaved workspace feedback?'))) return false;
    return true;
  }
  function choose(task: PilotTask) { if (task === selected || !own || !mayLeave()) return; setSelected(task); reset(own.feedback.find(row => row.task === task)); setError(''); setNotice(''); }
  function shortcut(open?: () => void) { if (!open || !mayLeave()) return; reset(own?.feedback.find(row => row.task === selected)); open(); }
  async function mutate(operation: (version: number, signal: AbortSignal) => Promise<void>) {
    if (!current() || !own || loadingRef.current || busyRef.current || needsReload) return;
    controller.current?.abort(); const abort = new AbortController(); controller.current = abort; const version = ++generation.current;
    busyRef.current = true; setBusy(true); publish(); setError(''); setNotice('');
    try { await operation(version, abort.signal); }
    catch (failure) {
      if (!current(version) || abort.signal.aborted || accessFailure(failure)) return;
      if (failure instanceof APIError && failure.status === 409) setNeedsReload(true);
      setError(failure instanceof APIError && failure.status === 409 ? 'This feedback changed in another window. Your draft is kept here. Reload before saving again.' : failureText(failure));
    } finally { if (current(version) && !abort.signal.aborted) { busyRef.current = false; setBusy(false); publish(); } }
  }
  function save(event: FormEvent) {
    event.preventDefault(); if (!own || !consent) return;
    const revision = own.revisions[selected];
    void mutate(async (version, signal) => {
      const row = await pilotFeedbackApi.save(workspaceId, selected, draft, revision, consent, signal); if (!current(version) || signal.aborted) return;
      setOwn(value => value ? { ...value, revisions: { ...value.revisions, [selected]: row.revision }, feedback: [...value.feedback.filter(previous => previous.task !== selected), row] } : value);
      reset(row); setNotice('Your task feedback was saved.'); await readSummary(own.can_manage && isAdmin !== false, version, signal);
    });
  }
  function remove() {
    if (!own || !deleting || !own.feedback.some(row => row.task === selected)) return;
    void mutate(async (version, signal) => {
      const result = await pilotFeedbackApi.remove(workspaceId, selected, own.revisions[selected], signal); if (!current(version) || signal.aborted) return;
      setOwn(value => value ? { ...value, revisions: { ...value.revisions, [selected]: result.revision }, feedback: value.feedback.filter(row => row.task !== selected) } : value);
      reset(); setNotice('Your feedback for this task was deleted.'); await readSummary(own.can_manage && isAdmin !== false, version, signal);
    });
  }
  if (expired || !current()) return <section className="fc-pilot-feedback" aria-label={t('Workspace feedback')}><p className="fc-pilot-message" role="alert">{t('Your access changed. Reopen workspace feedback to continue.')}</p></section>;
  const locked = loading || busy, completed = own?.feedback.filter(row => row.outcome === 'completed').length || 0, reported = own?.feedback.length || 0;
  const active = own?.feedback.find(row => row.task === selected);
  return <section className="fc-pilot-feedback" aria-label={t('Workspace feedback')} aria-busy={locked}>
    <header className="fc-pilot-heading"><div><span className="fc-pilot-kicker">{t('Your workspace. Your feedback.')}</span><h3>{t('Share what worked.')}</h3><p>{t('Choose any everyday task you want to comment on. Every response is optional and stays in this workspace.')}</p></div><button className="fc-pilot-icon" type="button" aria-label={t('Reload workspace feedback')} disabled={locked} onClick={() => { if (mayLeave()) void load(); }}><RefreshCw size={17} /></button></header>
    <div className="fc-pilot-privacy"><ShieldCheck size={18} /><p>{t('Only pressing Save sends feedback. Your note is visible only to you and encrypted on the server, not end-to-end encrypted. Administrators see aggregate counts and ratings, without participant names or notes. Keep passwords, personal data and channel content out of notes.')}</p></div>
    {error && <p className="fc-pilot-message" role="alert" tabIndex={-1} ref={errorRef}>{t(error)}</p>}{notice && <p className="fc-pilot-message" role="status">{t(notice)}</p>}
    {loading && <p className="fc-pilot-loading" role="status"><Loader2 size={17} className="animate-spin" />{t('Loading your workspace feedback…')}</p>}
    {!loading && !own && <button type="button" disabled={busy} onClick={() => void load()}>{t('Retry workspace feedback')}</button>}
    {!loading && own && <>
      <div className="fc-pilot-progress"><span className="fc-pilot-status" data-status={reported === pilotTaskIds.length ? 'complete' : reported ? 'in_progress' : 'not_started'}><ClipboardList size={14} />{t(reported === pilotTaskIds.length ? 'Five outcomes recorded' : reported ? 'In progress' : 'Not started')}</span><span>{t('{count} of 5 outcomes recorded · {completed} reported completed', { count: reported, completed })}</span></div>
      <p className="fc-pilot-summary-note">{t('Use harmless content and your normal channel permissions. If access, credentials or a teammate are missing, you can record Blocked or leave the task unanswered.')}</p>
      <ol className="fc-pilot-task-list">{own.tasks.map((guide, index) => {
        const record = own.feedback.find(row => row.task === guide.id), open = selected === guide.id;
        return <li className="fc-pilot-task" key={guide.id} data-pilot-task={guide.id}>
          <fieldset disabled={locked}><legend><span className="fc-pilot-task-number">{String(index + 1).padStart(2, '0')}</span>{t(guide.title)}</legend><p>{t(guide.instructions)}</p>
            {!open && <button type="button" aria-label={t('Record feedback: {task}', { task: t(guide.title) })} onClick={() => choose(guide.id)}>{record?.outcome === 'completed' ? <Check size={14} /> : <ArrowUpRight size={14} />}{t(record ? record.outcome === 'completed' ? 'Reported completed · edit feedback' : 'Reported blocked · edit feedback' : 'Record an outcome')}</button>}
          </fieldset>
          {open && <form className="fc-pilot-form" aria-label={t('Feedback for {task}', { task: t(guide.title) })} onSubmit={save}>
            <fieldset disabled={locked}><legend>{t('What happened when you tried?')}</legend><div className="fc-pilot-outcomes">
              <label className="fc-pilot-outcome"><input type="radio" name={'pilot-outcome-' + guide.id} value="completed" checked={draft.outcome === 'completed'} onChange={() => update({ ...draft, outcome: 'completed' })} />{t('Completed')}</label>
              <label className="fc-pilot-outcome"><input type="radio" name={'pilot-outcome-' + guide.id} value="blocked" checked={draft.outcome === 'blocked'} onChange={() => update({ ...draft, outcome: 'blocked' })} />{t('Blocked')}</label>
            </div></fieldset>
            <div className="fc-pilot-note-fields"><label>{t('Your private note (optional)')}<textarea aria-label={t('Your private note (optional)')} value={draft.note} maxLength={1000} disabled={locked} onChange={event => update({ ...draft, note: event.target.value })} placeholder={t('What helped, or what got in the way?')} /><span className="fc-pilot-note-hint">{t('{count}/1000 characters. Visible only to you.', { count: draft.note.length })}</span></label><label>{t('Ease of this task (optional)')}<select aria-label={t('Ease of this task (optional)')} value={draft.rating} disabled={locked} onChange={event => update({ ...draft, rating: event.target.value })}><option value="">{t('No rating')}</option>{[1, 2, 3, 4, 5].map(value => <option key={value} value={value}>{t(value === 1 ? '1 · Very difficult' : value === 2 ? '2 · Difficult' : value === 3 ? '3 · Neutral' : value === 4 ? '4 · Easy' : '5 · Very easy')}</option>)}</select></label></div>
            <label className="fc-pilot-consent"><input type="checkbox" checked={consent} disabled={locked} onChange={event => { setConsent(event.target.checked); publish(JSON.stringify(draft) !== baseline.current || event.target.checked || deleting); }} /><span>{t('I choose to save this self-reported outcome and optional rating or private note for this task.')}</span></label>
            <div className="fc-pilot-actions"><button className="fc-pilot-primary" disabled={locked || needsReload || !draft.outcome || !consent}>{busy ? <Loader2 size={15} className="animate-spin" /> : <Check size={15} />}{t('Save my task feedback')}</button>{active && <><span className="fc-pilot-saved">{t('Last saved {date}', { date: new Date(active.updated_at).toLocaleString(locale, { dateStyle: 'medium', timeStyle: 'short' }) })}</span><button type="button" className="fc-pilot-danger" disabled={locked} onClick={() => { setDeleting(true); publish(true); }}><Trash2 size={14} />{t('Delete my task feedback')}</button></>}</div>
            {deleting && <div className="fc-pilot-confirm" role="group" aria-label={t('Confirm feedback deletion')}><p>{t('Delete your saved outcome, rating and note for this task? Aggregate counts will update. This does not undo any action you performed in the product.')}</p><div className="fc-pilot-actions"><button type="button" disabled={locked} onClick={() => { setDeleting(false); publish(JSON.stringify(draft) !== baseline.current || consent); }}>{t('Keep my feedback')}</button><button type="button" className="fc-pilot-danger" disabled={locked || needsReload} onClick={remove}>{t('Confirm deletion')}</button></div></div>}
          </form>}
        </li>;
      })}</ol>
      {(onOpenConnections || onOpenLaunch) && own.can_manage && isAdmin !== false && <nav className="fc-pilot-shortcuts" aria-label={t('Workspace tools')}>
        {onOpenConnections && <button type="button" disabled={locked} onClick={() => shortcut(onOpenConnections)}>{t('Open connection setup')}<ArrowUpRight size={14} /></button>}
        {onOpenLaunch && own.can_manage && <button type="button" disabled={locked} onClick={() => shortcut(onOpenLaunch)}>{t('Open launch checklist')}<ArrowUpRight size={14} /></button>}
      </nav>}
      {own.can_manage && isAdmin !== false && <section className="fc-pilot-overview" aria-label={t('Workspace feedback summary')}>
        <div className="fc-pilot-overview-heading"><div><h4>{t('Feedback from this workspace')}</h4><p>{t('This local summary includes optional self-reports from current members. It does not observe activity or assess whether the workspace is ready to launch.')}</p></div><span className="fc-pilot-status">{t(summaryLoading ? 'Loading summary…' : summary ? summary.distinct_participants ? 'Reports available' : 'No reports yet' : 'Summary unavailable')}</span></div>
        {summaryError && <p className="fc-pilot-message" role="alert">{t(summaryError)}</p>}
        {summary && <><dl className="fc-pilot-metrics"><div><dt>{t('Members who shared feedback')}</dt><dd>{summary.distinct_participants}</dd></div><div><dt>{t('Task reports')}</dt><dd>{summary.feedback_count}</dd></div><div><dt>{t('Reported completed')}</dt><dd>{summary.tasks.reduce((sum, row) => sum + row.completed, 0)}</dd></div></dl>
          <ul className="fc-pilot-summary-list">{summary.tasks.map(row => <li key={row.task}><strong>{t(own.tasks.find(task => task.id === row.task)!.title)}</strong><div className="fc-pilot-summary-counts"><span><b>{row.completed}</b> {t('reported completed')}</span><span><b>{row.blocked}</b> {t('reported blocked')}</span><span>{row.rating_count ? t('Mean ease {rating}/5 from {count} ratings', { rating: new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(row.rating_total / row.rating_count), count: row.rating_count }) : t('No ratings')}</span></div></li>)}</ul><p className="fc-pilot-summary-note">{t('Each person counts once per task. Deleting feedback or losing workspace access removes it from this overview. Participant identities and private notes are excluded.')}</p></>}
      </section>}
    </>}
  </section>;
}
