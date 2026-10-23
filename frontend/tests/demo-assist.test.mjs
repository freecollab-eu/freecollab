import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
const cache = new Map(); let requests = 0;
function load(name) {
  if (cache.has(name)) return cache.get(name);
  const exports = {}; cache.set(name, exports);
  const source = readFileSync(new URL('../src/lib/' + name + '.ts', import.meta.url), 'utf8');
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText, {
    exports, URLSearchParams, require: dependency => { if (dependency === './demo-ai') return load('demo-ai'); throw new Error('Unexpected runtime dependency'); },
    fetch: () => { requests++; throw new Error('Contextual examples must not make requests'); },
  });
  return exports;
}
const { createDemoState, demoReducer } = load('demo-showroom');
const { buildDemoAssistContext, buildDemoAssistSources, createDemoAssistPreview, demoAssistPreviewIsCurrent, demoAssistCanApply, demoAssistLimits } = load('demo-assist');
const plain = value => JSON.parse(JSON.stringify(value));
const defaults = { channel: 'studio', draft: '', provider: 'grok', model: 'grok-4.20-0309-non-reasoning', locale: 'en' };
const context = (state, scope, patch = {}, t) => buildDemoAssistContext(state, { ...defaults, scope, ...patch }, t);
function rehash(recipe) { return { ...recipe, fingerprint: JSON.stringify([recipe.contextFingerprint, recipe.id, recipe.target, recipe.before, recipe.after, recipe.sourceKeys]) }; }

test('each scope exposes only its actual local records and never treats a task or draft as a sent message', () => {
  let state = demoReducer(createDemoState(), { type: 'task_from_message', channel: 'product', messageId: 'product-1', text: 'A product-only task' });
  const foreignTask = state.tasks.at(-1), draft = 'My unsent ceramic draft';
  const composer = context(state, { kind: 'composer' }, { draft });
  assert.equal(composer.sources[0].kind, 'draft'); assert.equal(composer.sources[0].text, draft);
  assert.equal(composer.sources[0].key, 'draft:studio');
  const message = context(state, { kind: 'message', id: 'studio-1' }, { draft });
  assert.deepEqual(plain(message.sources.map(source => source.key)), ['message:studio-1']);
  assert.equal(message.sources.some(source => source.text.includes('My unsent')), false);
  const task = context(state, { kind: 'task', id: 'task-2' });
  assert.equal(task.sources[0].kind, 'task'); assert.equal(task.sources[0].key, 'task:task-2');
  for (const scope of [{ kind: 'message', id: 'product-1' }, { kind: 'document', id: 'product-plan' }, { kind: 'stack', id: 'product-reference' }, { kind: 'task', id: foreignTask.id }, { kind: 'message', id: 'missing' }, { kind: 'constructor' }]) assert.equal(context(state, scope), null);
  assert.equal(context(state, { kind: 'channel' }, { channel: 'constructor' }), null);
});

test('allowed actions follow the location and cannot prepare an unrelated write', () => {
  const state = createDemoState();
  const cases = [
    [{ kind: 'message', id: 'studio-1' }, ['reply', 'task', 'summary']],
    [{ kind: 'composer' }, ['tidy', 'shorten']],
    [{ kind: 'document', id: 'brief' }, ['summary', 'outline', 'link_context']],
    [{ kind: 'stack', id: 'studio-reference' }, ['summary', 'organize']],
    [{ kind: 'task', id: 'task-2' }, ['next_step', 'details']],
    [{ kind: 'channel' }, ['recap', 'next_steps']],
  ];
  for (const [scope, expected] of cases) {
    const current = context(state, scope);
    assert.deepEqual(plain(current.actions.map(action => action.id)), expected);
    assert.equal(createDemoAssistPreview(current, 'unscoped-operation'), null);
    for (const intent of expected) assert.equal(createDemoAssistPreview(current, intent).mode, 'local-preview');
  }
  assert.equal(createDemoAssistPreview(context(state, { kind: 'document', id: 'brief' }), 'task'), null);
});

test('tidying proposes spacing changes with a precise before/after and never changes the draft itself', () => {
  const state = createDemoState(), before = JSON.stringify(state), draft = '  Ceramic   nozzle\r\n\r\n\r\n review\t next.  ';
  const current = context(state, { kind: 'composer' }, { draft }), preview = createDemoAssistPreview(current, 'tidy'), recipe = preview.recipes[0];
  assert.equal(recipe.target.kind, 'draft'); assert.equal(recipe.before, draft);
  assert.equal(recipe.after, 'Ceramic nozzle\n\nreview next.');
  assert.deepEqual(plain(recipe.sourceKeys), ['draft:studio']);
  assert.equal(demoAssistCanApply(preview, recipe, current), true);
  assert.equal(current.draft, draft); assert.equal(JSON.stringify(state), before);
  assert.equal(createDemoAssistPreview(context(state, { kind: 'composer' }, { draft: 'Already tidy.' }), 'tidy').recipes.length, 0);
});

test('shortening uses an actual excerpt rather than adding facts, dates or owners', () => {
  const state = createDemoState(), draft = 'Review the ceramic nozzle. Then discuss the cooling prototype together.';
  const current = context(state, { kind: 'composer' }, { draft }), preview = createDemoAssistPreview(current, 'shorten'), recipe = preview.recipes[0];
  assert.equal(recipe.after, 'Review the ceramic nozzle.'); assert.ok(draft.startsWith(recipe.after));
  assert.equal(demoAssistCanApply(preview, recipe, current), true);
  const long = context(state, { kind: 'composer' }, { draft: 'A ceramic nozzle cooling prototype '.repeat(30) });
  const shorter = createDemoAssistPreview(long, 'shorten').recipes[0];
  assert.ok(shorter.after.length < shorter.before.length); assert.ok(shorter.after.endsWith('…'));
  assert.equal(createDemoAssistPreview(context(state, { kind: 'composer' }, { draft: '   ' }), 'shorten').recipes.length, 0);
});

test('a message reply appends to the exact existing draft and becomes stale as soon as that draft changes', () => {
  const state = createDemoState(), draft = 'My own unsent introduction.';
  const current = context(state, { kind: 'message', id: 'studio-2' }, { draft }), preview = createDemoAssistPreview(current, 'reply'), recipe = preview.recipes[0];
  assert.equal(recipe.before, draft); assert.ok(recipe.after.startsWith(draft + ' '));
  assert.ok(recipe.after.includes(state.messages.studio[1].text));
  assert.equal(demoAssistCanApply(preview, recipe, current), true);
  const editedDraft = context(state, { kind: 'message', id: 'studio-2' }, { draft: draft + ' Keep this newer sentence.' });
  assert.equal(demoAssistPreviewIsCurrent(preview, editedDraft), false); assert.equal(demoAssistCanApply(preview, recipe, editedDraft), false);
  const full = context(state, { kind: 'message', id: 'studio-2' }, { draft: 'x'.repeat(2000) });
  assert.equal(createDemoAssistPreview(full, 'reply').recipes.length, 0);
});

test('message-to-task proposals cite only that selected sent message and preserve the task review boundary', () => {
  const state = demoReducer(createDemoState(), { type: 'message', channel: 'studio', text: 'x'.repeat(179) + '😀 more', time: '12:00' });
  const message = state.messages.studio.at(-1), current = context(state, { kind: 'message', id: message.id }), preview = createDemoAssistPreview(current, 'task'), recipe = preview.recipes[0];
  assert.equal(recipe.target.kind, 'new_task'); assert.equal(recipe.target.messageId, message.id); assert.equal(recipe.after, 'x'.repeat(179));
  assert.equal(recipe.before, ''); assert.deepEqual(plain(recipe.sourceKeys), ['message:' + message.id]);
  assert.equal(demoAssistCanApply(preview, recipe, current), true);
  assert.equal(state.tasks.length, 4); assert.equal('owner' in recipe.target, false); assert.equal('dueDate' in recipe.target, false);
  assert.equal(createDemoAssistPreview(current, 'task', undefined, ['message:studio-1']), null);
});

test('document outlines append actual headings and cannot overwrite, repeat or exceed the document bound', () => {
  let state = demoReducer(createDemoState(), { type: 'document', id: 'brief', text: '## Ceramic nozzle\n\nKeep the original description.\n\n### Cooling review\n\nKeep this question open.' });
  const current = context(state, { kind: 'document', id: 'brief' }), preview = createDemoAssistPreview(current, 'outline'), recipe = preview.recipes[0];
  assert.equal(recipe.target.kind, 'document'); assert.equal(recipe.target.id, 'brief'); assert.equal(recipe.before, state.documents[0].edited);
  assert.equal(recipe.after, recipe.before + '\n\n## Outline from this document\n\n- Ceramic nozzle\n- Cooling review');
  assert.equal(demoAssistCanApply(preview, recipe, current), true); assert.equal(state.documents[0].edited, recipe.before);
  state = demoReducer(state, { type: 'document', id: 'brief', text: recipe.after });
  assert.equal(createDemoAssistPreview(context(state, { kind: 'document', id: 'brief' }), 'outline').recipes.length, 0);
  state = demoReducer(state, { type: 'document', id: 'brief', text: 'Only one line.' });
  assert.equal(createDemoAssistPreview(context(state, { kind: 'document', id: 'brief' }), 'outline').recipes.length, 0);
  state = demoReducer(state, { type: 'document', id: 'brief', text: '## One\n## Two\n' + 'z'.repeat(19986) });
  assert.equal(state.documents[0].edited.length, 20000);
  assert.equal(createDemoAssistPreview(context(state, { kind: 'document', id: 'brief' }), 'outline').recipes.length, 0);
});

test('document context uses only saved links and stack references and stack membership changes invalidate it', () => {
  let state = demoReducer(createDemoState(), { type: 'document_link', channel: 'studio', documentId: 'brief', relatedDocumentId: 'materials' });
  state = demoReducer(state, { type: 'stack_reference', channel: 'studio', stackId: 'studio-reference', messageId: 'studio-2' });
  const document = context(state, { kind: 'document', id: 'brief' });
  assert.deepEqual(plain(document.sources.map(source => source.key)), ['document:brief', 'message:studio-2', 'document:materials']);
  assert.equal(document.sources.some(source => source.key === 'message:studio-1'), false);
  const stack = context(state, { kind: 'stack', id: 'studio-reference' }), preview = createDemoAssistPreview(stack, 'summary');
  state = demoReducer(state, { type: 'document_move', channel: 'studio', id: 'notes', stackId: 'studio-sketches' });
  assert.equal(demoAssistPreviewIsCurrent(preview, context(state, { kind: 'stack', id: 'studio-reference' })), false);
});

test('task clarification uses cited source words and binds completion, owner, sample and provenance metadata', () => {
  let state = demoReducer(createDemoState(), { type: 'task_from_message', channel: 'studio', messageId: 'studio-3', text: 'Review this', owner: 'Maja' });
  const task = state.tasks.at(-1), current = context(state, { kind: 'task', id: task.id }), preview = createDemoAssistPreview(current, 'details'), recipe = preview.recipes[0];
  assert.equal(recipe.before, task.text); assert.equal(recipe.after, state.messages.studio[2].text);
  assert.equal(recipe.target.kind, 'task'); assert.deepEqual(plain(recipe.sourceKeys), ['task:' + task.id, 'message:studio-3']);
  assert.equal(demoAssistCanApply(preview, recipe, current), true);
  for (const patch of [{ done: true }, { owner: 'Noor' }, { sample: true }, { source: { channel: 'studio', messageId: 'studio-2' } }]) {
    const changed = { ...state, tasks: state.tasks.map(row => row.id === task.id ? { ...row, ...patch } : row) };
    assert.equal(demoAssistPreviewIsCurrent(preview, context(changed, { kind: 'task', id: task.id })), false);
  }
  const t = source => source === 'Review the courtyard materials' ? 'Granska  materialet' : source;
  const sample = context(createDemoState(), { kind: 'task', id: 'task-2' }, { locale: 'sv' }, t), samplePreview = createDemoAssistPreview(sample, 'details', t);
  assert.equal(samplePreview.recipes[0].before, 'Granska  materialet'); assert.equal(samplePreview.recipes[0].after, 'Granska materialet');
});

test('source budget keeps whole exact snapshots, reports omissions and never includes the composer draft in a channel recap', () => {
  let state = createDemoState();
  state = demoReducer(state, { type: 'document', id: 'brief', text: 'b'.repeat(20000) });
  state = demoReducer(state, { type: 'document', id: 'notes', text: 'n'.repeat(20000) });
  const current = context(state, { kind: 'stack', id: 'studio-reference' }, { draft: 'UNSENT PRIVATE DRAFT' });
  assert.equal(current.sources.length, 1); assert.equal(current.omittedCount, 1); assert.equal(current.sources[0].text.length, 20000);
  assert.ok(current.sources.reduce((sum, source) => sum + source.text.length, 0) <= demoAssistLimits.characters);
  const channel = context(state, { kind: 'channel' }, { draft: 'UNSENT PRIVATE DRAFT' }), preview = createDemoAssistPreview(channel, 'recap');
  assert.ok(channel.sources.length <= 8); assert.ok(channel.sources.every(source => source.channel === 'studio'));
  assert.equal(preview.sources.some(source => source.kind === 'draft'), false);
  assert.equal(JSON.stringify(preview.items).includes('UNSENT PRIVATE DRAFT'), false);
});

test('provider, model, locale, full source text and scope changes invalidate a prepared write', () => {
  const state = demoReducer(createDemoState(), { type: 'document', id: 'brief', text: '## One\n## Two\n' + 'x'.repeat(300) + ' old ending' });
  const current = context(state, { kind: 'document', id: 'brief' }), preview = createDemoAssistPreview(current, 'outline'), recipe = preview.recipes[0];
  for (const patch of [{ provider: 'openai' }, { model: 'another-model' }, { locale: 'sv' }, { draft: 'A newer draft elsewhere' }]) assert.equal(demoAssistCanApply(preview, recipe, context(state, { kind: 'document', id: 'brief' }, patch)), false);
  const changed = demoReducer(state, { type: 'document', id: 'brief', text: state.documents[0].edited.replace('old ending', 'new ending') });
  assert.equal(demoAssistPreviewIsCurrent(preview, context(changed, { kind: 'document', id: 'brief' })), false);
  assert.equal(demoAssistCanApply(preview, recipe, context(state, { kind: 'document', id: 'notes' })), false);
  assert.equal(context(state, { kind: 'composer' }, { model: 'invalid model' }), null);
});

test('modified recipes cannot smuggle a different target or invented after text into an explicit apply', () => {
  let state = demoReducer(createDemoState(), { type: 'task_from_message', channel: 'studio', messageId: 'studio-3', text: 'Review source' });
  const cases = [
    [context(state, { kind: 'composer' }, { draft: '  My   draft ' }), 'tidy'],
    [context(state, { kind: 'message', id: 'studio-1' }), 'reply'],
    [context(state, { kind: 'document', id: 'brief' }), 'outline'],
    [context(state, { kind: 'task', id: state.tasks.at(-1).id }), 'details'],
    [context(state, { kind: 'message', id: 'studio-1' }), 'task'],
  ];
  for (const [current, intent] of cases) {
    const preview = createDemoAssistPreview(current, intent), original = preview.recipes[0];
    assert.ok(original); assert.equal(demoAssistCanApply(preview, original, current), true);
    const forged = rehash({ ...original, after: original.after + ' Invented commitment.' });
    const alteredPreview = { ...preview, recipes: [forged] };
    assert.equal(demoAssistCanApply(alteredPreview, forged, current), false, intent);
    assert.equal(demoAssistCanApply(preview, { ...original, before: 'Different current content' }, current), false);
  }
});

test('previews stay detached and local across every scope and action', () => {
  const state = createDemoState(), before = JSON.stringify(state);
  for (const scope of [{ kind: 'message', id: 'studio-1' }, { kind: 'composer' }, { kind: 'document', id: 'brief' }, { kind: 'stack', id: 'studio-reference' }, { kind: 'task', id: 'task-2' }, { kind: 'channel' }]) {
    const current = context(state, scope, { draft: '  A   draft to tidy. And another sentence. ' });
    for (const action of current.actions) {
      const preview = createDemoAssistPreview(current, action.id);
      assert.equal(demoAssistPreviewIsCurrent(preview, current), true);
      assert.ok(preview.sources.every(source => current.sources.some(actual => actual.key === source.key)));
      if (preview.sources.length) assert.notEqual(preview.sources[0], current.sources[0]);
    }
  }
  assert.equal(requests, 0); assert.equal(JSON.stringify(state), before);
});

test('one shared base-source cache preserves every scope fingerprint without extracting the full channel again', () => {
  const ai = load('demo-ai'), original = ai.buildDemoAiSources, state = createDemoState();
  const t = (source, values = {}) => (source === 'Local preview' ? 'Lokal förhandsvisning' : source).replace(/\{([A-Za-z][A-Za-z0-9_]*)\}/g, (whole, key) => Object.hasOwn(values, key) ? String(values[key]) : whole);
  const base = Object.freeze(original(state, 'studio', t).map(source => Object.freeze(source)));
  const scopes = [{ kind: 'message', id: 'studio-1' }, { kind: 'composer' }, { kind: 'document', id: 'brief' }, { kind: 'stack', id: 'studio-reference' }, { kind: 'task', id: 'task-2' }, { kind: 'channel' }];
  const inputs = scopes.map(scope => ({ ...defaults, scope, locale: 'sv', draft: '  My   draft ' }));
  const expected = inputs.map(input => buildDemoAssistContext(state, input, t));
  let extractions = 0;
  ai.buildDemoAiSources = (...arguments_) => { extractions++; return original(...arguments_); };
  try {
    for (let index = 0; index < inputs.length; index++) {
      const cached = buildDemoAssistContext(state, inputs[index], t, base);
      assert.equal(cached.fingerprint, expected[index].fingerprint);
      assert.deepEqual(plain(cached.sources), plain(expected[index].sources));
      assert.deepEqual(plain(buildDemoAssistSources(state, inputs[index], t, base)), plain(expected[index].sources));
      const preview = createDemoAssistPreview(cached, cached.actions[0].id, t);
      assert.equal(demoAssistPreviewIsCurrent(preview, expected[index]), true);
    }
    assert.equal(extractions, 0);
    buildDemoAssistContext(state, inputs[0], t);
    assert.equal(extractions, 1);
  } finally { ai.buildDemoAiSources = original; }
  assert.equal(buildDemoAssistContext(state, { ...defaults, scope: { kind: 'message', id: 'studio-1' } }, undefined, original(state, 'product')), null);
});
