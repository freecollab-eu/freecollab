import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import * as Y from 'yjs';

function compile(name, dependencies = {}) {
  const source = readFileSync(new URL('../src/lib/' + name + '.ts', import.meta.url), 'utf8');
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const exports = {};
  vm.runInNewContext(output, { exports, Date, URL, Map, Set, JSON, Promise, Error,
    require: name => { assert.ok(name in dependencies, name); return dependencies[name]; } });
  return exports;
}
const model = compile('channel-return-summary'), since = Date.parse('2026-10-20T12:00:00Z');
const later = '2026-10-21T12:00:00Z';
const message = (id, extra = {}) => ({ id, channel_id: 'room', author_id: 'peer', content: 'Hello viewer, can you help?', encrypted: true, created_at: later, ...extra });

test('attention uses real selected mentions and authored reply parents, never prose or unrelated rooms', () => {
  const rows = [message('guess'), message('mention', { mentions: ['viewer'] }), message('parent', { author_id: 'viewer', created_at: '2026-10-19T00:00:00Z' }),
    message('reply', { parent_id: 'parent' }), message('missing-parent', { parent_id: 'not-loaded' }),
    message('other', { channel_id: 'other', mentions: ['viewer'] }), message('own', { author_id: 'viewer', mentions: ['viewer'] }),
    message('old', { mentions: ['viewer'], created_at: '2026-10-19T00:00:00Z' }), message('deleted', { mentions: ['viewer'], deleted: true }),
    message('locked', { mentions: ['viewer'], locked: true })];
  const group = model.finishReturnGroup(model.returnMessageAttention(rows, 'room', 'viewer', since));
  assert.equal(group.count, 2); assert.equal(group.incomplete, true);
  assert.deepEqual(Array.from(group.items, row => row.source.id).sort(), ['mention', 'reply']);
  assert.equal(model.returnTimestamp('2026-10-20T12:00:00'), since);
  assert.equal(model.returnTimestamp('2026-10-20T14:00:00+02:00'), since);
});

test('verified encrypted work excludes stale, archived, done, foreign assignment and conflicting records', () => {
  const row = (id, kind, fields = {}, extra = {}) => ({ id, kind, invalid: [], conflicts: false,
    selected: { timestamp: since + 1, archived: false, fields: { title: id, ...fields } }, ...extra });
  const view = { context: { matrix_user_id: '@fc_viewer:matrix.example' }, warnings: [], quarantined_event_ids: [], legacyRecords: [], records: [
    row('agreement', 'decision'), row('mine', 'task', { assignee_matrix_id: '@fc_viewer:matrix.example', status: 'doing' }),
    row('peer', 'task', { assignee_matrix_id: '@fc_peer:matrix.example', status: 'todo' }),
    row('done', 'task', { assignee_matrix_id: '@fc_viewer:matrix.example', status: 'done' }), row('conflict', 'decision', {}, { conflicts: true }),
    row('old', 'decision', {}, { selected: { timestamp: since - 1, archived: false, fields: { title: 'old' } } }),
  ] };
  const result = model.returnEncryptedWork(view, since);
  assert.equal(result.decisions.items.length, 1); assert.equal(result.attention.items.length, 1);
  assert.equal(result.attention.items[0].source.resourceKind, 'task'); assert.equal(result.decisions.incomplete, true);
});

function loaderFixture({ secure = false, hold } = {}) {
  const state = { token: 'account-a', current: true, requests: [], inspections: [], stopped: 0 };
  const channel = { id: 'room', workspace_id: 'work', secure };
  const api = { getAccessToken: () => state.token, getCurrentUserId: () => 'viewer', getChannel: async () => { state.channelReads=(state.channelReads||0)+1; if(state.revokeAtFinalRead && state.channelReads>1)throw Error('Current channel access removed'); return channel; },
    request: async (path, options) => { state.requests.push({ path, options }); if (hold) await hold;
      return { channel_id: 'room', since: '2026-10-20T12:00:00', groups: {}, incomplete: false }; } };
  const doc = new Y.Doc(); doc.getText('body').insert(0, 'A genuine changed passage');
  const update = Y.encodeStateAsUpdate(doc); doc.destroy();
  const snapshot = { context: { channel_id: 'room', workspace_id: 'work', connection_id: 'connection', matrix_user_id: '@fc_viewer:matrix.example' },
    records: [], legacyRecords: [{ id: 'shared-doc', kind: 'document', invalid: [], conflicts: false, root_event_id: '$root',
      selected: { timestamp: since - 1, archived: false, fields: { title: 'Creation title' } } }], warnings: [], quarantined_event_ids: [] };
  const secureClient = { handles: async () => true, getRecentMessages: async () => [message('$mention', { mentions: ['viewer'] })] };
  const loaded = compile('channel-return-loader', {
    './api': { api }, './secure-client': { getSecureMessaging: async () => secureClient },
    './shared-documents-api': { sharedDocumentsApi: { encryptedSummaries: async () => ({ items: [{ id: 'doc', title: 'Verified latest title', updated_at: later }], incomplete: false }) } },
    './collaboration-api': { collaborationApi: { encryptedDecisionSummaries: async () => ({ items: state.legacyDecisions || [], incomplete: false }) } },
    './encrypted-work-api': { EncryptedWorkClient: class {
      constructor(id, options) { state.inspections.push({ id, options }); }
      async inspect() { if (state.workError) throw Error('Locked work'); return { snapshot, events: [{ type: 'doc-update', timestamp: since + 5, content: { id: 'shared-doc', root_event_id: '$root', connection_id: 'connection', protocol: 'v2', update_base64: state.invalidUpdate ? new Uint8Array([255]) : update } }] }; }
      stop() { state.stopped++; }
    } },
    './federated-work-model': { WORK_DOCUMENT_UPDATE: 'doc-update', WORK_PROTOCOL: 'v1', WORK_PROTOCOL_V2: 'v2' },
    './federated-work-document': { decodeWorkUpdate: bytes => bytes }, yjs: Y, './channel-return-summary': model,
  });
  const input = { workspaceId: 'work', channel, baseline: { status: 'known', lastReadMessageId: 'read', readAt: '2026-10-20T12:00:00' },
    signal: new AbortController().signal, isCurrent: () => state.current };
  return { state, input, loaded };
}

test('native inspection sends the captured baseline without marking read and discards a late cross-channel result', async () => {
  let release; const hold = new Promise(resolve => release = resolve);
  const f = loaderFixture({ hold });
  const pending = f.loaded.loadChannelReturnSummary(f.input);
  await new Promise(resolve => setImmediate(resolve));
  f.state.current = false; release();
  await assert.rejects(pending, /conversation changed/);
  assert.equal(f.state.requests.length, 1);
  assert.match(f.state.requests[0].path, /return-summary\?since=2026-10-20T12%3A00%3A00/);
  assert.equal(f.state.requests[0].options.method, undefined);
});

test('encrypted summary uses bounded read-only projections and real inline Yjs edit metadata, never native fallback', async () => {
  const f = loaderFixture({ secure: true });
  const result = await f.loaded.loadChannelReturnSummary(f.input);
  assert.equal(result.secure, true); assert.equal(result.incomplete, true);
  assert.equal(result.groups.documents.count, 2); assert.equal(result.groups.attention.count, 1);
  assert.equal(result.groups.documents.items.find(row => row.source.kind === 'document').title, 'Verified latest title');
  assert.equal(result.groups.documents.items.find(row => row.source.kind === 'shared-work').source.id, 'shared-doc');
  assert.equal(f.state.requests.length, 0);
  assert.equal(f.state.inspections[0].options.readOnly, true); assert.equal(f.state.inspections[0].options.eventLimit, 200);
  assert.equal(f.state.stopped, 1);
});

test('locked encrypted work retains separately verified document metadata and labels missing history without native fallback', async () => {
  const f = loaderFixture({ secure: true }); f.state.workError = true;
  const result = await f.loaded.loadChannelReturnSummary(f.input);
  assert.equal(result.groups.documents.count, 1); assert.equal(result.groups.documents.incomplete, true);
  assert.equal(result.groups.decisions.incomplete, true); assert.equal(result.groups.attention.count, 1);
  assert.equal(f.state.requests.length, 0);
});
test('malformed encrypted Yjs edits are omitted from counts and leave an explicit document-history gap', async () => {
  const f = loaderFixture({ secure: true }); f.state.invalidUpdate = true;
  const result = await f.loaded.loadChannelReturnSummary(f.input);
  assert.equal(result.groups.documents.count, 1); assert.equal(result.groups.documents.incomplete, true);
  assert.equal(f.state.requests.length, 0);
});
test('existing encrypted decision domain is included with its exact legacy navigation identity', async () => {
  const f = loaderFixture({ secure: true });
  f.state.legacyDecisions = [{ id: 'legacy-decision', title: 'Verified old-protocol decision', revised_at: later }];
  const result = await f.loaded.loadChannelReturnSummary(f.input);
  assert.equal(result.groups.decisions.count, 1);
  assert.equal(result.groups.decisions.items[0].source.kind, 'decision');
  assert.equal(result.groups.decisions.items[0].source.id, 'legacy-decision');
  assert.equal(f.state.requests.length, 0);
});
test('encrypted summary reauthorizes current channel membership after asynchronous device projections', async () => {
  const f = loaderFixture({ secure: true }); f.state.revokeAtFinalRead = true;
  await assert.rejects(f.loaded.loadChannelReturnSummary(f.input), /access removed/);
  assert.equal(f.state.channelReads, 2); assert.equal(f.state.requests.length, 0); assert.equal(f.state.stopped, 1);
});
