import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

// Exercise the widget methods without a browser or an additional test dependency.
// innerHTML throws so an accidental return to HTML message rendering fails here.
class Element {
  children = [];
  textContent = '';
  style = {};
  value = '';
  disabled = false;
  scrollHeight = 0;
  set innerHTML(_) { throw new Error('Untrusted content must not use innerHTML'); }
  appendChild(child) { this.children.push(child); }
  replaceChildren() { this.children = []; }
  remove() { this.removed = true; }
}

const source = readFileSync(new URL('../src/widget.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;

function fixture() {
  const exports = {};
  const timers = [];
  const context = vm.createContext({
    exports, window: {}, document: { createElement: () => new Element() },
    AbortController, setTimeout: (fn) => { timers.push(fn); return timers.length; },
    clearTimeout: () => {},
  });
  vm.runInContext(compiled, context);
  const messages = new Element();
  const input = new Element();
  const button = new Element();
  const status = new Element();
  const badge = new Element();
  const elements = {
    '.freecollab-messages': messages, '.freecollab-input': input,
    '.freecollab-send-btn': button, '.freecollab-status': status, '.freecollab-badge': badge,
  };
  const errors = [];
  const notifications = [];
  const widget = Object.create(exports.FreeCollabWidget.prototype);
  Object.assign(widget, {
    config: { apiUrl: 'https://chat.example.com', channelId: 'channel', userToken: 'test-token',
      onError: (error) => errors.push(error), onMessage: (message) => notifications.push(message) },
    container: { querySelector: (selector) => elements[selector], remove() {} },
    generation: 0, destroyed: false, sending: false, seenMessageIds: new Set(),
    currentUserId: null, unreadCount: 0, isOpen: false, pollController: null,
  });
  return { widget, context, exports, messages, input, button, status, errors, notifications, timers };
}

const message = (id = 'm1', content = 'Hello', author_id = 'other') => ({
  id, content, author_id, created_at: '2026-10-20T12:00:00Z',
});

test('module exports retain the script-tag init API when assigned to the bundle global', () => {
  const { context, exports } = fixture();
  // Vite's IIFE assigns its module exports to window.FreeCollab after evaluation.
  context.window.FreeCollab = exports;
  assert.equal(typeof context.window.FreeCollab.init, 'function');
  assert.equal(typeof context.window.FreeCollab.FreeCollabWidget, 'function');
});

test('received HTML is rendered as text and duplicate deliveries are ignored', () => {
  const { widget, messages, notifications } = fixture();
  const incoming = message('xss', '<img src=x onerror=alert(1)>');
  widget.receiveMessage(incoming);
  widget.receiveMessage(incoming);
  assert.equal(messages.children.length, 1);
  assert.equal(messages.children[0].textContent, incoming.content);
  assert.equal(widget.unreadCount, 1);
  assert.equal(notifications.length, 1);
});

test('missing credentials retain the draft and do not claim delivery', async () => {
  const { widget, messages, input, errors, context } = fixture();
  widget.config.userToken = '';
  input.value = 'Unsent';
  context.fetch = () => assert.fail('No request should be sent without credentials');
  await widget.sendMessage();
  assert.equal(input.value, 'Unsent');
  assert.equal(messages.children.length, 0);
  assert.equal(errors.length, 1);
});

test('HTTP failure retains draft, restores send button and reports failure', async () => {
  const { widget, messages, input, button, errors, context } = fixture();
  input.value = 'Please retry';
  context.fetch = async () => ({ ok: false, status: 403 });
  await widget.sendMessage();
  assert.equal(input.value, 'Please retry');
  assert.equal(messages.children.length, 0);
  assert.equal(button.disabled, false);
  assert.equal(errors.length, 1);
});

test('send displays submitted plaintext only after acknowledgement', async () => {
  const { widget, messages, input, context } = fixture();
  input.value = '<script>untrusted</script>';
  let resolve;
  context.fetch = () => new Promise((done) => { resolve = done; });
  const sending = widget.sendMessage();
  assert.equal(messages.children.length, 0);
  assert.equal(input.value, '<script>untrusted</script>');
  resolve({ ok: true, json: async () => message('saved', 'stored-ciphertext', 'self') });
  await sending;
  assert.equal(messages.children[0].textContent, '<script>untrusted</script>');
  assert.equal(input.value, '');
  assert.equal(widget.seenMessageIds.has('saved'), true);
});

test('a channel change discards an old in-flight send result', async () => {
  const { widget, messages, input, context } = fixture();
  input.value = 'Old channel draft';
  let resolve;
  context.fetch = () => new Promise((done) => { resolve = done; });
  const sending = widget.sendMessage();
  ++widget.generation;
  resolve({ ok: true, json: async () => message() });
  await sending;
  assert.equal(messages.children.length, 0);
  assert.equal(input.value, 'Old channel draft');
});

test('polling authenticates, orders history and deduplicates subsequent fetches', async () => {
  const { widget, context, messages, timers } = fixture();
  const requests = [];
  context.fetch = async (url, options) => {
    requests.push({ url, options });
    return { ok: true, json: async () => url.endsWith('/auth/me')
      ? { id: 'self' }
      : [message('new', 'second', 'self'), message('old', '<b>first</b>')] };
  };
  await widget.pollMessages(0);
  await widget.pollMessages(0);
  assert.equal(requests.filter(({ url }) => url.endsWith('/auth/me')).length, 1);
  assert.equal(requests[0].options.headers.Authorization, 'Bearer test-token');
  assert.equal(messages.children.length, 2);
  assert.equal(messages.children[0].textContent, '<b>first</b>');
  assert.equal(messages.children[1].className, 'freecollab-message sent');
  assert.equal(widget.unreadCount, 0);
  assert.equal(timers.length, 2);
});

test('destroy aborts polling and prevents future rendering', () => {
  const { widget, messages } = fixture();
  const controller = new AbortController();
  widget.pollController = controller;
  widget.destroy();
  widget.receiveMessage(message());
  assert.equal(controller.signal.aborted, true);
  assert.equal(messages.children.length, 0);
});
