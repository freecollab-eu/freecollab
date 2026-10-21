import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { encryptAttachment, decryptAttachment } from 'matrix-encrypt-attachment';

const source = readFileSync(new URL('../src/lib/secure/files.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const previewHelpers = {};
vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/lib/file-preview.ts', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, { exports: previewHelpers, File });
function fixture({ peerServer } = {}) {
  const exports = {}, events = [], media = new Map(), transactions = new Map(), uploads = [], downloads = [];
  const control = { owner: 'owner', failAfterSend: false, upload: null };
  function event(content, sender = '@fc_owner:local.test', id = '$' + crypto.randomUUID()) {
    return { getContent: () => content, getSender: () => sender, getId: () => id, getRoomId: () => '!room:local.test',
      getTs: () => Date.now(), getType: () => 'eu.freecollab.file', isEncrypted: () => true, isRedacted: () => false };
  }
  const c = { getUserId: () => `@fc_${control.owner}:local.test`, getHomeserverUrl: () => 'https://freecollab.test/api', getAccessToken: () => 'matrix-token',
    uploadContent: async (blob, options) => {
      uploads.push({ blob, options });
      if (control.upload) return control.upload(blob, options);
      const uri = 'mxc://local.test/' + crypto.randomUUID(); media.set(uri.split('/').at(-1), await blob.arrayBuffer()); return { content_uri: uri };
    } };
  const secureMessaging = { handles: async () => true, roomFor: async () => ({ c, room: { oldState: {} }, roomId: '!room:local.test', federatedPeerServer: peerServer }),
    events: async () => events,
    sendEvent: async (channel, type, content, transactionId) => {
      if (transactionId && transactions.has(transactionId)) return { event_id: transactions.get(transactionId) };
      const item = event(content, c.getUserId()); events.push(item);
      if (transactionId) transactions.set(transactionId, item.getId());
      if (control.failAfterSend) { control.failAfterSend = false; throw new Error('lost acknowledgement'); }
      return { event_id: item.getId() };
    },
    getDecryptedEvent: async (channel, id) => { const item = events.find(e => e.getId() === id); if (!item) throw new Error('missing event'); return item; },
  };
  vm.runInNewContext(compiled, { exports, require: name => name === 'matrix-encrypt-attachment' ? { encryptAttachment, decryptAttachment } : name === '../file-preview' ? previewHelpers : { secureMessaging },
    crypto, TextEncoder, Uint8Array, Blob, File, DOMException, AbortController,
    fetch: async (url, options) => { downloads.push({ url, options }); return new Response(media.get(decodeURIComponent(url.split('/').at(-1))) || new Uint8Array([1])); } });
  return { provider: exports.secureFileProvider, events, media, uploads, downloads, event, control };
}
const file = () => new File(['private attachment bytes'], 'private-name.txt', { type: 'text/plain' });
const opts = () => ({ requestId: crypto.randomUUID() });

test('secure file transfer encrypts bytes and sends neither filename nor media type to storage; decrypts exact bytes', async () => {
  const { provider, uploads, downloads } = fixture();
  const record = await provider.upload('channel', file(), opts());
  assert.equal(record.encrypted, true); assert.equal(uploads[0].blob.type, 'application/octet-stream');
  assert.equal(uploads[0].options.includeFilename, false); assert.equal('name' in uploads[0].options, false);
  assert.notEqual(await uploads[0].blob.text(), 'private attachment bytes');
  const blob = await provider.download({ ...record, secure_url: 'mxc://evil.test/fake', encrypted_file: {} });
  assert.equal(await blob.text(), 'private attachment bytes');
  assert.ok(downloads[0].url.startsWith('https://freecollab.test/api/_matrix/client/v1/media/download/local.test/'));
  assert.equal(downloads[0].options.headers.Authorization, 'Bearer matrix-token'); assert.ok(!downloads[0].url.includes('matrix-token'));
});

test('secure lost acknowledgements retry once using authenticated encrypted receipts', async () => {
  const { provider, control, uploads } = fixture(), options = opts(); control.failAfterSend = true;
  await assert.rejects(provider.upload('channel', file(), options), /lost acknowledgement/);
  const saved = await provider.upload('channel', file(), options);
  assert.equal((await provider.list('channel')).length, 1); assert.equal(uploads.length, 1); assert.equal(saved.version_number, 1);
  await assert.rejects(provider.upload('channel', new File(['changed'], 'private-name.txt', {type:'text/plain'}), options), /another file/);
});

test('secure versions preserve bytes and reject forged owner, foreign-domain impersonation and unauthorized deletion events', async () => {
  const { provider, events, event, control } = fixture();
  const first = await provider.upload('channel', file(), opts());
  const second = await provider.upload('channel', new File(['second bytes'], 'revision.txt', {type:'text/plain'}), { ...opts(), documentId: first.document_id });
  const initial = events[0].getContent();
  events.push(event({...initial, version_number:3, name:'forged.txt'}, '@fc_intruder:local.test'));
  events.push(event({...initial, version_number:3, name:'foreign.txt'}, '@fc_owner:evil.test'));
  events.push(event({document_id:first.document_id, owner_id:'intruder', deleted:true}, '@fc_intruder:local.test'));
  assert.equal((await provider.list('channel'))[0].filename, 'revision.txt'); assert.equal((await provider.versions(second)).length, 2);
  assert.equal(await (await provider.download(first)).text(), 'private attachment bytes');
  assert.equal(await (await provider.download(second)).text(), 'second bytes');
  control.owner = 'intruder';
  await assert.rejects(provider.upload('channel', file(), {...opts(), documentId:first.document_id}), /owner/);
  await assert.rejects(provider.delete({...second, document_owner_id:'intruder'}), /owner/);
});

test('secure deletion hides all versions, prevents old download and does not resurrect a retried request', async () => {
  const { provider } = fixture(), options = opts(); const first = await provider.upload('channel', file(), options);
  await provider.delete(first); assert.equal((await provider.list('channel')).length, 0);
  await assert.rejects(provider.download(first), /no longer available/); await assert.rejects(provider.upload('channel', file(), options), /no longer available/);
});

test('secure download rejects a ciphertext integrity failure', async () => {
  const { provider, media } = fixture(), first = await provider.upload('channel', file(), opts());
  const key = [...media.keys()][0], bytes = new Uint8Array(media.get(key)); bytes[0] ^= 255; media.set(key, bytes.buffer);
  await assert.rejects(provider.download(first), /hash|digest|mismatch|decrypt/i);
});

test('secure upload abort reaches the homeserver upload and sends no metadata acknowledgement', async () => {
  const { provider, control, uploads, events } = fixture(), controller = new AbortController();
  let release; const ready = new Promise(resolve => { release = resolve; });
  control.upload = (blob, options) => new Promise((resolve, reject) => { release(); options.abortController.signal.addEventListener('abort', () => reject(new DOMException('aborted','AbortError'))); });
  const pending = provider.upload('channel', file(), { ...opts(), signal: controller.signal }), rejected = assert.rejects(pending, { name:'AbortError' });
  await ready; controller.abort(); await rejected; assert.equal(uploads[0].options.abortController.signal.aborted, true); assert.equal(events.length, 0);
});

test('only an explicitly bound peer domain may supply remote encrypted files and cannot impersonate a local owner', async () => {
  const { provider, events, event } = fixture({ peerServer: 'peer.test' });
  await provider.upload('channel', file(), opts());
  const content = events[0].getContent();
  events.push(event({ ...content, document_id: 'remote-document', name: 'peer-file.txt' }, '@fc_owner:peer.test'));
  events.push(event({ ...content, document_id: 'evil-document', name: 'evil.txt' }, '@fc_owner:untrusted.test'));
  const records = await provider.list('channel');
  assert.equal(records.length, 2);
  const remote = records.find(row => row.document_id === 'remote-document');
  assert.equal(remote.document_owner_id, '@fc_owner:peer.test');
  assert.equal(remote.uploader_id, '@fc_owner:peer.test');
  assert.equal(await (await provider.download(remote)).text(), 'private attachment bytes');
  await assert.rejects(provider.delete(remote), /owner/);
  await assert.rejects(provider.upload('channel', file(), { ...opts(), documentId: remote.document_id }), /owner/);
});
