import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
const fields = { provider: 'box', title: 'Chosen private design', url: 'https://app.box.com/file/20', external_access_acknowledged: true };
function fixture({ encrypted = true, trusted = true } = {}) {
  const state = { token: 'one', events: [], requests: [], tamper: null };
  const secure = {
    handles: async () => encrypted,
    roomFor: async () => ({ roomId: '!channel:test', c: { getUserId: () => '@fc_actor:test', getDeviceId: () => 'device', getCrypto: () => ({ getDeviceVerificationStatus: async () => { state.beforeTrust?.(); return { isVerified: () => trusted }; } }) } }),
    sendEvent: async (channel, type, content) => { const event_id = '$' + state.events.length; state.events.push({ channel, type, content, event_id }); return { event_id }; },
    getDecryptedEvent: async (_channel, id) => {
      const source = state.events.find(row => row.event_id === id);
      return { getRoomId: () => state.tamper?.room || '!channel:test', getType: () => state.tamper?.type || source.type,
        getSender: () => state.tamper?.sender || '@fc_actor:test', isRedacted: () => state.tamper?.redacted || false,
        getContent: () => ({ ...source.content, ...state.tamper?.content }) };
    },
  };
  const api = { getAccessToken: () => state.token, request: async (path, init) => {
    state.requests.push({ path, init }); const data = JSON.parse(init.body);
    return { id: data.id, workspace_id: 'workspace', channel_id: 'channel', created_by: 'actor', content_mode: data.matrix_event_id ? 'matrix' : 'server',
      matrix_event_id: data.matrix_event_id || null, ...(!data.matrix_event_id ? { fields: data.fields } : {}) };
  } };
  const compiled = ts.transpileModule(readFileSync(new URL('../src/lib/cloud-files-api.ts', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  const exports = {};
  vm.runInNewContext(compiled, { exports, require: name => ({ './api': { api }, './secure-client': { getSecureMessaging: async () => secure }, './enterprise-api': { syncApi: {} } })[name], URL, Set, crypto: { randomUUID: () => 'selected' } });
  return { ...exports, state, secure, api };
}

test('encrypted provider links send only an opaque reference to native API', async () => {
  const f = fixture(), result = await f.cloudFilesApi.addLink('workspace', 'channel', fields);
  assert.equal(result.fields.title, fields.title);
  assert.deepEqual(JSON.parse(f.state.requests[0].init.body), { id: 'selected', matrix_event_id: '$0' });
  assert.ok(!f.state.requests[0].init.body.includes(fields.url));
  assert.equal(f.state.events[0].type, 'eu.freecollab.cloud-file');
});

test('a verified own device is required before an encrypted provider link leaves the client', async () => {
  const f = fixture({ trusted: false });
  await assert.rejects(f.cloudFilesApi.addLink('workspace', 'channel', fields), /Verify this device/);
  assert.equal(f.state.events.length, 0); assert.equal(f.state.requests.length, 0);
});

test('encrypted link resolution rejects substituted channel, sender, revision, ID, workspace and type', async () => {
  for (const tamper of [{ room: '!foreign:test' }, { sender: '@fc_foreign:test' }, { type: 'm.room.message' }, { redacted: true },
    { content: { channel_id: 'other' } }, { content: { workspace_id: 'other' } }, { content: { id: 'other' } }, { content: { revision: 2 } }]) {
    const f = fixture(); f.state.tamper = tamper;
    const result = await f.cloudFilesApi.addLink('workspace', 'channel', fields);
    assert.equal(result.fields, undefined); assert.match(result.resolution_error, /verified/);
  }
});

test('missing secure binding never falls back to a plaintext link', async () => {
  const f = fixture(); f.secure.handles = async () => { throw new Error('Binding unavailable'); };
  await assert.rejects(f.cloudFilesApi.addLink('workspace', 'channel', fields), /Binding unavailable/);
  assert.equal(f.state.events.length, 0); assert.equal(f.state.requests.length, 0);
});

test('auth changes while awaiting device checks prevent old-account link writes', async () => {
  const f = fixture(); f.secure.handles = async () => { f.state.token = 'two'; return true; };
  await assert.rejects(f.cloudFilesApi.addLink('workspace', 'channel', fields), /account changed/);
  assert.equal(f.state.events.length, 0); assert.equal(f.state.requests.length, 0);
});

test('provider links reject lookalike hosts, unacknowledged access and credentials', () => {
  const f = fixture();
  for (const bad of [{ ...fields, url: 'https://app.box.com.evil.example/file/20' }, { ...fields, external_access_acknowledged: false },
    { ...fields, url: 'https://app.box.com/file/20?access_token=private' }, { ...fields, url: 'javascript:alert(1)' }, { ...fields, unexpected: 'hidden' }]) assert.throws(() => f.validateCloudLink(bad));
});


test('logout during the awaited own-device trust lookup prevents sending encrypted old-account content', async () => {
  const f = fixture(); f.state.beforeTrust = () => { f.state.token = 'two'; };
  await assert.rejects(f.cloudFilesApi.addLink('workspace', 'channel', fields), /account changed/);
  assert.equal(f.state.events.length, 0); assert.equal(f.state.requests.length, 0);
});


test('browse sends the displayed consent revision and rejects substituted response grants', async () => {
  const f=fixture({encrypted:false}), connection={id:'cloud:chosen',revision:4};
  f.api.request=async(path,init)=>{ f.state.requests.push({path,init}); return {connection_id:'cloud:chosen',connection_revision:4,items:[]}; };
  await f.cloudFilesApi.browse('workspace','channel',connection);
  assert.equal(JSON.parse(f.state.requests[0].init.body).connection_revision,4);
  f.api.request=async()=>({connection_id:'cloud:chosen',connection_revision:5,items:[]});
  await assert.rejects(f.cloudFilesApi.browse('workspace','channel',connection),/connection changed/);
});

test('import remains pinned to the selected entry grant even if another connection has identical file revision', async () => {
  const f=fixture({encrypted:false});
  const entry={id:'same-id',parent_id:'same-parent',etag:'same-etag',connection_id:'cloud:chosen',connection_revision:4};
  await f.cloudFilesApi.importFile('workspace','channel','cloud:chosen',entry,'explicit-selection');
  assert.equal(JSON.parse(f.state.requests[0].init.body).connection_revision,4);
  assert.throws(()=>f.cloudFilesApi.importFile('workspace','channel','cloud:other',entry,'different-grant'),/current cloud connection/);
  assert.equal(f.state.requests.length,1);
});
