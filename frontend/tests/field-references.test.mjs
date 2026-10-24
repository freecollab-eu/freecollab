import test from 'node:test';
import assert from 'node:assert/strict';
import { fieldReferences as f } from './load-field-references.mjs';
const plain = value => JSON.parse(JSON.stringify(value));
const ref = (id, start = 0, end = 4, kind = 'person') => ({ kind, id, start, end });

test('field references preserve all actual resource kinds and UTF16 positions with no inferred identities', () => {
  for (const kind of ['person', 'group', 'document', 'task', 'file', 'channel']) {
    assert.deepEqual(plain(f.validateFieldReferences({ title: '🧭 @Ada ' }, { title: [ref(kind, 3, 7, kind)] })), { title: [ref(kind, 3, 7, kind)] });
  }
  assert.equal(f.validateFieldReferences({ title: '@Ada' }, undefined), undefined);
  assert.equal(f.validateFieldReferences({ title: '@Ada' }, { title: [] }), undefined);
  assert.throws(() => f.validateFieldReferences({ title: '@Ada' }, { description: [ref('forged')] }));
  assert.throws(() => f.validateFieldReferences({ title: '@Ada' }, { title: [ref('forged', 0, 99)] }));
});

test('the 50 reference budget applies across all writing fields together', () => {
  const text = Array(26).fill('@Ada').join(' '), references = Array.from({ length: 26 }, (_, i) => ref('person-' + i, i * 5, i * 5 + 4));
  assert.equal(f.collectFieldReferences(f.validateFieldReferences({ title: text, body: text }, { title: references.slice(0, 25), body: references.slice(0, 25) })).length, 50);
  assert.throws(() => f.validateFieldReferences({ title: text, body: text }, { title: references, body: references.slice(0, 25) }));
});

test('edits move explicit bindings and removed checklist/poll fields cannot retain hidden bindings', () => {
  const before = { title: '@Ada', 'checklist:a': '@Ada' }, after = { title: 'Ask @Ada' };
  assert.deepEqual(plain(f.reconcileFieldReferences(before, after, { title: [ref('person')], 'checklist:a': [ref('old-document', 0, 4, 'document')] })), { title: [ref('person', 4, 8)] });
  assert.equal(f.reconcileFieldReferences({ title: '@Ada' }, { title: '@Ana' }, { title: [ref('person')] }), undefined);
  assert.deepEqual(plain(f.feedReferenceTexts({ title: 'Poll', body: 'Body', poll: { question: 'Who?', options: [{ id: 'actual', label: '@Ada' }] } })), { title: 'Poll', body: 'Body', 'poll.question': 'Who?', 'poll.option:actual': '@Ada' });
});

test('unchanged historic field bindings survive status edits, while any text or ID change needs current resolution', () => {
  const refs = { title: [ref('historic')] };
  assert.deepEqual(plain(f.changedFieldReferences({ title: '@Ada' }, refs, { title: '@Ada' }, refs)), []);
  assert.deepEqual(plain(f.changedFieldReferences({ title: '@Ada!' }, refs, { title: '@Ada' }, refs)), refs.title);
  assert.deepEqual(plain(f.changedFieldReferences({ title: '@Ada' }, { title: [ref('different')] }, { title: '@Ada' }, refs)), [ref('different')]);
});
