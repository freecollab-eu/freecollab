import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

function compile(file, dependencies = {}) {
  const source = readFileSync(new URL('../src/' + file, import.meta.url), 'utf8');
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const exports = {};
  vm.runInNewContext(output, { exports, require: name => { if (name.endsWith('.css')) return {}; if (!(name in dependencies)) throw new Error('Missing fixture dependency ' + name); return dependencies[name]; }, window: { addEventListener() {}, removeEventListener() {}, setInterval: () => 1, clearInterval() {} }, document: { visibilityState: 'visible' }, crypto: { randomUUID: () => 'request' }, URL, Set, Map });
  return exports;
}
const presets = compile('lib/connection-presets.ts');
const plain = value => JSON.parse(JSON.stringify(value));

test('recipe filtering never borrows readiness from another provider, resource kind or channel', () => {
  const recipe = { provider: 'google', kind: 'calendar', channelId: 'selected' };
  for (const row of [{ provider: 'microsoft', kind: 'calendar', channel_id: 'selected' }, { provider: 'google', kind: 'files', channel_id: 'selected' }, { provider: 'google', kind: 'calendar', channel_id: 'other' }]) assert.equal(presets.matchingSyncRecipe(row, recipe), false);
  assert.equal(presets.matchingSyncRecipe({ provider: 'google', kind: 'calendar', channel_id: 'selected' }, recipe), true);
  assert.equal(presets.syncRecipeDraft(recipe, ['other']).channelId, '');
  assert.deepEqual(plain(presets.syncRecipeDraft(recipe, ['selected']).config), { calendar_id: 'primary' });
  assert.deepEqual(plain(presets.syncRecipeDraft({ provider: 'microsoft', kind: 'calendar' }, ['selected']).config), {});
  assert.equal(presets.syncRecipeDraft({ provider: 'caldav', kind: 'files' }, ['selected']).kind, 'calendar');
});

test('dirty snapshots ignore object/tool ordering but preserve content, scope and agent order changes', () => {
  assert.equal(presets.stableDraft({ config: { a: '1', b: '2' }, channel: 'one' }), presets.stableDraft({ channel: 'one', config: { b: '2', a: '1' } }));
  const agent = { name: 'copilot', instructions: '', tools: ['create_task', 'context_sources'] };
  const base = presets.copilotDraftSnapshot(false, 'https://api.openai.com/v1/chat/completions', 'gpt-4o-mini', [agent]);
  assert.equal(base, presets.copilotDraftSnapshot(false, 'https://api.openai.com/v1/chat/completions', 'gpt-4o-mini', [{ ...agent, tools: [...agent.tools].reverse() }]));
  assert.notEqual(base, presets.copilotDraftSnapshot(false, 'https://other.example/v1/chat/completions', 'gpt-4o-mini', [agent]));
  assert.notEqual(base, presets.copilotDraftSnapshot(false, 'https://api.openai.com/v1/chat/completions', 'gpt-4o-mini', [{ ...agent, instructions: 'Changed' }]));
});

function componentHarness(file, exportName, props, dependencies) {
  let index = 0, pending = true, tree;
  const slots = [], effects = [];
  const changed = (a, b) => !a || !b || a.length !== b.length || a.some((value, n) => !Object.is(value, b[n]));
  const hooks = {
    useState(initial) { const n = index++; if (!(n in slots)) slots[n] = typeof initial === 'function' ? initial() : initial; return [slots[n], value => { const next = typeof value === 'function' ? value(slots[n]) : value; if (!Object.is(next, slots[n])) { slots[n] = next; pending = true; } }]; },
    useRef(initial) { const n = index++; return slots[n] ||= { current: initial }; },
    useCallback(callback, deps) { const n = index++; if (!slots[n] || changed(slots[n].deps, deps)) slots[n] = { callback, deps }; return slots[n].callback; },
    useEffect(callback, deps) { const n = index++; if (!slots[n] || changed(slots[n].deps, deps)) effects.push(() => { slots[n]?.cleanup?.(); slots[n] = { deps, cleanup: callback() }; }); },
  };
  const jsx = (type, props) => ({ type, props: props || {} });
  const fn = compile(file, { react: hooks, 'react/jsx-runtime': { jsx, jsxs: jsx, Fragment: 'fragment' }, 'lucide-react': new Proxy({}, { get: (_, name) => name }), '../i18n': { t: value => value, useI18n: () => ({ t: value => value }) }, '../lib/connection-presets': presets, ...dependencies })[exportName];
  async function settle() { for (let cycle = 0; cycle < 30; cycle++) { if (pending) { pending = false; index = 0; tree = fn(props); for (const effect of effects.splice(0)) effect(); } for (let microtask = 0; microtask < 8; microtask++) await Promise.resolve(); if (!pending && !effects.length) { await Promise.resolve(); if (!pending) return; } } throw new Error('Fixture did not settle'); }
  const nodes = () => { const result = []; function visit(node) { if (Array.isArray(node)) node.forEach(visit); else if (node && typeof node === 'object' && node.props) { result.push(node); visit(node.props.children); } } visit(tree); return result; };
  const field = label => { const node = nodes().find(node => node.type === 'label' && (Array.isArray(node.props.children) ? node.props.children[0] : node.props.children) === label); assert.ok(node, 'Missing label ' + label); return node.props.children.find(child => child?.type === 'input' || child?.type === 'select' || child?.type === 'textarea'); };
  return { settle, nodes, field, setProps(next) { props = { ...props, ...next }; pending = true; }, unmount() { slots.forEach(slot => slot?.cleanup?.()); } };
}
const channel = { id: 'selected', workspace_id: 'workspace', name: 'Selected', role: 'admin', secure: false, type: 'public' };

test('AI setup opens a clean owner draft without context reads; changing endpoint clears typed secrets', async () => {
  const dirty = [], guards = [], requests = [];
  const status = { globally_enabled: true, configured: false, consented: false, config_revision: 0, provider: null, agents: [], conversations: [] };
  const h = componentHarness('components/CopilotPanel.tsx', 'CopilotPanel', { workspaceId: 'workspace', channelId: 'selected', isAdmin: true, setupMode: true, onDirtyChange: value => dirty.push(value), onGuardChange: value => guards.push(value) }, {
    '../lib/api': { api: { getAccessToken: () => 'actor' } }, '../lib/files-api': {},
    '../lib/copilot-api': { copilotApi: { status: async () => { requests.push('status'); return status; } }, copilotChannelIsSecure: async () => { throw new Error('Setup must not read channel crypto'); } },
  });
  await h.settle(); assert.equal(dirty.at(-1), false); assert.deepEqual(requests, ['status']);
  assert.equal(h.nodes().find(node => node.type === 'details').props.open, true);
  assert.equal(h.field('AI provider preset').props.value, 'openai-mini');
  assert.ok(!h.nodes().some(node => node.props.children === 'Choose messages, documents and files'));
  for (const [label, value] of [['Provider API key', 'private-api-key'], ['Current password', 'private-password'], ['Authenticator or recovery code', '123456']]) h.field(label).props.onChange({ target: { value } });
  await h.settle(); assert.equal(dirty.at(-1), true);
  h.field('Chat completions endpoint').props.onChange({ target: { value: 'https://ai.customer.example/v1/chat/completions' } });
  await h.settle(); for (const label of ['Provider API key', 'Current password', 'Authenticator or recovery code']) assert.equal(h.field(label).props.value, '');
  assert.deepEqual(requests, ['status']); assert.equal(guards.at(-1), null);
  h.unmount(); assert.equal(dirty.at(-1), false);
});

test('sync setup starts a clean selected-calendar draft and protects edits from recipe changes', async () => {
  const dirty = [], writes = [];
  const h = componentHarness('components/SyncSettings.tsx', 'SyncSettings', { workspaceId: 'workspace', channels: [channel], security: { mfa_enabled: false }, setupMode: true, initialProvider: 'google', initialKind: 'calendar', initialChannelId: 'selected', onGuardChange() {}, onDirtyChange: value => dirty.push(value) }, {
    '../lib/api': { api: { getAccessToken: () => 'actor' } }, '../lib/enterprise-api': { syncApi: { list: async () => ({ connections: [], enabled: true, callback_url: 'https://app.example/callback' }), create: async body => writes.push(body) } },
    './EnterpriseControls': { EnterpriseAlerts: 'alerts', NativeConfirmation: 'confirmation', enterpriseDate: value => value, enterpriseFailure: value => String(value) },
  });
  await h.settle(); const input = id => h.nodes().find(node => node.props.id === id);
  assert.equal(input('sync-provider').props.value, 'google'); assert.equal(input('sync-provider').props.disabled, true);
  assert.equal(input('sync-kind').props.value, 'calendar'); assert.equal(input('sync-channel').props.value, 'selected');
  assert.equal(input('sync-calendar-id').props.value, 'primary'); assert.equal(dirty.at(-1), false); assert.deepEqual(writes, []);
  input('sync-client-secret').props.onChange({ target: { value: 'typed-secret' } }); await h.settle(); assert.equal(dirty.at(-1), true);
  input('sync-client-id').props.onChange({ target: { value: 'customer-app' } }); await h.settle(); assert.equal(input('sync-client-secret').props.value, '');
  input('sync-name').props.onChange({ target: { value: 'My edited calendar' } }); await h.settle();
  h.setProps({ initialProvider: 'microsoft' }); await h.settle();
  assert.equal(input('sync-name').props.value, 'My edited calendar'); assert.equal(input('sync-provider').props.value, 'google'); assert.equal(dirty.at(-1), true);
  h.unmount(); assert.equal(dirty.at(-1), false);
});

test('AI setup reports save busy immediately and clears its dirty guard only after the real save finishes', async () => {
  const dirty = [], guards = [], writes = [];
  let finishSave, saved = false;
  const h = componentHarness('components/CopilotPanel.tsx', 'CopilotPanel', { workspaceId: 'workspace', isAdmin: true, setupMode: true, onDirtyChange: value => dirty.push(value), onGuardChange: value => guards.push(value) }, {
    '../lib/api': { api: { getAccessToken: () => 'actor' } }, '../lib/files-api': {},
    '../lib/copilot-api': { copilotApi: {
      status: async () => ({ globally_enabled: true, configured: false, consented: false, config_revision: saved ? 1 : 0, provider: saved ? { endpoint: 'https://api.openai.com/v1/chat/completions', model: 'gpt-4o-mini' } : null, agents: [], conversations: [] }),
      configure: async (workspace, body) => { writes.push({ workspace, body }); await new Promise(resolve => { finishSave = resolve; }); saved = true; },
    } },
  });
  await h.settle(); h.field('Provider API key').props.onChange({ target: { value: 'customer-key' } }); h.field('Current password').props.onChange({ target: { value: 'current-password' } }); await h.settle();
  const save = h.nodes().find(node => node.type === 'button' && node.props.children === 'Save provider and agents');
  assert.equal(save.props.disabled, false); save.props.onClick(); assert.equal(guards.at(-1), 'busy');
  await h.settle(); assert.equal(dirty.at(-1), true); assert.equal(writes.length, 1); assert.equal(writes[0].workspace, 'workspace'); assert.equal(writes[0].body.api_key, 'customer-key');
  finishSave(); await h.settle(); await h.settle(); assert.equal(guards.at(-1), null); assert.equal(dirty.at(-1), false); assert.equal(h.field('Provider API key').props.value, '');
  h.unmount();
});

test('normal AI panel keeps its opt-in and conversation controls while provider settings stay collapsed', async () => {
  let cryptoReads = 0;
  const h = componentHarness('components/CopilotPanel.tsx', 'CopilotPanel', { workspaceId: 'workspace', channelId: 'selected', isAdmin: true }, {
    '../lib/api': { api: { getAccessToken: () => 'actor' } }, '../lib/files-api': {},
    '../lib/copilot-api': { copilotApi: { status: async () => ({ globally_enabled: true, configured: true, consented: true, config_revision: 1, provider: { endpoint: 'https://api.openai.com/v1/chat/completions', model: 'gpt-4o-mini' }, agents: [{ name: 'copilot', instructions: '', tools: [] }], conversations: [] }) }, copilotChannelIsSecure: async () => { cryptoReads++; return false; } },
  });
  await h.settle(); assert.equal(cryptoReads, 1);
  assert.equal(h.nodes().find(node => node.type === 'details').props.open, false);
  assert.ok(h.nodes().some(node => node.props.children === 'I opt in to this provider configuration for my account.'));
  assert.ok(h.nodes().some(node => node.props.children === 'Private conversation'));
  assert.ok(!h.nodes().some(node => node.type === 'label' && node.props.children?.[0] === 'AI provider preset'));
  h.unmount();
});
