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

test('the real picker ranks current writing context and keeps its selected identity when context changes', async () => {
  const h = fixture({ text: '@', suggestions: { surface: 'conversation' } });
  try {
    await h.settle(); await h.query(); await h.advance();
    const rows = [candidate('person'), { kind: 'document', id: 'doc', label: 'Plan', channel_id: 'room' }, { kind: 'file', id: 'file', label: 'Notes', channel_id: 'room' }];
    await h.resolve(h.requests[0], rows);
    assert.deepEqual(plain(h.current.candidates).map(row => row.id), ['person', 'doc', 'file']);
    h.current.setActiveIndex(0); await h.settle();
    await h.render({ suggestions: { surface: 'document', field: 'body' } });
    assert.deepEqual(plain(h.current.candidates).map(row => row.id), ['doc', 'file', 'person']);
    assert.equal(h.current.candidates[h.current.activeIndex].id, 'person');
    assert.equal(h.requests.length, 1, 'changing presentation context does not request another authority snapshot');
    assert.deepEqual(rows.map(row => row.id), ['person', 'doc', 'file']);
    await h.choose(h.current.candidates[h.current.activeIndex]);
    assert.equal(h.options.references[0].id, 'person');
  } finally { h.unmount(); }
});

test('a successful reference choice becomes recent within this session and clears on account change', async () => {
  const h = fixture({ text: '@', suggestions: { surface: 'task', field: 'assignee' } });
  const rows = [candidate('alex-a', 'Alex'), candidate('alex-b', 'Alex')];
  try {
    await h.settle(); await h.query(); await h.advance(); await h.resolve(h.requests[0], rows);
    await h.choose(h.current.candidates[1]);
    assert.equal(h.options.references[0].id, 'alex-b');
    await h.render({ text: '@', references: [] }); h.input.current.selectionStart = h.input.current.selectionEnd = 1;
    await h.query(); await h.advance(); await h.resolve(h.requests[1], rows);
    assert.deepEqual(plain(h.current.candidates).map(row => row.id), ['alex-b', 'alex-a']);
    await h.changeAccount(); await h.query(); await h.advance(); await h.resolve(h.requests[2], rows);
    assert.deepEqual(plain(h.current.candidates).map(row => row.id), ['alex-a', 'alex-b']);
  } finally { h.unmount(); }
});

test('an exact reference label outranks a contextual prefix in the actual picker', async () => {
  const h = fixture({ text: '/task Review @Alex' });
  try {
    await h.settle(); await h.query(); await h.advance();
    await h.resolve(h.requests[0], [candidate('person', 'Alex Morgan'), { kind: 'document', id: 'doc', label: 'Alex', channel_id: 'room' }]);
    assert.deepEqual(plain(h.current.candidates).map(row => row.id), ['doc', 'person']);
    await h.choose();
    assert.deepEqual(plain(h.options.references).map(row => [row.kind, row.id]), [['document', 'doc']]);
    assert.equal(h.options.text, '/task Review @Alex ');
  } finally { h.unmount(); }
});

/** Execute the actual hook with deterministic React effects, delayed reads and frames. */
function fixture(initial = {}) {
  const slots = [], effects = [], requests = [], inserted = [], focus = [], timers = new Map(), frames = new Map();
  let index = 0, dirty = true, mounted = true, clock = 0, nextTimer = 0, nextFrame = 0, current;
  let actor = 'reader', token = 'reader-token';
  const hub = () => {
    const handlers = new Map();
    const key = (name, capture) => name + ':' + (capture === true || capture?.capture === true);
    return {
      addEventListener(name, handler, capture = false) { const id = key(name, capture); if (!handlers.has(id)) handlers.set(id, new Set()); handlers.get(id).add(handler); },
      removeEventListener(name, handler, capture = false) { handlers.get(key(name, capture))?.delete(handler); },
      emit(name, event = {}) { for (const capture of [true, false]) for (const handler of [...handlers.get(key(name, capture)) || []]) handler({ type: name, ...event }); },
      get listenerCount() { return [...handlers.values()].reduce((count, items) => count + items.size, 0); },
    };
  };
  const window = hub(), document = hub();
  document.activeElement = null; document.getElementById = () => null;
  const input = { current: null };
  const inputNode = (name, text) => ({
    name, value: text, selectionStart: text.length, selectionEnd: text.length, isConnected: true,
    focus() {
      focus.push({ name, kind: 'focus' });
      if (document.activeElement !== this) { document.activeElement = this; document.emit('focusin', { target: this }); }
    },
    setSelectionRange(start, end) {
      this.selectionStart = start; this.selectionEnd = end;
      focus.push({ name, kind: 'selection', start, end }); document.emit('select', { target: this });
    },
  });
  let options = {
    channelId: 'room', workspaceId: 'workspace', secure: false, contextId: 'thread-a',
    text: '@Ada', references: [], input, ...initial,
    onInsert(text, references) {
      inserted.push({ contextId: options.contextId, text, references: plain(references) });
      options = { ...options, text, references }; input.current.value = text;
      input.current.selectionStart = input.current.selectionEnd = text.length; dirty = true;
    },
  };
  input.current = inputNode(options.contextId || 'main', options.text);
  document.activeElement = input.current;
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
  const ranking = {};
  vm.runInNewContext(compile(readFileSync(new URL('../src/lib/suggestion-ranking.ts', import.meta.url), 'utf8')), { exports: ranking, window });
  imports['./suggestion-ranking'] = ranking;
  const exports = {};
  vm.runInNewContext(compile(hookSource), {
    exports, require(name) { assert.ok(Object.hasOwn(imports, name), name); return imports[name]; },
    window, document, AbortController, clearTimeout,
    requestAnimationFrame(callback) { const id = ++nextFrame; frames.set(id, callback); return id; },
    cancelAnimationFrame(id) { frames.delete(id); },
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
    requests, inserted, focus, input, document,
    get current() { return current; },
    get options() { return options; },
    get pendingFrames() { return [...frames.values()]; },
    createInput: inputNode,
    emit(name, target = input.current) { document.emit(name, { target }); },
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
      input.current.isConnected = false; input.current = inputNode(contextId || 'main', options.text);
      document.activeElement = input.current; dirty = true; await settle();
    },
    async render(changes = {}) { options = { ...options, ...changes }; input.current.value = options.text; dirty = true; await settle(); },
    async changeAccount() { actor = 'other'; token = 'other-token'; dirty = true; window.emit('freecollab:auth-changed'); await settle(); },
    account(nextActor, nextToken, notify = true) { actor = nextActor; token = nextToken; if (notify) window.emit('freecollab:auth-changed'); },
    keyboardInsert(text) {
      const node = document.activeElement;
      document.emit('beforeinput', { target: node });
      const end = node.selectionStart + text.length;
      node.value = node.value.slice(0, node.selectionStart) + text + node.value.slice(node.selectionEnd);
      node.selectionStart = node.selectionEnd = end; document.emit('input', { target: node });
    },
    async flushFrames() {
      for (const [id, callback] of [...frames]) if (frames.has(id)) { frames.delete(id); callback(clock); }
      if (mounted) await settle();
    },
    unmount() { if (!mounted) return; mounted = false; for (const slot of slots) slot?.cleanup?.(); },
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


test('long document references respect their declared limit rather than the message budget', async () => {
  const text = 'A'.repeat(12000) + ' @Ada';
  const h = fixture({ contextId: 'document-body', text, maxLength: 50000 });
  try {
    await h.settle(); await h.query(); await h.advance(); await h.resolve(h.requests[0]);
    await h.choose();
    assert.equal(h.inserted.length, 1);
    assert.equal(h.inserted[0].text, 'A'.repeat(12000) + ' @Ada Lovelace ');
    assert.deepEqual(h.inserted[0].references, [{ kind: 'person', id: 'ada', start: 12001, end: 12014 }]);
  } finally { h.unmount(); }
});

test('short fields retain their exact native text limit when selecting a reference', async () => {
  const h = fixture({ contextId: 'document-title', text: '@Ada', maxLength: 8 });
  try {
    await h.settle(); await h.query(); await h.advance(); await h.resolve(h.requests[0]);
    await h.choose();
    assert.equal(h.inserted.length, 0);
    assert.equal(h.current.open, true);
    assert.equal(h.input.current.value, '@Ada');
  } finally { h.unmount(); }
});

async function chooseCurrent(h) {
  await h.settle(); await h.query(); await h.advance(); await h.resolve(h.requests[0]); await h.choose();
  assert.equal(h.inserted.length, 1);
  assert.equal(h.pendingFrames.length, 1);
  return h.pendingFrames[0];
}

test('a reference selected in the middle of current text restores its own caret and preserves the trailing passage', async () => {
  const h = fixture({ text: 'Review @Ada after lunch.' });
  try {
    h.input.current.selectionStart = h.input.current.selectionEnd = 'Review @Ada'.length;
    await chooseCurrent(h);
    await h.flushFrames();
    assert.equal(h.options.text, 'Review @Ada Lovelace  after lunch.');
    assert.deepEqual(plain(h.options.references), [{ kind: 'person', id: 'ada', start: 7, end: 20 }]);
    assert.equal(h.document.activeElement, h.input.current);
    assert.equal(h.input.current.selectionStart, 21); assert.equal(h.input.current.selectionEnd, 21);
    assert.deepEqual(h.focus, [{ name: 'thread-a', kind: 'focus' }, { name: 'thread-a', kind: 'selection', start: 21, end: 21 }]);
    assert.equal(h.pendingFrames.length, 0);
    assert.equal(h.document.listenerCount, 0);
  } finally { h.unmount(); }
});

test('focusing the next field before the queued frame keeps its typed text in that field', async () => {
  const h = fixture();
  try {
    const staleFrame = await chooseCurrent(h), selectedText = h.options.text;
    const next = h.createInput('poll-option-2', '');
    next.focus();
    assert.equal(h.pendingFrames.length, 0);
    // Model the two browser turns in fill(): focus/select first, insertText
    // next. Even an already-delivered stale callback must not steal focus.
    staleFrame(0); await h.flushFrames(); h.keyboardInsert('Discuss first');
    assert.equal(h.document.activeElement, next); assert.equal(next.value, 'Discuss first');
    assert.equal(h.input.current.value, selectedText); assert.equal(h.options.text, selectedText);
    assert.deepEqual(plain(h.options.references), [{ kind: 'person', id: 'ada', start: 0, end: 13 }]);
    assert.deepEqual(h.focus, [{ name: 'poll-option-2', kind: 'focus' }]);
    assert.equal(h.document.listenerCount, 0);
  } finally { h.unmount(); }
});

for (const event of ['pointerdown', 'touchstart', 'keydown', 'beforeinput', 'input', 'compositionstart']) {
  test('newer ' + event + ' intent retires the pending reference caret even when its final text is unchanged', async () => {
    const h = fixture();
    try {
      const staleFrame = await chooseCurrent(h), selectedText = h.options.text;
      // Text equality alone cannot authorize delayed focus: a pointer move,
      // key, IME edit or edit/undo may have superseded the insertion already.
      h.emit(event);
      assert.equal(h.pendingFrames.length, 0);
      staleFrame(0); await h.flushFrames();
      assert.equal(h.options.text, selectedText); assert.equal(h.inserted.length, 1);
      assert.deepEqual(h.focus, []); assert.equal(h.document.listenerCount, 0);
    } finally { h.unmount(); }
  });
}

test('a newer selection in the same unchanged field survives the old reference caret frame', async () => {
  const h = fixture();
  try {
    const staleFrame = await chooseCurrent(h), selectedText = h.options.text;
    h.input.current.setSelectionRange(0, 3);
    staleFrame(0); await h.flushFrames();
    assert.equal(h.options.text, selectedText);
    assert.equal(h.input.current.selectionStart, 0); assert.equal(h.input.current.selectionEnd, 3);
    assert.deepEqual(h.focus, [{ name: 'thread-a', kind: 'selection', start: 0, end: 3 }]);
    assert.equal(h.pendingFrames.length, 0); assert.equal(h.document.listenerCount, 0);
  } finally { h.unmount(); }
});

test('moving focus away and back does not revive an earlier reference caret request', async () => {
  const h = fixture();
  try {
    const staleFrame = await chooseCurrent(h), source = h.input.current;
    h.createInput('another-field', '').focus(); source.focus();
    staleFrame(0); await h.flushFrames();
    assert.equal(h.document.activeElement, source);
    assert.deepEqual(h.focus, [{ name: 'another-field', kind: 'focus' }, { name: 'thread-a', kind: 'focus' }]);
    assert.equal(h.pendingFrames.length, 0); assert.equal(h.document.listenerCount, 0);
  } finally { h.unmount(); }
});

test('a same-scope input replacement with identical text cannot inherit the old node focus request', async () => {
  const h = fixture();
  try {
    await chooseCurrent(h);
    const original = h.input.current, replacement = h.createInput('replacement', h.options.text);
    // Keep the original connected and active to exercise exact node identity,
    // independently of the normal focusin/disconnection cancellation paths.
    h.input.current = replacement;
    await h.flushFrames();
    assert.equal(h.document.activeElement, original); assert.equal(original.isConnected, true);
    assert.equal(h.input.current, replacement); assert.deepEqual(h.focus, []);
    assert.equal(h.document.listenerCount, 0);
  } finally { h.unmount(); }
});

test('a disconnected source cannot be focused by its pending reference frame', async () => {
  const h = fixture();
  try {
    await chooseCurrent(h); h.input.current.isConnected = false;
    await h.flushFrames();
    assert.deepEqual(h.focus, []); assert.equal(h.document.listenerCount, 0);
  } finally { h.unmount(); }
});

test('a same-scope permission change disables a previously authorized reference caret frame', async () => {
  const h = fixture();
  try {
    await chooseCurrent(h); await h.render({ disabled: true });
    await h.flushFrames();
    assert.equal(h.current.open, false); assert.deepEqual(h.focus, []);
    assert.equal(h.inserted.length, 1); assert.equal(h.document.listenerCount, 0);
  } finally { h.unmount(); }
});

test('returning to the original account and credential does not revive focus after an auth transition', async () => {
  const h = fixture();
  try {
    const staleFrame = await chooseCurrent(h);
    h.account('other', 'other-token'); h.account('reader', 'reader-token');
    assert.equal(h.pendingFrames.length, 0);
    staleFrame(0); await h.flushFrames();
    assert.equal(h.inserted.length, 1); assert.deepEqual(h.focus, []);
    assert.equal(h.document.listenerCount, 0);
  } finally { h.unmount(); }
});

test('a changed actor cannot restore focus even if its credential string and rendered scope have not changed yet', async () => {
  const h = fixture();
  try {
    await chooseCurrent(h); h.account('other', 'reader-token', false);
    await h.flushFrames();
    assert.deepEqual(h.focus, []); assert.equal(h.document.listenerCount, 0);
  } finally { h.unmount(); }
});

test('unmount cancels queued focus and removes its listeners even if its callback was already delivered', async () => {
  const h = fixture();
  try {
    const staleFrame = await chooseCurrent(h);
    assert.ok(h.document.listenerCount > 0, 'Pending focus intent listeners must actually be registered');
    h.unmount();
    assert.equal(h.pendingFrames.length, 0); assert.equal(h.document.listenerCount, 0);
    staleFrame(0); await h.flushFrames();
    assert.deepEqual(h.focus, []); assert.equal(h.inserted.length, 1);
  } finally { h.unmount(); }
});
