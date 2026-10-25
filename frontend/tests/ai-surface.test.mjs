import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const plain = value => JSON.parse(JSON.stringify(value));
function load(path, dependencies = {}) {
  const exports = {};
  const code = ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  vm.runInNewContext(code, { exports, require: name => name.endsWith('.css') ? {} : dependencies[name] || {}, URL, Set, Intl });
  return exports;
}
const references = load('../src/lib/message-references.ts');
const writing = load('../src/components/WritingCopilot.tsx', { '../lib/message-references': references });
const runtime = load('../src/components/AIRuntimePanel.tsx');

test('writer uses the exact selected passage and preserves the surrounding draft and reference bindings', () => {
  const value = '@Ada will review a draft. Ask @Bo tomorrow.';
  const start = value.indexOf('review'), end = value.indexOf('. Ask');
  const range = writing.writingSelection({ value, selectionStart: start, selectionEnd: end });
  assert.equal(range.text, 'review a draft');
  const result = writing.writingSuggestion(range, 'approve the revised draft', [{ kind: 'person', id: 'ada', start: 0, end: 4 }, { kind: 'person', id: 'bo', start: value.indexOf('@Bo'), end: value.indexOf('@Bo') + 3 }], 100);
  assert.equal(result.value, '@Ada will approve the revised draft. Ask @Bo tomorrow.');
  assert.deepEqual(plain(result.references), [{ kind: 'person', id: 'ada', start: 0, end: 4 }, { kind: 'person', id: 'bo', start: result.value.indexOf('@Bo'), end: result.value.indexOf('@Bo') + 3 }]);
  assert.equal(result.caret, start + 'approve the revised draft'.length);
});

test('AI changes inside an explicit mention remove its binding without guessing another recipient', () => {
  const value = 'Ask @Ada now.';
  const range = writing.writingSelection({ value, selectionStart: 4, selectionEnd: 8 });
  const result = writing.writingSuggestion(range, '@Bo', [{ kind: 'person', id: 'ada', start: 4, end: 8 }], 100);
  assert.equal(result.value, 'Ask @Bo now.');
  assert.deepEqual(plain(result.references), []);
});

test('writer rejects split Unicode boundaries, invalid ranges, blank, stale and overflowing suggestions', () => {
  for (const [selectionStart, selectionEnd] of [[2, 3], [1, 2], [-1, 2], [-1, -1], [3, 1], [0, 50], [0.5, 2]]) assert.equal(writing.writingSelection({ value: 'A🪴B', selectionStart, selectionEnd }), null);
  const whole = writing.writingSelection({ value: 'A🪴B', selectionStart: 1, selectionEnd: 1 });
  assert.equal(whole.text, 'A🪴B');
  assert.equal(writing.writingSuggestion(whole, '   ', [], 100), null);
  assert.equal(writing.writingSuggestion(whole, 'too long', [], 5), null);
  assert.equal(writing.writingSuggestion({ ...whole, text: 'stale' }, 'text', [], 100), null);
});

test('model-generated plans accept only the reviewed catalog, exact revisions and bounded registered actions', () => {
  const allowed = [{ profile_id: 'fast', expected_profile_revision: 4 }, { profile_id: 'careful', expected_profile_revision: 8 }];
  const valid = [{ operation: 'summarize', ...allowed[0] }, { operation: 'draft', ...allowed[1], instructions: 'Review the first result.' }];
  assert.equal(runtime.validateReviewedPlan(valid, allowed, 2), true);
  for (const value of [[], null, [...valid, valid[0]], [{ ...valid[0], operation: 'send_email' }], [{ ...valid[0], profile_id: 'unreviewed' }], [{ ...valid[0], expected_profile_revision: 5 }], [{ ...valid[0], instructions: 'x'.repeat(1001) }]]) assert.equal(runtime.validateReviewedPlan(value, allowed, 2), false);
});

test('content groups preserve actual typed resource identities and parent IDs', () => {
  const choices = [{ source: { kind: 'shared_document', id: 'd' } }, { source: { kind: 'task', id: 't' }, parentId: 'project' }, { source: { kind: 'calendar', id: 'c' } }];
  assert.deepEqual(plain(runtime.reviewedGroupMembers(['t', 'd', 'c'], choices)), [{ kind: 'task', id: 't', parent_id: 'project' }, { kind: 'document', id: 'd' }, { kind: 'calendar', id: 'c' }]);
  for (const ids of [[], ['d', 'd'], ['unknown'], [false], Array(9).fill('d')]) assert.throws(() => runtime.reviewedGroupMembers(ids, choices), /reviewed sources/);
  assert.throws(() => runtime.reviewedGroupMembers(['d'], [...choices, { source: { kind: 'file', id: 'd' } }]), /reviewed sources/);
  assert.deepEqual(plain(runtime.reviewedGroupMembers(['task:t'], [{ source: { kind: 'task', id: 't' }, reviewedId: 'task:t', parentId: 'project' }])), [{ kind: 'task', id: 't', parent_id: 'project' }]);
  assert.throws(() => runtime.reviewedGroupMembers(['x'], [{ source: { kind: 'made-up-kind', id: 'x' } }]), /reviewed sources/);
});

test('exact model comparison prevents implicit provider, owner, protocol, endpoint, revision or model substitutions', () => {
  const { sameAIProfile, aiProfileRecipient } = load('../src/lib/ai-runtime-api.ts');
  const original = { id: 'fast', revision: 1, protocol: 'openai', endpoint: 'https://api.example.com/v1/chat/completions', model: 'fast-v1', key_source: 'personal' };
  assert.equal(sameAIProfile(original, { ...original }), true);
  for (const [field, value] of [['id', 'other'], ['revision', 2], ['protocol', 'xai'], ['endpoint', 'https://other.example.com/v1'], ['model', 'v2'], ['key_source', 'workspace']]) assert.equal(sameAIProfile(original, { ...original, [field]: value }), false);
  assert.equal(aiProfileRecipient(original), 'api.example.com');
  assert.equal(aiProfileRecipient({ endpoint: 'https://secret:password@example.com/v1' }), '');
});

test('runtime writes carry exact CAS revisions and explicit retry/disclosure decisions to encoded endpoints', async () => {
  const calls = [];
  const { aiRuntimeApi } = load('../src/lib/ai-runtime-api.ts', { './api': { api: { request: async (...args) => { calls.push(args); return {}; } } } });
  const run = { id: 'run/1', revision: 7 };
  await aiRuntimeApi.cancel('work/space', run);
  await aiRuntimeApi.resume('work/space', run);
  await aiRuntimeApi.approve('work/space', run, 's0-p1');
  await aiRuntimeApi.removePersonalKey('work/space', 'personal/model', { password: 'synthetic-proof' });
  assert.equal(calls[0][0], '/workspaces/work%2Fspace/copilot/runtime/runs/run%2F1/cancel');
  assert.deepEqual(JSON.parse(calls[0][1].body), { expected_revision: 7 });
  assert.deepEqual(JSON.parse(calls[1][1].body), { expected_revision: 7, acknowledge_possible_duplicate_charge: true });
  assert.deepEqual(JSON.parse(calls[2][1].body), { expected_revision: 7, proposal_id: 's0-p1', confirm: true });
  assert.equal(calls[3][1].method, 'DELETE');
  assert.deepEqual(JSON.parse(calls[3][1].body), { password: 'synthetic-proof' });
});


test('read excerpts reject empty, excessive and split-surrogate ranges without silently changing their bounds', () => {
  const { validResourcePassage } = load('../src/components/ReviewedResourceCopilot.tsx');
  assert.equal(validResourcePassage('A🪴B', 1, 3), true);
  for (const [start, end] of [[1, 2], [2, 3], [-1, 3], [0, 8], [3, 3]]) assert.equal(validResourcePassage('A🪴B', start, end), false);
  assert.equal(validResourcePassage('x'.repeat(2801), 0, 2801), false);
});


test('semantic selections bind channel, kind and exact ID so peer IDs cannot select another conversation', () => {
  const first = { channel_id: 'one', kind: 'task', id: 'same-record-id' };
  assert.notEqual(runtime.semanticSourceKey(first), runtime.semanticSourceKey({ ...first, channel_id: 'two' }));
  assert.notEqual(runtime.semanticSourceKey(first), runtime.semanticSourceKey({ ...first, kind: 'document' }));
  assert.equal(runtime.semanticSourceKey(first), 'one:task:same-record-id');
});
