import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const compile = path => ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const model = {};
vm.runInNewContext(compile('../src/lib/message-references.ts'), { exports: model });
const plain = value => JSON.parse(JSON.stringify(value));
function fixture(respond, secureImports = {}) {
  let token = 'owner-token', actor = 'owner';
  const listeners = new Map(), requests = [], exports = {};
  const api = { getAccessToken: () => token, getCurrentUserId: () => actor, request: async (path, options = {}) => { requests.push({ path, options }); return respond(path, options); } };
  vm.runInNewContext(compile('../src/lib/message-references-api.ts'), {
    exports, URLSearchParams, DOMException,
    window: { addEventListener(name, listener) { listeners.set(name, listener); } },
    require(name) {
      const imports = { './api': { api }, './message-references': model,
        './secure-client': { getSecureMessaging: async () => ({ handles: async () => false }) },
        './shared-documents-api': {}, './project-tasks-api': {}, './files-api': {}, ...secureImports,
      };
      assert.ok(Object.hasOwn(imports, name), name); return imports[name];
    },
  });
  return { client: exports.messageReferencesApi, requests, rotate() { token = 'other-token'; actor = 'other'; }, workspace() { listeners.get('freecollab:workspace-changed')(); } };
}
const person = { kind: 'person', id: 'ada', label: 'Ada', channel_id: 'room' };

test('suggestions send a bounded encoded query and retain genuine typed candidate identities', async () => {
  const f = fixture(() => [person, { ...person }, { kind: 'document', id: 'ada', label: 'Ada notes', channel_id: 'room' }]);
  const rows = await f.client.suggestions('room', 'å team?');
  assert.deepEqual(plain(rows).map(row => [row.kind, row.id]), [['person', 'ada'], ['document', 'ada']]);
  assert.equal(f.requests[0].path, '/mentions/channels/room/suggestions?query=%C3%A5+team%3F');
});

test('late account or workspace replies cannot publish another scope\'s names', async () => {
  for (const action of ['rotate', 'workspace']) {
    let complete; const f = fixture(() => new Promise(resolve => { complete = resolve; }));
    const result = f.client.suggestions('room', 'Ada'); f[action](); complete([person]);
    await assert.rejects(result, /account or workspace changed/);
  }
});

test('an aborted suggestion request never reaches the API', async () => {
  const f = fixture(() => [person]), abort = new AbortController(); abort.abort();
  await assert.rejects(f.client.suggestions('room', '', abort.signal), error => error.name === 'AbortError');
  assert.equal(f.requests.length, 0);
});

test('substituted channel names and malformed IDs are rejected before display', async () => {
  for (const response of [[{ ...person, channel_id: 'private-other-room' }], [{ ...person, id: 'bad id' }], [{ ...person, label: 'Ada\nprivate text' }]]) {
    const f = fixture(() => response); await assert.rejects(f.client.suggestions('room'), /not available/);
  }
});

test('resolve is ID-only, deduplicated and preserves unavailable references without labels', async () => {
  const f = fixture(() => ({ items: [{ ...person, available: true, user_ids: ['ada', 'ada'] }, { kind: 'document', id: 'missing', available: false }] }));
  const rows = await f.client.resolve('room', [{ kind: 'person', id: 'ada' }, { kind: 'person', id: 'ada' }, { kind: 'document', id: 'missing' }]);
  assert.deepEqual(JSON.parse(f.requests[0].options.body), { references: [{ kind: 'person', id: 'ada' }, { kind: 'document', id: 'missing' }] });
  assert.deepEqual(plain(rows[0].user_ids), ['ada']);
  assert.equal(rows[1].available, false); assert.equal(rows[1].label, '');
});

test('resolver cannot substitute targets or forge duplicate availability records', async () => {
  for (const response of [{ items: [{ ...person, id: 'other', available: true }] }, { items: [{ ...person, available: true }, { ...person, available: true }] }]) {
    const f = fixture(() => response); await assert.rejects(f.client.resolve('room', [{ kind: 'person', id: 'ada' }]), /Invalid reference response/);
  }
});

test('encrypted labels never fall back to unverified plaintext server titles', async () => {
  const f = fixture(() => ({ items: [{ kind: 'document', id: 'secret-doc', channel_id: 'room', available: true, label: 'Unverified server title' }] }));
  await assert.rejects(f.client.resolve('room', [{ kind: 'document', id: 'secret-doc' }], undefined, true), /could not be verified/);
});

test('encrypted Matrix file targets stay on device and historical versions use the verified file index', async () => {
  const checked = [];
  const f = fixture(() => ({ items: [{ ...person, available: true }] }), {
    './secure-client': { getSecureMessaging: async () => ({ handles: async id => id === 'room' }) },
    './shared-documents-api': { sharedDocumentsApi: {} },
    './project-tasks-api': { projectTasksApi: {} },
    './files-api': { listChannelFiles: async () => [{ id: '$latest:matrix', channel_id: 'room', filename: 'Current.pdf' }] },
    './secure/files': { verifiedSecureFileReferenceIds: async (channelId, ids) => { checked.push([channelId, ids]); return new Set(['$old:matrix']); } },
  });
  const rows = await f.client.resolve('room', [{ kind: 'person', id: 'ada' }, { kind: 'file', id: '$latest:matrix' }, { kind: 'file', id: '$old:matrix' }, { kind: 'file', id: '$deleted:matrix' }], undefined, true);
  assert.deepEqual(JSON.parse(f.requests[0].options.body), { references: [{ kind: 'person', id: 'ada' }] });
  assert.deepEqual(plain(rows).map(row => [row.id, row.available, row.label]), [['ada', true, 'Ada'], ['$latest:matrix', true, 'Current.pdf'], ['$old:matrix', true, 'Encrypted file'], ['$deleted:matrix', false, '']]);
  assert.deepEqual(plain(checked), [['room', ['$old:matrix', '$deleted:matrix']]]);
  const filesOnly = fixture(() => { throw new Error('Native resolver must not receive Matrix file targets'); }, {
    './secure-client': { getSecureMessaging: async () => ({ handles: async () => true }) },
    './shared-documents-api': { sharedDocumentsApi: {} }, './project-tasks-api': { projectTasksApi: {} },
    './files-api': { listChannelFiles: async () => [{ id: '$latest:matrix', channel_id: 'room', filename: 'Current.pdf' }] },
  });
  assert.equal((await filesOnly.client.resolve('room', [{ kind: 'file', id: '$latest:matrix' }], undefined, true))[0].available, true);
  assert.equal(filesOnly.requests.length, 0);
});

test('encrypted resource suggestions show only verified local titles', async () => {
  const f = fixture(() => [person, { kind: 'document', id: 'server-only', channel_id: 'room', label: 'Unverified server title' }], {
    './secure-client': { getSecureMessaging: async () => ({ handles: async () => true }) },
    './shared-documents-api': { sharedDocumentsApi: { list: async () => [{ id: 'verified-doc', channel_id: 'room', title: 'Verified local title', archived: false }] } },
    './project-tasks-api': { projectTasksApi: { projects: async () => [] } },
    './files-api': { listChannelFiles: async () => [] },
  });
  assert.deepEqual(plain(await f.client.suggestions('room', '', undefined, true)).map(row => [row.id, row.label]), [['ada', 'Ada'], ['verified-doc', 'Verified local title']]);
});

test('group mutation uses current channel and explicit version checks, never a name-derived audience', async () => {
  const group = { id: 'design', channel_id: 'room', name: 'Design', member_ids: ['ada'], version: 2 };
  const f = fixture(path => path.includes('?expected_version=') ? undefined : group);
  await f.client.updateGroup('room', 'design', { expected_version: 1, name: 'Design', member_ids: ['ada'] });
  await f.client.removeGroup('room', 'design', 2);
  assert.deepEqual(JSON.parse(f.requests[0].options.body), { expected_version: 1, name: 'Design', member_ids: ['ada'] });
  assert.equal(f.requests[1].path, '/mentions/channels/room/groups/design?expected_version=2');
  assert.equal(f.requests[1].options.method, 'DELETE');
});

test('a group from another channel or substituted identity is not accepted after saving', async () => {
  const f = fixture(() => ({ id: 'other', channel_id: 'room', name: 'Design', member_ids: ['ada'], version: 2 }));
  await assert.rejects(f.client.updateGroup('room', 'design', { expected_version: 1, name: 'Design' }), /no longer available/);
  const other = fixture(() => [{ id: 'design', channel_id: 'other-room', name: 'Design', member_ids: ['ada'], version: 1 }]);
  await assert.rejects(other.client.groups('room'), /not available/);
});
