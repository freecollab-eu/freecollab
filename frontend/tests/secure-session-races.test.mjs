import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/lib/secure/index.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };

function fixture() {
  const exports = {}, events = [], responses = [], bodyStarted = deferred();
  let token = 'account-a';
  const dependencies = new Set(['matrix-js-sdk', 'matrix-js-sdk/lib/crypto-api/CryptoEvent', 'matrix-js-sdk/lib/crypto-api/recovery-key',
    './device-store', './transaction-retry', './publish-guard', '../channel-metadata', './message-content']);
  vm.runInNewContext(compiled, {
    exports, clearTimeout,
    require: name => {
      if (name === '../api') return { APIError: class extends Error {} };
      assert.ok(dependencies.has(name), name);
      return {};
    },
    Event, window: { dispatchEvent: event => events.push(event.type) },
    fetch: async (_url, options) => {
      const response = responses.shift();
      assert.ok(response, 'Every request must have an explicit fixture response');
      response.authorization = options.headers.Authorization;
      return { ok: response.ok, status: response.status, json: () => { bodyStarted.resolve(); return response.body.promise; } };
    },
  });
  exports.configureSecureMessaging({ baseUrl: '/api', getAccessToken: () => token, getUserId: () => token });
  const enqueue = (ok = true, status = 200) => { const response = { ok, status, body: deferred() }; responses.push(response); return response; };
  return { secure: exports.secureMessaging, events, bodyStarted: bodyStarted.promise, enqueue,
    switchAccount: () => { token = 'account-b'; exports.secureMessaging.stop(); } };
}

for (const boundary of ['account change', 'encryption reset']) test(`a delayed secure channel body cannot restore bindings after ${boundary}`, async () => {
  const f = fixture(), response = f.enqueue();
  const pending = f.secure.refreshBindings(), rejected = assert.rejects(pending, /signed-in account changed/);
  await f.bodyStarted;
  if (boundary === 'account change') f.switchAccount(); else f.secure.stop();
  response.body.resolve([{ channel_id: 'account-a-private', room_id: '!private:example.test', status: 'active' }]);
  await rejected;
  assert.equal(response.authorization, 'Bearer account-a');

  const current = f.enqueue(); current.body.resolve([]);
  assert.equal(await f.secure.handles('account-a-private'), false);
  assert.equal(current.authorization, boundary === 'account change' ? 'Bearer account-b' : 'Bearer account-a');
});

test('an old encryption generation cannot expire the current sign-in with a delayed 401 body', async () => {
  const f = fixture(), response = f.enqueue(false, 401);
  const pending = f.secure.refreshBindings(), rejected = assert.rejects(pending, /expired/);
  await f.bodyStarted; f.secure.stop(); response.body.resolve({ detail: 'Session expired' }); await rejected;
  assert.deepEqual(f.events, []);
});

test('the current encryption generation still expires its unauthorized sign-in', async () => {
  const f = fixture(), response = f.enqueue(false, 401);
  response.body.resolve({ detail: 'Session expired' });
  await assert.rejects(f.secure.refreshBindings(), /expired/);
  assert.deepEqual(f.events, ['freecollab:session-expired']);
});

test('a delayed body from the current account and encryption generation preserves its binding', async () => {
  const f = fixture(), response = f.enqueue();
  const pending = f.secure.handles('current-private');
  await f.bodyStarted;
  response.body.resolve([{ channel_id: 'current-private', room_id: '!private:example.test', status: 'active' }]);
  assert.equal(await pending, true);
  assert.equal(response.authorization, 'Bearer account-a');
  assert.deepEqual(f.events, []);
});
