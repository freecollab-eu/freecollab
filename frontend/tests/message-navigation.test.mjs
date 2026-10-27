import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/lib/message-navigation.ts', import.meta.url), 'utf8');
const channel = { id: 'room', workspace_id: 'workspace', secure: false };
const message = { id: 'message', channel_id: 'room', author_id: 'author', content: 'Actual source', encrypted: false, created_at: '2026-10-28T12:00:00Z' };
const plain = value => JSON.parse(JSON.stringify(value));
function navigation({ secure = false, reply = false } = {}) {
  const state = { token: 'session', actor: 'actor', current: true, channels: [], requests: [], secureCalls: [], channel: { ...channel, secure },
    message: { ...message, encrypted: secure, ...(reply ? { parent_id: 'parent' } : {}) }, parent: { ...message, id: 'parent' }, hook: async () => {} };
  const api = {
    getAccessToken: () => state.token, getCurrentUserId: () => state.actor,
    async getChannel(id) { state.channels.push(id); await state.hook('channel:' + state.channels.length); return { ...state.channel }; },
    async request(path) { state.requests.push(path); await state.hook(path); return path === '/messages/parent' ? state.parent : state.message; },
  };
  const exports = {};
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, {
    exports, require: name => name === './api' ? { api } : { getSecureMessaging: async () => {
      await state.hook('secure-load');
      return { getMessage: async (...args) => { state.secureCalls.push(args); await state.hook('secure-message'); return state.message; } };
    } },
  });
  return { state, resolve: () => exports.resolveNavigationMessage({ ...channel, secure }, 'message', () => state.current) };
}

test('native source navigation refreshes exact channel authority and fetches the actual message without remembered IDs', async () => {
  const fixture = navigation();
  assert.deepEqual(plain(await fixture.resolve()), fixture.state.message);
  assert.deepEqual(fixture.state.channels, ['room', 'room']);
  assert.deepEqual(fixture.state.requests, ['/messages/message']);
  assert.equal(fixture.state.secureCalls.length, 0);
});

test('native replies resolve and verify their real root parent', async () => {
  const fixture = navigation({ reply: true });
  assert.equal((await fixture.resolve()).parent_id, 'parent');
  assert.deepEqual(fixture.state.requests, ['/messages/message', '/messages/parent']);
  for (const bad of [{ id: 'different' }, { channel_id: 'foreign' }, { deleted: true }, { locked: true }, { parent_id: 'nested' }]) {
    const invalid = navigation({ reply: true }); Object.assign(invalid.state.parent, bad);
    await assert.rejects(invalid.resolve(), /no longer available/);
  }
});

test('wrong channel/workspace/mode or unavailable substituted message never becomes a navigation target', async () => {
  for (const bad of [{ id: 'foreign' }, { workspace_id: 'foreign' }, { secure: true }]) {
    const fixture = navigation(); Object.assign(fixture.state.channel, bad);
    await assert.rejects(fixture.resolve()); assert.equal(fixture.state.requests.length, 0);
  }
  for (const bad of [{ id: 'different' }, { channel_id: 'foreign' }, { deleted: true }, { locked: true }, { parent_id: 'message' }]) {
    const fixture = navigation(); Object.assign(fixture.state.message, bad); await assert.rejects(fixture.resolve());
  }
});

test('secure navigation uses only the real secure single-event reader and never a native fallback', async () => {
  const fixture = navigation({ secure: true, reply: true });
  assert.equal((await fixture.resolve()).parent_id, 'parent');
  assert.deepEqual(fixture.state.secureCalls, [['room', 'message']]);
  assert.deepEqual(fixture.state.requests, []);
  const failing = navigation({ secure: true }); failing.state.hook = async step => { if (step === 'secure-message') throw new Error('Missing encrypted event'); };
  await assert.rejects(failing.resolve(), /Missing encrypted event/); assert.deepEqual(failing.state.requests, []);
});

test('session, actor, caller-scope and final authority changes stop every late navigation result', async () => {
  for (const step of ['channel:1', '/messages/message', '/messages/parent', 'channel:2']) for (const change of ['token', 'actor', 'scope']) {
    const fixture = navigation({ reply: true });
    fixture.state.hook = async at => { if (at === step) { if (change === 'scope') fixture.state.current = false; else fixture.state[change] = 'changed'; } };
    await assert.rejects(fixture.resolve(), /no longer available/, step + ':' + change);
  }
  for (const step of ['secure-load', 'secure-message']) {
    const fixture = navigation({ secure: true }); fixture.state.hook = async at => { if (at === step) fixture.state.token = 'changed'; };
    await assert.rejects(fixture.resolve()); assert.deepEqual(fixture.state.requests, []);
  }
  const revoked = navigation(); revoked.state.hook = async step => { if (step === 'channel:2') throw new Error('Membership revoked'); };
  await assert.rejects(revoked.resolve(), /Membership revoked/);
});

const secureSource = readFileSync(new URL('../src/lib/secure/index.ts', import.meta.url), 'utf8');
const parsed = ts.createSourceFile('secure.ts', secureSource, ts.ScriptTarget.Latest, true);
const actualFunctions = ['asMessage', 'getMessage'].map(name => {
  const declaration = parsed.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === name);
  assert.ok(declaration, 'Tests execute actual secure ' + name); return declaration.getText(parsed);
}).join('\n');
function encryptedEvent(id, overrides = {}) {
  const value = { id, room: '!room:test', sender: '@fc_author:test', type: 'm.room.message', body: 'Actual encrypted source', encrypted: true,
    redacted: false, failed: false, timestamp: 1793188800000, content: {}, relation: undefined, ...overrides };
  return { value, getId: () => value.id, getRoomId: () => value.room, getSender: () => value.sender, getType: () => value.type,
    isEncrypted: () => value.encrypted, isRedacted: () => value.redacted, isDecryptionFailure: () => value.failed, getTs: () => value.timestamp,
    getContent: () => ({ body: value.body, ...value.content }), getOriginalContent: () => ({ 'm.relates_to': value.relation }) };
}
function encrypted({ reply = false } = {}) {
  const root = encryptedEvent('$root'), child = encryptedEvent('$message', reply ? { relation: { rel_type: 'm.thread', event_id: '$root' } } : {});
  const state = { token: 'session', actor: 'actor', active: true, refreshes: 0, fetched: [], hook: async () => {}, child, root };
  const context = {
    exports: {}, generation: 0, client: {}, EventType: { RoomMessage: 'm.room.message' },
    configuration: { getAccessToken: () => state.token, getUserId: () => state.actor },
    accountId: sender => sender.split(':')[0].slice(4), encryptedContentReferences: () => [],
    refreshBindings: async () => { state.refreshes++; await state.hook('bindings:' + state.refreshes); },
    roomFor: async () => ({ c: context.client, roomId: '!room:test', assertCurrent: () => { if (!state.active) throw new Error('Binding revoked'); } }),
    secureMessaging: { getDecryptedEvent: async (_, id) => { state.fetched.push(id); await state.hook(id); const event = id === '$message' ? state.child : id === '$root' ? state.root : undefined; if (!event) throw new Error('Event missing'); return event; } },
  };
  vm.runInNewContext(ts.transpileModule(actualFunctions + '\nexports.getMessage = getMessage;', {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, context);
  return { state, context, resolve: () => context.exports.getMessage('room', '$message') };
}

test('actual secure projection returns a decrypted root/reply and verifies the real parent event', async () => {
  for (const reply of [false, true]) {
    const fixture = encrypted({ reply }); const result = await fixture.resolve();
    assert.equal(result.id, '$message'); assert.equal(result.channel_id, 'room'); assert.equal(result.content, 'Actual encrypted source');
    assert.equal(result.encrypted, true); assert.equal(result.parent_id, reply ? '$root' : undefined);
    assert.deepEqual(fixture.state.fetched, reply ? ['$message', '$root'] : ['$message']); assert.equal(fixture.state.refreshes, 2);
  }
});

test('actual secure reader rejects substituted events, replacements, nonmessages and unreadable roots/parents', async () => {
  for (const bad of [{ id: '$different' }, { room: '!foreign:test' }, { type: 'eu.freecollab.product.task' }, { encrypted: false },
    { redacted: true }, { failed: true }, { body: undefined }, { relation: { rel_type: 'm.replace', event_id: '$root' } },
    { content: { 'm.new_content': { body: 'Replacement' } } }, { relation: { rel_type: 'm.thread', event_id: '$message' } },
    { relation: { 'm.in_reply_to': { event_id: '$root' } } }]) {
    const fixture = encrypted(); Object.assign(fixture.state.child.value, bad); await assert.rejects(fixture.resolve());
  }
  for (const bad of [{ id: '$different' }, { room: '!foreign:test' }, { redacted: true }, { failed: true },
    { type: 'eu.freecollab.product.task' }, { relation: { rel_type: 'm.thread', event_id: '$nested' } }]) {
    const fixture = encrypted({ reply: true }); Object.assign(fixture.state.root.value, bad); await assert.rejects(fixture.resolve());
  }
});

test('actual secure reader rejects account/encryption resets, revocation and changed parents across awaits', async () => {
  for (const step of ['bindings:1', '$message', '$root', 'bindings:2']) for (const change of ['token', 'actor', 'generation', 'binding']) {
    const fixture = encrypted({ reply: true });
    fixture.state.hook = async at => { if (at === step) { if (change === 'binding') fixture.state.active = false; else if (change === 'generation') fixture.context.generation++; else fixture.state[change] = 'changed'; } };
    await assert.rejects(fixture.resolve(), undefined, step + ':' + change);
  }
  const changed = encrypted({ reply: true }); changed.state.hook = async at => { if (at === 'bindings:2') changed.state.child.value.relation.event_id = '$other'; };
  await assert.rejects(changed.resolve());
});
