import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const compile = source => ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const referenceModel = {};
vm.runInNewContext(compile(readFileSync(new URL('../src/lib/message-references.ts', import.meta.url), 'utf8')), { exports: referenceModel });
const plain = value => JSON.parse(JSON.stringify(value));
const text = 'Hej @Ada', refs = [{ kind: 'person', id: 'ada', start: 4, end: 8 }];

/** The vault boundary is isolated here; real AES-GCM/IndexedDB is exercised by browser proofs. */
function fixture({ online = true, memory = false, secure = false } = {}) {
  const exports = {}, records = new Map(), calls = [], encrypted = [], events = [];
  const state = { user: 'owner', token: 'token-owner', online, memory, secure, holdSave: null, request: null };
  const currentOwner = () => ({ user: state.user, token: state.token });
  const assertOwner = expected => { if (expected.user !== state.user || expected.token !== state.token) throw new Error('The signed-in account changed.'); };
  const key = (kind, id, expected) => expected.user + ':' + kind + ':' + id;
  const storage = {
    owner: currentOwner, assertOwner, fingerprint: async value => value,
    async listPrivate(kind, expected = currentOwner()) { assertOwner(expected); return [...records.values()].filter(row => row.user === expected.user && row.kind === kind).map(row => ({ id: row.id, value: structuredClone(row.value) })); },
    async savePrivate(kind, id, value, expected = currentOwner()) { if (state.holdSave) await state.holdSave; assertOwner(expected); records.set(key(kind, id, expected), { user: expected.user, kind, id, value: structuredClone(value) }); },
    async removePrivate(kind, id, expected = currentOwner()) { assertOwner(expected); records.delete(key(kind, id, expected)); },
    purgeOtherAccounts: async () => {},
  };
  class APIError extends Error {}
  const api = {
    getCurrentUserId: () => state.user, getAccessToken: () => state.token, isMemorySession: () => state.memory,
    rememberMessage: value => value,
    async request(path, options = {}) {
      calls.push({ path, options: plain(options) });
      if (state.request) return state.request(path, options);
      if (path === '/auth/me') return { id: state.user };
      if (path.endsWith('/members')) return [{ user_id: state.user }];
      return { id: 'native-receipt', content: JSON.parse(options.body).content, references: JSON.parse(options.body).references };
    },
  };
  const secureClient = { getSecureMessaging: async () => ({ handles: async () => state.secure,
    sendMessage: async (...args) => { encrypted.push(plain(args)); return { id: '$encrypted-receipt', channel_id: args[0], content: args[1], references: args[4] }; },
  }) };
  const source = readFileSync(new URL('../src/mobile/index.ts', import.meta.url), 'utf8');
  assert.ok(source.includes('import.meta.env.PROD'));
  vm.runInNewContext(compile(source.replace('import.meta.env.PROD', 'false')), {
    exports, crypto, structuredClone, Event, CustomEvent, DOMException, AbortSignal, TypeError,
    navigator: { get onLine() { return state.online; } },
    window: { dispatchEvent: event => { events.push(event.type); return true; } },
    require: name => {
      if (name === '../lib/api') return { api, APIError };
      if (name === './storage') return storage;
      if (name === '../lib/message-references') return referenceModel;
      if (name === '../lib/secure-client') return secureClient;
      if (name === './library') return {};
      throw new Error('Unexpected dependency ' + name);
    },
  });
  return { ...exports, records, state, calls, encrypted, events };
}

test('rich drafts snapshot selected identities and legacy text saves retain only untouched bindings', async () => {
  const f = fixture(), source = plain(refs);
  await f.mobileDrafts.saveRich('room', { text, references: source }, true);
  source[0].id = 'substituted';
  assert.deepEqual(plain(await f.mobileDrafts.loadRich('room')), { text, references: refs });
  await f.mobileDrafts.save('room', '🙂 ' + text);
  assert.deepEqual(plain((await f.mobileDrafts.loadRich('room')).references), [{ ...refs[0], start: 7, end: 11 }]);
  await f.mobileDrafts.save('room', '🙂 Hej @Other');
  assert.deepEqual(plain((await f.mobileDrafts.loadRich('room')).references), []);
});

test('queued draft writes and clear are serialized before subsequent reads', async () => {
  const f = fixture(); let release;
  f.state.holdSave = new Promise(resolve => { release = resolve; });
  const first = f.mobileDrafts.saveRich('room', { text, references: refs });
  const second = f.mobileDrafts.save('room', 'Before ' + text);
  await new Promise(resolve => setImmediate(resolve));
  release(); f.state.holdSave = null;
  await Promise.all([first, second]);
  assert.equal(await f.mobileDrafts.load('room'), 'Before ' + text);
  assert.deepEqual(plain((await f.mobileDrafts.loadRich('room')).references), [{ ...refs[0], start: 11, end: 15 }]);
  await f.mobileDrafts.clear('room');
  assert.deepEqual(plain(await f.mobileDrafts.loadRich('room')), { text: '', references: [] });
});

test('old stored text migrates to empty references and account changes cannot finish an old rich write', async () => {
  const f = fixture();
  f.records.set('owner:draft:old', { user: 'owner', kind: 'draft', id: 'old', value: { content: 'Older plain draft' } });
  assert.deepEqual(plain(await f.mobileDrafts.loadRich('old')), { text: 'Older plain draft', references: [] });
  let release; f.state.holdSave = new Promise(resolve => { release = resolve; });
  const pending = f.mobileDrafts.saveRich('room', { text, references: refs });
  const rejected = assert.rejects(pending, /account changed/);
  await new Promise(resolve => setImmediate(resolve));
  f.state.user = 'other'; f.state.token = 'token-other'; release(); f.state.holdSave = null;
  await rejected;
  assert.deepEqual(plain(await f.mobileDrafts.loadRich('room')), { text: '', references: [] });
  assert.equal(f.records.has('other:draft:room'), false);
});

test('native offline retries retain the exact selected IDs and transaction identity after network failure', async () => {
  const f = fixture({ online: false });
  const queued = await f.mobileOutbox.sendOrQueue('room', text, [], false, refs);
  const stored = (await f.mobileOutbox.list())[0];
  assert.deepEqual(plain(stored.references), refs); assert.equal(stored.id, queued.id);
  f.state.online = true; let lost = true;
  f.state.request = async (path, options) => {
    if (path === '/auth/me') return { id: 'owner' };
    if (path.endsWith('/members')) return [{ user_id: 'owner' }];
    if (lost) { lost = false; throw new TypeError('network failed'); }
    return { id: 'durable-receipt', content: text, references: refs };
  };
  await f.mobileOutbox.flush(); assert.equal((await f.mobileOutbox.list()).length, 1);
  await f.mobileOutbox.flush(); assert.equal((await f.mobileOutbox.list()).length, 0);
  const attempts = f.calls.filter(row => row.path.includes('/mobile/offline/')).map(row => JSON.parse(row.options.body));
  assert.equal(attempts.length, 2); assert.equal(attempts[0].client_id, attempts[1].client_id);
  assert.deepEqual(attempts[0].references, refs); assert.deepEqual(attempts[1].references, refs);
});

test('encrypted queue delivery passes references only to the trusted encrypted message adapter', async () => {
  const f = fixture({ secure: true });
  const result = await f.mobileOutbox.sendOrQueue('room', text, [], true, refs);
  assert.equal(result.message.id, '$encrypted-receipt');
  assert.equal(f.calls.some(row => row.path.includes('/mobile/offline/')), false);
  assert.equal(f.encrypted.length, 1); assert.deepEqual(f.encrypted[0].slice(0, 5), ['room', text, null, [], refs]);
  assert.match(f.encrypted[0][5], /^mobile-/);
});

test('token replacement fences queued content and encrypted scheduled references wait until due', async () => {
  const f = fixture({ online: false });
  await f.mobileOutbox.sendOrQueue('room', text, [], false, refs);
  f.state.token = 'replacement-token'; f.state.online = true;
  await f.mobileOutbox.flush();
  assert.equal((await f.mobileOutbox.list())[0].state, 'review');
  assert.equal(f.calls.length, 0);
  const encrypted = fixture({ secure: true });
  await encrypted.mobileOutbox.schedule('room', text, [], new Date(Date.now() + 3600000).toISOString(), refs);
  assert.deepEqual(plain((await encrypted.mobileOutbox.list())[0].references), refs);
  await encrypted.mobileOutbox.flush(); assert.equal(encrypted.encrypted.length, 0);
});

test('memory-only embedded sessions reject offline queueing rather than persisting standalone data', async () => {
  const f = fixture({ memory: true, online: false });
  await assert.rejects(f.mobileOutbox.sendOrQueue('room', text, [], false, refs), /needs a connection/);
  await assert.rejects(f.mobileOutbox.schedule('room', text, [], new Date(Date.now() + 3600000).toISOString(), refs), /standalone app/);
  assert.equal(f.records.size, 0);
});

test('API native and trusted-adapter paths preserve typed references and explicit edit clearing', async () => {
  const source = readFileSync(new URL('../src/lib/api.ts', import.meta.url), 'utf8').replace("const API_URL = import.meta.env.VITE_API_URL || '/api';", "const API_URL = '/api';");
  const module = {};
  vm.runInNewContext(compile(source), { exports: module, localStorage: { getItem: () => null } });
  const calls = []; module.api.secureChannel = async () => null; module.api.secureMessage = async () => null;
  module.api.request = async (path, options) => { calls.push({ path, body: JSON.parse(options.body) }); return {}; };
  await module.api.sendMessage('room', text, [], refs);
  await module.api.replyToMessage('parent', text, [], refs);
  await module.api.editMessage('message', 'Plain replacement', []);
  assert.deepEqual(calls.map(row => row.body.references), [refs, refs, []]);
  assert.deepEqual(calls[2].body.mentions, []);
  const trusted = []; module.api.secureChannel = async () => ({ sendMessage: async (...args) => { trusted.push(args); return { id: '$event' }; } });
  await module.api.sendMessage('room', text, [], refs);
  assert.equal(calls.length, 3); assert.deepEqual(plain(trusted[0]), ['room', text, null, [], refs]);
});

test('API recipient snapshots cannot change while awaiting encryption routing, and a new session cannot send an old draft', async () => {
  const source = readFileSync(new URL('../src/lib/api.ts', import.meta.url), 'utf8').replace("const API_URL = import.meta.env.VITE_API_URL || '/api';", "const API_URL = '/api';");
  const module = {};
  vm.runInNewContext(compile(source), { exports: module, Event, localStorage: { getItem: () => null, setItem() {}, removeItem() {} }, window: { dispatchEvent() {} } });
  let release; const pendingRoute = new Promise(resolve => { release = resolve; });
  const calls = [], selected = plain(refs), mentions = ['ada'];
  module.api.secureChannel = () => pendingRoute;
  module.api.request = async (path, options) => { calls.push(JSON.parse(options.body)); return {}; };
  const send = module.api.sendMessage('room', text, mentions, selected);
  selected[0].id = 'changed'; mentions.push('unreviewed'); release(null); await send;
  assert.deepEqual(calls[0].references, refs); assert.deepEqual(calls[0].mentions, ['ada']);
  let finish; const secondRoute = new Promise(resolve => { finish = resolve; });
  module.api.secureChannel = () => secondRoute;
  const oldSend = module.api.sendMessage('room', text, [], refs), rejected = assert.rejects(oldSend, /account changed/);
  module.api.applySession({ access_token: 'new-session', user_id: 'new-person', token_type: 'bearer', public_key: '', encrypted_private_key: null });
  finish(null); await rejected; assert.equal(calls.length, 1);
});

test('native scheduled-message payload retains the selected reference identities', async () => {
  const source = readFileSync(new URL('../src/lib/chat-productivity.ts', import.meta.url), 'utf8'), module = {}, calls = [];
  vm.runInNewContext(compile(source), { exports: module, crypto, require: name => {
    if (name === './api') return { api: { request: async (path, options) => { calls.push({ path, body: JSON.parse(options.body) }); return {}; } } };
    if (name === './secure-client') return {};
    throw new Error(name);
  } });
  const due = new Date(Date.now() + 3600000).toISOString();
  await module.chatProductivity.schedule('room', text, [], due, refs);
  assert.equal(calls[0].path, '/channels/room/scheduled-messages'); assert.equal(calls[0].body.due_at, due);
  assert.deepEqual(calls[0].body.references, refs);
});
