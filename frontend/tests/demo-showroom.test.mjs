import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
const source = readFileSync(new URL('../src/lib/demo-showroom.ts', import.meta.url), 'utf8');
const exports = {};
vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText, { exports });
const { createDemoState, demoReducer, demoMarkdown } = exports;

test('independent demo instances and reset never retain another visitor draft', () => {
  const first = createDemoState(), second = createDemoState();
  const edited = demoReducer(first, { type: 'message', channel: 'studio', text: 'My local note', time: '12:00' });
  assert.equal(second.messages.studio.length, 3); assert.equal(first.messages.studio.length, 3);
  const reset = demoReducer(edited, { type: 'reset' });
  assert.equal(reset.messages.studio.length, 3); assert.ok(!JSON.stringify(reset).includes('My local note'));
});

test('sending a message affects one channel and never invents a teammate response', () => {
  const state = createDemoState(), updated = demoReducer(state, { type: 'message', channel: 'design', text: '  My words <script>literal text</script>  ', time: '12:01' });
  assert.equal(updated.messages.studio, state.messages.studio); assert.equal(updated.messages.product, state.messages.product);
  assert.equal(updated.messages.design.length, state.messages.design.length + 1);
  assert.equal(updated.messages.design.at(-1).text, 'My words <script>literal text</script>'); assert.equal(updated.messages.design.at(-1).sample, false);
});

test('tasks can be added and toggled without changing sample document or message records', () => {
  const state = createDemoState(), added = demoReducer(state, { type: 'task_add', text: 'My next step' });
  const task = added.tasks.at(-1), done = demoReducer(added, { type: 'task_toggle', id: task.id });
  assert.equal(task.sample, false); assert.equal(done.tasks.at(-1).done, true);
  assert.equal(done.documents, state.documents); assert.equal(done.messages, state.messages); assert.equal(state.tasks.length, 4);
});

test('Markdown export uses the actual edited text and translates only untouched sample content', () => {
  const initial = createDemoState(), text = 'A user note\n\n<script>this remains Markdown text</script>';
  const updated = demoReducer(initial, { type: 'document', id: 'brief', text });
  const document = updated.documents.find(row => row.id === 'brief');
  const result = demoMarkdown(document, source => source === 'Project brief' ? 'Projektbeskrivning' : 'TRANSLATED SAMPLE');
  assert.equal(result, '# Projektbeskrivning\n\n' + text + '\n');
  assert.equal(updated.documents.find(row => row.id === 'notes').edited, null);
});

test('empty and oversized interactions are bounded and cannot mutate the example', () => {
  const state = createDemoState();
  for (const action of [{ type: 'message', channel: 'studio', text: '   ', time: '12:00' }, { type: 'message', channel: 'studio', text: 'x'.repeat(2001), time: '12:00' }, { type: 'task_add', text: 'x'.repeat(181) }, { type: 'document', id: 'brief', text: 'x'.repeat(20001) }]) assert.equal(demoReducer(state, action), state);
  let full = state;
  for (let index = 0; index < 150; index++) full = demoReducer(full, { type: 'message', channel: 'studio', text: 'bounded', time: '12:00' });
  assert.equal(full.messages.studio.length, 100);
});

test('the courtyard opening is an actual sample conversation with shared project context', () => {
  const state = createDemoState();
  assert.equal(state.messages.studio[0].text, 'Shall we open up to the courtyard?');
  assert.equal(state.messages.studio[1].text, 'Yes. Then the light reaches all the way in.');
  assert.equal(state.messages.studio[2].author, 'Elias');
  assert.match(state.documents[0].source, /courtyard/);
});

test('personal layout is bounded, can be reordered and pinned, and resets independently of work', () => {
  const initial = createDemoState();
  assert.equal(demoReducer(initial, { type: 'layout_move', id: 'courtyard', direction: -1 }), initial);
  let state = demoReducer(initial, { type: 'message', channel: 'studio', text: 'Keep this note', time: '12:00' });
  state = demoReducer(state, { type: 'layout_move', id: 'brief', direction: -1 });
  assert.deepEqual(Array.from(state.layout.order), ['brief', 'courtyard']);
  state = demoReducer(state, { type: 'layout_pin', id: 'brief' });
  assert.deepEqual(Array.from(state.layout.pinned), ['brief']);
  state = demoReducer(state, { type: 'layout_side', side: 'left' });
  state = demoReducer(state, { type: 'focus' });
  assert.equal(state.layout.focus, true); assert.equal(state.layout.side, 'left');
  const layoutReset = demoReducer(state, { type: 'layout_reset' });
  assert.deepEqual(Array.from(layoutReset.layout.order), ['courtyard', 'brief']);
  assert.equal(layoutReset.layout.focus, false); assert.equal(layoutReset.layout.side, 'right');
  assert.equal(layoutReset.layout.pinned.length, 0);
  assert.equal(layoutReset.messages.studio.at(-1).text, 'Keep this note');
  const reset = demoReducer(state, { type: 'reset' });
  assert.equal(reset.messages.studio.length, 3); assert.equal(reset.layout.focus, false);
  assert.equal(reset.layout.pinned.length, 0); assert.equal(reset.layout.side, 'right');
});
