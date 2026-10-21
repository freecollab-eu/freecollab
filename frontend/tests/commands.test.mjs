import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/lib/commands.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;
const helpers = {};
vm.runInNewContext(compiled, { exports: helpers });
const { searchCommands, nextCommandIndex, normalizeCommandQuery } = helpers;
const plain = value => JSON.parse(JSON.stringify(value));

const channels = [
  { id: 'product-notes', name: 'Product notes' },
  { id: 'general', name: 'Allmänt' },
  { id: 'product', name: 'Product' },
  { id: 'design', name: 'Design' },
];
const options = { channels, messages: [], activeChannelId: 'general', hasActiveChannel: true };

function search(query, changes = {}) {
  return plain(searchCommands({ ...options, query, ...changes }));
}

test('channels keep their original names while matching case, accents and extra whitespace', () => {
  assert.equal(normalizeCommandQuery('  ALLMÄNT   Idéer  '), 'allmant ideer');
  assert.equal(search('ALLMANT')[0].label, 'Allmänt');
  assert.equal(search('allma\u0308nt')[0].channelId, 'general');
  assert.equal(search('  proDUct   notes ')[0].channelId, 'product-notes');
  assert.equal(search('prod')[0].channelId, 'product-notes');
  assert.deepEqual(search('product').filter(item => item.kind === 'channel').map(item => item.channelId), ['product', 'product-notes']);
});

test('blank suggestions prefer the active channel and retain useful actions without returning message history', () => {
  const result = search('', { messages: [{ id: 'private-message', content: 'Visible conversation', author_id: 'one', created_at: '2026-10-20T10:00:00Z' }] });
  assert.equal(result[0].channelId, 'general');
  assert.equal(result[0].current, true);
  assert.equal(result[0].description, 'Current channel');
  assert.deepEqual(result.filter(item => item.kind === 'action').map(item => item.action), ['create', 'files', 'members', 'appearance']);
  assert.equal(result.some(item => item.kind === 'message'), false);
});

test('channel search can find channels beyond the initial suggestions and # limits results to channels', () => {
  const manyChannels = Array.from({ length: 20 }, (_, index) => ({ id: `channel-${index}`, name: `Studio ${index}` }));
  assert.equal(search('', { channels: manyChannels }).filter(item => item.kind === 'channel').length, 8);
  assert.equal(search('Studio 19', { channels: manyChannels })[0].channelId, 'channel-19');
  assert.deepEqual(search('# allmant').map(item => [item.kind, item.channelId]), [['channel', 'general']]);
  assert.equal(search('# files').length, 0);
});

test('actions are discoverable by intent and channel-specific actions are absent without a selected channel', () => {
  assert.equal(search('typography')[0].action, 'appearance');
  assert.equal(search('attachments')[0].action, 'files');
  assert.equal(search('invite people')[0].action, 'members');
  const withoutChannel = { activeChannelId: null, hasActiveChannel: false, channels: [] };
  assert.deepEqual(search('', withoutChannel).map(item => item.action), ['create', 'appearance']);
  assert.equal(search('files', withoutChannel).length, 0);
  assert.equal(search('members', withoutChannel).length, 0);
});

test('message results use only supplied visible parent messages, exclude deletions and retain literal text', () => {
  const message = { author_id: 'member', created_at: '2026-10-20T10:00:00Z' };
  const messages = [
    { ...message, id: 'older', content: 'A proposal for the linen studio.' },
    { ...message, id: 'deleted', content: 'linen secret', deleted: true },
    { ...message, id: 'thread', content: 'linen thread reply', parent_id: 'older' },
    { ...message, id: 'newer', content: 'Línén <img src=x onerror=alert(1)> stays literal.' },
  ];
  const result = search('linen', { messages }).filter(item => item.kind === 'message');
  assert.deepEqual(result.map(item => item.messageId), ['newer', 'older']);
  assert.equal(result[0].label, 'Línén <img src=x onerror=alert(1)> stays literal.');
  assert.equal(result[0].description, 'Jump to message');
  assert.equal(search('linen', { messages, hasActiveChannel: false }).some(item => item.kind === 'message'), false);
  assert.equal(search('unavailable-history', { messages }).length, 0);
});

test('message excerpts reveal a matching passage in long content and cap suggestions to the latest six matches', () => {
  const messages = Array.from({ length: 10 }, (_, index) => ({
    id: String(index), author_id: 'member', created_at: '2026-10-20T10:00:00Z',
    content: `${'An earlier paragraph. '.repeat(30)} We are choosing limestone for the studio. ${'More details. '.repeat(30)}`,
  }));
  const result = search('limestone studio', { messages }).filter(item => item.kind === 'message');
  assert.deepEqual(result.map(item => item.messageId), ['9', '8', '7', '6', '5', '4']);
  assert.ok(result[0].label.includes('limestone'));
  assert.ok(result[0].label.startsWith('…'));
  assert.ok(result[0].label.endsWith('…'));
  assert.ok(result[0].label.length <= 152);
});

test('searching does not mutate the authenticated view data', () => {
  const input = { ...options, messages: [{ id: 'one', content: 'Notes', author_id: 'member', created_at: '2026-10-20T10:00:00Z' }] };
  const original = JSON.stringify(input);
  searchCommands({ ...input, query: 'notes' });
  searchCommands({ ...input, query: '' });
  assert.equal(JSON.stringify(input), original);
});

test('keyboard navigation wraps both ways and has no selectable result for an empty list', () => {
  assert.equal(nextCommandIndex(0, -1, 4), 3);
  assert.equal(nextCommandIndex(3, 1, 4), 0);
  assert.equal(nextCommandIndex(-1, 1, 4), 0);
  assert.equal(nextCommandIndex(-1, -1, 4), 3);
  assert.equal(nextCommandIndex(0, 1, 0), -1);
  assert.equal(nextCommandIndex(0, -1, 1), 0);
});
