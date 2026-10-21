import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

function compile(path, dependencies) {
  const source = readFileSync(new URL(path, import.meta.url), 'utf8');
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const exports = {};
  vm.runInNewContext(output, { exports, Date, Promise, window: dependencies.window, require: name => {
    assert.ok(name in dependencies, 'Known component dependency: ' + name); return dependencies[name];
  } });
  return exports;
}
const connection = { id: 'connection', channel_id: 'local-channel', workspace_id: 'ours', peer_origin: 'https://partner.example.com', peer_workspace_id: 'theirs', name: 'Shared', status: 'active', role: 'host', transport: 'federated-matrix', secure: true, room_ready: true };
function fixture({ enabled = true, configuration, list = [connection] } = {}) {
  const state = { token: 'session', user: 'admin', calls: [], guards: [] }, slots = [], effects = [], listeners = new Map();
  let cursor = 0, tree;
  const react = {
    useState(initial) { const id = cursor++; if (!(id in slots)) slots[id] = typeof initial === 'function' ? initial() : initial; return [slots[id], value => { slots[id] = typeof value === 'function' ? value(slots[id]) : value; }]; },
    useRef(initial) { const id = cursor++; return slots[id] ||= { current: initial }; },
    useEffect(callback, deps) { const id = cursor++, old = slots[id]; if (!old || deps.some((value, index) => value !== old.deps[index])) { slots[id] = { deps, cleanup: old?.cleanup }; effects.push(() => { slots[id].cleanup?.(); slots[id].cleanup = callback(); }); } },
  };
  const jsx = (type, props) => ({ type, props: props || {} });
  const track = (method, value) => async (...args) => { state.calls.push([method, ...args]); return typeof value === 'function' ? value(...args) : value; };
  const federationApi = {
    configuration: track('configuration', configuration || { enabled, origin: 'https://ours.example.com', public_key: 'public-key', matrix_server_name: 'matrix.ours.example.com', trusted_origins: ['https://partner.example.com'] }),
    list: track('list', list), members: track('members', []), offer: track('offer', { ...connection, status: 'pending', invitation_token: 'private-once' }),
    accept: track('accept', connection), grant: track('grant', {}), remove: track('remove', {}), sync: track('sync', connection), revoke: track('revoke', { ...connection, status: 'revoked', remote_cleanup_pending: false }),
  };
  const { FederatedConnectPanel } = compile('../src/components/FederatedConnectPanel.tsx', {
    react, 'react/jsx-runtime': { jsx, jsxs: jsx }, 'lucide-react': new Proxy({}, { get: (_, value) => String(value) }),
    '../i18n': { t: value => value, useI18n() {} }, '../lib/api': { api: { getAccessToken: () => state.token, getCurrentUserId: () => state.user } },
    '../lib/connect-federation-api': { federationApi }, '../lib/workspace-api': { workspaceApi: { members: track('people', [{ user_id: 'colleague', name: 'Colleague' }]) } },
    window: { addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: name => listeners.delete(name) },
  });
  function render() { cursor = 0; tree = FederatedConnectPanel({ workspaceId: 'ours', onGuardChange: guard => state.guards.push(guard) }); while (effects.length) effects.shift()(); }
  async function flush() { for (let n = 0; n < 12; n++) { await Promise.resolve(); render(); } }
  function nodes() { const rows = []; const visit = item => { if (!item || typeof item !== 'object') return; if (Array.isArray(item)) return item.forEach(visit); rows.push(item); visit(item.props?.children); }; visit(tree); return rows; }
  const text = item => typeof item === 'string' ? item : Array.isArray(item) ? item.map(text).join('') : text(item?.props?.children || '');
  function find(predicate) { const row = nodes().find(predicate); assert.ok(row, 'Control exists'); return row; }
  function field(id, value) { find(row => row.props.id === id).props.onChange({ target: { value } }); render(); }
  function submit(name) { find(row => row.type === 'form' && text(row).includes(name)).props.onSubmit({ preventDefault() {} }); }
  function click(name) { find(row => row.type === 'button' && text(row) === name).props.onClick(); }
  render();
  return { state, render, flush, nodes, text: () => text(tree), find, field, submit, click, authChanged: () => listeners.get('freecollab:auth-changed')?.() };
}

test('cross-installation API encodes each local workspace/connection/person boundary and sends only explicit peer metadata', async () => {
  const calls = [];
  const { federationApi } = compile('../src/lib/connect-federation-api.ts', { './api': { api: { request: async (...args) => { calls.push(args); return {}; } } } });
  await federationApi.offer('ours /?', 'Project', 'https://peer.example.com', 'target');
  await federationApi.accept('ours /?', 'https://peer.example.com', 'private-token');
  await federationApi.remove('ours /?', 'connection /?', 'person /?');
  assert.equal(calls[0][0], '/connect/federation/workspaces/ours%20%2F%3F/connections');
  assert.deepEqual(JSON.parse(calls[0][1].body), { name: 'Project', peer_origin: 'https://peer.example.com', peer_workspace_id: 'target' });
  assert.deepEqual(JSON.parse(calls[1][1].body), { peer_origin: 'https://peer.example.com', invitation_token: 'private-token' });
  assert.equal(calls[2][0], '/connect/federation/workspaces/ours%20%2F%3F/connections/connection%20%2F%3F/members/person%20%2F%3F');
});

test('disabled federation exposes operator setup without an actionable invitation form', async () => {
  const f = fixture({ enabled: false, list: [] }); await f.flush();
  assert.match(f.text(), /Operator setup required/);
  assert.equal(f.nodes().some(row => row.type === 'form'), false);
  assert.equal(f.state.calls.some(row => row[0] === 'offer'), false);
});

test('federated invitation remains only in current view and holds navigation guard until explicit dismissal', async () => {
  const f = fixture(); await f.flush();
  f.field('federation-peer', 'https://partner.example.com'); f.field('federation-name', 'Project'); f.field('federation-workspace', 'theirs');
  f.submit('Invite another installation'); await f.flush();
  assert.equal(f.find(row => row.props.id === 'federation-issued').props.value, 'private-once');
  assert.equal(f.state.guards.at(-1), 'secret');
  assert.equal(f.state.calls.filter(row => row[0] === 'grant').length, 0);
  f.click('I have stored the invitation'); await f.flush();
  assert.equal(f.nodes().some(row => row.props.id === 'federation-issued'), false);
  assert.equal(f.state.guards.at(-1), null);
});

test('bilateral installation acceptance does not silently enroll the accepting administrator', async () => {
  const f = fixture(); await f.flush();
  f.field('federation-accept-peer', 'https://partner.example.com'); f.field('federation-token', ' secret-token '); f.submit('Accept an invitation'); await f.flush();
  assert.deepEqual(f.state.calls.find(row => row[0] === 'accept').slice(1), ['ours', 'https://partner.example.com', 'secret-token']);
  assert.equal(f.state.calls.some(row => row[0] === 'grant'), false);
  assert.equal(f.find(row => row.props.id === 'federation-token').props.value, '');
});

test('logout clears invitation/key metadata and prevents late configuration response from repopulating it', async () => {
  let release;
  const f = fixture({ configuration: () => new Promise(resolve => { release = resolve; }) });
  f.state.token = null; f.authChanged(); release({ enabled: true, public_key: 'old-public-key', trusted_origins: ['https://old.example.com'] }); await f.flush();
  assert.equal(f.text().includes('old-public-key'), false);
  assert.equal(f.text().includes('https://old.example.com'), false);
  assert.equal(f.state.guards.at(-1), null);
});

test('ending independent shared access requires the visible review before calling revoke', async () => {
  const f = fixture(); await f.flush();
  f.click('Revoke connection'); await f.flush();
  assert.equal(f.state.calls.some(row => row[0] === 'revoke'), false);
  assert.match(f.text(), /Previously downloaded content cannot be recalled/);
  f.click('End shared access'); await f.flush();
  assert.equal(f.state.calls.filter(row => row[0] === 'revoke').length, 1);
  assert.match(f.text(), /Shared channel access has ended/);
});

test('revoked connection keeps a safe retry action for unavailable peer cleanup', async () => {
  const f = fixture({ list: [{ ...connection, status: 'revoked' }] }); await f.flush();
  f.click('Retry remote cleanup'); await f.flush();
  assert.equal(f.state.calls.filter(row => row[0] === 'revoke').length, 1);
  assert.match(f.text(), /Remote cleanup completed/);
  assert.equal(f.state.calls.some(row => row[0] === 'grant'), false);
});
