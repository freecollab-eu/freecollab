import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { webcrypto } from 'node:crypto';
import ts from 'typescript';
import { fieldReferences } from './load-field-references.mjs';
const plain = value => JSON.parse(JSON.stringify(value));
const reference = (id = 'actual-resource') => ({ kind: 'document', id, start: 0, end: 5 });
const decisionFields = () => ({ title: '  Decision  ', content: '@Plan says hello', status: 'proposed', responsible_user_id: null, decision_date: null, source_message_id: null, field_references: { content: [reference()] } });
function fixture(name, encrypted = false) {
  const state = { token: 'session', events: [], requests: [], resolutions: [], available: true, rotate: false };
  const secure = {
    handles: async () => encrypted,
    sendEvent: async (channel, type, content) => { const event_id = '$' + state.events.length; state.events.push({ event_id, channel, type, content: plain(content) }); return { event_id }; },
    roomFor: async () => ({ roomId: '!room:test', c: { getUserId: () => '@fc_actor:test' } }),
    getDecryptedEvent: async (_, id) => { const event = state.events.find(event => event.event_id === id); if(state.rotateOnRead)state.token='replacement'; return { getRoomId: () => state.tamperRoom || '!room:test', getType: () => event.type, getContent: () => event.content, getSender: () => '@fc_actor:test', isRedacted: () => false }; },
  };
  const api = { getAccessToken: () => state.token, getCurrentUserId: () => 'actor', request: async (path, options) => {
    if (!options?.method) { state.requests.push({ path }); return state.metadata; }
    const body = JSON.parse(options.body), event = state.events.find(event => event.event_id === body.matrix_event_id); state.requests.push({ path, body });
    const decision = name === 'collaboration-api', kind = decision ? undefined : path.endsWith('/comments') ? 'comment' : 'post';
    return { id: body.id || 'decision', channel_id: 'channel', created_by: 'actor', created_at: '2026-10-24T00:00:00Z', revised_by: 'actor', revised_at: '2026-10-24T00:00:00Z', revision: (body.expected_revision || 0) + 1, revision_id: 'revision', content_mode: event ? 'matrix' : 'server', matrix_event_id: event?.event_id || null,
      ...(decision ? event ? {} : body : { kind, post_id: kind === 'post' ? body.id : 'post', archived: body.archived, poll_id: null, poll: null, ...(event ? {} : { fields: body.fields }) }) };
  } };
  const dependencies = { './api': { api }, './field-references': fieldReferences, './secure-client': { getSecureMessaging: async () => secure }, './message-references-api': { messageReferencesApi: { resolve: async (_, refs) => { state.resolutions.push(plain(refs)); if (state.rotate) state.token = 'different-session'; return refs.map(ref => ({ ...ref, available: state.available })); } } } };
  const source = readFileSync(new URL(`../src/lib/${name}.ts`, import.meta.url), 'utf8'), exports = {};
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText, { exports, require: name => dependencies[name], window: { addEventListener() {} }, Date, Set, Map, crypto: { randomUUID: () => '12345678-1234-1234-1234-123456789012', subtle: webcrypto.subtle }, TextEncoder, Uint8Array, URLSearchParams });
  return { ...exports, state };
}

for (const encrypted of [false, true]) {
  test(`feed post and comment preserve explicit reference IDs in ${encrypted ? 'encrypted' : 'native'} revisions`, async () => {
    const f = fixture('activity-feed-api', encrypted), body = '@Plan says hello';
    const post = await f.activityFeedApi.savePost('channel', { title: 'Update', body, poll: null, field_references: { body: [reference()] } });
    const comment = await f.activityFeedApi.saveComment('channel', 'post', { body, field_references: { body: [reference()] } });
    for (const result of [post, comment]) { assert.equal(result.fields.body, body); assert.deepEqual(plain(result.fields.field_references), { body: [reference()] }); }
    assert.equal(f.state.resolutions.length, 2);
    for (const request of f.state.requests) {
      if (encrypted) { assert.equal(request.body.fields, undefined); assert.equal(request.body.field_references, undefined); assert.ok(request.body.matrix_event_id); }
      else assert.deepEqual(request.body.fields.field_references, { body: [reference()] });
    }
  });
  test(`decision reference payload remains ${encrypted ? 'encrypted' : 'native'} and exact`, async () => {
    const f = fixture('collaboration-api', encrypted), fields = decisionFields(), saved = await f.collaborationApi.createDecision('channel', fields);
    assert.equal(saved.title, fields.title); assert.equal(saved.content, fields.content); assert.deepEqual(plain(saved.field_references), fields.field_references);
    if (encrypted) assert.deepEqual(Object.keys(f.state.requests[0].body), ['matrix_event_id']);
    else assert.deepEqual(f.state.requests[0].body.field_references, fields.field_references);
  });
}

test('unavailable new references or account rotation cannot publish a feed or decision', async () => {
  for (const name of ['activity-feed-api', 'collaboration-api']) for (const rotate of [false, true]) {
    const f = fixture(name, true); f.state.available = false; f.state.rotate = rotate;
    const saving = name === 'activity-feed-api' ? f.activityFeedApi.savePost('channel', { title: 'Update', body: '@Plan', poll: null, field_references: { body: [reference()] } }) : f.collaborationApi.createDecision('channel', decisionFields());
    await assert.rejects(saving); assert.equal(f.state.events.length, 0); assert.equal(f.state.requests.length, 0);
  }
});

test('historical feed/comment/decision references do not block archive or status changes', async () => {
  const feed = fixture('activity-feed-api'), fields = { title: 'Update', body: '@Plan', poll: null, field_references: { body: [reference()] } };
  const post = await feed.activityFeedApi.savePost('channel', fields), comment = await feed.activityFeedApi.saveComment('channel', 'post', { body: '@Plan', field_references: fields.field_references }); feed.state.available = false;
  await feed.activityFeedApi.savePost('channel', fields, post, { archived: true });
  await feed.activityFeedApi.saveComment('channel', 'post', comment.fields, comment, true);
  assert.equal(feed.state.resolutions.length, 2);
  const decisions = fixture('collaboration-api'), decision = await decisions.collaborationApi.createDecision('channel', decisionFields()); decisions.state.available = false;
  await decisions.collaborationApi.reviseDecision(decision, { ...decisionFields(), status: 'accepted' });
  assert.equal(decisions.state.resolutions.length, 1);
  await assert.rejects(decisions.collaborationApi.reviseDecision(decision, { ...decisionFields(), content: '@Plan changed' }), /no longer available/);
});

test('legacy encrypted return decisions use only verified event fields and reject plaintext and wrong-room metadata', async () => {
  const f = fixture('collaboration-api', true), saved = await f.collaborationApi.createDecision('channel', decisionFields());
  f.state.metadata = { channel_id: 'channel', incomplete: false, items: [{ ...saved, title: 'Forged server title', content: 'Forged server content' },
    { ...saved, id: 'native', content_mode: 'server', title: 'Never fall back' }] };
  const sends = f.state.events.length;
  const result = await f.collaborationApi.encryptedDecisionSummaries('channel', '2026-10-20T00:00:00');
  assert.equal(result.items.length, 1); assert.equal(result.items[0].title, decisionFields().title); assert.equal(result.incomplete, true);
  assert.equal(f.state.events.length, sends);
  assert.match(f.state.requests.at(-1).path, /return-summary\/encrypted-decisions\?since=/);
  f.state.tamperRoom = '!wrong:test';
  const locked = await f.collaborationApi.encryptedDecisionSummaries('channel', '2026-10-20T00:00:00');
  assert.equal(locked.items.length, 0); assert.equal(locked.incomplete, true);
  f.state.tamperRoom = null; f.state.rotateOnRead = true;
  await assert.rejects(f.collaborationApi.encryptedDecisionSummaries('channel', '2026-10-20T00:00:00'), /account changed/);
});

test('poll bindings are limited to actual options; non-writing activity payloads reject sidecars', async () => {
  const f = fixture('activity-feed-api'), id = 'a'.repeat(32), poll = { id, nonce: 'b'.repeat(32), question: '@Plan', options: [{ id: 'c'.repeat(32), label: 'One' }, { id: 'd'.repeat(32), label: 'Two' }], multiple: false, closes_at: null };
  await assert.rejects(f.activityFeedApi.savePost('channel', { title: 'Update', body: 'Text', poll, field_references: { 'poll.option:phantom': [reference()] } }));
  assert.equal(f.state.requests.length, 0);
  const saved = await f.activityFeedApi.savePost('channel', { title: 'Update', body: 'Text', poll, field_references: { 'poll.question': [reference()] } });
  // This fixture has no poll routing metadata; the real read boundary deliberately marks it unverified.
  assert.match(saved.resolution_error, /verified/);
  assert.deepEqual(f.state.requests[0].body.fields.field_references, { 'poll.question': [reference()] });
});
