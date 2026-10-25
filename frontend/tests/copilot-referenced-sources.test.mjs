import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { createHash, webcrypto } from 'node:crypto';

const source = readFileSync(new URL('../src/lib/copilot-api.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const plain = value => JSON.parse(JSON.stringify(value));
const message = (id = 'old-message', overrides = {}) => ({ id, channel_id: 'channel', content: 'An older selected message', encrypted: false, author_id: 'author', created_at: '2026-10-23T10:00:00Z', ...overrides });
const document = (id, overrides = {}) => ({ id, channel_id: 'channel', title: id, version: 3, archived: false, ...overrides });
const event = (overrides = {}) => ({ getId: () => '$old', getRoomId: () => '!approved:example.test', getType: () => 'm.room.message', isEncrypted: () => true, isRedacted: () => false, isDecryptionFailure: () => false, getOriginalContent: () => ({}), ...overrides });

function fixture({ secure = false, documents = [], native = message(), canonical = [], encryptedEvent = event(), eventFailure, files = [], clips = [], fileBody = 'Reviewed file text', crypto = webcrypto } = {}) {
  let token = 'original-account', requestHandler = async () => native;
  const calls = [], exports = {};
  const api = { getAccessToken: () => token, request: async path => { calls.push(['native', path]); return requestHandler(path); }, getMessages: async () => { throw new Error('Inline lookup must not read the capped native message inventory'); } };
  const secureClient = {
    handles: async channel => { calls.push(['handles', channel]); return secure; },
    roomFor: async channel => { calls.push(['room', channel]); return { roomId: '!approved:example.test' }; },
    getDecryptedEvent: async (channel, id) => { calls.push(['event', channel, id]); if (eventFailure) throw eventFailure; return encryptedEvent; },
    getMessages: async (...args) => { calls.push(['canonical', ...args]); return canonical; },
  };
  const unused = () => { throw new Error('Inline lookup must not read unselected files, clips or projects'); };
  const dependencies = {
    './api': { api }, './secure-client': { getSecureMessaging: async () => secureClient },
    './files-api': { downloadChannelFile: async file => { calls.push(['download', file.id]); return new Blob([fileBody]); }, listChannelFiles: async channel => { calls.push(['files', channel]); return files; } },
    './shared-documents-api': { sharedDocumentsApi: { list: async channel => { calls.push(['documents', channel]); return documents; } } },
    './project-tasks-api': { projectTasksApi: { projects: unused } }, './clips-api': { clipsApi: { list: async channel => { calls.push(['clips', channel]); return clips; } } },
  };
  vm.runInNewContext(compiled, { exports, require: name => { assert.ok(name in dependencies, name); return dependencies[name]; }, Set, encodeURIComponent, crypto, TextEncoder, TextDecoder, Uint8Array });
  return { ...exports, calls, changeActor: () => { token = 'different-account'; }, setRequest: fn => { requestHandler = fn; } };
}

test('an old native message resolves by exact ID without reading the recent-message picker', async () => {
  const h = fixture();
  const result = await h.loadCopilotReferencedChoices('channel', [{ kind: 'message', id: 'old-message' }]);
  assert.equal(result.secure, false);
  assert.deepEqual(plain(result.choices.map(choice => choice.message)), [message()]);
  assert.deepEqual(h.calls, [['handles', 'channel'], ['native', '/messages/old-message']]);
});

test('the twenty-first shared document is selected before any central picker display cap', async () => {
  const documents = Array.from({ length: 21 }, (_, index) => document('document-' + index));
  const h = fixture({ documents });
  const result = await h.loadCopilotReferencedChoices('channel', [{ kind: 'shared_document', id: 'document-20' }]);
  assert.equal(result.choices.length, 1);
  assert.deepEqual(plain(result.choices[0].document), documents[20]);
  assert.deepEqual(h.calls, [['handles', 'channel'], ['documents', 'channel']]);
});

test('mixed exact references retain reviewed order and do not load unselected resource kinds', async () => {
  const h = fixture({ documents: [document('selected'), document('omitted')] });
  const refs = [{ kind: 'shared_document', id: 'selected' }, { kind: 'message', id: 'old-message' }];
  const result = await h.loadCopilotReferencedChoices('channel', refs);
  assert.deepEqual(plain(result.choices.map(choice => choice.source)), refs);
  assert.ok(!JSON.stringify(result).includes('omitted'));
});

for (const [label, native] of [
  ['wrong ID', message('another')], ['wrong channel', message('old-message', { channel_id: 'elsewhere' })],
  ['deleted', message('old-message', { deleted: true })], ['locked', message('old-message', { locked: true })],
]) test(`native exact lookup rejects a ${label} source`, async () => {
  const h = fixture({ native });
  await assert.rejects(h.loadCopilotReferencedChoices('channel', [{ kind: 'message', id: 'old-message' }]), /unavailable/);
});

test('an account switch during exact native lookup discards its result', async () => {
  const h = fixture();
  h.setRequest(async () => { h.changeActor(); return message(); });
  await assert.rejects(h.loadCopilotReferencedChoices('channel', [{ kind: 'message', id: 'old-message' }]), /account changed/);
});

test('native message preparation binds the exact reviewed UTF-8 text to a SHA-256 digest', async () => {
  const h = fixture(), reviewed = message('selected', { content: 'Granskad text: åäö 🪴\nBevara  mellanrum.' });
  const source = await h.prepareCopilotSource({ source: { kind: 'message', id: reviewed.id }, message: reviewed }, 'channel', false);
  assert.deepEqual(plain(source), { kind: 'message', id: reviewed.id, expected_text_sha256: createHash('sha256').update(reviewed.content, 'utf8').digest('hex') });
  assert.deepEqual(h.calls, []);
});

test('an actor change while hashing native reviewed text discards the prepared source', async () => {
  let h;
  const crypto = { subtle: { digest: async (...args) => { h.changeActor(); return webcrypto.subtle.digest(...args); } } };
  h = fixture({ crypto });
  await assert.rejects(h.prepareCopilotSource({ source: { kind: 'message', id: 'old-message' }, message: message() }, 'channel', false), /account changed/);
});

test('secure exact lookup discloses the canonical authorized edit, never a raw original body', async () => {
  const canonical = message('$old', { content: 'Current authorized edit', encrypted: true, edited_at: '2026-10-23T10:01:00Z' });
  const h = fixture({ secure: true, canonical: [canonical], encryptedEvent: event({ getContent: () => { throw new Error('Raw original encrypted text must not be projected here'); } }) });
  const result = await h.loadCopilotReferencedChoices('channel', [{ kind: 'message', id: '$old' }]);
  assert.equal(result.secure, true);
  assert.deepEqual(plain(await h.prepareCopilotSource(result.choices[0], 'channel', true)), { kind: 'message', id: '$old', text: canonical.content, event_ids: ['$old'] });
  assert.deepEqual(h.calls, [['handles', 'channel'], ['room', 'channel'], ['event', 'channel', '$old'], ['canonical', 'channel']]);
});

test('an unavailable secure event fails without native or whole-history fallback', async () => {
  const h = fixture({ secure: true, eventFailure: new Error('Encrypted event not found.') });
  await assert.rejects(h.loadCopilotReferencedChoices('channel', [{ kind: 'message', id: '$old' }]), /Encrypted event not found/);
  assert.ok(!h.calls.some(([kind]) => kind === 'native' || kind === 'canonical'));
});

test('an event outside the bounded canonical secure window fails instead of using stale original text', async () => {
  const h = fixture({ secure: true, canonical: [message('$different', { encrypted: true })] });
  await assert.rejects(h.loadCopilotReferencedChoices('channel', [{ kind: 'message', id: '$old' }]), /unavailable/);
  assert.deepEqual(h.calls.filter(([kind]) => kind === 'canonical'), [['canonical', 'channel']]);
  assert.ok(!h.calls.some(([kind]) => kind === 'native'));
});

for (const [label, invalid] of [
  ['cross-room', { getRoomId: () => '!another:example.test' }],
  ['redacted', { isRedacted: () => true }], ['undecryptable', { isDecryptionFailure: () => true }],
  ['replacement event ID', { getOriginalContent: () => ({ 'm.relates_to': { rel_type: 'm.replace', event_id: '$base' } }) }],
]) test(`a ${label} encrypted event cannot become a reviewed source`, async () => {
  const h = fixture({ secure: true, encryptedEvent: event(invalid) });
  await assert.rejects(h.loadCopilotReferencedChoices('channel', [{ kind: 'message', id: '$old' }]), /unavailable/);
  assert.ok(!h.calls.some(([kind]) => kind === 'native' || kind === 'canonical'));
});

test('missing, archived, wrong-channel and unresolved shared documents fail without substitutes', async () => {
  for (const documents of [[], [document('selected', { archived: true })], [document('selected', { channel_id: 'elsewhere' })], [document('selected', { resolution_error: 'Locked' })]]) {
    const h = fixture({ documents });
    await assert.rejects(h.loadCopilotReferencedChoices('channel', [{ kind: 'shared_document', id: 'selected' }]), /unavailable/);
  }
});

test('duplicate, excessive and unsupported references are rejected before any reads', async () => {
  for (const refs of [Array.from({ length: 9 }, (_, id) => ({ kind: 'message', id: String(id) })), [{ kind: 'message', id: 'same' }, { kind: 'message', id: 'same' }], [{ kind: 'task', id: 'not-inline' }], [{ kind: 'message', id: '' }]]) {
    const h = fixture();
    await assert.rejects(h.loadCopilotReferencedChoices('channel', refs), /eight unique sources/);
    assert.deepEqual(h.calls, []);
  }
});

test('an exact text file loads only the selected file inventory and hashes the actual reviewed bytes', async () => {
  const file = { id: 'selected-file', channel_id: 'channel', filename: 'notes.txt', size: 25, content_type: 'text/plain' };
  const h = fixture({ files: [file], fileBody: 'Actual reviewed åäö 🪴' });
  const loaded = await h.loadCopilotReferencedChoices('channel', [{ kind: 'file', id: file.id }]);
  const source = await h.prepareCopilotSource(loaded.choices[0], 'channel', false);
  assert.deepEqual(plain(source), { kind: 'file', id: file.id, text: 'Actual reviewed åäö 🪴', expected_text_sha256: createHash('sha256').update('Actual reviewed åäö 🪴').digest('hex') });
  assert.deepEqual(h.calls, [['handles', 'channel'], ['files', 'channel'], ['download', file.id]]);
});

test('exact file references reject other channels, binary types and oversized contents before disclosure', async () => {
  const file = { id: 'f', channel_id: 'channel', filename: 'notes.txt', size: 25, content_type: 'text/plain' };
  for (const change of [{ channel_id: 'other' }, { content_type: 'image/png' }, { size: 12001 }]) {
    const h = fixture({ files: [{ ...file, ...change }] });
    await assert.rejects(h.loadCopilotReferencedChoices('channel', [{ kind: 'file', id: file.id }]), /unavailable/);
    assert.ok(!h.calls.some(([kind]) => kind === 'download'));
  }
  const h = fixture({ files: [file], fileBody: 'x'.repeat(12001) });
  const loaded = await h.loadCopilotReferencedChoices('channel', [{ kind: 'file', id: file.id }]);
  await assert.rejects(h.prepareCopilotSource(loaded.choices[0], 'channel', false), /at most 12 KB/);
});

test('clip AI uses the exact resolved transcript and refuses silent truncation or an unverified secure event', async () => {
  const clip = { id: 'c', channel_id: 'channel', fields: { title: 'Reviewed clip', transcript: 'Actual transcript' }, matrix_event_id: '$clip' };
  const h = fixture({ clips: [clip] });
  const loaded = await h.loadCopilotReferencedChoices('channel', [{ kind: 'clip', id: 'c' }]);
  const prepared = await h.prepareCopilotSource(loaded.choices[0], 'channel', true);
  assert.equal(prepared.text, 'Reviewed clip\nActual transcript');
  assert.deepEqual(plain(prepared.event_ids), ['$clip']);
  assert.equal(prepared.expected_text_sha256, createHash('sha256').update(prepared.text).digest('hex'));
  await assert.rejects(h.prepareCopilotSource({ ...loaded.choices[0], clip: { ...clip, matrix_event_id: null } }, 'channel', true), /available clip/);
  await assert.rejects(h.prepareCopilotSource({ ...loaded.choices[0], clip: { ...clip, fields: { ...clip.fields, transcript: 'x'.repeat(12000) } } }, 'channel', false), /Select a passage/);
  assert.deepEqual(h.calls, [['handles', 'channel'], ['clips', 'channel']]);
});


test('file source hashes preserve a UTF-8 BOM exactly as the canonical server reader does', async () => {
  const file = { id: 'bom-file', channel_id: 'channel', filename: 'bom.txt', size: 14, content_type: 'text/plain' };
  const body = '\uFEFFåäö text', h = fixture({ files: [file], fileBody: body });
  const loaded = await h.loadCopilotReferencedChoices('channel', [{ kind: 'file', id: file.id }]);
  const prepared = await h.prepareCopilotSource(loaded.choices[0], 'channel', false);
  assert.equal(prepared.text, body);
  assert.equal(prepared.expected_text_sha256, createHash('sha256').update(body).digest('hex'));
});
