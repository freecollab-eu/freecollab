import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const plain = value => JSON.parse(JSON.stringify(value));
const translate = (text, values = {}) => text.replace(/\{(\w+)\}/g, (all, key) => key in values ? values[key] : all);
const source = { id: 'selected', channel_id: 'channel', content: 'Reviewed native source: ' + 'ä'.repeat(300) + '\nVISIBLE TAIL 🧭', deleted: false };
function fixture({ prepare } = {}) {
  const slots = [], effects = [], calls = [], exports = {};
  let token = 'owner-token', index = 0, pending = true, tree, props = { workspaceId: 'workspace', channelId: 'channel' };
  const status = { globally_enabled: true, configured: true, consented: true, config_revision: 4, role: 'owner', provider: { endpoint: 'https://reviewed.example/v1/chat/completions', model: 'reviewed-model' } };
  const api = { getAccessToken: () => token, getCurrentUserId: () => 'owner', getChannel: async id => ({ id, role: 'admin', type: 'group' }), getChannelMembers: async () => [{ user_id: 'owner', name: 'Owner' }], getMessages: async () => [source, { ...source, id: 'omitted', content: 'UNSELECTED MESSAGE' }] };
  const appPlatformApi = {
    list: async () => ({ installations: [] }), cards: async () => ({ cards: [] }), drafts: async () => ({ drafts: [] }), recaps: async () => ({ schedules: [] }),
    generate: async (...args) => { calls.push({ kind: 'generate', args: plain(args) }); return { id: 'private-draft', kind: 'summary', status: 'draft', text: 'Reviewed answer [S1]', citations: [] }; },
    schedule: async (...args) => { calls.push({ kind: 'schedule', args: plain(args) }); },
  };
  const changed = (left, right) => !left || left.length !== right.length || left.some((value, n) => !Object.is(value, right[n]));
  const hooks = {
    useState(initial) { const n = index++; if (!(n in slots)) slots[n] = initial; return [slots[n], value => { const next = typeof value === 'function' ? value(slots[n]) : value; if (!Object.is(next, slots[n])) { slots[n] = next; pending = true; } }]; },
    useRef(initial) { const n = index++; return slots[n] ||= { current: initial }; },
    useEffect(callback, deps) { const n = index++; if (!slots[n] || changed(slots[n].deps, deps)) effects.push(() => { slots[n]?.cleanup?.(); slots[n] = { deps, cleanup: callback() }; }); },
  };
  const jsx = (type, props) => ({ type, props: props || {} });
  const dependencies = {
    react: hooks, 'react/jsx-runtime': { jsx, jsxs: jsx }, 'lucide-react': new Proxy({}, { get: (_, key) => key }), '../lib/api': { api },
    '../lib/app-platform-api': { appPlatformApi }, '../i18n': { t: translate, useI18n: () => ({}) }, '../lib/copilot-intents': { copilotProviderHost: value => new URL(value).hostname },
    '../lib/copilot-api': { copilotApi: { status: async () => plain(status) }, prepareCopilotSource: async choice => {
      calls.push({ kind: 'prepare', choice: plain(choice) });
      if (prepare) await prepare();
      return { ...choice.source, expected_text_sha256: 'a'.repeat(64) };
    } },
  };
  const code = ts.transpileModule(readFileSync(new URL('../src/components/ChannelApps.tsx', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  vm.runInNewContext(code, { exports, require: name => name.endsWith('.css') ? {} : dependencies[name] || (() => { throw new Error(name); })(), Set });
  function visit() { const all = []; function walk(node) { if (Array.isArray(node)) node.forEach(walk); else if (node?.props) { all.push(node); walk(node.props.children); } } walk(tree); return all; }
  const text = node => Array.isArray(node) ? node.map(text).join('') : node?.props ? text(node.props.children) : node == null || typeof node === 'boolean' ? '' : String(node);
  async function settle() { let idle = 0; for (let cycle = 0; cycle < 40; cycle++) { if (pending) { pending = false; index = 0; tree = exports.ChannelApps(props); effects.splice(0).forEach(effect => effect()); } for (let tick = 0; tick < 20; tick++) await Promise.resolve(); if (!pending && !effects.length) { if (++idle === 3) return; } else idle = 0; } throw new Error('Hooks did not settle'); }
  async function click(label) { const button = visit().find(node => node.type === 'button' && text(node).trim() === label); assert.ok(button, label); assert.ok(!button.props.disabled, 'Disabled ' + label); button.props.onClick(); await settle(); }
  async function change(label, value, type = 'input') { const row = visit().find(node => node.type === 'label' && text(node).includes(label)); assert.ok(row, label); const input = (Array.isArray(row.props.children) ? row.props.children : [row.props.children]).find(node => node?.type === type); assert.ok(input, label); input.props.onChange({ target: { checked: value, value } }); await settle(); }
  return { calls, exports, settle, click, change, text: () => text(tree), changeActor() { token = 'different-actor'; }, async openAI() { await settle(); await click('AI drafts & recaps'); }, async chooseSource() { await click('Choose native message context'); await change('Reviewed native source:', true); }, setProps(next) { props = { ...props, ...next }; pending = true; } };
}

test('app AI reviews the entire selected text and actual API warning without contacting a provider', async () => {
  const h = fixture(); await h.openAI(); await h.chooseSource();
  assert.ok(h.text().includes(source.content)); assert.ok(h.text().includes('VISIBLE TAIL 🧭'));
  assert.ok(h.text().includes('reviewed.example using reviewed-model'));
  assert.ok(h.text().includes('API use may incur charges. A chat subscription is separate.'));
  assert.equal(h.calls.length, 0);
  await h.change('Send these instructions', true); await h.click('Generate private draft');
  const request = h.calls.find(call => call.kind === 'generate').args[2];
  assert.equal(request.expected_config_revision, 4);
  assert.deepEqual(request.sources, [{ kind: 'message', id: 'selected', expected_text_sha256: 'a'.repeat(64) }]);
  assert.equal(request.disclose_to_provider, true); assert.ok(!JSON.stringify(request).includes('UNSELECTED'));
});

test('an actor change while source hashes prepare prevents the private draft request', async () => {
  let finish; const pending = new Promise(resolve => { finish = resolve; });
  const h = fixture({ prepare: () => pending }); await h.openAI(); await h.chooseSource();
  await h.change('Send these instructions', true); await h.click('Generate private draft');
  h.changeActor(); finish(); await h.settle();
  assert.equal(h.calls.filter(call => call.kind === 'generate').length, 0);
});

test('recap consent binds the reviewed revision and interval changes require a new acknowledgment', async () => {
  const h = fixture(); await h.openAI(); await h.change('I explicitly authorize future', true);
  await h.change('Every', '168', 'select');
  await assert.rejects(() => h.click('Schedule private recap'), /Disabled/);
  await h.change('I explicitly authorize future', true); await h.click('Schedule private recap');
  assert.deepEqual(h.calls.find(call => call.kind === 'schedule').args, ['workspace', 'channel', 168, 4]);
});

test('changed channels clear old source, instruction and destination review instead of carrying it forward', async () => {
  const h = fixture(); await h.openAI(); await h.chooseSource(); await h.change('Send these instructions', true);
  h.setProps({ channelId: 'another-channel' }); await h.settle();
  assert.ok(!h.text().includes(source.content)); assert.equal(h.calls.length, 0);
});

test('review validation rejects missing, ambiguous, foreign, deleted, duplicate and oversized sources', () => {
  const choose = fixture().exports.reviewedAppAiMessages;
  for (const messages of [[], [source, source], [{ ...source, channel_id: 'foreign' }], [{ ...source, deleted: true }], [{ ...source, locked: true }]]) assert.throws(() => choose(messages, ['selected'], 'channel'));
  assert.throws(() => choose([source], ['selected', 'selected'], 'channel'));
  assert.throws(() => choose([{ ...source, content: 'x'.repeat(24000) }], ['selected'], 'channel'));
  const bounded = { ...source, content: 'x'.repeat(23995) }; assert.equal(choose([bounded], ['selected'], 'channel')[0].content.length, 23995);
});
