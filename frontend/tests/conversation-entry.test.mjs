import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const compiled = ts.transpileModule(readFileSync(new URL('../src/lib/conversation-entry.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const policy = {};
vm.runInNewContext(compiled, { exports: policy });
const choose = value => JSON.parse(JSON.stringify(policy.chooseConversationEntry(value)));
const scope = { userId: 'ada', credential: 'session-a', workspaceId: 'studio', channelId: 'courtyard', secure: false };
const message = (id, author_id = 'noor', extra = {}) => ({ id, channel_id: 'courtyard', author_id, ...extra });
const defaults = () => ({ scope: { ...scope }, messages: [message('older'), message('read'), message('newer')],
  readState: { status: 'known', lastReadMessageId: 'read' }, hasOlder: false });
const memory = (extra = {}) => ({ scope: { ...scope }, top: 102.25, following: false, ...extra });

test('an explicit source wins over remembered reading and unread messages, even while paging to it', () => {
  const value = defaults(); value.pendingSource = { channelId: 'courtyard', messageId: 'source-outside-page' }; value.remembered = memory();
  assert.deepEqual(choose(value), { kind: 'source', messageId: 'source-outside-page', loaded: false });
  value.pendingSource.messageId = 'older';
  assert.deepEqual(choose(value), { kind: 'source', messageId: 'older', loaded: true });
});

test('sources from another channel cannot displace the authorized channel entry', () => {
  assert.deepEqual(choose({ ...defaults(), pendingSource: { channelId: 'other', messageId: 'older' } }), { kind: 'unread', messageId: 'newer' });
});

test('scoped remembered reading is an explicit choice even close to the bottom', () => {
  assert.deepEqual(choose({ ...defaults(), remembered: memory() }), { kind: 'position', top: 102.25 });
  assert.deepEqual(choose({ ...defaults(), remembered: memory({ following: true }) }), { kind: 'latest', reason: 'remembered-follow' });
});

test('account, credential, workspace, channel and encryption changes invalidate old reading choices', () => {
  for (const change of [{ userId: 'other' }, { credential: 'rotated' }, { workspaceId: 'other' }, { channelId: 'other' }, { secure: true }]) {
    assert.deepEqual(choose({ ...defaults(), remembered: memory({ scope: { ...scope, ...change } }) }), { kind: 'unread', messageId: 'newer' });
  }
});

test('invalid remembered offsets do not turn unread history into a follow choice', () => {
  for (const top of [-1, Infinity, NaN]) assert.deepEqual(choose({ ...defaults(), remembered: memory({ top, following: true }) }), { kind: 'unread', messageId: 'newer' });
});

test('the genuine receipt boundary chooses the first incoming root, excluding own, deleted, reply and foreign rows', () => {
  const value = defaults();
  value.messages = [message('read'), message('own', 'ada'), message('deleted', 'noor', { deleted: true }),
    message('reply', 'noor', { parent_id: 'read' }), message('foreign', 'noor', { channel_id: 'other' }), message('first'), message('second')];
  assert.deepEqual(choose(value), { kind: 'unread', messageId: 'first' });
});

test('a retained deleted receipt can still establish the actual chronological boundary', () => {
  assert.deepEqual(choose({ ...defaults(), messages: [message('older'), message('read', 'noor', { deleted: true }), message('newer')] }), { kind: 'unread', messageId: 'newer' });
});

test('a first visit selects the first incoming root only when all earlier history is loaded', () => {
  const value = { ...defaults(), readState: { status: 'known', lastReadMessageId: null } };
  assert.deepEqual(choose(value), { kind: 'unread', messageId: 'older' });
  value.hasOlder = true;
  assert.deepEqual(choose(value), { kind: 'history', beforeId: 'older', lastReadMessageId: null });
});

test('a receipt outside the loaded window requests authorized pagination instead of guessing from counts or timestamps', () => {
  const value = { ...defaults(), readState: { status: 'known', lastReadMessageId: 'outside' }, hasOlder: true };
  assert.deepEqual(choose(value), { kind: 'history', beforeId: 'older', lastReadMessageId: 'outside' });
  value.hasOlder = false;
  assert.deepEqual(choose(value), { kind: 'unavailable', reason: 'read-boundary-unavailable', oldestLoadedMessageId: 'older' });
});

test('unavailable read state stays honest and cannot silently become a latest/read-all decision', () => {
  assert.deepEqual(choose({ ...defaults(), readState: { status: 'unavailable' } }), { kind: 'unavailable', reason: 'read-state-unavailable', oldestLoadedMessageId: 'older' });
});

test('known no incoming unread messages selects latest without treating own sends as unread', () => {
  assert.deepEqual(choose({ ...defaults(), messages: [message('read'), message('own', 'ada')] }), { kind: 'latest', reason: 'no-unread' });
  assert.deepEqual(choose({ ...defaults(), messages: [] }), { kind: 'latest', reason: 'empty' });
});

test('equal timestamp rows retain the verified adapter ordering without mutating input', () => {
  const value = defaults(); value.messages = [message('read'), message('b', 'noor', { created_at: 'same' }), message('a', 'noor', { created_at: 'same' })];
  const before = JSON.stringify(value);
  assert.deepEqual(choose(value), { kind: 'unread', messageId: 'b' });
  assert.equal(JSON.stringify(value), before);
});

test('incomplete authority cannot expose a pending source or remembered reading position', () => {
  for (const change of [{ userId: null }, { credential: null }, { workspaceId: null }, { channelId: null }]) {
    assert.deepEqual(choose({ ...defaults(), scope: { ...scope, ...change }, remembered: memory(),
      pendingSource: { channelId: 'courtyard', messageId: 'older' } }), { kind: 'unavailable', reason: 'incomplete-scope', oldestLoadedMessageId: null });
  }
});

test('a locked incoming root cannot become an unread target or automatic latest entry', () => {
  const value = defaults(); value.scope.secure = true; value.messages[2].locked = true;
  assert.deepEqual(choose(value), { kind: 'unavailable', reason: 'read-content-unavailable', oldestLoadedMessageId: 'older' });
});

test('a locked own send after the receipt cannot falsely become known no-unread', () => {
  const value = defaults(); value.scope.secure = true; value.messages = [message('read'), message('own', 'ada', { locked: true })];
  assert.deepEqual(choose(value), { kind: 'unavailable', reason: 'read-content-unavailable', oldestLoadedMessageId: 'read' });
});

test('readable rows after a locked unread root do not authorize following across inaccessible content', () => {
  const value = defaults(); value.scope.secure = true;
  value.messages = [message('read'), message('locked', 'noor', { locked: true }), message('readable')];
  assert.deepEqual(choose(value), { kind: 'unavailable', reason: 'read-content-unavailable', oldestLoadedMessageId: 'read' });
  value.remembered = memory({ scope: { ...value.scope }, following: true });
  assert.deepEqual(choose(value), { kind: 'unavailable', reason: 'read-content-unavailable', oldestLoadedMessageId: 'read' });
});

test('locks before the actual receipt and deleted, reply or foreign locks do not block a readable unread root', () => {
  const value = defaults(); value.scope.secure = true;
  value.messages = [message('historical', 'noor', { locked: true }), message('read'),
    message('removed', 'noor', { locked: true, deleted: true }), message('reply', 'noor', { locked: true, parent_id: 'read' }),
    message('foreign', 'noor', { locked: true, channel_id: 'other' }), message('readable')];
  assert.deepEqual(choose(value), { kind: 'unread', messageId: 'readable' });
});

test('a currently locked latest receipt does not turn into a readable latest decision', () => {
  const value = defaults(); value.scope.secure = true; value.messages = [message('read', 'noor', { locked: true })];
  assert.deepEqual(choose(value), { kind: 'unavailable', reason: 'read-content-unavailable', oldestLoadedMessageId: 'read' });
});

test('an unread window with locked content requests unlock rather than automatically paging or guessing', () => {
  const value = defaults(); value.scope.secure = true; value.readState.lastReadMessageId = 'outside'; value.hasOlder = true;
  value.messages[2].locked = true;
  assert.deepEqual(choose(value), { kind: 'unavailable', reason: 'read-content-unavailable', oldestLoadedMessageId: 'older' });
});

test('explicit locked-source navigation and remembered reading stay distinct from authorizing content reads', () => {
  const value = defaults(); value.scope.secure = true; value.messages[2].locked = true;
  value.pendingSource = { channelId: 'courtyard', messageId: 'newer' };
  assert.deepEqual(choose(value), { kind: 'source', messageId: 'newer', loaded: true });
  value.pendingSource = null; value.remembered = memory({ scope: { ...value.scope } });
  assert.deepEqual(choose(value), { kind: 'position', top: 102.25 });
});
