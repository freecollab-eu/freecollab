import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const connection = {
  id: 'connection-a', channel_id: 'shared-a', name: 'Partner launch',
  source_workspace_id: 'our-workspace', target_workspace_id: 'partner-workspace',
  source_workspace_name: 'Our team', target_workspace_name: 'Partner team',
  status: 'active', transport: 'local', secure: true, secure_sync_pending: false,
};

function compile(path, dependencies) {
  const source = readFileSync(new URL(path, import.meta.url), 'utf8');
  const output = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText;
  const exports = {};
  vm.runInNewContext(output, { exports, window: dependencies.window, Date, Promise, require: name => {
    if (!(name in dependencies)) throw new Error('Unexpected dependency: ' + name);
    return dependencies[name];
  } });
  return exports;
}

test('Connect client encodes every organization, connection and person boundary', async () => {
  const calls = [];
  const { connectApi } = compile('../src/lib/connect-api.ts', { './api': { api: { request: async (...args) => { calls.push(args); return {}; } } } });
  await connectApi.list('org /?');
  await connectApi.create('org /?', 'Shared work', 'partner &?');
  await connectApi.accept('org /?', 'one-time-private-token');
  await connectApi.members('org /?', 'connection /?');
  await connectApi.grant('org /?', 'connection /?', 'local-person');
  await connectApi.remove('org /?', 'connection /?', 'person /?');
  await connectApi.revoke('org /?', 'connection /?');
  assert.equal(calls[0][0], '/connect/workspaces/org%20%2F%3F/connections');
  assert.equal(calls[3][0], '/connect/workspaces/org%20%2F%3F/connections/connection%20%2F%3F/members');
  assert.equal(calls[5][0], '/connect/workspaces/org%20%2F%3F/connections/connection%20%2F%3F/members/person%20%2F%3F');
  assert.deepEqual(JSON.parse(calls[1][1].body), { name: 'Shared work', target_workspace_id: 'partner &?', expires_in_hours: 24 });
  assert.deepEqual(JSON.parse(calls[2][1].body), { token: 'one-time-private-token' });
  assert.deepEqual(JSON.parse(calls[4][1].body), { user_id: 'local-person' });
  assert.equal(calls[5][1].method, 'DELETE');
  assert.equal(calls[6][1].method, 'DELETE');
});

function fixture({ encrypted = true, list } = {}) {
  const state = { token: 'native-session', user: 'our-admin', calls: [], connections: [], grants: [], guards: [] };
  const slots = [], effects = [], listeners = new Map();
  let cursor = 0, tree;
  const react = {
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial;
      return [slots[index], value => { slots[index] = typeof value === 'function' ? value(slots[index]) : value; }];
    },
    useRef(initial) { const index = cursor++; return slots[index] ||= { current: initial }; },
    useEffect(callback, deps) {
      const index = cursor++, previous = slots[index];
      if (!previous || deps.some((value, position) => value !== previous.deps[position])) {
        slots[index] = { deps, cleanup: previous?.cleanup };
        effects.push(() => { slots[index].cleanup?.(); slots[index].cleanup = callback(); });
      }
    },
  };
  const jsx = (type, props, key) => ({ type, props: props || {}, key });
  const track = (name, result) => async (...args) => { state.calls.push([name, ...args]); return typeof result === 'function' ? result(...args) : result; };
  const connectApi = {
    list: track('list', list || (() => state.connections)),
    capabilities: track('capabilities', { encryption_configured: encrypted, transport: 'local', cross_installation: false }),
    members: track('members', () => state.grants),
    accept: track('accept', connection),
    create: track('create', { ...connection, status: 'pending', invitation_token: 'private-once-only' }),
    grant: track('grant', (_workspace, _connection, userId) => { state.grants = [{ user_id: userId, name: 'Our colleague', workspace_id: 'our-workspace', active: true }]; }),
    remove: track('remove', () => { state.grants = []; }),
    revoke: track('revoke', {}),
    retry: track('retry', { ...connection, secure_sync_pending: false }),
  };
  const { ConnectPanel } = compile('../src/components/ConnectPanel.tsx', {
    react, 'react/jsx-runtime': { jsx, jsxs: jsx },
    'lucide-react': new Proxy({}, { get: (_target, name) => String(name) }),
    '../i18n': { t: value => value, useI18n: () => {} },
    '../lib/api': { api: { getAccessToken: () => state.token, getCurrentUserId: () => state.user } },
    '../lib/connect-api': { connectApi },
    '../lib/workspace-api': { workspaceApi: { members: track('people', [{ user_id: 'our-colleague', name: 'Our colleague', role: 'member' }]) } },
    '../styles/connect.css': {},
    window: { addEventListener: (event, handler) => listeners.set(event, handler), removeEventListener: event => listeners.delete(event) },
  });
  const render = () => { cursor = 0; tree = ConnectPanel({ workspaceId: 'our-workspace', onGuardChange: value => state.guards.push(value) }); while (effects.length) effects.shift()(); return tree; };
  const flush = async () => { for (let index = 0; index < 12; index++) { await Promise.resolve(); render(); } };
  const nodes = () => {
    const result = [];
    const visit = item => { if (!item || typeof item !== 'object') return; if (Array.isArray(item)) { item.forEach(visit); return; } result.push(item); visit(item.props?.children); };
    visit(tree); return result;
  };
  const text = item => typeof item === 'string' ? item : Array.isArray(item) ? item.map(text).join('') : text(item?.props?.children || '');
  const find = predicate => { const node = nodes().find(predicate); assert.ok(node, 'Requested control exists'); return node; };
  const field = (id, value) => { find(node => node.props.id === id).props.onChange({ target: { value } }); render(); };
  const submit = heading => { find(node => node.type === 'form' && text(node).includes(heading)).props.onSubmit({ preventDefault() {} }); };
  const click = label => { find(node => node.type === 'button' && text(node) === label).props.onClick(); };
  render();
  return { state, render, flush, nodes, text: () => text(tree), field, submit, click, find,
    authChanged: () => listeners.get('freecollab:auth-changed')?.() };
}

test('accepting an organization invitation never automatically grants a participant', async () => {
  const f = fixture(); await f.flush();
  f.field('connect-accept-token', ' partner-token '); f.submit('Accept an invitation'); await f.flush();
  assert.equal(f.state.calls.filter(call => call[0] === 'accept').length, 1);
  assert.equal(f.state.calls.find(call => call[0] === 'accept')[2], 'partner-token');
  assert.equal(f.state.calls.filter(call => call[0] === 'grant').length, 0);
  assert.match(f.text(), /Choose the people from your organization/);
  assert.equal(f.find(node => node.props.id === 'connect-accept-token').props.value, '');
  f.field('connect-member', 'our-colleague'); f.submit('Add someone from your organization'); await f.flush();
  assert.equal(f.state.calls.filter(call => call[0] === 'grant').length, 1);
  assert.deepEqual(f.state.calls.find(call => call[0] === 'grant').slice(1), ['our-workspace', 'connection-a', 'our-colleague']);
});

test('creating an invitation holds its once-only token behind a navigation guard until explicit dismissal', async () => {
  const f = fixture(); await f.flush();
  f.field('connect-channel-name', 'Client delivery'); f.field('connect-target-id', 'partner-workspace'); f.submit('Invite an organization'); await f.flush();
  assert.equal(f.find(node => node.props.id === 'connect-issued-token').props.value, 'private-once-only');
  assert.equal(f.state.guards.at(-1), 'secret');
  assert.equal(f.find(node => node.type === 'button' && node.props.type === 'submit' && node.props.children?.[1] === 'Create invitation').props.disabled, true);
  f.click('I have stored the invitation'); await f.flush();
  assert.equal(f.nodes().some(node => node.props.id === 'connect-issued-token'), false);
  assert.equal(f.state.guards.at(-1), null);
});

test('the UI requires operator encryption setup before creating a shared channel', async () => {
  const f = fixture({ encrypted: false }); await f.flush();
  f.field('connect-channel-name', 'Client delivery'); f.field('connect-target-id', 'partner-workspace'); f.submit('Invite an organization'); await f.flush();
  assert.equal(f.state.calls.some(call => call[0] === 'create'), false);
  assert.match(f.text(), /configure Matrix before creating an invitation/);
});

test('an auth change drops pending connection metadata and clears invitation input', async () => {
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  const f = fixture({ list: () => pending });
  f.field('connect-accept-token', 'secret-before-logout');
  f.state.token = null; f.authChanged(); release([connection]); await f.flush();
  assert.equal(f.text().includes('Partner launch'), false);
  assert.equal(f.find(node => node.props.id === 'connect-accept-token').props.value, '');
  assert.equal(f.state.calls.some(call => call[0] === 'members'), false);
});

test('revoking a connection requires an explicit review before calling the destructive API', async () => {
  const f = fixture({ list: () => [connection] }); await f.flush();
  f.click('Revoke connection'); await f.flush();
  assert.equal(f.state.calls.some(call => call[0] === 'revoke'), false);
  assert.match(f.text(), /Previously downloaded content cannot be recalled/);
  f.click('End shared access'); await f.flush();
  assert.equal(f.state.calls.filter(call => call[0] === 'revoke').length, 1);
  assert.match(f.text(), /Shared channel access has ended for both organizations/);
});

test('pending encrypted membership can be retried without granting another content seat', async () => {
  const f = fixture({ list: () => [{ ...connection, secure_sync_pending: true }] }); await f.flush();
  f.click('Retry encrypted synchronization'); await f.flush();
  assert.equal(f.state.calls.filter(call => call[0] === 'retry').length, 1);
  assert.equal(f.state.calls.filter(call => call[0] === 'grant').length, 0);
  assert.match(f.text(), /Encrypted membership synchronized/);
  assert.equal(f.text().includes('Encrypted membership synchronization is pending'), false);
});
