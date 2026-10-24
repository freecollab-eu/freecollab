import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const exports = {};
vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/lib/thread-history.ts', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText, { exports, Map, Date });
const merge = exports.mergeThreadReplies;
const reply = (id, extra = {}) => ({ id, channel_id: 'channel', parent_id: 'parent', content: id, author_id: 'person', created_at: '2026-10-23T10:00:00', encrypted: true, ...extra });

test('earlier thread pages are complete, ordered and idempotent even with equal timestamps', () => {
  const current = Array.from({ length: 50 }, (_, n) => reply('reply-' + String(55 + n).padStart(3, '0')));
  const page = Array.from({ length: 50 }, (_, n) => reply('reply-' + String(5 + n).padStart(3, '0')));
  const all = merge(current, page, 'channel', 'parent');
  assert.equal(all.length, 100); assert.equal(all[0].id, 'reply-005'); assert.equal(all.at(-1).id, 'reply-104');
  assert.deepEqual(merge(all, page, 'channel', 'parent'), all); assert.equal(current.length, 50);
});

test('a late older page cannot overwrite a socket edit or resurrect a deleted reply', () => {
  const current = [reply('edited', { content: 'New edit' }), reply('removed', { deleted: true, content: '[Message deleted]' })];
  const all = merge(current, [reply('edited'), reply('removed'), reply('older', { created_at: '2020-01-01T00:00:00Z' })], 'channel', 'parent');
  assert.equal(all.find(row => row.id === 'edited').content, 'New edit'); assert.equal(all.find(row => row.id === 'removed').deleted, true); assert.equal(all[0].id, 'older');
});

test('page results from a different channel or thread are rejected before merging', () => {
  assert.throws(() => merge([], [reply('foreign', { channel_id: 'other' })], 'channel', 'parent'));
  assert.throws(() => merge([], [reply('foreign', { parent_id: 'other' })], 'channel', 'parent'));
  assert.equal(merge([reply('foreign', { channel_id: 'other' })], [reply('valid')], 'channel', 'parent').length, 1);
});

test('actual native thread API forwards the opaque cursor and bounded page size', async () => {
  const source = readFileSync(new URL('../src/lib/api.ts', import.meta.url), 'utf8').replace("const API_URL = import.meta.env.VITE_API_URL || '/api';", "const API_URL = '/api';");
  const module = {};
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText, { exports: module, localStorage: { getItem: () => null }, URLSearchParams, DOMException });
  const calls = []; module.api.secureMessage = async () => null;
  module.api.request = async (...args) => { calls.push(args); return { parent_id: 'parent', replies: [], has_more: false }; };
  await module.api.getThread('parent', { before: 'reply oldest 🧭', limit: 500 });
  const query = new URL(calls[0][0], 'https://local.example'); assert.equal(query.pathname, '/threads/messages/parent'); assert.equal(query.searchParams.get('limit'), '100'); assert.equal(query.searchParams.get('before'), 'reply oldest 🧭');
});

test('secure pagination stays on the trusted adapter and never enters native REST', async () => {
  const source = readFileSync(new URL('../src/lib/api.ts', import.meta.url), 'utf8').replace("const API_URL = import.meta.env.VITE_API_URL || '/api';", "const API_URL = '/api';");
  const module = {};
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText, { exports: module, localStorage: { getItem: () => null }, URLSearchParams, DOMException });
  const all = Array.from({ length: 105 }, (_, n) => reply('reply-' + n));
  let trustedReads = 0; module.api.secureMessage = async () => ({ channelId: 'channel', adapter: { getThread: async () => { trustedReads++; return all; } } });
  module.api.request = async () => { throw new Error('Secure history must never fall back to native REST'); };
  const latest = await module.api.getThread('parent', { limit: 50 }), older = await module.api.getThread('parent', { limit: 50, before: latest.replies[0].id });
  assert.equal(latest.has_more, true); assert.equal(latest.replies[0].id, 'reply-55'); assert.equal(older.replies[0].id, 'reply-5'); assert.equal(trustedReads, 2);
  await assert.rejects(module.api.getThread('parent', { before: 'missing' }), /cursor not found/);
});

test('main history explicitly opts into roots-only without changing the complete-history default', async () => {
  const source = readFileSync(new URL('../src/lib/api.ts', import.meta.url), 'utf8').replace("const API_URL = import.meta.env.VITE_API_URL || '/api';", "const API_URL = '/api';");
  const module = {};
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText, { exports: module, localStorage: { getItem: () => null }, URLSearchParams, DOMException });
  const calls = []; module.api.secureChannel = async () => null; module.api.request = async (path) => { calls.push(path); return []; };
  await module.api.getMessages('channel'); await module.api.getMessages('channel', { rootsOnly: true, limit: 100 });
  assert.equal(new URL(calls[0], 'https://local.example').searchParams.has('roots_only'), false);
  assert.equal(new URL(calls[1], 'https://local.example').searchParams.get('roots_only'), 'true');
});

test('actual trusted Matrix projection filters replies before pagination and retains canonical edits/counts', async () => {
  const source = readFileSync(new URL('../src/lib/secure/index.ts', import.meta.url), 'utf8');
  const parsed = ts.createSourceFile('secure.ts', source, ts.ScriptTarget.Latest, true);
  const actual = parsed.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'getMessages');
  const projection = parsed.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'asMessage');
  assert.ok(actual, 'Test must execute the real trusted getMessages function');
  assert.ok(projection, 'Test must execute the real trusted message projection');
  const referenceModel = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/lib/message-references.ts', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText, { exports: referenceModel });
  const messageContent = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/lib/secure/message-content.ts', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText, { exports: messageContent, require: name => { assert.equal(name, '../message-references'); return referenceModel; } });
  const event = (id, relation, extra = {}) => ({ getId: () => id, getType: () => 'm.room.message', getOriginalContent: () => ({ ...(relation ? { 'm.relates_to': relation } : {}) }), isDecryptionFailure: () => false, isRedacted: () => false, getSender: () => 'same-sender', getContent: () => ({ body: id, ...extra }), getTs: () => 1 });
  const replies = Array.from({ length: 105 }, (_, n) => event('reply-' + n, { rel_type: 'm.thread', event_id: 'root' }));
  const editedBody = 'Current verified root edit @Ada', reference = { kind: 'person', id: 'ada', start: editedBody.indexOf('@'), end: editedBody.length };
  const root = event('root'), edit = event('edit', { rel_type: 'm.replace', event_id: 'root' }, { 'm.new_content': { body: editedBody, 'eu.freecollab.references': [reference] } });
  let rows = replies, reads = 0;
  const room = { oldState: { paginationToken: 'older' } }, module = {};
  const context = { exports: module, EventType: { RoomMessage: 'm.room.message' }, Map, Date, encryptedContentReferences: messageContent.encryptedContentReferences, accountId: id => id, roomFor: async () => ({ room, c: { scrollback: async () => { reads++; rows = [root, ...replies, edit]; room.oldState.paginationToken = null; } } }), events: async () => rows };
  vm.runInNewContext(ts.transpileModule(projection.getText(parsed) + '\n' + actual.getText(parsed) + '\nexports.read = getMessages;', { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText, context);
  const roots = await module.read('channel', undefined, 50, true);
  assert.equal(reads, 1); assert.equal(roots.length, 1); assert.equal(roots[0].id, 'root'); assert.equal(roots[0].content, editedBody); assert.equal(roots[0].thread_count, 105);
  assert.deepEqual(JSON.parse(JSON.stringify(roots[0].references)), [reference]);
  const complete = await module.read('channel', undefined, 200); assert.equal(complete.length, 106); assert.ok(complete.some(row => row.id === 'reply-0'));
});
