import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/lib/avatar-api.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
function fixture({ honorTransportAbort = true } = {}) {
  let token = 'actor-one';
  const requests = [], listeners = new Map(), exports = {};
  const window = {
    addEventListener(name, fn) { listeners.set(name, fn); },
    dispatchEvent(event) { listeners.get(event.type)?.(event); },
  };
  const api = {
    getAccessToken: () => token,
    requestBlob(path, { signal }) {
      let yes, no;
      const promise = new Promise((resolve, reject) => { yes = resolve; no = reject; });
      const request = { path, signal, resolve: yes, reject: no, token };
      requests.push(request);
      if (honorTransportAbort) signal.addEventListener('abort', () => no(new DOMException('Aborted', 'AbortError')));
      return promise;
    },
  };
  vm.runInNewContext(compiled, { exports, require: name => { assert.equal(name, './api'); return { api }; },
    window, AbortController, DOMException, Map, JSON, FormData, Blob, CustomEvent, Event });
  return { avatar: exports.avatarApi, requests,
    event: (type, user_id) => window.dispatchEvent(new CustomEvent(type, { detail: { user_id } })),
    token(value) { token = value; },
  };
}

test('100 simultaneous same-person/scope/actor reads use one request and no settled cache', async () => {
  const f = fixture();
  const pending = Array.from({ length: 100 }, () => f.avatar.read('person/x', 'workspace/x'));
  assert.equal(f.requests.length, 1);
  assert.equal(f.requests[0].path, '/users/person%2Fx/avatar?workspace_id=workspace%2Fx');
  const bytes = new Blob(['pixels'], { type: 'image/png' });
  f.requests[0].resolve(bytes);
  assert.ok((await Promise.all(pending)).every(blob => blob === bytes));
  const fresh = f.avatar.read('person/x', 'workspace/x');
  assert.equal(f.requests.length, 2);
  f.requests[1].resolve(bytes); await fresh;
});

test('one cancelled consumer does not abort another avatar; all cancelled consumers abort network', async () => {
  const f = fixture(), first = new AbortController(), second = new AbortController();
  const one = f.avatar.read('same', 'scope', first.signal), two = f.avatar.read('same', 'scope', second.signal);
  const cancelled = assert.rejects(one, { name: 'AbortError' });
  first.abort(); await cancelled;
  assert.equal(f.requests[0].signal.aborted, false);
  f.requests[0].resolve(new Blob(['pixels'])); assert.equal((await two).size, 6);
  const third = new AbortController();
  const three = f.avatar.read('same', 'scope', third.signal);
  const thirdCancelled = assert.rejects(three, { name: 'AbortError' });
  third.abort(); await thirdCancelled;
  assert.equal(f.requests[1].signal.aborted, true);
  const fourth = f.avatar.read('same', 'scope'); assert.equal(f.requests.length, 3);
  f.requests[2].resolve(new Blob()); await fourth;
});

test('account, workspace and person are separate in-flight boundaries', async () => {
  const f = fixture();
  const one = f.avatar.read('person', 'one');
  const otherWorkspace = f.avatar.read('person', 'two');
  const otherPerson = f.avatar.read('another', 'one');
  f.token('actor-two');
  const secondActor = f.avatar.read('person', 'one');
  const stale = [one, otherWorkspace, otherPerson].map(pending => assert.rejects(pending, { name: 'AbortError' }));
  assert.equal(f.requests.length, 4);
  f.requests.forEach(request => request.resolve(new Blob(['bytes'])));
  await Promise.all(stale); await secondActor;
});

for (const event of ['freecollab:auth-changed', 'freecollab:workspace-changed', 'freecollab:avatar-changed'])
  test(`${event} cancels old bytes and allows a fresh authenticated read`, async () => {
    const f = fixture();
    const old = f.avatar.read('person', 'scope');
    const denied = assert.rejects(old, { name: 'AbortError' });
    f.event(event, 'person'); await denied;
    assert.equal(f.requests[0].signal.aborted, true);
    const next = f.avatar.read('person', 'scope');
    assert.equal(f.requests.length, 2);
    f.requests[1].resolve(new Blob(['new pixels'])); await next;
  });

test('a changed photo invalidates only that person’s pending byte read', async () => {
  const f = fixture();
  const changed = f.avatar.read('changed', 'scope'), unchanged = f.avatar.read('unchanged', 'scope');
  const rejected = assert.rejects(changed, { name: 'AbortError' });
  f.event('freecollab:avatar-changed', 'changed'); await rejected;
  assert.equal(f.requests[1].signal.aborted, false);
  f.requests[1].resolve(new Blob()); await unchanged;
});

test('an already cancelled avatar cannot start network work', async () => {
  const f = fixture(), controller = new AbortController(); controller.abort();
  await assert.rejects(f.avatar.read('person', 'scope', controller.signal), { name: 'AbortError' });
  assert.equal(f.requests.length, 0);
});

for (const event of ['freecollab:workspace-changed', 'freecollab:avatar-changed'])
  test(`a transport that finishes after ${event} cannot resolve stale bytes`, async () => {
    const f = fixture({ honorTransportAbort: false });
    const old = f.avatar.read('person', 'old-workspace');
    const denied = assert.rejects(old, { name: 'AbortError' });
    f.event(event, 'person');
    f.requests[0].resolve(new Blob(['old pixels']));
    await denied;
    const fresh = f.avatar.read('person', 'new-workspace');
    f.requests[1].resolve(new Blob(['new pixels']));
    assert.equal(await (await fresh).text(), 'new pixels');
  });
