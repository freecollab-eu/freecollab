import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const ids = ['first_message', 'file_share', 'invite_team', 'connect_service', 'find_content'];
const clone = value => JSON.parse(JSON.stringify(value));
const record = (change = {}) => ({ task: 'first_message', outcome: 'completed', rating: 4, note: 'Only my private note', revision: 3, created_at: '2026-10-21T12:00:00Z', updated_at: '2026-10-21T13:00:00Z', ...change });
const own = () => ({ workspace_id: 'workspace', can_manage: true, self_reported: true, tasks: ids.map(id => ({ id, title: id, instructions: 'Try this optional task.' })), revisions: Object.fromEntries(ids.map(id => [id, id === 'first_message' ? 3 : 0])), feedback: [record()] });
const summary = () => ({ workspace_id: 'workspace', self_reported: true, pilot_verdict: null, distinct_participants: 2, feedback_count: 2, notes_included: false,
  tasks: ids.map((task, index) => ({ task, completed: index === 0 ? 1 : 0, blocked: index === 1 ? 1 : 0, rating_count: index < 2 ? 1 : 0, rating_total: index === 0 ? 4 : index === 1 ? 2 : 0 })) });
function fixture() {
  const state = { token: 'native-session', user: 'current-member', requests: [], response: own(), beforeResponse: null };
  const api = { getAccessToken: () => state.token, getCurrentUserId: () => state.user, request: async (path, options) => { state.requests.push({ path, options }); await state.beforeResponse?.(); return state.response; } };
  const source = readFileSync(new URL('../src/lib/pilot-feedback-api.ts', import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  const exports = {};
  vm.runInNewContext(compiled, { exports, Set, require: name => { if (name !== './api') throw new Error('Unexpected dependency'); return { api }; } });
  return { ...exports, state };
}

test('own feedback reads are scoped, abortable, read only and not cached', async () => {
  const f = fixture(), signal = new AbortController().signal;
  f.state.response = { ...own(), workspace_id: 'workspace /?' };
  await f.pilotFeedbackApi.get('workspace /?', signal);
  assert.equal(f.state.requests[0].path, '/workspaces/workspace%20%2F%3F/pilot-feedback');
  assert.equal(f.state.requests[0].options.signal, signal); assert.equal(f.state.requests[0].options.cache, 'no-store'); assert.equal(f.state.requests[0].options.method, undefined);
});

test('own parser strips uncontracted identity and credential fields while retaining only own note fields', () => {
  const f = fixture(), input = own();
  input.user_id = 'ANOTHER-USER'; input.token = 'NATIVE-SECRET'; input.feedback[0].email = 'PRIVATE-EMAIL'; input.tasks[0].provider_secret = 'PROVIDER-SECRET';
  const result = JSON.stringify(f.parsePilotOwn(input, 'workspace'));
  assert.ok(result.includes('Only my private note'));
  for (const secret of ['ANOTHER-USER', 'NATIVE-SECRET', 'PRIVATE-EMAIL', 'PROVIDER-SECRET']) assert.ok(!result.includes(secret));
});

test('cross-workspace, malformed tasks, duplicate rows and revision substitutions are rejected', () => {
  const f = fixture();
  for (const change of [value => { value.workspace_id = 'outsider'; }, value => { value.self_reported = false; }, value => { value.can_manage = 'true'; },
    value => { value.tasks.reverse(); }, value => { value.tasks.pop(); }, value => { value.tasks[0].id = 'unknown'; },
    value => { value.feedback.push(record()); }, value => { value.feedback[0].revision = 2; }, value => { value.revisions.first_message = true; }]) {
    const value = own(); change(value); assert.throws(() => f.parsePilotOwn(value, 'workspace'), /invalid or mismatched/);
  }
});

test('tombstones survive own reads and a new deliberate report uses the retained revision', async () => {
  const f = fixture(), value = own(); value.feedback = []; value.revisions.first_message = 4;
  const parsed = f.parsePilotOwn(value, 'workspace');
  assert.equal(parsed.revisions.first_message, 4); assert.equal(parsed.feedback.length, 0);
  f.state.response = record({ revision: 5 });
  await f.pilotFeedbackApi.save('workspace', 'first_message', { outcome: 'completed', rating: '4', note: ' A new voluntary note ' }, parsed.revisions.first_message, true);
  assert.deepEqual(JSON.parse(f.state.requests[0].options.body), { expected_revision: 4, outcome: 'completed', rating: 4, note: 'A new voluntary note', consent: true });
});

test('save requires explicit true consent and strict valid values before any request', async () => {
  const f = fixture(), draft = { outcome: 'completed', rating: '', note: '' };
  for (const consent of [false, undefined, null, 1, 'yes']) await assert.rejects(f.pilotFeedbackApi.save('workspace', 'first_message', draft, 0, consent), /explicitly agree/);
  for (const changed of [{ ...draft, outcome: '' }, { ...draft, outcome: 'observed_success' }, { ...draft, rating: '1.0' }, { ...draft, rating: '6' }, { ...draft, note: 'x'.repeat(1001) }]) await assert.rejects(f.pilotFeedbackApi.save('workspace', 'first_message', changed, 0, true));
  for (const revision of [true, -1, 0.5, Infinity, Number.MAX_SAFE_INTEGER + 1]) await assert.rejects(f.pilotFeedbackApi.save('workspace', 'first_message', draft, revision, true));
  assert.equal(f.state.requests.length, 0);
});

test('save body is a whitelist and never includes caller-provided ownership or channel contents', async () => {
  const f = fixture(); f.state.response = record({ revision: 1, rating: null, note: null });
  await f.pilotFeedbackApi.save('workspace', 'first_message', { outcome: 'completed', rating: '', note: '', user_id: 'other', workspace_id: 'other', channel_id: 'private-channel', access_token: 'secret' }, 0, true);
  const request = f.state.requests[0]; assert.equal(request.options.method, 'PUT');
  assert.deepEqual(JSON.parse(request.options.body), { expected_revision: 0, outcome: 'completed', rating: null, note: null, consent: true });
});

test('save response must bind the selected task and next exact revision', async () => {
  for (const changed of [{ task: 'file_share', revision: 4 }, { revision: 5 }]) {
    const f = fixture(); f.state.response = record(changed);
    await assert.rejects(f.pilotFeedbackApi.save('workspace', 'first_message', { outcome: 'completed', rating: '', note: '' }, 3, true), /invalid or mismatched/);
  }
});

test('delete uses only the selected own revision and retains the returned tombstone', async () => {
  const f = fixture(), signal = new AbortController().signal;
  f.state.response = { task: 'first_message', revision: 4, deleted: true, note: 'MUST-NOT-RETURN' };
  const result = await f.pilotFeedbackApi.remove('workspace', 'first_message', 3, signal);
  assert.equal(f.state.requests[0].path, '/workspaces/workspace/pilot-feedback/first_message?expected_revision=3');
  assert.equal(f.state.requests[0].options.method, 'DELETE'); assert.equal(f.state.requests[0].options.signal, signal);
  assert.deepEqual(clone(result), { task: 'first_message', revision: 4, deleted: true });
});

test('delete response cannot substitute a task, verdict or stale tombstone', async () => {
  for (const response of [{ task: 'file_share', revision: 4, deleted: true }, { task: 'first_message', revision: 3, deleted: true }, { task: 'first_message', revision: 4, deleted: false }]) {
    const f = fixture(); f.state.response = response; await assert.rejects(f.pilotFeedbackApi.remove('workspace', 'first_message', 3), /invalid or mismatched/);
  }
});

test('all awaited operations discard responses after account replacement or logout', async () => {
  for (const operation of ['get', 'summary', 'save', 'remove']) {
    const f = fixture(); f.state.beforeResponse = () => { f.state.token = null; f.state.user = 'different-user'; };
    const result = operation === 'save' ? f.pilotFeedbackApi.save('workspace', 'first_message', { outcome: 'completed', rating: '', note: '' }, 0, true)
      : operation === 'remove' ? f.pilotFeedbackApi.remove('workspace', 'first_message', 3) : f.pilotFeedbackApi[operation]('workspace');
    await assert.rejects(result, /account changed/);
  }
});

test('signed-out helpers never issue feedback requests', async () => {
  const f = fixture(); f.state.token = null;
  await assert.rejects(f.pilotFeedbackApi.get('workspace'), /Sign in again/); assert.equal(f.state.requests.length, 0);
});

test('aggregate parsing excludes participant identities, notes, individual ratings and server extras', () => {
  const f = fixture(), value = summary(); value.participants = ['PRIVATE-USER']; value.notes = ['PRIVATE-NOTE']; value.tasks[0].ratings = [4]; value.secret = 'SECRET';
  const result = JSON.stringify(f.parsePilotSummary(value, 'workspace'));
  for (const secret of ['PRIVATE-USER', 'PRIVATE-NOTE', 'ratings', 'SECRET']) assert.ok(!result.includes(secret));
  assert.equal(f.parsePilotSummary(value, 'workspace').pilot_verdict, null);
});

test('aggregate cannot invent a verdict or accept inconsistent participant, task or rating counts', () => {
  const f = fixture();
  for (const change of [value => { value.workspace_id = 'outsider'; }, value => { value.self_reported = false; }, value => { value.pilot_verdict = 'passed'; }, value => { value.notes_included = true; },
    value => { value.distinct_participants = 0; }, value => { value.distinct_participants = 3; }, value => { value.feedback_count = 4; },
    value => { value.tasks[0].rating_count = 2; }, value => { value.tasks[0].rating_total = 6; }, value => { value.tasks[0].rating_total = 0; }, value => { value.tasks[0].completed = 3; }, value => { value.tasks.reverse(); }]) {
    const value = summary(); change(value); assert.throws(() => f.parsePilotSummary(value, 'workspace'), /invalid or mismatched/);
  }
  const empty = summary(); empty.distinct_participants = 0; empty.feedback_count = 0; empty.tasks = ids.map(task => ({ task, completed: 0, blocked: 0, rating_count: 0, rating_total: 0 }));
  assert.equal(f.parsePilotSummary(empty, 'workspace').distinct_participants, 0);
});
