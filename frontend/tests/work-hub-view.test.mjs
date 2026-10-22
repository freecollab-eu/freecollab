import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const exports = {};
vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/lib/work-hub-view.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText, { exports });
const { createWorkHubViewMemory } = exports;
const scope = { userId: 'native-member', credential: 'current-account-session', workspaceId: 'studio', channelId: 'courtyard', secure: false };

test('returning to work recalls each channel view without selecting another channel', () => {
  const memory = createWorkHubViewMemory();
  assert.equal(memory.recall(scope), 'tasks');
  memory.remember(scope, 'documents');
  assert.equal(memory.recall({ ...scope, channelId: 'planning' }), 'tasks');
  memory.remember({ ...scope, channelId: 'planning' }, 'meetings');
  assert.equal(memory.recall(scope), 'documents');
  assert.equal(memory.recall({ ...scope, channelId: 'planning' }), 'meetings');
});

test('account, credential and workspace changes purge previous navigation choices', () => {
  for (const changes of [{ userId: 'another-member' }, { credential: 'renewed-session' }, { workspaceId: 'another-studio' }]) {
    const memory = createWorkHubViewMemory();
    memory.remember(scope, 'documents');
    const other = { ...scope, ...changes };
    assert.equal(memory.recall(other), 'tasks');
    memory.remember(other, 'feed');
    assert.equal(memory.recall(other), 'feed');
    assert.equal(memory.recall(scope), 'tasks');
  }
});

test('anonymous or incomplete scopes never retain navigation across sign-out', () => {
  for (const changes of [{ userId: null }, { credential: null }, { workspaceId: undefined }, { channelId: '' }]) {
    const memory = createWorkHubViewMemory();
    memory.remember(scope, 'documents');
    memory.remember({ ...scope, ...changes }, 'feed');
    assert.equal(memory.recall({ ...scope, ...changes }), 'tasks');
    assert.equal(memory.recall(scope), 'tasks');
  }
});

test('changing channel encryption mode invalidates its view and cannot restore a shared-work tab into a native channel', () => {
  const memory = createWorkHubViewMemory(), secure = { ...scope, secure: true };
  memory.remember(secure, 'shared-work');
  assert.equal(memory.recall(secure), 'shared-work');
  assert.equal(memory.recall(scope), 'tasks');
  assert.equal(memory.recall(secure), 'tasks');
  memory.remember(scope, 'shared-work');
  assert.equal(memory.recall(scope), 'tasks');
  memory.remember(scope, 'documents');
  assert.equal(memory.recall(scope), 'documents');
});

test('only known tool selections are remembered and explicit clearing removes every channel', () => {
  const memory = createWorkHubViewMemory();
  memory.remember(scope, 'documents');
  memory.remember(scope, 'unregistered-tool');
  assert.equal(memory.recall(scope), 'documents');
  memory.remember({ ...scope, channelId: 'planning' }, 'feed');
  memory.clear();
  assert.equal(memory.recall(scope), 'tasks');
  assert.equal(memory.recall({ ...scope, channelId: 'planning' }), 'tasks');
});

test('view memory is bounded to the 24 most recently used channels', () => {
  const memory = createWorkHubViewMemory();
  for (let index = 0; index < 24; index++) memory.remember({ ...scope, channelId: 'channel-' + index }, 'documents');
  assert.equal(memory.recall({ ...scope, channelId: 'channel-0' }), 'documents');
  memory.remember({ ...scope, channelId: 'channel-24' }, 'feed');
  assert.equal(memory.recall({ ...scope, channelId: 'channel-1' }), 'tasks');
  assert.equal(memory.recall({ ...scope, channelId: 'channel-0' }), 'documents');
  assert.equal(memory.recall({ ...scope, channelId: 'channel-24' }), 'feed');
});
