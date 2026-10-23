import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import { webcrypto, randomUUID } from 'node:crypto';
import ts from 'typescript';

const require = createRequire(import.meta.url), Y = require('yjs');
const plain = value => JSON.parse(JSON.stringify(value));

function fixture({ encrypted = true, cryptoChanged } = {}) {
  const state = { token: 'first-account', loads: 0, requests: [], events: [], records: [], tamper: null };
  const secure = {
    handles: async () => encrypted,
    sendEvent: async (channel, type, content) => {
      const event_id = '$entry-' + state.events.length;
      state.events.push({ channel, type, content: plain(content), event_id }); return { event_id };
    },
    roomFor: async () => ({ roomId: '!room:test', c: { getUserId: () => '@fc_actor:test' } }),
    getDecryptedEvent: async (_, identifier) => {
      const source = state.events.find(value => value.event_id === identifier);
      if (!source) throw new Error('Missing encrypted event');
      return { getRoomId: () => state.tamper?.room || '!room:test', getType: () => state.tamper?.type || source.type,
        getSender: () => state.tamper?.sender || '@fc_actor:test', isRedacted: () => !!state.tamper?.redacted,
        getContent: () => ({ ...source.content, ...state.tamper?.content }) };
    },
  };
  const api = { getAccessToken: () => state.token, request: async (path, options) => {
    state.requests.push({ path, options });
    if (!options) return state.records;
    const body = JSON.parse(options.body);
    return { id: body.id, channel_id: 'channel', created_by: 'actor', created_at: '2026-10-21T00:00:00',
      updated_at: '2026-10-21T00:00:00', archived: false, update_count: 1, version: 1,
      summary_event_id: body.matrix_event_id || null, summary_update_id: body.update_id, summary_by: 'actor' };
  } };
  const dependencies = { yjs: Y, './api': { api, APIError: class APIError extends Error {} }, './secure-client': {
    getSecureMessaging: async () => { state.loads++; cryptoChanged?.(state); return secure; },
  } };
  const source = readFileSync(new URL('../src/lib/shared-documents-api.ts', import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  const exports = {};
  vm.runInNewContext(compiled, { exports, require: name => dependencies[name], Date, Set, Map, Symbol, Uint8Array, ArrayBuffer,
    atob, btoa, crypto: { randomUUID, subtle: webcrypto.subtle }, setTimeout, clearTimeout });
  return { ...exports, state, secure };
}

test('secure document creation sends only an authenticated opaque reference to the native API', async () => {
  const f = fixture(); await f.sharedDocumentsApi.create('channel', 'A private shared page');
  assert.equal(f.state.events.length, 1);
  assert.equal(f.state.events[0].type, 'eu.freecollab.shared_document.update');
  assert.equal(f.state.events[0].content.fields.title, 'A private shared page');
  const body = JSON.parse(f.state.requests[0].options.body);
  assert.deepEqual(Object.keys(body).sort(), ['id', 'matrix_event_id', 'update_id']);
  assert.equal(body.matrix_event_id, '$entry-0');
  assert.ok(!f.state.requests[0].options.body.includes('private'));
});

test('explicit native document creation commits the reviewed title and body in one initial Yjs update', async () => {
  const f = fixture({ encrypted: false });
  await f.sharedDocumentsApi.create('channel', '  Courtyard brief  ', 'Reviewed message\n\nA real working draft.');
  assert.equal(f.state.requests.length, 1); assert.equal(f.state.events.length, 0);
  const body = JSON.parse(f.state.requests[0].options.body), doc = new Y.Doc();
  try {
    Y.applyUpdate(doc, Uint8Array.from(atob(body.update_base64), character => character.charCodeAt(0)));
    assert.equal(doc.getText('title').toString(), 'Courtyard brief');
    assert.equal(doc.getText('body').toString(), 'Reviewed message\n\nA real working draft.');
  } finally { doc.destroy(); }
});

test('explicit encrypted document draft stays in one verified encrypted event and an opaque native reference', async () => {
  const f = fixture(); await f.sharedDocumentsApi.create('channel', 'Private brief', 'Reviewed private body');
  assert.equal(f.state.events.length, 1); assert.equal(f.state.requests.length, 1);
  const doc = new Y.Doc();
  try {
    Y.applyUpdate(doc, Uint8Array.from(atob(f.state.events[0].content.fields.update_base64), character => character.charCodeAt(0)));
    assert.equal(doc.getText('title').toString(), 'Private brief');
    assert.equal(doc.getText('body').toString(), 'Reviewed private body');
    assert.deepEqual(Object.keys(JSON.parse(f.state.requests[0].options.body)).sort(), ['id', 'matrix_event_id', 'update_id']);
    assert.ok(!f.state.requests[0].options.body.includes('private'));
  } finally { doc.destroy(); }
});

test('new document body is bounded before any native write or encrypted event', async () => {
  for (const encrypted of [false, true]) {
    const f = fixture({ encrypted });
    await assert.rejects(f.sharedDocumentsApi.create('channel', 'Brief', 'x'.repeat(50001)), /limit|long|50,000|50000/i);
    assert.equal(f.state.requests.length, 0); assert.equal(f.state.events.length, 0);
  }
});

test('existing two-argument document creation retains an empty initial body', async () => {
  const f = fixture({ encrypted: false }); await f.sharedDocumentsApi.create('channel', 'Existing workflow');
  const body = JSON.parse(f.state.requests[0].options.body), doc = new Y.Doc();
  try {
    Y.applyUpdate(doc, Uint8Array.from(atob(body.update_base64), character => character.charCodeAt(0)));
    assert.equal(doc.getText('body').toString(), '');
  } finally { doc.destroy(); }
});

test('changing account during the second crypto acquisition prevents encrypted content reaching the replacement identity', async () => {
  const f = fixture({ cryptoChanged: state => { if (state.loads === 2) state.token = 'replacement-account'; } });
  await assert.rejects(f.sharedDocumentsApi.create('channel', 'Do not transfer this private draft'), /account changed/);
  assert.equal(f.state.events.length, 0); assert.equal(f.state.requests.length, 0);
});

test('a binding lookup failure never falls back to a native plaintext update', async () => {
  const f = fixture(); f.secure.handles = async () => { throw new Error('Binding unavailable'); };
  await assert.rejects(f.sharedDocumentsApi.create('channel', 'Private draft'), /Binding unavailable/);
  assert.equal(f.state.events.length, 0); assert.equal(f.state.requests.length, 0);
});

test('encrypted comment verification rejects foreign room, sender, type, ID, document and redaction', async () => {
  for (const tamper of [{ room: '!foreign:test' }, { sender: '@fc_other:test' }, { type: 'eu.freecollab.shared_document.update' },
    { content: { id: 'other' } }, { content: { document_id: 'other' } }, { redacted: true }]) {
    const f = fixture();
    f.state.events.push({ event_id: '$comment', type: 'eu.freecollab.shared_document.comment', content: { id: 'comment', document_id: 'doc', fields: { text: 'Private comment' } } });
    f.state.records = [{ id: 'comment', document_id: 'doc', channel_id: 'channel', created_by: 'actor', content_mode: 'matrix', matrix_event_id: '$comment', resolved: false, version: 1 }];
    f.state.tamper = tamper;
    await assert.rejects(f.sharedDocumentsApi.comments('channel', 'doc'), /verified/);
  }
});

test('canonical bounded update decoding rejects malformed, tiny and oversized payloads', () => {
  const f = fixture();
  assert.deepEqual([...f.decodeDocumentUpdate('AAA=')], [0, 0]);
  for (const value of ['', 'AA==', '!!!!', 'AAA=\n', btoa('x'.repeat(256 * 1024 + 1))]) assert.throws(() => f.decodeDocumentUpdate(value));
});
