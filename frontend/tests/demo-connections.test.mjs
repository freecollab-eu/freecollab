import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
function load(path) {
  const exports = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText, { exports, URLSearchParams });
  return exports;
}
const { createDemoState, demoReducer } = load('../src/lib/demo-showroom.ts');
const { suggestDemoConnections } = load('../src/lib/demo-connections.ts');
const plain = value => JSON.parse(JSON.stringify(value));
function empty() { const state = createDemoState(); state.documents = []; for (const channel of ['studio', 'product', 'design']) state.messages[channel] = []; return state; }
function document(state, { id, title, text = '', channel = 'studio', stackId = channel + '-reference', edited = null, sample = false }) {
  const value = { id, channel, stackId, title, source: text, edited, sample, owner: 'You', updatedLabel: 'Just now' }; state.documents.push(value); return value;
}
function message(state, text, id = 'test-message', channel = 'studio') { const value = { id, author: 'You', text, time: '12:00', sample: false }; state.messages[channel].push(value); return value; }
function pair(state, left, right) { return suggestDemoConnections(state, 'studio').find(row => row.kind === 'document_link' && [row.documentId, row.relatedDocumentId].sort().join('|') === [left, right].sort().join('|')); }

test('the actual sketch message finds its relevant sample document and stack without mutation', () => {
  const state = createDemoState(), before = JSON.stringify(state), sketch = suggestDemoConnections(state, 'studio').find(row => row.kind === 'message_stack' && row.messageId === 'studio-3');
  assert.equal(sketch.documentId, 'materials'); assert.equal(sketch.stackId, 'studio-sketches'); assert.equal(sketch.reason.kind, 'shared_words');
  for (const word of sketch.reason.words) assert.ok(state.messages.studio[2].text.toLowerCase().includes(word.toLowerCase()), word);
  assert.equal(JSON.stringify(state), before);
});
test('new visitor content and custom stack names drive suggestions without sample IDs', () => {
  const state = empty(); state.stacks.studio.push({ id: 'custom-launch', title: 'Lunar propulsion', sample: false, references: [] });
  document(state, { id: 'visitor-spec', title: 'Lunar propulsion notes', text: 'Review the ceramic rocket nozzle prototype and cooling system.', stackId: 'custom-launch' });
  message(state, 'The ceramic rocket nozzle prototype is ready.', 'visitor-question');
  const suggestion = suggestDemoConnections(state, 'studio').find(row => row.kind === 'message_stack');
  assert.equal(suggestion?.documentId, 'visitor-spec'); assert.equal(suggestion?.stackId, 'custom-launch'); assert.ok(suggestion.reason.words.every(word => /ceramic|rocket|nozzle|prototype/i.test(word)));
});
test('weak generic words, blanks and unknown channels do not invent connections', () => {
  const state = empty(); document(state, { id: 'generic', title: 'Project document', text: 'A shared plan for the team. Keep the next step clear.' });
  document(state, { id: 'blank', title: '', text: '', stackId: 'studio-sketches' }); message(state, 'The team has a shared project and a clear next step.');
  assert.equal(suggestDemoConnections(state, 'studio').length, 0); assert.equal(suggestDemoConnections(state, 'missing').length, 0);
});
test('current edits replace the old source, including an intentionally blank edit', () => {
  let state = empty(); document(state, { id: 'amethyst', title: 'Document', text: 'Amethyst geothermal borehole survey.' }); message(state, 'The amethyst geothermal borehole survey arrived.');
  assert.ok(suggestDemoConnections(state, 'studio').some(row => row.kind === 'message_stack'));
  state = demoReducer(state, { type: 'document', id: 'amethyst', text: 'Ceramic rocket nozzle prototype.' }); assert.equal(suggestDemoConnections(state, 'studio').length, 0);
  state = demoReducer(state, { type: 'document', id: 'amethyst', text: '' }); assert.equal(suggestDemoConnections(state, 'studio').length, 0);
});
test('records are never borrowed from another channel or an invalid stack', () => {
  const state = empty(); document(state, { id: 'foreign-secret', title: 'Amethyst borehole survey', text: 'Amethyst geothermal borehole survey.', channel: 'design' });
  document(state, { id: 'wrong-stack', title: 'Amethyst borehole survey', text: 'Amethyst geothermal borehole survey.', stackId: 'design-sketches' }); message(state, 'Review Amethyst borehole survey and geothermal samples.');
  assert.equal(suggestDemoConnections(state, 'studio').length, 0); message(state, 'Amethyst borehole survey', 'foreign-message', 'design');
  const design = suggestDemoConnections(state, 'design'); assert.ok(design.some(row => row.kind === 'message_stack' && row.messageId === 'foreign-message'));
  assert.ok(design.every(row => row.channel === 'design' && !row.id.includes('wrong-stack') && !row.id.includes('test-message')));
});
test('saved references and dismissed identities disappear; undo and restore re-enable them', () => {
  let state = createDemoState(); const suggestion = suggestDemoConnections(state, 'studio').find(row => row.kind === 'message_stack'), before = JSON.stringify(state.messages.studio);
  state = demoReducer(state, { type: 'stack_reference', channel: 'studio', stackId: suggestion.stackId, messageId: suggestion.messageId }); assert.ok(!suggestDemoConnections(state, 'studio').some(row => row.id === suggestion.id));
  state = demoReducer(state, { type: 'stack_undo', channel: 'studio' }); assert.ok(suggestDemoConnections(state, 'studio').some(row => row.id === suggestion.id));
  state = demoReducer(state, { type: 'connection_dismiss', channel: 'studio', id: suggestion.id }); assert.ok(!suggestDemoConnections(state, 'studio').some(row => row.id === suggestion.id));
  state = demoReducer(state, { type: 'connection_restore_dismissed', channel: 'studio' }); assert.ok(suggestDemoConnections(state, 'studio').some(row => row.id === suggestion.id)); assert.equal(JSON.stringify(state.messages.studio), before);
});
test('a descriptive document title can anchor a move; moving or renaming removes obsolete advice', () => {
  let state = empty(); state.stacks.studio.push({ id: 'visitors-stack', title: 'Lunar propulsion', sample: false, references: [] }); document(state, { id: 'visitor-doc', title: 'Lunar propulsion specification', text: 'A nozzle cooling prototype.' });
  const move = suggestDemoConnections(state, 'studio').find(row => row.kind === 'document_stack'); assert.equal(move?.documentId, 'visitor-doc'); assert.equal(move?.stackId, 'visitors-stack'); assert.equal(move.reason.kind, 'stack_title');
  const renamed = demoReducer(state, { type: 'stack_rename', channel: 'studio', id: 'visitors-stack', title: 'Invoice archive' }); assert.ok(!suggestDemoConnections(renamed, 'studio').some(row => row.id === move.id));
  state = demoReducer(state, { type: 'document_move', channel: 'studio', id: move.documentId, stackId: move.stackId }); assert.ok(!suggestDemoConnections(state, 'studio').some(row => row.id === move.id));
});
test('mentioning another stack in general notes does not recommend moving the whole document', () => {
  const state = empty(); document(state, { id: 'general', title: 'Studio notes', text: 'Record decisions before the walkthrough. Keep the courtyard welcoming.' });
  document(state, { id: 'agreed', title: 'Agreed direction', text: 'Record decisions before the walkthrough. Keep the courtyard welcoming.', stackId: 'studio-decisions' });
  assert.ok(!suggestDemoConnections(state, 'studio').some(row => row.kind === 'document_stack' && row.documentId === 'general'));
});
test('undirected document links find title mentions in either direction and deduplicate saved pairs', () => {
  let state = empty(); document(state, { id: 'earlier', title: 'Acoustic charter', text: 'Absorption targets.' }); document(state, { id: 'later', title: 'Inspection record', text: 'See Acoustic charter.', stackId: 'studio-sketches' });
  const suggestion = pair(state, 'earlier', 'later'); assert.ok(suggestion); assert.equal(suggestion.documentId, 'later'); assert.equal(suggestion.reason.kind, 'document_title'); assert.deepEqual(plain(suggestion.reason.words), ['Acoustic charter']);
  assert.equal(pair({ ...state, documents: [...state.documents].reverse() }, 'earlier', 'later')?.id, suggestion.id);
  state = demoReducer(state, { type: 'document_link', channel: 'studio', documentId: 'later', relatedDocumentId: 'earlier' }); assert.ok(!pair(state, 'earlier', 'later'));
});
test('symmetric similarities produce an explicit link rather than reciprocal document moves', () => {
  const state = empty(); state.stacks.studio = [{ id: 'alpha', title: 'Rocket Alpha', sample: false, references: [] }, { id: 'beta', title: 'Rocket Beta', sample: false, references: [] }];
  for (const [id, stackId] of [['first', 'alpha'], ['second', 'beta']]) document(state, { id, stackId, title: 'Rocket Alpha / Rocket Beta', text: 'Ceramic nozzle prototype cooling chamber ignition turbine pressure.' });
  const result = suggestDemoConnections(state, 'studio'); assert.ok(result.some(row => row.kind === 'document_link')); assert.ok(!result.some(row => row.kind === 'document_stack'));
});
test('Swedish visitor words match English concepts while displayed spelling stays literal', () => {
  const state = empty(); document(state, { id: 'spec', title: 'Materials & sketches', text: 'A sketch of the table by the doorway.', stackId: 'studio-sketches' }); message(state, 'Skissen visar bordet vid dörröppningen.');
  const suggestion = suggestDemoConnections(state, 'studio').find(row => row.kind === 'message_stack'); assert.equal(suggestion?.documentId, 'spec'); assert.ok(suggestion.reason.words.every(word => state.messages.studio[0].text.includes(word))); assert.ok(suggestion.reason.words.includes('Skissen'));
});
test('translated samples supply visible reasons; visitor titles and current edits bypass translation', () => {
  const state = empty(), calls = []; const row = document(state, { id: 'sample', title: 'Materials & sketches', text: 'A sketch of the table by the doorway.', stackId: 'studio-sketches', sample: true }); state.messages.studio.push({ id: 'sample-message', author: 'Maja', text: 'The sketch has a table and doorway.', time: '12:00', sample: true });
  const translations = { 'Materials & sketches': 'Material och skisser', 'A sketch of the table by the doorway.': 'En skiss av bordet vid dörröppningen.', 'The sketch has a table and doorway.': 'Skissen visar bordet vid dörröppningen.' };
  const translate = source => { calls.push(source); return translations[source] ?? source; }; const suggestion = suggestDemoConnections(state, 'studio', translate).find(row => row.kind === 'message_stack'); assert.ok(suggestion); assert.ok(suggestion.reason.words.every(word => translations[state.messages.studio[0].text].includes(word)));
  row.edited = 'Ceramic rocket nozzle prototype.'; row.sample = false; row.title = 'Literal <i>rocket</i> specification'; message(state, 'Ceramic rocket nozzle prototype.', 'visitor'); calls.length = 0;
  assert.ok(suggestDemoConnections(state, 'studio', translate).some(row => row.kind === 'message_stack' && row.messageId === 'visitor')); assert.ok(!calls.includes(row.edited)); assert.ok(!calls.includes(row.title));
});
test('ranking and IDs stay stable; suggestions are bounded and full stacks do not accept references', () => {
  const state = empty(); document(state, { id: 'spec', title: 'Lunar propulsion specification', text: 'Ceramic rocket nozzle prototype cooling system.', stackId: 'studio-sketches' });
  for (let index = 0; index < 100; index++) message(state, 'The ceramic rocket nozzle prototype cooling system is ready.', 'message-' + index);
  const first = plain(suggestDemoConnections(state, 'studio')); assert.deepEqual(plain(suggestDemoConnections(state, 'studio')), first); assert.ok(first.length <= 6); assert.equal(first.filter(row => row.kind === 'message_stack').length, 3);
  state.stacks.studio.find(row => row.id === 'studio-sketches').references = Array.from({ length: 30 }, (_, index) => 'saved-' + index); assert.ok(!suggestDemoConnections(state, 'studio').some(row => row.kind === 'message_stack'));
});
test('only the bounded document and message windows are analyzed, with no draft input', () => {
  const state = empty(); for (let index = 0; index < 30; index++) document(state, { id: 'blank-' + index, title: '', text: '' }); document(state, { id: 'beyond-bound', title: 'Geothermal amethyst survey', text: 'Geothermal amethyst borehole survey.' }); message(state, 'Geothermal amethyst borehole survey.');
  assert.equal(suggestDemoConnections(state, 'studio').length, 0); state.documents = state.documents.slice(-1); for (let index = 0; index < 100; index++) message(state, 'Hello team', 'recent-' + index); assert.equal(suggestDemoConnections(state, 'studio').length, 0); assert.ok(!Object.hasOwn(state, 'drafts'));
});
test('the confirmed-link capacity suppresses proposals that cannot be accepted', () => {
  let state = empty();
  for (let index = 0; index < 15; index++) document(state, { id: 'capacity-' + index, title: 'Specification ' + index, text: 'Ceramic rocket prototype.', stackId: index % 2 ? 'studio-sketches' : 'studio-reference' });
  for (let left = 0; left < 15; left++) for (let right = left + 1; right < 15; right++) {
    if (state.documentLinks.studio.length < 100) state = demoReducer(state, { type: 'document_link', channel: 'studio', documentId: 'capacity-' + left, relatedDocumentId: 'capacity-' + right });
  }
  assert.equal(state.documentLinks.studio.length, 100);
  assert.ok(!suggestDemoConnections(state, 'studio').some(row => row.kind === 'document_link'));
  const freed = demoReducer(state, { type: 'document_unlink', channel: 'studio', id: state.documentLinks.studio[0].id });
  assert.ok(suggestDemoConnections(freed, 'studio').some(row => row.kind === 'document_link'));
});
test('document context is filtered before global ranking and rejects a foreign focus', () => {
  const state = empty();
  for (let index = 0; index < 10; index++) document(state, { id: 'high-' + index, title: 'Rocket nozzle control', text: 'Ceramic propulsion cooling prototype.', stackId: index % 2 ? 'studio-sketches' : 'studio-reference' });
  document(state, { id: 'focus', title: 'Document', text: 'Moonstone acoustic lantern.', stackId: 'studio-reference' });
  document(state, { id: 'related-focus', title: 'Project document', text: 'Moonstone acoustic lantern.', stackId: 'studio-decisions' });
  document(state, { id: 'foreign-focus', title: 'Moonstone acoustic lantern', channel: 'design' });
  const global = suggestDemoConnections(state, 'studio'); assert.ok(!global.some(row => row.documentId === 'focus' || row.relatedDocumentId === 'focus'));
  const focused = suggestDemoConnections(state, 'studio', undefined, { documentId: 'focus' });
  assert.ok(focused.some(row => row.kind === 'document_link' && [row.documentId, row.relatedDocumentId].includes('related-focus')));
  assert.ok(focused.every(row => row.documentId === 'focus' || row.relatedDocumentId === 'focus'));
  assert.equal(suggestDemoConnections(state, 'studio', undefined, { documentId: 'foreign-focus' }).length, 0);
  assert.equal(suggestDemoConnections(state, 'studio', undefined, { documentId: '' }).length, 0);
});
