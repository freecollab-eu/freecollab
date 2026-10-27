import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const originalSource = readFileSync(new URL('../src/lib/api.ts', import.meta.url), 'utf8');
const apiConstant = "const API_URL = import.meta.env.VITE_API_URL || '/api';";
assert.ok(originalSource.includes(apiConstant), 'Test must replace only the Vite API URL constant');
const source = originalSource.replace(apiConstant, "const API_URL = 'https://freecollab.test/api';");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;

function fixture(authenticated = true, embedded = false) {
  const exports = {};
  const storage = new Map(authenticated ? [
    ['freecollab_token', 'token-a'], ['freecollab_user_id', 'user-a'],
  ] : []);
  const events = [];
  const storageReads = [];
  const requests = [];
  let notifyStarted;
  const started = new Promise(resolve => { notifyStarted = resolve; });
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  const response = (body, status = 200) => new Response(JSON.stringify(body), {
    status, headers: { 'Content-Type': 'application/json' },
  });
  vm.runInNewContext(compiled, {
    exports, Event, FormData, Blob, File,
    require: name => { assert.equal(name, './secure-client'); return { getSecureMessaging: async () => ({ handles: async () => false }) }; },
    document: { getElementById: () => ({ hasAttribute: name => embedded && name === 'data-embed-application' }) },
    localStorage: {
      getItem: key => { storageReads.push(key); return storage.get(key) ?? null; },
      setItem: (key, value) => storage.set(key, value),
      removeItem: key => storage.delete(key),
    },
    window: { dispatchEvent: event => { events.push(event.type); return true; } },
    fetch: (url, options = {}) => {
      requests.push({ url, options });
      notifyStarted();
      if (url.endsWith('/auth/login')) return Promise.resolve(response({
        access_token: 'token-b', token_type: 'bearer', user_id: 'user-b',
        public_key: '', encrypted_private_key: null,
      }));
      return pending;
    },
  });
  return {
    api: exports.api, storage, storageReads, events, requests, started,
    unauthorized: () => release(response({ detail: 'This request is no longer authorized.' }, 401)),
  };
}

const operations = [
  { label: 'JSON request', path: '/channels', method: 'GET', start: api => api.getChannels() },
  { label: 'file upload', path: '/files/channels/channel-a', method: 'POST', start: api => api.uploadFile('channel-a', new File(['Notes'], 'notes.txt', { type: 'text/plain' })) },
  { label: 'file download', path: '/files/file-a', method: 'GET', start: api => api.downloadFile('file-a') },
];

function expectUnauthorized(pending) {
  return assert.rejects(pending, error => error.status === 401 && error.message === 'This request is no longer authorized.');
}

for (const operation of operations) {
  test(`${operation.label}: a late old-account 401 cannot expire the newly signed-in account`, async () => {
    const { api, storage, events, requests, unauthorized, started } = fixture();
    const rejected = expectUnauthorized(operation.start(api));
    await started;
    assert.equal(requests[0].url, 'https://freecollab.test/api' + operation.path);
    assert.equal(requests[0].options.method || 'GET', operation.method);
    assert.equal(new Headers(requests[0].options.headers).get('Authorization'), 'Bearer token-a');
    await api.login('bob@example.com', 'test-password');
    assert.equal(api.getCurrentUserId(), 'user-b');
    unauthorized();
    await rejected;
    assert.equal(events.includes('freecollab:session-expired'), false);
    assert.equal(storage.get('freecollab_token'), 'token-b');
    assert.equal(api.getCurrentUserId(), 'user-b');
    assert.equal(new Headers(requests[0].options.headers).get('Authorization'), 'Bearer token-a');
  });

  test(`${operation.label}: a 401 for the current account still expires its session`, async () => {
    const { api, events, unauthorized, started } = fixture();
    const rejected = expectUnauthorized(operation.start(api));
    await started;
    unauthorized();
    await rejected;
    assert.deepEqual(events, ['freecollab:session-expired']);
  });

  test(`${operation.label}: an unauthenticated 401 rejects without a global session-expired event`, async () => {
    const { api, events, requests, unauthorized, started } = fixture(false);
    const rejected = expectUnauthorized(operation.start(api));
    await started;
    assert.equal(new Headers(requests[0].options.headers).get('Authorization'), null);
    unauthorized();
    await rejected;
    assert.deepEqual(events, []);
  });

  test(`${operation.label}: an anonymous request cannot expire an account signed in while it was pending`, async () => {
    const { api, storage, events, unauthorized, started } = fixture(false);
    const rejected = expectUnauthorized(operation.start(api));
    await started;
    await api.login('bob@example.com', 'test-password');
    unauthorized();
    await rejected;
    assert.equal(events.includes('freecollab:session-expired'), false);
    assert.equal(storage.get('freecollab_token'), 'token-b');
    assert.equal(api.getCurrentUserId(), 'user-b');
  });
}


test('trusted embedded sessions never read or overwrite an existing standalone account', () => {
  const { api, storage, storageReads } = fixture(true, true);
  assert.equal(api.isMemorySession(), true);
  assert.equal(api.isAuthenticated(), false);
  assert.equal(api.getCurrentUserId(), null);
  assert.deepEqual(storageReads, []);
  api.applySession({ access_token: 'embedded-child', user_id: 'embed-user', public_key: '', encrypted_private_key: null, token_type: 'bearer' });
  assert.equal(api.getAccessToken(), 'embedded-child');
  api.syncStoredSession();
  assert.equal(api.getAccessToken(), 'embedded-child');
  assert.equal(api.getCurrentUserId(), 'embed-user');
  assert.equal(storage.get('freecollab_token'), 'token-a');
  assert.equal(storage.get('freecollab_user_id'), 'user-a');
  api.logout();
  assert.equal(api.isAuthenticated(), false);
  assert.equal(storage.get('freecollab_token'), 'token-a');
  assert.equal(storage.get('freecollab_user_id'), 'user-a');
  assert.deepEqual(storageReads, []);
});

test('trusted sign-in popup switches to memory before providers without clearing the standalone session', () => {
  const { api, storage } = fixture();
  assert.equal(api.getAccessToken(), 'token-a');
  api.useMemorySession();
  assert.equal(api.isAuthenticated(), false);
  assert.equal(storage.get('freecollab_token'), 'token-a');
  api.applySession({ access_token: 'popup-token', user_id: 'popup-user', public_key: '', encrypted_private_key: null, token_type: 'bearer' });
  api.syncStoredSession();
  assert.equal(api.getAccessToken(), 'popup-token');
  assert.equal(storage.get('freecollab_user_id'), 'user-a');
});
