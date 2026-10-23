import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
const source = readFileSync(new URL('../src/lib/demo-showroom.ts', import.meta.url), 'utf8');
const exports = {};
vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText, { exports, URLSearchParams });
const { createDemoState, demoReducer, demoMarkdown, readDemoDestination, demoDestinationHash, demoDecisionTaskId, demoChannelDocuments, demoChannelStacks, demoStackDocuments, demoStackReferences, demoStackLimit, demoDocumentLimit, demoStackReferenceLimit, demoDocumentLinkLimit, demoDismissedConnectionLimit, demoConnectionIdLimit } = exports;

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

test('explicit demo destinations round-trip both providers without taking over account routes', () => {
  assert.equal(demoDestinationHash(), '#demo');
  assert.equal(readDemoDestination('#demo').section, 'chat');
  for (const provider of ['microsoft', 'google']) {
    const hash = demoDestinationHash({ section: 'connections', provider });
    assert.equal(hash, '#demo=connections&provider=' + provider);
    assert.equal(readDemoDestination(hash).section, 'connections');
    assert.equal(readDemoDestination(hash).provider, provider);
  }
  for (const hash of ['#docs', '#sso=one-time-handoff', '#workspace-invite=private', '#meeting=room']) assert.equal(readDemoDestination(hash), null);
  assert.equal(readDemoDestination('#demo=connections&provider=untrusted').provider, undefined);
  assert.equal(readDemoDestination('#demo=untrusted&provider=google').section, 'chat');
  assert.equal(readDemoDestination('#demo=documents&provider=google').provider, undefined);
  assert.equal(demoDestinationHash({ section: 'assistant' }), '#demo=assistant');
  assert.equal(readDemoDestination('#demo=assistant&provider=google').section, 'assistant');
  assert.equal(readDemoDestination('#demo=assistant&provider=google').provider, undefined);
});

test('a local decision creates one owned next-step task beside the same edited brief', () => {
  const initial = createDemoState();
  const edited = demoReducer(initial, { type: 'document', id: 'brief', text: 'My actual shared brief edit' });
  let state = demoReducer(edited, { type: 'decision_record', text: '  Choose the timber samples  ', nextStep: '  Bring two samples  ', owner: 'Noor' });
  assert.equal(initial.decision, null);
  assert.equal(state.decision.text, 'Choose the timber samples');
  assert.equal(state.decision.owner, 'Noor');
  assert.equal(state.documents, edited.documents);
  assert.equal(state.documents[0].edited, 'My actual shared brief edit');
  const task = state.tasks.find(row => row.id === demoDecisionTaskId);
  assert.equal(task.text, state.decision.nextStep); assert.equal(task.owner, state.decision.owner);
  assert.equal(task.sample, false); assert.equal(task.done, false);
  state = demoReducer(state, { type: 'task_toggle', id: task.id });
  state = demoReducer(state, { type: 'decision_record', text: 'Clarify the same direction', nextStep: task.text, owner: 'Noor' });
  assert.equal(state.tasks.find(row => row.id === task.id).done, true);
  state = demoReducer(state, { type: 'decision_record', text: 'A revised choice', nextStep: 'Bring a new material', owner: 'Elias' });
  assert.equal(state.tasks.length, initial.tasks.length + 1);
  assert.equal(state.tasks.find(row => row.id === task.id).owner, 'Elias');
  assert.equal(state.tasks.find(row => row.id === task.id).done, false);
  const reset = demoReducer(state, { type: 'reset' });
  assert.equal(reset.decision, null); assert.equal(reset.tasks.some(row => row.id === task.id), false);
  assert.equal(reset.documents[0].edited, null);
});

test('decision input and task capacity stay bounded without partial state changes', () => {
  const initial = createDemoState();
  for (const action of [
    { text: '', nextStep: 'A step', owner: 'Maja' },
    { text: 'A decision', nextStep: ' ', owner: 'Maja' },
    { text: 'x'.repeat(1001), nextStep: 'A step', owner: 'Maja' },
    { text: 'A decision', nextStep: 'x'.repeat(181), owner: 'Maja' },
    { text: 'A decision', nextStep: 'A step', owner: 'not-a-fictional-teammate' },
  ]) assert.equal(demoReducer(initial, { type: 'decision_record', ...action }), initial);
  let full = initial;
  while (full.tasks.length < 50) full = demoReducer(full, { type: 'task_add', text: 'A visitor task' });
  assert.equal(demoReducer(full, { type: 'decision_record', text: 'A choice', nextStep: 'A step', owner: 'Maja' }), full);
});

test('document stacks expose only the current channel and keep the original sample brief identities', () => {
  const state = createDemoState(), second = createDemoState();
  assert.deepEqual(Array.from(state.documents.slice(0, 2), row => row.id), ['brief', 'notes']);
  assert.deepEqual(Array.from(demoChannelDocuments(state, 'studio'), row => row.id), ['brief', 'notes', 'materials', 'decision']);
  assert.deepEqual(Array.from(demoChannelStacks(state, 'studio'), row => row.title), ['Reference material', 'Sketches', 'Decisions']);
  assert.deepEqual(Array.from(demoStackDocuments(state, 'studio', 'studio-reference'), row => row.id), ['brief', 'notes']);
  assert.equal(demoStackDocuments(state, 'studio', 'product-reference').length, 0);
  assert.equal(demoChannelDocuments(state, 'constructor').length, 0);
  assert.equal(demoChannelStacks(state, 'constructor').length, 0);
  assert.equal(demoStackReferences(state, 'studio', 'product-reference').length, 0);
  assert.equal(demoStackDocuments(state, 'product', 'product-sketches').length, 0);
  assert.notEqual(state.stacks.studio, second.stacks.studio);
  assert.notEqual(state.stacks.studio[0].references, second.stacks.studio[0].references);
  const saved = demoReducer(state, { type: 'stack_reference', channel: 'studio', stackId: 'studio-reference', messageId: 'studio-1' });
  assert.equal(state.stacks.studio[0].references.length, 0);
  assert.equal(second.stacks.studio[0].references.length, 0);
  assert.equal(demoStackReferences(saved, 'studio', 'studio-reference')[0], state.messages.studio[0]);
});

test('invalid and cross-channel stack operations are atomic and preserve the available undo', () => {
  const initial = createDemoState();
  const state = demoReducer(initial, { type: 'document_move', channel: 'studio', id: 'brief', stackId: 'studio-sketches' });
  for (const action of [
    { type: 'document_move', channel: 'studio', id: 'brief', stackId: 'product-reference' },
    { type: 'document_move', channel: 'studio', id: 'product-plan', stackId: 'studio-reference' },
    { type: 'document_move', channel: 'studio', id: 'brief', stackId: 'studio-sketches' },
    { type: 'document_move', channel: 'studio', id: 'missing', stackId: 'studio-reference' },
    { type: 'document_move', channel: 'constructor', id: 'brief', stackId: 'studio-reference' },
    { type: 'stack_reference', channel: 'studio', stackId: 'studio-reference', messageId: 'product-1' },
    { type: 'stack_reference', channel: 'studio', stackId: 'product-reference', messageId: 'studio-1' },
    { type: 'stack_reference', channel: 'studio', stackId: 'studio-reference', messageId: 'missing' },
    { type: 'stack_reference_remove', channel: 'studio', stackId: 'studio-reference', messageId: 'studio-1' },
    { type: 'stack_rename', channel: 'studio', id: 'product-reference', title: 'A different name' },
    { type: 'document_add', channel: 'studio', stackId: 'product-reference', title: 'A new document' },
    { type: 'stack_undo', channel: 'constructor' },
    { type: 'message', channel: 'constructor', text: 'A message', time: '12:00' },
    { type: 'document', id: 'missing', text: 'A document' },
  ]) assert.equal(demoReducer(state, action), state, JSON.stringify(action));
  assert.equal(state.nextId, initial.nextId);
  assert.equal(state.stackUndo.studio.documentId, 'brief');
  assert.equal(initial.documents[0].stackId, 'studio-reference');
});

test('undoing a document move preserves later editing and work in other channels', () => {
  const initial = createDemoState();
  let state = demoReducer(initial, { type: 'document_move', channel: 'studio', id: 'brief', stackId: 'studio-sketches' });
  state = demoReducer(state, { type: 'document_move', channel: 'product', id: 'product-plan', stackId: 'product-decisions' });
  state = demoReducer(state, { type: 'document', id: 'brief', text: 'The text I wrote after moving the brief' });
  state = demoReducer(state, { type: 'message', channel: 'design', text: 'Keep this newer conversation', time: '12:03' });
  const productUndo = state.stackUndo.product, currentMessages = state.messages, currentTasks = state.tasks;
  state = demoReducer(state, { type: 'stack_undo', channel: 'studio' });
  const brief = state.documents.find(row => row.id === 'brief');
  assert.equal(brief.stackId, 'studio-reference');
  assert.equal(brief.edited, 'The text I wrote after moving the brief');
  assert.equal(brief.owner, 'You'); assert.equal(brief.updatedLabel, 'Just now');
  assert.equal(state.messages, currentMessages); assert.equal(state.tasks, currentTasks);
  assert.equal(state.messages.design.at(-1).text, 'Keep this newer conversation');
  assert.equal(state.stackUndo.product, productUndo); assert.equal(state.stackUndo.studio, undefined);
  assert.equal(state.documents.find(row => row.id === 'product-plan').stackId, 'product-decisions');
  assert.equal(demoReducer(state, { type: 'stack_undo', channel: 'studio' }), state);
  state = demoReducer(state, { type: 'stack_undo', channel: 'product' });
  assert.equal(state.documents.find(row => row.id === 'product-plan').stackId, 'product-reference');
  assert.equal(state.documents[0].edited, brief.edited);
  assert.equal(initial.documents[0].edited, null);
});

test('saved conversation references are scoped, removable and undoable without losing later work', () => {
  let state = createDemoState();
  for (const messageId of ['studio-1', 'studio-2']) state = demoReducer(state, { type: 'stack_reference', channel: 'studio', stackId: 'studio-reference', messageId });
  assert.equal(demoReducer(state, { type: 'stack_reference', channel: 'studio', stackId: 'studio-reference', messageId: 'studio-2' }), state);
  state = demoReducer(state, { type: 'stack_reference', channel: 'product', stackId: 'product-reference', messageId: 'product-1' });
  state = demoReducer(state, { type: 'stack_undo', channel: 'studio' });
  assert.deepEqual(Array.from(demoStackReferences(state, 'studio', 'studio-reference'), row => row.id), ['studio-1']);
  assert.equal(demoStackReferences(state, 'product', 'product-reference')[0].id, 'product-1');
  state = demoReducer(state, { type: 'stack_reference', channel: 'studio', stackId: 'studio-reference', messageId: 'studio-2' });
  state = demoReducer(state, { type: 'stack_reference_remove', channel: 'studio', stackId: 'studio-reference', messageId: 'studio-1' });
  assert.deepEqual(Array.from(demoStackReferences(state, 'studio', 'studio-reference'), row => row.id), ['studio-2']);
  state = demoReducer(state, { type: 'document', id: 'brief', text: 'Keep this edit made after removing a reference' });
  const messages = state.messages, productStacks = state.stacks.product;
  state = demoReducer(state, { type: 'stack_undo', channel: 'studio' });
  assert.deepEqual(Array.from(demoStackReferences(state, 'studio', 'studio-reference'), row => row.id), ['studio-1', 'studio-2']);
  assert.equal(state.messages, messages); assert.equal(state.stacks.product, productStacks);
  assert.equal(state.documents[0].edited, 'Keep this edit made after removing a reference');
  assert.equal(state.stackUndo.product.kind, 'reference_add');
  assert.equal(demoReducer(state, { type: 'stack_undo', channel: 'studio' }), state);
});

test('stack and document creation enforce per-channel limits without consuming ids on rejection', () => {
  const initial = createDemoState();
  for (const action of [
    { type: 'stack_add', channel: 'studio', title: ' ' },
    { type: 'stack_add', channel: 'studio', title: 'x'.repeat(81) },
    { type: 'stack_add', channel: 'studio', title: '  REFERENCE MATERIAL  ' },
    { type: 'document_add', channel: 'studio', stackId: 'studio-reference', title: ' ' },
    { type: 'document_add', channel: 'studio', stackId: 'studio-reference', title: 'x'.repeat(201) },
    { type: 'stack_add', channel: 'constructor', title: 'A stack' },
    { type: 'document_add', channel: 'constructor', stackId: 'studio-reference', title: 'A document' },
  ]) assert.equal(demoReducer(initial, action), initial);
  let state = initial;
  while (state.stacks.studio.length < demoStackLimit) state = demoReducer(state, { type: 'stack_add', channel: 'studio', title: '  Visitor stack ' + state.nextId + '  ' });
  assert.equal(state.stacks.studio.length, demoStackLimit);
  assert.equal(state.stacks.studio.at(-1).sample, false);
  assert.equal(state.stacks.studio.at(-1).title, 'Visitor stack ' + (state.nextId - 1));
  assert.equal(demoReducer(state, { type: 'stack_add', channel: 'studio', title: 'One too many' }), state);
  while (demoChannelDocuments(state, 'studio').length < demoDocumentLimit) state = demoReducer(state, { type: 'document_add', channel: 'studio', stackId: 'studio-reference', title: '  Visitor document ' + state.nextId + '  ' });
  assert.equal(demoChannelDocuments(state, 'studio').length, demoDocumentLimit);
  assert.equal(demoReducer(state, { type: 'document_add', channel: 'studio', stackId: 'studio-reference', title: 'One too many' }), state);
  const last = state.documents.at(-1);
  assert.equal(last.sample, false); assert.equal(last.owner, 'You'); assert.equal(last.updatedLabel, 'Just now');
  assert.equal(last.edited, ''); assert.equal(last.source, '');
  assert.equal(last.id, 'document-' + (state.nextId - 1));
  assert.equal(demoChannelDocuments(state, 'product').length, 2);
  const otherChannel = demoReducer(state, { type: 'document_add', channel: 'product', stackId: 'product-reference', title: 'Product can still create a document' });
  assert.equal(demoChannelDocuments(otherChannel, 'product').length, 3);
  assert.equal(demoChannelDocuments(otherChannel, 'studio').length, demoDocumentLimit);
});

test('stack references stop at the limit while conversation messages remain intact', () => {
  let state = createDemoState();
  while (state.messages.studio.length <= demoStackReferenceLimit) state = demoReducer(state, { type: 'message', channel: 'studio', text: 'A useful reference ' + state.nextId, time: '12:00' });
  for (const message of state.messages.studio.slice(0, demoStackReferenceLimit)) state = demoReducer(state, { type: 'stack_reference', channel: 'studio', stackId: 'studio-reference', messageId: message.id });
  assert.equal(demoStackReferences(state, 'studio', 'studio-reference').length, demoStackReferenceLimit);
  const extraMessage = state.messages.studio.at(-1);
  assert.equal(demoReducer(state, { type: 'stack_reference', channel: 'studio', stackId: 'studio-reference', messageId: extraMessage.id }), state);
  assert.equal(state.messages.studio.length, demoStackReferenceLimit + 1);
  const undone = demoReducer(state, { type: 'stack_undo', channel: 'studio' });
  assert.equal(demoStackReferences(undone, 'studio', 'studio-reference').length, demoStackReferenceLimit - 1);
  assert.equal(undone.messages, state.messages);
});

test('visitor names and document text stay literal even when they match sample translation keys', () => {
  let state = createDemoState();
  const original = state.stacks.studio[0];
  state = demoReducer(state, { type: 'stack_rename', channel: 'studio', id: original.id, title: '  My shared research  ' });
  assert.equal(state.stacks.studio[0].title, 'My shared research');
  assert.equal(state.stacks.studio[0].sample, false);
  state = demoReducer(state, { type: 'stack_rename', channel: 'studio', id: original.id, title: 'MY SHARED RESEARCH' });
  assert.equal(state.stacks.studio[0].title, 'MY SHARED RESEARCH');
  assert.equal(demoReducer(state, { type: 'stack_rename', channel: 'studio', id: original.id, title: 'MY SHARED RESEARCH' }), state);
  assert.equal(demoReducer(state, { type: 'stack_rename', channel: 'studio', id: original.id, title: 'SKETCHES' }), state);
  const nextId = state.nextId;
  state = demoReducer(state, { type: 'document_add', channel: 'studio', stackId: original.id, title: 'Project brief' });
  state = demoReducer(state, { type: 'document', id: 'document-' + nextId, text: '## A quieter courtyard' });
  const document = state.documents.find(row => row.id === 'document-' + nextId);
  const markdown = demoMarkdown(document, () => { assert.fail('Visitor content must not be translated'); });
  assert.equal(markdown, '# Project brief\n\n## A quieter courtyard\n');
  assert.equal(original.title, 'Reference material'); assert.equal(original.sample, true);
});

test('reset removes every visitor stack, document, reference and undo without affecting another visitor', () => {
  const initial = createDemoState(), second = createDemoState();
  let state = demoReducer(initial, { type: 'stack_add', channel: 'studio', title: 'My private draft stack' });
  state = demoReducer(state, { type: 'document_add', channel: 'studio', stackId: state.stacks.studio.at(-1).id, title: 'My private draft document' });
  state = demoReducer(state, { type: 'document', id: state.documents.at(-1).id, text: 'My private draft text' });
  state = demoReducer(state, { type: 'stack_reference', channel: 'studio', stackId: 'studio-reference', messageId: 'studio-1' });
  state = demoReducer(state, { type: 'document_move', channel: 'product', id: 'product-plan', stackId: 'product-sketches' });
  const reset = demoReducer(state, { type: 'reset' });
  assert.equal(JSON.stringify(reset), JSON.stringify(second));
  assert.equal(JSON.stringify(initial), JSON.stringify(second));
  assert.equal(Object.keys(reset.stackUndo).length, 0);
  assert.equal(reset.nextId, 1);
  assert.equal(JSON.stringify(reset).includes('My private'), false);
});

test('confirmed document relationships reject reversed duplicates and foreign endpoints atomically', () => {
  const initial = createDemoState();
  const state = demoReducer(initial, { type: 'document_link', channel: 'studio', documentId: 'brief', relatedDocumentId: 'notes' });
  const link = state.documentLinks.studio[0];
  assert.equal(link.id, 'link-' + initial.nextId);
  assert.equal(link.documentId, 'brief'); assert.equal(link.relatedDocumentId, 'notes');
  assert.equal(state.nextId, initial.nextId + 1);
  assert.equal(state.documents, initial.documents); assert.equal(state.stacks, initial.stacks);
  assert.equal(state.documentLinks.product, initial.documentLinks.product);
  assert.equal(state.stackUndo.studio.kind, 'document_link');
  assert.equal(state.stackUndo.studio.linkId, link.id); assert.equal(state.stackUndo.studio.stackId, 'studio-reference');
  for (const action of [
    { type: 'document_link', channel: 'studio', documentId: 'brief', relatedDocumentId: 'notes' },
    { type: 'document_link', channel: 'studio', documentId: 'notes', relatedDocumentId: 'brief' },
    { type: 'document_link', channel: 'studio', documentId: 'brief', relatedDocumentId: 'brief' },
    { type: 'document_link', channel: 'studio', documentId: 'brief', relatedDocumentId: 'missing' },
    { type: 'document_link', channel: 'studio', documentId: 'missing', relatedDocumentId: 'notes' },
    { type: 'document_link', channel: 'studio', documentId: 'brief', relatedDocumentId: 'product-plan' },
    { type: 'document_link', channel: 'studio', documentId: 'product-plan', relatedDocumentId: 'brief' },
    { type: 'document_link', channel: 'constructor', documentId: 'brief', relatedDocumentId: 'notes' },
    { type: 'document_unlink', channel: 'product', id: link.id },
    { type: 'document_unlink', channel: 'studio', id: 'missing' },
    { type: 'document_unlink', channel: 'constructor', id: link.id },
  ]) assert.equal(demoReducer(state, action), state, JSON.stringify(action));
  assert.equal(initial.documentLinks.studio.length, 0);
});

test('undoing a confirmed relationship preserves later document edits, dismissals and another channel', () => {
  let state = demoReducer(createDemoState(), { type: 'document_link', channel: 'studio', documentId: 'brief', relatedDocumentId: 'materials' });
  state = demoReducer(state, { type: 'document_link', channel: 'product', documentId: 'product-plan', relatedDocumentId: 'product-walkthrough' });
  state = demoReducer(state, { type: 'document', id: 'brief', text: 'A revision after confirming the relationship' });
  state = demoReducer(state, { type: 'message', channel: 'design', text: 'An independent conversation', time: '12:05' });
  state = demoReducer(state, { type: 'connection_dismiss', channel: 'studio', id: 'another-suggestion' });
  const documents = state.documents, messages = state.messages, dismissed = state.dismissedConnections, productLinks = state.documentLinks.product, productUndo = state.stackUndo.product, nextId = state.nextId;
  state = demoReducer(state, { type: 'stack_undo', channel: 'studio' });
  assert.equal(state.documentLinks.studio.length, 0);
  assert.equal(state.documents, documents); assert.equal(state.messages, messages);
  assert.equal(state.documents[0].edited, 'A revision after confirming the relationship');
  assert.equal(state.dismissedConnections, dismissed); assert.equal(state.documentLinks.product, productLinks);
  assert.equal(state.stackUndo.product, productUndo); assert.equal(state.nextId, nextId);
  assert.equal(demoReducer(state, { type: 'stack_undo', channel: 'studio' }), state);
});

test('unlink undo restores the same relationship in order without restoring stale document snapshots', () => {
  let state = createDemoState();
  for (const [documentId, relatedDocumentId] of [['brief', 'notes'], ['materials', 'notes'], ['brief', 'decision']]) state = demoReducer(state, { type: 'document_link', channel: 'studio', documentId, relatedDocumentId });
  const originalLinks = state.documentLinks.studio, removed = originalLinks[1], nextId = state.nextId;
  state = demoReducer(state, { type: 'document_unlink', channel: 'studio', id: removed.id });
  assert.deepEqual(Array.from(state.documentLinks.studio, link => link.id), [originalLinks[0].id, originalLinks[2].id]);
  assert.equal(state.stackUndo.studio.kind, 'document_unlink'); assert.equal(state.stackUndo.studio.stackId, 'studio-sketches');
  assert.equal(state.nextId, nextId);
  state = demoReducer(state, { type: 'document', id: 'materials', text: 'A better sketch description' });
  state = demoReducer(state, { type: 'document', id: 'notes', text: 'New notes after unlinking' });
  state = demoReducer(state, { type: 'connection_dismiss', channel: 'studio', id: 'keep-this-dismissal' });
  const documents = state.documents, dismissed = state.dismissedConnections;
  state = demoReducer(state, { type: 'stack_undo', channel: 'studio' });
  assert.deepEqual(Array.from(state.documentLinks.studio, link => link.id), Array.from(originalLinks, link => link.id));
  assert.equal(state.documentLinks.studio[1], removed);
  assert.equal(state.documents, documents); assert.equal(state.dismissedConnections, dismissed);
  assert.equal(state.documents.find(row => row.id === 'notes').edited, 'New notes after unlinking');
  assert.equal(demoReducer(state, { type: 'stack_undo', channel: 'studio' }), state);
});

test('stale relationship undo never restores a missing endpoint, cross-channel pair or duplicate', () => {
  const linked = demoReducer(createDemoState(), { type: 'document_link', channel: 'studio', documentId: 'brief', relatedDocumentId: 'notes' });
  const link = linked.documentLinks.studio[0], unlinked = demoReducer(linked, { type: 'document_unlink', channel: 'studio', id: link.id });
  const staleStates = [
    { ...unlinked, documents: unlinked.documents.filter(document => document.id !== 'brief') },
    { ...unlinked, documents: unlinked.documents.map(document => document.id === 'notes' ? { ...document, channel: 'product' } : document) },
    { ...unlinked, documentLinks: { ...unlinked.documentLinks, studio: [{ id: 'new-link', documentId: 'notes', relatedDocumentId: 'brief' }] } },
    { ...unlinked, documentLinks: { ...unlinked.documentLinks, studio: [{ id: link.id, documentId: 'materials', relatedDocumentId: 'decision' }] } },
    { ...linked, documentLinks: { ...linked.documentLinks, studio: [] } },
  ];
  for (const stale of staleStates) assert.equal(demoReducer(stale, { type: 'stack_undo', channel: 'studio' }), stale);
  const foreignLink = { ...linked, documents: linked.documents.map(document => document.id === 'notes' ? { ...document, channel: 'product' } : document) };
  assert.equal(demoReducer(foreignLink, { type: 'document_unlink', channel: 'studio', id: link.id }), foreignLink);
});

test('relationship limits allow the final valid pair and reject add or undo overflow independently per channel', () => {
  let state = createDemoState();
  while (demoChannelDocuments(state, 'studio').length < 16) state = demoReducer(state, { type: 'document_add', channel: 'studio', stackId: 'studio-reference', title: 'Additional document ' + state.nextId });
  const documents = demoChannelDocuments(state, 'studio'), pairs = [];
  for (let first = 0; first < documents.length; first++) for (let second = first + 1; second < documents.length; second++) pairs.push([documents[first].id, documents[second].id]);
  for (const [documentId, relatedDocumentId] of pairs.slice(0, demoDocumentLinkLimit - 1)) state = demoReducer(state, { type: 'document_link', channel: 'studio', documentId, relatedDocumentId });
  assert.equal(state.documentLinks.studio.length, demoDocumentLinkLimit - 1);
  const beforeFinalId = state.nextId, [lastDocumentId, lastRelatedId] = pairs[demoDocumentLinkLimit - 1];
  state = demoReducer(state, { type: 'document_link', channel: 'studio', documentId: lastDocumentId, relatedDocumentId: lastRelatedId });
  assert.equal(state.documentLinks.studio.length, demoDocumentLinkLimit); assert.equal(state.nextId, beforeFinalId + 1);
  const [extraDocumentId, extraRelatedId] = pairs[demoDocumentLinkLimit];
  assert.equal(demoReducer(state, { type: 'document_link', channel: 'studio', documentId: extraDocumentId, relatedDocumentId: extraRelatedId }), state);
  const other = demoReducer(state, { type: 'document_link', channel: 'product', documentId: 'product-plan', relatedDocumentId: 'product-walkthrough' });
  assert.equal(other.documentLinks.product.length, 1); assert.equal(other.documentLinks.studio, state.documentLinks.studio);
  const removed = demoReducer(state, { type: 'document_unlink', channel: 'studio', id: state.documentLinks.studio[0].id });
  const fullWithNewPair = { ...removed, documentLinks: { ...removed.documentLinks, studio: [...removed.documentLinks.studio, { id: 'replacement', documentId: extraDocumentId, relatedDocumentId: extraRelatedId }] } };
  assert.equal(demoReducer(fullWithNewPair, { type: 'stack_undo', channel: 'studio' }), fullWithNewPair);
  const restored = demoReducer(removed, { type: 'stack_undo', channel: 'studio' });
  assert.equal(restored.documentLinks.studio.length, demoDocumentLinkLimit);
  assert.equal(restored.documentLinks.studio[0].id, state.documentLinks.studio[0].id);
});

test('dismissed suggestions are bounded, opaque and scoped without consuming ids or replacing work undo', () => {
  const initial = demoReducer(createDemoState(), { type: 'document_link', channel: 'studio', documentId: 'brief', relatedDocumentId: 'notes' });
  for (const action of [
    { type: 'connection_dismiss', channel: 'studio', id: '' },
    { type: 'connection_dismiss', channel: 'studio', id: '   ' },
    { type: 'connection_dismiss', channel: 'studio', id: 'x'.repeat(demoConnectionIdLimit + 1) },
    { type: 'connection_dismiss', channel: 'constructor', id: 'suggestion' },
    { type: 'connection_restore_dismissed', channel: 'constructor' },
    { type: 'connection_restore_dismissed', channel: 'studio' },
  ]) assert.equal(demoReducer(initial, action), initial);
  const boundaryId = 'x'.repeat(demoConnectionIdLimit);
  let state = demoReducer(initial, { type: 'connection_dismiss', channel: 'studio', id: boundaryId });
  assert.equal(state.dismissedConnections.studio[0], boundaryId);
  assert.equal(demoReducer(state, { type: 'connection_dismiss', channel: 'studio', id: boundaryId }), state);
  for (let index = 1; index < demoDismissedConnectionLimit; index++) state = demoReducer(state, { type: 'connection_dismiss', channel: 'studio', id: 'suggestion-' + index });
  assert.equal(state.dismissedConnections.studio.length, demoDismissedConnectionLimit);
  assert.equal(demoReducer(state, { type: 'connection_dismiss', channel: 'studio', id: 'one-too-many' }), state);
  assert.equal(state.nextId, initial.nextId); assert.equal(state.stackUndo, initial.stackUndo);
  state = demoReducer(state, { type: 'connection_dismiss', channel: 'product', id: boundaryId });
  assert.equal(state.dismissedConnections.product.length, 1);
  const productDismissals = state.dismissedConnections.product, links = state.documentLinks, undo = state.stackUndo;
  state = demoReducer(state, { type: 'connection_restore_dismissed', channel: 'studio' });
  assert.equal(state.dismissedConnections.studio.length, 0); assert.equal(state.dismissedConnections.product, productDismissals);
  assert.equal(state.documentLinks, links); assert.equal(state.stackUndo, undo); assert.equal(state.documents, initial.documents);
  assert.equal(demoReducer(state, { type: 'connection_restore_dismissed', channel: 'studio' }), state);
});

test('confirmed and dismissed relationships never leak between visitor instances or survive reset', () => {
  const initial = createDemoState(), second = createDemoState();
  assert.notEqual(initial.documentLinks.studio, second.documentLinks.studio);
  assert.notEqual(initial.dismissedConnections.studio, second.dismissedConnections.studio);
  assert.notEqual(initial.documentLinks.studio, initial.documentLinks.product);
  assert.notEqual(initial.dismissedConnections.studio, initial.dismissedConnections.product);
  let state = demoReducer(initial, { type: 'document_link', channel: 'studio', documentId: 'brief', relatedDocumentId: 'notes' });
  state = demoReducer(state, { type: 'connection_dismiss', channel: 'studio', id: 'visitor-only-suggestion' });
  assert.equal(initial.documentLinks.studio.length, 0); assert.equal(second.documentLinks.studio.length, 0);
  assert.equal(initial.dismissedConnections.studio.length, 0); assert.equal(second.dismissedConnections.studio.length, 0);
  const reset = demoReducer(state, { type: 'reset' });
  assert.equal(JSON.stringify(reset), JSON.stringify(second));
  assert.equal(reset.nextId, 1); assert.equal(Object.keys(reset.stackUndo).length, 0);
  assert.equal(JSON.stringify(reset).includes('visitor-only-suggestion'), false);
});

test('tasks created from sample or visitor messages retain a literal citation and leave the message untouched', () => {
  const initial = createDemoState();
  const sample = demoReducer(initial, { type: 'task_from_message', channel: 'studio', messageId: 'studio-3', text: '  Bring the material samples  ', owner: 'Noor' });
  const sampleTask = sample.tasks.at(-1);
  assert.equal(sampleTask.id, 'local-' + initial.nextId); assert.equal(sample.nextId, initial.nextId + 1);
  assert.equal(sampleTask.text, 'Bring the material samples'); assert.equal(sampleTask.owner, 'Noor');
  assert.equal(sampleTask.sample, false); assert.equal(sampleTask.group, 'Team'); assert.equal(sampleTask.done, false);
  assert.equal(sampleTask.source.channel, 'studio'); assert.equal(sampleTask.source.messageId, 'studio-3');
  assert.equal(sample.messages, initial.messages); assert.equal(sample.documents, initial.documents);
  assert.equal(initial.tasks.at(-1).source, undefined);
  const sent = demoReducer(sample, { type: 'message', channel: 'design', text: 'Use <script> as literal planning text', time: '12:06' });
  const message = sent.messages.design.at(-1);
  const derived = demoReducer(sent, { type: 'task_from_message', channel: 'design', messageId: message.id, text: message.text });
  const task = derived.tasks.at(-1);
  assert.equal(task.text, message.text); assert.equal(task.owner, undefined);
  assert.equal(task.source.channel, 'design'); assert.equal(task.source.messageId, message.id);
  assert.equal(derived.messages, sent.messages); assert.equal(derived.messages.design.at(-1), message);
  assert.equal(derived.nextId, sent.nextId + 1); assert.notEqual(task.id, message.id);
});

test('creating another task from the same cited message preserves the existing task and its completion', () => {
  let state = demoReducer(createDemoState(), { type: 'task_from_message', channel: 'studio', messageId: 'studio-1', text: 'Keep the first task', owner: 'Maja' });
  const task = state.tasks.at(-1);
  assert.equal(demoReducer(state, { type: 'task_from_message', channel: 'studio', messageId: 'studio-1', text: 'A different proposed title', owner: 'Noor' }), state);
  state = demoReducer(state, { type: 'task_toggle', id: task.id });
  assert.equal(state.tasks.at(-1).done, true);
  assert.equal(demoReducer(state, { type: 'task_from_message', channel: 'studio', messageId: 'studio-1', text: 'Do not reset the completed task' }), state);
  const differentMessage = demoReducer(state, { type: 'task_from_message', channel: 'studio', messageId: 'studio-2', text: task.text });
  assert.equal(differentMessage.tasks.length, state.tasks.length + 1);
  assert.equal(differentMessage.tasks.at(-1).source.messageId, 'studio-2');
  const fixture = createDemoState();
  fixture.messages.studio[0].id = 'same-message-id'; fixture.messages.product[0].id = 'same-message-id';
  let scoped = demoReducer(fixture, { type: 'task_from_message', channel: 'studio', messageId: 'same-message-id', text: 'A studio task' });
  scoped = demoReducer(scoped, { type: 'task_from_message', channel: 'product', messageId: 'same-message-id', text: 'A product task' });
  assert.equal(scoped.tasks.length, fixture.tasks.length + 2);
  assert.equal(scoped.tasks.at(-2).source.channel, 'studio'); assert.equal(scoped.tasks.at(-1).source.channel, 'product');
});

test('message-derived tasks require a current visible source, bounded text and a valid owner', () => {
  const initial = createDemoState();
  for (const action of [
    { channel: 'studio', messageId: 'missing', text: 'A task' },
    { channel: 'studio', messageId: 'product-1', text: 'A task' },
    { channel: 'constructor', messageId: 'studio-1', text: 'A task' },
    { channel: 'studio', messageId: 'studio-1', text: ' ' },
    { channel: 'studio', messageId: 'studio-1', text: 'x'.repeat(181) },
    { channel: 'studio', messageId: 'studio-1', text: 'A task', owner: 'not-a-fictional-teammate' },
  ]) assert.equal(demoReducer(initial, { type: 'task_from_message', ...action }), initial, JSON.stringify(action));
  const stale = { ...initial, messages: { ...initial.messages, studio: initial.messages.studio.filter(message => message.id !== 'studio-1') } };
  assert.equal(demoReducer(stale, { type: 'task_from_message', channel: 'studio', messageId: 'studio-1', text: 'A task from a removed source' }), stale);
  const boundary = demoReducer(initial, { type: 'task_from_message', channel: 'studio', messageId: 'studio-1', text: '  ' + 'x'.repeat(180) + '  ' });
  assert.equal(boundary.tasks.at(-1).text.length, 180); assert.equal(boundary.nextId, initial.nextId + 1);
});

test('message-derived tasks share the global task capacity and accept exactly the final available place', () => {
  let state = createDemoState();
  while (state.tasks.length < 49) state = demoReducer(state, { type: 'task_add', text: 'An existing shared task' });
  const previousId = state.nextId;
  state = demoReducer(state, { type: 'task_from_message', channel: 'studio', messageId: 'studio-1', text: 'The final task' });
  assert.equal(state.tasks.length, 50); assert.equal(state.nextId, previousId + 1);
  assert.equal(state.tasks.at(-1).source.messageId, 'studio-1');
  assert.equal(demoReducer(state, { type: 'task_from_message', channel: 'studio', messageId: 'studio-2', text: 'One task too many' }), state);
  assert.equal(demoReducer(state, { type: 'task_from_message', channel: 'product', messageId: 'product-1', text: 'The cap also applies in another channel' }), state);
  assert.equal(demoReducer(state, { type: 'task_from_message', channel: 'studio', messageId: 'studio-1', text: 'A repeated proposal at capacity', owner: 'Noor' }), state);
});

test('undoing a newly created source task preserves newer work and allows a fresh task from that source', () => {
  const initial = createDemoState();
  let state = demoReducer(initial, { type: 'task_from_message', channel: 'studio', messageId: 'studio-3', text: '  Bring two samples  ', owner: 'Elias' });
  const created = state.tasks.at(-1);
  state = demoReducer(state, { type: 'task_from_message', channel: 'product', messageId: 'product-1', text: 'Keep the product task' });
  const productTask = state.tasks.at(-1);
  state = demoReducer(state, { type: 'task_toggle', id: productTask.id });
  state = demoReducer(state, { type: 'document', id: 'brief', text: 'Keep this newer document edit' });
  state = demoReducer(state, { type: 'message', channel: 'design', text: 'Keep this newer message', time: '12:07' });
  state = demoReducer(state, { type: 'document_link', channel: 'studio', documentId: 'brief', relatedDocumentId: 'notes' });
  const documents = state.documents, messages = state.messages, links = state.documentLinks, stackUndo = state.stackUndo, nextId = state.nextId;
  const action = { type: 'task_undo_created', channel: 'studio', id: created.id, text: created.text, owner: created.owner };
  state = demoReducer(state, action);
  assert.equal(state.tasks.some(task => task.id === created.id), false);
  assert.equal(state.tasks.find(task => task.id === productTask.id).done, true);
  assert.equal(state.documents, documents); assert.equal(state.messages, messages);
  assert.equal(state.documentLinks, links); assert.equal(state.stackUndo, stackUndo); assert.equal(state.nextId, nextId);
  assert.equal(demoReducer(state, action), state);
  const recreated = demoReducer(state, { type: 'task_from_message', channel: 'studio', messageId: 'studio-3', text: 'A fresh task from the same message' });
  assert.equal(recreated.tasks.at(-1).source.messageId, created.source.messageId);
  assert.notEqual(recreated.tasks.at(-1).id, created.id); assert.equal(recreated.nextId, nextId + 1);
  const withoutCurrentSource = { ...recreated, messages: { ...recreated.messages, studio: recreated.messages.studio.filter(message => message.id !== 'studio-3') } };
  const latest = recreated.tasks.at(-1);
  const undoneStaleSource = demoReducer(withoutCurrentSource, { type: 'task_undo_created', channel: 'studio', id: latest.id, text: latest.text });
  assert.equal(undoneStaleSource.tasks.some(task => task.id === latest.id), false);
  assert.equal(undoneStaleSource.messages, withoutCurrentSource.messages);
});

test('source-task undo rejects foreign, completed or changed tasks without deleting their newer state', () => {
  const derived = demoReducer(createDemoState(), { type: 'task_from_message', channel: 'studio', messageId: 'studio-1', text: 'A new task', owner: 'Maja' });
  const task = derived.tasks.at(-1), action = { type: 'task_undo_created', channel: 'studio', id: task.id, text: task.text, owner: task.owner };
  for (const changedAction of [
    { ...action, channel: 'product' },
    { ...action, channel: 'constructor' },
    { ...action, id: 'missing' },
    { ...action, text: 'An outdated title' },
    { ...action, text: '  ' + task.text + '  ' },
    { ...action, owner: 'Noor' },
    { ...action, owner: undefined },
  ]) assert.equal(demoReducer(derived, changedAction), derived);
  const completed = demoReducer(derived, { type: 'task_toggle', id: task.id });
  assert.equal(demoReducer(completed, action), completed);
  for (const patch of [{ text: 'A later edit' }, { owner: 'Noor' }, { source: { channel: 'product', messageId: 'product-1' } }, { sample: true }, { group: 'Design' }]) {
    const changed = { ...derived, tasks: derived.tasks.map(row => row.id === task.id ? { ...row, ...patch } : row) };
    assert.equal(demoReducer(changed, action), changed);
  }
  const ordinary = demoReducer(createDemoState(), { type: 'task_add', text: task.text });
  assert.equal(demoReducer(ordinary, { type: 'task_undo_created', channel: 'studio', id: ordinary.tasks.at(-1).id, text: task.text }), ordinary);
  const sampleTask = derived.tasks[0];
  assert.equal(demoReducer(derived, { type: 'task_undo_created', channel: 'studio', id: sampleTask.id, text: sampleTask.text }), derived);
});

test('source tasks and their citation objects stay local to one visitor and disappear on reset', () => {
  const initial = createDemoState(), second = createDemoState();
  const firstDerived = demoReducer(initial, { type: 'task_from_message', channel: 'studio', messageId: 'studio-1', text: 'Visitor-only follow-up' });
  const secondDerived = demoReducer(second, { type: 'task_from_message', channel: 'studio', messageId: 'studio-1', text: 'Another visitor follow-up' });
  assert.notEqual(firstDerived.tasks.at(-1).source, secondDerived.tasks.at(-1).source);
  assert.equal(initial.tasks.some(task => task.source), false); assert.equal(second.tasks.some(task => task.source), false);
  const reset = demoReducer(firstDerived, { type: 'reset' });
  assert.equal(reset.tasks.some(task => task.source), false); assert.equal(reset.nextId, 1);
  assert.equal(JSON.stringify(reset), JSON.stringify(initial));
  assert.equal(JSON.stringify(reset).includes('Visitor-only follow-up'), false);
  assert.equal(secondDerived.tasks.at(-1).text, 'Another visitor follow-up');
});
