import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { webcrypto } from 'node:crypto';
import vm from 'node:vm';
import ts from 'typescript';

const author = '@fc_alice:matrix.example.com';
function compile(name, dependencies, extra = {}) {
  const exports = {};
  const source = readFileSync(new URL('../src/lib/' + name, import.meta.url), 'utf8');
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText,
    { exports, require: name => { assert.ok(name in dependencies, name); return dependencies[name]; }, crypto: webcrypto, TextEncoder, Uint8Array, Date, Map, Set, ...extra });
  return exports;
}
function fixture() {
  const original = { event_id: '$original', sender: author, type: 'm.room.message', timestamp: 1000, content: { msgtype: 'm.text', body: 'Original reviewed text' } };
  const state = { token: 'one', actor: 'alice', events: new Map([['$original', original]]), ids: [], nextBatch: null, reads: [], relationReads: [], onRelations: null, verify: null, loads: 0, saves: 0, stopped: 0, onLoad: null };
  const window = new EventTarget();
  const api = { getAccessToken: () => state.token, getCurrentUserId: () => state.actor };
  const verified = async (channel, id, options) => {
    state.reads.push({ channel, id, options });
    if (state.verify) await state.verify(id);
    if (!state.events.has(id)) throw Error('The encrypted source was redacted, unavailable or unverified.');
    return structuredClone(state.events.get(id));
  };
  const secure = { roomFor: async () => ({ roomId: '!room:matrix.example.com', c: { relations: async (...args) => {
    state.relationReads.push(args);
    if (state.onRelations) await state.onRelations();
    return { events: state.ids.map(id => ({ getId: () => id })), nextBatch: state.nextBatch };
  } } }) };
  const helper = compile('encrypted-message-preview.ts', { './api': { api }, './secure-client': { getSecureMessaging: async () => secure }, './encrypted-work-api': { verifyEncryptedRoomEvent: verified } }, { window });
  const edit = (id, text, timestamp = 2000, fields = {}) => {
    state.ids.push(id); state.events.set(id, { event_id: id, sender: author, type: 'm.room.message', timestamp,
      content: { 'm.relates_to': { rel_type: 'm.replace', event_id: '$original' }, 'm.new_content': { msgtype: 'm.text', body: text } }, ...fields });
  };
  return { state, window, api, helper, edit };
}

test('current encrypted preview verifies original and every edit instead of using original body or SDK filtering', async () => {
  const f = fixture(); f.edit('$later-z', 'Current verified words', 3000); f.edit('$earlier', 'Intermediate words'); f.edit('$later-a', 'Tie ordered before z', 3000);
  const result = await f.helper.verifiedEncryptedMessagePreview('channel', '$original');
  assert.equal(result.id, '$original'); assert.equal(result.text, 'Current verified words'); assert.equal(result.author_id, author);
  assert.equal(result.created_at, new Date(1000).toISOString()); assert.equal(result.edited_at, new Date(3000).toISOString());
  assert.equal(result.expected_text_sha256, Buffer.from(await webcrypto.subtle.digest('SHA-256', new TextEncoder().encode(result.text))).toString('hex'));
  assert.equal(f.state.relationReads[0][3], null, 'Keep potentially undecryptable edit candidates for independent verification');
  assert.deepEqual(f.state.reads.map(row => row.id), ['$original', '$later-z', '$earlier', '$later-a', '$original']);
  assert.ok(f.state.reads.every(row => row.channel === 'channel' && row.options.requireCurrentParticipant === false));
});

test('no-edit original remains valid; an edit event cannot masquerade as the original reference', async () => {
  const f = fixture(), result = await f.helper.verifiedEncryptedMessagePreview('channel', '$original');
  assert.equal(result.text, 'Original reviewed text'); assert.equal(result.edited_at, null);
  f.edit('$edit', 'New words'); await assert.rejects(f.helper.verifiedEncryptedMessagePreview('channel', '$edit'), /original text message/);
});

test('simultaneous edits use exact code-unit event IDs independent of locale collation or arrival order', async () => {
  for (const ids of [['$A', '$a'], ['$a', '$A']]) {
    const f = fixture(); for (const id of ids) f.edit(id, id === '$a' ? 'Exact current source' : 'Collation would choose this', 4000);
    const result = await f.helper.verifiedEncryptedMessagePreview('channel', '$original');
    assert.equal(result.text, 'Exact current source'); assert.equal(result.edited_at, new Date(4000).toISOString());
  }
});

test('incomplete, duplicate, undecryptable, foreign-author and wrong-target replacements fail closed', async () => {
  for (const kind of ['incomplete', 'duplicate', 'unavailable', 'foreign', 'target', 'new-content', 'type']) {
    const f = fixture(); f.edit('$edit', 'Changed words');
    if (kind === 'incomplete') f.state.nextBatch = 'more-unreviewed-edits';
    if (kind === 'duplicate') f.state.ids.push('$edit');
    if (kind === 'unavailable') f.state.events.delete('$edit');
    if (kind === 'foreign') f.state.events.get('$edit').sender = '@fc_other:matrix.example.com';
    if (kind === 'target') f.state.events.get('$edit').content['m.relates_to'].event_id = '$other-message';
    if (kind === 'new-content') delete f.state.events.get('$edit').content['m.new_content'];
    if (kind === 'type') f.state.events.get('$edit').content['m.new_content'].msgtype = 'm.file';
    await assert.rejects(f.helper.verifiedEncryptedMessagePreview('channel', '$original'), /history|unavailable|replacement/);
  }
});

test('revocation after the edit lookup and auth ABA cannot return a stale plaintext source', async () => {
  const f = fixture(); let originals = 0;
  f.state.verify = id => { if (id === '$original' && ++originals === 2) throw Error('The original was redacted or its device revoked.'); };
  await assert.rejects(f.helper.verifiedEncryptedMessagePreview('channel', '$original'), /redacted|revoked/);
  const g = fixture(); g.state.onRelations = () => { g.state.token = 'other'; g.window.dispatchEvent(new Event('freecollab:auth-changed')); g.state.token = 'one'; };
  await assert.rejects(g.helper.verifiedEncryptedMessagePreview('channel', '$original'), /account changed/);
});

test('stack save rechecks edited text after loading current aggregate and never publishes a stale review', async () => {
  const f = fixture(), source = await f.helper.verifiedEncryptedMessagePreview('channel', '$original');
  const stack = { id: 'alice:stack', generation: '1'.repeat(32), author, title: 'Working sources', version: 1, created_at: '2026-10-25T10:00:00Z', updated_at: '2026-10-25T10:00:00Z' };
  const fields = { title: 'Document organization', body: JSON.stringify({ version: 1, stacks: [stack], placements: [], relations: [], messages: [] }) };
  const revision = { event_id: '$root', sender: author, parents: [], timestamp: 1, kind: 'organization', fields }, record = { id: 'org', kind: 'organization', history: [revision], heads: [revision], selected: revision, conflicts: false };
  class Client {
    async load() { f.state.loads++; f.edit('$during-load', 'Edited while organization loads'); return { records: [record], context: { matrix_user_id: author } }; }
    async save() { f.state.saves++; throw Error('No stale publication may occur'); }
    stop() { f.state.stopped++; }
  }
  const adapter = compile('encrypted-document-organization-api.ts', {
    './api': { api: f.api }, './encrypted-work-api': { EncryptedWorkClient: Client }, './encrypted-message-preview': f.helper,
    './shared-documents-api': {}, './federated-work-api': {}, './federated-work-model': { authorNamespace: async () => 'alice', matrixIdentity: value => value === author },
  });
  await assert.rejects(adapter.encryptedDocumentOrganizationApi.saveMessage('channel', stack, source, 'reference'), /source message changed/);
  assert.equal(f.state.loads, 1); assert.equal(f.state.saves, 0); assert.equal(f.state.stopped, 1);
});
