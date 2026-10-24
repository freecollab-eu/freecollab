import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const compile = value => ts.transpileModule(value, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const helpers = {};
vm.runInNewContext(compile(readFileSync(new URL('../src/lib/chat-shared-material.ts', import.meta.url), 'utf8')), { exports: helpers });
const scope = { token: 'account-token', actor: 'actor', workspaceId: 'workspace', channel: { id: 'channel', workspace_id: 'workspace', encrypted_channel_key: null } };
const file = (extra = {}) => ({ id: 'photo', channel_id: 'channel', filename: 'Actual photo.png', content_type: 'image/png', encrypted: false, created_at: '2026-10-23T10:00:00Z', ...extra });
const document = (extra = {}) => ({ id: 'document', channel_id: 'channel', title: 'Actual shared brief', archived: false, updated_at: '2026-10-23T10:00:00Z', summary_event_id: null, ...extra });
const project = (extra = {}) => ({ id: 'project', channel_id: 'channel', kind: 'project', archived: false, content_mode: 'server', revised_at: '2026-10-23T10:00:00Z', fields: { title: 'Actual project' }, ...extra });
const task = (extra = {}) => ({ id: 'task', project_id: 'project', channel_id: 'channel', kind: 'task', archived: false, content_mode: 'server', created_at: '2026-10-23T10:00:00Z', fields: { title: 'Actual unfinished task', status: 'todo', priority: 'normal', due_date: null }, ...extra });
const desk = (extra = {}) => ({ supported: true, stacks: [], placements: [], ...extra });
function reader(extra = {}) {
  return { current: () => true, native: async () => true, files: async () => [file()], documents: async () => [document()], organization: async () => desk(), projects: async () => [project()], tasks: async () => [task()], readDocument: async row => ({ document: row, text: 'Verified body from the actual Yjs reader.' }), ...extra };
}

test('one real native document, its current stack and a real unfinished task accompany the channel image', async () => {
  let reads = 0;
  const result = await helpers.readChannelMaterial(scope, reader({
    files: async () => [file({ id: 'new-pdf', content_type: 'application/pdf', created_at: '2026-10-23T11:00:00Z' }), file(), file({ id: 'foreign', channel_id: 'other' })],
    organization: async () => desk({ stacks: [{ id: 'stack', channel_id: 'channel', title: 'Actual stack' }], placements: [{ document_id: 'document', channel_id: 'channel', stack_id: 'stack' }] }),
    tasks: async () => [task({ id: 'done', fields: { status: 'done' } }), task({ id: 'foreign', channel_id: 'other' }), task({ id: 'other-board', project_id: 'different' }), task()],
    readDocument: async row => { reads++; return { document: row, text: 'A'.repeat(500) }; },
  }));
  assert.equal(result.file.id, 'photo', 'A real image stays the visual hero when a newer non-image file exists');
  assert.equal(result.document.record.id, 'document'); assert.equal(result.document.stack.id, 'stack'); assert.equal(result.document.stackDocumentCount, 1);
  assert.equal(result.document.excerpt.length, 361); assert.equal(reads, 1); assert.equal(result.nextTask.record.id, 'task'); assert.equal(result.nextTask.project.id, 'project');
});

test('encrypted, foreign workspace and unresolved secure binding never cause plaintext preview reads', async () => {
  for (const channel of [{ ...scope.channel, secure: true }, { ...scope.channel, matrix_room_id: '!room' }, { ...scope.channel, encrypted_channel_key: 'locked' }, { ...scope.channel, workspace_id: 'another' }]) {
    let calls = 0;
    assert.equal(await helpers.readChannelMaterial({ ...scope, channel }, reader({ native: async () => { calls++; return true; } })), null);
    assert.equal(calls, 0);
  }
  let calls = 0;
  assert.equal(await helpers.readChannelMaterial(scope, reader({ native: async () => false, files: async () => { calls++; return []; } })), null);
  assert.equal(calls, 0);
  assert.equal(await helpers.readChannelMaterial(scope, reader({ organization: async () => desk({ supported: false }) })), null);
});

test('partial failures and foreign or encrypted API rows never produce a fabricated brief or task', async () => {
  const result = await helpers.readChannelMaterial(scope, reader({ documents: async () => { throw new Error('Unavailable'); }, projects: async () => [project({ channel_id: 'foreign' })] }));
  assert.equal(result.file.id, 'photo'); assert.equal(result.document, undefined); assert.equal(result.nextTask, undefined);
  const empty = await helpers.readChannelMaterial(scope, reader({ files: async () => [file({ encrypted: true }), file({ secure_locator: '$encrypted' })], documents: async () => [document({ channel_id: 'foreign' }), document({ resolution_error: 'Locked' })], projects: async () => [project({ content_mode: 'matrix' })] }));
  assert.equal(empty, null);
});

test('a late native-binding or document result cannot cross the current actor/channel scope', async () => {
  let release, live = true, metadata = 0;
  const pending = helpers.readChannelMaterial(scope, reader({ current: () => live, native: () => new Promise(resolve => release = resolve), files: async () => { metadata++; return []; } }));
  live = false; release(true); assert.equal(await pending, null); assert.equal(metadata, 0);
  live = true;
  const staleBody = await helpers.readChannelMaterial(scope, reader({ current: () => live, readDocument: async row => { live = false; return { document: row, text: 'Old account content' }; } }));
  assert.equal(staleBody, null);
});

test('only four recent current-channel projects are read and an in-progress task is actionable', async () => {
  const read = [];
  const result = await helpers.readChannelMaterial(scope, reader({
    projects: async () => Array.from({ length: 9 }, (_, index) => project({ id: 'project-' + index, revised_at: `2026-10-${String(10 + index).padStart(2, '0')}T10:00:00Z` })),
    tasks: async board => { read.push(board.id); return [task({ project_id: board.id, fields: { title: 'Visible next action', status: board.id === 'project-7' ? 'doing' : 'todo', priority: 'normal', due_date: null } })]; },
  }));
  assert.equal(read.length, 4); assert.equal(result.nextTask.project.id, 'project-7');
});

function actualDocumentLoader(rows, { target = 'document', currentSelection = null } = {}) {
  const source = readFileSync(new URL('../src/components/SharedDocumentsPanel.tsx', import.meta.url), 'utf8');
  const tree = ts.createSourceFile('SharedDocumentsPanel.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let declaration;
  function walk(node) { if (ts.isFunctionDeclaration(node) && node.name?.text === 'load') declaration = node.getText(tree); ts.forEachChild(node, walk); }
  walk(tree); assert.ok(declaration);
  const exports = {}, state = { rows: [], selected: currentSelection }, initialSelectionApplied = { current: false }, selectedId = { current: currentSelection?.id || null };
  let live = true;
  vm.runInNewContext(compile(declaration + '\nexports.load=load;'), {
    exports, generation: { current: 0 }, listRequest: { current: 0 }, showArchived: false, channelId: 'channel', initialDocumentId: target, initialSelectionApplied, selectedId,
    sharedDocumentsApi: { list: async () => typeof rows === 'function' ? rows() : rows }, current: () => live,
    setLoading: () => {}, setError: () => {}, describe: String, setRows: value => state.rows = value, setSelected: value => { state.selected = value; selectedId.current = value.id; },
  });
  return { ...exports, state, initialSelectionApplied, stop: () => live = false };
}

test('actual editor deep links select only a returned live channel document, once', async () => {
  const loader = actualDocumentLoader([document(), document({ id: 'foreign', channel_id: 'other' })]);
  await loader.load(); assert.equal(loader.state.selected.id, 'document'); assert.equal(loader.state.rows.length, 1);
  loader.state.selected = null; await loader.load(); assert.equal(loader.state.selected, null, 'A list refresh must not reopen a consumed deep link');
  for (const record of [document({ channel_id: 'other' }), document({ archived: true })]) {
    const invalid = actualDocumentLoader([record]); await invalid.load(); assert.equal(invalid.state.selected, null);
  }
  const creating = actualDocumentLoader([document()], { currentSelection: document({ id: 'new-user-page' }) });
  await creating.load(); assert.equal(creating.state.selected.id, 'new-user-page', 'An initial target must not replace a newly created page');
});

test('actual editor deep link ignores an old-account response after the scope is invalidated', async () => {
  let release;
  const loader = actualDocumentLoader(() => new Promise(resolve => release = resolve));
  const pending = loader.load(); loader.stop(); release([document()]); await pending;
  assert.equal(loader.state.rows.length, 0); assert.equal(loader.state.selected, null); assert.equal(loader.initialSelectionApplied.current, false);
});

test('the actual native card click guard rejects a changed account or workspace before effects render', () => {
  const source = readFileSync(new URL('../src/components/ChatSharedMaterial.tsx', import.meta.url), 'utf8');
  const tree = ts.createSourceFile('ChatSharedMaterial.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let declaration;
  function walk(node) { if (ts.isVariableDeclaration(node) && node.name.getText(tree) === 'inScope') declaration = node.getText(tree); ts.forEachChild(node, walk); }
  walk(tree); assert.ok(declaration);
  function guard(extra = {}) {
    const exports = {};
    vm.runInNewContext(compile('const ' + declaration + ';exports.allowed=inScope;'), {
      exports, material: { scope }, api: { getAccessToken: () => scope.token, getCurrentUserId: () => scope.actor },
      channel: scope.channel, workspace: { id: scope.workspaceId }, workspaceId: scope.workspaceId, scope: 'rendered-scope',
      latestScope: { current: 'rendered-scope' }, invalidatedScope: { current: null }, allowsChannelMaterial: helpers.allowsChannelMaterial, ...extra,
    });
    return exports.allowed();
  }
  assert.equal(guard(), true);
  for (const changed of [
    { api: { getAccessToken: () => 'another-token', getCurrentUserId: () => scope.actor } },
    { api: { getAccessToken: () => scope.token, getCurrentUserId: () => 'another-actor' } },
    { workspace: { id: 'another-workspace' } }, { channel: { ...scope.channel, id: 'another-channel' } },
    { channel: { ...scope.channel, secure: true } }, { latestScope: { current: 'next-render-scope' } },
    { invalidatedScope: { current: 'rendered-scope' } },
  ]) assert.equal(guard(changed), false);
});
