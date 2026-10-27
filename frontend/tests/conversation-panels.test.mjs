import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { fieldReferences, messageReferences, taskDrafts } from './load-field-references.mjs';

// Exercise the actual native panel event handlers and lifecycle with controlled
// API promises. Browser proofs separately verify real DOM/focus and APIs.
const plain = value => JSON.parse(JSON.stringify(value));
const taskFields = { title: 'Bring samples', description: 'Native task', status: 'todo', priority: 'normal', assignee_user_id: null, due_date: null, checklist: [{ id: 'check', title: 'Collect samples', done: false }], file_ids: [], decision_ids: [] };
const project = { id: 'project', kind: 'project', channel_id: 'room', project_id: null, revision: 1, archived: false, fields: { title: 'Courtyard', description: '', color: 'sage' } };
const task = { id: 'task', kind: 'task', channel_id: 'room', project_id: 'project', revision: 1, archived: false, created_by: 'actor', created_at: '2026-10-23T10:00:00Z', revised_at: '2026-10-23T10:00:00Z', fields: taskFields };
const decision = { id: 'decision', channel_id: 'room', revision: 1, created_by: 'actor', created_at: '2026-10-23T10:00:00Z', title: 'Use samples', content: 'Review before ordering.', status: 'proposed', responsible_user_id: null, decision_date: null, source_message_id: 'message' };
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
function nodes(tree, predicate) {
  if (!tree || typeof tree !== 'object') return [];
  if (Array.isArray(tree)) return tree.flatMap(node => nodes(node, predicate));
  return [...(predicate(tree) ? [tree] : []), ...nodes(tree.props?.children, predicate)];
}
function label(node) {
  if (node == null || typeof node === 'boolean') return '';
  if (typeof node !== 'object') return String(node);
  if (Array.isArray(node)) return node.map(label).join('');
  return label(node.props?.children);
}
function fixture(kind, props = {}) {
  const state = { token: 'session', projects: [project], tasks: [task, { ...task, id: 'other', fields: { ...taskFields, title: 'Another task' } }], decisions: [decision], calls: [], pending: [], saved: [], selected: [], confirmed: [], confirm: false, closes: 0, opened: [], portals: 0, modalActive: null, wait: null, guards: [], views: [], feedPosts: [{ id: 'post', created_by: 'actor', revision: 1, revised_at: '2026-10-24T10:00:00Z', fields: { title: 'A native update', body: 'Discuss the plan', poll: null } }], comments: [] };
  const hooks = []; let cursor = 0, dirty = true, tree, effects = [], component;
  const react = {
    useId() { const index = cursor++; if (!hooks[index]) hooks[index] = { id: "fixture-" + index }; return hooks[index].id; },
    useState(initial) { const index = cursor++; if (!hooks[index]) hooks[index] = { value: typeof initial === 'function' ? initial() : initial }; return [hooks[index].value, value => { const next = typeof value === 'function' ? value(hooks[index].value) : value; if (!Object.is(next, hooks[index].value)) { hooks[index].value = next; dirty = true; } }]; },
    useRef(initial) { const index = cursor++; if (!hooks[index]) hooks[index] = { current: initial }; return hooks[index]; },
    useEffect(callback, dependencies) { const index = cursor++, previous = hooks[index]; if (!previous || !dependencies || dependencies.some((value, i) => !Object.is(value, previous.dependencies?.[i]))) { hooks[index] = { dependencies, cleanup: previous?.cleanup }; effects.push(() => { hooks[index].cleanup?.(); hooks[index].cleanup = callback(); }); } },
  };
  react.useLayoutEffect = react.useEffect;
  const jsx = (type, value, key) => ({ type, props: value || {}, key });
  const api = { getAccessToken: () => state.token, getCurrentUserId: () => 'actor', getChannelMembers: async () => [{ user_id: 'actor', name: 'Nora', role: 'admin' }] };
  async function saveTask(channel, projectId, fields, previous, options) {
    state.calls.push({ kind: 'task', channel, projectId, fields: plain(fields), previous, options });
    if (state.wait) await state.wait.promise;
    return { ...task, id: previous?.id || options?.id || 'new-task', revision: (previous?.revision || 0) + 1, fields: plain(fields) };
  }
  async function saveDecision(previous, fields) {
    state.calls.push({ kind: 'decision', fields: plain(fields), previous });
    if (state.wait) await state.wait.promise;
    return { ...decision, ...plain(fields), revision: (previous?.revision || 0) + 1 };
  }
  const dependencies = {
    react, 'react/jsx-runtime': { jsx, jsxs: jsx, Fragment: 'fragment' },
    'react-dom': { createPortal: value => { state.portals++; return value; } },
    'lucide-react': new Proxy({}, { get: (_, name) => String(name) }),
    '../i18n': { t: value => value, useI18n: () => ({ t: value => value, locale: 'en' }), currentLocale: () => 'en' },
    '../lib/api': { api },
    '../lib/collaboration-context': { useCollaborationContext: () => props.surfaceId ? { surfaceId: props.surfaceId } : null },
    '../lib/field-references': fieldReferences, '../lib/message-references': messageReferences,
    '../lib/task-draft': taskDrafts,
    './CollaborationInput': { CollaborationInput: 'collaboration-input' }, './FieldReferenceText': { FieldReferenceText: 'field-reference-text' }, './ReferencedMessage': { ReferencedMessage: 'referenced-message' },
    '../lib/project-tasks-api': { projectTasksApi: { projects: async () => state.projects, tasks: async () => state.tasks, saveTask, history: async () => [task], saveProject: async (_, fields) => ({ ...project, fields }) } },
    '../lib/collaboration-api': { collaborationApi: { bookmarks: async () => { state.views.push('bookmarks'); return [{ channel_id: 'room', item_id: 'saved', kind: 'message', available: true, created_at: '2026-10-23T10:00:00Z', message: { id: 'message', content: 'Saved native message' } }]; }, pins: async () => { state.views.push('pins'); return []; }, settings: async () => { state.views.push('notifications'); return { level: 'mentions', email_digest: 'none' }; }, decisions: async () => state.decisions, revisions: async () => [decision], createDecision: (_, fields) => saveDecision(undefined, fields), reviseDecision: saveDecision } },
    './EnterpriseControls': { EnterpriseAlerts: 'enterprise-alerts', NativeConfirmation: 'native-confirmation' },
    './ScopedCollaborationInput': { ScopedCollaborationInput: 'collaboration-input' },
    '../lib/account-api': { accountApi: {} },
    '../lib/workflows-api': { workflowsApi: { definitions: async () => ({ enabled: true, can_manage: false, secure_channel: false, definitions: [] }), runs: async () => [{ id: 'run', workflow_name: 'Review', event_kind: 'manual', step_index: 0, step_count: 1, status: 'waiting', created_at: '2026-10-24T10:00:00Z', may_vote: true, votes: [] }], vote: async (_, __, run, choice, note, references) => { state.calls.push({ kind: 'approval', run: run.id, choice, note, references: plain(references) }); } } },
    '../lib/activity-feed-api': { activityFeedApi: { identifier: () => 'new-feed-id', posts: async () => ({ items: state.feedPosts, next_before_id: null, following: [] }), detail: async () => ({ post: state.feedPosts[0], comments: state.comments, votes: [], reactions: [] }), savePost: async (_, fields) => { state.calls.push({ kind: 'feed', fields: plain(fields) }); }, saveComment: async (_, __, fields) => { state.calls.push({ kind: 'comment', fields: plain(fields) }); } } },
    '../lib/files-api': { listChannelFiles: async () => [], downloadChannelFile: async () => new Blob() },
    '../lib/conversation-navigation': { registerConversationNavigationGuard: (owner, canLeave) => { const guard = { owner, canLeave }; state.guards.push(guard); return () => state.guards.splice(state.guards.indexOf(guard), 1); } },
    // These behavior fixtures pass no feed/workflow target. Actual target leases and DOM focus have separate navigation proofs.
    '../lib/useResourceTargetFocus': { useResourceTargetFocus: () => {} },
    '../lib/useModalFocus': { useModalFocus: (_, __, active = true) => { state.modalActive = active; } },
    './ContextualCopilot': { ContextualCopilot: 'copilot' }, './VersionProofs': { VersionProofs: 'proofs' },
    '../lib/chat-productivity': { chatProductivity: {} },
  };
  const writingGuard = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/lib/useScopedWritingGuard.ts', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText, { exports: writingGuard, require: name => name === 'react' ? react : name === '../i18n' ? dependencies['../i18n'] : dependencies['../lib/' + name.slice(2)], window: { confirm: text => { state.confirmed.push(text); return state.confirm; } } });
  dependencies['../lib/useScopedWritingGuard'] = writingGuard;
  const exported = {};
  const source = readFileSync(new URL(`../src/components/${kind}.tsx`, import.meta.url), 'utf8');
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText, {
    exports: exported, require: name => dependencies[name] || {},
    window: { addEventListener() {}, removeEventListener() {}, confirm: text => { state.confirmed.push(text); return state.confirm; }, setTimeout, setInterval: () => 1, clearInterval() {} },
    requestAnimationFrame: callback => { callback(); return 1; }, cancelAnimationFrame() {}, CSS: { escape: value => value },
    crypto: { randomUUID: () => 'new-task' }, Date, Set, Map, AbortController, URL, structuredClone,
    document: { body: {} },
  });
  component = exported[kind];
  const common = { workspaceId: 'workspace', channelId: 'room', channel: { id: 'room', name: 'Courtyard', role: 'admin' }, tab: 'decisions', presentation: 'flow', onClose: () => state.closes++, onPendingChange: value => state.pending.push(value), onSelectedTask: value => state.selected.push(value), onTaskSaved: value => state.saved.push(value), onDecisionSaved: value => state.saved.push(value), onOpenMessage: (...args) => state.opened.push(args), ...props };
  function render() { for (let attempts = 0; dirty && attempts < 50; attempts++) { dirty = false; cursor = 0; effects = []; tree = component(common); for (const effect of effects) effect(); } assert.equal(dirty, false, 'Panel must settle without an update loop'); return tree; }
  async function settle() { for (let i = 0; i < 8; i++) { await new Promise(resolve => setImmediate(resolve)); render(); } return tree; }
  function one(predicate) { const matches = nodes(tree, predicate); assert.equal(matches.length, 1, `Expected one control, found ${matches.length}`); return matches[0]; }
  const button = name => one(node => node.type === 'button' && (node.props['aria-label'] || label(node)) === name);
  const input = id => one(node => node.props.id === id);
  const edit = (id, value) => { const field = input(id); if (field.props.onValueChange) field.props.onValueChange(value, []); else field.props.onChange({ target: { value } }); render(); };
  render();
  return { state, settle, render, one, button, input, edit, get tree() { return tree; }, unmount() { for (const hook of hooks) hook.cleanup?.(); } };
}

test('focused flow task uses the authorized exact card, supports native checklist writes, and keeps full board available', async () => {
  const f = fixture('ProjectTasksPanel', { initialProjectId: 'project', focusTaskId: 'task' }); await f.settle();
  assert.deepEqual(nodes(f.tree, node => node.props['data-task-id']).map(node => node.props['data-task-id']), ['task']);
  assert.equal(nodes(f.tree, node => node.props.className === 'fc-project-board').length, 0);
  assert.equal(nodes(f.tree, node => node.props['aria-label'] === 'Choose project').length, 0);
  assert.equal(f.state.selected.at(-1).id, 'task');
  const check = f.one(node => node.type === 'input' && node.props.type === 'checkbox');
  check.props.onChange({ target: { checked: true } }); await f.settle();
  assert.equal(f.state.calls[0].fields.checklist[0].done, true);
  assert.equal(f.state.calls[0].previous.revision, 1);
  assert.equal(f.state.saved.at(-1).fields.checklist[0].done, true);
  const full = fixture('ProjectTasksPanel', { presentation: 'full' }); await full.settle();
  assert.equal(nodes(full.tree, node => node.props.className === 'fc-project-board').length, 1);
});

test('missing or foreign task/project targets never select a convenient replacement', async () => {
  for (const props of [{ initialProjectId: 'foreign', focusTaskId: 'task' }, { initialProjectId: 'project', focusTaskId: 'missing' }]) {
    const f = fixture('ProjectTasksPanel', props); await f.settle();
    assert.equal(nodes(f.tree, node => node.props['data-task-id']).length, 0);
    assert.equal(f.state.selected.at(-1), null);
    assert.equal(f.state.calls.length, 0);
  }
});

test('task draft guards cancellation/refresh, survives failed save, and reports actual successful revision only', async () => {
  const f = fixture('ProjectTasksPanel', { initialProjectId: 'project', focusTaskId: 'task' }); await f.settle();
  f.button('Edit').props.onClick(); f.render();
  assert.equal(f.state.pending.at(-1), false);
  f.edit('project-item-description', 'An unsaved change');
  assert.equal(f.state.pending.at(-1), true);
  f.button('Cancel task or project editing').props.onClick(); f.render();
  assert.equal(f.input('project-item-description').props.value, 'An unsaved change');
  f.button('Refresh projects and tasks').props.onClick(); f.render();
  assert.equal(f.input('project-item-description').props.value, 'An unsaved change');
  f.state.wait = deferred();
  const submitting = f.one(node => node.type === 'form').props.onSubmit({ preventDefault() {} }); f.render();
  assert.equal(f.state.pending.at(-1), true); assert.equal(f.state.saved.length, 0);
  f.state.wait.reject(new Error('Version conflict')); await submitting; await f.settle();
  assert.equal(f.input('project-item-description').props.value, 'An unsaved change');
  assert.equal(f.state.pending.at(-1), true); assert.equal(f.state.saved.length, 0);
  f.state.wait = null;
  await f.one(node => node.type === 'form').props.onSubmit({ preventDefault() {} }); await f.settle();
  assert.equal(f.state.saved.length, 1); assert.equal(f.state.saved[0].fields.description, 'An unsaved change'); assert.equal(f.state.pending.at(-1), false);
  f.unmount(); assert.equal(f.state.selected.at(-1), null);
});

test('source-prefilled task remains an explicit unsaved native task without unsupported provenance fields', async () => {
  const f = fixture('ProjectTasksPanel', { sourceMessage: { id: 'message', channel_id: 'room', content: 'Use natural materials.' } }); await f.settle();
  assert.equal(f.state.calls.length, 0);
  f.button('New task').props.onClick(); f.render();
  assert.equal(f.input('project-item-description').props.value, 'Use natural materials.'); assert.equal(f.state.pending.at(-1), true);
  f.edit('project-item-title', 'Review materials');
  await f.one(node => node.type === 'form').props.onSubmit({ preventDefault() {} }); await f.settle();
  assert.equal(f.state.calls.length, 1); assert.equal('source_message_id' in f.state.calls[0].fields, false); assert.equal(f.state.saved[0].id, 'new-task');
});

test('message task preview fills title, real assignee and editable date but publishes only after Save', async () => {
  const text = 'Nora reviews the samples by 2026-11-03.';
  const f = fixture('ProjectTasksPanel', { sourceMessage: { id: 'message', channel_id: 'room', content: text } }); await f.settle();
  f.button('New task').props.onClick(); f.render();
  assert.equal(f.state.calls.length, 0);
  assert.equal(f.input('project-item-title').props.value, text);
  assert.equal(f.one(node => node.type === 'select' && node.props['aria-label'] === 'Responsible person').props.value, 'actor');
  const date = f.one(node => node.type === 'input' && node.props.type === 'date');
  assert.equal(date.props.value, '2026-11-03');
  f.edit('project-item-title', 'Review samples together');
  f.one(node => node.type === 'input' && node.props.type === 'date').props.onChange({ target: { value: '2026-11-05' } }); f.render();
  assert.equal(f.state.calls.length, 0);
  await f.one(node => node.type === 'form').props.onSubmit({ preventDefault() {} }); await f.settle();
  assert.equal(f.state.calls.length, 1);
  assert.equal(f.state.calls[0].fields.title, 'Review samples together');
  assert.equal(f.state.calls[0].fields.assignee_user_id, 'actor');
  assert.equal(f.state.calls[0].fields.due_date, '2026-11-05');
});

test('incoming task dates remain editable and invalid dates do not enter the editor', async () => {
  const draft = { key: 'selected', title: 'Review', text: 'Source excerpt', dueDate: '2026-11-03' };
  const f = fixture('ProjectTasksPanel', { initialDraft: draft }); await f.settle();
  assert.equal(f.one(node => node.type === 'input' && node.props.type === 'date').props.value, draft.dueDate);
  assert.equal(f.state.calls.length, 0);
  const invalid = fixture('ProjectTasksPanel', { initialDraft: { ...draft, dueDate: '2026-02-30' } }); await invalid.settle();
  assert.equal(nodes(invalid.tree, node => node.type === 'form').length, 0);
});

test('message task suggestions resolve owners from freshly loaded actual channel members before preview', async () => {
  const sourceMessage = { id: 'message', channel_id: 'room', content: 'Nora reviews the samples tomorrow.' };
  const initialDraft = taskDrafts.taskDraftFromMessage(sourceMessage);
  assert.equal(initialDraft.assigneeUserId, undefined);
  const f = fixture('ProjectTasksPanel', { sourceMessage, initialDraft }); await f.settle();
  assert.equal(f.one(node => node.type === 'select' && node.props['aria-label'] === 'Responsible person').props.value, 'actor');
  assert.equal(f.state.calls.length, 0);
  f.one(node => node.type === 'select' && node.props['aria-label'] === 'Responsible person').props.onChange({ target: { value: '' } }); f.render();
  assert.equal(f.one(node => node.type === 'select' && node.props['aria-label'] === 'Responsible person').props.value, '', 'A reviewed edit is not replaced by inference');
});

test('flow decision is nonmodal, exact, and source navigation keeps a dirty native revision open', async () => {
  const f = fixture('CollaborationPanel', { focusDecisionId: 'decision' }); await f.settle();
  assert.equal(f.state.modalActive, false); assert.equal(f.state.portals, 0); assert.equal(f.tree.props.role, 'region'); assert.equal(f.tree.props['aria-modal'], undefined);
  assert.equal(nodes(f.tree, node => node.type === 'nav').length, 0);
  f.button('Revise').props.onClick(); f.render(); f.edit('decision-content', 'Retain this unsaved revision.');
  f.button('Source').props.onClick(); f.render();
  assert.deepEqual(f.state.opened, [['room', 'message']]); assert.equal(f.state.closes, 0); assert.equal(f.input('decision-content').props.value, 'Retain this unsaved revision.'); assert.equal(f.state.pending.at(-1), true);
});

test('decision cancellation requires confirmation and a failed save keeps fields/pending without an invented record', async () => {
  const f = fixture('CollaborationPanel', { focusDecisionId: 'decision' }); await f.settle(); f.button('Revise').props.onClick(); f.render();
  f.edit('decision-content', 'Changed reason'); f.button('Cancel decision editor').props.onClick(); f.render(); assert.equal(f.input('decision-content').props.value, 'Changed reason');
  f.state.wait = deferred(); const submitting = f.one(node => node.type === 'form').props.onSubmit({ preventDefault() {} }); f.render(); assert.equal(f.state.pending.at(-1), true);
  f.state.wait.reject(new Error('Server refused')); await submitting; await f.settle(); assert.equal(f.state.saved.length, 0); assert.equal(f.input('decision-content').props.value, 'Changed reason');
  f.state.wait = null; await f.one(node => node.type === 'form').props.onSubmit({ preventDefault() {} }); await f.settle();
  assert.equal(f.state.saved.length, 1); assert.equal(f.state.saved[0].revision, 2); assert.equal(f.state.saved[0].source_message_id, 'message'); assert.equal(f.state.pending.at(-1), false);
  const full = fixture('CollaborationPanel', { presentation: 'full' }); await full.settle(); assert.equal(full.state.modalActive, true); assert.ok(full.state.portals > 0); assert.equal(full.tree.props.children.props.role, 'dialog');
});

test('a changed credential cannot publish a late task or decision selection/save callback', async () => {
  for (const kind of ['ProjectTasksPanel', 'CollaborationPanel']) {
    const f = fixture(kind, kind === 'ProjectTasksPanel' ? { initialProjectId: 'project', focusTaskId: 'task' } : { focusDecisionId: 'decision' }); await f.settle(); f.button(kind === 'ProjectTasksPanel' ? 'Edit' : 'Revise').props.onClick(); f.render();
    f.edit(kind === 'ProjectTasksPanel' ? 'project-item-description' : 'decision-content', 'Old account draft');
    f.state.wait = deferred(); const submitting = f.one(node => node.type === 'form').props.onSubmit({ preventDefault() {} }); f.render(); f.state.token = 'replacement-account'; f.state.wait.resolve(); await submitting; await f.settle();
    assert.equal(f.state.saved.length, 0);
  }
});


test('an old editor cannot start a native write after the credential has already changed', async () => {
  for (const kind of ['ProjectTasksPanel', 'CollaborationPanel']) {
    const f = fixture(kind, kind === 'ProjectTasksPanel' ? { initialProjectId: 'project', focusTaskId: 'task' } : { focusDecisionId: 'decision' }); await f.settle();
    f.button(kind === 'ProjectTasksPanel' ? 'Edit' : 'Revise').props.onClick(); f.render();
    f.edit(kind === 'ProjectTasksPanel' ? 'project-item-description' : 'decision-content', 'A draft belonging to the old account');
    f.state.token = 'new-account';
    await f.one(node => node.type === 'form').props.onSubmit({ preventDefault() {} }); await f.settle();
    assert.equal(f.state.calls.length, 0); assert.equal(f.state.saved.length, 0);
  }
});

test('explicitly accepting discard clears the dirty editor and its pending guard', async () => {
  for (const kind of ['ProjectTasksPanel', 'CollaborationPanel']) {
    const f = fixture(kind, kind === 'ProjectTasksPanel' ? { initialProjectId: 'project', focusTaskId: 'task' } : { focusDecisionId: 'decision' }); await f.settle();
    f.button(kind === 'ProjectTasksPanel' ? 'Edit' : 'Revise').props.onClick(); f.render();
    f.edit(kind === 'ProjectTasksPanel' ? 'project-item-description' : 'decision-content', 'Unsaved'); f.state.confirm = true;
    f.button(kind === 'ProjectTasksPanel' ? 'Cancel task or project editing' : 'Cancel decision editor').props.onClick(); f.render();
    assert.equal(nodes(f.tree, node => node.type === 'form').length, 0); assert.equal(f.state.pending.at(-1), false); assert.equal(f.state.calls.length, 0);
  }
});


test('standalone decision flow registers a scoped dirty/busy navigation guard and cleans it up', async () => {
  const f = fixture('CollaborationPanel', { focusDecisionId: 'decision' }); await f.settle();
  assert.equal(f.state.guards.length, 1); assert.deepEqual(plain(f.state.guards[0].owner), { workspaceId: 'workspace', userId: 'actor', credential: 'session' });
  assert.equal(f.state.guards[0].canLeave(), true);
  f.button('Revise').props.onClick(); f.render(); f.edit('decision-content', 'Keep me');
  assert.equal(f.state.guards[0].canLeave(), false); assert.equal(f.input('decision-content').props.value, 'Keep me');
  f.state.confirm = true; assert.equal(f.state.guards[0].canLeave(), true);
  f.state.wait = deferred(); const submitting = f.one(node => node.type === 'form').props.onSubmit({ preventDefault() {} }); f.render();
  assert.equal(f.state.guards[0].canLeave(), false, 'An outstanding native save cannot be discarded');
  f.state.wait.resolve(); await submitting; await f.settle(); assert.equal(f.state.guards[0].canLeave(), true);
  f.unmount(); assert.equal(f.state.guards.length, 0);
});

test('native task flow refuses global navigation while its real save promise is outstanding', async () => {
  const f = fixture('ProjectTasksPanel', { initialProjectId: 'project', focusTaskId: 'task' }); await f.settle();
  assert.equal(f.state.guards.length, 1);
  assert.deepEqual(plain(f.state.guards[0].owner), { workspaceId: 'workspace', userId: 'actor', credential: 'session' });
  f.button('Edit').props.onClick(); f.render(); f.edit('project-item-description', 'Pending native save');
  assert.equal(f.state.guards[0].canLeave(), false);
  f.state.confirm = true; assert.equal(f.state.guards[0].canLeave(), true);
  f.state.wait = deferred(); const submitting = f.one(node => node.type === 'form').props.onSubmit({ preventDefault() {} }); f.render();
  assert.equal(f.state.guards[0].canLeave(), false); assert.equal(f.state.saved.length, 0);
  f.state.wait.resolve(); await submitting; await f.settle();
  assert.equal(f.state.guards[0].canLeave(), true); assert.equal(f.state.saved.length, 1);
  f.unmount(); assert.equal(f.state.guards.length, 0);
});

test('flow project/task options retain native management, archived recovery and exact revision history', async () => {
  const f = fixture('ProjectTasksPanel'); await f.settle();
  const options = f.one(node => node.type === 'details' && node.props.className === 'fc-project-flow-options');
  assert.match(label(options), /Task options/);
  const archived = nodes(options, node => node.type === 'input' && node.props.type === 'checkbox')[1];
  f.state.tasks.push({ ...task, id: 'archived', archived: true, fields: { ...taskFields, title: 'Archived step' } });
  f.button('Refresh projects and tasks').props.onClick(); await f.settle();
  assert.equal(nodes(f.tree, node => node.type === 'button' && label(node).includes('Archived step')).length, 0);
  archived.props.onChange({ target: { checked: true } }); f.render();
  assert.equal(nodes(f.tree, node => node.type === 'button' && label(node).includes('Archived step')).length, 1);
  const projectOptions = f.one(node => node.type === 'details' && node.props.className === 'fc-project-flow-project-options');
  assert.match(label(projectOptions), /Project options/);
  f.button('Edit project').props.onClick(); f.render();
  assert.equal(f.input('project-item-title').props.value, 'Courtyard');
  f.button('Cancel task or project editing').props.onClick(); f.render();
  f.button('Project history').props.onClick(); await f.settle();
  assert.equal(nodes(f.tree, node => node.props['aria-label'] === 'Project or task revision history').length, 1);
});


test('flow saved-items entry respects its native view and exposes pinned/settings through closed contextual options', async () => {
  const f = fixture('CollaborationPanel', { tab: 'bookmarks' }); await f.settle();
  assert.deepEqual(f.state.views, ['bookmarks']); assert.equal(f.tree.props['aria-label'], 'Channel notebook'); assert.match(label(f.tree), /Saved native message/); assert.equal(f.state.modalActive, false);
  const options = f.one(node => node.type === 'details' && node.props.className === 'fc-collaboration-flow-options');
  assert.match(label(options), /Saved items and preferences/); assert.equal(options.props.open, undefined);
  f.button('Pinned').props.onClick(); await f.settle(); assert.equal(f.state.views.at(-1), 'pins');
  f.button('Notifications').props.onClick(); await f.settle(); assert.equal(f.state.views.at(-1), 'notifications');
  assert.equal(nodes(f.tree, node => node.type === 'form').length, 1);
});


test('task and decision editing retain actual reference IDs without publishing until explicit submit', async () => {
  const reference = { kind: 'document', id: 'actual-plan', start: 0, end: 5 };
  for (const kind of ['ProjectTasksPanel', 'CollaborationPanel']) {
    const task = kind === 'ProjectTasksPanel', f = fixture(kind, task ? { initialProjectId: 'project', focusTaskId: 'task' } : { focusDecisionId: 'decision' });
    await f.settle(); f.button(task ? 'Edit' : 'Revise').props.onClick(); f.render();
    const input = f.input(task ? 'project-item-description' : 'decision-content');
    input.props.onValueChange('@Plan review', [reference]); f.render();
    assert.equal(f.state.calls.length, 0);
    assert.deepEqual(plain(f.input(task ? 'project-item-description' : 'decision-content').props.references), [reference]);
    await f.one(node => node.type === 'form').props.onSubmit({ preventDefault() {} }); await f.settle();
    assert.deepEqual(f.state.calls[0].fields.field_references, { [task ? 'description' : 'content']: [reference] });
  }
});

test('activity post and comment fields persist actual references and keep drafts until their own explicit save', async () => {
  const f = fixture('ActivityFeedPanel'); await f.settle();
  f.button('Write an update').props.onClick(); f.render();
  const reference = { kind: 'person', id: 'actual-person', start: 0, end: 4 };
  f.input('feed-title').props.onValueChange('Next steps', []); f.input('feed-body').props.onValueChange('@Ada review', [reference]); f.render();
  assert.equal(f.state.calls.length, 0);
  f.one(node => node.type === 'form' && node.props['aria-label'] === 'Activity editor').props.onSubmit({ preventDefault() {} }); await f.settle();
  assert.deepEqual(f.state.calls[0].fields.field_references, { body: [reference] });
  f.button('Open discussion').props.onClick(); await f.settle();
  f.input('feed-comment').props.onValueChange('@Ada agreed', [reference]); f.render();
  assert.equal(f.state.calls.length, 1);
  f.one(node => node.type === 'form' && node.props['aria-label'] === 'Activity comment editor').props.onSubmit({ preventDefault() {} }); await f.settle();
  assert.deepEqual(f.state.calls[1].fields.field_references, { body: [reference] });
});

test('additional decision instance has unique field IDs and a local guarded close controller', async () => {
  const closeController = { current: null }, f = fixture('CollaborationPanel', { instanceId: 'second', surfaceId: 'card-two', focusDecisionId: 'decision', closeController }); await f.settle();
  f.button('Revise').props.onClick(); f.render();
  assert.equal(f.state.guards[0].owner.surfaceId, 'card-two');
  f.input('second-decision-content').props.onValueChange('Unsaved card two', []); f.render();
  closeController.current(); assert.equal(f.state.closes, 0);
  f.state.confirm = true; closeController.current(); assert.equal(f.state.closes, 1);
  f.unmount(); assert.equal(closeController.current, null);
});

test('workflow approval notes bind their real selected IDs and an open menu blocks the owning Approve/Reject action', async () => {
  const f = fixture('WorkflowsPanel', { instanceId: 'second' }); await f.settle();
  const reference = { kind: 'document', id: 'actual-plan', start: 0, end: 5 };
  f.input('second-workflow-note-run').props.onValueChange('@Plan review', [reference]); f.render();
  const input = f.input('second-workflow-note-run'), approve = f.button('Approve'), reject = f.button('Reject');
  assert.equal(input.props.submissionScope, approve.props['data-collaboration-submit']); assert.equal(input.props.submissionScope, reject.props['data-collaboration-submit']);
  input.props.onPaletteActiveChange(true); approve.props.onClick(); reject.props.onClick(); await f.settle(); assert.equal(f.state.calls.length, 0); assert.equal(f.input('second-workflow-note-run').props.value, '@Plan review');
  input.props.onPaletteActiveChange(false); f.button('Approve').props.onClick(); await f.settle(); assert.equal(f.state.calls.length, 1); assert.deepEqual(f.state.calls[0].references, { note: [reference] });
});


test('command-created task and decision editors retain exact selected reference IDs through their explicit native save', async () => {
  const title = '@Plan next', body = 'Ask @Ada', titleRefs = [{ kind: 'document', id: 'actual-plan', start: 0, end: 5 }], bodyRefs = [{ kind: 'person', id: 'actual-ada', start: 4, end: 8 }];
  for (const kind of ['ProjectTasksPanel', 'CollaborationPanel']) {
    const task = kind === 'ProjectTasksPanel', fields = { title: titleRefs, [task ? 'description' : 'content']: bodyRefs };
    const draft = { key: 'command-references', title, [task ? 'text' : 'content']: body, field_references: fields };
    const f = fixture(kind, { initialDraft: draft, ...(task ? { initialProjectId: 'project' } : {}) }); await f.settle();
    assert.equal(f.state.calls.length, 0); assert.deepEqual(plain(f.input(task ? 'project-item-title' : 'decision-title').props.references), titleRefs); assert.deepEqual(plain(f.input(task ? 'project-item-description' : 'decision-content').props.references), bodyRefs);
    await f.one(node => node.type === 'form').props.onSubmit({ preventDefault() {} }); await f.settle(); assert.deepEqual(f.state.calls[0].fields.field_references, fields);
  }
});
test('malformed command reference metadata cannot be silently adopted or inferred from matching labels', async () => {
  for (const kind of ['ProjectTasksPanel', 'CollaborationPanel']) {
    const task = kind === 'ProjectTasksPanel', f = fixture(kind, { initialDraft: { key: 'invalid-bindings', title: '@Plan', [task ? 'text' : 'content']: 'Context', field_references: { title: [{ kind: 'document', id: 'forged', start: 0, end: 99 }] } }, ...(task ? { initialProjectId: 'project' } : {}) }); await f.settle();
    assert.equal(nodes(f.tree, node => node.props.id === (task ? 'project-item-title' : 'decision-title')).length, 0); assert.equal(f.state.calls.length, 0);
  }
});
