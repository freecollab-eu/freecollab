import type { ReferenceCandidate } from './message-references';

export interface SuggestionContext {
  surface: 'conversation' | 'thread' | 'document' | 'task' | 'decision' | 'calendar' | 'file' | 'comment' | 'workspace';
  field?: 'title' | 'body' | 'description' | 'assignee' | 'responsible';
  resourceId?: string;
}
export interface SuggestionScope { actorId?: string | null; token?: string | null; workspaceId?: string; channelId?: string }
interface Activity { choices: Map<string, number> }
const activity = new Map<string, Activity>();
let owner = '', sequence = 0;

/** Session hints contain stable choice IDs only. They never persist writing or labels. */
export function clearSuggestionActivity() { activity.clear(); owner = ''; sequence = 0; }
if (typeof window !== 'undefined') for (const name of ['freecollab:auth-changed', 'freecollab:session-expired', 'freecollab:signed-out']) window.addEventListener(name, clearSuggestionActivity);

function currentActivity(scope?: SuggestionScope, create = false): Activity | undefined {
  if (!scope?.actorId || !scope.token || !scope.workspaceId || !scope.channelId) return undefined;
  const session = JSON.stringify([scope.actorId, scope.token]);
  if (owner !== session) { clearSuggestionActivity(); owner = session; }
  const key = JSON.stringify([scope.workspaceId, scope.channelId]);
  let row = activity.get(key);
  if (!row && create) {
    if (activity.size >= 64) activity.delete(activity.keys().next().value!);
    row = { choices: new Map() }; activity.set(key, row);
  }
  return row;
}
function record(scope: SuggestionScope | undefined, key: string) {
  const row = currentActivity(scope, true); if (!row) return;
  row.choices.delete(key); row.choices.set(key, ++sequence);
  if (row.choices.size > 128) row.choices.delete(row.choices.keys().next().value!);
}
export function recordCommandChoice(scope: SuggestionScope | undefined, id: string) { record(scope, 'command:' + id); }
export function recordReferenceChoice(scope: SuggestionScope | undefined, candidate: Pick<ReferenceCandidate, 'kind' | 'id'>) { record(scope, 'reference:' + candidate.kind + ':' + candidate.id); }

const normalize = (value: string) => value.normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/\s+/g, ' ').trim();
const commandKinds: Record<SuggestionContext['surface'], readonly string[][]> = {
  conversation: [['task', 'document', 'decision', 'meetings', 'files'], ['schedule', 'search', 'groups', 'members', 'pins']],
  thread: [['task', 'decision', 'document', 'files'], ['meetings', 'schedule', 'search', 'groups', 'pins']],
  document: [['document'], ['task', 'decision', 'files', 'cloud', 'search']],
  task: [['task'], ['document', 'files', 'decision', 'schedule', 'my-day']],
  decision: [['decision'], ['task', 'document', 'files', 'members']],
  calendar: [['personal-calendar', 'meetings', 'external-meetings', 'schedule'], ['task', 'my-day', 'members']],
  file: [['files', 'cloud'], ['document', 'task', 'decision', 'search']],
  comment: [['task', 'decision', 'document'], ['files', 'members', 'groups', 'search']],
  workspace: [['channels', 'direct-message', 'search'], ['my-day', 'personal-calendar', 'notifications', 'members']],
};
function commandPriority(id: string, context: SuggestionContext) {
  const groups = commandKinds[context.surface];
  const index = groups.findIndex(group => group.includes(id));
  return index < 0 ? groups.length : index;
}
interface CommandRow { command: { id: string }; index: number; score: number }
/** Matching stays first; contextual choices and scoped recency only break match ties. */
export function orderCommandSuggestions<T extends CommandRow>(rows: T[], context?: SuggestionContext, scope?: SuggestionScope): T[] {
  const recent = currentActivity(scope)?.choices;
  return [...rows].sort((left, right) => left.score - right.score
    || (context ? commandPriority(left.command.id, context) - commandPriority(right.command.id, context) : 0)
    || (recent?.get('command:' + right.command.id) || 0) - (recent?.get('command:' + left.command.id) || 0)
    || left.index - right.index);
}

/** Context IDs describe the existing editor; contents and identities remain untouched. */
export function inferSuggestionContext(...ids: (string | undefined)[]): SuggestionContext {
  const value = ids.filter(Boolean).join(' ').toLowerCase();
  const has = (word: string) => new RegExp('(?:^|[^a-z])' + word + '(?:$|[^a-z])').test(value);
  const surface: SuggestionContext['surface'] = has('comment') ? 'comment' : has('task') || has('project') ? 'task'
    : has('document') || has('shared-document') ? 'document' : has('decision') ? 'decision'
      : has('calendar') || has('event') ? 'calendar' : has('file') ? 'file' : has('thread') ? 'thread' : 'conversation';
  const field = has('assignee') ? 'assignee' : has('responsible') ? 'responsible' : has('title') ? 'title' : has('description') ? 'description' : has('body') ? 'body' : undefined;
  return { surface, ...(field ? { field } : {}) };
}

export function referenceSuggestionContext(context: SuggestionContext, text: string, caret: number): SuggestionContext {
  const line = text.slice(text.lastIndexOf('\n', caret - 1) + 1, caret).trimStart();
  if (/^\/(?:task|todo|uppgift)(?:\s|$)/i.test(line)) return { surface: 'task', field: 'assignee' };
  if (/^\/(?:decision|beslut)(?:\s|$)/i.test(line)) return { surface: 'decision', field: 'responsible' };
  if (/^\/(?:groups|group|grupp|grupper)(?:\s|$)/i.test(line)) return { surface: 'conversation', field: 'assignee' };
  if (/^\/(?:document|doc|dokument)(?:\s|$)/i.test(line)) return { surface: 'document', field: 'body' };
  return context;
}
function referencePriority(candidate: ReferenceCandidate, context: SuggestionContext) {
  if (context.field === 'assignee' || context.field === 'responsible') return candidate.kind === 'person' ? 0 : candidate.kind === 'group' ? 1 : 2;
  const groups = context.surface === 'document' || context.surface === 'file' ? [['document', 'file'], ['task', 'person'], ['group', 'channel']]
    : context.surface === 'task' || context.surface === 'decision' ? [['task', 'document', 'file'], ['person', 'group'], ['channel']]
      : context.surface === 'workspace' ? [['channel', 'person'], ['group'], ['document', 'task', 'file']]
        : [['person', 'group'], ['document', 'task', 'file'], ['channel']];
  return groups.findIndex(group => group.includes(candidate.kind));
}
export function rankReferenceSuggestions(candidates: ReferenceCandidate[], query: string, context: SuggestionContext, scope?: SuggestionScope): ReferenceCandidate[] {
  const words = normalize(query).split(' ').filter(Boolean), recent = currentActivity(scope)?.choices;
  const match = (candidate: ReferenceCandidate) => {
    if (!words.length) return 0;
    const label = normalize(candidate.label), value = words.join(' ');
    return label === value ? 0 : label.startsWith(value) ? 1 : words.every(word => label.includes(word)) ? 2 : 3;
  };
  return candidates.map((candidate, index) => ({ candidate, index, match: match(candidate) }))
    .sort((left, right) => left.match - right.match || referencePriority(left.candidate, context) - referencePriority(right.candidate, context)
      || (recent?.get('reference:' + right.candidate.kind + ':' + right.candidate.id) || 0) - (recent?.get('reference:' + left.candidate.kind + ':' + left.candidate.id) || 0)
      || left.index - right.index).map(row => row.candidate);
}
