import type { DemoChannel, DemoState } from './demo-showroom';

/** Public showroom examples only. This module has no provider or network client. */
export const demoAiProviders = [
  { id: 'grok', name: 'Grok · xAI', host: 'api.x.ai', model: 'grok-4.20-0309-non-reasoning' },
  { id: 'openai', name: 'OpenAI', host: 'api.openai.com', model: 'gpt-4.1-mini' },
  { id: 'custom', name: 'Your OpenAI-compatible service', host: 'Your own installation', model: 'your-model' },
] as const;
export type DemoAiProvider = typeof demoAiProviders[number]['id'];
export const demoAiIntents = ['summary', 'next_steps', 'reply', 'task'] as const;
export type DemoAiIntent = typeof demoAiIntents[number];
export type DemoAiTranslate = (source: string, values?: Record<string, string | number>) => string;
export const demoAiSelectionLimit = 8;
const limits = { messages: 100, documents: 30, message: 2000, document: 20000, excerpt: 280, model: 100 };

export interface DemoAiSource {
  key: string;
  kind: 'message' | 'document';
  id: string;
  channel: DemoChannel;
  label: string;
  text: string;
  author?: string;
  /** Exact content identity, including the display language; never an approval token. */
  fingerprint: string;
}
export interface DemoAiOutputItem { text: string; sourceKeys: string[] }
export interface DemoAiTaskProposal { channel: DemoChannel; messageId: string; text: string; sourceKey: string; fingerprint: string }
export interface DemoAiRun {
  mode: 'local-preview';
  channel: DemoChannel;
  provider: DemoAiProvider;
  model: string;
  intent: DemoAiIntent;
  selectedKeys: string[];
  sources: DemoAiSource[];
  title: string;
  description: string;
  output: string;
  items: DemoAiOutputItem[];
  replyDraft: string | null;
  task: DemoAiTaskProposal | null;
}
export interface DemoAiSession {
  provider: DemoAiProvider;
  model: string;
  intent: DemoAiIntent;
  selectedKeys: string[];
  run: DemoAiRun | null;
}

const translate: DemoAiTranslate = (source, values = {}) => source.replace(/\{([A-Za-z][A-Za-z0-9_]*)\}/g, (whole, key: string) => Object.prototype.hasOwnProperty.call(values, key) ? String(values[key]) : whole);
const validChannel = (channel: DemoChannel) => (['studio', 'product', 'design'] as string[]).includes(channel);
const clip = (text: string, length: number) => text.slice(0, length).replace(/[\uD800-\uDBFF]$/, '');
const excerpt = (text: string) => { const value = text.trim(); return clip(value, limits.excerpt) + (value.length > limits.excerpt ? '…' : ''); };
const validSource = (source: DemoAiSource) => validChannel(source.channel) && (source.kind === 'message' || source.kind === 'document') && typeof source.id === 'string' && source.id.length > 0 && source.id.length <= 1024 && source.key === source.kind + ':' + source.id && typeof source.label === 'string' && source.label.length > 0 && source.label.length <= 300 && typeof source.text === 'string' && source.text.length <= (source.kind === 'message' ? limits.message : limits.document) && typeof source.fingerprint === 'string' && source.fingerprint.length > 0;
const validSession = (session: DemoAiSession) => demoAiProviders.some(provider => provider.id === session.provider) && (demoAiIntents as readonly string[]).includes(session.intent) && typeof session.model === 'string' && session.model.length <= limits.model && /^[A-Za-z0-9][A-Za-z0-9_.:/-]*$/.test(session.model) && Array.isArray(session.selectedKeys) && session.selectedKeys.length > 0 && session.selectedKeys.length <= demoAiSelectionLimit && new Set(session.selectedKeys).size === session.selectedKeys.length;

/** Only sent messages and documents already visible in the current sample channel. */
export function buildDemoAiSources(state: DemoState, channel: DemoChannel, t: DemoAiTranslate = translate): DemoAiSource[] {
  if (!validChannel(channel)) return [];
  const sources: DemoAiSource[] = [], language = t('Local preview');
  const add = (value: Omit<DemoAiSource, 'fingerprint'>) => {
    const source: DemoAiSource = { ...value, fingerprint: JSON.stringify([value.channel, value.kind, value.id, value.label, value.text, value.author ?? null, language]) };
    if (validSource(source) && !sources.some(row => row.key === source.key)) sources.push(source);
  };
  state.messages[channel].slice(-limits.messages).forEach((message, index) => {
    const author = message.author === 'You' ? t('You') : message.author;
    add({ key: 'message:' + message.id, kind: 'message', id: message.id, channel, author, label: t('{author} · message {number}', { author, number: index + 1 }), text: message.sample ? t(message.text) : message.text });
  });
  const stackIds = new Set(state.stacks[channel].map(stack => stack.id));
  state.documents.filter(document => document.channel === channel && stackIds.has(document.stackId)).slice(0, limits.documents).forEach(document => {
    add({ key: 'document:' + document.id, kind: 'document', id: document.id, channel, label: document.sample ? t(document.title) : document.title, text: document.edited ?? (document.sample ? t(document.source) : document.source) });
  });
  return sources;
}

export function createDemoAiSession(sources: readonly DemoAiSource[]): DemoAiSession {
  const first = sources.find(validSource), channelSources = first ? sources.filter(source => validSource(source) && source.channel === first.channel) : [];
  const message = channelSources.find(source => source.kind === 'message'), document = channelSources.find(source => source.kind === 'document');
  return { provider: 'grok', model: demoAiProviders[0].model, intent: 'summary', selectedKeys: [message, document].filter((source): source is DemoAiSource => !!source).map(source => source.key), run: null };
}

/** An extractive local example; selecting a provider never contacts that provider. */
export function createDemoAiRun(session: DemoAiSession, sources: readonly DemoAiSource[], channel: DemoChannel, t: DemoAiTranslate = translate): DemoAiRun | null {
  if (!validChannel(channel) || !validSession(session)) return null;
  const selected: DemoAiSource[] = [];
  for (const key of session.selectedKeys) {
    const matches = sources.filter(source => source.key === key);
    if (matches.length !== 1 || !validSource(matches[0]) || matches[0].channel !== channel) return null;
    selected.push({ ...matches[0] });
  }
  const quote = (source: DemoAiSource) => source.text.trim() ? '“' + excerpt(source.text) + '”' : t('This selected source is empty.');
  let title = t('Excerpts from your selection'), description = t('Local preview: exact excerpts from the sources you selected.');
  let items: DemoAiOutputItem[] = selected.map(source => ({ text: quote(source), sourceKeys: [source.key] }));
  let replyDraft: string | null = null, task: DemoAiTaskProposal | null = null;
  if (session.intent === 'next_steps') {
    title = t('Questions to take the work forward');
    description = t('Local preview: questions based on selected excerpts, without inferred commitments.');
    items = selected.map(source => ({ text: source.text.trim() ? t('What is one next step for “{excerpt}”?', { excerpt: excerpt(source.text) }) : t('What would you like to add to this empty source?'), sourceKeys: [source.key] }));
  } else if (session.intent === 'reply') {
    title = t('A reply to start from');
    description = t('Local preview: a sample reply using a selected excerpt. Edit it in your own words.');
    const source = selected.find(row => row.text.trim());
    if (source) {
      replyDraft = t('Thanks for sharing. What should we explore next about “{excerpt}”?', { excerpt: excerpt(source.text) });
      items = [{ text: replyDraft, sourceKeys: [source.key] }];
    }
  } else if (session.intent === 'task') {
    const source = selected.find(row => row.kind === 'message');
    const text = source ? clip(source.text.trim(), 180) : '';
    title = t('A task to review');
    if (source && text) {
      description = t('Local preview: a task title copied from the first selected message. Review it before creating anything.');
      task = { channel, messageId: source.id, text, sourceKey: source.key, fingerprint: source.fingerprint };
      items = [{ text: quote(source), sourceKeys: [source.key] }];
    } else {
      title = t('Choose a message to prepare a task');
      description = t('Select a sent message to prepare a task. Documents alone cannot create a message-based task.');
    }
  }
  const output = items.map(item => item.text + ' ' + item.sourceKeys.map(key => '[S' + (selected.findIndex(source => source.key === key) + 1) + ']').join(' ')).join('\n');
  return { mode: 'local-preview', channel, provider: session.provider, model: session.model, intent: session.intent, selectedKeys: [...session.selectedKeys], sources: selected, title, description, output, items, replyDraft, task };
}

/** A preview can approve a task only while its exact selection and controls match. */
export function demoAiRunIsCurrent(run: DemoAiRun | null, session: DemoAiSession, sources: readonly DemoAiSource[], channel: DemoChannel): boolean {
  if (!run || run.mode !== 'local-preview' || !validChannel(channel) || !validSession(session) || run.channel !== channel || run.provider !== session.provider || run.model !== session.model || run.intent !== session.intent || run.selectedKeys.length !== session.selectedKeys.length || run.sources.length !== session.selectedKeys.length || run.selectedKeys.some((key, index) => key !== session.selectedKeys[index])) return false;
  const currentSelection = run.sources.every((snapshot, index) => {
    const matches = sources.filter(source => source.key === session.selectedKeys[index]);
    if (matches.length !== 1 || !validSource(snapshot) || !validSource(matches[0]) || snapshot.channel !== channel || snapshot.key !== session.selectedKeys[index]) return false;
    const current = matches[0];
    return current.channel === channel && current.fingerprint === snapshot.fingerprint && current.kind === snapshot.kind && current.id === snapshot.id && current.label === snapshot.label && current.text === snapshot.text && current.author === snapshot.author;
  });
  if (!currentSelection) return false;
  const message = run.intent === 'task' ? run.sources.find(source => source.kind === 'message') : undefined;
  const text = message ? clip(message.text.trim(), 180) : '';
  if (!message || !text) return run.task === null;
  return run.task !== null && run.task.channel === channel && run.task.messageId === message.id && run.task.sourceKey === message.key && run.task.fingerprint === message.fingerprint && run.task.text === text;
}
