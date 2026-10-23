import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const compiled = ts.transpileModule(readFileSync(new URL('../src/lib/federated-work-pointers.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;

function fixture({ demo = false, embedded = false, memory = false } = {}) {
  const callbacks = new Map(), operations = [], retained = new Set(['private-retry-from-an-existing-account']);
  const db = {
    transaction: (store, mode) => {
      assert.equal(store, 'pointers'); assert.equal(mode, 'readwrite');
      const transaction = { objectStore: () => ({ clear: () => {
        operations.push('clear'); retained.clear(); queueMicrotask(() => transaction.oncomplete?.());
      } }) };
      return transaction;
    },
    close: () => operations.push('close'),
  };
  vm.runInNewContext(compiled, {
    exports: {},
    require: name => { assert.equal(name, './api'); return { api: { isMemorySession: () => memory } }; },
    document: { getElementById: () => ({ hasAttribute: name => name === 'data-demo-application' ? demo : name === 'data-embed-application' && embedded }) },
    window: { addEventListener: (event, callback) => callbacks.set(event, callback) },
    indexedDB: { open: name => {
      assert.equal(name, 'freecollab-federated-work-pointers-v1'); operations.push('open');
      const request = { result: db }; queueMicrotask(() => request.onsuccess?.()); return request;
    } },
  });
  return { operations, retained, authChanged: () => callbacks.get('freecollab:auth-changed')() };
}

test('demo bootstrap, reset and sign-out never open or purge existing federated-work retries', async () => {
  const sample = fixture({ demo: true, memory: true });
  sample.authChanged(); sample.authChanged(); sample.authChanged();
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(sample.operations, []);
  assert.deepEqual([...sample.retained], ['private-retry-from-an-existing-account']);
});

for (const [name, options] of [['standalone account', {}], ['trusted encrypted embed', { embedded: true, memory: true }]]) {
  test(`${name} retains its existing real account-change cleanup behavior`, async () => {
    const normal = fixture(options); normal.authChanged();
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(normal.operations, ['open', 'clear', 'close']);
    assert.equal(normal.retained.size, 0);
  });
}
