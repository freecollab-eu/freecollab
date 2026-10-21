import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash, webcrypto } from 'node:crypto';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/lib/proofs-api.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
function fixture() {
  const exports = {}, requests = [], signals = [];
  let token = 'original', download = async () => new Blob(['encrypted-version bytes']);
  const api = { getAccessToken: () => token, getBaseUrl: () => '/api', request: async (path, options) => {
    requests.push({ path, body: options?.body ? JSON.parse(options.body) : null }); return {};
  } };
  vm.runInNewContext(compiled, {
    exports, require: name => name === './api' ? { api } : { downloadChannelFile: (...args) => download(...args) },
    TextEncoder, crypto: webcrypto, Blob, window: { dispatchEvent: event => signals.push(event.type) }, Event,
  });
  return { ...exports, requests, signals, setToken: value => { token = value; }, setDownload: value => { download = value; } };
}

test('decision timestamp protocol commits to exact UTF-8 revision fields in a stable cross-language format', async () => {
  const f = fixture();
  const record = { id: 'decision', revision: 2, title: 'Återställning 🌲', content: 'Exact\nwords', status: 'accepted', responsible_user_id: null, decision_date: '2026-10-20', source_message_id: null };
  const expected = '["freecollab-decision-v1","decision",2,"Återställning 🌲","Exact\\nwords","accepted",null,"2026-10-20",null]';
  assert.equal(Buffer.from(f.decisionTimestampBytes(record)).toString('utf8'), expected);
  assert.equal(await f.timestampDigest({ decision: record }), createHash('sha256').update(expected).digest('hex'));
  assert.notEqual(await f.timestampDigest({ decision: { ...record, revision: 3 } }), await f.timestampDigest({ decision: record }));
  assert.throws(() => f.decisionTimestampBytes({ ...record, resolution_error: 'Locked' }), /Unlock this exact/);
});

test('encrypted file requests disclose only explicit digest and opaque version pointer, never metadata or bytes', async () => {
  const f = fixture();
  const file = { id: '$event', secure_locator: '$version', channel_id: 'secret-channel', filename: 'secret-name.txt', content_type: 'text/plain', encrypted: true };
  const digest = await f.timestampDigest({ file });
  assert.equal(digest, createHash('sha256').update('encrypted-version bytes').digest('hex'));
  await f.proofsApi.request({ file }, digest);
  assert.equal(f.requests.length, 1);
  assert.deepEqual(f.requests[0], { path: '/channels/secret-channel/proofs', body: { target_kind: 'file', target_id: '$version', sha256: digest, consent: true } });
  assert.ok(!JSON.stringify(f.requests).includes('secret-name'));
  assert.ok(!JSON.stringify(f.requests).includes('encrypted-version bytes'));
});

test('an account switch during decryption aborts timestamp hashing before disclosure', async () => {
  const f = fixture();
  f.setDownload(async () => { f.setToken('new-account'); return new Blob(['old private bytes']); });
  await assert.rejects(f.timestampDigest({ file: { id: 'old', channel_id: 'channel' } }), /signed-in account changed/);
  assert.equal(f.requests.length, 0);
});
