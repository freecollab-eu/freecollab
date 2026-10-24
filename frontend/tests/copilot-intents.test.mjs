import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/lib/copilot-intents.ts', import.meta.url), 'utf8');
const exports = {};
vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText, { exports, URL });
const { appendCopilotIntent, COPILOT_PROMPT_LIMIT, copilotIntents, copilotProviderHost, copilotDiscussionSummaryInstruction, copilotRelatedDocumentsInstruction } = exports;

test('every quick intent keeps the exact existing user draft and prepares a bounded instruction', () => {
  const existing = '  My own request: åäö, <script>literal text</script>\nKeep this detail.  ';
  assert.equal(copilotIntents.length, 4);
  assert.equal(new Set(copilotIntents.map(intent => intent.id)).size, 4);
  for (const intent of copilotIntents) {
    const result = appendCopilotIntent(existing, intent.id);
    assert.equal(result.status, 'added');
    assert.equal(result.prompt.slice(0, existing.length), existing);
    assert.ok(result.prompt.length <= COPILOT_PROMPT_LIMIT);
    assert.ok(result.prompt.includes('selected'));
  }
});

test('adding the same final intent twice keeps the draft unchanged while another intent can be added', () => {
  const first = appendCopilotIntent('A useful detail', 'summary');
  const repeated = appendCopilotIntent(first.prompt, 'summary');
  assert.equal(repeated.status, 'already_present'); assert.equal(repeated.prompt, first.prompt);
  const next = appendCopilotIntent(first.prompt, 'reply');
  assert.equal(next.status, 'added'); assert.ok(next.prompt.startsWith(first.prompt));
});

test('overflow refuses the addition without truncating user text, including at the exact composer boundary', () => {
  const instruction = copilotIntents.find(intent => intent.id === 'summary').instruction;
  const fitting = 'x'.repeat(COPILOT_PROMPT_LIMIT - instruction.length - 2);
  const exact = appendCopilotIntent(fitting, 'summary');
  assert.equal(exact.status, 'added'); assert.equal(exact.prompt.length, COPILOT_PROMPT_LIMIT);
  const oversized = fitting + 'Å';
  const rejected = appendCopilotIntent(oversized, 'summary');
  assert.equal(rejected.status, 'too_long'); assert.equal(rejected.prompt, oversized);
  const fullUnicodeDraft = '🪴'.repeat(COPILOT_PROMPT_LIMIT / 2);
  assert.equal(appendCopilotIntent(fullUnicodeDraft, 'task').prompt, fullUnicodeDraft);
});

test('only the app instruction is translated; existing user text remains literal', () => {
  const existing = 'Please keep the phrase Summarise selection exactly as I wrote it.';
  const seen = [];
  const result = appendCopilotIntent(existing, 'next_steps', value => { seen.push(value); return 'SV: ' + value; });
  assert.equal(seen.length, 1);
  assert.equal(seen[0], copilotIntents.find(intent => intent.id === 'next_steps').instruction);
  assert.ok(result.prompt.startsWith(existing)); assert.ok(result.prompt.includes('\n\nSV: '));
});

test('invalid actions preserve the draft and task drafting never asserts that a write happened', () => {
  const invalid = appendCopilotIntent('Existing text', 'arbitrary-remote-action');
  assert.equal(invalid.status, 'unavailable'); assert.equal(invalid.prompt, 'Existing text');
  const task = appendCopilotIntent('', 'task');
  assert.ok(task.prompt.includes('only if it is available for my chosen destination'));
  assert.ok(task.prompt.includes('otherwise return a draft'));
  assert.ok(task.prompt.includes('do not claim the task was created'));
});

test('recipient labels contain only parsed HTTPS hostnames and never URL credentials or query secrets', () => {
  assert.equal(copilotProviderHost('https://api.customer.example/v1/chat/completions'), 'api.customer.example');
  assert.equal(copilotProviderHost('https://localhost:8443/v1/chat/completions'), 'localhost');
  for (const endpoint of [undefined, '', 'not a URL', '/v1/chat/completions', 'http://api.customer.example/v1/chat/completions', 'https://user:private-key@api.customer.example/v1', 'https://api.customer.example/v1?key=private-key', 'https://api.customer.example/v1#private-key']) assert.equal(copilotProviderHost(endpoint), null);
});

test('contextual discussion and related-document instructions stay bound to reviewed sources', () => {
  assert.ok(copilotDiscussionSummaryInstruction.includes('only the selected sources'));
  assert.ok(copilotDiscussionSummaryInstruction.includes('when replies or context are missing'));
  assert.ok(copilotRelatedDocumentsInstruction.includes('only the reviewed document sources'));
  assert.ok(copilotRelatedDocumentsInstruction.includes('Do not invent documents, links, paragraph identifiers or access to other channel content'));
  assert.ok(copilotRelatedDocumentsInstruction.includes('If no document sources were selected'));
});
