import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const helpers = {};
const source = readFileSync(new URL('../src/lib/discussion-work.ts', import.meta.url), 'utf8');
vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText, { exports: helpers });
const plain = value => JSON.parse(JSON.stringify(value));
const reference = (extra = {}) => ({ id: 'link', channel_id: 'channel', source_message_id: 'root', discussion_message_id: 'root', resource_kind: 'document', resource_id: 'doc', project_id: null, created_by: 'person', created_at: '2026-10-24T10:00:00Z', ...extra });
const document = (extra = {}) => ({ id: 'doc', channel_id: 'channel', title: 'Real brief', archived: false, summary_event_id: null, ...extra });
const task = (extra = {}) => ({ id: 'task', channel_id: 'channel', project_id: 'project', content_mode: 'server', matrix_event_id: null, fields: { title: 'Real next step', status: 'todo' }, ...extra });
const decision = (extra = {}) => ({ id: 'decision', channel_id: 'channel', title: 'Real agreement', content_mode: 'server', matrix_event_id: null, source_message_id: 'reply', discussion_message_id: 'root', created_by: 'person', created_at: '2026-10-24T10:00:00Z', status: 'accepted', ...extra });
const row = (extra = {}, fields = {}) => ({ reference: reference(extra), title: 'Real resource', ...fields });
const message = (id, parent_id, extra = {}) => ({ id, channel_id: 'channel', parent_id, ...extra });
const reader = (extra = {}) => ({ current: () => true, references: async () => ({ items: [reference()], next_offset: null }), documents: async () => [document()], decisions: async () => [], tasks: async () => [], ...extra });

test('all routing pages are read once and document candidates come from real channel records', async () => {
  const offsets = [];
  const result = await helpers.readDiscussionWork('channel', reader({ references: async offset => {
    offsets.push(offset);
    return { items: [reference({ id: 'ref-' + offset, source_message_id: 'message-' + offset, discussion_message_id: 'message-' + offset })], next_offset: offset < 200 ? offset + 100 : null };
  } }));
  assert.deepEqual(offsets, [0, 100, 200]);
  assert.equal(result.rows.length, 3); assert.equal(result.documents[0].id, 'doc'); assert.equal(result.partial, false);
});

test('empty scanned routing pages still follow the authoritative next cursor', async () => {
  const offsets = [];
  const result = await helpers.readDiscussionWork('channel', reader({ references: async offset => {
    offsets.push(offset); return offset === 0 ? { items: [], next_offset: 100 } : { items: [reference()], next_offset: null };
  } }));
  assert.deepEqual(offsets, [0, 100]); assert.equal(result.rows.length, 1);
});

test('linked tasks beyond the first eight boards are read with bounded concurrency', async () => {
  const requested = []; let concurrent = 0, maximum = 0;
  const refs = Array.from({ length: 13 }, (_, index) => reference({ id: 'ref-' + index, resource_kind: 'task', resource_id: 'task-' + index, project_id: 'project-' + index }));
  const result = await helpers.readDiscussionWork('channel', reader({ references: async () => ({ items: refs, next_offset: null }), tasks: async id => {
    requested.push(id); concurrent++; maximum = Math.max(maximum, concurrent); await new Promise(resolve => setImmediate(resolve)); concurrent--;
    return [task({ id: 'task-' + id.slice(8), project_id: id })];
  } }));
  assert.equal(requested.length, 13); assert.equal(result.rows.length, 13); assert.ok(maximum <= 4); assert.equal(result.partial, false);
});

test('deleted, foreign, encrypted and unavailable records cannot become visible native work', async () => {
  const refs = [reference(), reference({ id: 'task-ref', resource_kind: 'task', resource_id: 'task', project_id: 'project' })];
  const result = await helpers.readDiscussionWork('channel', reader({ references: async () => ({ items: refs, next_offset: null }),
    documents: async () => [document({ archived: true }), document({ channel_id: 'foreign' }), document({ summary_event_id: '$encrypted' }), document({ resolution_error: 'Unavailable' })],
    tasks: async () => [task({ content_mode: 'matrix' }), task({ project_id: 'foreign-project' }), task({ resolution_error: 'Unavailable' })],
    decisions: async () => [decision({ content_mode: 'matrix' }), decision({ channel_id: 'foreign' }), decision({ discussion_message_id: null })],
  }));
  assert.equal(result.rows.length, 0); assert.equal(result.documents.length, 0);
});

test('canonical reply decisions are derived without creating an index record', async () => {
  const result = await helpers.readDiscussionWork('channel', reader({ references: async () => ({ items: [], next_offset: null }), decisions: async () => [decision()] }));
  assert.equal(result.rows.length, 1); assert.equal(result.rows[0].reference.source_message_id, 'reply');
  assert.equal(result.rows[0].reference.discussion_message_id, 'root');
});

test('failed resource groups are marked partial while other genuine resources remain', async () => {
  const result = await helpers.readDiscussionWork('channel', reader({ decisions: async () => { throw new Error('Unavailable'); } }));
  assert.equal(result.partial, true); assert.equal(result.rows.length, 1); assert.equal(result.rows[0].title, 'Real brief');
});

test('invalid routing cursors stop loading and never imply complete counts', async () => {
  let calls = 0;
  const result = await helpers.readDiscussionWork('channel', reader({ references: async () => { calls++; return { items: [reference()], next_offset: 0 }; } }));
  assert.equal(calls, 1); assert.equal(result.partial, true); assert.equal(result.rows.length, 0);
});

test('actor invalidation during paging or task resolution discards every old result', async () => {
  let live = true, calls = 0;
  const stale = await helpers.readDiscussionWork('channel', reader({ current: () => live, references: async () => { calls++; live = false; return { items: [reference()], next_offset: 100 }; } }));
  assert.equal(stale, null); assert.equal(calls, 1);
  live = true;
  const taskStale = await helpers.readDiscussionWork('channel', reader({ current: () => live,
    references: async () => ({ items: [reference({ resource_kind: 'task', resource_id: 'task', project_id: 'project' })], next_offset: null }),
    tasks: async () => { live = false; return [task()]; },
  }));
  assert.equal(taskStale, null);
});

test('unopened replies aggregate through authorized server roots and deduplicate resources by kind', () => {
  const rows = [row(), row({ id: 'reply-link', source_message_id: 'unopened-reply' }), row({ id: 'task-link', source_message_id: 'unopened-reply', resource_kind: 'task', resource_id: 'doc', project_id: 'project' })];
  const grouped = helpers.groupDiscussionWork(rows, [message('root')], 'channel');
  assert.equal(grouped.get('root').length, 2);
  assert.deepEqual(plain(helpers.discussionWorkCounts(grouped.get('root'))), { document: 1, task: 1, decision: 0 });
});

test('known reply ancestry works with older servers but an unknown parent is never invented as a root', () => {
  const legacy = row({ discussion_message_id: undefined, source_message_id: 'reply' });
  const complete = helpers.groupDiscussionWork([legacy], [message('reply', 'root'), message('root')], 'channel');
  assert.equal(complete.get('root').length, 1);
  const incomplete = helpers.groupDiscussionWork([legacy], [message('reply', 'unknown-parent')], 'channel');
  assert.equal(incomplete.has('unknown-parent'), false); assert.equal(incomplete.get('reply').length, 1);
});

test('deleted, locked, conflicting, foreign and cyclic message paths cannot produce a discussion group', () => {
  const rows = [row({ source_message_id: 'reply' })];
  for (const messages of [[message('reply', 'root', { deleted: true }), message('root')], [message('reply', 'root'), message('root', undefined, { locked: true })],
    [message('reply', 'different'), message('different')], [message('reply', 'root'), message('root', 'reply')]]) {
    assert.equal(helpers.groupDiscussionWork(rows, messages, 'channel').size, 0);
  }
  assert.equal(helpers.groupDiscussionWork([row({ channel_id: 'foreign' })], [message('root')], 'channel').size, 0);
});

test('authoritative channel revocation or encryption binding rejection purges earlier sibling results', async () => {
  for (const status of [401, 403, 404, 409]) {
    const result = await helpers.readDiscussionWork('channel', reader({ decisions: async () => { throw { status }; } }));
    assert.deepEqual(plain(result), { rows: [], documents: [], partial: true });
  }
  const result = await helpers.readDiscussionWork('channel', reader({
    references: async () => ({ items: [reference(), reference({ id: 'task-ref', resource_kind: 'task', resource_id: 'task', project_id: 'project' })], next_offset: null }),
    tasks: async () => { throw { status: 403 }; },
  }));
  assert.deepEqual(plain(result), { rows: [], documents: [], partial: true });
});

test('an individually deleted project is partial and cannot erase other authorized current work', async () => {
  const result = await helpers.readDiscussionWork('channel', reader({
    references: async () => ({ items: [reference(), reference({ id: 'task-ref', resource_kind: 'task', resource_id: 'task', project_id: 'project' })], next_offset: null }),
    tasks: async () => { throw { status: 404 }; },
  }));
  assert.equal(result.partial, true); assert.equal(result.rows.length, 1); assert.equal(result.rows[0].reference.resource_kind, 'document');
});

test('older source-only responses stay usable while disclosing incomplete discussion routing', async () => {
  const result = await helpers.readDiscussionWork('channel', reader({ references: async () => ({ items: [reference({ discussion_message_id: undefined })], next_offset: null }) }));
  assert.equal(result.rows.length, 1); assert.equal(result.rows[0].reference.source_message_id, 'root'); assert.equal(result.partial, true);
});

async function mountActualHook({ workspace = 'workspace', targetWorkspace = 'workspace', handled = false, delayNative = false } = {}) {
  const text = readFileSync(new URL('../src/components/ConversationWorkReferences.tsx', import.meta.url), 'utf8');
  const tree = ts.createSourceFile('ConversationWorkReferences.tsx', text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let declaration;
  function find(node) { if (ts.isFunctionDeclaration(node) && node.name?.text === 'useConversationWorkReferences') declaration = node.getText(tree); ts.forEachChild(node, find); }
  find(tree); assert.ok(declaration);
  const material = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/lib/chat-shared-material.ts', import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText, { exports: material });
  const exports = {}, effects = [], emissions = [];
  let token = 'token', reads = 0, nativeReads = 0, resolveNative;
  const forbidden = () => { reads++; throw new Error('Unexpected native resource read'); };
  const runtime = {
    exports, AbortController, JSON, Map,
    api: { getCurrentUserId: () => 'actor', getAccessToken: () => token },
    useAccount: () => ({ workspace: { id: workspace } }),
    useRef: value => ({ current: value }), useState: value => [value, next => emissions.push(next)],
    useEffect: effect => effects.push(effect),
    document: { hidden: false, addEventListener() {}, removeEventListener() {} },
    window: { addEventListener() {}, removeEventListener() {}, setInterval: () => 1 }, clearInterval() {},
    allowsChannelMaterial: material.allowsChannelMaterial,
    getSecureMessaging: async () => ({ handles: async () => { nativeReads++; return delayNative ? new Promise(resolve => resolveNative = resolve) : handled; } }),
    conversationWorkApi: { list: forbidden }, sharedDocumentsApi: { list: forbidden }, collaborationApi: { decisions: forbidden }, projectTasksApi: { tasks: forbidden },
    readDiscussionWork: forbidden,
  };
  vm.runInNewContext(ts.transpileModule(declaration + '\nexports.mount=useConversationWorkReferences;', {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText, runtime);
  exports.mount({ id: 'channel', workspace_id: targetWorkspace }, targetWorkspace, 0);
  const cleanups = effects.map(effect => effect());
  for (let i = 0; i < 4; i++) await new Promise(resolve => setImmediate(resolve));
  return { emissions, reads: () => reads, nativeReads: () => nativeReads,
    changeToken: () => { token = 'replacement-token'; }, resolveNative: value => resolveNative?.(value),
    settle: async () => { for (let i = 0; i < 4; i++) await new Promise(resolve => setImmediate(resolve)); },
    cleanup: () => cleanups.forEach(cleanup => cleanup?.()),
  };
}

test('the actual channel hook refuses a workspace mismatch before any native binding or content request', async () => {
  const mounted = await mountActualHook({ workspace: 'current', targetWorkspace: 'foreign' });
  try { assert.equal(mounted.reads(), 0); assert.equal(mounted.nativeReads(), 0); assert.ok(mounted.emissions.some(value => value?.supported === false)); }
  finally { mounted.cleanup(); }
});

test('the actual channel hook never falls back to native routing for an encrypted client binding', async () => {
  const mounted = await mountActualHook({ handled: true });
  try { assert.equal(mounted.reads(), 0); assert.equal(mounted.nativeReads(), 1); assert.ok(mounted.emissions.some(value => value?.supported === false)); }
  finally { mounted.cleanup(); }
});

test('a late native-binding result cannot start requests for the previous actor credential', async () => {
  const mounted = await mountActualHook({ delayNative: true });
  try {
    mounted.changeToken(); mounted.resolveNative(false); await mounted.settle();
    assert.equal(mounted.reads(), 0); assert.equal(mounted.emissions.some(value => value?.supported === true), false);
  } finally { mounted.cleanup(); }
});
