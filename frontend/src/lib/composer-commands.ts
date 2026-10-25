import { t } from '../i18n/core';
import { getSlashCommands, type SlashAction, type SlashCommand, type SlashContext } from './slash-commands';
import { normalizeMessageReferences, validateMessageReferences, type MessageReference } from './message-references';
import type { FieldReferences } from './field-references';

/** A reviewed native draft, never an instruction to create a record. */
export interface ComposerWorkDraft { key: string; title: string; text: string; assigneeUserId?: string; field_references?: FieldReferences }
export interface ComposerDecisionDraft { key: string; title: string; content: string; responsibleUserId?: string; field_references?: FieldReferences }

export type ComposerCommandIntent =
  | { kind: 'document' | 'task'; title: string; text: string }
  | { kind: 'decision'; title: string; content: string }
  | { kind: 'groups'; name: string; memberText: string }
  | { kind: 'search'; query: string }
  | { kind: 'app-command'; key: string; generation: number; argumentText: string }
  | { kind: 'open'; action: SlashAction };

export interface ParsedComposerCommand {
  status: 'menu' | 'ready' | 'invalid' | 'unknown';
  /** Only the command token filters the menu; argument text stays intact. */
  query: string;
  command?: SlashCommand;
  arguments?: string;
  intent?: ComposerCommandIntent;
  usage?: string;
  error?: string;
  ambiguous?: boolean;
}

/** Explicit person IDs only; a display name or group never implies identity. */
export function prepareCommandPeople(value: string, references: MessageReference[]): { text: string; personIds: string[] } {
  const people = normalizeMessageReferences(value, references).filter(reference => reference.kind === 'person');
  const personIds = [...new Set(people.map(reference => reference.id))];
  let text = value;
  for (const reference of [...people].reverse()) {
    if (reference.end <= text.length && !text.slice(reference.end).trim()) text = text.slice(0, reference.start).trimEnd();
  }
  return { text, personIds };
}

interface SourceText { text: string; positions: number[] }
const sliceSource = (source: SourceText, start: number, end = source.text.length): SourceText => ({ text: source.text.slice(start, end), positions: source.positions.slice(start, end) });
function trimSource(source: SourceText, end = false): SourceText {
  const start = source.text.length - source.text.trimStart().length;
  return sliceSource(source, start, end ? source.text.trimEnd().length : source.text.length);
}

/**
 * Transfer chosen identities through the command grammar, using source offsets
 * rather than matching names. A failed transfer must keep the caller's text.
 */
export function prepareComposerDraftReferences(value: string, references: MessageReference[], intent: ComposerCommandIntent | undefined): FieldReferences | undefined {
  if (!intent || !['document', 'task', 'decision'].includes(intent.kind) || !references.length) return undefined;
  const refs = validateMessageReferences(value, references);
  const prepared = intent.kind === 'document' ? value : prepareCommandPeople(value, refs).text;
  const match = /^\/[ \t]*([^\s]*)([\s\S]*)$/.exec(prepared);
  const fail = () => new Error(t('The selected references could not be carried into this draft. Your source text is unchanged.'));
  if (!match) throw fail();
  let start = prepared.length - match[2].length;
  start += /^[ \t]+/.exec(prepared.slice(start))?.[0].length || 0;
  start += /^\r?\n/.exec(prepared.slice(start))?.[0].length || 0;
  let argument: SourceText = { text: '', positions: [] };
  for (let index = start; index < prepared.length; index++) {
    argument.text += prepared[index] === '\r' ? '\n' : prepared[index];
    argument.positions.push(index);
    if (prepared[index] === '\r' && prepared[index + 1] === '\n') index++;
  }
  argument = trimSource(argument);
  let title: SourceText, body: SourceText;
  if (argument.text.startsWith('"')) {
    title = { text: '', positions: [] };
    let end = -1;
    for (let index = 1; index < argument.text.length; index++) {
      const character = argument.text[index];
      if (character === '\\' && ['"', '\\'].includes(argument.text[index + 1])) index++;
      else if (character === '"') { end = index; break; }
      title.text += argument.text[index]; title.positions.push(argument.positions[index]);
    }
    if (end < 0 || argument.text[end + 1] && !/\s/.test(argument.text[end + 1])) throw fail();
    title = trimSource(title, true);
    body = sliceSource(argument, end + 1);
    body = sliceSource(body, /^[ \t]*(?:\n)?/.exec(body.text)![0].length);
  } else {
    const newline = argument.text.indexOf('\n');
    title = trimSource(sliceSource(argument, 0, newline < 0 ? argument.text.length : newline), true);
    body = newline < 0 ? { text: '', positions: [] } : sliceSource(argument, newline + 1);
  }
  if (intent.kind !== 'document' && intent.kind !== 'task' && intent.kind !== 'decision') throw fail();
  const content = intent.kind === 'decision' ? intent.content : intent.text;
  if (title.text !== intent.title || (intent.kind === 'decision' ? body.text || title.text : body.text) !== content) throw fail();
  if (intent.kind === 'decision' && !body.text) body = title;
  const bodyKey = intent.kind === 'task' ? 'description' : intent.kind === 'document' ? 'body' : 'content';
  const result: FieldReferences = {};
  let count = 0;
  for (const ref of refs) {
    if (ref.end > prepared.length) { if (ref.kind === 'person') continue; throw fail(); }
    let transferred = false;
    for (const [key, field] of [['title', title], [bodyKey, body]] as [string, SourceText][]) {
      const offset = field.positions.indexOf(ref.start), length = ref.end - ref.start;
      if (offset < 0 || field.text.slice(offset, offset + length) !== value.slice(ref.start, ref.end)
        || !field.positions.slice(offset, offset + length).every((position, index) => position === ref.start + index)
        || field.positions.length < offset + length) continue;
      const moved = { ...ref, start: offset, end: offset + length };
      try { validateMessageReferences(field.text, [moved]); } catch { throw fail(); }
      (result[key] ||= []).push(moved); transferred = true;
      if (++count > 50) throw fail();
    }
    if (!transferred) throw fail();
  }
  return count ? result : undefined;
}

const aliases: Partial<Record<SlashAction, readonly string[]>> = {
  document: ['doc', 'dokument'], task: ['todo', 'uppgift'], decision: ['beslut'],
  search: ['sök', 'sok'], groups: ['group', 'grupp', 'grupper'],
  meetings: ['möte', 'mote'], files: ['filer'], members: ['medlemmar'],
  appearance: ['utseende'], account: ['konto'], notifications: ['notiser'],
  integrations: ['integrationer'], 'new-channel': ['ny-kanal'],
};
const normalize = (value: string) => value.normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase();

export function composerCommandUsage(command: SlashCommand): string {
  if (command.installedApp) return `/${command.command} ${command.installedApp.arguments.map(argument => `${argument.name}="…"`).join(' ')}`.trimEnd();
  if (['document', 'task', 'decision'].includes(command.id)) return `/${command.command} ${t('"Title" Optional context on the same or next line')}`;
  if (command.id === 'search') return `/${command.command} ${t('Words to find')}`;
  if (command.id === 'groups') return `/${command.command} ${t('"Group name" @people')}`;
  return `/${command.command}`;
}

/** Recognizes intentional slash input without treating URLs or // as commands. */
export function getComposerCommandQuery(value: string): string | null {
  if (!value.startsWith('/') || value.startsWith('//')) return null;
  const match = /^\/[ \t]*([^\s]*)([\s\S]*)$/.exec(value);
  if (!match || !match[1] && /[\r\n\u2028\u2029]/.test(match[2])) return null;
  return match[1];
}

/** Multiline ordinary text stays a message unless its command is recognized. */
export function isComposerCommandDraft(value: string, context: SlashContext): boolean {
  if (getComposerCommandQuery(value) === null) return false;
  const parsed = parseComposerCommand(value, context);
  return !/[\r\n\u2028\u2029]/.test(value) || !!parsed.command || !!parsed.ambiguous;
}

function titleAndContext(argument: string): { title: string; text: string } | { error: string } {
  const value = argument.replace(/\r\n?/g, '\n').trimStart();
  if (value.startsWith('"')) {
    let title = '', end = -1;
    for (let index = 1; index < value.length; index++) {
      const character = value[index];
      if (character === '\\' && ['"', '\\'].includes(value[index + 1])) { title += value[++index]; continue; }
      if (character === '"') { end = index; break; }
      title += character;
    }
    if (end < 0) return { error: t('Close the quoted title before opening this draft.') };
    if (value[end + 1] && !/\s/.test(value[end + 1])) return { error: t('Separate the quoted title and its context with a space or a new line.') };
    return { title: title.trim(), text: value.slice(end + 1).replace(/^[ \t]*(?:\n)?/, '') };
  }
  const newline = value.indexOf('\n');
  return newline < 0 ? { title: value.trim(), text: '' } : { title: value.slice(0, newline).trim(), text: value.slice(newline + 1) };
}

/**
 * Convert text into a reviewed UI intent. No persistence, notification, AI or
 * installed-app invocation occurs here. Selection can complete a menu prefix.
 */
export function parseComposerCommand(value: string, context: SlashContext, selectedAction?: SlashAction): ParsedComposerCommand {
  const query = getComposerCommandQuery(value);
  if (query === null) return { status: 'unknown', query: '', error: t('Type / followed by a command, or keep writing a message.') };
  const installedPrefix = context.installedCommands?.some(command => normalize(`${command.app_id}:${command.name}`).startsWith(normalize(query)));
  if (value.length > 10000 || query.length > 192 || query.length > 80 && !installedPrefix) return { status: 'invalid', query, error: t('This command exceeds the supported writing limit.') };
  const match = /^\/[ \t]*([^\s]*)([\s\S]*)$/.exec(value)!;
  const argument = match[2].replace(/^[ \t]+/, '').replace(/^\r?\n/, '');
  const commands = getSlashCommands(context), normalized = normalize(query);
  if (!query && !selectedAction) return { status: 'menu', query: '' };
  const exact = commands.filter(item =>
    normalize(item.command) === normalized || (aliases[item.id] || []).some(alias => normalize(alias) === normalized));
  let command = selectedAction ? commands.find(item => item.id === selectedAction)
    : exact.find(item => !item.installedApp) || (exact.length === 1 ? exact[0] : undefined);
  if (!selectedAction && !command && exact.length > 1) return { status: 'menu', query, arguments: argument, ambiguous: true };
  if (!command && !selectedAction) {
    const installed = commands.filter(item => item.installedApp && normalize(item.installedApp.name) === normalized);
    if (installed.length === 1) command = installed[0];
    else if (installed.length > 1) return { status: 'menu', query, arguments: argument, ambiguous: true };
  }
  if (!command) {
    const matches = getSlashCommands(context, query);
    return { status: matches.length ? 'menu' : 'unknown', query, arguments: argument,
      ...(!matches.length ? { error: t('Unknown command. Choose a suggestion, or press Escape to keep the literal text.') } : {}) };
  }
  if (query && selectedAction && normalize(command.command) !== normalized && !(aliases[command.id] || []).some(alias => normalize(alias) === normalized)
    && !getSlashCommands(context, query).some(item => item.id === selectedAction)) {
    return { status: 'invalid', query, command, arguments: argument, error: t('Choose a command matching what you typed.') };
  }
  const base = { query, command, arguments: argument, usage: composerCommandUsage(command) };
  if (command.installedApp) return { ...base, status: 'ready', intent: { kind: 'app-command', key: command.installedApp.key, generation: command.installedApp.generation, argumentText: argument } };
  if (!argument.trim()) return { ...base, status: 'ready', intent: { kind: 'open', action: command.id } };
  if (command.id === 'search') {
    let search = argument.trim();
    if (search.startsWith('"')) {
      const parsed = titleAndContext(search);
      if ('error' in parsed) return { ...base, status: 'invalid', error: parsed.error };
      search = parsed.title + (parsed.text ? '\n' + parsed.text : '');
    }
    search = search.replace(/[\r\n\u2028\u2029]+/g, ' ').trim();
    if (search.length > 256) return { ...base, status: 'invalid', error: t('Keep the search to 256 characters or fewer.') };
    return { ...base, status: 'ready', intent: { kind: 'search', query: search } };
  }
  if (command.id === 'groups') {
    const parsed = titleAndContext(argument);
    if ('error' in parsed) return { ...base, status: 'invalid', error: parsed.error };
    const name = parsed.title.normalize('NFKC').trim();
    if (!name || name.length > 80 || /[\u0000-\u001f\u007f-\u009f\u2028\u2029]/.test(name)) return { ...base, status: 'invalid', error: t('Give this group a name of 1 to 80 characters on one line.') };
    return { ...base, status: 'ready', intent: { kind: 'groups', name, memberText: parsed.text } };
  }
  if (!['document', 'task', 'decision'].includes(command.id)) return { ...base, status: 'invalid', error: t('This command opens its existing controls. Use it without arguments.') };
  const parsed = titleAndContext(argument);
  if ('error' in parsed) return { ...base, status: 'invalid', error: parsed.error };
  if (/[\r\n\u2028\u2029]/.test(parsed.title)) return { ...base, status: 'invalid', error: t('Use a single line for the title; put its context after the quoted title.') };
  if (!parsed.title || parsed.title.length > 200) return { ...base, status: 'invalid', error: t('Give this draft a title between 1 and 200 characters.') };
  const maximum = command.id === 'task' ? 12000 : command.id === 'decision' ? 20000 : 50000;
  if (parsed.text.length > maximum) return { ...base, status: 'invalid', error: t('The draft context exceeds this feature’s text limit.') };
  return { ...base, status: 'ready', intent: command.id === 'decision'
    ? { kind: 'decision', title: parsed.title, content: parsed.text || parsed.title }
    : { kind: command.id as 'document' | 'task', title: parsed.title, text: parsed.text } };
}
