import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/lib/secure/index.ts', import.meta.url), 'utf8');
const parsed = ts.createSourceFile('secure.ts', source, ts.ScriptTarget.Latest, true);
const adapter = parsed.statements.find(node => ts.isVariableStatement(node) && node.declarationList.declarations.some(item => item.name.getText() === 'secureMessaging'));
const object = adapter.declarationList.declarations[0].initializer;
const names = ['asMessage', 'currentRoomEvents', 'getRecentMessages', 'getReadReceipts'];
const functions = names.map(name => {
  const declaration = parsed.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === name);
  if (declaration) return declaration.getText(parsed);
  const method = object.properties.find(node => node.name?.getText() === name && ts.isMethodDeclaration(node));
  return method ? method.getText(parsed).replace(/^async /, 'async function ') : '';
}).join('\n');

function wire(id, type = 'm.room.message', extra = {}) {
  return { event_id: id, room_id: '!room:test', sender: '@fc_viewer:test', origin_server_ts: 1793188800000,
    type: 'm.room.encrypted', content: {}, clearType: type, clearContent: { body: 'Current snapshot message' }, ...extra };
}
class Event {
  constructor(raw) { this.raw = raw; this.decrypted = false; }
  getId() { return this.raw.event_id; } getRoomId() { return this.raw.room_id; }
  getSender() { return this.raw.sender; } getTs() { return this.raw.origin_server_ts; }
  isEncrypted() { return this.raw.type === 'm.room.encrypted'; }
  isRedacted() { return !!this.raw.redacted; } isDecryptionFailure() { return !!this.raw.failed; }
  getType() { return this.decrypted ? this.raw.clearType : this.raw.type; }
  getContent() { return this.decrypted ? this.raw.clearContent : this.raw.content; }
  getOriginalContent() { return this.getContent(); }
}
function fixture(snapshot, { cold = false, live = [], end } = {}) {
  const state = { actor: 'viewer', token: 'session', owner: cold ? null : 'session', current: true, permitted: true,
    requests: [], decrypted: [], refreshed: 0, hook: async () => {} };
  const client = { createMessagesRequest: async (...args) => { state.requests.push(args); await state.hook('snapshot'); return { chunk: snapshot, end }; },
    decryptEventIfNeeded: async event => { state.decrypted.push(event.getId()); await state.hook('decrypt'); event.decrypted = true; } };
  const room = { getLiveTimeline: () => ({ getEvents: () => live }) };
  const context = { exports: {}, generation: 1, client: cold ? null : client, MatrixEvent: Event, Direction: { Backward: 'b' },
    EventType: { RoomMessage: 'm.room.message', RoomCreate: 'm.room.create', RoomEncrypted: 'm.room.encrypted' }, RECEIPT: 'eu.freecollab.receipt', Map, Date, Number, Array, Promise,
    configuration: { getAccessToken: () => state.token, getUserId: () => state.actor },
    accountId: id => id === '@fc_viewer:test' ? 'viewer' : id, encryptedContentReferences: () => [],
    ensureSession: async () => { if (state.owner !== state.token) { state.owner = state.token; context.generation++; }
      await state.hook('bootstrap'); if (!context.client) context.client = client; return client; },
    refreshBindings: async () => { state.refreshed++; await state.hook('refresh:'+state.refreshed); },
    roomFor: async () => ({ c: client, room, roomId: '!room:test', assertCurrent: () => { if (!state.permitted) throw Error('Current membership removed'); } }),
    events: async () => live,
  };
  vm.runInNewContext(ts.transpileModule(functions+'\nexports.receipts=getReadReceipts;exports.recent=getRecentMessages;',
    { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText, context);
  return { state, context, receipts: () => context.exports.receipts('room'), recent: options => context.exports.recent('room', options) };
}
const receipt = wire('$read', 'eu.freecollab.receipt', { clearContent: { event_id: '$root' } });

test('cold reload reads the current encrypted receipt even when cached sync has no current room events', async () => {
  const f = fixture([receipt], { cold: true });
  const rows = await f.receipts();
  assert.equal(rows.length, 1); assert.equal(rows[0].last_read_message_id, '$root');
  assert.equal(rows[0].user_id, 'viewer'); assert.equal(rows[0].read_at, '2026-10-28T12:00:00.000Z');
  assert.deepEqual(f.state.requests, [['!room:test', null, 200, 'b']]);
});
test('summary gets the current root/reply from one bounded encrypted snapshot while sync is behind', async () => {
  const f = fixture([wire('$reply', 'm.room.message', { sender: '@fc_peer:test', clearContent: { body: 'Reply', 'm.relates_to': { rel_type: 'm.thread', event_id: '$root' } } }), wire('$root')], { cold: true });
  const rows = await f.recent({ eventLimit: 2 });
  assert.equal(rows.length, 2); assert.equal(rows[0].id, '$reply'); assert.equal(rows[0].parent_id, '$root');
  assert.deepEqual(f.state.requests, [['!room:test', null, 2, 'b']]);
});
for (const reader of ['receipts', 'recent']) for (const change of ['actor', 'token', 'generation', 'client', 'membership']) {
  test(`${reader} rejects ${change} changes during its current snapshot read`, async () => {
    const f = fixture([receipt]);
    f.state.hook = async step => {
      if (step !== 'snapshot') return;
      if (change === 'generation') f.context.generation++;
      else if (change === 'client') f.context.client = {};
      else if (change === 'membership') f.state.permitted = false;
      else f.state[change] = 'replacement';
    };
    await assert.rejects(f[reader]()); assert.equal(f.state.decrypted.length, 0);
  });
}
test('unreadable snapshot ciphertext makes the receipt baseline unavailable instead of claiming no prior read', async () => {
  const f = fixture([wire('$locked', 'eu.freecollab.receipt', { failed: true })]);
  await assert.rejects(f.receipts());
  await assert.rejects(fixture([wire('$unresolved', 'm.room.encrypted')]).receipts());
});
test('missing own receipt requires a verified history boundary even when a short page is returned', async () => {
  const partial = fixture([wire('$message')], { end: 'opaque-more-history' });
  await assert.rejects(partial.receipts());
  const complete = fixture([wire('$message')]); assert.equal((await complete.receipts()).length, 0);
  const created = fixture([wire('$message'), wire('$create', 'm.room.create', { type: 'm.room.create' })], { end: 'opaque' });
  assert.equal((await created.receipts()).length, 0);
});
test('a verified own receipt tolerates older own locks and peer locks but rejects a potentially newer own receipt', async () => {
  const older = wire('$older', 'eu.freecollab.receipt', { failed: true });
  const peer = wire('$peer', 'eu.freecollab.receipt', { failed: true, sender: '@fc_peer:test' });
  assert.equal((await fixture([peer, receipt, older], { end: 'older-history' }).receipts()).length, 1);
  await assert.rejects(fixture([older, receipt]).receipts());
});
test('receipt snapshot ignores redactions and chooses the latest valid receipt in backwards response order', async () => {
  const f = fixture([wire('$redacted', 'eu.freecollab.receipt', { redacted: true, clearContent: { event_id: '$bad' } }),
    { ...receipt, event_id: '$latest', clearContent: { event_id: '$new-root' } }, receipt]);
  const rows = await f.receipts(); assert.equal(rows.length, 1); assert.equal(rows[0].last_read_message_id, '$new-root');
});
test('current room snapshot rejects foreign-room events and never decrypts more than its event bound', async () => {
  const f = fixture([wire('$foreign', 'm.room.message', { room_id: '!other:test' })]);
  await assert.rejects(f.recent()); assert.equal(f.state.decrypted.length, 0);
  const bounded = fixture(Array.from({ length: 300 }, (_, index) => wire('$'+index)));
  await bounded.recent({ eventLimit: 2 }); assert.equal(bounded.state.decrypted.length, 2);
});
test('current snapshot decrypts with at most four simultaneous operations', async () => {
  const f = fixture(Array.from({ length: 20 }, (_, index) => wire('$'+index)));
  let active = 0, peak = 0;
  f.state.hook = async step => { if (step !== 'decrypt') return; peak = Math.max(peak, ++active); await new Promise(resolve => setTimeout(resolve, 1)); active--; };
  await f.recent(); assert.equal(peak, 4); assert.equal(active, 0);
});
test('receipt snapshot retains each member latest valid read while rejecting a final membership recheck', async () => {
  const peer = wire('$peer', 'eu.freecollab.receipt', { sender: '@fc_peer:test', clearContent: { event_id: '$peer-root' } });
  assert.equal((await fixture([peer, receipt]).receipts()).length, 2);
  const f = fixture([receipt]); f.state.hook = async step => { if (step === 'refresh:2') f.state.permitted = false; };
  await assert.rejects(f.receipts());
});
