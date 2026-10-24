import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { randomUUID } from 'node:crypto';
import ts from 'typescript';
import { fieldReferences, resourceReferenceTargets } from './load-field-references.mjs';

const plain = value => JSON.parse(JSON.stringify(value));
function load(file, dependencies = {}, globals = {}) {
  const source = readFileSync(new URL('../src/lib/' + file, import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  const exports = {};
  vm.runInNewContext(compiled, { exports, require: name => name === './field-references' ? fieldReferences : name === './resource-reference-targets' ? resourceReferenceTargets(dependencies['./api']?.api) : dependencies[name], Set, Map, Date, URL, File, crypto: { randomUUID }, location: { origin: 'http://127.0.0.1:3000' }, ...globals });
  return exports;
}

test('CSV parsing respects escaped quotes and embedded newlines without evaluating formula cells', () => {
  const { parseCsv } = load('file-preview.ts');
  assert.deepEqual(plain(parseCsv('name,note\r\nAlice,"a,b"\r\nBob,"two ""quotes""\nand a newline"\r\nFormula,=1+1').rows),
    [['name', 'note'], ['Alice', 'a,b'], ['Bob', 'two "quotes"\nand a newline'], ['Formula', '=1+1']]);
  assert.throws(() => parseCsv('"unfinished'), /unfinished/);
  const bounded = parseCsv('a,b,c\nd,e,f\ng,h,i', 2, 2);
  assert.deepEqual(plain(bounded.rows), [['a', 'b'], ['d', 'e']]); assert.equal(bounded.truncated, true);
});

test('only explicit preview types are supported and encrypted Office files never use native previews', () => {
  const { previewKind, normalizeExperienceFile } = load('file-preview.ts');
  assert.equal(previewKind({ content_type: 'text/html', encrypted: true }), 'text');
  assert.equal(previewKind({ content_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', encrypted: true }), null);
  assert.equal(previewKind({ content_type: 'application/x-msdownload', encrypted: false }), null);
  const source = normalizeExperienceFile(new File(['print(1)'], 'source.py', { type: '' }));
  assert.equal(source.type, 'text/x-python');
  assert.equal(normalizeExperienceFile(new File(['bytes'], 'unknown.exe', { type: '' })).type, '');
});

function fixture() {
  const file = { id: '$file', document_id: 'actor:document', document_owner_id: 'actor', channel_id: 'channel', encrypted: true, secure_locator: '$file' };
  const state = { token: 'account', requests: [], sent: [], tamper: {}, available: true };
  const secure = { handles: async () => true, roomFor: async () => ({ roomId: '!room:test', c: { getUserId: () => '@fc_actor:test' } }),
    sendEvent: async (...args) => { state.sent.push(args); return { event_id: '$saved' }; },
    events: async () => [{ getType: () => 'eu.freecollab.file.comment', getRoomId: () => state.tamper.room || '!room:test', isEncrypted: () => true,
      isRedacted: () => !!state.tamper.redacted, getSender: () => state.tamper.sender || '@fc_peer:test', getId: () => '$comment', getTs: () => 1,
      getContent: () => ({ id: 'comment', document_id: file.document_id, file_event_id: '$file', owner_id: 'peer', text: 'Private review', ...state.tamper.content }) }],
    getDecryptedEvent: async () => ({ getRoomId: () => '!room:test', getType: () => 'eu.freecollab.file', isEncrypted: () => true, isRedacted: () => false,
      getSender: () => state.tamper.targetSender || '@fc_actor:test', getContent: () => ({ document_id: state.tamper.targetDocument || file.document_id }) }),
  };
  const api = { getAccessToken: () => state.token, getCurrentUserId: () => 'actor', getBaseUrl: () => '/api', request: async (...args) => { state.requests.push(args); return []; } };
  const exports = load('file-experience-api.ts', { './api': { api }, './secure-client': { getSecureMessaging: async () => secure }, './files-api': { listFileVersions: async () => state.available ? [file] : [] } });
  return { ...exports, file, secure, state, api };
}

test('encrypted file comments verify room, author, document target and redaction', async () => {
  const f = fixture(); assert.equal((await f.fileExperienceApi.comments(f.file))[0].text, 'Private review'); assert.equal(f.state.requests.length, 0);
  for (const tamper of [{ room: '!foreign:test' }, { sender: '@fc_forged:test' }, { redacted: true }, { targetDocument: 'foreign' }, { targetSender: '@fc_other:test' }]) {
    const bad = fixture(); bad.state.tamper = tamper; await assert.rejects(bad.fileExperienceApi.comments(bad.file), /verified|different/);
  }
});

test('secure file comments never send plaintext to the native application API or fall back on binding errors', async () => {
  const f = fixture(); await f.fileExperienceApi.comment(f.file, ' Private review ', 'stable');
  assert.equal(f.state.sent.length, 1); assert.equal(f.state.sent[0][1], 'eu.freecollab.file.comment'); assert.equal(f.state.sent[0][2].text, 'Private review'); assert.equal(f.state.requests.length, 0);
  const bad = fixture(); bad.secure.handles = async () => { throw new Error('Binding unavailable'); };
  await assert.rejects(bad.fileExperienceApi.comment(bad.file, 'Never plaintext'), /Binding unavailable/); assert.equal(bad.state.requests.length, 0); assert.equal(bad.state.sent.length, 0);
});

test('account changes and removed files stop encrypted comments before transmission', async () => {
  const f = fixture(); f.secure.handles = async () => { f.state.token = 'replacement'; return true; };
  await assert.rejects(f.fileExperienceApi.comment(f.file, 'Private draft'), /account changed/); assert.equal(f.state.sent.length, 0);
  const removed = fixture(); removed.state.available = false;
  await assert.rejects(removed.fileExperienceApi.comment(removed.file, 'Private draft'), /no longer available/); assert.equal(removed.state.sent.length, 0);
});

test('desktop Office never accepts encrypted files and rejects insecure remote handoff origins', async () => {
  const f = fixture(); await assert.rejects(f.fileExperienceApi.office(f.file, 'password'), /standard channel/); assert.equal(f.state.requests.length, 0);
  const exported = load('file-experience-api.ts', { './api': { api: { getBaseUrl: () => 'http://api.remote.invalid/api' } }, './secure-client': {}, './files-api': {} });
  assert.throws(() => exported.officeHandoff({ path: '/office-edit/id/secret/brief.docx', handler: 'ms-word' }), /HTTPS/);
});

for (const encrypted of [false, true]) test(`file comments retain explicit field references ${encrypted ? 'inside Matrix' : 'inside the native encrypted snapshot'}`, async () => {
  const f = fixture(); if (!encrypted) { f.secure.handles = async () => false; f.file.encrypted = false; delete f.file.secure_locator; }
  const refs = { text: [{ kind: 'document', id: 'actual-document', start: 2, end: 7 }] };
  await f.fileExperienceApi.comment(f.file, '  @Plan  ', 'stable', refs);
  const payload = encrypted ? f.state.sent[0][2] : JSON.parse(f.state.requests[0][1].body);
  assert.equal(payload.text, '  @Plan  '); assert.deepEqual(plain(payload.field_references), refs);
  if (encrypted) assert.equal(f.state.requests.length, 0);
});
test('secure file comment read validates ranges and never guesses missing identity metadata', async () => {
  const f = fixture(); f.state.tamper.content = { text: '@Plan', field_references: { text: [{ kind: 'document', id: 'actual', start: 0, end: 5 }] } };
  assert.equal((await f.fileExperienceApi.comments(f.file))[0].field_references.text[0].id, 'actual');
  f.state.tamper.content.field_references.text[0].end = 99;
  await assert.rejects(f.fileExperienceApi.comments(f.file), /references/);
});
