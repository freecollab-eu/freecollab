import { useEffect, useRef, useState } from 'react';
import { CalendarClock, Check, Users } from 'lucide-react';
import { api, type ChannelMember } from '../lib/api';
import { currentLocale, t, useI18n } from '../i18n';
import { calendarPlanningApi, type CalendarRoster, type CalendarAvailability } from '../lib/calendar-planning-api';
import { calendarWallInput, expandCalendarOccurrences, zonedInstant, type CalendarOccurrence } from '../lib/calendar';
import { getSecureMessaging } from '../lib/secure-client';
import { documentCalendarApi, type CalendarRecord } from '../lib/document-calendar-api';
import '../styles/calendar-planning.css';
const describe = (error: unknown) => error instanceof Error ? error.message : t('The calendar action could not be completed.');
const label = (value: string, allDay = false, timeZone?: string) => allDay ? value : new Date(value).toLocaleString(currentLocale(), { dateStyle: 'medium', timeStyle: 'short', ...(timeZone ? { timeZone } : {}) });

export function CalendarEventPlanning({ row, isAdmin, onChanged }: { row: CalendarRecord; isAdmin: boolean; onChanged: (row: CalendarRecord) => void }) {
  useI18n();
  const [open, setOpen] = useState(false), [roster, setRoster] = useState<CalendarRoster | null>(null), [members, setMembers] = useState<ChannelMember[]>([]), [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [edit, setEdit] = useState<CalendarOccurrence | null>(null), [start, setStart] = useState(''), [end, setEnd] = useState('');
  const scope = useRef(0), token = useRef(api.getAccessToken());
  const canEdit = isAdmin || row.created_by === api.getCurrentUserId();
  useEffect(() => { scope.current++; token.current = api.getAccessToken(); setRoster(null); setError(''); setOpen(false); setEdit(null); const revoke = () => { scope.current++; token.current = ''; setRoster(null); setMembers([]); setSelected([]); setOpen(false); setEdit(null); }; window.addEventListener('freecollab:auth-changed', revoke); return () => { scope.current++; window.removeEventListener('freecollab:auth-changed', revoke); }; }, [row.id, row.revision, row.channel_id]);
  const current = (version: number) => version === scope.current && token.current === api.getAccessToken();
  async function load() {
    if (token.current !== api.getAccessToken()) return;
    if (open) { setOpen(false); return; }
    const version = scope.current; setBusy(true); setError('');
    try {
      const [people, invitations] = await Promise.all([api.getChannelMembers(row.channel_id), calendarPlanningApi.attendees(row.channel_id, row.id)]);
      if (current(version)) { setMembers(people); setRoster(invitations); setSelected(invitations.attendees.map(person => person.user_id)); setOpen(true); }
    } catch (failure) { if (current(version)) setError(describe(failure)); }
    finally { if (current(version)) setBusy(false); }
  }
  async function act(action: () => Promise<CalendarRoster>) {
    if (token.current !== api.getAccessToken()) return;
    const version = scope.current; setBusy(true); setError('');
    try { const result = await action(); if (current(version)) setRoster(result); }
    catch (failure) { if (current(version)) setError(describe(failure)); }
    finally { if (current(version)) setBusy(false); }
  }
  async function changeOccurrence(occurrence: CalendarOccurrence, cancel: boolean) {
    if (token.current !== api.getAccessToken()) return;
    const version = scope.current; setBusy(true); setError('');
    try {
      const fields = row.fields!;
      const replacement = cancel ? { original_start: occurrence.original_start, cancelled: true } : { original_start: occurrence.original_start, cancelled: false, starts_at: fields.all_day ? start : zonedInstant(start, fields.timezone), ends_at: fields.all_day ? end : zonedInstant(end, fields.timezone) };
      const saved = await documentCalendarApi.saveEvent(row.channel_id, { ...fields, exceptions: [...(fields.exceptions || []).filter(item => item.original_start !== occurrence.original_start), replacement] }, row);
      if (current(version)) { if (saved.resolution_error) throw new Error(saved.resolution_error); setEdit(null); onChanged(saved); }
    } catch (failure) { if (current(version)) setError(describe(failure)); }
    finally { if (current(version)) setBusy(false); }
  }
  let occurrences: CalendarOccurrence[] = [];
  try { if (row.fields?.recurrence) occurrences = expandCalendarOccurrences(row.fields, new Date(Date.now() - 86400000), new Date(Date.now() + 366 * 86400000), 12); } catch { /* The validated content error remains on its parent card. */ }
  const me = roster?.attendees.find(person => person.user_id === api.getCurrentUserId());
  return <div className="fc-calendar-planning">
    {row.fields?.recurrence && <><p className="fc-calendar-series"><CalendarClock size={14} />{t('Recurring series')} · {t(row.fields.recurrence.frequency)} · {row.fields.recurrence.count} {t('occurrences')}</p><ol className="fc-calendar-occurrences">{occurrences.map(occurrence => <li key={occurrence.original_start}><span>{label(occurrence.starts_at, occurrence.all_day, occurrence.timezone)}</span>{canEdit && <div><button type="button" className="fc-text-button" disabled={busy} onClick={() => { setEdit(occurrence); setStart(occurrence.all_day ? occurrence.starts_at : calendarWallInput(occurrence.starts_at, occurrence.timezone)); setEnd(occurrence.all_day ? occurrence.ends_at : calendarWallInput(occurrence.ends_at, occurrence.timezone)); }}>{t('Move this occurrence')}</button><button type="button" className="fc-text-button" disabled={busy} onClick={() => void changeOccurrence(occurrence, true)}>{t('Cancel this occurrence')}</button></div>}</li>)}</ol>{!occurrences.length && <p className="fc-integration-content-caption">{t('No occurrences in the next year.')}</p>}{edit && <form className="fc-calendar-occurrence-editor" onSubmit={event => { event.preventDefault(); void changeOccurrence(edit, false); }}><label>{t('Occurrence starts')}<input className="fc-input" type={edit.all_day ? 'date' : 'datetime-local'} value={start} onChange={event => setStart(event.target.value)} required /></label><label>{t('Occurrence ends (exclusive for all-day)')}<input className="fc-input" type={edit.all_day ? 'date' : 'datetime-local'} value={end} onChange={event => setEnd(event.target.value)} required /></label><button className="fc-button fc-button-primary" disabled={busy}>{t('Save occurrence')}</button><button type="button" className="fc-button" onClick={() => setEdit(null)}>{t('Cancel')}</button></form>}</>}
    <button type="button" className="fc-text-button" disabled={busy} onClick={() => void load()}><Users size={14} />{t('Attendees & responses')}</button>
    {error && <p className="fc-integration-content-error" role="alert">{error}</p>}
    {open && roster && <section className="fc-calendar-attendees" aria-label={t('Event attendees')}><p className="fc-integration-content-caption">{t('Invitations stay inside this channel. Responses apply to the current series version; changing the event asks everyone to respond again.')}</p>{roster.attendees.length ? <ul>{roster.attendees.map(person => <li key={person.user_id}><span>{person.name}</span><span className={'fc-calendar-response fc-calendar-response-' + person.response}>{t(person.response)}</span></li>)}</ul> : <p>{t('No attendees invited yet.')}</p>}
    {me && <div className="fc-integration-content-actions">{(['accepted', 'tentative', 'declined'] as const).map(response => <button key={response} className={'fc-button' + (me.response === response ? ' fc-button-primary' : '')} type="button" disabled={busy} onClick={() => void act(() => calendarPlanningApi.respond(row.channel_id, row.id, row.revision, response))}>{me.response === response && <Check size={14} />}{t(response)}</button>)}</div>}
    {canEdit && <fieldset disabled={busy}><legend>{t('Invite channel members')}</legend><div className="fc-calendar-people">{members.map(person => <label key={person.user_id}><input type="checkbox" checked={selected.includes(person.user_id)} onChange={event => setSelected(previous => event.target.checked ? [...previous, person.user_id] : previous.filter(id => id !== person.user_id))} />{person.name}</label>)}</div><button className="fc-button" type="button" onClick={() => void act(() => calendarPlanningApi.invite(row.channel_id, row.id, row.revision, selected))}>{t('Save invitations')}</button></fieldset>}
    </section>}
  </div>;
}

export function CalendarAvailabilityFinder({ channelId, rows }: { channelId: string; rows: CalendarRecord[] }) {
  useI18n();
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  const [open, setOpen] = useState(false), [members, setMembers] = useState<ChannelMember[]>([]), [selected, setSelected] = useState<string[]>([]), [starts, setStarts] = useState(() => calendarWallInput(new Date(Date.now() + 3600000).toISOString(), zone)), [ends, setEnds] = useState(() => calendarWallInput(new Date(Date.now() + 86400000).toISOString(), zone));
  const [duration, setDuration] = useState(30), [result, setResult] = useState<CalendarAvailability | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const scope = useRef(0), token = useRef(api.getAccessToken());
  useEffect(() => { scope.current++; token.current = api.getAccessToken(); setOpen(false); setResult(null); setSelected([]); setMembers([]); setError(''); const revoke = () => { scope.current++; token.current = ''; setOpen(false); setResult(null); setMembers([]); setSelected([]); }; window.addEventListener('freecollab:auth-changed', revoke); return () => { scope.current++; window.removeEventListener('freecollab:auth-changed', revoke); }; }, [channelId]);
  const current = (version: number) => version === scope.current && token.current === api.getAccessToken();
  async function toggle() {
    if (token.current !== api.getAccessToken()) return;
    if (open) { setOpen(false); return; }
    const version = scope.current; setBusy(true); setError('');
    try { const people = await api.getChannelMembers(channelId); if (current(version)) { setMembers(people); setSelected([api.getCurrentUserId()!]); setOpen(true); } }
    catch (failure) { if (current(version)) setError(describe(failure)); }
    finally { if (current(version)) setBusy(false); }
  }
  async function find() {
    if (token.current !== api.getAccessToken()) return;
    const version = scope.current; setBusy(true); setError(''); setResult(null);
    try {
      const a = zonedInstant(starts, zone), b = zonedInstant(ends, zone);
      const secure = await getSecureMessaging();
      if (!current(version)) return;
      const encrypted = await secure.handles(channelId);
      if (!current(version)) return;
      let slots: CalendarAvailability;
      if (encrypted) {
        if (Date.parse(b) <= Date.parse(a) || Date.parse(b) - Date.parse(a) > 31 * 86400000) throw new Error(t('Choose a window of at most 31 days.'));
        if (rows.length > 100 || rows.some(row => !row.fields)) throw new Error(t('Unlock all events and use a calendar with at most 100 series for device availability.'));
        const people = await api.getChannelMembers(channelId);
        if (!current(version)) return;
        if (selected.some(id => !people.some(person => person.user_id === id))) throw new Error(t('Selected people must still be channel members.'));
        const rosters = await Promise.all(rows.map(row => calendarPlanningApi.attendees(channelId, row.id)));
        if (!current(version)) return;
        if (rosters.some((roster, index) => roster.event_revision !== rows[index].revision)) throw new Error(t('The calendar changed. Reload it before finding a shared time.'));
        const busy: Array<{ starts_at: string; ends_at: string }> = [];
        rows.forEach((row, index) => {
          if (!selected.includes(row.created_by) && !rosters[index].attendees.some(person => selected.includes(person.user_id) && person.response !== 'declined')) return;
          for (const occurrence of expandCalendarOccurrences(row.fields!, a, b)) busy.push({ starts_at: occurrence.all_day ? zonedInstant(occurrence.starts_at + 'T00:00', occurrence.timezone) : occurrence.starts_at, ends_at: occurrence.all_day ? zonedInstant(occurrence.ends_at + 'T00:00', occurrence.timezone) : occurrence.ends_at });
        });
        const available: Array<{ starts_at: string; ends_at: string }> = [];
        let cursor = Math.ceil(Date.parse(a) / 900000) * 900000;
        while (cursor + duration * 60000 <= Date.parse(b) && available.length < 100) {
          if (!busy.some(item => cursor < Date.parse(item.ends_at) && cursor + duration * 60000 > Date.parse(item.starts_at))) available.push({ starts_at: new Date(cursor).toISOString(), ends_at: new Date(cursor + duration * 60000).toISOString() });
          cursor += 900000;
        }
        slots = { scope: 'selected-channel-only', private_calendars_included: false, busy, slots: available };
      } else slots = await calendarPlanningApi.availability(channelId, a, b, selected, duration);
      if (current(version)) setResult(slots);
    }
    catch (failure) { if (current(version)) setError(describe(failure)); }
    finally { if (current(version)) setBusy(false); }
  }
  return <section className="fc-calendar-availability"><button className="fc-button" type="button" disabled={busy} onClick={() => void toggle()}><CalendarClock size={15} />{t('Find a shared time')}</button>{error && <p role="alert" className="fc-integration-content-error">{error}</p>}{open && <form onSubmit={event => { event.preventDefault(); void find(); }}><p>{t('Search the selected members’ events and invitations in this channel. Private calendars and other channels are excluded. Encrypted calendars keep their schedule on your device.')}</p><div className="fc-calendar-people">{members.map(person => <label key={person.user_id}><input type="checkbox" checked={selected.includes(person.user_id)} onChange={event => { setResult(null); setSelected(previous => event.target.checked ? [...previous, person.user_id] : previous.filter(id => id !== person.user_id)); }} />{person.name}</label>)}</div><div className="fc-integration-calendar-dates"><label>{t('From')}<input className="fc-input" type="datetime-local" value={starts} onChange={event => { setStarts(event.target.value); setResult(null); }} required /></label><label>{t('Until')}<input className="fc-input" type="datetime-local" value={ends} onChange={event => { setEnds(event.target.value); setResult(null); }} required /></label></div><label>{t('Meeting duration')}<select className="fc-input" value={duration} onChange={event => { setDuration(Number(event.target.value)); setResult(null); }}>{[15, 30, 45, 60, 90, 120].map(minutes => <option key={minutes} value={minutes}>{minutes} {t('minutes')}</option>)}</select></label><p className="fc-integration-content-caption">{zone}</p><button type="submit" className="fc-button fc-button-primary" disabled={busy || !selected.length || selected.length > 25}>{t('Find available times')}</button>{result && <div aria-live="polite"><p>{t('Available in this channel only. Confirm other commitments with the attendees.')}</p>{result.slots.length ? <ul className="fc-calendar-slots">{result.slots.slice(0, 12).map(slot => <li key={slot.starts_at}>{label(slot.starts_at)} – {new Date(slot.ends_at).toLocaleTimeString(currentLocale(), { timeStyle: 'short' })}</li>)}</ul> : <p>{t('No shared time found in this window.')}</p>}</div>}</form>}</section>;
}
