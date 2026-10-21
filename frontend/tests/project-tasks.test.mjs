import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const task = overrides => ({ title: 'Private next step', description: 'Notes', status: 'todo', priority: 'normal', assignee_user_id: null,
  due_date: null, checklist: [{ id: 'one', title: 'Verify result', done: false }], file_ids: [], decision_ids: [], ...overrides });
const project = { title: 'Private launch', description: 'Project notes', color: 'sage' };
const plain = value => JSON.parse(JSON.stringify(value));

function fixture({ encrypted = true } = {}) {
  const state = { token: 'account-one', requests: [], events: [], pages: [], members: [{ user_id: 'member' }], files: [], decisions: [], tamper: null };
  const secure = {
    handles: async () => encrypted,
    sendEvent: async (channel, type, content) => { const event_id = '$event-' + state.events.length; state.events.push({ event_id, channel, type, content: plain(content) }); return { event_id }; },
    roomFor: async () => ({ roomId: '!room:test', c: { getUserId: () => '@fc_actor:test' } }),
    getDecryptedEvent: async (channel, id) => {
      const source = state.events.find(row => row.event_id === id);
      if (!source) throw new Error('Missing encrypted event');
      return { getRoomId: () => state.tamper?.room || '!room:test', getType: () => state.tamper?.type || source.type,
        isRedacted: () => state.tamper?.redacted || false, getSender: () => state.tamper?.sender || '@fc_actor:test',
        getContent: () => ({ ...source.content, ...state.tamper?.content }) };
    },
  };
  const api = { getAccessToken: () => state.token, getChannelMembers: async () => state.members,
    request: async (path, options) => {
      state.requests.push({ path, options });
      if (!options) return state.pages.shift() || [];
      const body = JSON.parse(options.body), event = state.events.find(row => row.event_id === body.matrix_event_id), kind = path.endsWith('/tasks') ? 'task' : 'project';
      return { id: body.id, kind, channel_id: 'channel', project_id: kind === 'task' ? 'project' : null,
        created_by: 'actor', revised_by: 'actor', created_at: '2026-10-21T00:00:00', revised_at: '2026-10-21T00:00:00',
        revision: body.expected_revision + 1, archived: body.archived, content_mode: event ? 'matrix' : 'server', matrix_event_id: event?.event_id || null,
        ...(!event ? { fields: body.fields } : {}) };
    },
  };
  const dependencies = { './api': { api }, './secure-client': { getSecureMessaging: async () => secure },
    './files-api': { listChannelFiles: async () => state.files }, './collaboration-api': { collaborationApi: { decisions: async () => state.decisions } } };
  const source = readFileSync(new URL('../src/lib/project-tasks-api.ts', import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  const exports = {};
  vm.runInNewContext(compiled, { exports, require: name => dependencies[name], Date, Set, Map, crypto: { randomUUID: () => 'draft' } });
  return { ...exports, state, secure, api };
}

test('task validation rejects hidden fields, duplicate checklist IDs and invalid dates', () => {
  const f = fixture();
  assert.equal(f.validateTask(task()).checklist.length, 1);
  for (const fields of [task({ due_date: '2026-02-30' }), task({ checklist: [{ id: 'one', title: 'A', done: false }, { id: 'one', title: 'B', done: true }] }), task({ unexpected: 'hidden' }), task({ file_ids: ['same', 'same'] })]) {
    assert.throws(() => f.validateTask(fields));
  }
  assert.throws(() => f.validateProject({ ...project, color: 'url(javascript:evil)' }));
});

test('secure project and task writes expose only opaque reference and revision metadata', async () => {
  const f = fixture();
  const result = await f.projectTasksApi.saveTask('channel', 'project', task(), undefined, { id: 'task' });
  assert.equal(result.fields.title, 'Private next step');
  const body = JSON.parse(f.state.requests[0].options.body);
  assert.deepEqual(body, { id: 'task', expected_revision: 0, archived: false, matrix_event_id: '$event-0' });
  assert.ok(!f.state.requests[0].options.body.includes('Private'));
  assert.equal(f.state.events[0].content.project_id, 'project');
  assert.equal(f.state.events[0].type, 'eu.freecollab.product.task');
  assert.equal(f.state.events[0].content.revision, 1);
});

test('encrypted revision verification rejects substituted room, sender, kind, ID and parent project', async () => {
  for (const tamper of [{ room: '!foreign:test' }, { sender: '@fc_other:test' }, { type: 'eu.freecollab.product.project' },
    { content: { id: 'other' } }, { content: { revision: 7 } }, { content: { project_id: 'foreign' } }, { content: { archived: true } }, { redacted: true }]) {
    const f = fixture(); f.state.tamper = tamper;
    const result = await f.projectTasksApi.saveTask('channel', 'project', task(), undefined, { id: 'task' });
    assert.equal(result.fields, undefined);
    assert.match(result.resolution_error, /verified/);
  }
});

test('binding failures never fall back to a native plaintext save', async () => {
  const f = fixture(); f.secure.handles = async () => { throw new Error('Binding unavailable'); };
  await assert.rejects(f.projectTasksApi.saveProject('channel', project), /Binding unavailable/);
  assert.equal(f.state.requests.length, 0); assert.equal(f.state.events.length, 0);
});

test('current member and linked resource checks run before sending an encrypted event', async () => {
  for (const fields of [task({ assignee_user_id: 'outsider' }), task({ file_ids: ['foreign-file'] }), task({ decision_ids: ['foreign-decision'] })]) {
    const f = fixture();
    await assert.rejects(f.projectTasksApi.saveTask('channel', 'project', fields));
    assert.equal(f.state.events.length, 0); assert.equal(f.state.requests.length, 0);
  }
});

test('an account change while preparing crypto prevents either account receiving a stale write', async () => {
  const f = fixture(); f.secure.handles = async () => { f.state.token = 'account-two'; return true; };
  await assert.rejects(f.projectTasksApi.saveProject('channel', project), /account changed/);
  assert.equal(f.state.requests.length, 0); assert.equal(f.state.events.length, 0);
});

test('native channel saves normalize snapshots and retain optimistic revision numbers', async () => {
  const f = fixture({ encrypted: false });
  const previous = { id: 'project', kind: 'project', revision: 4, archived: false, project_id: null };
  const result = await f.projectTasksApi.saveProject('channel', { ...project, title: '  Updated title  ' }, previous, { archived: true });
  assert.equal(result.revision, 5); assert.equal(result.fields.title, 'Updated title');
  assert.equal(JSON.parse(f.state.requests[0].options.body).expected_revision, 4);
  assert.equal(f.state.events.length, 0);
});

test('task pages are combined without exposing partially resolved rows after logout', async () => {
  const f = fixture({ encrypted: false });
  const row = id => ({ id, kind: 'task', channel_id: 'channel', project_id: 'project', revision: 1, archived: false, content_mode: 'server', fields: task() });
  f.state.pages.push({ items: [row('one')], next_offset: 200 }, { items: [row('two')], next_offset: null });
  assert.deepEqual(plain((await f.projectTasksApi.tasks('channel', 'project')).map(item => item.id)), ['one', 'two']);
  const original = f.api.request;
  f.api.request = async (...args) => { const result = await original(...args); f.state.token = 'account-two'; return result; };
  f.state.pages.push([row('one')]);
  await assert.rejects(f.projectTasksApi.projects('channel'), /account changed/);
});
