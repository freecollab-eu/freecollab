import type { ComposerWorkDraft } from './composer-commands';
import type { Message } from './api';
import type { Locale } from '../i18n/core';
import { validateMessageReferences, type MessageReference } from './message-references';
import type { FieldReferences } from './field-references';

export interface TaskDraftMember { user_id: string; name: string }
export interface TaskDraftOptions {
  /** An exact excerpt of this message. Omit to use the entire message. */
  text?: string;
  members?: readonly TaskDraftMember[];
  locale?: Locale;
  now?: Date;
  /** Defaults to the browser's time zone; date-only values never shift at DST. */
  timeZone?: string;
  key?: string;
}

const escape = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const dateString = (year: number, month: number, day: number) => `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
export function isTaskDraftDate(value: unknown): value is string {
  if (typeof value !== 'string' || !/^(?!0000)\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(value + 'T00:00:00Z');
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

/** Conservative date extraction. Conflicting dates remain an editable blank. */
export function suggestTaskDueDate(text: string, { locale = 'en', now = new Date(), timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone }: Pick<TaskDraftOptions, 'locale' | 'now' | 'timeZone'> = {}): string | undefined {
  let today: string;
  try {
    if (!Number.isFinite(now.getTime())) return undefined;
    const parts = new Intl.DateTimeFormat('en-US', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(now);
    today = `${parts.find(part => part.type === 'year')!.value}-${parts.find(part => part.type === 'month')!.value}-${parts.find(part => part.type === 'day')!.value}`;
  } catch { return undefined; }
  const year = Number(today.slice(0, 4)), dates = new Set<string>();
  let invalid = false;
  const add = (value: string) => { if (isTaskDraftDate(value)) dates.add(value); else invalid = true; };
  const calendarDate = (month: number, day: number, explicitYear?: number) => {
    let value = dateString(explicitYear ?? year, month, day);
    if (explicitYear === undefined && isTaskDraftDate(value) && value < today) value = dateString(year + 1, month, day);
    add(value);
  };
  const addDays = (days: number) => {
    const date = new Date(today + 'T12:00:00Z');
    date.setUTCDate(date.getUTCDate() + days);
    add(date.toISOString().slice(0, 10));
  };
  for (const match of text.matchAll(/(?<![\p{L}\p{N}-])(\d{4})-(\d{2})-(\d{2})(?![\p{L}\p{N}-])/gu)) add(match[0]);
  // The application's English date locale uses month/day, Swedish day/month.
  // Do not guess from numeric values when the selected locale says otherwise.
  for (const match of text.matchAll(/(?<![\p{L}\p{N}/])(\d{1,2})\/(\d{1,2})(?:\/(\d{4}))?(?![\p{L}\p{N}/])/gu)) {
    calendarDate(Number(match[locale === 'sv' ? 2 : 1]), Number(match[locale === 'sv' ? 1 : 2]), match[3] ? Number(match[3]) : undefined);
  }
  const months = ['january|januari', 'february|februari', 'march|mars', 'april', 'may|maj', 'june|juni', 'july|juli', 'august|augusti', 'september', 'october|oktober', 'november', 'december'];
  months.forEach((names, index) => {
    const before = new RegExp(`(?<![\\p{L}\\p{N}])(\\d{1,2})(?:st|nd|rd|th)?\\s+(?:${names})(?:\\s+(\\d{4}))?(?![\\p{L}\\p{N}])`, 'giu');
    const after = new RegExp(`(?<![\\p{L}\\p{N}])(?:${names})\\s+(\\d{1,2})(?:st|nd|rd|th)?(?:,?\\s+(\\d{4}))?(?![\\p{L}\\p{N}])`, 'giu');
    for (const pattern of [before, after]) for (const match of text.matchAll(pattern)) calendarDate(index + 1, Number(match[1]), match[2] ? Number(match[2]) : undefined);
  });
  if (/(?<![\p{L}\p{N}])(?:today|idag|i dag)(?![\p{L}\p{N}])/iu.test(text)) addDays(0);
  if (/(?<![\p{L}\p{N}])(?:tomorrow|imorgon|i morgon)(?![\p{L}\p{N}])/iu.test(text)) addDays(1);
  const weekdays = ['sunday|söndag', 'monday|måndag', 'tuesday|tisdag', 'wednesday|onsdag', 'thursday|torsdag', 'friday|fredag', 'saturday|lördag'];
  weekdays.forEach((names, weekday) => {
    const pattern = new RegExp(`(?<![\\p{L}\\p{N}])(?:(next|nästa)\\s+)?(?:${names})(?![\\p{L}\\p{N}])`, 'giu');
    for (const match of text.matchAll(pattern)) {
      let days = (weekday - new Date(today + 'T12:00:00Z').getUTCDay() + 7) % 7;
      if (match[1] && days === 0) days = 7;
      addDays(days);
    }
  });
  return !invalid && dates.size === 1 ? [...dates][0] : undefined;
}

/** Only a single real channel identity can become a suggested assignee. */
export function suggestTaskAssignee(text: string, members: readonly TaskDraftMember[], references: MessageReference[] = []): string | undefined {
  const current = [...new Map(members.filter(member => typeof member.user_id === 'string' && member.user_id && typeof member.name === 'string').map(member => [member.user_id, member])).values()];
  const ids = new Set(current.map(member => member.user_id));
  const explicit = new Set(references.filter(reference => reference.kind === 'person').map(reference => reference.id));
  let literal = text;
  for (const reference of [...references].sort((a, b) => b.start - a.start)) literal = literal.slice(0, reference.start) + ' '.repeat(reference.end - reference.start) + literal.slice(reference.end);
  for (const member of current) {
    if (new RegExp(`(?<![\\p{L}\\p{N}_@])@${escape(member.user_id)}(?![\\p{L}\\p{N}_:@/#-]|\\.[\\p{L}\\p{N}_])`, 'u').test(literal)) explicit.add(member.user_id);
  }
  if (explicit.size) return explicit.size === 1 && ids.has([...explicit][0]) ? [...explicit][0] : undefined;
  const named = current.filter(member => member.name.trim() && new RegExp(`(?<![\\p{L}\\p{N}_@-])@?${escape(member.name.trim())}(?![\\p{L}\\p{N}_@:/#-]|\\.[\\p{L}\\p{N}_])`, 'iu').test(literal));
  const firstName = (name: string) => name.trim().split(/\s+/)[0].toLocaleLowerCase();
  if (named.length) return named.length === 1 && (named[0].name.trim().includes(' ')
    || current.filter(member => firstName(member.name) === firstName(named[0].name)).length === 1) ? named[0].user_id : undefined;
  const action = '(?:skickar|tar|granskar|fixar|gör|uppdaterar|bokar|ordnar|samlar|läser|testar|skapar|bygger|förbereder|levererar|kontaktar|kollar|undersöker|sends|takes|reviews|fixes|updates|books|collects|reads|tests|creates|builds|prepares|delivers|contacts|checks|handles|writes|plans|(?:ska|kan|kommer att|will|can|should)\\s+(?:skicka|ta|granska|fixa|göra|uppdatera|boka|ordna|samla|läsa|testa|skapa|bygga|förbereda|leverera|kontakta|kolla|undersöka|send|take|review|fix|update|book|collect|read|test|create|build|prepare|deliver|contact|check|handle|write|plan))';
  const leading = current.filter(member => {
    const first = member.name.trim().split(/\s+/)[0];
    return /^[\p{L}][\p{L}\p{M}'’-]*$/u.test(first)
      && current.filter(other => firstName(other.name) === firstName(first)).length === 1
      && new RegExp(`^\\s*(?:[-*]\\s+)?${escape(first)}\\s+${action}(?![\\p{L}\\p{N}_])`, 'iu').test(literal);
  });
  return leading.length === 1 ? leading[0].user_id : undefined;
}

/** Local, provider-free suggestions. The caller must open an editable preview. */
export function taskDraftFromMessage(message: Pick<Message, 'id' | 'channel_id' | 'content' | 'references' | 'deleted' | 'locked'>, options: TaskDraftOptions = {}): ComposerWorkDraft | undefined {
  if (!message.id || !message.channel_id || message.deleted || message.locked || typeof message.content !== 'string') return undefined;
  const selected = options.text ?? message.content;
  if (typeof selected !== 'string' || !selected.trim() || selected.length > 12000) return undefined;
  const offset = message.content.indexOf(selected);
  if (offset < 0) return undefined;
  let original: MessageReference[];
  try { original = validateMessageReferences(message.content, message.references ?? []); } catch { return undefined; }
  // Repeated excerpts cannot safely identify which selected reference was meant.
  if (options.text !== undefined && original.length && message.content.indexOf(selected, offset + 1) >= 0) return undefined;
  const end = offset + selected.length;
  if (original.some(reference => reference.start < end && reference.end > offset && (reference.start < offset || reference.end > end))) return undefined;
  const references = original.filter(reference => reference.start >= offset && reference.end <= end).map(reference => ({ ...reference, start: reference.start - offset, end: reference.end - offset }));
  const start = selected.length - selected.trimStart().length;
  let titleEnd = Math.min(selected.length, start + 200);
  const sentence = /[\r\n]|[.!?](?=\s|$)/.exec(selected.slice(start, titleEnd));
  if (sentence) titleEnd = start + sentence.index + (/[.!?]/.test(sentence[0]) ? 1 : 0);
  if (titleEnd < selected.length && !sentence) {
    const space = selected.lastIndexOf(' ', titleEnd);
    if (space > start) titleEnd = space;
  }
  for (const reference of references) if (reference.start < titleEnd && reference.end > titleEnd) titleEnd = reference.start;
  if (/[\uD800-\uDBFF]/.test(selected[titleEnd - 1] || '')) titleEnd--;
  const title = selected.slice(start, titleEnd).trimEnd();
  if (!title) return undefined;
  const titleReferences = references.filter(reference => reference.start >= start && reference.end <= start + title.length).map(reference => ({ ...reference, start: reference.start - start, end: reference.end - start }));
  const field_references: FieldReferences = {};
  if (references.length) field_references.description = references;
  // Preserve the overall 50-reference write limit, including duplicated fields.
  if (references.length + titleReferences.length <= 50 && titleReferences.length) field_references.title = titleReferences;
  const assigneeUserId = suggestTaskAssignee(selected, options.members ?? [], references);
  let dateText = selected;
  for (const reference of [...references].reverse()) dateText = dateText.slice(0, reference.start) + ' '.repeat(reference.end - reference.start) + dateText.slice(reference.end);
  const dueDate = suggestTaskDueDate(dateText, options);
  return { key: options.key || `message-task:${message.channel_id}:${message.id}`, title, text: selected,
    ...(assigneeUserId ? { assigneeUserId } : {}), ...(dueDate ? { dueDate } : {}),
    ...(Object.keys(field_references).length ? { field_references } : {}) };
}
