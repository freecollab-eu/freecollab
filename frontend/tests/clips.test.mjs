import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import vm from 'node:vm';
import ts from 'typescript';
import { fieldReferences, resourceReferenceTargets } from './load-field-references.mjs';

function fixture() {
  const state = { token: 'actor-session', uploads: [], requests: [], sent: [], tamper: {}, failCrypto: false };
  const file = { id: '$file', channel_id: 'room', uploader_id: 'actor', filename: 'encrypted.webm', content_type: 'audio/webm', size: 10, encrypted: true };
  const fields = { title: 'Private clip', duration: 3, transcript: 'Private transcript', captions: [{ start: 0, end: 2, text: 'Private transcript' }] };
  const row = { id: 'clip', channel_id: 'room', created_by: 'actor', created_at: '2026-10-21', content_mode: 'matrix', matrix_event_id: '$clip' };
  const secure = {
    handles: async () => true,
    roomFor: async () => ({ roomId: '!room:test', c: { getUserId: () => '@fc_actor:test' } }),
    sendEvent: async (...args) => { state.sent.push(args); return { event_id: '$clip' }; },
    getDecryptedEvent: async () => ({ isEncrypted: () => true, isRedacted: () => !!state.tamper.redacted, getType: () => state.tamper.type || 'eu.freecollab.clip', getRoomId: () => state.tamper.room || '!room:test', getSender: () => state.tamper.sender || '@fc_actor:test', getContent: () => ({ id: state.requests.at(-1)?.[1]?.body ? JSON.parse(state.requests.at(-1)[1].body).id : row.id, channel_id: 'room', fields, file_event_id: '$file', ...state.tamper.content }) }),
  };
  const api = { getAccessToken: () => state.token, request: async (...args) => { state.requests.push(args); return args[1]?.method === 'POST' ? { ...row, id: JSON.parse(args[1].body).id } : [row]; } };
  const exports = {}, source = readFileSync(new URL('../src/lib/clips-api.ts', import.meta.url), 'utf8');
  const dependencies = { './meeting-capture-proof': { nativeRecordingProof:()=>{throw new Error('Unexpected recording capture');}, authorizeNativeRecording:()=>{throw new Error('Unexpected recording capture');} }, './encrypted-work-api': { loadEncryptedRoomContext:async()=>({mode:'local',room_id:'!room:test',connection_id:'c'.repeat(32),matrix_user_id:'@fc_actor:test'}), publishEncryptedRoomPointer:()=>{throw new Error('Unexpected federated pointer');} }, './encrypted-room-events':{}, './secure/files':{}, './field-references': fieldReferences, './resource-reference-targets': resourceReferenceTargets(api), './api': { api }, './secure-client': { getSecureMessaging: async () => { if (state.failCrypto) throw new Error('Locked encrypted account'); return secure; } }, './files-api': { uploadChannelFile: async (...args) => { state.uploads.push(args); return file; }, listChannelFiles: async () => [file], downloadChannelFile: async () => new Blob(['bytes']) } };
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText, { exports, require: name => dependencies[name], crypto: { randomUUID }, File, Blob, Date });
  return { ...exports, state, fields, secure };
}

test('secure clip publishes only an opaque event reference to native API', async () => {
  const f = fixture(); const result = await f.clipsApi.create('room', new Blob(['private recording'], { type: 'audio/webm;codecs=opus' }), f.fields);
  assert.equal(f.state.uploads[0][1].type, 'audio/webm');
  const body = JSON.parse(f.state.requests.at(-1)[1].body); assert.deepEqual(Object.keys(body).sort(), ['id', 'matrix_event_id']);
  assert.equal(body.matrix_event_id, '$clip'); assert.equal(f.state.sent[0][1], 'eu.freecollab.clip'); assert.equal(result.fields.transcript, 'Private transcript');
  assert.ok(!JSON.stringify(f.state.requests).includes('Private transcript') && !JSON.stringify(f.state.requests).includes('private recording'));
});

test('crypto bootstrap failure sends neither recording nor plaintext metadata', async () => {
  const f = fixture(); f.state.failCrypto = true;
  await assert.rejects(f.clipsApi.create('room', new Blob(['secret']), f.fields), /Locked/);
  assert.equal(f.state.uploads.length, 0); assert.equal(f.state.requests.length, 0);
});

test('channel encryption change during upload cannot forward a secure transcript to native API', async () => {
  const f = fixture(); f.secure.handles = async () => false;
  await assert.rejects(f.clipsApi.create('room', new Blob(['secret']), f.fields), /encryption changed/);
  assert.equal(f.state.requests.length, 0); assert.equal(f.state.sent.length, 0);
});

for (const tamper of [{ sender: '@fc_attacker:test' }, { room: '!wrong:test' }, { type: 'm.room.message' }, { redacted: true }, { content: { id: 'wrong' } }]) test('tampered encrypted clip is unavailable: ' + JSON.stringify(tamper), async () => {
  const f = fixture(); f.state.tamper = tamper; const [row] = await f.clipsApi.list('room');
  assert.ok(row.resolution_error); assert.equal(row.file, undefined); assert.equal(row.fields, undefined);
});

test('caption export preserves timing while preventing cue markup injection', () => {
  const { clipVtt } = fixture(); const text = clipVtt([{ start: 1.25, end: 2.75, text: '<script>bad</script> --> next' }]);
  assert.match(text, /00:00:01.250 --> 00:00:02.750/); assert.ok(!text.includes('<script>')); assert.match(text, /→ next/);
});

test('clip title/transcript reference IDs remain encrypted and preserve exact referenced text', async () => {
  const f = fixture(); f.fields.title = '  @Plan  '; f.fields.transcript = '@Ada review';
  f.fields.field_references = { title: [{ kind: 'document', id: 'actual-document', start: 2, end: 7 }], transcript: [{ kind: 'person', id: 'actual-person', start: 0, end: 4 }] };
  const saved = await f.clipsApi.create('room', new Blob(['recording'], { type: 'audio/webm' }), f.fields);
  assert.equal(saved.fields.title, '  @Plan  '); assert.deepEqual(JSON.parse(JSON.stringify(saved.fields.field_references)), f.fields.field_references);
  assert.deepEqual(JSON.parse(JSON.stringify(f.state.sent[0][2].fields.field_references)), f.fields.field_references);
  assert.ok(!JSON.stringify(f.state.requests).includes('actual-document'));
});
