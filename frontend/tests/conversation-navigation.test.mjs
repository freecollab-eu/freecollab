import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const compile = path => ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;

function fixture() {
  const state = { userId: 'member', token: 'session', records: [{ id: 'studio' }, { id: 'research' }] };
  const api = { getCurrentUserId: () => state.userId, getAccessToken: () => state.token, isMemorySession: () => false };
  const navigation = {};
  vm.runInNewContext(compile('../src/lib/conversation-navigation.ts'), {
    exports: navigation, Map, Set,
    require: name => { assert.equal(name, './api'); return { api }; },
  });
  const owner = () => ({ userId: state.userId, credential: state.token, workspaceId: 'studio' });
  navigation.setConversationNavigationWorkspace('studio');
  return { state, api, navigation, owner };
}

test('every live surface can prevent navigation until its actual pending work is safe', () => {
  const f = fixture(), calls = [];
  let pending = true;
  f.navigation.registerConversationNavigationGuard(f.owner(), () => { calls.push('document'); return !pending; });
  f.navigation.registerConversationNavigationGuard(f.owner(), () => { calls.push('decision'); return false; });
  assert.equal(f.navigation.mayLeaveConversationWork(), false);
  assert.deepEqual(calls, ['document']);
  pending = false;
  assert.equal(f.navigation.mayLeaveConversationWork(), false);
  assert.deepEqual(calls, ['document', 'document', 'decision']);
});

test('closing one retained card checks all of its guards and preserves dirty siblings', () => {
  const f = fixture(), calls = [];
  let pending = true;
  f.navigation.registerConversationNavigationGuard({ ...f.owner(), surfaceId: 'document-a' }, () => { calls.push('a-shell'); return true; });
  f.navigation.registerConversationNavigationGuard({ ...f.owner(), surfaceId: 'task-b' }, () => { calls.push('b-draft'); return false; });
  f.navigation.registerConversationNavigationGuard({ ...f.owner(), surfaceId: 'document-a' }, () => { calls.push('a-sync'); return !pending; });
  assert.equal(f.navigation.mayLeaveConversationWork('document-a'), false);
  assert.deepEqual(calls, ['a-shell', 'a-sync']);
  pending = false;
  assert.equal(f.navigation.mayLeaveConversationWork('document-a'), true);
  assert.deepEqual(calls, ['a-shell', 'a-sync', 'a-shell', 'a-sync']);
  assert.equal(f.navigation.mayLeaveConversationWork(), false);
  assert.deepEqual(calls.slice(-2), ['a-shell', 'b-draft']);
  assert.equal(f.navigation.mayLeaveConversationWork('task-b'), false);
  assert.equal(calls.at(-1), 'b-draft');
});

test('scoped checks permanently prune revoked siblings and never transfer mutated owner identity', () => {
  const f = fixture(), owner = { ...f.owner(), surfaceId: 'document-a' };
  let calls = 0;
  f.navigation.registerConversationNavigationGuard(owner, () => { calls++; return false; });
  owner.surfaceId = 'task-b';
  assert.equal(f.navigation.mayLeaveConversationWork('task-b'), true);
  assert.equal(f.navigation.mayLeaveConversationWork('document-a'), false);
  f.state.token = 'rotated';
  assert.equal(f.navigation.mayLeaveConversationWork('unrelated-card'), true);
  f.state.token = 'session';
  assert.equal(f.navigation.mayLeaveConversationWork(), true);
  assert.equal(calls, 1);
});

test('a scoped close cannot bypass a reentrant global guard or another guard on its own card', () => {
  const f = fixture(), calls = [];
  f.navigation.registerConversationNavigationGuard({ ...f.owner(), surfaceId: 'document-a' }, () => {
    calls.push('a');
    return f.navigation.mayLeaveConversationWork();
  });
  f.navigation.registerConversationNavigationGuard({ ...f.owner(), surfaceId: 'task-b' }, () => { calls.push('b'); return true; });
  assert.equal(f.navigation.mayLeaveConversationWork('document-a'), false);
  assert.deepEqual(calls, ['a']);
});

test('old registration cleanup cannot remove its replacement or an independent surface', () => {
  const f = fixture(), owner = f.owner();
  const old = f.navigation.registerConversationNavigationGuard(owner, () => false);
  const replacement = f.navigation.registerConversationNavigationGuard(owner, () => true);
  const other = f.navigation.registerConversationNavigationGuard(f.owner(), () => false);
  old();
  assert.equal(f.navigation.mayLeaveConversationWork(), false);
  other();
  assert.equal(f.navigation.mayLeaveConversationWork(), true);
  replacement();
  assert.equal(f.navigation.mayLeaveConversationWork(), true);
});

test('sign-out, actor change and credential rotation permanently remove old authority without asking', () => {
  for (const change of [{ userId: null, token: null }, { userId: 'other' }, { token: 'rotated' }]) {
    const f = fixture(); let calls = 0;
    f.navigation.registerConversationNavigationGuard(f.owner(), () => { calls++; return false; });
    Object.assign(f.state, change);
    assert.equal(f.navigation.mayLeaveConversationWork(), true);
    f.state.userId = 'member'; f.state.token = 'session';
    assert.equal(f.navigation.mayLeaveConversationWork(), true);
    assert.equal(calls, 0);
  }
});

test('authoritative workspace changes purge pending callbacks and never resurrect them on return', () => {
  const f = fixture(); let calls = 0;
  f.navigation.registerConversationNavigationGuard(f.owner(), () => { calls++; return false; });
  f.navigation.setConversationNavigationWorkspace('research');
  assert.equal(f.navigation.mayLeaveConversationWork(), true);
  f.navigation.setConversationNavigationWorkspace('studio');
  assert.equal(f.navigation.mayLeaveConversationWork(), true);
  f.navigation.setConversationNavigationWorkspace(null);
  assert.equal(calls, 0);
});

test('incomplete or already stale registrations cannot block an authorized current visit', () => {
  const f = fixture(); let calls = 0;
  for (const changes of [{ userId: null }, { credential: null }, { userId: 'other' }, { credential: 'old' }, { workspaceId: '' }, { workspaceId: 'other' }]) {
    f.navigation.registerConversationNavigationGuard({ ...f.owner(), ...changes }, () => { calls++; return false; });
  }
  assert.equal(f.navigation.mayLeaveConversationWork(), true);
  assert.equal(calls, 0);
});

test('guard failures and reentrant navigation preserve the authorized editor', () => {
  const f = fixture(), owner = f.owner();
  f.navigation.registerConversationNavigationGuard(owner, () => { throw new Error('Pending state unavailable'); });
  assert.equal(f.navigation.mayLeaveConversationWork(), false);
  f.navigation.registerConversationNavigationGuard(owner, () => f.navigation.mayLeaveConversationWork());
  assert.equal(f.navigation.mayLeaveConversationWork(), false);
});

test('a scope revoked while a leave decision is open cannot block clearing that scope', () => {
  const f = fixture();
  f.navigation.registerConversationNavigationGuard(f.owner(), () => { f.state.token = null; return false; });
  assert.equal(f.navigation.mayLeaveConversationWork(), true);
});

test('mutating an owner object cannot transfer a retained callback into another workspace', () => {
  const f = fixture(), owner = f.owner(); let calls = 0;
  f.navigation.registerConversationNavigationGuard(owner, () => { calls++; return false; });
  owner.workspaceId = 'research';
  f.navigation.setConversationNavigationWorkspace('research');
  assert.equal(f.navigation.mayLeaveConversationWork(), true);
  assert.equal(calls, 0);
});

function accountFixture() {
  const f = fixture(), slots = [], effects = [], listeners = new Map(), storage = new Map(), events = [];
  let cursor = 0, tree, dirty = true;
  const same = (a, b) => a && a.length === b.length && b.every((value, index) => value === a[index]);
  const react = {
    createContext: () => ({ Provider: 'AccountContext' }),
    useState(initial) { const id = cursor++; if (!(id in slots)) slots[id] = typeof initial === 'function' ? initial() : initial; return [slots[id], value => { const next = typeof value === 'function' ? value(slots[id]) : value; if (next !== slots[id]) { slots[id] = next; dirty = true; } }]; },
    useRef(initial) { const id = cursor++; return slots[id] ||= { current: initial }; },
    useMemo(callback, deps) { const id = cursor++; if (!same(slots[id]?.deps, deps)) slots[id] = { deps, value: callback() }; return slots[id].value; },
    useCallback(callback, deps) { return this.useMemo(() => callback, deps); },
    useEffect(callback, deps) { const id = cursor++; if (!same(slots[id]?.deps, deps)) { const previous = slots[id]; slots[id] = { deps, cleanup: previous?.cleanup }; effects.push(() => { previous?.cleanup?.(); slots[id].cleanup = callback(); }); } },
  };
  // TypeScript calls named hook exports as functions, without a receiver.
  react.useCallback = (callback, deps) => react.useMemo(() => callback, deps);
  const jsx = (type, props) => ({ type, props });
  const dependencies = {
    react, 'react/jsx-runtime': { jsx, jsxs: jsx, Fragment: 'Fragment' },
    '../i18n': { t: value => value, useI18n() {} }, '../lib/api': { api: f.api },
    '../lib/workspace-api': { workspaceApi: { list: async () => f.state.records } },
    '../lib/conversation-navigation': f.navigation,
    './AccountPanel': { AccountPanel: 'AccountPanel' }, '../components/WorkspaceManager': { WorkspaceManager: 'WorkspaceManager' },
    '../components/DirectMessagePicker': { DirectMessagePicker: 'DirectMessagePicker' }, '../components/SecureMessagingPanel': { SecureMessagingPanel: 'SecureMessagingPanel' },
    '../styles/account.css': {},
  };
  const provider = {};
  vm.runInNewContext(compile('../src/account/AccountProvider.tsx'), {
    exports: provider,
    require: name => { assert.ok(name in dependencies, name); return dependencies[name]; },
    localStorage: { getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) },
    CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
    window: {
      addEventListener(type, fn) { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(fn); },
      removeEventListener: (type, fn) => listeners.get(type)?.delete(fn),
      dispatchEvent(event) { events.push(event); for (const fn of listeners.get(event.type) || []) fn(event); },
    },
  });
  function render() { cursor = 0; dirty = false; tree = provider.AccountProvider({ children: 'conversation' }); while (effects.length) effects.shift()(); }
  async function flush() { for (let i = 0; i < 20; i++) { await Promise.resolve(); if (dirty) render(); } }
  function auth() { for (const fn of listeners.get('freecollab:auth-changed') || []) fn(); }
  function panels(type) {
    let count = 0;
    function visit(node) { if (Array.isArray(node)) return node.forEach(visit); if (!node || typeof node !== 'object') return; if (node.type === type) count++; visit(node.props?.children); }
    visit(tree); return count;
  }
  render();
  return { ...f, flush, auth, storage, events, panels, value: () => tree.props.value };
}

test('actual AccountProvider cancels workspace changes before selection, persistence, events or panel closure', async () => {
  const f = accountFixture(); await f.flush();
  assert.equal(f.value().workspace.id, 'studio');
  f.value().openWorkspaceManager(); await f.flush();
  assert.equal(f.panels('WorkspaceManager'), 1);
  let allowed = false, calls = 0;
  f.navigation.registerConversationNavigationGuard(f.owner(), () => { calls++; return allowed; });
  const before = f.events.length;
  f.value().selectWorkspace('research'); await f.flush();
  assert.equal(f.value().workspace.id, 'studio');
  assert.equal(f.storage.get('freecollab.workspace:member'), 'studio');
  assert.equal(f.events.length, before);
  assert.equal(f.panels('WorkspaceManager'), 1);
  assert.equal(calls, 1);
  allowed = true;
  f.value().selectWorkspace('research'); await f.flush();
  assert.equal(f.value().workspace.id, 'research');
  assert.equal(f.storage.get('freecollab.workspace:member'), 'research');
  assert.equal(f.events.at(-1).detail.workspace_id, 'research');
  assert.equal(f.panels('WorkspaceManager'), 0);
  assert.equal(f.navigation.mayLeaveConversationWork(), true);
  assert.equal(calls, 2);
});

test('actual AccountProvider skips guard for same/invalid selection and membership removal bypasses pending work', async () => {
  const f = accountFixture(); await f.flush(); let calls = 0;
  f.navigation.registerConversationNavigationGuard(f.owner(), () => { calls++; return false; });
  f.value().selectWorkspace('unknown'); f.value().selectWorkspace('studio'); await f.flush();
  assert.equal(calls, 0);
  f.state.records = [{ id: 'research' }];
  await f.value().refreshWorkspaces(); await f.flush();
  assert.equal(f.value().workspace.id, 'research');
  assert.equal(calls, 0);
  assert.equal(f.navigation.mayLeaveConversationWork(), true);
});

test('actual AccountProvider refresh applies memberships but preserves authorized work when a requested new workspace is declined', async () => {
  const f = accountFixture(); await f.flush(); let allowed = false, calls = 0;
  f.navigation.registerConversationNavigationGuard(f.owner(), () => { calls++; return allowed; });
  f.state.records = [{ id: 'studio' }, { id: 'research' }, { id: 'new-workspace' }];
  const before = f.events.length;
  await f.value().refreshWorkspaces('new-workspace'); await f.flush();
  assert.deepEqual(Array.from(f.value().workspaces, row => row.id), ['studio', 'research', 'new-workspace']);
  assert.equal(f.value().workspace.id, 'studio');
  assert.equal(f.storage.get('freecollab.workspace:member'), 'studio');
  assert.equal(f.events.length, before);
  assert.equal(calls, 1);
  allowed = true;
  await f.value().refreshWorkspaces('new-workspace'); await f.flush();
  assert.equal(f.value().workspace.id, 'new-workspace');
  assert.equal(f.events.at(-1).detail.workspace_id, 'new-workspace');
  assert.equal(calls, 2);
});

test('actual AccountProvider cannot finish a requested workspace change after the guard rotates identity', async () => {
  const f = accountFixture(); await f.flush();
  f.navigation.registerConversationNavigationGuard(f.owner(), () => { f.state.token = 'replacement'; f.auth(); return true; });
  f.value().selectWorkspace('research');
  assert.equal(f.storage.get('freecollab.workspace:member'), 'studio');
  await f.flush();
  assert.equal(f.value().workspace.id, 'studio');
});
