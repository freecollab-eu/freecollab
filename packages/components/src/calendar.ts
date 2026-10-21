import type {CollaborationClient} from './client.js';
import type {CalendarFields, ModuleOptions} from './types.js';
import {button, element, mountView, timestamp} from './view.js';
import {field, submit} from './forms.js';
export function mountCalendar(target: HTMLElement | ShadowRoot, client: CollaborationClient, options: ModuleOptions = {}) {
  return mountView(target, client, 'calendar', options, signal => client.listCalendar(signal), (event, context) => {
    const row = element('li'); row.append(element('h3', event.fields.title), timestamp(event.fields.starts_at), element('p', event.fields.location), element('p', event.fields.description));
    if (event.created_by === client.state.userId) row.append(button(context.labels.remove, () => void context.run(async signal => {await client.removeCalendar(event, signal);}))); return row;
  }, context => {
    const form = element('form'), title = field(form, context.labels.title), start = field(form, context.labels.starts) as HTMLInputElement, end = field(form, context.labels.ends) as HTMLInputElement, location = field(form, context.labels.location, false, 1000); title.required = start.required = end.required = true; start.type = end.type = 'datetime-local';
    submit(form, context.labels.create, () => {void context.run(async signal => {const starts = new Date(start.value), ends = new Date(end.value); if (!Number.isFinite(starts.getTime()) || !Number.isFinite(ends.getTime()) || ends <= starts) throw new Error(options.locale === 'sv' ? 'Ange en sluttid efter starttiden.' : 'Choose an end after the start.'); const fields: CalendarFields = {title: title.value.trim(), description: '', location: location.value, starts_at: starts.toISOString(), ends_at: ends.toISOString(), all_day: false, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC', reminder_minutes: null, external_uid: null}; await client.saveCalendar(fields, undefined, signal); title.value = location.value = '';});}); context.formArea.append(form);
  });
}
