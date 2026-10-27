import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

function load(path, dependencies = {}, globals = {}) {
  const exports = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX },
  }).outputText, { exports, require: name => dependencies[name] || {}, ...globals });
  return exports;
}
const helpers = load('../src/lib/message-task-selection.ts');
const { readMessageTaskSelection, sameMessageTaskSelection, messageTaskToolbarPosition } = helpers;
const message = { id: 'message', channel_id: 'room', author_id: 'author', created_at: '2026-11-01T12:00:00Z', content: 'Background. Maja sends the drawing tomorrow.' };

function selected(source = message, text = 'Maja sends the drawing tomorrow.') {
  const wrapper = { dataset: { messageSourceId: source.id }, contains: value => value === root };
  const root = {
    nodeType: 1, isConnected: true, hidden: false,
    closest(selector) {
      if (selector === '[data-message-source-text]') return root;
      if (selector === '[data-message-source-id]') return wrapper;
      return root.hidden ? root : null;
    },
  };
  const node = { nodeType: 3, parentElement: root };
  const start = source.content.indexOf(text);
  const rect = { left: 40, top: 100, right: 340, bottom: 120, width: 300, height: 20 };
  function range() {
    let prefix = false;
    return { startContainer: node, endContainer: node, startOffset: start, endOffset: start + text.length,
      cloneRange: range, selectNodeContents() { prefix = true; }, setEnd() {},
      toString: () => prefix ? source.content.slice(0, start) : text, getBoundingClientRect: () => rect };
  }
  const active = range();
  return { root, node, rect, range: active, selection: { rangeCount: 1, isCollapsed: false, getRangeAt: () => active } };
}

test('only one exact message content range can become a selected task excerpt', () => {
  const fixture = selected();
  const excerpt = readMessageTaskSelection(fixture.selection, 'room', [message]);
  assert.equal(excerpt.messageId, message.id); assert.equal(excerpt.text, 'Maja sends the drawing tomorrow.');
  assert.equal(excerpt.start, 12); assert.equal(excerpt.end, message.content.length);
  assert.notEqual(excerpt.range, fixture.range, 'The retained range is a clone');
  assert.equal(readMessageTaskSelection(fixture.selection, 'different-room', [message]), null);
  assert.equal(readMessageTaskSelection(fixture.selection, 'room', [{ ...message, content: 'Different prefix. ' + excerpt.text }]), null);
  assert.equal(readMessageTaskSelection({ ...fixture.selection, rangeCount: 2 }, 'room', [message]), null);
});

test('cross-message, chrome, hidden, locked, deleted and detached selections are rejected', () => {
  const cross = selected(), other = selected({ ...message, id: 'other' });
  cross.range.endContainer = other.node;
  assert.equal(readMessageTaskSelection(cross.selection, 'room', [message]), null);
  const chrome = selected(); chrome.node.parentElement = { closest: () => null };
  assert.equal(readMessageTaskSelection(chrome.selection, 'room', [message]), null);
  const hidden = selected(); hidden.root.hidden = true;
  assert.equal(readMessageTaskSelection(hidden.selection, 'room', [message]), null);
  const detached = selected(); detached.root.isConnected = false;
  assert.equal(readMessageTaskSelection(detached.selection, 'room', [message]), null);
  const valid = selected();
  for (const replacement of [{ ...message, locked: true }, { ...message, deleted: true }]) assert.equal(readMessageTaskSelection(valid.selection, 'room', [replacement]), null);
  assert.equal(readMessageTaskSelection(valid.selection, 'room', [message, { ...message, edited_at: 'later' }]), null);
});

test('source identity, offsets and reference metadata are part of stale-selection checks', () => {
  const fixture = selected();
  const first = readMessageTaskSelection(fixture.selection, 'room', [message]);
  assert.equal(sameMessageTaskSelection(first, readMessageTaskSelection(fixture.selection, 'room', [message])), true);
  const changed = readMessageTaskSelection(fixture.selection, 'room', [{ ...message, references: [{ kind: 'person', id: 'person', start: 12, end: 17 }] }]);
  assert.equal(sameMessageTaskSelection(first, changed), false);
  assert.equal(sameMessageTaskSelection(first, { ...first, start: first.start + 1 }), false);
});

test('floating toolbar geometry stays within a mobile or desktop viewport', () => {
  for (const rect of [{ left: -80, top: 2, width: 200, bottom: 22 }, { left: 310, top: 590, width: 40, bottom: 610 }]) {
    const position = messageTaskToolbarPosition(rect, { width: 360, height: 640 }, { width: 200, height: 40 });
    assert.ok(position.left >= 8 && position.left + 200 <= 352);
    assert.ok(position.top >= 8 && position.top + 40 <= 632);
  }
});

function componentFixture() {
  const state = { token: 'session', userId: 'actor', selection: selected().selection, calls: [], props: { scopeKey: 'scope', channelId: 'room', messages: [message] } };
  const listeners = new Map(), hooks = [];
  let cursor = 0, dirty = true, effects = [], tree;
  const react = {
    useState(initial) { const index = cursor++; if (!hooks[index]) hooks[index] = { value: typeof initial === 'function' ? initial() : initial }; return [hooks[index].value, value => { const next = typeof value === 'function' ? value(hooks[index].value) : value; if (!Object.is(next, hooks[index].value)) { hooks[index].value = next; dirty = true; } }]; },
    useRef(initial) { const index = cursor++; if (!hooks[index]) hooks[index] = { current: initial }; return hooks[index]; },
    useEffect(callback, dependencies) { const index = cursor++, previous = hooks[index]; if (!previous || dependencies.some((value, i) => !Object.is(value, previous.dependencies?.[i]))) { hooks[index] = { dependencies, cleanup: previous?.cleanup }; effects.push(() => { hooks[index].cleanup?.(); hooks[index].cleanup = callback(); }); } },
  };
  react.useLayoutEffect = react.useEffect;
  const events = { addEventListener(name, callback) { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(callback); }, removeEventListener(name, callback) { listeners.get(name)?.delete(callback); } };
  const jsx = (type, props) => ({ type, props });
  const exported = load('../src/components/MessageTaskSelection.tsx', {
    react, 'react/jsx-runtime': { jsx, jsxs: jsx }, 'react-dom': { createPortal: (value, container) => { state.portal = container; return value; } },
    'lucide-react': { CheckSquare: 'check-square' }, '../i18n': { t: value => value, useI18n() {} },
    '../lib/api': { api: { getAccessToken: () => state.token, getCurrentUserId: () => state.userId } },
    '../lib/message-task-selection': helpers,
  }, { document: { ...events, body: {} }, window: { ...events, getSelection: () => state.selection, innerWidth: 360, innerHeight: 640 } });
  function render() {
    for (let count = 0; dirty && count < 30; count++) { dirty = false; cursor = 0; effects = []; tree = exported.MessageTaskSelection({ ...state.props, onCreateTask: (...args) => state.calls.push(args) }); for (const effect of effects) effect(); }
    assert.equal(dirty, false, 'Selection toolbar must settle');
    return tree;
  }
  function dispatch(name, event = {}) { for (const handler of listeners.get(name) || []) handler(event); render(); }
  render();
  return { state, dispatch, render, get tree() { return tree; }, update(props) { state.props = { ...state.props, ...props }; dirty = true; render(); }, unmount() { for (const hook of hooks) hook.cleanup?.(); } };
}

test('real toolbar handlers retain the range and open only one reviewed draft after click', () => {
  const fixture = componentFixture(); fixture.dispatch('selectionchange');
  assert.equal(fixture.tree.type, 'button'); assert.equal(fixture.state.calls.length, 0);
  let prevented = false; fixture.tree.props.onPointerDown({ preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  fixture.tree.props.onClick(); fixture.render();
  assert.equal(fixture.state.calls.length, 1); assert.equal(fixture.state.calls[0][0].id, message.id);
  assert.equal(fixture.state.calls[0][1], 'Maja sends the drawing tomorrow.');
  fixture.dispatch('mouseup'); assert.equal(fixture.tree, null, 'The used selection does not immediately reopen');
  fixture.unmount();
});

test('thread selections keep the toolbar inside their dialog focus boundary', () => {
  const fixture = componentFixture(), selection = selected(), dialog = {};
  const closest = selection.root.closest;
  selection.root.closest = selector => selector === '[role="dialog"], [aria-modal="true"]' ? dialog : closest(selector);
  fixture.state.selection = selection.selection;
  fixture.dispatch('selectionchange');
  assert.equal(fixture.tree.type, 'button');
  assert.equal(fixture.state.portal, dialog);
  fixture.unmount();
});

test('first Escape consumes the visible selection toolbar before the enclosing dialog', () => {
  const fixture = componentFixture(); fixture.dispatch('selectionchange');
  const event = { key: 'Escape', prevented: false, stopped: false, preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; } };
  fixture.dispatch('keydown', event);
  assert.equal(event.prevented, true); assert.equal(event.stopped, true); assert.equal(fixture.tree, null);
  const next = { ...event, prevented: false, stopped: false };
  fixture.dispatch('keydown', next);
  assert.equal(next.prevented, false); assert.equal(next.stopped, false);
  fixture.unmount();
});

test('queued toolbar callbacks reject an edited source, changed selection, scope or credential', () => {
  for (const change of ['message', 'selection', 'scope', 'token', 'auth-event']) {
    const fixture = componentFixture(); fixture.dispatch('selectionchange'); const click = fixture.tree.props.onClick;
    if (change === 'message') fixture.update({ messages: [{ ...message, edited_at: 'later' }] });
    if (change === 'selection') fixture.state.selection = selected(message, 'Background.').selection;
    if (change === 'scope') fixture.update({ scopeKey: 'different-scope' });
    if (change === 'token') fixture.state.token = 'changed-session';
    if (change === 'auth-event') fixture.dispatch('freecollab:auth-changed');
    click(); fixture.render(); assert.equal(fixture.state.calls.length, 0, change);
    fixture.unmount();
  }
});

test('Escape dismisses only the toolbar and detached source never opens a draft', () => {
  const fixture = componentFixture(); fixture.dispatch('keyup', { key: 'Shift' }); assert.equal(fixture.tree.type, 'button');
  fixture.dispatch('keyup', { key: 'Escape' }); assert.equal(fixture.tree, null); fixture.dispatch('mouseup'); assert.equal(fixture.tree, null);
  const replacement = selected(); fixture.state.selection = replacement.selection; fixture.dispatch('selectionchange');
  assert.equal(fixture.tree.type, 'button'); const click = fixture.tree.props.onClick; replacement.root.isConnected = false;
  click(); fixture.render(); assert.equal(fixture.state.calls.length, 0); fixture.unmount();
});
