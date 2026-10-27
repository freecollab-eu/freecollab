import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/lib/secure/index.ts', import.meta.url), 'utf8');
const parsed = ts.createSourceFile('secure.ts', source, ts.ScriptTarget.Latest, true);
const functions = ['asMessage', 'currentRoomEvents', 'getRecentMessages'].map(name => {
  const declaration = parsed.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === name);
  assert.ok(declaration, name); return declaration.getText(parsed);
}).join('\n');
function event(id, extra = {}) {
  const value = { id, room: '!room:test', sender: '@fc_peer:test', type: 'm.room.message', encrypted: true,
    redacted: false, locked: false, body: 'A current message', timestamp: 1793188800000, relation: undefined, mentions: [], ...extra };
  return { value, getId: () => value.id, getRoomId: () => value.room, getSender: () => value.sender, getType: () => value.type,
    getTs: () => value.timestamp, isEncrypted: () => value.encrypted, isRedacted: () => value.redacted, isDecryptionFailure: () => value.locked,
    getContent: () => ({ body: value.body, 'eu.freecollab.mentions': value.mentions }), getOriginalContent: () => ({ 'm.relates_to': value.relation }) };
}
function fixture(rows, { cold = false } = {}) {
  const state = { token: 'session', actor: 'viewer', ownerToken: cold ? null : 'session', current: true, permitted: true, refreshes: 0, decrypted: [], snapshots: [], scrolls: 0, hook: async () => {} };
  const client = { createMessagesRequest: async (...args) => { state.snapshots.push(args); return { chunk: [...rows].reverse().slice(0, args[2]).map(event => ({ room_id: event.getRoomId(), event })) }; },
    decryptEventIfNeeded: async row => { state.decrypted.push(row.getId()); await state.hook('decrypt'); }, scrollback: async () => { state.scrolls++; throw Error('Summary must never scroll back'); } };
  const context = { exports: {}, configuration: { getAccessToken: () => state.token, getUserId: () => state.actor }, generation: 1,
    client: cold ? null : client, EventType: { RoomMessage: 'm.room.message', RoomCreate: 'm.room.create' }, Date, Map, Array, Promise, Number,
    MatrixEvent: class { constructor(raw) { return raw.event; } }, Direction: { Backward: 'b' },
    accountId: id => id === '@fc_viewer:test' ? 'viewer' : id,
    encryptedContentReferences: () => [],
    refreshBindings: async () => { state.refreshes++; await state.hook('refresh:'+state.refreshes); },
    ensureSession: async () => {
      if (state.ownerToken !== state.token) { context.generation++; state.ownerToken = state.token; }
      await state.hook('bootstrap');
      if (!context.client) context.client = client;
      return client;
    },
    roomFor: async () => ({ c: await context.ensureSession(), roomId: '!room:test', room: { oldState: { paginationToken: 'more-history' }, getLiveTimeline: () => ({ getEvents: () => rows }) },
      assertCurrent: () => { if (!state.permitted) throw Error('Current channel access removed'); } }),
  };
  vm.runInNewContext(ts.transpileModule(functions+'\nexports.read=getRecentMessages;', { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, context);
  return { state, context, read: options => context.exports.read('room', options) };
}

test('summary inspects at most 200 current events in a work-heavy room and never paginates for message quota', async () => {
  const rows = Array.from({ length: 12000 }, (_, index) => event('$work-'+index, { type: 'eu.freecollab.encrypted_work.revision' }));
  rows.push(event('$parent', { sender: '@fc_viewer:test' }), event('$reply', { relation: { rel_type: 'm.thread', event_id: '$parent' }, mentions: ['viewer'] }));
  const f = fixture(rows), result = await f.read({ limit: 100, eventLimit: 200, isCurrent: () => f.state.current });
  assert.equal(f.state.decrypted.length, 200); assert.equal(f.state.scrolls, 0); assert.equal(result.length, 2);
  assert.deepEqual(f.state.snapshots, [['!room:test', null, 200, 'b']]);
  assert.equal(result[0].parent_id, '$parent'); assert.equal(result[0].mentions[0], 'viewer');
  assert.equal(f.state.decrypted.includes('$work-0'), false);
});
test('recent inspection accepts a cold first encryption bootstrap without widening its bounds', async () => {
  const fixture = fixtureForCold();
  const result = await fixture.read({ eventLimit: 1, limit: 1 });
  assert.equal(result.length, 1); assert.equal(result[0].id, '$message');
  assert.equal(fixture.context.generation, 2); assert.deepEqual(fixture.state.decrypted, ['$message']); assert.equal(fixture.state.scrolls, 0);
});
function fixtureForCold() { return fixture([event('$message')], { cold: true }); }
test('recent cold bootstrap rejects changed owner/client and caller cancellation before decrypting', async () => {
  for (const change of ['token', 'actor', 'client', 'scope', 'abort']) {
    const f = fixtureForCold(), abort = new AbortController();
    f.state.hook = async step => {
      if (step !== 'bootstrap') return;
      if (change === 'client') f.context.client = {};
      else if (change === 'scope') f.state.current = false;
      else if (change === 'abort') abort.abort();
      else f.state[change] = 'replacement';
    };
    await assert.rejects(f.read({ signal: abort.signal, isCurrent: () => f.state.current }));
    assert.equal(f.state.decrypted.length, 0); assert.equal(f.state.scrolls, 0);
  }
});
test('recent encrypted projection excludes plaintext events and uses only same-sender current edits', async () => {
  const original = event('$message');
  const f = fixture([event('$plain', { encrypted: false }), original,
    event('$edit', { relation: { rel_type: 'm.replace', event_id: '$message' }, body: 'An actual revision', mentions: ['viewer'] }),
    event('$forged', { sender: '@fc_other:test', relation: { rel_type: 'm.replace', event_id: '$other' }, mentions: ['viewer'] })]);
  const result = await f.read();
  assert.equal(result.length, 1); assert.equal(result[0].content, 'An actual revision'); assert.equal(result[0].mentions[0], 'viewer');
  assert.equal(f.state.decrypted.includes('$plain'), false);
});
test('recent snapshot rejects foreign room content before decryption', async () => {
  const f = fixture([event('$foreign', { room: '!wrong:test' })]);
  await assert.rejects(f.read()); assert.equal(f.state.decrypted.length, 0);
});
for (const changed of ['token', 'actor', 'scope', 'abort', 'generation', 'access']) test('recent inspection discards asynchronous '+changed+' changes', async () => {
  const f = fixture([event('$message')]), abort = new AbortController();
  f.state.hook = async step => {
    if (step !== 'decrypt') return;
    if (changed === 'scope') f.state.current = false;
    else if (changed === 'abort') abort.abort();
    else if (changed === 'generation') f.context.generation++;
    else if (changed === 'access') f.state.permitted = false;
    else f.state[changed] = 'replacement';
  };
  await assert.rejects(f.read({ signal: abort.signal, isCurrent: () => f.state.current }));
  assert.equal(f.state.scrolls, 0);
});
