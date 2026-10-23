import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/lib/password-policy.ts', import.meta.url), 'utf8');
const exports = {};
vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText, { exports, TextEncoder });
const { newPasswordValidationError: check } = exports;

test('new native passwords require twelve Unicode code points, including emoji', () => {
  for (const value of ['elevenchars', '😀'.repeat(11)]) assert.match(check(value), /12 characters/);
  for (const value of ['twelve chars', '😀'.repeat(12)]) assert.equal(check(value), null);
});

test('the bcrypt byte boundary accepts exactly 72 UTF-8 bytes without truncation', () => {
  for (const value of ['a'.repeat(72), 'å'.repeat(36), '😀'.repeat(18)]) assert.equal(check(value), null);
  for (const value of ['a'.repeat(73), 'å'.repeat(37), '😀'.repeat(19)]) assert.match(check(value), /too long/);
});

test('new passwords preserve spaces and combining characters rather than normalizing credentials', () => {
  assert.equal(check(' '.repeat(12)), null);
  assert.equal(check('e\u0301'.repeat(6)), null);
  assert.match(check('\u00e9'.repeat(6)), /12 characters/);
});

test('native password checks refuse null bytes and invalid UTF-8 surrogate strings', () => {
  assert.match(check('validpassword\0'), /null character/);
  assert.match(check('validpassword\ud800'), /Unicode/);
  assert.match(check('validpassword\udc00'), /Unicode/);
  assert.equal(check('validpassword😀'), null);
});

function formFixture(kind) {
  const slots = [], effects = [], calls = [];
  let cursor = 0, tree;
  const react = {
    useState(initial) { const id = cursor++; if (!(id in slots)) slots[id] = typeof initial === 'function' ? initial() : initial; return [slots[id], value => { slots[id] = typeof value === 'function' ? value(slots[id]) : value; }]; },
    useRef(initial) { const id = cursor++; return slots[id] ||= { current: initial }; },
    useEffect(callback, deps) { const id = cursor++, old = slots[id]; if (!old || deps.some((value, index) => value !== old[index])) { slots[id] = deps; effects.push(callback); } },
  };
  const jsx = (type, props) => ({ type, props: props || {} });
  const track = name => async (...args) => { calls.push([name, ...args]); };
  const accountApi = {
    resetPassword: track('reset'), changePassword: track('change'),
    security: async () => ({ mfa_enabled: false, email_verified: true }), sessions: async () => [],
  };
  const dependencies = {
    react, 'react/jsx-runtime': { jsx, jsxs: jsx }, 'react-dom': { createPortal: value => value }, qrcode: {},
    'lucide-react': new Proxy({}, { get: (_, value) => String(value) }),
    '../i18n': { t: value => value, useI18n() {}, currentLocale: () => 'en' },
    '../lib/api': { api: { getCurrentUserId: () => 'visitor', getMe: async () => ({ id: 'visitor', name: 'Visitor', email: 'visitor@example.com', session_scope: 'native' }), logout: () => calls.push(['logout']) } },
    '../lib/account-api': { accountApi }, '../lib/password-policy': { newPasswordValidationError: check },
    '../lib/useModalFocus': { useModalFocus() {} }, '../components/SSOConnections': {}, '../components/ScopedSessionNotice': {}, './Brand': {}, '../styles/account.css': {},
  };
  const path = kind === 'reset' ? '../src/components/AuthenticationFlow.tsx' : '../src/account/AccountPanel.tsx';
  const compiled = ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const exports = {};
  vm.runInNewContext(compiled, { exports, document: { body: {} }, requestAnimationFrame: callback => callback(), CustomEvent: class {}, window: { dispatchEvent() {} }, require: name => { assert.ok(name in dependencies, 'Known form dependency: ' + name); return dependencies[name]; } });
  function render() { cursor = 0; tree = kind === 'reset' ? exports.AuthenticationFlow({ kind: 'reset-password', token: 'private-fixture-link', onSuccess() {}, onBack() {} }) : exports.AccountPanel({ onClose() {} }); while (effects.length) effects.shift()(); }
  async function flush() { for (let index = 0; index < 8; index++) { await Promise.resolve(); render(); } }
  const content = node => typeof node === 'string' ? node : Array.isArray(node) ? node.map(content).join('') : content(node?.props?.children || '');
  function find(predicate) { const nodes = []; const visit = node => { if (Array.isArray(node)) return node.forEach(visit); if (!node || typeof node !== 'object') return; nodes.push(node); visit(node.props?.children); }; visit(tree); const found = nodes.find(predicate); assert.ok(found, 'Visible form control exists'); return found; }
  function field(id, value) { find(node => node.props.id === id).props.onChange({ target: { value } }); render(); }
  async function submit() { await find(node => node.type === 'form' && (kind === 'reset' || content(node).includes('Change password'))).props.onSubmit({ preventDefault() {} }); await flush(); }
  render();
  return { calls, flush, field, find, submit, content: () => content(tree), security() { find(node => node.type === 'button' && content(node) === 'Security').props.onClick(); render(); } };
}

for (const kind of ['reset', 'change']) test(kind + ' form blocks invalid new credentials locally, preserves input and accepts twelve emoji', async () => {
  const fixture = formFixture(kind); await fixture.flush();
  if (kind === 'change') { fixture.security(); fixture.field('account-current-password', 'legacy'); }
  const field = kind === 'reset' ? 'reset-new-password' : 'account-new-password', confirm = kind === 'reset' ? 'reset-confirm-password' : 'account-confirm-password';
  fixture.field(field, '😀'.repeat(6)); fixture.field(confirm, '😀'.repeat(6)); await fixture.submit();
  assert.equal(fixture.calls.some(call => ['reset', 'change'].includes(call[0])), false);
  assert.match(fixture.content(), /12 characters/);
  assert.equal(fixture.find(node => node.props.id === field).props.value, '😀'.repeat(6));
  const valid = '😀'.repeat(12);
  fixture.field(field, valid); fixture.field(confirm, valid);
  assert.equal(fixture.find(node => node.props.id === field).props.minLength, undefined);
  await fixture.submit();
  const request = fixture.calls.find(call => call[0] === kind);
  assert.ok(request); assert.equal(request[2], valid);
});
