import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const compile = source => ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;
const model = {};
vm.runInNewContext(compile(readFileSync(new URL('../src/lib/message-references.ts', import.meta.url), 'utf8')), { exports: model });
const hookSource = readFileSync(new URL('../src/lib/useComposerReferences.ts', import.meta.url), 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));
const candidate = (id = 'ada', label = 'Ada Lovelace') => ({ kind: 'person', id, label, channel_id: 'room' });

/** Execute the actual hook with deterministic React effects, delayed reads and frames. */
function fixture(initial = {}) {
  const slots = [], effects = [], requests = [], inserted = [], focus = [], timers = new Map(), frames = new Map();
  let index = 0, dirty = true, mounted = true, clock = 0, nextTimer = 0, nextFrame = 0, current;
  let actor = 'reader', token = 'reader-token';
  const hub = () => {
    const handlers = new Map();
    return {
      addEventListener(name, handler) { if (!handlers.has(name)) handlers.set(name, new Set()); handlers.get(name).add(handler); },
      removeEventListener(name, handler) { handlers.get(name)?.delete(handler); },
      emit(name, event = {}) { for (const handler of [...handlers.get(name) || []]) handler(event); },
    };
  };
  const window = hub(), document = { ...hub(), getElementById: () => null };
  const input = { current: null };
  const inputNode = (name, text) => ({
    name, value: text, selectionStart: text.length,
    focus() { focus.push({ name, kind: 'focus' }); },
    setSelectionRange(start, end) { this.selectionStart = start; focus.push({ name, kind: 'selection', start, end }); },
  });
  let options = {
    channelId: 'room', workspaceId: 'workspace', secure: false, contextId: 'thread-a',
    text: '@Ada', references: [], input, ...initial,
    onInsert(text, references) {
      inserted.push({ contextId: options.contextId, text, references: plain(references) });
      options = { ...options, text, references }; input.current.value = text; dirty = true;
    },
  };
  input.current = inputNode(options.contextId || 'main', options.text);
  const changed = (left, right) => !left || !right || left.length !== right.length || left.some((value, n) => !Object.is(value, right[n]));
  const react = {
    useId() { const n = index++; if (!(n in slots)) slots[n] = { value: 'reference-' + n }; return slots[n].value; },
    useRef(initialValue) { const n = index++; if (!(n in slots)) slots[n] = { value: { current: initialValue } }; return slots[n].value; },
    useState(initialValue) {
      const n = index++;
      if (!(n in slots)) slots[n] = { value: typeof initialValue === 'function' ? initialValue() : initialValue };
      const cell = slots[n];
      return [cell.value, next => {
        const value = typeof next === 'function' ? next(cell.value) : next;
        if (!Object.is(value, cell.value)) { cell.value = value; if (mounted) dirty = true; }
      }];
    },
    useEffect(callback, dependencies) {
      const n = index++;
      if (!slots[n] || changed(slots[n].dependencies, dependencies)) effects.push(() => {
        slots[n]?.cleanup?.(); slots[n] = { dependencies, cleanup: callback() };
      });
    },
  };
  const api = { getCurrentUserId: () => actor, getAccessToken: () => token };
  const messageReferencesApi = { suggestions(channelId, query, signal, secure) {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    requests.push({ channelId, query, signal, secure, resolve, reject });
    return promise;
  } };
  window.setTimeout = (callback, delay) => { const id = ++nextTimer; timers.set(id, { callback, due: clock + delay }); return id; };
  const clearTimeout = id => timers.delete(id);
  const imports = {
    react, './api': { api }, './message-references': model,
    './message-references-api': { messageReferencesApi },
    './commands': { nextCommandIndex: (active, direction, length) => length ? (active + direction + length) % length : -1 },
  };
  const exports = {};
  vm.runInNewContext(compile(hookSource), {
    exports, require(name) { assert.ok(Object.hasOwn(imports, name), name); return imports[name]; },
    window, document, AbortController, clearTimeout,
    requestAnimationFrame(callback) { const id = ++nextFrame; frames.set(id, callback); return id; },
  });
  async function settle() {
    let idle = 0;
    for (let cycle = 0; cycle < 50; cycle++) {
      if (dirty) {
        assert.ok(mounted); dirty = false; index = 0; current = exports.useComposerReferences(options);
        for (const effect of effects.splice(0)) effect();
      }
      for (let microtask = 0; microtask < 8; microtask++) await Promise.resolve();
      if (!dirty && !effects.length) { if (++idle === 3) return; } else idle = 0;
    }
    throw new Error('Reference hook did not settle');
  }
  return {
    requests, inserted, focus, input,
    get current() { return current; },
    get options() { return options; },
    settle,
    async query() { current.update(options.text, input.current.selectionStart, options.references, true); await settle(); },
    async advance(milliseconds = 120) {
      clock += milliseconds;
      for (const [id, timer] of [...timers]) if (timer.due <= clock) { timers.delete(id); timer.callback(); }
      await settle();
    },
    async resolve(request, rows = [candidate()]) { request.resolve(rows); await settle(); },
    async choose(row = current.candidates[0]) { current.choose(row); await settle(); },
    async setContext(contextId, changes = {}) {
      options = { ...options, contextId, ...changes };
      input.current = inputNode(contextId || 'main', options.text); dirty = true; await settle();
    },
    async render(changes = {}) { options = { ...options, ...changes }; input.current.value = options.text; dirty = true; await settle(); },
    async changeAccount() { actor = 'other'; token = 'other-token'; dirty = true; window.emit('freecollab:auth-changed'); await settle(); },
    async flushFrames() { for (const [id, callback] of [...frames]) { frames.delete(id); callback(clock); } await settle(); },
    unmount() { mounted = false; for (const slot of slots) slot?.cleanup?.(); },
  };
}

test('changing threads with the same text aborts and discards the old in-flight suggestions', async () => {
  const h = fixture();
  try {
    await h.settle(); await h.query(); await h.advance();
    assert.equal(h.requests.length, 1); const old = h.requests[0];
    assert.equal(h.current.open, true);
    await h.setContext('thread-b');
    assert.equal(old.signal.aborted, true);
    assert.equal(h.current.open, false);
    await h.resolve(old, [candidate('old-thread-person', 'Old thread result')]);
    assert.deepEqual(plain(h.current.candidates), []);
    assert.equal(h.inserted.length, 0); assert.equal(h.focus.length, 0);
    await h.query(); await h.advance();
    assert.equal(h.requests.length, 2);
    await h.resolve(h.requests[1], [candidate('new-thread-person', 'New thread result')]);
    assert.deepEqual(plain(h.current.candidates).map(row => row.id), ['new-thread-person']);
  } finally { h.unmount(); }
});

test('a captured selection callback cannot insert into another thread with identical draft text', async () => {
  const h = fixture();
  try {
    await h.settle(); await h.query(); await h.advance(); await h.resolve(h.requests[0]);
    const old = h.current, row = old.candidates[0];
    assert.equal(row.id, 'ada');
    await h.setContext('thread-b');
    old.choose(row); await h.settle(); await h.flushFrames();
    assert.equal(h.options.text, '@Ada'); assert.equal(h.inserted.length, 0); assert.equal(h.focus.length, 0);
  } finally { h.unmount(); }
});

test('an old queued caret frame cannot focus a new thread whose text matches the inserted value', async () => {
  const h = fixture();
  try {
    await h.settle(); await h.query(); await h.advance(); await h.resolve(h.requests[0]);
    await h.choose();
    assert.equal(h.inserted.length, 1); assert.equal(h.inserted[0].contextId, 'thread-a');
    const sameText = h.options.text;
    await h.setContext('thread-b', { text: sameText, references: h.options.references });
    await h.flushFrames();
    assert.equal(h.options.text, sameText); assert.equal(h.focus.length, 0);
    assert.equal(h.inserted.length, 1);
  } finally { h.unmount(); }
});

test('a current thread can select a genuine person within task arguments and retain exact identity', async () => {
  const draft = '/task "Review the materials" @Ada';
  const h = fixture({ text: draft });
  try {
    await h.settle(); await h.query(); await h.advance();
    assert.equal(h.requests[0].query, 'Ada'); assert.equal(h.requests[0].channelId, 'room');
    await h.resolve(h.requests[0]); await h.choose(); await h.flushFrames();
    assert.equal(h.inserted.length, 1);
    assert.equal(h.options.text, '/task "Review the materials" @Ada Lovelace ');
    assert.deepEqual(plain(h.options.references).map(row => [row.kind, row.id]), [['person', 'ada']]);
    const reference = h.options.references[0];
    assert.deepEqual(Object.keys(reference).sort(), ['end', 'id', 'kind', 'start']);
    assert.equal(h.options.text.slice(reference.start, reference.end), '@Ada Lovelace');
    assert.deepEqual(h.focus, [
      { name: 'thread-a', kind: 'focus' },
      { name: 'thread-a', kind: 'selection', start: h.options.text.length, end: h.options.text.length },
    ]);
  } finally { h.unmount(); }
});

test('an unchanged default main composer retains current suggestions across ordinary rerenders', async () => {
  const h = fixture({ contextId: undefined });
  try {
    await h.settle(); await h.query(); await h.advance(); await h.resolve(h.requests[0]);
    await h.render();
    assert.equal(h.current.open, true); assert.equal(h.current.candidates[0].id, 'ada');
    assert.equal(h.requests.length, 1);
    await h.choose(); await h.flushFrames();
    assert.equal(h.inserted[0].contextId, undefined); assert.equal(h.focus[0].name, 'main');
  } finally { h.unmount(); }
});

test('closing and reopening a thread does not revive its old selection or request', async () => {
  const h = fixture();
  try {
    await h.settle(); await h.query(); await h.advance(); const old = h.requests[0];
    await h.setContext(undefined, { channelId: undefined });
    assert.equal(old.signal.aborted, true); assert.equal(h.current.open, false);
    await h.resolve(old);
    await h.setContext('thread-a', { channelId: 'room' });
    assert.equal(h.current.open, false); assert.deepEqual(plain(h.current.candidates), []);
    assert.equal(h.inserted.length, 0); assert.equal(h.focus.length, 0);
  } finally { h.unmount(); }
});

test('an account transition still invalidates requests and queued focus in an unchanged thread', async () => {
  const h = fixture();
  try {
    await h.settle(); await h.query(); await h.advance(); await h.resolve(h.requests[0]);
    await h.choose(); await h.changeAccount(); await h.flushFrames();
    assert.equal(h.focus.length, 0); assert.equal(h.current.open, false);
    assert.equal(h.inserted.length, 1);
  } finally { h.unmount(); }
});
