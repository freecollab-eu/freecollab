import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/lib/message-references.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const exports = {};
vm.runInNewContext(compiled, { exports });
const { getReferenceQuery, insertReference, reconcileReferences, validateMessageReferences, normalizeMessageReferences, referenceOptionId } = exports;
const plain = value => JSON.parse(JSON.stringify(value));
const ref = (id, start, end, kind = 'person') => ({ kind, id, start, end });

test('@ suggestions use the actual caret and support names and groups with spaces', () => {
  assert.deepEqual(plain(getReferenceQuery('A note for @Design team tomorrow', 23)), { query: 'Design team', start: 11, end: 23 });
  assert.deepEqual(plain(getReferenceQuery('Please @Ad review this', 10)), { query: 'Ad', start: 7, end: 10 });
  assert.deepEqual(plain(getReferenceQuery('@', 1)), { query: '', start: 0, end: 1 });
  assert.deepEqual(plain(getReferenceQuery('(@Ada', 5)), { query: 'Ada', start: 1, end: 5 });
});

test('email, URL credentials, escaped @ and completed punctuation do not become choices', () => {
  for (const text of ['ada@example.com', 'https://example.test/@ada', 'https://ada@example.test', '\\@ada', 'word@ada', '@ada, done', '@ada\nnext', '@ada: next', '@ada@bob'])
    assert.equal(getReferenceQuery(text, text.length), null, text);
  assert.equal(getReferenceQuery('@Ada', 0), null);
  assert.equal(getReferenceQuery('@Ada', -1), null);
  assert.equal(getReferenceQuery('@' + 'a'.repeat(81), 82), null);
  assert.equal(getReferenceQuery('@Ada', 5), null);
});

test('an explicit candidate inserts readable text while binding the selected ID', () => {
  const before = 'Ask @Ad about this';
  const result = insertReference(before, [], getReferenceQuery(before, 7), { kind: 'person', id: 'ada-actual-id', label: 'Ada Lovelace' });
  assert.deepEqual(plain(result), { text: 'Ask @Ada Lovelace  about this', references: [ref('ada-actual-id', 4, 17)], caret: 18 });
  assert.equal(result.text.slice(result.references[0].start, result.references[0].end), '@Ada Lovelace');
});

test('same display names remain distinct choices and IDs are never inferred from text', () => {
  const first = insertReference('@A', [], getReferenceQuery('@A', 2), { kind: 'person', id: 'first-person', label: 'Alex' });
  const secondText = first.text + '@A';
  const second = insertReference(secondText, first.references, getReferenceQuery(secondText, secondText.length), { kind: 'person', id: 'second-person', label: 'Alex' });
  assert.deepEqual(Array.from(second.references, row => row.id), ['first-person', 'second-person']);
  assert.deepEqual(plain(normalizeMessageReferences('@Alex', undefined)), []);
});

test('resource kinds and their stable IDs survive labels that are also person names', () => {
  const text = '@Budget @Budget @Budget';
  const references = [ref('person-1', 0, 7), ref('document-1', 8, 15, 'document'), ref('group-1', 16, 23, 'group')];
  assert.deepEqual(plain(validateMessageReferences(text, references)), references);
  assert.notEqual(referenceOptionId('menu', references[0]), referenceOptionId('menu', references[1]));
});

test('untouched references shift with edits before them and remain stable after them', () => {
  const before = 'Hi @Ada and @Grace.';
  const references = [ref('ada', 3, 7), ref('grace', 12, 18)];
  const longer = 'Well, ' + before;
  assert.deepEqual(plain(reconcileReferences(before, longer, references)), [ref('ada', 9, 13), ref('grace', 18, 24)]);
  assert.deepEqual(plain(reconcileReferences(before, before + ' A note.', references)), references);
  assert.deepEqual(plain(reconcileReferences(before, 'Hi @Ada or @Grace.', references)), [ref('ada', 3, 7), ref('grace', 11, 17)]);
});

test('editing or extending the chosen label removes its notification binding', () => {
  const references = [ref('ada', 3, 7), ref('grace', 12, 18)];
  const before = 'Hi @Ada and @Grace.';
  assert.deepEqual(plain(reconcileReferences(before, 'Hi @Ana and @Grace.', references)), [ref('grace', 12, 18)]);
  assert.deepEqual(plain(reconcileReferences('@Ada', '@AdaX', [ref('ada', 0, 4)])), []);
  assert.deepEqual(plain(reconcileReferences('@Ada', 'x@Ada', [ref('ada', 0, 4)])), []);
  assert.deepEqual(plain(reconcileReferences('@Ada', '@Ada!', [ref('ada', 0, 4)])), [ref('ada', 0, 4)]);
});

test('ambiguous repeated-name deletion cannot silently notify the wrong person', () => {
  const before = '@Alex @Alex';
  const references = [ref('first', 0, 5), ref('second', 6, 11)];
  assert.deepEqual(plain(reconcileReferences(before, '@Alex', references)), []);
  assert.deepEqual(plain(reconcileReferences(before, '@Alex', references, { start: 0, end: 6 })), [ref('second', 0, 5)]);
  assert.deepEqual(plain(reconcileReferences(before, '@Alex', references, { start: 5, end: 11 })), [ref('first', 0, 5)]);
});

test('UTF-16 offsets retain emoji before and inside references without splitting pairs', () => {
  const text = '🧭 @Ada';
  assert.deepEqual(plain(validateMessageReferences(text, [ref('ada', 3, 7)])), [ref('ada', 3, 7)]);
  assert.deepEqual(plain(reconcileReferences(text, 'New ' + text, [ref('ada', 3, 7)])), [ref('ada', 7, 11)]);
  assert.deepEqual(plain(validateMessageReferences('@🧭 ', [ref('compass', 0, 3, 'document')])), [ref('compass', 0, 3, 'document')]);
  assert.throws(() => validateMessageReferences('@🧭 ', [ref('split-pair', 0, 2)]));
  assert.equal(getReferenceQuery('@🧭', 2), null);
});

test('strict writing rejects overlaps, forged ranges, malformed IDs and unsupported kinds', () => {
  for (const references of [[ref('x', -1, 4)], [ref('x', 0, 99)], [ref('x', 0, 4), ref('y', 0, 4)], [ref('x', 0.5, 4)], [ref('x', 0, 4, 'html')], [ref('bad id', 0, 4)], [ref('', 0, 4)]])
    assert.throws(() => validateMessageReferences('@Ada', references));
  assert.throws(() => validateMessageReferences('Ada', [ref('x', 0, 3)]));
  assert.throws(() => validateMessageReferences('@A\nda', [ref('x', 0, 5)]));
  assert.throws(() => validateMessageReferences('@Ada', Array.from({ length: 51 }, () => ref('x', 0, 4))));
  assert.deepEqual(plain(normalizeMessageReferences('@Ada', [{ kind: 'person', id: 'x', start: 0, end: 100 }])), []);
});

test('stale query replacement rejects a changed draft and never reuses the old identity', () => {
  const query = getReferenceQuery('@Ad', 3);
  assert.throws(() => insertReference('@Grace', [], query, { kind: 'person', id: 'ada', label: 'Ada' }));
  assert.throws(() => insertReference('@Ad', [], query, { kind: 'person', id: 'ada', label: 'Ada\nGrace' }));
});

test('normalization and insertion never mutate the original drafts or bindings', () => {
  const references = [ref('ada', 0, 4)];
  const serialized = JSON.stringify(references);
  const result = reconcileReferences('@Ada says hello', 'Hi @Ada says hello', references);
  result[0].id = 'caller-mutated';
  assert.equal(JSON.stringify(references), serialized);
  assert.equal(validateMessageReferences('@Ada', references)[0].id, 'ada');
});
