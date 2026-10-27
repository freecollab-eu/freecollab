import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const compile = source => ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const secureSource = compile(readFileSync(new URL('../src/lib/secure/index.ts', import.meta.url), 'utf8'));
const filesSource = compile(readFileSync(new URL('../src/lib/files-api.ts', import.meta.url), 'utf8'));
const apiOriginal = readFileSync(new URL('../src/lib/api.ts', import.meta.url), 'utf8');
const apiConstant = "const API_URL = import.meta.env.VITE_API_URL || '/api';";
assert.ok(apiOriginal.includes(apiConstant));
const apiSource = compile(apiOriginal.replace(apiConstant, "const API_URL = 'https://fixture.test/api';"));
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const tick = async () => { for (let index = 0; index < 20; index++) await Promise.resolve(); };
const binding = { channel_id: 'private', room_id: '!private:fixture.test', status: 'active' };

/** Actual classification, API routing and file routing with bounded local transport spies. */
function fixture({ rows = [], channel = { id: 'private', secure: false }, status = 200, networkError = false } = {}) {
  const secureExports = {}, apiExports = {}, fileExports = {}, listeners = new Map(), reads = [], native = [], encrypted = [], uploads = [];
  const storage = new Map([['freecollab_token', 'token-a'], ['freecollab_user_id', 'actor-a']]);
  const state = { hook: async () => {}, beforeBody: null, boot: deferred(), bootStarted: 0, formDataCreated: 0 };
  class CountedFormData extends FormData { constructor() { super(); state.formDataCreated++; } }
  const window = {
    addEventListener(name, callback) { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(callback); },
    removeEventListener(name, callback) { listeners.get(name)?.delete(callback); },
    dispatchEvent(event) { for (const callback of [...listeners.get(event.type) || []]) callback(event); return true; },
  };
  const response = (value, responseStatus = 200, path = '') => ({ ok: responseStatus >= 200 && responseStatus < 300, status: responseStatus,
    json: async () => { await state.hook(path, 'body'); state.beforeBody?.(path); return value; } });
  const fetch = async (url, options = {}) => {
    const path = new URL(url, 'https://fixture.test').pathname.replace(/^\/api/, '');
    reads.push({ path, token: new Headers(options.headers).get('Authorization') });
    await state.hook(path, 'headers');
    if (path === '/secure/channels') return response(rows, 200, path);
    if (path === '/secure/session') { state.bootStarted++; return state.boot.promise; }
    if (path === '/channels/private') {
      if (networkError) throw new TypeError('Fixture channel metadata connection failed.');
      return response(channel, status, path);
    }
    native.push({ path, body: options.body });
    if (path.startsWith('/files/channels/') && options.method === 'POST') return response({ id: 'native-legacy-file' });
    return response(path.startsWith('/messages') && options.method === 'POST'
      ? { id: 'native-message', channel_id: 'private', content: JSON.parse(options.body).content } : []);
  };
  const dependencies = new Set(['matrix-js-sdk', 'matrix-js-sdk/lib/crypto-api/CryptoEvent', 'matrix-js-sdk/lib/crypto-api/recovery-key',
    './device-store', './transaction-retry', './publish-guard', '../channel-metadata', './message-content']);
  class APIError extends Error { constructor(message, errorStatus) { super(message); this.status = errorStatus; } }
  vm.runInNewContext(secureSource, { exports: secureExports, window, Event, clearTimeout, fetch,
    require: name => { if (name === '../api') return { APIError }; assert.ok(dependencies.has(name), name); return {}; } });
  const adapter = { ...secureExports.secureMessaging,
    sendMessage: async (channelId, content) => { encrypted.push('send'); return { id: '$encrypted', channel_id: channelId, content }; },
    getMessages: async () => { encrypted.push('messages'); return []; },
    getReadReceipts: async () => { encrypted.push('receipts'); return []; },
    markRead: async () => { encrypted.push('mark-read'); },
  };
  vm.runInNewContext(apiSource, { exports: apiExports, window, Event, Headers, FormData: CountedFormData, Blob, File, URLSearchParams, fetch,
    document: { getElementById: () => null },
    localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) },
    require: name => { assert.equal(name, './secure-client'); return { getSecureMessaging: async () => adapter }; } });
  const api = apiExports.api;
  secureExports.configureSecureMessaging({ baseUrl: 'https://fixture.test/api', getAccessToken: () => api.getAccessToken(), getUserId: () => api.getCurrentUserId() });
  class XHR {
    upload = {}; headers = {}; response = null; status = 0;
    constructor() { uploads.push(this); }
    open(method, url) { this.method = method; this.url = url; }
    setRequestHeader(name, value) { this.headers[name] = value; }
    send(body) { this.body = body; this.status = 200; this.response = { id: 'native-file' }; this.onload(); }
  }
  vm.runInNewContext(filesSource, { exports: fileExports, window, Event, DOMException, FormData: CountedFormData, File, Blob, Headers, XMLHttpRequest: XHR, fetch,
    require: name => name === './api' ? { api } : { getSecureMessaging: async () => adapter } });
  fileExports.registerSecureFileProvider({ handles: adapter.handles, upload: async () => { encrypted.push('file'); return { id: '$file', encrypted: true }; } });
  const setAccount = (user = 'actor-b', token = 'token-b') => api.applySession({ user_id: user, access_token: token, token_type: 'bearer', public_key: '', encrypted_private_key: null });
  return { secure: secureExports.secureMessaging, api, files: fileExports, reads, native, encrypted, uploads, state, setAccount,
    listenerCount: () => [...listeners.values()].reduce((count, set) => count + set.size, 0) };
}

const operations = [
  { name: 'send', start: f => f.api.sendMessage('private', 'private regression draft') },
  { name: 'messages', start: f => f.api.getMessages('private') },
  { name: 'receipts', start: f => f.api.getReadReceipts('private') },
  { name: 'mark-read', start: f => f.api.markAsRead('private', '$encrypted') },
  { name: 'file', start: f => f.files.uploadChannelFile('private', new File(['private regression file'], 'private.txt', { type: 'text/plain' })) },
];

for (const operation of operations) test(`${operation.name}: absent binding plus secure metadata never dispatches a native request`, async () => {
  const f = fixture({ channel: { id: 'private', secure: true } });
  await operation.start(f);
  assert.deepEqual(f.encrypted, [operation.name]); assert.deepEqual(f.native, []); assert.equal(f.uploads.length, 0);
  assert.ok(f.reads.some(row => row.path === '/channels/private'));
  assert.equal(f.listenerCount(), 0);
});

for (const operation of operations) test(`${operation.name}: revoked membership rejects before any plaintext transport`, async () => {
  const f = fixture({ status: 403, channel: { detail: 'Current membership required.' } });
  await assert.rejects(operation.start(f), error => error.status === 403);
  assert.deepEqual(f.native, []); assert.deepEqual(f.encrypted, []); assert.equal(f.uploads.length, 0);
  assert.equal(f.listenerCount(), 0);
});

test('explicit authorized native metadata preserves normal message and file delivery without Matrix startup', async () => {
  const f = fixture();
  assert.equal((await f.api.sendMessage('private', 'authorized native draft')).id, 'native-message');
  assert.equal((await f.files.uploadChannelFile('private', new File(['native'], 'native.txt', { type: 'text/plain' }))).id, 'native-file');
  assert.deepEqual(f.encrypted, []); assert.equal(f.state.bootStarted, 0);
  assert.equal(JSON.parse(f.native[0].body).content, 'authorized native draft'); assert.equal(f.uploads.length, 1);
});

for (const channel of [null, {}, { id: 'other', secure: false }, { id: 'private' }, { id: 'private', secure: null }, { id: 'private', secure: 'false' }]) {
  test(`unverified channel metadata cannot authorize native fallback: ${JSON.stringify(channel)}`, async () => {
    const f = fixture({ channel });
    await assert.rejects(f.api.sendMessage('private', 'private regression draft'), /encryption could not be verified/);
    assert.deepEqual(f.native, []); assert.deepEqual(f.encrypted, []);
  });
}
for (const status of [404, 503]) test(`channel metadata ${status} never becomes native fallback`, async () => {
  const f = fixture({ status, channel: { detail: 'Fixture channel unavailable.' } });
  await assert.rejects(f.api.sendMessage('private', 'private regression draft'), error => error.status === status);
  assert.deepEqual(f.native, []);
});
test('channel metadata network failure never becomes native fallback', async () => {
  const f = fixture({ networkError: true });
  await assert.rejects(f.api.sendMessage('private', 'private regression draft'), /connection failed/);
  assert.deepEqual(f.native, []);
});

test('a verified secure snapshot survives first-client cache clearing after producer completion', async () => {
  const f = fixture({ rows: [binding] }); let boot;
  f.state.beforeBody = path => {
    if (path === '/secure/channels') queueMicrotask(() => queueMicrotask(() => queueMicrotask(() => queueMicrotask(() => {
      boot = f.secure.ensureSession().catch(() => undefined);
    }))));
  };
  assert.equal(await f.secure.handles('private'), true);
  assert.equal(f.state.bootStarted, 1);
  assert.ok(!f.reads.some(row => row.path === '/channels/private'), 'Positive verified rows need no native metadata query');
  f.secure.stop(); f.state.boot.resolve({ ok: true, json: async () => ({ user_id: '@fc_actor-a:fixture.test' }) }); await boot;
});

for (const boundary of ['/secure/channels', '/channels/private']) test(`an actor ABA during ${boundary} cannot revive a classification`, { timeout: 2000 }, async () => {
  const f = fixture(), gate = deferred(), entered = deferred();
  f.state.hook = async (path, phase) => { if (path === boundary && phase === 'body') { entered.resolve(); await gate.promise; } };
  const pending = f.api.sendMessage('private', 'private regression draft'), rejected = assert.rejects(pending, /account changed/);
  await entered.promise;
  f.setAccount(); f.setAccount('actor-a', 'token-a'); gate.resolve(); await rejected;
  assert.deepEqual(f.native, []); assert.deepEqual(f.encrypted, []); assert.equal(f.listenerCount(), 0);
});

test('a changed actor with the same token cannot use the previous classification', { timeout: 2000 }, async () => {
  const f = fixture(), gate = deferred(), entered = deferred();
  f.state.hook = async (path, phase) => { if (path === '/channels/private' && phase === 'body') { entered.resolve(); await gate.promise; } };
  const pending = f.secure.handles('private'), rejected = assert.rejects(pending, /account changed/);
  await entered.promise; f.setAccount('actor-b', 'token-a'); gate.resolve(); await rejected;
  assert.equal(f.listenerCount(), 0);
});

for (const rows of [[binding], []]) test(`legacy upload rejects an encrypted channel before constructing multipart bytes (binding=${rows.length > 0})`, async () => {
  const f = fixture({ rows, channel: { id: 'private', secure: true } });
  await assert.rejects(f.api.uploadFile('private', new File(['private legacy bytes'], 'private.txt', { type: 'text/plain' })), /encrypted file transfer/);
  assert.equal(f.state.formDataCreated, 0); assert.deepEqual(f.native, []); assert.deepEqual(f.encrypted, []);
});

test('legacy upload rejects revoked membership before constructing multipart bytes', async () => {
  const f = fixture({ status: 403, channel: { detail: 'Current membership required.' } });
  await assert.rejects(f.api.uploadFile('private', new File(['private legacy bytes'], 'private.txt', { type: 'text/plain' })), error => error.status === 403);
  assert.equal(f.state.formDataCreated, 0); assert.deepEqual(f.native, []);
});

test('legacy upload preserves authorized native payload, header and response without Matrix startup', async () => {
  const f = fixture(), file = new File(['authorized native bytes'], 'native.txt', { type: 'text/plain' });
  assert.equal((await f.api.uploadFile('private', file)).id, 'native-legacy-file');
  assert.equal(f.state.bootStarted, 0); assert.equal(f.state.formDataCreated, 1); assert.equal(f.native.length, 1);
  assert.equal(f.native[0].path, '/files/channels/private');
  const uploaded = f.native[0].body.get('file');
  assert.equal(uploaded.name, file.name); assert.equal(await uploaded.text(), 'authorized native bytes');
  assert.equal(f.reads.find(row => row.path === '/files/channels/private').token, 'Bearer token-a');
});

for (const account of [['actor-b', 'token-a'], ['actor-a', 'token-b']]) test(`legacy upload owns the awaited routing result across ${account[0] === 'actor-a' ? 'token' : 'actor'} replacement`, async () => {
  const f = fixture(), route = deferred();
  // Isolate the legacy method's own fence from the classifier's separate lease.
  f.api.secureChannel = () => route.promise;
  const pending = f.api.uploadFile('private', new File(['old account bytes'], 'private.txt', { type: 'text/plain' }));
  const rejected = assert.rejects(pending, /account changed/);
  f.setAccount(...account); route.resolve(null); await rejected;
  assert.equal(f.state.formDataCreated, 0); assert.deepEqual(f.native, []);
});

test('legacy upload propagates unavailable classification instead of sending bytes', async () => {
  const f = fixture({ networkError: true });
  await assert.rejects(f.api.uploadFile('private', new File(['private'], 'private.txt', { type: 'text/plain' })), /connection failed/);
  assert.equal(f.state.formDataCreated, 0); assert.deepEqual(f.native, []);
});
