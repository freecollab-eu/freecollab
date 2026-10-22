import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { webcrypto } from 'node:crypto';
import vm from 'node:vm';
import ts from 'typescript';
import * as Y from 'yjs';
import * as cryptoApi from 'matrix-js-sdk/lib/crypto-api/index.js';

function compile(path, dependencies = {}) {
  const output = ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const exports = {};
  vm.runInNewContext(output, { exports, Date, Intl, TextEncoder, TextDecoder, Uint8Array, btoa, atob, crypto: webcrypto, require: name => { assert.ok(name in dependencies, name); return dependencies[name]; } });
  return exports;
}
const calendar = compile('../src/lib/calendar.ts');
const model = compile('../src/lib/federated-work-model.ts', { './calendar': calendar });
const document = compile('../src/lib/federated-work-document.ts', { yjs: Y });
const trust = compile('../src/lib/federated-work-trust.ts', { 'matrix-js-sdk/lib/crypto-api': cryptoApi });
const connection = 'a'.repeat(32), sender = '@fc_alice:matrix.a.example.com', remote = '@fc_bob:matrix.b.example.com';
const context = { connection_id: connection };
const task = () => ({ title: 'Private plan', description: 'Only participant devices read this', status: 'todo', priority: 'normal', assignee_matrix_id: remote, due_date: '2026-10-23', checklist: [{ id: 'one', title: 'Review', done: false }] });

test('manual device fingerprints never override forwarded-key, identity-change or sender-authenticity warnings', () => {
  const { EventShieldColour: C, EventShieldReason: R } = cryptoApi;
  assert.equal(trust.acceptableWorkEncryptionOrigin({ shieldColour: C.NONE, shieldReason: null }), true);
  for (const reason of [R.UNSIGNED_DEVICE, R.UNVERIFIED_IDENTITY]) assert.equal(trust.acceptableWorkEncryptionOrigin({ shieldColour: C.RED, shieldReason: reason }), true);
  for (const reason of [R.AUTHENTICITY_NOT_GUARANTEED, R.VERIFICATION_VIOLATION, R.MISMATCHED_SENDER, R.MISMATCHED_SENDER_KEY, R.UNKNOWN_DEVICE, R.UNKNOWN, R.SENT_IN_CLEAR]) for (const colour of [C.GREY, C.RED]) assert.equal(trust.acceptableWorkEncryptionOrigin({ shieldColour: colour, shieldReason: reason }), false);
  assert.equal(trust.acceptableWorkEncryptionOrigin({ shieldColour: C.RED, shieldReason: null }), false);
  assert.equal(trust.acceptableWorkEncryptionOrigin(null), false);
});
async function event(id = '$root', parents = [], fields = task(), author = sender, resource) {
  resource ||= connection + ':' + await model.authorNamespace(sender) + ':12345678-1234-4123-8123-123456789abc';
  return { event_id: id, sender: author, timestamp: 1, type: model.WORK_REVISION, content: { protocol: model.WORK_PROTOCOL, connection_id: connection, id: resource, kind: 'task', parents, archived: false, fields } };
}

test('shared task fields require globally named participants and reject native/provider leakage', () => {
  assert.equal(model.validateSharedFields('task', task()).assignee_matrix_id, remote);
  for (const invalid of [{ ...task(), assignee_matrix_id: 'bob' }, { ...task(), provider_id: 'private' }, { ...task(), due_date: '2026-02-30' }, { ...task(), checklist: [task().checklist[0], task().checklist[0]] }]) assert.throws(() => model.validateSharedFields('task', invalid));
});

test('shared calendar reuses DST-aware finite recurrence and exceptions while excluding external identity/email', () => {
  const fields = { title: 'Shared design review', description: '', location: '', starts_at: '2026-10-23T08:00:00Z', ends_at: '2026-10-23T09:00:00Z', all_day: false, timezone: 'Europe/Stockholm', reminder_minutes: null, external_uid: null, recurrence: { frequency: 'weekly', interval: 1, count: 3 }, exceptions: [{ original_start: '2026-10-30T09:00:00.000Z', cancelled: true }] };
  const validated = model.validateSharedFields('calendar', fields);
  const occurrences = calendar.expandCalendarOccurrences(validated, '2026-10-01', '2026-12-01');
  assert.equal(occurrences.length, 2);
  assert.equal(occurrences[1].starts_at, '2026-11-06T09:00:00.000Z');
  assert.throws(() => model.validateSharedFields('calendar', { ...fields, external_uid: 'provider@example.com' }));
  assert.throws(() => model.validateSharedFields('calendar', { ...fields, attendees: ['private@example.com'] }));
});

test('resource creation is pinned to the actual encrypted sender namespace and connection', async () => {
  const good = await event(); const revision = await model.parseRevision(good, context);
  assert.equal(revision.sender, sender);
  await assert.rejects(model.parseRevision({ ...good, sender: remote }, context), /namespace/);
  await assert.rejects(model.parseRevision({ ...good, content: { ...good.content, connection_id: 'b'.repeat(32) } }, context));
  await assert.rejects(model.parseRevision({ ...good, content: { ...good.content, unreviewed_native_id: 4 } }, context));
});

test('concurrent edits stay visible as heads until an explicit revision names both parents', async () => {
  const root = await model.parseRevision(await event(), context);
  const left = await model.parseRevision(await event('$left', ['$root'], { ...task(), status: 'doing' }), context);
  const right = await model.parseRevision(await event('$right', ['$root'], { ...task(), title: 'Peer revision' }, remote), context);
  const competing = model.reduceWorkRevisions([right, root, left])[0];
  assert.equal(competing.conflicts, true); assert.equal(competing.heads.length, 2);
  const merged = await model.parseRevision(await event('$merge', ['$left', '$right'], { ...task(), title: 'Peer revision', status: 'doing' }), context);
  const final = model.reduceWorkRevisions([right, merged, left, root])[0];
  assert.equal(final.conflicts, false); assert.equal(final.selected.event_id, '$merge'); assert.equal(final.history.length, 4);
  assert.equal(JSON.stringify(final), JSON.stringify(model.reduceWorkRevisions([root, left, merged, right])[0]));
});

test('missing and cyclic ancestry cannot silently become the current shared revision', async () => {
  const root = await model.parseRevision(await event(), context);
  const missing = await model.parseRevision(await event('$missing', ['$unknown']), context);
  const cycleA = await model.parseRevision(await event('$a', ['$b']), context), cycleB = await model.parseRevision(await event('$b', ['$a']), context);
  const reduced = model.reduceWorkRevisions([cycleB, missing, root, cycleA])[0];
  assert.equal(reduced.selected.event_id, '$root'); assert.equal(reduced.invalid.length, 3);
  await assert.rejects(model.parseRevision(await event('$duplicate-parent', ['$root', '$root']), context));
});

test('a rootless verified resource stays incomplete and read-only while healthy resources remain available', async () => {
  const healthy = await model.parseRevision(await event(), context);
  const otherId = connection + ':' + await model.authorNamespace(remote) + ':87654321-4321-4321-8321-abcdefabcdef';
  const rootless = await model.parseRevision(await event('$rootless', ['$unknown-parent'], task(), remote, otherId), context);
  const records = model.reduceWorkRevisions([rootless, healthy]);
  const good = records.find(row => row.id === healthy.id), incomplete = records.find(row => row.id === otherId);
  assert.equal(good.invalid.length, 0); assert.equal(good.root_event_id, '$root');
  assert.equal(incomplete.root_event_id, ''); assert.deepEqual([...incomplete.invalid], ['$rootless']); assert.equal(incomplete.heads.length, 0);
});

test('authenticated broken ancestry taints only its named known resource and cannot hide a healthy concurrent resource', async () => {
  const root = await model.parseRevision(await event(), context);
  const otherId = connection + ':' + await model.authorNamespace(remote) + ':87654321-4321-4321-8321-abcdefabcdef';
  const other = await model.parseRevision(await event('$peer-root', [], task(), remote, otherId), context);
  const missing = await model.parseRevision(await event('$missing', ['$unknown-concurrent-head']), context);
  const records = model.reduceWorkRevisions([root, other, missing], new Map([[root.id, ['$malformed-known-revision']]]));
  assert.equal(records.find(row => row.id === root.id).invalid.length, 2);
  assert.equal(records.find(row => row.id === otherId).invalid.length, 0);
});

function initial() { const doc = new Y.Doc(); doc.getText('title').insert(0, 'Shared document'); doc.getText('body'); const encoded = document.encodeWorkUpdate(Y.encodeStateAsUpdate(doc)); doc.destroy(); return encoded; }
function edit(initialState, title, body) {
  const doc = new Y.Doc();
  try { Y.applyUpdate(doc, document.decodeWorkUpdate(initialState)); const vector = Y.encodeStateVector(doc); document.replaceWorkText(doc, 'title', title); document.replaceWorkText(doc, 'body', body); document.validateWorkDocument(doc); return document.encodeWorkUpdate(Y.encodeStateAsUpdate(doc, vector)); }
  finally { doc.destroy(); }
}

test('actual Yjs changes on independent client replicas merge title and both concurrent text insertions', () => {
  const start = initial(), left = edit(start, 'Shared document', 'From organization A. '), right = edit(start, 'New collaborative title', 'From organization B. ');
  const first = document.reconstructWorkDocument(start, [{ id: '$a', update_base64: left }, { id: '$b', update_base64: right }]);
  const second = document.reconstructWorkDocument(start, [{ id: '$b', update_base64: right }, { id: '$a', update_base64: left }]);
  assert.equal(first.title, 'New collaborative title'); assert.match(first.body, /From organization A/); assert.match(first.body, /From organization B/);
  assert.equal(first.body, second.body); assert.equal(first.update_base64, second.update_base64);
});

test('Yjs edit deltas preserve existing identities and duplicate delivery is idempotent', () => {
  const start = initial(), one = edit(start, 'Shared document', 'Draft');
  const state = document.reconstructWorkDocument(start, [{ id: '$a', update_base64: one }]);
  const two = edit(state.update_base64, 'Shared document', 'Draft reviewed');
  const result = document.reconstructWorkDocument(start, [{ id: '$a', update_base64: one }, { id: '$b', update_base64: two }, { id: '$a-retry', update_base64: one }]);
  assert.equal(result.body, 'Draft reviewed');
});

test('bounded document parser rejects custom structures, rich embeds and oversized contents', () => {
  const start = initial();
  assert.throws(() => document.reconstructWorkDocument(start, Array.from({ length: 501 }, (_, i) => ({ id: '$' + i, update_base64: 'AAA=' }))), /count/);
  assert.throws(() => document.decodeWorkUpdate('AAAA\n')); assert.throws(() => document.decodeWorkUpdate('AAAA', 2));
  const doc = new Y.Doc(); doc.getText('title').insert(0, 'Poison'); doc.getMap('native-provider').set('token', 'not permitted');
  assert.throws(() => document.validateWorkDocument(doc), /structure/); doc.destroy();
  const rich = new Y.Doc(); rich.getText('title').insert(0, 'Rich'); rich.getText('body').insert(0, 'Bold', { bold: true }); assert.throws(() => document.validateWorkDocument(rich), /plain text/); rich.destroy();
  assert.throws(() => edit(start, 'Shared document', 'x'.repeat(50001)), /size/);
});
