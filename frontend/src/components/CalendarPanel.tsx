import { t, useI18n, currentLocale } from '../i18n';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { CalendarDays, Download, History, Loader2, Pencil, Plus, RefreshCw, Trash2, Upload, X } from 'lucide-react';
import { api } from '../lib/api';
import { documentCalendarApi, type CalendarRecord } from '../lib/document-calendar-api';
import { calendarComposeUrl, calendarWallInput, exportCalendar, importCalendar, validateCalendar, zonedInstant, type CalendarFields } from '../lib/calendar';
import { CalendarDeliveryPanel } from './CalendarDeliveryPanel';
import { CalendarAvailabilityFinder, CalendarEventPlanning } from './CalendarPlanning';
import { ChannelSyncNotice } from './ChannelSyncNotice';
import '../styles/integration-content.css';

interface Props { channelId: string; isAdmin?: boolean }
interface Editor { id: string; previous?: CalendarRecord; fields: CalendarFields }
interface ImportItem { id: string; fields: CalendarFields }
const localZone = () => Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
const describe = (error: unknown) => error instanceof Error ? error.message : 'The calendar action could not be completed.';
const shiftDay = (value: string, days: number) => new Date(Date.parse(value + 'T00:00:00Z') + days * 86400000).toISOString().slice(0, 10);
const blank = (): Editor => {
  const zone = localZone(), start = new Date(Date.now() + 3600000), end = new Date(start.getTime() + 3600000);
  return { id: crypto.randomUUID(), fields: { title: '', description: '', location: '',
    starts_at: calendarWallInput(start.toISOString(), zone), ends_at: calendarWallInput(end.toISOString(), zone),
    all_day: false, timezone: zone, reminder_minutes: 15, external_uid: null } };
};
function editing(row: CalendarRecord): Editor {
  const f = row.fields!;
  return { id: row.id, previous: row, fields: { ...f,
    starts_at: f.all_day ? f.starts_at : calendarWallInput(f.starts_at, f.timezone),
    ends_at: f.all_day ? shiftDay(f.ends_at, -1) : calendarWallInput(f.ends_at, f.timezone) } };
}
function dateLabel(f: CalendarFields): string {
  if (f.all_day) {
    const format = (value: string) => new Date(value + 'T00:00:00Z').toLocaleDateString(currentLocale(), { timeZone: 'UTC', dateStyle: 'medium' });
    const last = shiftDay(f.ends_at, -1);
    return format(f.starts_at) + (last === f.starts_at ? '' : ' – ' + format(last)) + ' · All day';
  }
  const formatter = new Intl.DateTimeFormat(currentLocale(), { timeZone: f.timezone, dateStyle: 'medium', timeStyle: 'short' });
  return formatter.format(new Date(f.starts_at)) + ' – ' + formatter.format(new Date(f.ends_at)) + ' · ' + f.timezone;
}
function download(rows: Array<{ id: string; fields: CalendarFields }>, name: string) {
  const blob = new Blob([exportCalendar(rows)], { type: 'text/calendar;charset=utf-8' });
  const url = URL.createObjectURL(blob), link = document.createElement('a');
  link.href = url; link.download = name; document.body.appendChild(link); link.click(); link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function CalendarPanel({ channelId, isAdmin = false }: Props) {
  useI18n();
  const [rows, setRows] = useState<CalendarRecord[]>([]), [editor, setEditor] = useState<Editor | null>(null);
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(''), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [history, setHistory] = useState<{ id: string; rows: CalendarRecord[] } | null>(null), [removing, setRemoving] = useState('');
  const [pendingImport, setPendingImport] = useState<ImportItem[] | null>(null), [progress, setProgress] = useState('');
  const fileInput = useRef<HTMLInputElement>(null), generation = useRef(0), identity = useRef(api.getAccessToken());
  const current = (request: number) => request === generation.current && identity.current === api.getAccessToken();
  const unreadable = rows.some(row => !row.fields);
  async function load(request = generation.current) {
    setLoading(true); setError('');
    try { const result = await documentCalendarApi.calendar(channelId); if (current(request)) setRows(result); }
    catch (failure) { if (current(request)) setError(describe(failure)); }
    finally { if (current(request)) setLoading(false); }
  }
  useEffect(() => {
    const request = ++generation.current; identity.current = api.getAccessToken();
    setRows([]); setEditor(null); setHistory(null); setRemoving(''); setError(''); setNotice(''); setBusy(''); setPendingImport(null); setProgress('');
    void load(request);
    const signedOut = () => { ++generation.current; setRows([]); setEditor(null); setHistory(null); setRemoving(''); setPendingImport(null); setLoading(false); setBusy(''); };
    window.addEventListener('freecollab:auth-changed', signedOut);
    return () => { ++generation.current; window.removeEventListener('freecollab:auth-changed', signedOut); };
  }, [channelId]);
  async function run(key: string, action: (request: number) => Promise<void>) {
    if (busy) return; const request = generation.current; setBusy(key); setError(''); setNotice('');
    try { await action(request); } catch (failure) { if (current(request)) setError(describe(failure)); }
    finally { if (current(request)) { setBusy(''); setProgress(''); } }
  }
  async function save(event: FormEvent) {
    event.preventDefault(); if (!editor) return;
    const pending = editor;
    await run('save', async request => {
      const draft = pending.fields;
      const fields = validateCalendar({ ...draft, starts_at: draft.all_day ? draft.starts_at : zonedInstant(draft.starts_at, draft.timezone),
        ends_at: draft.all_day ? shiftDay(draft.ends_at, 1) : zonedInstant(draft.ends_at, draft.timezone) });
      const saved = await documentCalendarApi.saveEvent(channelId, fields, pending.previous, pending.id);
      if (!current(request)) return;
      if (saved.resolution_error) throw new Error(saved.resolution_error);
      setRows(previous => [saved, ...previous.filter(item => item.id !== saved.id)]); setEditor(null); setHistory(null);
      setNotice(t("Calendar event saved. Export it to your calendar app to activate its reminder."));
    });
  }
  async function remove(row: CalendarRecord) {
    await run('remove:' + row.id, async request => {
      await documentCalendarApi.removeEvent(channelId, row); if (!current(request)) return;
      setRows(previous => previous.filter(item => item.id !== row.id)); setRemoving(''); setHistory(null);
      setNotice(t("Event removed from this channel. Copies already added to external calendars are managed there."));
    });
  }
  async function showHistory(row: CalendarRecord) {
    if (history?.id === row.id) { setHistory(null); return; }
    await run('history:' + row.id, async request => {
      const result = await documentCalendarApi.history<CalendarFields>(channelId, 'calendar', row.id);
      if (current(request)) setHistory({ id: row.id, rows: result });
    });
  }
  async function readFile(file: File | undefined) {
    if (!file) return;
    await run('parse', async request => {
      if (file.size > 1024 * 1024) throw new Error(t("Use an ICS file smaller than 1 MB."));
      const fields = importCalendar(await file.text(), localZone());
      if (current(request)) { setPendingImport(fields.map(value => ({ id: crypto.randomUUID(), fields: value }))); setEditor(null); }
    });
    if (fileInput.current) fileInput.current.value = '';
  }
  async function addImported() {
    if (!pendingImport) return;
    const pending = pendingImport;
    await run('import', async request => {
      const latest = await documentCalendarApi.calendar(channelId); if (!current(request)) return;
      if (latest.some(row => !row.fields)) throw new Error(t("Unlock existing encrypted events before importing a calendar."));
      const uids = new Set(latest.map(row => row.fields?.external_uid || row.id + '@freecollab.local')), ids = new Set(latest.map(row => row.id));
      let added = 0, skipped = 0;
      setRows(latest);
      for (const [index, item] of pending.entries()) {
        if (!current(request)) return;
        setProgress(`Importing ${index + 1} of ${pending.length} events…`);
        if (ids.has(item.id) || item.fields.external_uid && uids.has(item.fields.external_uid)) { skipped++; continue; }
        const saved = await documentCalendarApi.saveEvent(channelId, item.fields, undefined, item.id);
        if (!current(request)) return;
        if (saved.resolution_error) throw new Error(saved.resolution_error);
        ids.add(saved.id); uids.add(saved.fields?.external_uid || saved.id + '@freecollab.local');
        setRows(previous => [saved, ...previous.filter(row => row.id !== saved.id)]); added++;
      }
      setPendingImport(null); setNotice(`${added} event${added === 1 ? '' : 's'} imported${skipped ? `; ${skipped} existing event${skipped === 1 ? '' : 's'} skipped` : ''}. No invitations were sent.`);
    });
  }
  function exportAll() {
    try { download(rows.filter(row => !!row.fields).map(row => ({ id: row.id, fields: row.fields! })), 'freecollab-calendar.ics'); setNotice(t("Calendar downloaded. Its decrypted details are now in the file on your device.")); }
    catch (failure) { setError(describe(failure)); }
  }
  function changeAllDay(checked: boolean) {
    if (!editor) return;
    const f = editor.fields;
    setEditor({ ...editor, fields: { ...f, all_day: checked, starts_at: checked ? f.starts_at.slice(0, 10) : f.starts_at + 'T09:00',
      ends_at: checked ? f.ends_at.slice(0, 10) : f.ends_at + 'T10:00' } });
  }
  const sorted = [...rows].sort((a, b) => (a.fields ? Date.parse(a.fields.starts_at) : Infinity) - (b.fields ? Date.parse(b.fields.starts_at) : Infinity));
  return <section className="fc-integration-content" aria-label={t("Channel calendar")}>
    <ChannelSyncNotice channelId={channelId} kind="calendar" />
    <div className="fc-integration-content-heading"><div><h3>{t("Make room for what’s next.")}</h3><p>{t("Plan channel events and exchange them with calendar apps through ICS. Reminders run in the calendar app after you add the event.")}</p></div><button className="fc-icon-button" type="button" aria-label={t("Refresh calendar events")} disabled={!!busy} onClick={() => void load()}><RefreshCw size={16} /></button></div>
    {error && <div className="fc-integration-content-error" role="alert">{error}<button type="button" className="fc-text-button" onClick={() => void load()}>{t("Reload calendar")}</button></div>}
    {notice && <p className="fc-integration-content-notice" role="status">{notice}</p>}
    <div className="fc-integration-calendar-toolbar"><button type="button" className="fc-button" disabled={!!busy} onClick={() => { setEditor(blank()); setError(''); setNotice(''); setPendingImport(null); }}><Plus size={15} />{t("Add calendar event")}</button><button type="button" className="fc-button" disabled={!!busy || loading || unreadable} onClick={() => fileInput.current?.click()}><Upload size={15} />{t("Import ICS")}</button><button type="button" className="fc-button" disabled={!!busy || loading || unreadable || !rows.length} onClick={exportAll}><Download size={15} />{t("Export ICS")}</button><input ref={fileInput} type="file" accept=".ics,text/calendar" aria-label={t("Import ICS file")} hidden tabIndex={-1} onChange={event => void readFile(event.target.files?.[0])} /></div>
    <p className="fc-integration-content-caption">{t("Import copies individual events without sending invitations. Bounded daily, weekly and monthly series can be imported; other recurrence rules must be expanded first. Exporting or opening an external calendar shares its event details with the destination you choose.")}</p>
    <CalendarAvailabilityFinder channelId={channelId} rows={rows} />
    {isAdmin && !rows.some(row => row.content_mode === 'matrix') && <CalendarDeliveryPanel channelId={channelId} rows={rows} />}
    {unreadable && <p className="fc-integration-content-caption">{t("Unlock encrypted events before importing or exporting the complete calendar.")}</p>}
    {progress && <p className="fc-integration-content-loading" role="status"><Loader2 size={16} className="fc-integration-spinner" />{progress}</p>}
    {pendingImport && <section className="fc-integration-calendar-import" aria-label={t("Calendar import preview")}><h4>{t(pendingImport.length === 1 ? 'Review {count} calendar event' : 'Review {count} calendar events', { count: pendingImport.length })}</h4><p className="fc-integration-content-caption">{t("These events will be added to this channel. Existing imported UIDs are skipped.")}</p><ol>{pendingImport.map(item => <li key={item.id}>{item.fields.title} · {dateLabel(item.fields)}</li>)}</ol><button type="button" className="fc-button" disabled={!!busy} onClick={() => setPendingImport(null)}>{t("Cancel import")}</button><button type="button" className="fc-button fc-button-primary" disabled={!!busy} onClick={() => void addImported()}>{t("Add ")}{pendingImport.length} {t(" events to channel")}</button></section>}
    {editor && <form className="fc-integration-content-editor" onSubmit={save} aria-label={editor.previous ? t("Edit calendar event") : t("Add calendar event")}>
      <div className="fc-integration-editor-heading"><h4>{editor.previous ? t("Edit calendar event") : t("Add calendar event")}</h4><button className="fc-icon-button" type="button" aria-label={t("Cancel calendar editing")} disabled={!!busy} onClick={() => setEditor(null)}><X size={16} /></button></div>
      <label htmlFor="integration-calendar-title">{t("Event title")}</label><input autoFocus id="integration-calendar-title" className="fc-input" value={editor.fields.title} maxLength={200} required disabled={!!busy} onChange={event => setEditor({ ...editor, fields: { ...editor.fields, title: event.target.value } })} />
      <label className="fc-integration-calendar-check"><input type="checkbox" checked={editor.fields.all_day} disabled={!!busy} onChange={event => changeAllDay(event.target.checked)} />{t("All-day event")}</label>
      <div className="fc-integration-calendar-dates"><div><label htmlFor="integration-calendar-start">{t("Starts")}</label><input id="integration-calendar-start" className="fc-input" type={editor.fields.all_day ? 'date' : 'datetime-local'} value={editor.fields.starts_at} required disabled={!!busy} onChange={event => setEditor({ ...editor, fields: { ...editor.fields, starts_at: event.target.value } })} /></div><div><label htmlFor="integration-calendar-end">{editor.fields.all_day ? t("Through") : t("Ends")}</label><input id="integration-calendar-end" className="fc-input" type={editor.fields.all_day ? 'date' : 'datetime-local'} value={editor.fields.ends_at} required disabled={!!busy} onChange={event => setEditor({ ...editor, fields: { ...editor.fields, ends_at: event.target.value } })} /></div></div>
      {editor.fields.all_day ? <p className="fc-integration-content-caption">{t("All-day events include both selected dates.")}</p> : <><label htmlFor="integration-calendar-timezone">{t("Time zone")}</label><input id="integration-calendar-timezone" className="fc-input" list="integration-calendar-zones" value={editor.fields.timezone} maxLength={100} required disabled={!!busy} onChange={event => setEditor({ ...editor, fields: { ...editor.fields, timezone: event.target.value } })} /><datalist id="integration-calendar-zones">{['UTC', 'Europe/Stockholm', 'Europe/Copenhagen', 'Europe/Oslo', 'Europe/Helsinki', 'Europe/Berlin', 'Europe/London', 'America/New_York'].map(zone => <option key={zone} value={zone} />)}</datalist></>}
      <label htmlFor="integration-calendar-repeat">{t("Repeat")}</label><select id="integration-calendar-repeat" className="fc-input" value={editor.fields.recurrence?.frequency || ''} disabled={!!busy} onChange={event => setEditor({ ...editor, fields: { ...editor.fields, recurrence: event.target.value ? { frequency: event.target.value as 'daily' | 'weekly' | 'monthly', interval: editor.fields.recurrence?.interval || 1, count: editor.fields.recurrence?.count || 10 } : null } })}><option value="">{t("Does not repeat")}</option><option value="daily">{t("daily")}</option><option value="weekly">{t("weekly")}</option><option value="monthly">{t("monthly")}</option></select>
      {editor.fields.recurrence && <div className="fc-integration-calendar-dates"><label>{t("Every interval")}<input type="number" className="fc-input" min={1} max={30} required value={editor.fields.recurrence.interval} onChange={event => setEditor({ ...editor, fields: { ...editor.fields, recurrence: { ...editor.fields.recurrence!, interval: Number(event.target.value) } } })} /></label><label>{t("Number of occurrences")}<input type="number" className="fc-input" min={1} max={366} required value={editor.fields.recurrence.count} onChange={event => setEditor({ ...editor, fields: { ...editor.fields, recurrence: { ...editor.fields.recurrence!, count: Number(event.target.value) } } })} /></label></div>}
      {!!editor.fields.exceptions?.length && <div><p className="fc-integration-content-caption">{t("This series has occurrence exceptions. Changing its dates or repeat rule requires reviewing those exceptions.")}</p><button type="button" className="fc-text-button" disabled={!!busy} onClick={() => setEditor({ ...editor, fields: { ...editor.fields, exceptions: [] } })}>{t("Reset all occurrence changes")}</button><p className="fc-integration-content-caption">{t("Resetting restores moved and cancelled dates when you save this series.")}</p></div>}
      <label htmlFor="integration-calendar-location">{t("Location")}</label><input id="integration-calendar-location" className="fc-input" value={editor.fields.location} maxLength={1000} disabled={!!busy} onChange={event => setEditor({ ...editor, fields: { ...editor.fields, location: event.target.value } })} />
      <label htmlFor="integration-calendar-description">{t("Event details")}</label><textarea id="integration-calendar-description" className="fc-input" rows={3} value={editor.fields.description} maxLength={10000} disabled={!!busy} onChange={event => setEditor({ ...editor, fields: { ...editor.fields, description: event.target.value } })} />
      <label htmlFor="integration-calendar-reminder">{t("Reminder in your calendar app")}</label><select id="integration-calendar-reminder" className="fc-input" value={editor.fields.reminder_minutes === null ? '' : String(editor.fields.reminder_minutes)} disabled={!!busy} onChange={event => setEditor({ ...editor, fields: { ...editor.fields, reminder_minutes: event.target.value === '' ? null : Number(event.target.value) } })}><option value="">{t("No reminder")}</option>{[0, 5, 15, 30, 60, 1440, 10080, ...(editor.fields.reminder_minutes !== null ? [editor.fields.reminder_minutes] : [])].filter((value, index, list) => list.indexOf(value) === index).sort((a, b) => a - b).map(value => <option key={value} value={value}>{value === 0 ? t("At start time") : value % 1440 === 0 ? t(value === 1440 ? '{count} day before' : '{count} days before', { count: value / 1440 }) : t("{value0} minutes before", { value0: String(value) })}</option>)}</select>
      <button className="fc-button fc-button-primary" type="submit" disabled={!!busy}>{busy === 'save' && <Loader2 size={15} className="fc-integration-spinner" />}{t("Save calendar event")}</button>
    </form>}
    {loading ? <p className="fc-integration-content-loading" role="status"><Loader2 size={16} className="fc-integration-spinner" />{t("Loading calendar events…")}</p> : !rows.length ? <div className="fc-integration-content-empty"><CalendarDays size={26} /><p>{t("Bring team plans and the conversation together.")}</p></div> : <ul className="fc-integration-content-list">{sorted.map(row => <li key={row.id} className="fc-integration-content-card">
      {row.fields ? <><div className="fc-integration-content-meta"><span>{row.fields.all_day ? t("All-day event") : t("Calendar event")}</span><span>{t("Version ")}{row.revision}</span></div><h4>{row.fields.title}</h4><p className="fc-integration-content-caption">{dateLabel(row.fields)}</p>{row.fields.location && <p className="fc-integration-content-caption">{row.fields.location}</p>}{row.fields.description && <p className="fc-integration-content-detail">{row.fields.description}</p>}<div className="fc-integration-content-actions"><button type="button" className="fc-text-button" onClick={() => download([{ id: row.id, fields: row.fields! }], 'freecollab-event.ics')}><Download size={14} />{t("Download ICS")}</button><button type="button" className="fc-text-button" onClick={() => window.open(calendarComposeUrl('google', row.fields!), '_blank', 'noopener,noreferrer')}>{t("Google Calendar")}</button><button type="button" className="fc-text-button" onClick={() => window.open(calendarComposeUrl('outlook', row.fields!), '_blank', 'noopener,noreferrer')}>{t("Outlook Calendar")}</button></div></> : <p className="fc-integration-content-caption">{row.resolution_error || t("Unlock recovery to read this calendar event.")}</p>}
      <div className="fc-integration-content-actions"><button type="button" className="fc-text-button" disabled={!!busy} onClick={() => void showHistory(row)}><History size={14} />{t("History")}</button>{(isAdmin || row.created_by === api.getCurrentUserId()) && <>{row.fields && <button type="button" className="fc-text-button" disabled={!!busy} onClick={() => { setEditor(editing(row)); setError(''); }}><Pencil size={14} />{t("Edit")}</button>}<button type="button" className="fc-text-button" disabled={!!busy} onClick={() => setRemoving(row.id)}><Trash2 size={14} />{t("Remove")}</button></>}</div>
      {row.fields && <CalendarEventPlanning row={row} isAdmin={isAdmin} onChanged={saved => setRows(previous => previous.map(item => item.id === saved.id ? saved : item))} />}
      {removing === row.id && <div className="fc-integration-content-confirm"><p>{t("Remove this event from the channel?")}</p><button className="fc-button" type="button" disabled={!!busy} onClick={() => setRemoving('')}>{t("Keep event")}</button><button className="fc-button" type="button" disabled={!!busy} onClick={() => void remove(row)}>{t("Remove event")}</button></div>}
      {history?.id === row.id && <ol className="fc-integration-content-history" aria-label={t("Calendar event history")}>{history.rows.map(revision => <li key={revision.revision}><span>{t("Version ")}{revision.revision} · {new Date(revision.revised_at).toLocaleString(currentLocale())}</span><strong>{revision.deleted ? t("Event removed") : revision.fields?.title || revision.resolution_error || t("Encrypted revision unavailable")}</strong>{revision.fields && <small>{dateLabel(revision.fields)}</small>}</li>)}</ol>}
    </li>)}</ul>}
  </section>;
}
