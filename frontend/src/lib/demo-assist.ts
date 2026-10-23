import { buildDemoAiSources, demoAiProviders, type DemoAiProvider, type DemoAiSource, type DemoAiTranslate } from './demo-ai';
import type { DemoChannel, DemoState, DemoTask } from './demo-showroom';

/** Local contextual examples. No provider calls, writes, inferred owners or dates. */
export type DemoAssistScope = { kind: 'message'; id: string } | { kind: 'composer' } | { kind: 'document'; id: string } | { kind: 'stack'; id: string } | { kind: 'task'; id: string } | { kind: 'channel' };
export type DemoAssistIntent = 'reply' | 'task' | 'summary' | 'tidy' | 'shorten' | 'outline' | 'link_context' | 'organize' | 'next_step' | 'details' | 'recap' | 'next_steps';
export interface DemoAssistAction { id: DemoAssistIntent; label: string }
export interface DemoAssistSource { key: string; kind: 'message' | 'document' | 'draft' | 'task'; id: string; channel: DemoChannel; label: string; text: string; author?: string; fingerprint: string }
export type DemoAssistTarget = { kind: 'draft' } | { kind: 'document'; id: string } | { kind: 'task'; id: string } | { kind: 'new_task'; messageId: string };
export interface DemoAssistRecipe { id: string; label: string; target: DemoAssistTarget; before: string; after: string; sourceKeys: string[]; contextFingerprint: string; fingerprint: string }
export interface DemoAssistInput { channel: DemoChannel; scope: DemoAssistScope; draft: string; provider: DemoAiProvider; model: string; locale: string; channelName?: string }
export interface DemoAssistContext extends DemoAssistInput {
  label: string;
  sources: DemoAssistSource[];
  actions: DemoAssistAction[];
  fingerprint: string;
  omittedCount: number;
  target: { target: DemoAssistTarget; text: string } | null;
  templates: { reply: string; outlineHeading: string };
}
export interface DemoAssistPreview {
  mode: 'local-preview'; channel: DemoChannel; scope: DemoAssistScope; provider: DemoAiProvider; model: string; locale: string;
  intent: DemoAssistIntent; contextFingerprint: string; selectedKeys: string[]; sources: DemoAssistSource[];
  title: string; description: string; items: { text: string; sourceKeys: string[] }[]; recipes: DemoAssistRecipe[];
}
export const demoAssistLimits = { sources: 8, characters: 24000, draft: 2000, document: 20000, task: 180, excerpt: 240 } as const;
const translate: DemoAiTranslate = (source, values = {}) => source.replace(/\{([A-Za-z][A-Za-z0-9_]*)\}/g, (whole, key: string) => Object.prototype.hasOwnProperty.call(values, key) ? String(values[key]) : whole);
const clip = (text: string, length: number) => text.slice(0, length).replace(/[\uD800-\uDBFF]$/, '');
const excerpt = (text: string) => { const value = text.trim(); return clip(value, demoAssistLimits.excerpt) + (value.length > demoAssistLimits.excerpt ? '…' : ''); };
const visibleTask = (task: DemoTask, channel: DemoChannel) => !task.source || task.source.channel === channel;
const scopeActions: Record<DemoAssistScope['kind'], [DemoAssistIntent, string][]> = {
  message: [['reply', 'Draft a reply'], ['task', 'Prepare a task'], ['summary', 'Summarize this message']],
  composer: [['tidy', 'Tidy my draft'], ['shorten', 'Shorten my draft']],
  document: [['summary', 'Summarize this document'], ['outline', 'Outline this document'], ['link_context', 'Show its context']],
  stack: [['summary', 'Summarize this stack'], ['organize', 'Suggest a reading order']],
  task: [['next_step', 'Find a next step'], ['details', 'Clarify this task']],
  channel: [['recap', 'Recap this channel'], ['next_steps', 'Questions for next steps']],
};
const taskText = (task: DemoTask, t: DemoAiTranslate) => task.sample ? t(task.text) : task.text;
const sourceCopy = (source: DemoAssistSource): DemoAssistSource => ({ ...source });
const sameSource = (left: DemoAssistSource, right: DemoAssistSource) => left.key === right.key && left.kind === right.kind && left.id === right.id && left.channel === right.channel && left.label === right.label && left.text === right.text && left.author === right.author && left.fingerprint === right.fingerprint;
const recipeFingerprint = (recipe: Omit<DemoAssistRecipe, 'fingerprint'>) => JSON.stringify([recipe.contextFingerprint, recipe.id, recipe.target, recipe.before, recipe.after, recipe.sourceKeys]);

/** Bind a location to its real current sources and its exact write target.
 * A supplied cache must come from buildDemoAiSources for this state/channel/language.
 */
export function buildDemoAssistContext(state: DemoState, input: DemoAssistInput, t: DemoAiTranslate = translate, baseSources?: readonly DemoAiSource[]): DemoAssistContext | null {
  if (!(['studio', 'product', 'design'] as string[]).includes(input.channel) || !demoAiProviders.some(provider => provider.id === input.provider) || typeof input.model !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,99}$/.test(input.model) || typeof input.locale !== 'string' || !/^[A-Za-z][A-Za-z0-9-]{0,19}$/.test(input.locale) || typeof input.draft !== 'string' || input.draft.length > demoAssistLimits.draft || !Object.prototype.hasOwnProperty.call(scopeActions, input.scope.kind) || ('id' in input.scope && (typeof input.scope.id !== 'string' || !input.scope.id || input.scope.id.length > 1024))) return null;
  const { channel, scope } = input, available = baseSources ?? buildDemoAiSources(state, channel, t);
  const candidates: DemoAssistSource[] = [];
  const add = (source: DemoAssistSource | undefined) => { if (source && source.channel === channel && !candidates.some(row => row.key === source.key)) candidates.push(source); };
  const find = (kind: 'message' | 'document', id: string) => available.find(source => source.channel === channel && source.kind === kind && source.id === id);
  const addTask = (task: DemoTask) => add({ key: 'task:' + task.id, kind: 'task', id: task.id, channel, label: taskText(task, t), text: taskText(task, t), fingerprint: JSON.stringify([task, taskText(task, t), input.locale]) });
  let label = '', scopeSnapshot: unknown = null, target: DemoAssistContext['target'] = null;
  if (scope.kind === 'message') {
    const message = state.messages[channel].find(row => row.id === scope.id), source = find('message', scope.id);
    if (!message || !source) return null;
    add(source); label = t('Message from {author}', { author: source.author ?? t('You') }); scopeSnapshot = message;
    target = { target: { kind: 'draft' }, text: input.draft };
  } else if (scope.kind === 'composer') {
    label = t('Your unsent draft');
    add({ key: 'draft:' + channel, kind: 'draft', id: channel, channel, label, text: input.draft, fingerprint: JSON.stringify([channel, input.draft, input.locale, label]) });
    scopeSnapshot = input.draft; target = { target: { kind: 'draft' }, text: input.draft };
  } else if (scope.kind === 'document') {
    const document = state.documents.find(row => row.id === scope.id && row.channel === channel), source = find('document', scope.id);
    if (!document || !source) return null;
    add(source); label = source.label; target = { target: { kind: 'document', id: document.id }, text: source.text };
    const stack = state.stacks[channel].find(row => row.id === document.stackId);
    for (const id of stack?.references ?? []) add(find('message', id));
    const links = state.documentLinks[channel].filter(link => link.documentId === document.id || link.relatedDocumentId === document.id);
    for (const link of links) add(find('document', link.documentId === document.id ? link.relatedDocumentId : link.documentId));
    scopeSnapshot = { document, stackId: stack?.id, references: stack?.references ?? [], links };
  } else if (scope.kind === 'stack') {
    const stack = state.stacks[channel].find(row => row.id === scope.id);
    if (!stack) return null;
    label = stack.sample ? t(stack.title) : stack.title;
    const documents = state.documents.filter(row => row.channel === channel && row.stackId === stack.id);
    for (const document of documents) add(find('document', document.id));
    for (const id of stack.references) add(find('message', id));
    scopeSnapshot = { stack, members: documents.map(document => [document.id, document.title, document.sample]) };
  } else if (scope.kind === 'task') {
    const task = state.tasks.find(row => row.id === scope.id && visibleTask(row, channel));
    if (!task) return null;
    addTask(task); label = taskText(task, t); target = { target: { kind: 'task', id: task.id }, text: label }; scopeSnapshot = task;
    if (task.source?.channel === channel) add(find('message', task.source.messageId));
  } else {
    label = input.channelName ?? channel;
    const messages = state.messages[channel], documents = state.documents.filter(document => document.channel === channel), tasks = state.tasks.filter(task => visibleTask(task, channel));
    for (const message of messages.slice(-4)) add(find('message', message.id));
    for (const document of documents.slice(0, 2)) add(find('document', document.id));
    for (const task of tasks.slice(-2)) addTask(task);
    scopeSnapshot = { messages: messages.map(message => message.id), documents: documents.map(document => document.id), tasks: tasks.map(task => [task.id, task.done, task.owner]), channelName: input.channelName ?? channel };
  }
  const sources: DemoAssistSource[] = []; let size = 0;
  for (const source of candidates) {
    if (sources.length >= demoAssistLimits.sources || size + source.text.length > demoAssistLimits.characters) continue;
    sources.push(sourceCopy(source)); size += source.text.length;
  }
  const actions = scopeActions[scope.kind].map(([id, source]) => ({ id, label: t(source) }));
  const templates = { reply: t('Thanks for sharing. What should we look at next about “{excerpt}”?'), outlineHeading: t('Outline from this document') };
  const fingerprint = JSON.stringify([channel, scope, input.provider, input.model, input.locale, input.draft, label, scopeSnapshot, sources, target, templates]);
  return { ...input, scope: { ...scope }, label, sources, actions, fingerprint, omittedCount: candidates.length - sources.length, target, templates };
}

export function buildDemoAssistSources(state: DemoState, input: DemoAssistInput, t: DemoAiTranslate = translate, baseSources?: readonly DemoAiSource[]): DemoAssistSource[] { return buildDemoAssistContext(state, input, t, baseSources)?.sources ?? []; }

function tidy(text: string): string { return text.replace(/\r\n?/g, '\n').split('\n').map(line => line.replace(/[\t ]+/g, ' ').trim()).join('\n').replace(/\n{3,}/g, '\n\n').trim(); }
function shorten(text: string): string {
  const value = text.replace(/\s+/g, ' ').trim(), sentence = value.match(/^.*?[.!?](?:\s|$)/)?.[0].trim();
  if (sentence && sentence.length < value.length) return sentence;
  if (value.length <= 90) return value;
  const maximum = Math.min(180, Math.floor(value.length * .7)), prefix = clip(value, maximum), lastSpace = prefix.lastIndexOf(' ');
  return (lastSpace >= Math.floor(maximum / 2) ? prefix.slice(0, lastSpace) : prefix).trimEnd() + '…';
}
function outline(text: string): string[] {
  const headings = [...text.matchAll(/^ {0,3}#{1,6}[\t ]+(.+?)\s*#*\s*$/gm)].map(match => match[1].trim());
  const lines = headings.length >= 2 ? headings : text.split('\n').map(line => line.trim()).filter(Boolean);
  return [...new Set(lines)].slice(0, 6).map(line => clip(line, 180));
}
function outlinedText(text: string, heading: string): string | null {
  if (text.split('\n').some(line => line.trim() === '## ' + heading)) return null;
  const lines = outline(text);
  if (lines.length < 2) return null;
  const section = '## ' + heading + '\n\n' + lines.map(line => '- ' + line).join('\n');
  if (text.includes(section)) return null;
  const after = text + (text.trim() ? '\n\n' : '') + section;
  return after.length <= demoAssistLimits.document ? after : null;
}
function replyText(context: DemoAssistContext, source: DemoAssistSource): string {
  const reply = translate(context.templates.reply, { excerpt: clip(source.text.trim(), 160) });
  return context.draft + (context.draft && !/\s$/.test(context.draft) ? ' ' : '') + reply;
}

/** Prepare a reviewable example within the current location; never apply it. */
export function createDemoAssistPreview(context: DemoAssistContext, intent: DemoAssistIntent, t: DemoAiTranslate = translate, selectedKeys: readonly string[] = context.sources.map(source => source.key)): DemoAssistPreview | null {
  if (!context.actions.some(action => action.id === intent) || selectedKeys.length > demoAssistLimits.sources || new Set(selectedKeys).size !== selectedKeys.length || (!selectedKeys.length && context.sources.length)) return null;
  const sources: DemoAssistSource[] = [];
  for (const key of selectedKeys) { const source = context.sources.find(row => row.key === key); if (!source) return null; sources.push(sourceCopy(source)); }
  const items = sources.map(source => ({ text: source.text.trim() ? '“' + excerpt(source.text) + '”' : t('There is no text here yet.'), sourceKeys: [source.key] }));
  const recipes: DemoAssistRecipe[] = [];
  const addRecipe = (label: string, target: DemoAssistTarget, before: string, after: string, sourceKeys: string[]) => {
    const maximum = target.kind === 'document' ? demoAssistLimits.document : target.kind === 'draft' ? demoAssistLimits.draft : demoAssistLimits.task;
    if (!after.trim() || after === before || after.length > maximum || sourceKeys.some(key => !sources.some(source => source.key === key))) return;
    const recipe = { id: intent + ':' + target.kind + ':' + ('id' in target ? target.id : 'messageId' in target ? target.messageId : context.channel), label, target, before, after, sourceKeys, contextFingerprint: context.fingerprint };
    recipes.push({ ...recipe, fingerprint: recipeFingerprint(recipe) });
  };
  let title = context.actions.find(action => action.id === intent)!.label;
  let description = t('Local example from this context. No AI model is called and nothing changes until you choose Apply.');
  const primary = sources.find(source => source.kind === context.scope.kind && 'id' in context.scope && source.id === context.scope.id);
  if (intent === 'tidy' || intent === 'shorten') {
    const source = sources.find(row => row.kind === 'draft');
    if (source) {
      const after = intent === 'tidy' ? tidy(source.text) : shorten(source.text);
      addRecipe(t(intent === 'tidy' ? 'Use tidy draft' : 'Use shorter draft'), { kind: 'draft' }, context.draft, after, [source.key]);
      description = t(intent === 'tidy' ? 'Spacing only. Your words stay yours; compare the draft before applying.' : 'A shorter excerpt of your draft. Check that it still says what you mean.');
      if (!recipes.length) description = t('There is no useful text change to apply yet.');
    }
  } else if (intent === 'reply' && primary?.kind === 'message') {
    const text = clip(primary.text.trim(), 160);
    if (text) {
      addRecipe(t('Add reply to my draft'), { kind: 'draft' }, context.draft, replyText(context, primary), [primary.key]);
    }
    description = t('An example reply quoting this message. It is added to your draft; it is never sent for you.');
  } else if (intent === 'task' && primary?.kind === 'message') {
    addRecipe(t('Review task from this message'), { kind: 'new_task', messageId: primary.id }, '', clip(primary.text.trim(), 180), [primary.key]);
    description = t('The title uses this selected message’s words. Review the title and owner before creating a task.');
  } else if (intent === 'outline' && primary?.kind === 'document' && context.target?.target.kind === 'document') {
    const lines = outline(primary.text);
    if (lines.length >= 2) {
      const after = outlinedText(primary.text, context.templates.outlineHeading);
      if (after) addRecipe(t('Append this outline'), { kind: 'document', id: primary.id }, context.target.text, after, [primary.key]);
      items.splice(0, items.length, ...lines.map(text => ({ text, sourceKeys: [primary.key] })));
    }
    description = t('An outline copied from this document’s headings or lines. The original text stays in place.');
  } else if (intent === 'details' && primary?.kind === 'task' && context.target?.target.kind === 'task') {
    const message = sources.find(source => source.kind === 'message');
    const wording = message ? clip(message.text.trim(), 180) : tidy(primary.text);
    addRecipe(t(message ? 'Use the source wording' : 'Apply clearer spacing'), { kind: 'task', id: primary.id }, context.target.text, wording, [primary.key, ...(message ? [message.key] : [])]);
    description = t('Use the cited source wording or clearer spacing. No owner, date or new commitment is inferred.');
  } else if (intent === 'next_step' || intent === 'next_steps') {
    items.splice(0, items.length, ...sources.map(source => ({ text: source.text.trim() ? t('What is the smallest next step for “{excerpt}”?', { excerpt: excerpt(source.text) }) : t('What would help you begin here?'), sourceKeys: [source.key] })));
    description = t('Questions from the visible context, without assigning work or inventing a plan.');
  } else if (intent === 'organize') {
    const ordered = [...sources].sort((left, right) => left.label < right.label ? -1 : left.label > right.label ? 1 : 0);
    items.splice(0, items.length, ...ordered.map(source => ({ text: source.label, sourceKeys: [source.key] })));
    description = t('A possible alphabetical reading order for these sources. Nothing is moved.');
  } else if (intent === 'link_context') {
    description = t('This document’s saved links and references from its stack. No new relationship is assumed.');
  } else {
    title = t(intent === 'recap' ? 'A recap from this channel' : 'Excerpts from this context');
    description = t('Exact excerpts from these visible sources. A local example, without inferred conclusions.');
  }
  return { mode: 'local-preview', channel: context.channel, scope: { ...context.scope }, provider: context.provider, model: context.model, locale: context.locale, intent, contextFingerprint: context.fingerprint, selectedKeys: [...selectedKeys], sources, title, description, items, recipes };
}

export function demoAssistPreviewIsCurrent(preview: DemoAssistPreview | null, context: DemoAssistContext | null): boolean {
  return !!preview && !!context && preview.mode === 'local-preview' && preview.contextFingerprint === context.fingerprint && preview.channel === context.channel && preview.provider === context.provider && preview.model === context.model && preview.locale === context.locale && JSON.stringify(preview.scope) === JSON.stringify(context.scope) && context.actions.some(action => action.id === preview.intent) && preview.sources.length === preview.selectedKeys.length && new Set(preview.selectedKeys).size === preview.selectedKeys.length && preview.sources.every((source, index) => source.key === preview.selectedKeys[index] && context.sources.some(current => sameSource(source, current)));
}

/** Guard an explicit Apply against stale sources, changed targets and modified recipes. */
export function demoAssistCanApply(preview: DemoAssistPreview, recipe: DemoAssistRecipe, context: DemoAssistContext | null): boolean {
  if (!context || !demoAssistPreviewIsCurrent(preview, context) || recipe.contextFingerprint !== context.fingerprint || recipe.fingerprint !== recipeFingerprint(recipe) || !preview.recipes.some(current => current.id === recipe.id && current.fingerprint === recipe.fingerprint) || !recipe.after.trim() || recipe.after === recipe.before || !recipe.sourceKeys.length || recipe.sourceKeys.some(key => !preview.sources.some(source => source.key === key))) return false;
  if (recipe.target.kind === 'new_task') {
    const messageId = recipe.target.messageId, source = preview.sources.find(row => row.kind === 'message' && row.id === messageId);
    return context.scope.kind === 'message' && context.scope.id === messageId && preview.intent === 'task' && !!source && recipe.before === '' && recipe.after === clip(source.text.trim(), 180);
  }
  const target = context.target;
  if (!target || JSON.stringify(target.target) !== JSON.stringify(recipe.target) || target.text !== recipe.before) return false;
  if (recipe.target.kind === 'draft') {
    const message = preview.sources.find(source => source.kind === 'message' && context.scope.kind === 'message' && source.id === context.scope.id);
    return recipe.after.length <= demoAssistLimits.draft && ((context.scope.kind === 'message' && preview.intent === 'reply' && !!message && recipe.after === replyText(context, message)) || (context.scope.kind === 'composer' && ((preview.intent === 'tidy' && recipe.after === tidy(context.draft)) || (preview.intent === 'shorten' && recipe.after === shorten(context.draft)))));
  }
  if (recipe.target.kind === 'document') return context.scope.kind === 'document' && preview.intent === 'outline' && recipe.after === outlinedText(recipe.before, context.templates.outlineHeading) && recipe.sourceKeys.length === 1 && recipe.sourceKeys[0] === 'document:' + recipe.target.id;
  const message = preview.sources.find(source => source.kind === 'message');
  return context.scope.kind === 'task' && preview.intent === 'details' && recipe.after.length <= demoAssistLimits.task && recipe.after === (message ? clip(message.text.trim(), 180) : tidy(recipe.before));
}
