import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const plain = value => JSON.parse(JSON.stringify(value));
const translation = (value, variables = {}) => value.replace(/\{([A-Za-z][A-Za-z0-9_]*)\}/g, (all, key) => key in variables ? String(variables[key]) : all);
function fixture({ secure = false, consented = true, configured = true, refs, document = false, response } = {}) {
  let token = 'owner-token', index = 0, pending = true, tree, props;
  const slots = [], effects = [], listeners = new Map(), calls = [], applied = [];
  let state = { globally_enabled: true, configured, consented, config_revision: 4, provider: configured ? { endpoint: 'https://api.customer.example/v1/chat/completions', model: 'reviewed-model' } : null, agents: [{ name: 'helper', tools: [] }], conversations: [{ id: 'central-history-must-not-be-reused', channel_id: 'channel' }], role: 'member' };
  let choices = [{ key: 'message:m1', source: { kind: 'message', id: 'm1' }, label: 'Chosen message', preview: 'Reviewed source text', message: { id: 'm1', channel_id: 'channel', content: 'Reviewed source text', edited_at: null } }, { key: 'message:omitted', source: { kind: 'message', id: 'omitted' }, label: 'Omitted', preview: 'UNSELECTED SECRET', message: { id: 'omitted', channel_id: 'channel', content: 'UNSELECTED SECRET' } }];
  let documentText = 'Verified selected document body';
  if (document) choices = [{ key: 'shared_document:d1', source: { kind: 'shared_document', id: 'd1' }, label: 'Chosen document', preview: 'Chosen document', document: { id: 'd1', channel_id: 'channel', version: 2, archived: false } }];
  const api = { getAccessToken: () => token };
  const copilotApi = {
    status: async () => { calls.push({ kind: 'status' }); return plain(state); },
    consent: async (_, revision, enabled) => { calls.push({ kind: 'consent', revision, enabled }); state.consented = enabled; },
    create: async (_, channel) => { calls.push({ kind: 'create', channel }); return { id: 'owned-one-off', revision: 0 }; },
    send: async (_, conversation, payload) => { calls.push({ kind: 'send', conversation, payload: plain(payload) }); return response ? await response() : { text: 'A reviewed suggestion [S1].', proposals: [] }; },
    reset: async (_, conversation) => { calls.push({ kind: 'reset', conversation }); },
    history: async () => { throw new Error('Inline assistance must not read central history'); },
    approve: async () => { throw new Error('Inline assistance must never approve a tool'); },
  };
  const changed = (left, right) => !left || !right || left.length !== right.length || left.some((value, n) => !Object.is(value, right[n]));
  const hooks = {
    useId() { return 'fixture-' + index++; },
    useState(initial) { const n = index++; if (!(n in slots)) slots[n] = typeof initial === 'function' ? initial() : initial; return [slots[n], value => { const next = typeof value === 'function' ? value(slots[n]) : value; if (!Object.is(next, slots[n])) { slots[n] = next; pending = true; } }]; },
    useRef(initial) { const n = index++; return slots[n] ||= { current: initial }; },
    useEffect(callback, deps) { const n = index++; if (!slots[n] || changed(slots[n].deps, deps)) effects.push(() => { slots[n]?.cleanup?.(); slots[n] = { deps, cleanup: callback() }; }); },
  };
  const jsx = (type, props) => ({ type, props: props || {} });
  const source = readFileSync(new URL('../src/components/ContextualCopilot.tsx', import.meta.url), 'utf8');
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const exports = {};
  const dependencies = { react: hooks, 'react/jsx-runtime': { jsx, jsxs: jsx }, 'lucide-react': new Proxy({}, { get: (_, name) => name }), '../lib/api': { api }, '../i18n': { useI18n: () => ({ t: translation }) }, '../lib/copilot-intents': { copilotProviderHost: endpoint => endpoint ? new URL(endpoint).hostname : null }, '../lib/copilot-api': { copilotApi, copilotChannelIsSecure: async () => secure, loadCopilotReferencedChoices: async () => { calls.push({ kind: 'sources' }); return { choices: plain(choices), secure }; }, prepareCopilotSource: async choice => { calls.push({ kind: 'prepare', id: choice.source.id }); return choice.document ? { ...choice.source, revision: choice.document.version, text: documentText, ...(secure ? { event_ids: ['$verified'] } : {}) } : { ...choice.source, ...(secure ? { text: choice.message.content, event_ids: [choice.message.id] } : {}) }; } } };
  vm.runInNewContext(output, { exports, require: name => name.endsWith('.css') ? {} : dependencies[name] || (() => { throw new Error('Unexpected dependency ' + name); })(), window: { addEventListener(name, fn) { listeners.set(name, fn); }, removeEventListener(name) { listeners.delete(name); } }, crypto: { randomUUID: () => 'explicit-request-id' }, URL, Set });
  props = { workspaceId: 'workspace', channelId: 'channel', contextKey: 'exact-target-draft', contextLabel: 'This message', actions: [{ id: 'reply', label: 'Draft a reply', prompt: 'Draft a reply from the selected source.' }], sourceRefs: refs || [{ kind: document ? 'shared_document' : 'message', id: document ? 'd1' : 'm1' }], onApply: (text, key) => applied.push({ text, key }) };
  async function settle() { let idle = 0; for (let cycle = 0; cycle < 45; cycle++) { if (pending) { idle = 0; pending = false; index = 0; tree = exports.ContextualCopilot(props); for (const effect of effects.splice(0)) effect(); } for (let microtask = 0; microtask < 20; microtask++) await Promise.resolve(); if (!pending && !effects.length) { if (++idle >= 4) return; } else idle = 0; } throw new Error('Fixture did not settle'); }
  function nodes() { const found = []; function visit(node) { if (Array.isArray(node)) node.forEach(visit); else if (node && typeof node === 'object' && node.props) { found.push(node); visit(node.props.children); } } visit(tree); return found; }
  function text(node) { if (Array.isArray(node)) return node.map(text).join(''); if (node && typeof node === 'object') return text(node.props?.children); return node == null || typeof node === 'boolean' ? '' : String(node); }
  const button = name => { const found = nodes().find(node => node.type === 'button' && text(node).trim() === name); assert.ok(found, 'Missing button ' + name); return found; };
  async function click(name) { const node = button(name); assert.ok(!node.props.disabled, 'Button disabled: ' + name); node.props.onClick(); await settle(); }
  async function check(label, value = true) { const row = nodes().find(node => node.type === 'label' && text(node).includes(label)); assert.ok(row, 'Missing checkbox ' + label); const input = row.props.children.find(node => node?.type === 'input'); input.props.onChange({ target: { checked: value } }); await settle(); }
  return { calls, applied, exports, settle, nodes, button, click, check, text: () => text(tree), async open() { await settle(); const trigger = nodes().find(node => node.type === 'button' && node.props.className === 'fc-contextual-copilot-trigger'); assert.ok(trigger, 'Missing concrete contextual action'); assert.ok(!trigger.props.disabled); trigger.props.onClick(); await settle(); }, setState(next) { state = { ...state, ...next }; }, setChoices(next) { choices = next; }, setDocumentText(next) { documentText = next; }, setProps(next) { props = { ...props, ...next }; pending = true; }, changeActor() { token = 'different-actor'; listeners.get('freecollab:auth-changed')?.(); }, rawChoices: () => plain(choices) };
}

test('opening inline AI makes no provider request, reuse of central history, consent change or write', async () => {
  const h = fixture({ consented: false }); await h.open();
  assert.deepEqual(h.calls.map(call => call.kind), ['status', 'sources', 'prepare']);
  assert.equal(h.applied.length, 0); assert.ok(h.text().includes('api.customer.example')); assert.ok(h.text().includes('reviewed-model'));
  await h.check('I opt in'); assert.deepEqual(h.calls.find(call => call.kind === 'consent'), { kind: 'consent', revision: 4, enabled: true });
  assert.equal(h.calls.filter(call => call.kind === 'send' || call.kind === 'create').length, 0);
});

test('the entry describes its actual action while an external activation opens review without a provider request', async () => {
  const h = fixture(); await h.settle();
  const trigger = h.nodes().find(node => node.type === 'button' && node.props.className === 'fc-contextual-copilot-trigger');
  assert.equal(h.text(), 'Draft a reply');
  assert.equal(trigger.props['aria-label'], 'Draft a reply for This message');
  assert.equal(h.calls.length, 0);
  h.setProps({ hideTrigger: true, activation: { actionId: 'reply', key: 1 } }); await h.settle();
  assert.ok(!h.nodes().some(node => node.props.className === 'fc-contextual-copilot-trigger'));
  assert.ok(h.nodes().some(node => node.type === 'h3' && node.props.children === 'Draft a reply'));
  assert.deepEqual(h.calls.map(call => call.kind), ['status', 'sources', 'prepare']);
  assert.equal(h.button('Send to provider').props.disabled, true);
  assert.equal(h.applied.length, 0);
});

test('unknown and ambiguous action activations never fall back or load selected content', async () => {
  for (const actions of [[{ id: 'reply', label: 'Draft a reply', prompt: 'Only this action.' }], [{ id: 'reply', label: 'One', prompt: 'One' }, { id: 'reply', label: 'Two', prompt: 'Two' }]]) {
    const h = fixture();
    h.setProps({ actions, hideTrigger: true, activation: { actionId: actions.length === 1 ? 'unknown' : 'reply', key: 1 } }); await h.settle();
    assert.equal(h.calls.length, 0);
    assert.ok(!h.nodes().some(node => node.type === 'section'));
    assert.equal(h.applied.length, 0);
  }
});

test('external action changes discard the prior answer and its disclosure instead of sending the next action', async () => {
  const h = fixture();
  h.setProps({ actions: [{ id: 'reply', label: 'Draft a reply', prompt: 'Reply to selected source.' }, { id: 'next_steps', label: 'Suggest next steps', prompt: 'Suggest only reviewed next steps.' }], hideTrigger: true, activation: { actionId: 'reply', key: 1 } });
  await h.settle(); await h.check('Send this request'); await h.click('Send to provider');
  assert.ok(h.text().includes('A reviewed suggestion [S1].'));
  h.setProps({ activation: { actionId: 'next_steps', key: 2 } }); await h.settle();
  assert.ok(!h.text().includes('A reviewed suggestion [S1].'));
  assert.ok(h.nodes().some(node => node.type === 'h3' && node.props.children === 'Suggest next steps'));
  assert.equal(h.button('Send to provider').props.disabled, true);
  assert.equal(h.calls.filter(call => call.kind === 'send').length, 1);
  assert.equal(h.applied.length, 0);
});

test('closing an externally activated review does not reopen until a new activation key', async () => {
  const h = fixture(); let closed = 0;
  h.setProps({ hideTrigger: true, activation: { actionId: 'reply', key: 1 }, onClose: () => closed++ }); await h.settle();
  const close = h.nodes().find(node => node.type === 'button' && node.props['aria-label'] === 'Close AI help');
  close.props.onClick(); await h.settle();
  assert.equal(closed, 1); assert.ok(!h.nodes().some(node => node.type === 'section'));
  h.setProps({ contextLabel: 'Same reviewed message' }); await h.settle();
  assert.ok(!h.nodes().some(node => node.type === 'section'));
  h.setProps({ activation: { actionId: 'reply', key: 2 } }); await h.settle();
  assert.ok(h.nodes().some(node => node.type === 'section'));
  assert.equal(h.calls.filter(call => call.kind === 'send' || call.kind === 'create').length, 0);
});

test('restoring a temporarily unavailable action cannot reopen a dismissed activation', async () => {
  const h = fixture(), actions = [{ id: 'reply', label: 'Draft a reply', prompt: 'Reply to selected source.' }];
  h.setProps({ actions, hideTrigger: true, activation: { actionId: 'reply', key: 1 } }); await h.settle();
  h.nodes().find(node => node.type === 'button' && node.props['aria-label'] === 'Close AI help').props.onClick(); await h.settle();
  const reads = h.calls.length;
  h.setProps({ actions: [] }); await h.settle();
  h.setProps({ actions }); await h.settle();
  assert.ok(!h.nodes().some(node => node.type === 'section'));
  assert.equal(h.calls.length, reads);
  h.setProps({ activation: { actionId: 'reply', key: 2 } }); await h.settle();
  assert.ok(h.nodes().some(node => node.type === 'section'));
  assert.equal(h.calls.filter(call => call.kind === 'create' || call.kind === 'send').length, 0);
});

test('an invalid activation is consumed and replacing its action does not create a new user request', async () => {
  const h = fixture();
  h.setProps({ actions: [], hideTrigger: true, activation: { actionId: 'reply', key: 1 } }); await h.settle();
  h.setProps({ actions: [{ id: 'reply', label: 'Draft a reply', prompt: 'Reply.' }] }); await h.settle();
  assert.ok(!h.nodes().some(node => node.type === 'section'));
  h.setProps({ actions: [{ id: 'summary', label: 'Summarize this message', prompt: 'Summarize.' }], activation: { actionId: 'summary', key: 1 } }); await h.settle();
  assert.ok(!h.nodes().some(node => node.type === 'section'));
  assert.equal(h.calls.length, 0);
  h.setProps({ activation: { actionId: 'summary', key: 2 } }); await h.settle();
  assert.ok(h.nodes().some(node => node.type === 'section'));
  assert.equal(h.calls.filter(call => call.kind === 'create' || call.kind === 'send').length, 0);
});

test('Escape closes its own review before surrounding work, while composition keeps it open', async () => {
  const h = fixture(); let closed = 0, prevented = 0, stopped = 0;
  h.setProps({ hideTrigger: true, activation: { actionId: 'reply', key: 1 }, onClose: () => closed++ }); await h.settle();
  const panel = h.nodes().find(node => node.type === 'section');
  const event = composing => ({ key: 'Escape', defaultPrevented: false, nativeEvent: { isComposing: composing }, keyCode: composing ? 229 : 27, preventDefault: () => prevented++, stopPropagation: () => stopped++ });
  panel.props.onKeyDown(event(true)); await h.settle();
  assert.equal(closed, 0); assert.equal(prevented, 0); assert.equal(stopped, 0);
  assert.ok(h.nodes().some(node => node.type === 'section'));
  panel.props.onKeyDown(event(false)); await h.settle();
  assert.equal(closed, 1); assert.equal(prevented, 1); assert.equal(stopped, 1);
  assert.ok(!h.nodes().some(node => node.type === 'section'));
  assert.equal(h.calls.filter(call => call.kind === 'send' || call.kind === 'create').length, 0);
});

test('an honest unconfigured action review still names its purpose and never fabricates an answer', async () => {
  const h = fixture({ configured: false });
  h.setProps({ hideTrigger: true, activation: { actionId: 'reply', key: 1 } }); await h.settle();
  assert.ok(h.text().includes('Draft a reply'));
  assert.ok(h.text().includes('A workspace owner must enable and connect an AI provider'));
  assert.equal(h.calls.filter(call => call.kind === 'send' || call.kind === 'create' || call.kind === 'consent').length, 0);
  assert.equal(h.applied.length, 0);
});

test('returned citations list the exact reviewed sources without inventing links or including unselected content', async () => {
  const h = fixture(); await h.open(); await h.check('Send this request'); await h.click('Send to provider');
  const citations = h.nodes().find(node => node.props.className === 'fc-contextual-copilot-citations');
  assert.ok(citations);
  assert.ok(h.text().includes('[S1] Chosen message'));
  assert.ok(!h.text().includes('UNSELECTED SECRET'));
  assert.ok(!h.nodes().some(node => node.type === 'a'));
  assert.equal(h.applied.length, 0);
});

test('secure requests need both acknowledgments and only explicit Send discloses one selected source', async () => {
  const h = fixture({ secure: true }); await h.open();
  assert.equal(h.button('Send to provider').props.disabled, true);
  await h.check('Send this request'); assert.equal(h.button('Send to provider').props.disabled, true);
  await h.check('I explicitly disclose'); await h.click('Send to provider');
  const sent = h.calls.filter(call => call.kind === 'send'); assert.equal(sent.length, 1);
  assert.equal(sent[0].conversation, 'owned-one-off'); assert.equal(sent[0].payload.expected_config_revision, 4);
  assert.equal(sent[0].payload.disclose_to_provider, true); assert.equal(sent[0].payload.disclose_decrypted_content, true);
  assert.deepEqual(sent[0].payload.sources, [{ kind: 'message', id: 'm1', text: 'Reviewed source text', event_ids: ['m1'] }]);
  assert.ok(!JSON.stringify(sent).includes('UNSELECTED SECRET')); assert.ok(!('target_project_id' in sent[0].payload));
  assert.deepEqual(h.calls.filter(call => call.kind === 'reset'), [{ kind: 'reset', conversation: 'owned-one-off' }]);
  assert.equal(h.applied.length, 0); assert.equal(h.button('Send to provider').props.disabled, true);
  assert.ok(h.text().includes('API use may incur charges. A chat subscription is separate.'));
  await h.click('Use suggestion'); assert.deepEqual(h.applied, [{ text: 'A reviewed suggestion [S1].', key: 'exact-target-draft' }]);
});

test('a changed provider rejects a stale reviewed request even when the account has consented again', async () => {
  const h = fixture(); await h.open(); await h.check('Send this request');
  h.setState({ config_revision: 5, consented: true, provider: { endpoint: 'https://different.example/chat', model: 'new-model' } });
  await h.click('Send to provider');
  assert.equal(h.calls.filter(call => call.kind === 'create' || call.kind === 'send').length, 0);
  assert.ok(h.text().includes('AI configuration or consent changed.'));
});

test('document text and revision are prepared for review before Send; changed bound text blocks disclosure', async () => {
  const h = fixture({ document: true }); await h.open(); assert.ok(h.text().includes('Verified selected document body'));
  await h.check('Send this request'); h.setDocumentText('Changed after review'); await h.click('Send to provider');
  assert.equal(h.calls.filter(call => call.kind === 'create' || call.kind === 'send').length, 0);
  assert.ok(h.text().includes('The selected context changed.'));
});

test('late provider answers cannot replace changed context and clean up only the owned one-off conversation', async () => {
  let finish; const pending = new Promise(resolve => { finish = resolve; });
  const h = fixture({ response: () => pending }); await h.open(); await h.check('Send this request'); await h.click('Send to provider');
  assert.equal(h.calls.filter(call => call.kind === 'send').length, 1);
  h.setProps({ contextKey: 'newly-typed-target' }); await h.settle(); finish({ text: 'Late answer', proposals: [] }); await h.settle();
  assert.ok(!h.text().includes('Late answer')); assert.equal(h.applied.length, 0);
  assert.deepEqual(h.calls.filter(call => call.kind === 'reset'), [{ kind: 'reset', conversation: 'owned-one-off' }]);
});

test('Apply revalidates current source access instead of saving a stale answer', async () => {
  const h = fixture(); await h.open(); await h.check('Send this request'); await h.click('Send to provider');
  h.setChoices(h.rawChoices().filter(choice => choice.source.id !== 'm1')); await h.click('Use suggestion');
  assert.equal(h.applied.length, 0); assert.ok(h.text().includes('This AI source is unavailable.'));
});

test('actor changes discard in-flight results and never issue cleanup under the new actor', async () => {
  let finish; const response = new Promise(resolve => { finish = resolve; });
  const h = fixture({ response: () => response }); await h.open(); await h.check('Send this request'); await h.click('Send to provider');
  h.changeActor(); await h.settle(); finish({ text: 'Old actor private answer', proposals: [] }); await h.settle();
  assert.ok(!h.text().includes('Old actor private answer')); assert.equal(h.applied.length, 0);
  assert.equal(h.calls.filter(call => call.kind === 'reset').length, 0);
});

test('exact source resolution refuses duplicates, unavailable sources and foreign channels', () => {
  const h = fixture(), choose = h.exports.selectContextualSources, choices = h.rawChoices();
  for (const refs of [[{ kind: 'message', id: 'missing' }], [{ kind: 'message', id: 'm1' }, { kind: 'message', id: 'm1' }]]) assert.throws(() => choose(choices, refs, 'channel'));
  assert.throws(() => choose(choices, [{ kind: 'message', id: 'm1' }], 'foreign'));
  assert.throws(() => choose([...choices, choices[0]], [{ kind: 'message', id: 'm1' }], 'channel'));
  assert.equal(choose(choices, [{ kind: 'message', id: 'm1' }], 'channel').length, 1);
});

test('oversized target text stays intact in review and cannot start a provider request', async () => {
  const h = fixture(), prompt = 'å'.repeat(3901);
  h.setProps({ actions: [{ id: 'rewrite', label: 'Refine wording', prompt }] }); await h.open(); await h.check('Send this request');
  assert.equal(h.button('Send to provider').props.disabled, true); assert.ok(h.text().includes(prompt));
  assert.equal(h.calls.filter(call => call.kind === 'create' || call.kind === 'send').length, 0);
});

test('provider action proposals are never approved or inserted as suggested text', async () => {
  const h = fixture({ response: async () => ({ text: 'Claimed task created', proposals: [{ id: 'proposal' }] }) });
  await h.open(); await h.check('Send this request'); await h.click('Send to provider');
  assert.ok(h.text().includes('Nothing was applied.')); assert.ok(!h.text().includes('Claimed task created'));
  assert.equal(h.applied.length, 0); assert.equal(h.calls.filter(call => call.kind === 'reset').length, 1);
});

test('read-only intents cannot expose an unrelated target application action', async () => {
  const h = fixture(); h.setProps({ applyActionIds: ['reply'], actions: [{ id: 'summary', label: 'Summarize this message', prompt: 'Summarize only the selected message.' }] });
  await h.open(); await h.check('Send this request'); await h.click('Send to provider');
  assert.ok(h.text().includes('A reviewed suggestion [S1].'));
  assert.ok(!h.nodes().some(node => node.type === 'button' && node.props.children === 'Use suggestion'));
  assert.equal(h.applied.length, 0);
});

function mixedBudgetSources(messageText) {
  return [
    { key: 'message:m1', source: { kind: 'message', id: 'm1' }, label: 'Selected message', preview: messageText, message: { id: 'm1', channel_id: 'channel', content: messageText } },
    ...['d1', 'd2'].map(id => ({ key: 'shared_document:' + id, source: { kind: 'shared_document', id }, label: id, preview: id, document: { id, channel_id: 'channel', version: 2, archived: false } })),
  ];
}
const mixedRefs = [{ kind: 'message', id: 'm1' }, { kind: 'shared_document', id: 'd1' }, { kind: 'shared_document', id: 'd2' }];

test('mixed message/document budget includes citation headers and separators and cannot send one character over', async () => {
  const h = fixture({ refs: mixedRefs }), body = 'm'.repeat(6002), document = 'd'.repeat(8970);
  h.setChoices(mixedBudgetSources(body)); h.setDocumentText(document); await h.open();
  // Bodies alone are23,942; real [S#] kind headers and joins add59 =>24,001.
  assert.ok(h.text().includes(body)); assert.ok(h.text().includes(document));
  assert.ok(h.text().includes('Select fewer sources. AI context is limited to 24,000 characters; nothing was sent.'));
  await h.check('Send this request'); assert.equal(h.button('Send to provider').props.disabled, true);
  // The event handler also rejects a forced click; no conversation is allocated.
  h.button('Send to provider').props.onClick(); await h.settle();
  assert.equal(h.calls.filter(call => call.kind === 'create' || call.kind === 'send').length, 0);
});

test('an exact24k Unicode context sends the whole reviewed documents without truncation', async () => {
  const h = fixture({ refs: mixedRefs }), body = '🧭'.repeat(6001), document = 'd'.repeat(8970);
  h.setChoices(mixedBudgetSources(body)); h.setDocumentText(document); await h.open();
  // Python counts Unicode characters, not UTF-16 units:23,941 body +59 envelope.
  assert.ok(!h.text().includes('Select fewer sources. AI context is limited'));
  await h.check('Send this request'); assert.equal(h.button('Send to provider').props.disabled, false);
  await h.click('Send to provider');
  const sent = h.calls.find(call => call.kind === 'send'); assert.ok(sent);
  assert.deepEqual(sent.payload.sources, [{ kind: 'message', id: 'm1' }, ...['d1', 'd2'].map(id => ({ kind: 'shared_document', id, revision: 2, text: document }))]);
  assert.ok(h.text().includes(body)); assert.ok(h.text().includes(document));
});
