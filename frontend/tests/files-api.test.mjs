import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/lib/files-api.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
function fixture(bootstrapError) {
  const requests = [], events = [], exports = {};
  let token = 'old-token';
  const api = { getAccessToken: () => token, getBaseUrl: () => 'https://freecollab.test/api', getFiles: async () => [], request: async () => [], deleteFile: async () => {} };
  class XHR {
    upload = {}; headers = {}; response = null; status = 0;
    constructor() { requests.push(this); }
    open(method, url) { this.method = method; this.url = url; }
    setRequestHeader(name, value) { this.headers[name] = value; }
    send(body) { this.body = body; }
    abort() { this.onabort?.(); }
    complete(status, response) { this.status = status; this.response = response; this.onload?.(); }
    progress(loaded, total = 100) { this.upload.onprogress?.({ lengthComputable: true, loaded, total }); }
  }
  vm.runInNewContext(compiled, { exports, require: name => name === './api' ? {api} : {getSecureMessaging: async () => { if (bootstrapError) throw bootstrapError; }}, XMLHttpRequest: XHR, DOMException, FormData, File, Blob, Event, fetch: async () => new Response('native'), window: { dispatchEvent: event => events.push(event.type) } });
  exports.registerSecureFileProvider({handles:async () => false});
  return { files: exports, requests, events, switchToken: () => { token = 'new-token'; } };
}
const file = () => new File(['hello'], 'note.txt', { type: 'text/plain' });
const tick = async () => { for (let index=0;index<10;index++) await Promise.resolve(); };

test('upload authenticates by header, preserves retry key, and waits for acknowledgement before 100%', async () => {
  const { files, requests } = fixture(), progress = [];
  const pending = files.uploadChannelFile('channel/x', file(), { requestId: 'stable-request-key', onProgress: value => progress.push(value) });
  await tick(); const request = requests[0];
  assert.equal(request.url, 'https://freecollab.test/api/files/channels/channel%2Fx');
  assert.equal(request.headers.Authorization, 'Bearer old-token'); assert.equal(request.headers['Idempotency-Key'], 'stable-request-key');
  request.progress(100); assert.deepEqual(progress, [99]); request.complete(200, { id: 'acknowledged' });
  assert.equal((await pending).id, 'acknowledged'); assert.deepEqual(progress, [99, 100]);
});

test('cancel rejects and suppresses later progress and acknowledgements; retry keeps its key', async () => {
  const { files, requests } = fixture(), controller = new AbortController(), progress = [];
  const pending = files.uploadChannelFile('channel', file(), { signal: controller.signal, requestId: 'same-retry-key', onProgress: value => progress.push(value) });
  const rejected = assert.rejects(pending, { name: 'AbortError' }); await tick(); controller.abort(); await rejected;
  requests[0].progress(100); requests[0].complete(200, { id: 'late' }); assert.deepEqual(progress, []);
  const retry = files.uploadChannelFile('channel', file(), { requestId: 'same-retry-key' }); await tick();
  assert.equal(requests[1].headers['Idempotency-Key'], 'same-retry-key'); requests[1].complete(200, { id: 'original-ack' }); assert.equal((await retry).id, 'original-ack');
});

test('an already cancelled upload sends no request', async () => {
  const { files, requests } = fixture(), controller = new AbortController(); controller.abort();
  await assert.rejects(files.uploadChannelFile('channel', file(), { signal: controller.signal }), { name: 'AbortError' }); assert.equal(requests.length, 0);
});

for (const changed of [true, false]) test(`upload 401 expires only its original current account (changed=${changed})`, async () => {
  const { files, requests, events, switchToken } = fixture();
  const pending = files.uploadChannelFile('channel', file()); const rejected = assert.rejects(pending, /expired/);
  await tick(); if (changed) switchToken(); requests[0].complete(401, { detail: 'expired' }); await rejected;
  assert.deepEqual(events, changed ? [] : ['freecollab:session-expired']);
});

test('new versions upload to the immutable document version endpoint', async () => {
  const { files, requests } = fixture(); const pending = files.uploadChannelFile('channel', file(), { documentId: 'original/id' });
  await tick(); assert.equal(requests[0].url, 'https://freecollab.test/api/files/original%2Fid/versions'); requests[0].complete(200, { id: 'new-version' }); await pending;
});

test('secure lookup failures fail closed without a native plaintext upload', async () => {
  const { files, requests } = fixture(); files.registerSecureFileProvider({ handles: async () => { throw new Error('Unlock this channel'); } });
  await assert.rejects(files.uploadChannelFile('channel', file()), /Unlock/); assert.equal(requests.length, 0);
});

test('secure operations use the encryption provider, never native requests', async () => {
  const { files, requests } = fixture(), calls = [], metadata = { id: '$event', channel_id: 'secure', encrypted: true };
  const stop = files.registerSecureFileProvider({ handles: async () => true,
    list: async id => { calls.push(['list', id]); return [metadata]; },
    upload: async (id, input, options) => { calls.push(['upload', id, input.name, options.requestId]); return metadata; },
    download: async () => new Blob(['decrypted']), versions: async () => [metadata], delete: async () => { calls.push(['delete']); } });
  assert.equal((await files.listChannelFiles('secure'))[0].id, '$event'); assert.equal((await files.uploadChannelFile('secure', file(), { requestId: 'encrypted-request' })).encrypted, true);
  assert.equal(await (await files.downloadChannelFile(metadata)).text(), 'decrypted'); assert.equal((await files.listFileVersions(metadata)).length, 1); await files.deleteChannelFile(metadata);
  assert.deepEqual(calls, [['list', 'secure'], ['upload', 'secure', 'note.txt', 'encrypted-request'], ['delete']]); assert.equal(requests.length, 0);
  stop(); await assert.rejects(files.downloadChannelFile(metadata), /not ready|Unlock/);
});


test('crypto provider initialization failure sends no plaintext file bytes', async () => {
  const {files, requests} = fixture(new Error('Crypto module unavailable'));
  await assert.rejects(files.uploadChannelFile('secure',file()), /Crypto module unavailable/);
  assert.equal(requests.length,0);
});
