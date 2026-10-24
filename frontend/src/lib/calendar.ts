import { validateFieldReferences, type FieldReferences } from './field-references';
/** Local RFC 5545 interoperability. Content is never sent to a calendar provider automatically. */
export interface CalendarFields {
  field_references?: FieldReferences;
  title: string;
  description: string;
  location: string;
  starts_at: string;
  ends_at: string;
  all_day: boolean;
  timezone: string;
  reminder_minutes: number | null;
  external_uid: string | null;
  recurrence?: CalendarRecurrence | null;
  exceptions?: CalendarException[];
}
export interface CalendarRecurrence { frequency: 'daily' | 'weekly' | 'monthly'; interval: number; count: number }
export interface CalendarException { original_start: string; cancelled: boolean; starts_at?: string; ends_at?: string; title?: string; location?: string; description?: string }
export interface CalendarOccurrence extends CalendarFields { original_start: string }
export interface CalendarExport { id: string; fields: CalendarFields }
const encoder = new TextEncoder();
const text = (value: unknown, limit: number, field: string) => {
  if (typeof value !== 'string' || value.length > limit) throw new Error(`Invalid ${field}.`);
  return value;
};
const validDate = (value: string) => /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value + 'T00:00:00Z')) && new Date(value + 'T00:00:00Z').toISOString().slice(0, 10) === value;
function validInstant(value: string): boolean {
  const match = /^(\d{4}-\d{2}-\d{2})T(\d\d):(\d\d)(?::(\d\d)(?:\.\d{1,9})?)?(?:Z|[+-]\d\d:\d\d)$/.exec(value);
  return !!match && validDate(match[1]) && +match[2] < 24 && +match[3] < 60 && +(match[4] || 0) < 60 && Number.isFinite(Date.parse(value));
}
export function validateCalendar(value: unknown): CalendarFields {
  if (!value || typeof value !== 'object') throw new Error('Invalid calendar content.');
  const v = value as Record<string, unknown>;
  const rawTitle = text(v.title, 200, 'event title');
  const field_references = validateFieldReferences({ title: rawTitle, description: v.description as string, location: v.location as string }, v.field_references);
  const title = field_references?.title?.length ? rawTitle : rawTitle.trim();
  const description = text(v.description, 10000, 'event description'), location = text(v.location, 1000, 'event location');
  const starts_at = text(v.starts_at, 64, 'event start'), ends_at = text(v.ends_at, 64, 'event end');
  const timezone = text(v.timezone, 100, 'event time zone');
  if (!title || typeof v.all_day !== 'boolean') throw new Error('An event needs a title and dates.');
  new Intl.DateTimeFormat('en', { timeZone: timezone }).format();
  if (v.all_day) {
    if (!validDate(starts_at) || !validDate(ends_at) || ends_at <= starts_at) throw new Error('All-day events need an end date after the start.');
  } else if (!validInstant(starts_at) || !validInstant(ends_at) || Date.parse(ends_at) <= Date.parse(starts_at)) {
    throw new Error('Timed events need explicit time zones and an end after the start.');
  }
  const reminder_minutes = v.reminder_minutes;
  if (reminder_minutes !== null && (!Number.isInteger(reminder_minutes) || Number(reminder_minutes) < 0 || Number(reminder_minutes) > 10080)) throw new Error('Invalid calendar reminder.');
  const external_uid = v.external_uid;
  if (external_uid !== null && (typeof external_uid !== 'string' || !external_uid || external_uid.length > 255 || /[\x00-\x20\x7f]/.test(external_uid))) throw new Error('Invalid calendar UID.');
  const result: CalendarFields = { ...(field_references ? { field_references } : {}), title, description, location, starts_at: v.all_day ? starts_at : new Date(starts_at).toISOString(),
    ends_at: v.all_day ? ends_at : new Date(ends_at).toISOString(), all_day: v.all_day, timezone,
    reminder_minutes: reminder_minutes as number | null, external_uid: external_uid as string | null };
  if (v.recurrence !== undefined && v.recurrence !== null) {
    const rule = v.recurrence as CalendarRecurrence;
    if (!rule || !['daily', 'weekly', 'monthly'].includes(rule.frequency) || !Number.isInteger(rule.interval) || rule.interval < 1 || rule.interval > 30 || !Number.isInteger(rule.count) || rule.count < 1 || rule.count > 366 || Object.keys(rule).some(key => !['frequency', 'interval', 'count'].includes(key))) throw new Error('Use a daily, weekly or monthly series with at most 366 occurrences.');
    result.recurrence = { frequency: rule.frequency, interval: rule.interval, count: rule.count };
    seriesDates(result);
  }
  if (v.exceptions !== undefined) {
    if (!Array.isArray(v.exceptions) || v.exceptions.length > 100 || v.exceptions.length && !result.recurrence) throw new Error('Use at most 100 exceptions in a recurring series.');
    const identities = new Set(seriesDates(result).map(row => row.original_start)), seen = new Set<string>();
    result.exceptions = v.exceptions.map(raw => {
      if (!raw || typeof raw !== 'object' || Object.keys(raw).some(key => !['original_start', 'cancelled', 'starts_at', 'ends_at', 'title', 'location', 'description'].includes(key))) throw new Error('Invalid occurrence exception.');
      const item = Object.fromEntries(Object.entries(raw).filter(([, value]) => value !== null)) as unknown as CalendarException;
      if (typeof item.original_start !== 'string' || typeof item.cancelled !== 'boolean') throw new Error('Invalid occurrence exception.');
      const original_start = result.all_day ? item.original_start : validInstant(item.original_start) ? new Date(item.original_start).toISOString() : '';
      if (!identities.has(original_start) || seen.has(original_start)) throw new Error('Occurrence exceptions must identify distinct dates in this series.');
      seen.add(original_start);
      if (item.cancelled) {
        if (Object.keys(item).some(key => !['original_start', 'cancelled'].includes(key))) throw new Error('A cancelled occurrence cannot contain replacement details.');
        return { original_start, cancelled: true };
      }
      if ((item.starts_at === undefined) !== (item.ends_at === undefined)) throw new Error('An occurrence override needs both start and end.');
      const override = { ...result, recurrence: undefined, exceptions: undefined, ...item };
      override.field_references = result.field_references ? Object.fromEntries(Object.entries(result.field_references).filter(([key]) => result[key as 'title' | 'description' | 'location'] === override[key as 'title' | 'description' | 'location'])) : undefined;
      const normalized = validateCalendar(override);
      return { original_start, cancelled: false, ...(item.starts_at !== undefined ? { starts_at: normalized.starts_at, ends_at: normalized.ends_at } : {}), ...(item.title !== undefined ? { title: normalized.title } : {}), ...(item.location !== undefined ? { location: normalized.location } : {}), ...(item.description !== undefined ? { description: normalized.description } : {}) };
    });
  }
  return result;
}
const escapeText = (value: string) => value.replace(/\\/g, '\\\\').replace(/\r\n|\r|\n/g, '\\n').replace(/;/g, '\\;').replace(/,/g, '\\,');
function unescapeText(value: string) {
  return value.replace(/\\([nN,;\\])/g, (_, char: string) => /n/i.test(char) ? '\n' : char);
}
function foldLine(value: string) {
  const lines: string[] = []; let current = '', length = 0;
  for (const char of value) {
    const bytes = encoder.encode(char).length;
    if (length + bytes > 75) { lines.push(current); current = ' '; length = 1; }
    current += char; length += bytes;
  }
  lines.push(current); return lines.join('\r\n');
}
const basicInstant = (value: string) => new Date(value).toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z');
const basicDate = (value: string) => value.replace(/-/g, '');
function exportIndividualCalendar(events: CalendarExport[], now = new Date()): string {
  if (events.length > 1000) throw new Error('Export fewer than 1,000 events at a time.');
  const lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//FreeCollab//Calendar 1.0//EN', 'CALSCALE:GREGORIAN'];
  for (const record of events) {
    const f = validateCalendar(record.fields);
    const uid = f.external_uid || record.id + '@freecollab.local';
    if (/[\r\n\x00-\x20\x7f]/.test(uid) || uid.length > 300) throw new Error('Invalid calendar UID.');
    lines.push('BEGIN:VEVENT', 'UID:' + escapeText(uid), 'DTSTAMP:' + basicInstant(now.toISOString()),
      'DTSTART' + (f.all_day ? ';VALUE=DATE:' + basicDate(f.starts_at) : ':' + basicInstant(f.starts_at)),
      'DTEND' + (f.all_day ? ';VALUE=DATE:' + basicDate(f.ends_at) : ':' + basicInstant(f.ends_at)),
      'SUMMARY:' + escapeText(f.title), 'DESCRIPTION:' + escapeText(f.description), 'LOCATION:' + escapeText(f.location));
    if (f.reminder_minutes !== null) lines.push('BEGIN:VALARM', 'ACTION:DISPLAY', 'DESCRIPTION:' + escapeText(f.title),
      'TRIGGER:' + (f.reminder_minutes ? '-' : '') + 'PT' + f.reminder_minutes + 'M', 'END:VALARM');
    lines.push('END:VEVENT');
  }
  lines.push('END:VCALENDAR');
  return lines.map(foldLine).join('\r\n') + '\r\n';
}
/** ICS export expands supported bounded series, keeping DST and exceptions exact. */
export function exportCalendar(events: CalendarExport[], now = new Date()): string {
  const expanded: CalendarExport[] = [];
  for (const event of events) {
    const fields = validateCalendar(event.fields);
    if (!fields.recurrence) expanded.push({ ...event, fields });
    else for (const occurrence of expandCalendarOccurrences(fields, '1000-01-01T00:00:00Z', '9999-12-31T23:59:59Z')) {
      const suffix = occurrence.original_start.replace(/[^0-9A-Za-z]/g, '');
      expanded.push({ id: event.id + '-' + suffix, fields: { ...occurrence, recurrence: undefined, exceptions: undefined, external_uid: fields.external_uid ? fields.external_uid.slice(0, 210) + '-' + suffix : null } });
    }
    if (expanded.length > 1000) throw new Error('Export fewer than 1,000 occurrences at a time.');
  }
  return exportIndividualCalendar(expanded, now);
}
interface Parts { year: number; month: number; day: number; hour: number; minute: number; second: number }
const wallFormatters = new Map<string, Intl.DateTimeFormat>();
function wallParts(instant: number, zone: string): Parts {
  let formatter = wallFormatters.get(zone);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat('en-GB', { timeZone: zone, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
    if (wallFormatters.size >= 32) wallFormatters.delete(wallFormatters.keys().next().value!);
    wallFormatters.set(zone, formatter);
  }
  const parts = formatter.formatToParts(instant);
  return Object.fromEntries(parts.filter(part => part.type !== 'literal').map(part => [part.type, Number(part.value)])) as unknown as Parts;
}
export function calendarWallInput(instant: string, zone: string): string {
  const p = wallParts(Date.parse(instant), zone), pad = (value: number) => String(value).padStart(2, '0');
  return `${String(p.year).padStart(4, '0')}-${pad(p.month)}-${pad(p.day)}T${pad(p.hour)}:${pad(p.minute)}`;
}
const wallEpoch = (p: Parts) => Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second);
/** Resolve an IANA wall time. Ambiguous autumn times use the first occurrence, per RFC 5545. */
export function zonedInstant(value: string, zone: string): string {
  const match = /^(\d{4})-?(\d{2})-?(\d{2})T(\d{2}):?(\d{2})(?::?(\d{2}))?$/.exec(value);
  if (!match) throw new Error('Use a valid calendar date and time.');
  const p: Parts = { year: +match[1], month: +match[2], day: +match[3], hour: +match[4], minute: +match[5], second: +(match[6] || 0) };
  const base = wallEpoch(p);
  if (p.year < 1000 || new Date(base).toISOString().replace(/[-:]/g, '').slice(0, 15) !== `${match[1]}${match[2]}${match[3]}T${match[4]}${match[5]}${match[6] || '00'}`) throw new Error('Invalid calendar date.');
  const candidates = new Set<number>();
  for (const hours of [-36, -18, -6, 0, 6, 18, 36]) {
    const sample = base + hours * 3600000;
    const offset = wallEpoch(wallParts(sample, zone)) - sample;
    const candidate = base - offset;
    if (wallEpoch(wallParts(candidate, zone)) === base) candidates.add(candidate);
  }
  if (!candidates.size) throw new Error('This local time does not exist because the clock changes. Choose another time.');
  return new Date(Math.min(...candidates)).toISOString();
}
type Property = { name: string; parameters: Record<string, string>; value: string };
function property(line: string): Property {
  const colon = line.indexOf(':'); if (colon < 1) throw new Error('Invalid calendar property.');
  const [name, ...parameters] = line.slice(0, colon).split(';');
  return { name: name.toUpperCase(), parameters: Object.fromEntries(parameters.map(value => {
    const equal = value.indexOf('='); if (equal < 1) throw new Error('Invalid calendar parameter.');
    return [value.slice(0, equal).toUpperCase(), value.slice(equal + 1).replace(/^"|"$/g, '')];
  })), value: line.slice(colon + 1) };
}
function calendarDate(p: Property, fallbackZone: string): { value: string; allDay: boolean; zone: string } {
  if (p.parameters.VALUE === 'DATE') {
    const match = /^(\d{4})(\d{2})(\d{2})$/.exec(p.value);
    const value = match ? `${match[1]}-${match[2]}-${match[3]}` : '';
    if (!validDate(value)) throw new Error('Invalid all-day calendar date.');
    return { value, allDay: true, zone: fallbackZone };
  }
  if (p.parameters.VALUE && p.parameters.VALUE !== 'DATE-TIME') throw new Error('Unsupported calendar date format.');
  const utc = p.value.endsWith('Z'), raw = utc ? p.value.slice(0, -1) : p.value;
  const zone = utc ? 'UTC' : p.parameters.TZID || fallbackZone;
  return { value: zonedInstant(raw, zone), allDay: false, zone };
}
function duration(value: string): number {
  const m = /^(-)?P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$/.exec(value);
  if (!m || !m.slice(2).some(Boolean)) throw new Error('Unsupported calendar duration.');
  const seconds = (+m[2] || 0) * 86400 + (+m[3] || 0) * 3600 + (+m[4] || 0) * 60 + (+m[5] || 0);
  if (seconds > 366 * 86400) throw new Error('Calendar duration is too long.');
  return (m[1] ? -1 : 1) * seconds * 1000;
}
export function importCalendar(source: string, fallbackZone = 'UTC'): CalendarFields[] {
  if (encoder.encode(source).length > 1024 * 1024 || /\r(?!\n)/.test(source)) throw new Error('Use an ICS file smaller than 1 MB.');
  const lines = source.replace(/^\uFEFF/, '').split(/\r?\n/), unfolded: string[] = [];
  for (const line of lines) { if (/^[ \t]/.test(line) && unfolded.length) unfolded[unfolded.length - 1] += line.slice(1); else if (line) unfolded.push(line); }
  if (unfolded[0]?.toUpperCase() !== 'BEGIN:VCALENDAR' || unfolded[unfolded.length - 1]?.toUpperCase() !== 'END:VCALENDAR') throw new Error('This is not a complete ICS calendar.');
  const events: CalendarFields[] = [], stack: string[] = []; let properties: Property[] = [], alarms: Property[][] = [];
  for (const line of unfolded) {
    const p = property(line);
    if (p.name === 'BEGIN') {
      const component = p.value.toUpperCase();
      if (component === 'VEVENT') { if (stack.join('/') !== 'VCALENDAR') throw new Error('Invalid nested calendar event.'); properties = []; alarms = []; }
      if (component === 'VALARM' && stack.join('/') === 'VCALENDAR/VEVENT') alarms.push([]);
      stack.push(component); continue;
    }
    if (p.name === 'END') {
      if (stack.pop() !== p.value.toUpperCase()) throw new Error('Incomplete calendar component.');
      if (p.value.toUpperCase() !== 'VEVENT') continue;
      if (events.length >= 100) throw new Error('Import at most 100 events per file.');
      if (properties.some(item => ['RDATE', 'RECURRENCE-ID'].includes(item.name))) throw new Error('Expand RDATE and RECURRENCE-ID events before import.');
      const get = (name: string) => {
        const matching = properties.filter(item => item.name === name);
        if (matching.length > 1) throw new Error('Duplicate calendar property: ' + name);
        return matching[0];
      };
      const startProp = get('DTSTART'); if (!startProp) throw new Error('Calendar event has no start.');
      const start = calendarDate(startProp, fallbackZone), endProp = get('DTEND'), length = get('DURATION');
      if (endProp && length) throw new Error('Calendar event has both an end and duration.');
      const end = endProp ? calendarDate(endProp, start.zone) : { allDay: start.allDay, value: new Date(Date.parse(start.value) + (length ? duration(length.value) : start.allDay ? 86400000 : 3600000)).toISOString() };
      if (endProp && start.allDay !== end.allDay) throw new Error('Calendar start and end use different date formats.');
      let reminder: number | null = null;
      for (const alarm of alarms) {
        const action = alarm.find(item => item.name === 'ACTION')?.value;
        const trigger = alarm.find(item => item.name === 'TRIGGER');
        if (action !== 'DISPLAY' || !trigger || trigger.parameters.VALUE === 'DATE-TIME' || trigger.parameters.RELATED === 'END') throw new Error('Only display reminders relative to the event start can be imported.');
        const amount = -duration(trigger.value) / 60000;
        if (!Number.isInteger(amount) || amount < 0 || amount > 10080) throw new Error('Calendar reminder must be within seven days before the event.');
        reminder = Math.max(reminder ?? 0, amount);
      }
      const uid = get('UID') ? unescapeText(get('UID')!.value) : null;
      const recurrenceProp = get('RRULE'); let recurrence: CalendarRecurrence | undefined;
      if (recurrenceProp) {
        const entries = recurrenceProp.value.toUpperCase().split(';').map(value => value.split('='));
        const rule = Object.fromEntries(entries);
        if (entries.some(entry => entry.length !== 2) || new Set(entries.map(entry => entry[0])).size !== entries.length || Object.keys(rule).some(key => !['FREQ', 'INTERVAL', 'COUNT'].includes(key)) || !rule.COUNT) throw new Error('Import bounded daily, weekly or monthly recurrence with COUNT; expand other rules first.');
        recurrence = { frequency: rule.FREQ?.toLowerCase() as CalendarRecurrence['frequency'], interval: Number(rule.INTERVAL || 1), count: Number(rule.COUNT) };
      }
      const exceptions: CalendarException[] = properties.filter(item => item.name === 'EXDATE').flatMap(item => item.value.split(',').map(value => ({ original_start: calendarDate({ ...item, value }, start.zone).value, cancelled: true })));
      const fields = validateCalendar({ title: unescapeText(get('SUMMARY')?.value || 'Untitled event'),
        description: unescapeText(get('DESCRIPTION')?.value || ''), location: unescapeText(get('LOCATION')?.value || ''),
        starts_at: start.value, ends_at: start.allDay ? end.value.slice(0, 10) : end.value, all_day: start.allDay,
        timezone: start.zone, reminder_minutes: reminder, external_uid: uid, ...(recurrence ? { recurrence } : {}), ...(exceptions.length ? { exceptions } : {}) });
      events.push(fields); continue;
    }
    if (stack.join('/') === 'VCALENDAR/VEVENT') properties.push(p);
    else if (stack.join('/') === 'VCALENDAR/VEVENT/VALARM') alarms[alarms.length - 1].push(p);
  }
  if (stack.length || !events.length) throw new Error('This calendar contains no complete events.');
  return events;
}
export function calendarComposeUrl(provider: 'google' | 'outlook', fields: CalendarFields): string {
  const f = validateCalendar(fields);
  const parameters = new URLSearchParams();
  if (provider === 'google') {
    parameters.set('action', 'TEMPLATE'); parameters.set('text', f.title); parameters.set('details', f.description);
    parameters.set('location', f.location); parameters.set('ctz', f.timezone);
    parameters.set('dates', (f.all_day ? basicDate(f.starts_at) : basicInstant(f.starts_at)) + '/' + (f.all_day ? basicDate(f.ends_at) : basicInstant(f.ends_at)));
    return 'https://calendar.google.com/calendar/render?' + parameters;
  }
  parameters.set('path', '/calendar/action/compose'); parameters.set('rru', 'addevent'); parameters.set('subject', f.title);
  parameters.set('body', f.description); parameters.set('location', f.location); parameters.set('startdt', f.starts_at);
  parameters.set('enddt', f.ends_at); parameters.set('allday', String(f.all_day));
  return 'https://outlook.office.com/calendar/0/deeplink/compose?' + parameters;
}

function seriesDates(fields: CalendarFields): CalendarOccurrence[] {
  const rule = fields.recurrence, count = rule?.count || 1, interval = rule?.interval || 1;
  const startWall = fields.all_day ? fields.starts_at + 'T00:00' : calendarWallInput(fields.starts_at, fields.timezone);
  const wall = Date.parse(startWall + ':00Z'), base = new Date(wall);
  const duration = Date.parse(fields.ends_at) - Date.parse(fields.starts_at);
  const rows: CalendarOccurrence[] = [];
  for (let index = 0; rows.length < count && index < count * 3 + 3; index++) {
    let candidate: Date;
    if (rule?.frequency === 'monthly') {
      candidate = new Date(Date.UTC(base.getUTCFullYear(), base.getUTCMonth() + index * interval, base.getUTCDate(), base.getUTCHours(), base.getUTCMinutes()));
      if (candidate.getUTCDate() !== base.getUTCDate()) continue;
    } else candidate = new Date(wall + index * interval * (rule?.frequency === 'weekly' ? 7 : 1) * 86400000);
    if (!Number.isFinite(candidate.getTime()) || candidate.getUTCFullYear() > 9999) throw new Error('Calendar recurrence exceeds the supported date range.');
    let starts_at: string;
    if (fields.all_day) starts_at = candidate.toISOString().slice(0, 10);
    else if (!index) starts_at = fields.starts_at;
    else {
      try { starts_at = zonedInstant(candidate.toISOString().slice(0, 16), fields.timezone); }
      catch (error) { if (error instanceof Error && error.message.startsWith('This local time does not exist')) continue; throw error; }
      // Keep seconds and subseconds from the original explicit instant.
      starts_at = new Date(Date.parse(starts_at) + new Date(fields.starts_at).getUTCSeconds() * 1000 + new Date(fields.starts_at).getUTCMilliseconds()).toISOString();
    }
    const ends_at = new Date(Date.parse(starts_at) + duration).toISOString();
    rows.push({ ...fields, recurrence: undefined, exceptions: undefined, starts_at, ends_at: fields.all_day ? ends_at.slice(0, 10) : ends_at, original_start: starts_at });
  }
  return rows;
}
/** Device-local recurrence expansion also works for Matrix-decrypted fields. */
export function expandCalendarOccurrences(value: CalendarFields, starts: string | Date, ends: string | Date, limit = 366): CalendarOccurrence[] {
  const fields = validateCalendar(value), start = Date.parse(String(starts)), end = Date.parse(String(ends));
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start || !Number.isInteger(limit) || limit < 1 || limit > 366) throw new Error('Use an ordered occurrence window and a limit from 1 to 366.');
  const overrides = new Map((fields.exceptions || []).map(item => [item.original_start, item]));
  return seriesDates(fields).flatMap(row => {
    const item = overrides.get(row.original_start);
    if (item?.cancelled) return [];
    const result = item ? { ...row, ...item } : row;
    if (item && row.field_references) {
      const retained = Object.fromEntries(Object.entries(row.field_references).filter(([key]) => row[key as 'title' | 'description' | 'location'] === result[key as 'title' | 'description' | 'location']));
      if (Object.keys(retained).length) result.field_references = retained; else delete result.field_references;
    }
    const a = result.all_day ? Date.parse(zonedInstant(result.starts_at + 'T00:00', result.timezone)) : Date.parse(result.starts_at);
    const b = result.all_day ? Date.parse(zonedInstant(result.ends_at + 'T00:00', result.timezone)) : Date.parse(result.ends_at);
    return a < end && b > start ? [result] : [];
  }).sort((a, b) => a.starts_at.localeCompare(b.starts_at)).slice(0, limit);
}
