/** Local RFC 5545 interoperability. Content is never sent to a calendar provider automatically. */
export interface CalendarFields {
  title: string;
  description: string;
  location: string;
  starts_at: string;
  ends_at: string;
  all_day: boolean;
  timezone: string;
  reminder_minutes: number | null;
  external_uid: string | null;
}
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
  const title = text(v.title, 200, 'event title').trim();
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
  return { title, description, location, starts_at: v.all_day ? starts_at : new Date(starts_at).toISOString(),
    ends_at: v.all_day ? ends_at : new Date(ends_at).toISOString(), all_day: v.all_day, timezone,
    reminder_minutes: reminder_minutes as number | null, external_uid: external_uid as string | null };
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
export function exportCalendar(events: CalendarExport[], now = new Date()): string {
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
interface Parts { year: number; month: number; day: number; hour: number; minute: number; second: number }
function wallParts(instant: number, zone: string): Parts {
  const parts = new Intl.DateTimeFormat('en-GB', { timeZone: zone, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }).formatToParts(instant);
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
      if (properties.some(item => ['RRULE', 'RDATE', 'EXDATE', 'RECURRENCE-ID'].includes(item.name))) throw new Error('Recurring events need to be expanded into individual events before import.');
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
      const fields = validateCalendar({ title: unescapeText(get('SUMMARY')?.value || 'Untitled event'),
        description: unescapeText(get('DESCRIPTION')?.value || ''), location: unescapeText(get('LOCATION')?.value || ''),
        starts_at: start.value, ends_at: start.allDay ? end.value.slice(0, 10) : end.value, all_day: start.allDay,
        timezone: start.zone, reminder_minutes: reminder, external_uid: uid });
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
