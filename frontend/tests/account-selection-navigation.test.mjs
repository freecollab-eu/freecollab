import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

// Run the actual provider callbacks with a held workspace-list request. The
// fixture does not copy selection, authority, guard or race decisions.
function fixture() {
  const source = readFileSync(new URL('../src/account/AccountProvider.tsx', import.meta.url), 'utf8');
  const tree = ts.createSourceFile('AccountProvider.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const wanted = new Set(['choose', 'refreshWorkspaces', 'selectWorkspace', 'sync']), declarations = [];
  function visit(node) { if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && wanted.has(node.name.text)) declarations.push('const ' + node.getText(tree) + ';'); ts.forEachChild(node, visit); }
  visit(tree); assert.equal(declarations.length, wanted.size);
  const records = ['a', 'b', 'c'].map(id => ({ id }));
  const state = { actor: 'actor', token: 'token', selected: 'a', workspaces: records, loading: false, error: '', guards: 0, allowed: true, events: [], panels: [] };
  const held = [];
  const context = { exports: {}, useCallback: fn => fn, identity: 'actor', workspaces: records,
    identityRef: { current: 'actor' }, sessionRef: { current: 'token' }, selectedRef: { current: 'a' }, generation: { current: 0 }, selectionGeneration: { current: 0 }, mounted: { current: true },
    api: { getCurrentUserId: () => state.actor, getAccessToken: () => state.token, isMemorySession: () => true, getMe: async () => ({ session_scope: 'workspace' }) },
    workspaceApi: { list: () => new Promise((resolve, reject) => held.push({ resolve, reject })) },
    savedWorkspace: () => 'a', setConversationNavigationWorkspace() {}, mayLeaveConversationWork: () => { state.guards++; return state.allowed; },
    setSelected: value => state.selected = value, setWorkspaces: value => state.workspaces = value, setWorkspacesLoading: value => state.loading = value,
    setWorkspacesError: value => state.error = value, setPanel: value => state.panels.push(value), setIdentity() {},
    window: { dispatchEvent: event => state.events.push(event) }, CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
  };
  vm.runInNewContext(ts.transpileModule(declarations.join('\n') + '\n' + [...wanted].map(name => 'exports.' + name + '=' + name + ';').join('\n'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, context);
  return { ...context.exports, state, context, records, held };
}

test('an explicit held refresh cannot override a newer manual workspace choice', async () => {
  const f = fixture(), pending = f.refreshWorkspaces('c');
  f.selectWorkspace('b'); assert.equal(f.state.selected, 'b');
  f.held[0].resolve(f.records); await pending;
  assert.equal(f.state.selected, 'b'); assert.equal(f.state.loading, false); assert.equal(f.state.guards, 1);
});

test('manual A→B→A and selecting A again invalidate the older explicit selection', async () => {
  for (const choices of [['b', 'a'], ['a']]) {
    const f = fixture(), pending = f.refreshWorkspaces('c');
    for (const id of choices) f.selectWorkspace(id);
    const guards = f.state.guards; f.held[0].resolve(f.records); await pending;
    assert.equal(f.state.selected, 'a'); assert.equal(f.state.guards, guards);
  }
});

test('a declined newer manual navigation cancels an older requested target without another warning', async () => {
  const f = fixture(), pending = f.refreshWorkspaces('c'); f.state.allowed = false; f.selectWorkspace('b');
  f.state.allowed = true; f.held[0].resolve(f.records); await pending;
  assert.equal(f.state.selected, 'a'); assert.equal(f.state.guards, 1);
});

test('an expired or failed caller lease refreshes the list without selecting or warning for its old target', async () => {
  for (const lease of [() => false, () => { throw new Error('Source is gone'); }]) {
    const f = fixture(), pending = f.refreshWorkspaces('c', lease); f.held[0].resolve(f.records); await pending;
    assert.equal(f.state.selected, 'a'); assert.equal(f.state.guards, 0); assert.equal(f.state.error, ''); assert.equal(f.state.workspaces.length, 3);
  }
});

test('a valid requested selection asks once, while decline leaves the current workspace selected', async () => {
  for (const accepted of [true, false]) {
    const f = fixture(); f.state.allowed = accepted; const pending = f.refreshWorkspaces('c', () => true);
    f.held[0].resolve(f.records); await pending;
    assert.equal(f.state.selected, accepted ? 'c' : 'a'); assert.equal(f.state.guards, 1);
  }
});

test('a lease expiring inside its dirty guard cannot authorize the requested selection', async () => {
  const f = fixture(); let live = true;
  f.context.mayLeaveConversationWork = () => { f.state.guards++; live = false; return true; };
  const pending = f.refreshWorkspaces('c', () => live); f.held[0].resolve(f.records); await pending;
  assert.equal(f.state.selected, 'a'); assert.equal(f.state.guards, 1);
});

test('canceled explicit selection still accepts authoritative membership removal and safe fallback', async () => {
  const f = fixture(), pending = f.refreshWorkspaces('c', () => false); f.held[0].resolve([{ id: 'b' }, { id: 'c' }]); await pending;
  assert.equal(f.state.selected, 'b'); assert.equal(f.state.guards, 0); assert.deepEqual(Array.from(f.state.workspaces, row => row.id), ['b', 'c']);
});

test('account or token changes, including an auth ABA event, cannot settle an older selection', async () => {
  for (const change of ['actor', 'token', 'auth-aba']) {
    const f = fixture(), pending = f.refreshWorkspaces('c');
    if (change === 'actor') f.state.actor = 'other';
    if (change === 'token') f.state.token = 'other-token';
    if (change === 'auth-aba') { f.state.token = 'temporary'; f.sync(); f.state.token = 'token'; f.sync(); }
    f.held[0].resolve(f.records); await pending;
    assert.notEqual(f.state.selected, 'c');
  }
});
