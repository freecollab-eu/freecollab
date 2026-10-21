import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ArrowUpRight, Bell, CalendarDays, Check, CheckCheck, Clock3, LockKeyhole, RefreshCw, Sun, X } from 'lucide-react';
import { currentLocale, t, useI18n } from '../i18n';
import { api } from '../lib/api';
import { chatProductivity } from '../lib/chat-productivity';
import { projectTasksApi, type TaskRecord } from '../lib/project-tasks-api';
import { expandCalendarOccurrences } from '../lib/calendar';
import { loadMyDay, openPersonalSource, type MyDaySnapshot } from '../lib/my-day';
import { useModalFocus } from '../lib/useModalFocus';
import '../styles/my-day.css';

type Section = 'all' | 'tasks' | 'calendar' | 'attention';
const empty: MyDaySnapshot = { generated_at: '', channels: [], tasks: [], calendar: [], notifications: [], reminders: [], saved: [], incomplete: false };
const describe = (failure: unknown) => failure instanceof Error ? failure.message : 'Your day could not be loaded.';
export function MyDay({ onClose }: { onClose: () => void }) {
  useI18n();
  const [data, setData] = useState<MyDaySnapshot>(empty), [loading, setLoading] = useState(true), [busy, setBusy] = useState(''), [error, setError] = useState('');
  const [section, setSection] = useState<Section>('all');
  const dialog = useRef<HTMLElement>(null), generation = useRef(0), controller = useRef<AbortController | null>(null), owner = useRef(api.getAccessToken());
  const close = useRef(onClose), pendingAction = useRef(false), mounted = useRef(false); close.current = onClose;
  const current = (request: number) => mounted.current && request === generation.current && owner.current === api.getAccessToken();
  useModalFocus(dialog, onClose);
  const load = useCallback(async () => {
    const request = ++generation.current; controller.current?.abort(); const abort = new AbortController(); controller.current = abort;
    setLoading(true); setError('');
    // Clear the old snapshot during reauthorization. Revoked source text must
    // not remain visible behind a failed refresh.
    setData(empty);
    try { const result = await loadMyDay(abort.signal); if (current(request)) setData(result); }
    catch (failure) { if (current(request) && !abort.signal.aborted) setError(describe(failure)); }
    finally { if (current(request)) setLoading(false); }
  }, []);
  useEffect(() => {
    mounted.current = true;
    void load();
    const auth = () => { if (owner.current === api.getAccessToken()) return; ++generation.current; controller.current?.abort(); setData(empty); close.current(); };
    const refresh = () => { if (document.visibilityState === 'visible' && !pendingAction.current) void load(); };
    window.addEventListener('freecollab:auth-changed', auth); window.addEventListener('storage', auth); document.addEventListener('visibilitychange', refresh);
    const timer = window.setInterval(refresh, 60000);
    return () => { mounted.current = false; ++generation.current; controller.current?.abort(); window.clearInterval(timer); window.removeEventListener('freecollab:auth-changed', auth); window.removeEventListener('storage', auth); document.removeEventListener('visibilitychange', refresh); };
  }, [load]);
  const source = (channelId: string, kind: 'task' | 'calendar' | 'message', itemId?: string) => {
    const channel = data.channels.find(row => row.id === channelId);
    if (!channel) return;
    openPersonalSource({ workspace_id: channel.workspace_id, channel_id: channel.id, kind, item_id: itemId,
      ...(kind === 'task' ? { project_id: data.tasks.find(row => row.id === itemId)?.project_id || undefined } : {}) });
  };
  const context = (channelId: string) => {
    const channel = data.channels.find(row => row.id === channelId);
    return channel ? `${channel.workspace_name} · ${channel.name || t('Encrypted channel')}` : '';
  };
  async function act(key: string, action: () => Promise<unknown>) {
    if (pendingAction.current) return;
    const token = api.getAccessToken(); pendingAction.current = true; setBusy(key); setError('');
    try { await action(); if (mounted.current && token === api.getAccessToken()) await load(); }
    catch (failure) { if (mounted.current && token === api.getAccessToken()) { setData(empty); setError(describe(failure)); } }
    finally { pendingAction.current = false; if (mounted.current && token === api.getAccessToken()) setBusy(''); }
  }
  const completeTask = (row: TaskRecord) => act('task:' + row.id, () => projectTasksApi.saveTask(row.channel_id, row.project_id!, { ...row.fields!, status: 'done' }, row));
  const start = new Date(); start.setHours(0, 0, 0, 0); const end = new Date(start); end.setDate(end.getDate() + 7);
  const plannedMeetings = data.calendar.flatMap(row => row.fields ? expandCalendarOccurrences(row.fields, start, end).map(occurrence => ({ row, occurrence })) : [])
    .sort((a, b) => a.occurrence.starts_at.localeCompare(b.occurrence.starts_at));
  const meetings = plannedMeetings.slice(0, 100), limited = data.incomplete || plannedMeetings.length > 100;
  const attention = data.reminders.length + data.notifications.length + data.saved.length;
  const show = (value: Section) => section === 'all' || section === value;
  const date = (value: string) => new Date(value.length === 10 ? value + 'T12:00:00' : /(?:Z|[+-]\d\d:\d\d)$/.test(value) ? value : value + 'Z');
  const time = (value: string) => date(value).toLocaleString(currentLocale(), { weekday: 'short', month: 'short', day: 'numeric', ...(value.length === 10 ? {} : { hour: '2-digit', minute: '2-digit' }) });
  return createPortal(<div className="fc-overlay fc-day-overlay"><section ref={dialog} tabIndex={-1} className="fc-day" role="dialog" aria-modal="true" aria-label={t('My day')}>
    <header className="fc-day-header"><div><span className="fc-kicker"><Sun size={15} />{t('A little more clarity')}</span><h2>{t('My day')}</h2><p>{new Date().toLocaleDateString(currentLocale(), { weekday: 'long', day: 'numeric', month: 'long' })}</p></div><div className="fc-day-header-actions"><button type="button" disabled={loading || !!busy} className="fc-icon-button" aria-label={t('Refresh my day')} onClick={() => void load()}><RefreshCw size={19} className={loading ? 'fc-day-spin' : ''} /></button><button type="button" className="fc-icon-button" aria-label={t('Close my day')} onClick={onClose}><X size={20} /></button></div></header>
    <p className="fc-day-intro">{t('Your next steps, across the places you work.')}</p>
    <nav className="fc-day-filters" aria-label={t('My day views')}>{([
      ['all', 'Overview', Sun, data.tasks.length + meetings.length + attention], ['tasks', 'Assigned to me', CheckCheck, data.tasks.length],
      ['calendar', 'Next seven days', CalendarDays, meetings.length], ['attention', 'Follow up', Bell, attention],
    ] as const).map(([value, label, Icon, count]) => <button type="button" key={value} aria-pressed={section === value} onClick={() => setSection(value)}><Icon size={17} /><span>{t(label)}</span><strong>{count}</strong></button>)}</nav>
    {error && <div className="fc-alert" role="alert">{t(error)} <button type="button" className="fc-text-button" onClick={() => void load()}>{t('Retry')}</button></div>}
    {loading && <p className="fc-day-empty" role="status">{t('Gathering your day…')}</p>}
    {!loading && !error && <>
      {(limited || !!data.locked) && <p className="fc-day-notice" role="status"><LockKeyhole size={16} />{data.locked ? t('Some encrypted items need device recovery. Open their channel to unlock them.') : t('This view has reached its scan limit. Open individual channels for the complete list.')}{data.locked && limited ? ' ' + t('This view has reached its scan limit. Open individual channels for the complete list.') : ''}</p>}
      <div className="fc-day-columns">
        {show('tasks') && <section className="fc-day-section"><h3><CheckCheck size={18} />{t('Assigned to me')}<span>{data.tasks.length}</span></h3>{!data.tasks.length && <p className="fc-day-empty">{t('No open tasks assigned to you.')}</p>}<ul>{data.tasks.map(row => <li key={row.id}><button type="button" className="fc-day-complete" aria-label={t('Complete task {title}', { title: row.fields!.title })} disabled={!!busy} onClick={() => void completeTask(row)}><Check size={15} /></button><div><button type="button" className="fc-day-item-link" onClick={() => source(row.channel_id, 'task', row.id)}>{row.fields!.title}</button><small>{context(row.channel_id)}</small>{row.fields!.due_date && <time dateTime={row.fields!.due_date} className={row.fields!.due_date < start.toLocaleDateString('en-CA') ? 'is-overdue' : ''}>{time(row.fields!.due_date)}</time>}</div><span className="fc-day-priority">{row.fields!.priority === 'high' ? t('High priority') : row.fields!.status === 'doing' ? t('In progress') : ''}</span></li>)}</ul></section>}
        {show('calendar') && <section className="fc-day-section"><h3><CalendarDays size={18} />{t('Next seven days')}<span>{meetings.length}</span></h3>{!meetings.length && <p className="fc-day-empty">{t('A little room in your calendar.')}</p>}<ul>{meetings.map(({ row, occurrence }) => <li key={`${row.id}:${occurrence.original_start}`}><span className="fc-day-event-icon"><CalendarDays size={17} /></span><div><button type="button" className="fc-day-item-link" onClick={() => source(row.channel_id, 'calendar', row.id)}>{occurrence.title}</button><time dateTime={occurrence.starts_at}>{time(occurrence.starts_at)}</time><small>{context(row.channel_id)}{occurrence.location ? ' · ' + occurrence.location : ''}</small></div><button type="button" className="fc-icon-button" aria-label={t('Open calendar')} onClick={() => source(row.channel_id, 'calendar', row.id)}><ArrowUpRight size={17} /></button></li>)}</ul></section>}
        {show('attention') && <section className="fc-day-section fc-day-followup"><h3><Bell size={18} />{t('Follow up')}<span>{attention}</span></h3>{!attention && <p className="fc-day-empty">{t('Nothing waiting for your attention.')}</p>}<ul>
          {data.reminders.map(row => <li key={'reminder:' + row.id}><Clock3 size={18} /><div><button type="button" className="fc-day-item-link" onClick={() => source(row.channel_id, 'message', row.item_id)}>{t('Personal reminder')}</button><time dateTime={row.due_at}>{time(row.due_at)}</time><small>{context(row.channel_id)}</small></div><div className="fc-day-row-actions"><button type="button" className="fc-button" disabled={!!busy} onClick={() => void act('snooze:' + row.id, () => chatProductivity.snoozeReminder(row.id, new Date(Date.now() + 3600000).toISOString()))}>{t('In one hour')}</button><button type="button" className="fc-day-complete" disabled={!!busy} aria-label={t('Complete reminder')} onClick={() => void act('reminder:' + row.id, () => chatProductivity.completeReminder(row.id))}><Check size={16} /></button></div></li>)}
          {data.notifications.map(row => <li key={'notification:' + row.id}><Bell size={18} /><div><button type="button" className="fc-day-item-link" onClick={() => source(row.channel_id, 'message', row.entity_type === 'message' || row.entity_type === 'thread' || row.entity_type === 'encrypted_message' ? row.entity_id || undefined : undefined)}>{row.type === 'mention' ? t('You were mentioned') : row.type === 'reply' ? t('A conversation continued') : t('Unread activity')}</button><small>{context(row.channel_id)}</small></div><button type="button" disabled={!!busy} className="fc-day-complete" aria-label={t('Mark activity read')} onClick={() => void act('notification:' + row.id, () => api.markNotificationRead(row.id))}><Check size={16} /></button></li>)}
          {data.saved.map(row => <li key={'saved:' + row.channel_id + ':' + row.item_id}><CheckCheck size={18} /><div><button type="button" className="fc-day-item-link" onClick={() => source(row.channel_id, 'message', row.item_id)}>{t('Saved for later')}</button><small>{context(row.channel_id)}</small></div><button type="button" disabled={!!busy} className="fc-day-complete" aria-label={t('Complete saved follow-up')} onClick={() => void act('saved:' + row.item_id, () => chatProductivity.completeSaved(row.channel_id, row.item_id, true))}><Check size={16} /></button></li>)}
        </ul></section>}
      </div>
    </>}
    <footer className="fc-day-footer"><LockKeyhole size={14} /><span>{t('Your current access applies. Encrypted content stays on your device.')}</span></footer>
  </section></div>, document.body);
}
