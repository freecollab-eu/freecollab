import { LabeledWritingField } from './LabeledWritingField';
import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { CalendarDays, Download, Pencil, Plus, RefreshCw, Trash2 } from 'lucide-react';
import { api, type Channel } from '../lib/api';
import { currentLocale, t, useI18n } from '../i18n';
import { calendarWallInput, exportCalendar, validateCalendar, zonedInstant, type CalendarFields } from '../lib/calendar';
import { personalCalendarApi, type PersonalCalendarEvent } from '../lib/personal-calendar-api';
import { reconcileFieldReferences } from '../lib/field-references';
import type { MessageReference } from '../lib/message-references';
import { useScopedWritingGuard, type WritingLeaveController } from '../lib/useScopedWritingGuard';
import { ScopedCollaborationInput } from './ScopedCollaborationInput';
import '../styles/content-completion.css';

interface Editor { id: string; previous?: PersonalCalendarEvent; fields: CalendarFields; context?: string }
const zone = () => Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
const blank = (context?: string): Editor => { const timezone = zone(), start = new Date(Date.now() + 3600000); return { id: crypto.randomUUID(), context, fields: { title: '', description: '', location: '', starts_at: calendarWallInput(start.toISOString(), timezone), ends_at: calendarWallInput(new Date(start.getTime() + 3600000).toISOString(), timezone), timezone, all_day: false, external_uid: null, reminder_minutes: null } }; };
const description = (error: unknown) => error instanceof Error ? error.message : t('The private calendar could not be updated.');

/** Private actor-owned events share no schedule or content with channel members. */
export function PersonalCalendar({ workspaceId, channelId, initialEventId, leaveController, onPendingChange }: { workspaceId: string; channelId?: string; initialEventId?: string; leaveController?: WritingLeaveController; onPendingChange?: (pending: boolean) => void }) {
  useI18n(); const id = useId();
  const [rows, setRows] = useState<PersonalCalendarEvent[]>([]), [editor, setEditor] = useState<Editor | null>(null);
  const [busy, setBusy] = useState(false), [loading, setLoading] = useState(true), [error, setError] = useState('');
  const [originalChannel, setOriginalChannel] = useState<Channel | null>(null), [contextError, setContextError] = useState('');
  const contextLease = useRef<(() => void) | null>(null);
  const metadataIdentity = JSON.stringify([workspaceId, channelId, editor?.id, editor?.context, api.getAccessToken()]), metadataIdentityRef = useRef(metadataIdentity); metadataIdentityRef.current = metadataIdentity;
  const contextIdentity = JSON.stringify([workspaceId, channelId, editor?.id, editor?.context, editor?.fields, api.getAccessToken()]), contextIdentityRef = useRef(contextIdentity); contextIdentityRef.current = contextIdentity;
  const epoch = useRef(0), owner = useRef(api.getAccessToken()), mutation = useRef(false), baseline = useRef('');
  const region = useRef<HTMLElement>(null), initialFocus = useRef(true);
  const live = (request: number) => request === epoch.current && owner.current === api.getAccessToken();
  const dirty = !!editor && JSON.stringify(editor.fields) !== baseline.current;
  const mayLeave = useScopedWritingGuard(channelId || 'private-calendar', dirty, busy, { workspaceId, leaveController });
  useEffect(() => { onPendingChange?.(dirty || busy); return () => onPendingChange?.(false); }, [dirty, busy, onPendingChange]);
  useEffect(() => { initialFocus.current = true; const cancel = () => { initialFocus.current = false; }; document.addEventListener('pointerdown', cancel, true); document.addEventListener('keydown', cancel, true); return () => { document.removeEventListener('pointerdown', cancel, true); document.removeEventListener('keydown', cancel, true); }; }, [workspaceId, initialEventId]);
  useEffect(() => { if (!initialEventId || !initialFocus.current || !rows.some(row => row.id === initialEventId)) return; const target = [...(region.current?.querySelectorAll<HTMLElement>('[data-private-event-id]') || [])].find(element => element.dataset.privateEventId === initialEventId); if (target) { initialFocus.current = false; target.focus({ preventScroll: true }); target.scrollIntoView({ block: 'nearest' }); } }, [rows, initialEventId]);
  async function load(request = epoch.current) { setLoading(true); try { const result = await personalCalendarApi.list(workspaceId); if (live(request)) setRows(result); } catch (failure) { if (live(request)) setError(description(failure)); } finally { if (live(request)) setLoading(false); } }
  useEffect(() => {
    ++epoch.current; owner.current = api.getAccessToken(); setRows([]); setEditor(null); setError(''); setBusy(false); mutation.current = false; void load();
    const revoke = () => { ++epoch.current; owner.current = null; setRows([]); setEditor(null); setBusy(false); };
    window.addEventListener('freecollab:auth-changed', revoke); return () => { ++epoch.current; window.removeEventListener('freecollab:auth-changed', revoke); };
  }, [workspaceId]);
  useEffect(() => {
    setOriginalChannel(null); setContextError(''); const request = epoch.current, target = editor?.context;
    if (!target || target === channelId) return;
    const identity = metadataIdentity;
    void api.getChannel(target).then(channel => { if (!live(request) || metadataIdentityRef.current !== identity) return; if (channel.workspace_id !== workspaceId) throw Error('The original reference channel is outside this workspace.'); setOriginalChannel(channel); }).catch(() => { if (live(request) && metadataIdentityRef.current === identity) setContextError('The original channel is no longer available. Existing references remain attached to this private event.'); });
    return () => contextLease.current?.();
  }, [workspaceId, channelId, editor?.id, editor?.context]);
  async function continueInContext(element: HTMLButtonElement) {
    if (busy || mutation.current || !editor?.context || !originalChannel || editor.context === channelId || originalChannel.id !== editor.context) return;
    contextLease.current?.(); const request = epoch.current, identity = contextIdentity, target = editor.context, eventId = editor.id; let active = true;
    const cancel = () => { active = false; window.removeEventListener('pointerdown', cancel, true); window.removeEventListener('keydown', cancel, true); window.removeEventListener('beforeinput', cancel, true); window.removeEventListener('input', cancel, true); window.removeEventListener('compositionstart', cancel, true); }; contextLease.current = cancel; window.addEventListener('pointerdown', cancel, true); window.addEventListener('keydown', cancel, true); window.addEventListener('beforeinput', cancel, true); window.addEventListener('input', cancel, true); window.addEventListener('compositionstart', cancel, true);
    const isCurrent = () => active && live(request) && contextIdentityRef.current === identity && element.isConnected && !mutation.current;
    try { const channel = await api.getChannel(target); if (!isCurrent()) return; if (channel.id !== target || channel.workspace_id !== workspaceId) throw Error('The original reference context changed.'); window.dispatchEvent(new CustomEvent('freecollab:open-private-calendar-context', {detail:{workspace_id:workspaceId,channel_id:target,event_id:eventId,sourceElement:element,isCurrent}})); }
    catch (failure) { if (isCurrent()) setContextError(description(failure)); cancel(); }
  }
  function edit(row?: PersonalCalendarEvent) { if (!mayLeave()) return; const next = row ? { id: row.id, previous: row, context: row.context_channel_id || undefined, fields: { ...row.fields, starts_at: row.fields.all_day ? row.fields.starts_at : calendarWallInput(row.fields.starts_at, row.fields.timezone), ends_at: row.fields.all_day ? row.fields.ends_at : calendarWallInput(row.fields.ends_at, row.fields.timezone) } } : blank(channelId); baseline.current = JSON.stringify(next.fields); setEditor(next); setError(''); }
  function writing(field: 'title' | 'description' | 'location', value: string, references: MessageReference[]) { setEditor(previous => { if (!previous) return previous; const fields = { ...previous.fields, [field]: value }; const refs = reconcileFieldReferences({ title: previous.fields.title, description: previous.fields.description, location: previous.fields.location }, { title: fields.title, description: fields.description, location: fields.location }, previous.fields.field_references, { [field]: references }); if (refs) fields.field_references = refs; else delete fields.field_references; return { ...previous, fields }; }); }
  async function save(event: FormEvent) {
    event.preventDefault(); if (!editor || mutation.current) return;
    const submitted = editor, request = epoch.current; mutation.current = true; setBusy(true); setError('');
    try { const fields = validateCalendar({ ...submitted.fields, starts_at: submitted.fields.all_day ? submitted.fields.starts_at : zonedInstant(submitted.fields.starts_at, submitted.fields.timezone), ends_at: submitted.fields.all_day ? submitted.fields.ends_at : zonedInstant(submitted.fields.ends_at, submitted.fields.timezone) }); await personalCalendarApi.save(workspaceId, fields, submitted.previous, submitted.context, submitted.id); if (live(request)) { setEditor(null); await load(request); } }
    catch (failure) { if (live(request)) setError(description(failure)); }
    finally { if (live(request)) { mutation.current = false; setBusy(false); } }
  }
  async function remove(row: PersonalCalendarEvent) { if (!mayLeave() || mutation.current || !window.confirm(t('Delete this private event?'))) return; const request = epoch.current; mutation.current = true; setBusy(true); setError(''); try { await personalCalendarApi.remove(workspaceId, row); if (live(request)) { if (editor?.id === row.id) setEditor(null); await load(request); } } catch (failure) { if (live(request)) setError(description(failure)); } finally { if (live(request)) { mutation.current = false; setBusy(false); } } }
  function download() { const url = URL.createObjectURL(new Blob([exportCalendar(rows)], { type: 'text/calendar;charset=utf-8' })), link = document.createElement('a'); link.href = url; link.download = 'freecollab-private-calendar.ics'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
  const inputChannel = editor?.context || channelId || '';
  return <section ref={region} className="fc-personal-calendar" aria-label={t('Private calendar')}>
    <header className="fc-content-toolbar"><h3><CalendarDays size={18} />{t('Private calendar')}</h3><button type="button" className="fc-button" disabled={busy} onClick={() => edit()}><Plus size={15} />{t('New private event')}</button><button type="button" className="fc-icon-button" aria-label={t('Refresh private calendar')} disabled={busy} onClick={() => { if (mayLeave()) void load(); }}><RefreshCw size={15} /></button>{rows.length > 0 && <button type="button" className="fc-button" disabled={busy} onClick={download}><Download size={15} />{t('Export calendar')}</button>}</header>
    <p className="fc-chat-muted">{t('Only you can read these events. They are separate from shared channel calendars.')}</p>
    {error && <p role="alert" className="fc-alert">{t(error)}</p>}{loading && <p role="status">{t('Loading private calendar…')}</p>}
    {editor && <form className="fc-content-editor" aria-label={t('Private event')} onSubmit={event => void save(event)}>
      {editor.context && editor.context !== channelId && <section aria-label={t('Original reference channel')}><p>{t('This event keeps its original channel for / commands and @ references. Continue there to use those tools without rebinding existing references.')}</p>{originalChannel && <button type="button" className="fc-button" disabled={busy} onClick={event => void continueInContext(event.currentTarget)}>{t('Continue in {channel}', {channel:originalChannel.name || t('Original channel')})}</button>}{contextError && <p role="status">{t(contextError)}</p>}</section>}
      {(['title', 'description', 'location'] as const).map(field => <LabeledWritingField key={field} label={t(field === 'title' ? 'Event title' : field === 'description' ? 'Description' : 'Location')}><ScopedCollaborationInput aria-label={t(field === 'title' ? 'Event title' : field === 'description' ? 'Description' : 'Location')} as={field === 'description' ? 'textarea' : 'input'} channelId={inputChannel} contextId={`${id}:${workspaceId}:${editor.id}:${field}`} value={editor.fields[field]} references={editor.fields.field_references?.[field]} onValueChange={(value, refs) => writing(field, value, refs)} className="fc-input" disabled={busy} required={field === 'title'} maxLength={field === 'title' ? 200 : field === 'location' ? 1000 : 10000} /></LabeledWritingField>)}
      <label><input type="checkbox" checked={editor.fields.all_day} disabled={busy} onChange={event => setEditor(previous => previous && ({ ...previous, fields: { ...previous.fields, all_day: event.target.checked, starts_at: event.target.checked ? previous.fields.starts_at.slice(0, 10) : previous.fields.starts_at + 'T09:00', ends_at: event.target.checked ? (previous.fields.ends_at.slice(0, 10) > previous.fields.starts_at.slice(0, 10) ? previous.fields.ends_at.slice(0, 10) : new Date(Date.parse(previous.fields.starts_at.slice(0, 10) + 'T00:00:00Z') + 86400000).toISOString().slice(0, 10)) : previous.fields.ends_at + 'T10:00' } }))} />{t('All day')}</label>
      <div className="fc-content-dates">{(['starts_at', 'ends_at'] as const).map(field => <label key={field}>{t(field === 'starts_at' ? 'Starts' : 'Ends (exclusive)')}<input className="fc-input" type={editor.fields.all_day ? 'date' : 'datetime-local'} value={editor.fields[field]} required disabled={busy} onChange={event => setEditor(previous => previous && ({ ...previous, fields: { ...previous.fields, [field]: event.target.value } }))} /></label>)}</div>
      <label>{t('Time zone')}<input className="fc-input" value={editor.fields.timezone} disabled={busy} required onChange={event => setEditor(previous => previous && ({ ...previous, fields: { ...previous.fields, timezone: event.target.value } }))} /></label>
      <label>{t('Repeat')}<select className="fc-input" disabled={busy} value={editor.fields.recurrence?.frequency || ''} onChange={event => setEditor(previous => previous && ({ ...previous, fields: { ...previous.fields, recurrence: event.target.value ? { frequency: event.target.value as 'daily' | 'weekly' | 'monthly', count: 12, interval: 1 } : null, exceptions: [] } }))}><option value="">{t('Does not repeat')}</option>{(['daily', 'weekly', 'monthly'] as const).map(value => <option key={value} value={value}>{t(value)}</option>)}</select></label>
      {editor.fields.recurrence && <label>{t('Number of occurrences')}<input className="fc-input" type="number" min={1} max={366} value={editor.fields.recurrence.count} disabled={busy} onChange={event => setEditor(previous => previous && ({ ...previous, fields: { ...previous.fields, recurrence: { ...previous.fields.recurrence!, count: Number(event.target.value) } } }))} /></label>}
      <div className="fc-content-toolbar"><button type="submit" className="fc-button fc-button-primary" disabled={busy || !editor.fields.title.trim()}>{t('Save private event')}</button><button type="button" className="fc-button" disabled={busy} onClick={() => { if (mayLeave()) setEditor(null); }}>{t('Cancel')}</button></div>
    </form>}
    <ul className="fc-content-items">{rows.slice().sort((a, b) => a.fields.starts_at.localeCompare(b.fields.starts_at)).map(row => <li key={row.id} data-private-event-id={row.id} tabIndex={-1}><div><strong>{row.fields.title}</strong><p><time dateTime={row.fields.starts_at}>{row.fields.all_day ? row.fields.starts_at : new Date(row.fields.starts_at).toLocaleString(currentLocale(), { timeZone: row.fields.timezone, dateStyle: 'medium', timeStyle: 'short' })}</time>{row.fields.location ? ' · ' + row.fields.location : ''}{row.fields.recurrence ? ` · ${t(row.fields.recurrence.frequency)} (${row.fields.recurrence.count})` : ''}</p></div><button type="button" className="fc-icon-button" aria-label={t('Edit private event {title}', { title: row.fields.title })} disabled={busy} onClick={() => edit(row)}><Pencil size={16} /></button><button type="button" className="fc-icon-button" aria-label={t('Delete private event {title}', { title: row.fields.title })} disabled={busy} onClick={() => void remove(row)}><Trash2 size={16} /></button></li>)}</ul>
    {!loading && !rows.length && <p className="fc-chat-muted">{t('Your private calendar is empty.')}</p>}
  </section>;
}
