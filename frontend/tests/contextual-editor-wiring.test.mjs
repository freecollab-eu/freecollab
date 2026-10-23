import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const plain = value => JSON.parse(JSON.stringify(value));
const translate = (source, values = {}) => source.replace(/\{(\w+)\}/g, (all, key) => Object.hasOwn(values, key) ? String(values[key]) : all);

/** Exercise the real parent callbacks without a provider, effects, timers or a server. */
function componentFixture(name, states) {
  const source = readFileSync(new URL('../src/components/' + name + '.tsx', import.meta.url), 'utf8');
  const parsed = ts.createSourceFile(name + '.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const component = parsed.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === name);
  assert.ok(component?.body, 'the fixture executes the actual named component');
  // Resolve the real hook names instead of pinning numeric slots. Adding a
  // state/ref for flow presentation must not silently seed a different field.
  const hooks = { useState: [], useRef: [] };
  function collect(node) {
    if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && Object.hasOwn(hooks, node.expression.text)) {
      const declaration = node.parent;
      assert.ok(ts.isVariableDeclaration(declaration), 'fixture hooks have explicit variable declarations');
      const binding = ts.isArrayBindingPattern(declaration.name) ? declaration.name.elements[0].name : declaration.name;
      assert.ok(ts.isIdentifier(binding));
      hooks[node.expression.text].push(binding.text);
    }
    ts.forEachChild(node, collect);
  }
  collect(component.body);
  const state = { token: 'account-one', states, refs: {}, updates: [], mutations: [] };
  let stateIndex = 0, refIndex = 0;
  const Copilot = () => null;
  const jsx = (type, props) => ({ type, props: props || {} });
  const enqueue = (index, value) => state.updates.push({ index, value });
  const react = {
    useState(initial) {
      const index = hooks.useState[stateIndex++];
      assert.ok(index, 'every state call belongs to a named real component hook');
      if (!Object.hasOwn(state.states, index)) state.states[index] = typeof initial === 'function' ? initial() : initial;
      return [state.states[index], value => enqueue(index, value)];
    },
    useRef(initial) { const index = hooks.useRef[refIndex++]; assert.ok(index); return state.refs[index] ?? (state.refs[index] = { current: initial }); },
    useEffect() {},
    useLayoutEffect() {},
  };
  const unexpected = () => { throw new Error('Applying an AI result must not save a project or task.'); };
  const api = { getAccessToken: () => state.token, getCurrentUserId: () => 'actor' };
  const dependencies = {
    react, 'react/jsx-runtime': { jsx, jsxs: jsx },
    '../i18n': { t: translate, useI18n: () => ({ t: translate }), currentLocale: () => 'en' },
    'lucide-react': new Proxy({}, { get: (_, key) => () => key }),
    '../lib/api': { api }, './ContextualCopilot': { ContextualCopilot: Copilot },
    '../lib/shared-documents-api': {}, '../lib/collaboration-api': {}, '../lib/files-api': {},
    '../lib/project-tasks-api': { projectTasksApi: { saveTask: unexpected, saveProject: unexpected } },
  };
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const exports = {};
  vm.runInNewContext(compiled, { exports, require: id => dependencies[id] ?? {}, Date, Set, Map, JSON, crypto: { randomUUID: () => 'new-id' } });
  const nodes = (node, predicate, result = []) => {
    if (Array.isArray(node)) { node.forEach(value => nodes(value, predicate, result)); return result; }
    if (!node || typeof node !== 'object') return result;
    if (predicate(node)) result.push(node);
    nodes(node.props?.children, predicate, result);
    return result;
  };
  const flush = () => {
    while (state.updates.length) {
      const { index, value } = state.updates.shift();
      state.states[index] = typeof value === 'function' ? value(state.states[index]) : value;
    }
  };
  let tree;
  const render = () => {
    flush(); stateIndex = 0; refIndex = 0;
    tree = exports[name]({ workspaceId: 'workspace', channelId: 'channel' });
    return nodes(tree, node => node.type === Copilot).map(node => node.props);
  };
  return { state, enqueue, flush, render, elements: predicate => nodes(tree, predicate) };
}

const documentRecord = {
  id: 'document', channel_id: 'channel', created_by: 'actor', created_at: '2026-10-23T00:00:00Z', updated_at: '2026-10-23T00:00:00Z',
  archived: false, version: 4, update_count: 7, summary_event_id: null, summary_update_id: null, summary_by: null,
};
function documentFixture(body = 'Original document') {
  const value = { title: 'Plan', body, pending: 0, ready: true, locked: false, error: '', sequence: 7 };
  // Selected document and its synchronized view; unrelated effects are deliberately inactive.
  const fixture = componentFixture('SharedDocumentsPanel', { selected: plain(documentRecord), state: plain(value) });
  fixture.render();
  const controller = {
    channelId: 'channel', metadata: plain(documentRecord), state: () => ({ ...value }),
    replace(field, text) { fixture.state.mutations.push({ field, text }); value[field] = text; value.pending = 1; fixture.enqueue('state', { ...value }); },
  };
  fixture.state.refs.session.current = controller;
  const props = fixture.render()[0];
  assert.ok(props, 'verified document exposes in-place AI help');
  return { ...fixture, value, controller, props };
}

test('document application appends once through the existing collaborative edit boundary', () => {
  const fixture = documentFixture();
  assert.deepEqual(plain(fixture.props.sourceRefs), [{ kind: 'shared_document', id: 'document' }]);
  assert.equal(fixture.props.onApply('Reviewed addition', fixture.props.contextKey), true);
  assert.equal(fixture.value.body, 'Original document\n\nReviewed addition');
  assert.equal(fixture.value.pending, 1);
  assert.equal(fixture.props.onApply('Reviewed addition', fixture.props.contextKey), false);
  assert.equal(fixture.state.mutations.length, 1);
});

const documentDrift = [
  ['body changed before React rendered', f => { f.value.body += ' remote edit'; }],
  ['title changed', f => { f.value.title = 'Renamed'; }],
  ['history sequence advanced', f => { f.value.sequence++; }],
  ['local edit is pending', f => { f.value.pending++; }],
  ['document became locked', f => { f.value.locked = true; }],
  ['document is no longer ready', f => { f.value.ready = false; }],
  ['synchronization failed', f => { f.value.error = 'Sync failed'; }],
  ['document version advanced', f => { f.controller.metadata.version++; }],
  ['document was archived', f => { f.controller.metadata.archived = true; }],
  ['account changed', f => { f.state.token = 'replacement-account'; }],
  ['panel generation changed', f => { f.state.refs.generation.current++; }],
  ['document session was replaced', f => { f.state.refs.session.current = { ...f.controller }; }],
  ['another document was selected', f => { f.state.refs.selectedId.current = 'another-document'; }],
];
for (const [reason, change] of documentDrift) test('document apply rejects when ' + reason, () => {
  const fixture = documentFixture(); change(fixture);
  assert.equal(fixture.props.onApply('Must not append', fixture.props.contextKey), false);
  assert.equal(fixture.state.mutations.length, 0);
});

test('document application accounts for the separator in its exact 50,000-character budget', () => {
  const fixture = documentFixture('x'.repeat(49995));
  assert.equal(fixture.props.maxApplyLength, 3);
  assert.equal(fixture.props.onApply('four', fixture.props.contextKey), false);
  assert.equal(fixture.props.onApply('yes', fixture.props.contextKey), true);
  assert.equal(fixture.value.body.length, 50000);
});

const project = { id: 'project', kind: 'project', channel_id: 'channel', project_id: null, created_by: 'actor', revision: 2, archived: false, fields: { title: 'Launch', description: 'Project details', color: 'sage' } };
const task = {
  id: 'task', kind: 'task', channel_id: 'channel', project_id: 'project', created_by: 'actor', revision: 3, archived: false,
  fields: { title: 'Verify release', description: 'Keep these details', status: 'doing', priority: 'high', assignee_user_id: 'owner', due_date: '2026-11-02', checklist: [{ id: 'first', title: 'Check it', done: false }], file_ids: ['file'], decision_ids: ['decision'] },
};
function editorFixture(kind = 'task', description) {
  const row = plain(kind === 'task' ? task : project);
  if (description !== undefined) row.fields.description = description;
  const editor = { kind, id: row.id, fields: plain(row.fields), previous: row };
  const fixture = componentFixture('ProjectTasksPanel', { projects: [plain(project)], selected: 'project', tasks: kind === 'task' ? [row] : [], loading: false, editor });
  const props = fixture.render().find(value => value.contextLabel === (kind === 'task' ? 'Task description draft' : 'Project description draft'));
  assert.ok(props, 'editor exposes in-place AI help');
  return { ...fixture, props, editor };
}

for (const kind of ['task', 'project']) test(kind + ' applies only to its description draft and never calls Save', () => {
  const fixture = editorFixture(kind), before = plain(fixture.editor.fields);
  assert.equal(fixture.props.onApply('Reviewed description', fixture.props.contextKey), true);
  fixture.flush();
  const after = fixture.state.states.editor.fields;
  assert.equal(after.description, 'Reviewed description');
  const preserved = plain(after); delete before.description; delete preserved.description;
  assert.deepEqual(preserved, before);
  assert.equal(JSON.stringify(fixture.props.sourceRefs), '[]', 'task and project JSON belongs to the reviewed prompt, not invented native citations');
  assert.equal(fixture.props.onApply('x'.repeat(kind === 'task' ? 12001 : 4001), fixture.props.contextKey), false);
});

for (const field of ['title', 'description', 'status', 'priority', 'assignee_user_id', 'due_date', 'checklist', 'file_ids', 'decision_ids']) test('a queued user edit to ' + field + ' invalidates the old task suggestion', () => {
  const fixture = editorFixture();
  const changed = plain(fixture.editor);
  changed.fields[field] = Array.isArray(changed.fields[field]) ? [] : 'New user value';
  fixture.enqueue('editor', changed);
  fixture.props.onApply('Must not overwrite', fixture.props.contextKey);
  fixture.flush();
  assert.deepEqual(plain(fixture.state.states.editor), changed);
});

test('the actual description input preserves its queued user text against an earlier AI callback', () => {
  const fixture = editorFixture();
  const input = fixture.elements(node => node.type === 'textarea' && node.props.id === 'project-item-description')[0];
  input.props.onChange({ target: { value: 'My newer wording' } });
  fixture.props.onApply('Earlier AI wording', fixture.props.contextKey);
  fixture.flush();
  assert.equal(fixture.state.states.editor.fields.description, 'My newer wording');
});

test('changing account prevents any editor application', () => {
  const fixture = editorFixture(); fixture.state.token = 'replacement-account';
  assert.equal(fixture.props.onApply('Never apply', fixture.props.contextKey), false);
  fixture.flush(); assert.equal(fixture.state.states.editor.fields.description, 'Keep these details');
});

test('long, quoted drafts produce bounded, valid, explicitly marked JSON in the reviewed request', () => {
  const fixture = editorFixture('task', 'Quoted "data" and \\ text 😀 '.repeat(500));
  for (const props of fixture.render()) {
    assert.equal(JSON.stringify(props.sourceRefs), '[]');
    if (props.contextLabel !== 'Task description draft') { assert.equal(props.onApply, undefined); continue; }
    for (const action of props.actions) {
      assert.ok(action.prompt.length <= 3900, 'request stays within the shared copilot prompt limit');
      const body = JSON.parse(action.prompt.slice(action.prompt.lastIndexOf('\n\n') + 2));
      assert.equal(body.truncated, true);
      assert.equal(typeof body.draft_json_excerpt, 'string');
      assert.ok(JSON.stringify({ kind: 'task', fields: fixture.editor.fields }).startsWith(body.draft_json_excerpt));
    }
  }
});

test('short drafts disclose the exact fields as JSON while saved-card help remains read-only', () => {
  const fixture = editorFixture();
  for (const props of fixture.render()) {
    if (props.contextLabel !== 'Task description draft') { assert.equal(props.onApply, undefined); continue; }
    for (const action of props.actions) {
      const body = JSON.parse(action.prompt.slice(action.prompt.lastIndexOf('\n\n') + 2));
      assert.equal(body.kind, 'task'); assert.deepEqual(body.fields, plain(fixture.editor.fields));
    }
  }
});
