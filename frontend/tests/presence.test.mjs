import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
function componentFixture(component, initial, load, send = async () => {}) {
  let props = initial, token = 'actor-one', index = 0, pending = true, tree;
  const slots = [], effects = [], listeners = new Map(), socketHandlers = new Map(), sent = [];
  const hooks = {
    useState(value) { const n = index++; if (!(n in slots)) slots[n] = value; return [slots[n], next => { const value = typeof next === 'function' ? next(slots[n]) : next; if (!Object.is(value, slots[n])) { slots[n] = value; pending = true; } }]; },
    useRef(value) { const n = index++; return slots[n] ||= { current: value }; },
    useEffect(callback, deps) { const n = index++; if (!slots[n] || deps.some((value, i) => !Object.is(value, slots[n].deps[i]))) effects.push(() => { slots[n]?.cleanup?.(); slots[n] = { deps, cleanup: callback() }; }); },
  };
  const jsx = (type, props) => ({ type, props: props || {} });
  const api = { getAccessToken: () => token, getCurrentUserId: () => 'self', getUserPresence: load };
  const socket = { on: (name, handler) => socketHandlers.set(name, handler), off: name => socketHandlers.delete(name) };
  const dependencies = { react: hooks, 'react/jsx-runtime': { jsx, jsxs: jsx }, '../lib/api': { api }, '../i18n': { t: value => value, useI18n() {} }, '../lib/socket': { getSocket: () => socket, setPresenceStatus: async (...args) => { sent.push(args); return send(...args); } } };
  const exports = {}, source = readFileSync(new URL('../src/components/PresenceIndicator.tsx', import.meta.url), 'utf8');
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX } }).outputText, { exports, require: name => name.endsWith('.css') ? {} : dependencies[name], Date, window: { addEventListener: (name, handler) => listeners.set(name, handler), removeEventListener: name => listeners.delete(name) } });
  async function settle() { for (let step = 0, idle = 0; step < 40; step++) { if (pending) { idle = 0; pending = false; index = 0; tree = exports[component](props); for (const effect of effects.splice(0)) effect(); } for (let n = 0; n < 10; n++) await Promise.resolve(); if (!pending && !effects.length && ++idle > 2) return; } throw new Error('Presence fixture did not settle'); }
  function nodes() { const found = []; const visit = node => { if (Array.isArray(node)) node.forEach(visit); else if (node && typeof node === 'object') { found.push(node); visit(node.props?.children); } }; visit(tree); return found; }
  const text = node => Array.isArray(node) ? node.map(text).join('') : node && typeof node === 'object' ? text(node.props?.children) : node == null ? '' : String(node);
  return { settle, sent, nodes, text: () => text(tree), setProps(next) { props = { ...props, ...next }; pending = true; }, emit(data) { socketHandlers.get('presence')?.(data); }, changeActor() { token = 'replacement-actor'; listeners.get('freecollab:auth-changed')?.(); }, async type(value) { nodes().find(node => node.type === 'input').props.onChange({ target: { value } }); await settle(); }, async save() { nodes().find(node => node.type === 'button' && text(node).trim() === 'Save Status').props.onClick(); await settle(); } };
}

test('a delayed person lookup never replaces the next person’s current presence', async () => {
  const old = deferred(), f = componentFixture('PresenceIndicator', { userId: 'old', showLabel: true }, id => id === 'old' ? old.promise : Promise.resolve({ user_id: id, status: 'busy', custom_status: 'New person status' }));
  await f.settle(); f.setProps({ userId: 'new' }); await f.settle();
  assert.match(f.text(), /New person status/);
  old.resolve({ user_id: 'old', status: 'online', custom_status: 'Must not reappear' }); await f.settle();
  assert.match(f.text(), /New person status/); assert.ok(!f.text().includes('Must not reappear'));
});

test('live custom status wins over an older REST read; explicit clear and actor change erase it', async () => {
  const old = deferred(), f = componentFixture('PresenceIndicator', { userId: 'peer', showLabel: true }, () => old.promise);
  await f.settle(); f.emit({ user_id: 'peer', status: 'busy', custom_status: 'Current review' }); await f.settle();
  old.resolve({ user_id: 'peer', status: 'online', custom_status: 'Old text' }); await f.settle(); assert.match(f.text(), /Current review/);
  f.emit({ user_id: 'peer', status: 'away', custom_status: null }); await f.settle(); assert.match(f.text(), /Away/); assert.ok(!f.text().includes('Current review'));
  f.changeActor(); f.emit({ user_id: 'peer', status: 'busy', custom_status: 'Previous actor event' }); await f.settle();
  assert.match(f.text(), /Offline/); assert.ok(!f.text().includes('Previous actor event'));
});

test('selector loads the active status, explicitly clears text and waits for acknowledgement', async () => {
  let closed = 0; const ack = deferred(), f = componentFixture('PresenceStatusSelector', { onClose: () => closed++ }, async () => ({ status: 'busy', custom_status: 'Reviewing the brief' }), () => ack.promise);
  await f.settle(); assert.equal(f.nodes().find(node => node.type === 'input').props.value, 'Reviewing the brief');
  await f.type(''); await f.save(); assert.deepEqual(f.sent, [['busy', null]]); assert.equal(closed, 0);
  assert.ok(f.nodes().filter(node => node.type === 'button').every(node => node.props.disabled));
  ack.resolve(); await f.settle(); assert.equal(closed, 1);
});

test('late initial settings cannot erase typed status and a failed acknowledgement keeps the editor open', async () => {
  let closed = 0; const initial = deferred(), f = componentFixture('PresenceStatusSelector', { onClose: () => closed++ }, () => initial.promise, async () => { throw new Error('No acknowledgement'); });
  await f.settle(); await f.type('My new text'); initial.resolve({ status: 'away', custom_status: 'Older text' }); await f.settle();
  assert.equal(f.nodes().find(node => node.type === 'input').props.value, 'My new text');
  await f.save(); assert.deepEqual(f.sent, [['online', 'My new text']]); assert.equal(closed, 0); assert.match(f.text(), /could not be updated/);
});

test('a changed sign-in cannot accept a pending status acknowledgement', async () => {
  let closed = 0; const ack = deferred(), f = componentFixture('PresenceStatusSelector', { onClose: () => closed++ }, async () => ({ status: 'online' }), () => ack.promise);
  await f.settle(); await f.type('My text'); await f.save(); f.changeActor(); ack.resolve(); await f.settle(); assert.equal(closed, 0);
  assert.equal(f.nodes().find(node => node.type === 'input').props.value, '');
});

test('actual socket sender distinguishes omission from clear and rejects failure or actor replacement', async () => {
  let token = 'actor', reply = { ok: true }; const calls = [], handlers = new Map();
  const socket = { connected: true, on: (name, callback) => handlers.set(name, callback), timeout: value => { assert.equal(value, 10000); return { emitWithAck: async (...args) => { calls.push(args); return typeof reply === 'function' ? reply() : reply; } }; } }; socket.volatile = socket;
  const source = readFileSync(new URL('../src/lib/socket.ts', import.meta.url), 'utf8').replace('const API_URL = import.meta.env.VITE_API_URL || undefined;', 'const API_URL = undefined;');
  const exports = {};
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText, { exports, require: name => name === './api' ? { api: { getAccessToken: () => token } } : { io: (_, options) => { options.auth(() => {}); return socket; } }, console: { log() {}, error() {} } });
  await exports.setPresenceStatus('away'); await exports.setPresenceStatus('busy', null);
  assert.deepEqual(JSON.parse(JSON.stringify(calls)), [['set_status', { status: 'away' }], ['set_status', { status: 'busy', custom_status: null }]]);
  reply = { error: 'Rejected' }; await assert.rejects(exports.setPresenceStatus('busy', 'text'), /could not be updated/);
  reply = () => { token = 'new actor'; return { ok: true }; }; await assert.rejects(exports.setPresenceStatus('away', 'text'), /account changed/);
  const before = calls.length;
  await assert.rejects(exports.setPresenceStatus('busy', 'previous actor text'), /could not be updated/);
  socket.connected = false; await assert.rejects(exports.setPresenceStatus('away', 'offline text'), /could not be updated/);
  assert.equal(calls.length, before, 'Disconnected or old-actor sockets must not buffer new profile text');
});

test('actual realtime transport uses the API origin rather than the REST path as a namespace', () => {
  for (const [apiUrl, expected] of [[undefined, undefined], ['/api', 'https://studio.example'], ['https://api.example/api', 'https://api.example'], ['https://api.example', 'https://api.example']]) {
    let origin; const exports = {};
    const source = readFileSync(new URL('../src/lib/socket.ts', import.meta.url), 'utf8').replace('const API_URL = import.meta.env.VITE_API_URL || undefined;', 'const API_URL = ' + JSON.stringify(apiUrl) + ';');
    vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText, { exports, URL, window: { location: { href: 'https://studio.example/app' } }, require: name => name === './api' ? { api: {} } : { io: value => { origin = value; return { on() {} }; } } });
    exports.getSocket(); assert.equal(origin, expected);
  }
});
