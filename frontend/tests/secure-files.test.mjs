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
function fixture({ peerServer, recordingVerifier } = {}) {
  const exports = {}, events = [], media = new Map(), transactions = new Map(), uploads = [], downloads = [], publications = [];
  const control = { owner: 'owner', generation: 0, failAfterSend: false, upload: null, pause: null, indexReads: 0, digestCalls: 0 };
  const pause = async stage => { await control.pause?.(stage); };
  function event(content, sender = '@fc_owner:local.test', id = '$' + crypto.randomUUID()) {
    return { getContent: () => content, getSender: () => sender, getId: () => id, getRoomId: () => '!room:local.test',
      getTs: () => Date.now(), getType: () => 'eu.freecollab.file', isEncrypted: () => true, isRedacted: () => false };
  }
  const c = { getUserId: () => `@fc_${control.owner}:local.test`, getHomeserverUrl: () => 'https://freecollab.test/api', getAccessToken: () => 'matrix-token',
    scrollback: async room => { await pause('scrollback'); room.oldState.paginationToken = undefined; },
    uploadContent: async (blob, options) => {
      uploads.push({ blob, options });
      await pause('ciphertext-upload');
      if (control.upload) return control.upload(blob, options);
      const uri = 'mxc://local.test/' + crypto.randomUUID(); media.set(uri.split('/').at(-1), await blob.arrayBuffer()); return { content_uri: uri };
    } };
  const secureMessaging = { handles: async () => true, roomFor: async channel => {
      const owner = control.owner, generation = control.generation;
      return { c, room: { oldState: control.paginate ? { paginationToken: 'older' } : {} }, roomId: channel === 'channel' ? '!room:local.test' : '!other-room:local.test', federatedPeerServer: peerServer,
        assertCurrent: () => { if (owner !== control.owner || generation !== control.generation) throw new Error('The signed-in encryption session changed.'); } };
    },
    events: async () => { await pause(++control.indexReads === 1 ? 'file-index' : 'confirmation-index'); return events; },
    sendEvent: async (channel, type, content, transactionId, options) => {
      control.publication = {transactionId,options};
      await pause('sdk-publication');
      await options?.authorize?.();
      publications.push({ owner: control.owner, content });
      if (transactionId && transactions.has(transactionId)) return { event_id: transactions.get(transactionId) };
      const item = event(content, c.getUserId()); events.push(item);
      if (transactionId) transactions.set(transactionId, item.getId());
      if (control.failAfterSend) { control.failAfterSend = false; throw new Error('lost acknowledgement'); }
      return { event_id: item.getId() };
    },
    getDecryptedEvent: async (channel, id) => { await pause('receipt'); const item = events.find(e => e.getId() === id); if (!item) throw new Error('missing event'); return item; },
  };
  vm.runInNewContext(compiled, { exports, require: name => name === 'matrix-encrypt-attachment' ? {
      encryptAttachment: async bytes => { const value = await encryptAttachment(bytes); await pause('encryption'); return value; },
      decryptAttachment: async (...args) => { const value = await decryptAttachment(...args); await pause('decryption'); return value; },
    } : name === '../file-preview' ? previewHelpers : name === '../meeting-capture-proof' ? {verifyEncryptedRecordingFile:recordingVerifier || (async()=>{throw Error('unverified recording');})} : { secureMessaging },
    crypto: { randomUUID: () => crypto.randomUUID(), subtle: { digest: async (...args) => { const value = await crypto.subtle.digest(...args); await pause(++control.digestCalls === 1 ? 'content-digest' : 'fingerprint-digest'); return value; } } },
    TextEncoder, Uint8Array, Blob, File, DOMException, AbortController,
    fetch: async (url, options) => { downloads.push({ url, options }); await pause('download-response'); const bytes = media.get(decodeURIComponent(url.split('/').at(-1))) || new Uint8Array([1]).buffer;
      return { ok: true, headers: new Headers(), body: null, arrayBuffer: async () => { await pause('download-body'); return bytes; } }; } });
  return { provider: exports.secureFileProvider, verifyReferences: exports.verifiedSecureFileReferenceIds, getReference: exports.getVerifiedSecureFileReference, events, media, uploads, downloads, publications, event, control };
}
const file = () => new File(['private attachment bytes'], 'private-name.txt', { type: 'text/plain' });
const opts = () => ({ requestId: crypto.randomUUID() });
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
function pauseAt(fixture, boundary) {
  const entered = deferred(), release = deferred(); let paused = false;
  fixture.control.pause = async stage => { if (stage === boundary && !paused) { paused = true; entered.resolve(); await release.promise; } };
  return { entered: entered.promise, release: release.resolve };
}
function invalidate(fixture, lifecycle) {
  if (lifecycle === 'account change') fixture.control.owner = 'replacement';
  fixture.control.generation++;
}

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

test('file references require actual verified room events and do not survive deletion', async () => {
  const { provider, verifyReferences, events, event } = fixture();
  const first = await provider.upload('channel', file(), opts());
  const second = await provider.upload('channel', new File(['new'], 'revision.txt', { type: 'text/plain' }), { ...opts(), documentId: first.document_id });
  events.push(event({ ...events[0].getContent(), name: 'forged.txt' }, '@fc_intruder:local.test', '$forged'));
  const available = await verifyReferences('channel', [first.id, second.id, '$forged', '$missing']);
  assert.deepEqual([...available].sort(), [first.id, second.id].sort());
  await provider.delete(second);
  assert.equal((await verifyReferences('channel', [first.id, second.id])).size, 0);
});

test('exact secure file reference lookup opens historical verified versions only in their genuine channel', async () => {
  const { provider, getReference, events, event } = fixture();
  const first = await provider.upload('channel', file(), opts());
  const second = await provider.upload('channel', new File(['revision'], 'revision.txt', { type: 'text/plain' }), { ...opts(), documentId: first.document_id });
  assert.equal((await provider.list('channel'))[0].id, second.id);
  const historical = await getReference('channel', first.id);
  assert.equal(historical.id, first.id); assert.equal(historical.filename, 'private-name.txt');
  assert.equal(historical.version_number, 1); assert.equal(historical.version_count, 2);
  assert.equal((await getReference('channel', second.id)).id, second.id);
  assert.equal(await getReference('another-channel', first.id), undefined);
  events.push(event({ ...events[0].getContent(), version_number: 3, name: 'forged.txt' }, '@fc_intruder:local.test', '$forged'));
  assert.equal(await getReference('channel', '$forged'), undefined);
  assert.equal(await getReference('channel', '$missing'), undefined);
  await provider.delete(second);
  assert.equal(await getReference('channel', first.id), undefined);
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


test('capture key publication requires current foreground review, reserved transaction and encrypted proof',async()=>{
 let reviews=0;const f=fixture({recordingVerifier:async()=>{}}),proof={protocol:'test-fixture-proof',generation:1},authorize=async()=>{reviews++;},controller=new AbortController();
 await assert.rejects(f.provider.upload('channel',file(),{requestId:'fc-capture-file-one'}),/consent proof/);
 await assert.rejects(f.provider.upload('channel',file(),{requestId:'plain',meetingCaptureProof:proof,beforePublish:authorize}),/consent proof/);
 assert.equal(f.uploads.length,0);
 const row=await f.provider.upload('channel',file(),{requestId:'fc-capture-file-one',meetingCaptureProof:proof,beforePublish:authorize,signal:controller.signal});
 assert.equal(f.control.publication.transactionId,'fc-capture-file-one');assert.equal(typeof f.control.publication.options.authorize,'function');assert.equal(reviews,1);assert.equal(f.control.publication.options.signal,controller.signal);
 assert.deepEqual(JSON.parse(JSON.stringify(f.events[0].getContent().meeting_capture)),proof);assert.equal(row.filename,'private-name.txt');
});
test('recording indexes and downloads quarantine missing or rejected proof without exposing the file key',async()=>{
 const f=fixture(),template={document_id:'owner:recording',owner_id:'owner',version_number:1,request_id:'fc-capture-file-one',fingerprint:'a',name:'recording.webm',content_type:'video/webm',size:10,file:{url:'mxc://local.test/cipher',hashes:{sha256:'hash'},iv:'iv',key:{k:'secret',kty:'oct'},v:'v2'}};
 f.events.push(f.event(template));assert.equal((await f.provider.list('channel')).length,0);
 f.events[0]=f.event({...template,meeting_capture:{protocol:'forged'}});assert.equal((await f.provider.list('channel')).length,0);
 await assert.rejects(f.provider.download({id:f.events[0].getId(),channel_id:'channel'}),/no longer available/);assert.equal(f.downloads.length,0);
});

test('a published recording with rejected consent stays quarantined and reports its actual verification failure',async()=>{
 const rejection=new Error('The recorded participant withdrew consent.'),f=fixture({recordingVerifier:async()=>{throw rejection;}});
 await assert.rejects(f.provider.upload('channel',file(),{requestId:'fc-capture-file-rejected',meetingCaptureProof:{protocol:'test-fixture-proof'},beforePublish:async()=>{}}),error=>error===rejection);
 assert.equal(f.events.length,1);assert.equal((await f.provider.list('channel')).length,0);
 assert.equal(f.downloads.length,0);
});

for (const lifecycle of ['account change', 'encryption reset']) {
  for (const boundary of ['file-read', 'content-digest', 'fingerprint-digest', 'scrollback', 'file-index', 'encryption', 'ciphertext-upload', 'sdk-publication', 'publication-review']) {
    test(`secure upload publishes no key after ${lifecycle} during ${boundary}`, { timeout: 2000 }, async () => {
      const f = fixture(), input = file(), gate = pauseAt(f, boundary), progress = [];
      const read = input.arrayBuffer.bind(input);
      input.arrayBuffer = async () => { const bytes = await read(); await f.control.pause?.('file-read'); return bytes; };
      if (boundary === 'scrollback') f.control.paginate = true;
      const pending = f.provider.upload('channel', input, { ...opts(), onProgress: value => progress.push(value),
        beforePublish: async () => { await f.control.pause?.('publication-review'); } });
      const rejected = assert.rejects(pending, /encryption session changed/);
      await gate.entered; invalidate(f, lifecycle); gate.release(); await rejected;
      assert.deepEqual(f.publications, [], 'file metadata and its decryption key must stay unpublished');
      assert.equal(progress.includes(100), false);
    });
  }

  for (const boundary of ['receipt', 'confirmation-index']) test(`secure upload rejects stale confirmation after ${lifecycle} during ${boundary}`, async () => {
    const f = fixture(), gate = pauseAt(f, boundary), progress = [];
    const pending = f.provider.upload('channel', file(), { ...opts(), onProgress: value => progress.push(value) });
    const rejected = assert.rejects(pending, /encryption session changed/);
    await gate.entered; invalidate(f, lifecycle); gate.release(); await rejected;
    assert.equal(f.publications.length, 1); assert.equal(f.publications[0].owner, 'owner');
    assert.equal(progress.includes(100), false);
  });

  for (const boundary of ['file-index', 'sdk-publication']) test(`secure deletion cannot publish under a new session after ${lifecycle} during ${boundary}`, async () => {
    const f = fixture(), stored = await f.provider.upload('channel', file(), opts());
    f.publications.length = 0; f.control.indexReads = 0;
    const gate = pauseAt(f, boundary), pending = f.provider.delete(stored), rejected = assert.rejects(pending, /encryption session changed/);
    await gate.entered; invalidate(f, lifecycle); gate.release(); await rejected;
    assert.deepEqual(f.publications, []);
  });

  for (const boundary of ['file-index', 'receipt', 'download-response', 'download-body', 'decryption']) test(`secure download discards private bytes after ${lifecycle} during ${boundary}`, async () => {
    const f = fixture(), stored = await f.provider.upload('channel', file(), opts());
    f.control.indexReads = 0;
    const gate = pauseAt(f, boundary), pending = f.provider.download(stored), rejected = assert.rejects(pending, /encryption session changed/);
    await gate.entered; invalidate(f, lifecycle); gate.release(); await rejected;
  });
}

test('a delayed publication review preserves a current owner and publishes the exact decryptable file once', async () => {
  const f = fixture(), gate = pauseAt(f, 'publication-review'); let reviews = 0;
  const pending = f.provider.upload('channel', file(), { ...opts(), beforePublish: async () => { reviews++; await f.control.pause?.('publication-review'); } });
  await gate.entered; assert.deepEqual(f.publications, []); gate.release();
  const stored = await pending;
  assert.equal(reviews, 1); assert.equal(f.publications.length, 1); assert.equal(f.publications[0].owner, 'owner');
  assert.equal(await (await f.provider.download(stored)).text(), 'private attachment bytes');
});
