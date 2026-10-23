import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

let networkRequests = 0;
function load(path) {
  const exports = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText, {
    exports, URLSearchParams, fetch: () => { networkRequests++; throw new Error('The public preview must stay local'); },
  });
  return exports;
}
const { createDemoState, demoReducer } = load('../src/lib/demo-showroom.ts');
const { buildDemoAiSources, createDemoAiSession, createDemoAiRun, demoAiRunIsCurrent, demoAiProviders, demoAiIntents, demoAiSelectionLimit } = load('../src/lib/demo-ai.ts');
const plain = value => JSON.parse(JSON.stringify(value));
const translator = dictionary => (source, values = {}) => (dictionary[source] ?? source).replace(/\{([A-Za-z][A-Za-z0-9_]*)\}/g, (whole, key) => Object.hasOwn(values, key) ? String(values[key]) : whole);
const english = translator({});
const swedish = translator({ 'Local preview': 'Lokal förhandsvisning', 'You': 'Du', '{author} · message {number}': '{author} · meddelande {number}', 'Project brief': 'Projektbeskrivning', 'Shall we open up to the courtyard?': 'Ska vi öppna upp mot gården?' });
function sessionFor(sources, keys, changes = {}) { return { ...createDemoAiSession(sources), selectedKeys: keys, ...changes }; }

test('source extraction follows the visible channel, translated samples and actual document edits', () => {
  let state = createDemoState();
  const original = JSON.stringify(state);
  let sources = buildDemoAiSources(state, 'studio', swedish);
  assert.equal(sources.find(source => source.key === 'message:studio-1').text, 'Ska vi öppna upp mot gården?');
  assert.equal(sources.find(source => source.key === 'document:brief').label, 'Projektbeskrivning');
  assert.equal(sources.length, 7); assert.ok(sources.every(source => source.channel === 'studio'));
  assert.equal(sources.some(source => source.key === 'document:product-plan'), false);
  assert.equal(JSON.stringify(state), original);
  state = demoReducer(state, { type: 'document', id: 'brief', text: 'Project brief' });
  state = demoReducer(state, { type: 'document', id: 'notes', text: '' });
  state = demoReducer(state, { type: 'message', channel: 'studio', text: 'Project brief', time: '12:08' });
  state = demoReducer(state, { type: 'document_add', channel: 'studio', stackId: 'studio-reference', title: 'Project brief' });
  const userDocument = state.documents.at(-1);
  state.drafts = { studio: 'Unsent words must not become selected context' };
  sources = buildDemoAiSources(state, 'studio', swedish);
  assert.equal(sources.find(source => source.key === 'document:brief').text, 'Project brief');
  assert.equal(sources.find(source => source.key === 'document:notes').text, '');
  assert.equal(sources.find(source => source.key === 'document:' + userDocument.id).label, 'Project brief');
  assert.equal(sources.find(source => source.key === 'message:' + state.messages.studio.at(-1).id).text, 'Project brief');
  assert.equal(JSON.stringify(sources).includes('Unsent words'), false);
  assert.equal(buildDemoAiSources(state, 'constructor').length, 0);
  const wrongStack = { ...state, documents: state.documents.map(document => document.id === 'brief' ? { ...document, stackId: 'design-reference' } : document) };
  assert.equal(buildDemoAiSources(wrongStack, 'studio').some(source => source.id === 'brief'), false);
});

test('a new session visibly starts with Grok and one message plus one document without running anything', () => {
  const sources = buildDemoAiSources(createDemoState(), 'studio'), before = JSON.stringify(sources), session = createDemoAiSession(sources);
  assert.equal(session.provider, 'grok'); assert.equal(session.model, 'grok-4.20-0309-non-reasoning');
  assert.equal(session.intent, 'summary'); assert.equal(session.run, null);
  assert.deepEqual(plain(session.selectedKeys), ['message:studio-1', 'document:brief']);
  assert.equal(JSON.stringify(sources), before); assert.equal(createDemoAiSession([]).selectedKeys.length, 0);
  assert.equal(demoAiProviders.find(provider => provider.id === 'grok').host, 'api.x.ai');
  assert.equal(demoAiProviders.find(provider => provider.id === 'openai').model, 'gpt-4.1-mini');
  assert.equal(demoAiProviders.find(provider => provider.id === 'custom').host, 'Your own installation');
});

test('summary snapshots contain only explicitly selected content and cite its actual excerpts', () => {
  let state = createDemoState();
  state = demoReducer(state, { type: 'document', id: 'brief', text: 'Actual approved budget: 420. No date or owner agreed.' });
  state = demoReducer(state, { type: 'message', channel: 'studio', text: 'A ceramic nozzle needs a cooling review.', time: '12:08' });
  state = demoReducer(state, { type: 'message', channel: 'studio', text: 'UNSELECTED ORIGINAL WORDS MUST STAY OUT', time: '12:09' });
  const message = state.messages.studio.at(-2), sources = buildDemoAiSources(state, 'studio');
  const session = sessionFor(sources, ['document:brief', 'message:' + message.id]), before = JSON.stringify(state);
  const run = createDemoAiRun(session, sources, 'studio');
  assert.equal(run.mode, 'local-preview'); assert.match(run.description, /^Local preview:/);
  assert.deepEqual(plain(run.sources.map(source => source.key)), session.selectedKeys);
  assert.equal(run.sources[0].text, 'Actual approved budget: 420. No date or owner agreed.');
  assert.match(run.output, /“Actual approved budget: 420\. No date or owner agreed\.” \[S1\]/);
  assert.match(run.output, /“A ceramic nozzle needs a cooling review\.” \[S2\]/);
  assert.deepEqual(plain(run.items.map(item => item.sourceKeys)), [['document:brief'], ['message:' + message.id]]);
  assert.equal(JSON.stringify(run).includes('UNSELECTED ORIGINAL WORDS'), false);
  assert.equal(run.task, null); assert.equal(run.replyDraft, null); assert.equal(JSON.stringify(state), before);
});

test('invalid selections, cross-channel records and unsupported controls cannot produce a preview', () => {
  let state = createDemoState();
  for (let index = 0; index < 2; index++) state = demoReducer(state, { type: 'message', channel: 'studio', text: 'Another selectable message ' + index, time: '12:10' });
  const sources = buildDemoAiSources(state, 'studio'), session = createDemoAiSession(sources), foreign = buildDemoAiSources(state, 'product')[0];
  assert.equal(createDemoAiRun(sessionFor(sources, sources.slice(0, demoAiSelectionLimit).map(source => source.key)), sources, 'studio').sources.length, demoAiSelectionLimit);
  for (const patch of [
    { selectedKeys: [] }, { selectedKeys: ['missing'] }, { selectedKeys: [sources[0].key, sources[0].key] },
    { selectedKeys: sources.slice(0, demoAiSelectionLimit + 1).map(source => source.key) },
    { provider: 'untrusted-provider' }, { intent: 'execute-everything' },
    { model: '' }, { model: ' invalid model ' }, { model: 'x'.repeat(101) },
  ]) assert.equal(createDemoAiRun({ ...session, ...patch }, sources, 'studio'), null, JSON.stringify(patch));
  assert.equal(createDemoAiRun(session, sources, 'constructor'), null);
  assert.equal(createDemoAiRun(sessionFor(sources, [foreign.key]), [...sources, foreign], 'studio'), null);
  assert.equal(createDemoAiRun(session, [...sources, { ...sources[0] }], 'studio'), null);
  assert.equal(createDemoAiRun(session, sources.filter(source => source.key !== session.selectedKeys[0]), 'studio'), null);
});

test('source and control changes invalidate approval while unrelated edits leave the snapshot current', () => {
  let state = demoReducer(createDemoState(), { type: 'document', id: 'brief', text: 'x'.repeat(300) + ' original ending' });
  const sources = buildDemoAiSources(state, 'studio'), session = sessionFor(sources, ['document:brief', 'message:studio-1']), run = createDemoAiRun(session, sources, 'studio');
  assert.equal(demoAiRunIsCurrent(run, session, sources, 'studio'), true);
  const unrelated = demoReducer(state, { type: 'document', id: 'notes', text: 'An unselected document changed' });
  assert.equal(demoAiRunIsCurrent(run, session, buildDemoAiSources(unrelated, 'studio'), 'studio'), true);
  state = demoReducer(state, { type: 'document', id: 'brief', text: 'x'.repeat(300) + ' edited ending beyond the excerpt' });
  assert.equal(run.items[0].text, createDemoAiRun(session, buildDemoAiSources(state, 'studio'), 'studio').items[0].text);
  assert.equal(demoAiRunIsCurrent(run, session, buildDemoAiSources(state, 'studio'), 'studio'), false);
  for (const patch of [{ provider: 'openai' }, { model: 'a-different-model' }, { intent: 'task' }, { selectedKeys: [...session.selectedKeys].reverse() }, { selectedKeys: [session.selectedKeys[0]] }]) assert.equal(demoAiRunIsCurrent(run, { ...session, ...patch }, sources, 'studio'), false);
  assert.equal(demoAiRunIsCurrent(run, session, sources, 'product'), false);
  assert.equal(demoAiRunIsCurrent(run, session, sources.filter(source => source.id !== 'brief'), 'studio'), false);
  const copiedFingerprint = sources.map(source => source.id === 'brief' ? { ...source, text: 'Changed text with an old fingerprint' } : source);
  assert.equal(demoAiRunIsCurrent(run, session, copiedFingerprint, 'studio'), false);
});

test('changing language invalidates a user-only selection without translating the user content', () => {
  let state = demoReducer(createDemoState(), { type: 'document_add', channel: 'studio', stackId: 'studio-reference', title: 'My literal name' });
  const document = state.documents.at(-1);
  state = demoReducer(state, { type: 'document', id: document.id, text: 'My literal words' });
  const sources = buildDemoAiSources(state, 'studio', english), session = sessionFor(sources, ['document:' + document.id]), run = createDemoAiRun(session, sources, 'studio', english);
  const changed = buildDemoAiSources(state, 'studio', swedish), current = changed.find(source => source.id === document.id);
  assert.equal(current.label, 'My literal name'); assert.equal(current.text, 'My literal words');
  assert.notEqual(current.fingerprint, run.sources[0].fingerprint);
  assert.equal(demoAiRunIsCurrent(run, session, changed, 'studio'), false);
});

test('run snapshots never share mutable source or selection records with the controls', () => {
  const sources = buildDemoAiSources(createDemoState(), 'studio'), session = createDemoAiSession(sources), run = createDemoAiRun(session, sources, 'studio');
  const before = JSON.stringify(run);
  sources[0].text = 'Changed outside the snapshot'; session.selectedKeys.push('another-key');
  assert.equal(JSON.stringify(run), before);
  assert.notEqual(run.sources[0], sources[0]); assert.notEqual(run.selectedKeys, session.selectedKeys);
});

test('next-step and reply examples quote selected evidence without inventing commitments', () => {
  let state = demoReducer(createDemoState(), { type: 'message', channel: 'studio', text: 'The ceramic nozzle needs a cooling review.', time: '12:11' });
  const message = state.messages.studio.at(-1), sources = buildDemoAiSources(state, 'studio');
  const steps = createDemoAiRun(sessionFor(sources, ['message:' + message.id], { intent: 'next_steps' }), sources, 'studio');
  assert.equal(steps.items[0].text, 'What is one next step for “' + message.text + '”?');
  assert.match(steps.description, /^Local preview:/); assert.equal(steps.task, null);
  const reply = createDemoAiRun(sessionFor(sources, ['message:' + message.id], { intent: 'reply' }), sources, 'studio');
  assert.equal(reply.replyDraft, 'Thanks for sharing. What should we explore next about “' + message.text + '”?');
  assert.equal(reply.items[0].sourceKeys[0], 'message:' + message.id); assert.match(reply.description, /^Local preview:/);
  for (const output of [steps.output, reply.output]) assert.equal(/Maja|Noor|Elias|tomorrow|Friday|2026|approved by/.test(output), false);
});

test('task preview copies the first selected sent message and never falls back to an unselected message', () => {
  let state = demoReducer(createDemoState(), { type: 'message', channel: 'studio', text: 'x'.repeat(179) + '😀 and more', time: '12:12' });
  const message = state.messages.studio.at(-1), sources = buildDemoAiSources(state, 'studio');
  const session = sessionFor(sources, ['document:brief', 'message:' + message.id, 'message:studio-1'], { intent: 'task' });
  const run = createDemoAiRun(session, sources, 'studio');
  assert.equal(run.task.messageId, message.id); assert.equal(run.task.text, 'x'.repeat(179));
  assert.equal(run.task.channel, 'studio'); assert.equal(run.task.sourceKey, 'message:' + message.id);
  assert.equal(run.task.fingerprint, run.sources[1].fingerprint);
  assert.deepEqual(Object.keys(run.task).sort(), ['channel', 'fingerprint', 'messageId', 'sourceKey', 'text']);
  assert.equal(demoAiRunIsCurrent(run, session, sources, 'studio'), true);
  for (const patch of [{ messageId: 'studio-2' }, { sourceKey: 'document:brief' }, { text: 'An invented task title' }, { channel: 'product' }, { fingerprint: 'stale-content' }]) assert.equal(demoAiRunIsCurrent({ ...run, task: { ...run.task, ...patch } }, session, sources, 'studio'), false);
  assert.equal(demoAiRunIsCurrent({ ...run, task: null }, session, sources, 'studio'), false);
  const documentOnly = createDemoAiRun(sessionFor(sources, ['document:brief'], { intent: 'task' }), sources, 'studio');
  assert.equal(documentOnly.task, null); assert.match(documentOnly.description, /Select a sent message/);
  assert.deepEqual(plain(documentOnly.sources.map(source => source.key)), ['document:brief']);
});

test('empty current edits stay empty and long selected documents produce bounded cited output', () => {
  let state = createDemoState();
  state = demoReducer(state, { type: 'document', id: 'brief', text: '' });
  let sources = buildDemoAiSources(state, 'studio'), run = createDemoAiRun(sessionFor(sources, ['document:brief']), sources, 'studio');
  assert.equal(run.sources[0].text, ''); assert.equal(run.items[0].text, 'This selected source is empty.');
  const documents = Array.from({ length: demoAiSelectionLimit }, (_, index) => ({ ...state.documents[0], id: 'long-' + index, title: 'Long document ' + index, source: 'unused old source', edited: 'z'.repeat(20000), sample: false }));
  state = { ...state, documents };
  sources = buildDemoAiSources(state, 'studio');
  run = createDemoAiRun(sessionFor(sources, documents.map(document => 'document:' + document.id)), sources, 'studio');
  assert.equal(run.sources.length, demoAiSelectionLimit); assert.ok(run.sources.every(source => source.text.length === 20000));
  assert.ok(run.output.length < 4000); assert.ok(run.items.every(item => item.sourceKeys.length === 1 && item.text.length < 300));
});

test('all provider and intent combinations stay local and never modify the showroom', () => {
  const state = createDemoState(), before = JSON.stringify(state), sources = buildDemoAiSources(state, 'studio');
  for (const provider of demoAiProviders) for (const intent of demoAiIntents) {
    const session = { ...createDemoAiSession(sources), provider: provider.id, model: provider.model, intent };
    const run = createDemoAiRun(session, sources, 'studio');
    assert.equal(run.provider, provider.id); assert.equal(run.model, provider.model); assert.equal(run.mode, 'local-preview');
    assert.equal(demoAiRunIsCurrent(run, session, sources, 'studio'), true);
  }
  assert.equal(networkRequests, 0); assert.equal(JSON.stringify(state), before);
});
