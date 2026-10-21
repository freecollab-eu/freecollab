import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { webcrypto } from 'node:crypto';

const compiled = ts.transpileModule(readFileSync(new URL('../src/mobile/library.ts', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
function fixture() {
  const exports = {}, records = new Map(), uploads = [], events = [], requests = [];
  let token = 'original-session', memory = false, encrypted = false, member = true, uploadError;
  const navigator = { onLine: true };
  class APIError extends Error { constructor(message, status) { super(message); this.status = status; } }
  const api = { getAccessToken: () => token, getCurrentUserId: () => 'alice', isMemorySession: () => memory,
    request: async path => { requests.push(path); if (path === '/auth/me') return { id: 'alice' }; if (!member) throw new APIError('removed', 403); return [{ user_id: 'alice' }]; },
    getMessages: async () => [{ id: 'message', channel_id: 'studio', content: 'private message', author_id: 'alice', encrypted: true, created_at: new Date().toISOString() }] };
  const expected = () => ({ user: 'alice', token });
  const storage = { owner: expected, assertOwner: value => { if (value.token !== token) throw new Error('changed'); },
    fingerprint: async value => value + '-digest',
    listPrivate: async kind => [...records.entries()].filter(([key]) => key.startsWith(kind + ':')).map(([key, value]) => ({ id: key.slice(kind.length + 1), value: structuredClone(value) })),
    savePrivate: async (kind, id, value, actor) => { storage.assertOwner(actor); records.set(kind + ':' + id, structuredClone(value)); },
    removePrivate: async (kind, id) => { records.delete(kind + ':' + id); } };
  const file = { id: 'reference', channel_id: 'studio', filename: 'trusted.txt', content_type: 'text/plain', size: 5 };
  const provider = { listChannelFiles: async () => [file], downloadChannelFile: async actual => { assert.equal(actual.filename, 'trusted.txt'); return new Blob(['hello'], { type: 'text/plain' }); },
    uploadChannelFile: async (channelId, content, options) => { uploads.push({ channelId, text: await content.text(), options }); if (uploadError) throw uploadError; return { id: options.requestId, channel_id: channelId }; } };
  vm.runInNewContext(compiled, { exports, require: name => name.endsWith('/api') ? { api, APIError } : name.endsWith('files-api') ? provider : name === './storage' ? storage : { getSecureMessaging: async () => ({ handles: async () => encrypted, roomFor: async () => ({}) }) }, navigator, crypto: webcrypto, TextEncoder, Uint8Array, ArrayBuffer, Date, Blob, File, Error, TypeError, DOMException, AbortSignal, Event, CustomEvent, btoa, atob, location: { origin: 'https://app.test' }, window: { dispatchEvent: event => events.push(event.type) } });
  return { ...exports, records, navigator, uploads, requests, events, file, token: value => { token = value; }, embedded: () => { memory = true; }, secure: value => { encrypted = value; }, revoke: () => { member = false; }, uploadFailure: value => { uploadError = value; } };
}
const note = () => new File(['hello'], 'local.txt', { type: 'text/plain' });

test('offline history is opt-in and embedded sessions cannot enroll or persist', async () => {
  const f = fixture(); assert.equal((await f.offlineLibrary.list()).channels.length, 0);
  await assert.rejects(f.attachmentOutbox.enqueue('studio', note()), /Save this channel/);
  f.embedded(); await assert.rejects(f.offlineLibrary.enable('studio', 'Studio', false), /standalone/); assert.equal(f.records.size, 0);
});
test('changed sign-in erases previous history and files instead of relabeling them', async () => {
  const f = fixture(); await f.offlineLibrary.enable('studio', 'Studio', false); await f.offlineLibrary.saveFile(f.file);
  f.token('replacement-session'); f.navigator.onLine = false;
  const list = await f.offlineLibrary.list(); assert.equal(list.channels.length, 0); assert.equal(list.files.length, 0); assert.equal(f.records.size, 0);
});
test('expired local copies are removed and cannot be read offline', async () => {
  const f = fixture(); await f.offlineLibrary.enable('studio', 'Studio', false);
  f.records.get('offline-channel:studio').savedAt = new Date(Date.now() - 8 * 86400000).toISOString(); f.navigator.onLine = false;
  assert.equal((await f.offlineLibrary.list()).channels.length, 0); assert.equal(f.records.size, 0);
});
test('provider-confirmed metadata is used for saved files and bounded local bytes', async () => {
  const f = fixture(); await f.offlineLibrary.enable('studio', 'Studio', false); await f.offlineLibrary.saveFile({ ...f.file, filename: 'forged.html', content_type: 'text/html' });
  f.navigator.onLine = false; const saved = await f.offlineLibrary.fileBlob('reference'); assert.equal(saved.name, 'trusted.txt'); assert.equal(await saved.blob.text(), 'hello');
  await assert.rejects(f.attachmentOutbox.enqueue('studio', new File([new Uint8Array(5 * 1048576 + 1)], 'huge.txt', { type: 'text/plain' })), /5 MB/);
});
test('known online membership revocation purges retained copies and blocks attachment dispatch', async () => {
  const f = fixture(); await f.offlineLibrary.enable('studio', 'Studio', false); await f.offlineLibrary.saveFile(f.file); f.navigator.onLine = false;
  await f.attachmentOutbox.enqueue('studio', note()); f.revoke(); f.navigator.onLine = true;
  assert.equal((await f.offlineLibrary.list()).channels.length, 0); await f.attachmentOutbox.flush(); assert.equal(f.uploads.length, 0);
  assert.equal((await f.attachmentOutbox.list())[0].state, 'review'); assert.equal([...f.records.keys()].some(key => /^offline-/.test(key)), false);
});
test('queued file sign-in and encryption mode changes both require review before any upload', async () => {
  for (const change of ['token', 'secure']) {
    const f = fixture(); await f.offlineLibrary.enable('studio', 'Studio', false); f.navigator.onLine = false; await f.attachmentOutbox.enqueue('studio', note());
    if (change === 'token') f.token('another-session'); else f.secure(true);
    f.navigator.onLine = true; await f.attachmentOutbox.flush(); assert.equal(f.uploads.length, 0); assert.equal((await f.attachmentOutbox.list())[0].state, 'review');
  }
});
test('ambiguous transfer preserves original durable idempotency identifier for retry', async () => {
  const f = fixture(); await f.offlineLibrary.enable('studio', 'Studio', false); f.navigator.onLine = false;
  await f.attachmentOutbox.enqueue('studio', note(), undefined, 'stable-upload-identifier'); f.navigator.onLine = true;
  f.uploadFailure(new TypeError('network lost')); await f.attachmentOutbox.flush(); assert.equal((await f.attachmentOutbox.list())[0].state, 'queued');
  f.uploadFailure(undefined); await f.attachmentOutbox.flush(); assert.equal((await f.attachmentOutbox.list()).length, 0);
  assert.equal(f.uploads.length, 2); assert.equal(f.uploads[0].options.requestId, 'stable-upload-identifier'); assert.equal(f.uploads[1].options.requestId, 'stable-upload-identifier');
  assert.equal(f.uploads[1].text, 'hello');
});
test('encrypted queue uses normal secure file routing and rejects changed encryption instead of native fallback', async () => {
  const f = fixture(); f.secure(true); await f.offlineLibrary.enable('studio', 'Studio', true); f.navigator.onLine = false; await f.attachmentOutbox.enqueue('studio', note());
  assert.equal((await f.attachmentOutbox.list())[0].secure, true); f.navigator.onLine = true; await f.attachmentOutbox.flush(); assert.equal(f.uploads.length, 1);
  assert.equal(f.uploads[0].channelId, 'studio');
});
