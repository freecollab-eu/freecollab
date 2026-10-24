import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

function moduleAt(path, dependencies = {}) {
  const exports = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, { exports, require: name => { assert.ok(name in dependencies, name); return dependencies[name]; } });
  return exports;
}
const references = moduleAt('../src/lib/message-references.ts');
const { prepareMessageReferences, encryptedMessageContent, encryptedContentReferences, REFERENCE_CONTENT_KEY } = moduleAt('../src/lib/secure/message-content.ts', { '../message-references': references });
const plain = value => JSON.parse(JSON.stringify(value));
const content = 'Hej @Ada och @Design, läs @Budget.';
const refs = [
  { kind: 'person', id: 'ada-id', start: 4, end: 8 },
  { kind: 'group', id: 'design-id', start: 13, end: 20 },
  { kind: 'document', id: 'budget-id', start: 26, end: 33 },
];
const available = keys => ({ items: keys.map(key => ({ ...key, available: true, ...(key.kind === 'person' ? { user_ids: ['ada-id'] } : key.kind === 'group' ? { user_ids: ['ada-id', 'bea-id'] } : {}) })) });

test('encrypted reference resolution sends only opaque IDs and expands current groups once', async () => {
  const calls = [];
  const result = await prepareMessageReferences(content, refs, ['legacy-id'], async keys => { calls.push(plain(keys)); return available(keys); });
  assert.deepEqual(calls, [[{ kind: 'person', id: 'ada-id' }, { kind: 'group', id: 'design-id' }, { kind: 'document', id: 'budget-id' }]]);
  assert.equal(JSON.stringify(calls).includes('Budget'), false);
  assert.equal(JSON.stringify(calls).includes(content), false);
  assert.deepEqual(plain(result.mentions), ['legacy-id', 'ada-id', 'bea-id']);
  assert.deepEqual(plain(result.references), refs);
});

test('current membership is resolved for each attempt rather than reusing an old group audience', async () => {
  let members = ['ada-id'];
  const resolve = async keys => ({ items: keys.map(key => ({ ...key, available: true, ...(key.kind === 'group' ? { user_ids: members } : key.kind === 'person' ? { user_ids: ['ada-id'] } : {}) })) });
  assert.deepEqual(plain((await prepareMessageReferences(content, refs, [], resolve)).mentions), ['ada-id']);
  members = ['bea-id'];
  assert.deepEqual(plain((await prepareMessageReferences(content, refs, [], resolve)).mentions), ['ada-id', 'bea-id']);
});

test('unavailable, duplicate and malformed group resolutions fail before an encrypted send', async () => {
  for (const response of [
    { items: [] },
    { items: available(refs).items.map(row => ({ ...row, available: false })) },
    { items: [...available(refs).items, available(refs).items[0]] },
    { items: available(refs).items.map(row => row.kind === 'group' ? { ...row, user_ids: ['invalid\nrecipient'] } : row) },
  ]) await assert.rejects(prepareMessageReferences(content, refs, [], async () => response), /available|verified/);
});

test('recipient fanout is bounded after deduplicating explicit and group mentions', async () => {
  const fifty = Array.from({ length: 50 }, (_, index) => 'member-' + index);
  const group = [{ kind: 'group', id: 'large', start: 0, end: 6 }];
  const resolve = async keys => ({ items: keys.map(key => ({ ...key, available: true, user_ids: fifty })) });
  assert.equal((await prepareMessageReferences('@Group', group, fifty, resolve)).mentions.length, 50);
  await assert.rejects(prepareMessageReferences('@Group', group, ['extra-person'], resolve), /more than 50/);
});

test('reference positions and identities remain inside the encrypted payload alongside a proper thread relation', async () => {
  const prepared = await prepareMessageReferences(content, refs, [], async keys => available(keys));
  const payload = encryptedMessageContent(content, prepared.mentions, prepared.references, '$parent', ['@fc_ada:local.test', '@fc_bea:local.test']);
  assert.equal(payload.body, content);
  assert.deepEqual(plain(payload[REFERENCE_CONTENT_KEY]), refs);
  assert.equal(payload['m.relates_to'].event_id, '$parent');
  assert.deepEqual(plain(payload['m.mentions'].user_ids), ['@fc_ada:local.test', '@fc_bea:local.test']);
  assert.deepEqual(plain(encryptedContentReferences(content, payload)), refs);
});

test('legacy and malformed encrypted metadata renders as literal text without guessed bindings', () => {
  assert.deepEqual(plain(encryptedContentReferences(content, {})), []);
  assert.deepEqual(plain(encryptedContentReferences(content, { [REFERENCE_CONTENT_KEY]: [{ ...refs[0], start: 5 }] })), []);
  assert.throws(() => encryptedMessageContent(content, [], [{ ...refs[0], end: 300 }]), /references/);
});

test('a session change during opaque reference lookup prevents encryption initialization or sending', async () => {
  const source = readFileSync(new URL('../src/lib/secure/index.ts', import.meta.url), 'utf8');
  const parsed = ts.createSourceFile('secure.ts', source, ts.ScriptTarget.Latest, true);
  const actual = parsed.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'messageRecipients');
  assert.ok(actual, 'Test must execute the actual encrypted reference preparation');
  let token = 'owner-token', release, encryptionInitializations = 0;
  const pending = new Promise(resolve => { release = resolve; }), module = {}, lookups = [];
  vm.runInNewContext(ts.transpileModule(actual.getText(parsed) + '\nexports.prepare = messageRecipients;', {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, {
    exports: module, prepareMessageReferences,
    configuration: { getAccessToken: () => token, getUserId: () => 'owner' },
    request: async (path, body) => { lookups.push({ path, body: plain(body) }); return pending; },
    ensureSession: async () => { encryptionInitializations++; return { getUserId: () => '@fc_owner:local.test' }; },
  });
  const operation = module.prepare('room', content, refs, []), rejected = assert.rejects(operation, /account changed/);
  await new Promise(resolve => setImmediate(resolve));
  token = 'other-token'; release(available(refs)); await rejected;
  assert.equal(encryptionInitializations, 0);
  assert.equal(lookups[0].path, '/mentions/channels/room/references/resolve');
  assert.equal(JSON.stringify(lookups).includes(content), false);
});
