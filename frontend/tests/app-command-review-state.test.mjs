import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

// These are component state/authority tests. The secure adapter is a dependency
// double, not evidence of real Matrix encryption or provider delivery.
function fixture({ response, commandPatch = {} } = {}) {
  const hooks = [], effects = [], calls = [], notices = [], listeners = new Map();
  let index = 0, dirty = true, tree, token = 'owner-token', actor = 'owner';
  const command = { key: 'installation:run', installation_id: 'installation', app_id: 'customer.app', app_name: 'Customer app',
    generation: 3, origin: 'https://customer.example', name: 'run', description: 'Reviewed action',
    arguments: [{ name: 'text', label: 'Reviewed text', type: 'text', max_length: 2000, required: true }],
    supports_secure_foreground: true, delivery: 'foreground', ...commandPatch };
  const react = {
    useId: () => 'review-state',
    useRef(value) { const slot = index++; return hooks[slot] ||= { current: value }; },
    useState(value) { const slot = index++; if (!(slot in hooks)) hooks[slot] = value; return [hooks[slot], next => { const value = typeof next === 'function' ? next(hooks[slot]) : next; if (!Object.is(value, hooks[slot])) { hooks[slot] = value; dirty = true; } }]; },
    useEffect(callback, deps) { const slot = index++, old = hooks[slot]; if (!old || deps.some((value, i) => !Object.is(value, old.deps[i]))) effects.push(() => { old?.cleanup?.(); hooks[slot] = { deps, cleanup: callback() }; }); },
  };
  const jsx = (type, props) => ({ type, props: props || {} });
  const dependencies = {
    react, 'react/jsx-runtime': { jsx, jsxs: jsx }, 'lucide-react': new Proxy({}, { get: (_, key) => key }),
    '../lib/api': { api: { getAccessToken: () => token, getCurrentUserId: () => actor } },
    '../i18n': { useI18n: () => ({ t: text => text }) }, './ScopedCollaborationInput': { ScopedCollaborationInput: 'collaboration-input' },
    '../lib/installed-app-commands': { installedAppCommandsApi: {
      list: async () => ({ commands: [command] }),
      foreground: async (...args) => { calls.push({ kind: 'foreground', args }); return response ? response() : { id: 'receipt', foreground: true, status: 'uncertain', preview: null }; },
      invoke: async () => { throw new Error('Must use explicit foreground transport'); },
    } },
    '../lib/secure-client': { getSecureMessaging: async () => ({ roomFor: async channel => ({ c: { getDeviceId: () => 'BOUND-DEVICE' } }),
      sendMessage: async (...args) => { calls.push({ kind: 'encrypted-publication', args }); } }) },
  };
  const exported = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/components/AppCommandReview.tsx', import.meta.url), 'utf8'),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX } }).outputText,
  { exports: exported, require: name => name.endsWith('.css') ? {} : dependencies[name], Set, crypto: { randomUUID: () => 'stable-request-id' },
    window: { addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: name => listeners.delete(name) } });
  const props = { workspaceId: 'workspace', channelId: 'channel', command, arguments: { text: 'Explicit device-selected text' }, onPendingChange: value => notices.push(value) };
  const visit = () => { const all = []; const walk = node => { if (Array.isArray(node)) node.forEach(walk); else if (node?.props) { all.push(node); walk(node.props.children); } }; walk(tree); return all; };
  const text = node => Array.isArray(node) ? node.map(text).join('') : node?.props ? text(node.props.children) : node == null || typeof node === 'boolean' ? '' : String(node);
  function render() { for (let count = 0; dirty && count < 50; count++) { dirty = false; index = 0; tree = exported.AppCommandReview(props); effects.splice(0).forEach(fn => fn()); } assert.equal(dirty, false); }
  async function settle() { for (let tick = 0; tick < 8; tick++) { await new Promise(resolve => setImmediate(resolve)); render(); } }
  function button(label) { const node = visit().find(node => node.type === 'button' && text(node).trim() === label); assert.ok(node, label); return node; }
  async function check(prefix, value = true) { const label = visit().find(node => node.type === 'label' && text(node).startsWith(prefix)); assert.ok(label, prefix); label.props.children.find(node => node?.type === 'input').props.onChange({ target: { checked: value } }); await settle(); }
  async function submit() { visit().find(node => node.type === 'form').props.onSubmit({ preventDefault() {} }); await settle(); }
  return { calls, notices, settle, check, submit, button, text: () => text(tree),
    value: () => visit().find(node => node.type === 'collaboration-input').props.value,
    storage(key) { listeners.get('storage')?.({ key }); render(); },
    edit(value) { visit().find(node => node.type === 'collaboration-input').props.onValueChange(value); render(); },
    changeActor() { token = 'other-token'; actor = 'other'; listeners.get('freecollab:auth-changed')?.(); render(); } };
}

test('secure arguments require both approvals, and edits revoke both before any relay', async () => {
  const h = fixture(); await h.settle(); assert.equal(h.notices.at(-1), true);
  await h.check('I reviewed these exact arguments'); await h.submit(); assert.equal(h.calls.length, 0);
  await h.check('For this request'); h.edit('Changed selected text');
  assert.equal(h.button('Approve and run command').props.disabled, true); await h.submit(); assert.equal(h.calls.length, 0);
  await h.check('I reviewed these exact arguments'); await h.check('For this request'); await h.submit();
  assert.equal(h.calls.length, 1); assert.equal(h.calls[0].args[5], 'BOUND-DEVICE');
});

test('uncertain foreground delivery remains pending until explicit acknowledgment and never offers a resend', async () => {
  const h = fixture(); await h.settle(); await h.check('I reviewed these exact arguments'); await h.check('For this request'); await h.submit();
  assert.equal(h.notices.at(-1), true); assert.equal(h.button('Approve and run command').props.disabled, true);
  h.button('I acknowledge the delivery outcome. Do not retry this action.').props.onClick(); await h.settle();
  assert.equal(h.notices.at(-1), false); await h.submit(); assert.equal(h.calls.length, 1);
});

test('a returned foreground preview stays private until its separate encrypted-publication review', async () => {
  const h = fixture({ response: () => ({ id: 'receipt', foreground: true, status: 'delivered', preview: 'Private provider answer' }) });
  await h.settle(); await h.check('I reviewed these exact arguments'); await h.check('For this request'); await h.submit();
  assert.equal(h.calls.filter(call => call.kind === 'encrypted-publication').length, 0); assert.ok(h.text().includes('Private provider answer'));
  await h.check('I reviewed this response'); h.button('Publish encrypted response').props.onClick(); await h.settle();
  const publication = h.calls.find(call => call.kind === 'encrypted-publication');
  assert.deepEqual(Array.from(publication.args.slice(0, 2)), ['channel', 'Private provider answer']);
  assert.equal(publication.args[5], 'app-response-receipt');
});

test('an account change while the provider is awaited cannot reveal its late private response', async () => {
  let release; const wait = new Promise(resolve => { release = resolve; });
  const h = fixture({ response: () => wait }); await h.settle(); await h.check('I reviewed these exact arguments'); await h.check('For this request'); await h.submit();
  assert.equal(h.calls.length, 1); h.changeActor(); release({ id: 'receipt', foreground: true, status: 'delivered', preview: 'LATE PRIVATE OUTPUT' }); await h.settle();
  assert.ok(!h.text().includes('LATE PRIVATE OUTPUT')); assert.equal(h.calls.filter(call => call.kind === 'encrypted-publication').length, 0);
});

test('cross-tab appearance or language changes preserve the draft while credential changes erase it', async () => {
  const h = fixture(); await h.settle();
  h.storage('freecollab.appearance:owner'); h.storage('freecollab.language:owner'); await h.settle();
  assert.equal(h.value(), 'Explicit device-selected text'); assert.equal(h.notices.at(-1), true);
  h.storage('freecollab_token'); await h.settle();
  assert.equal(h.value(), ''); assert.equal(h.button('Approve and run command').props.disabled, true);
});
