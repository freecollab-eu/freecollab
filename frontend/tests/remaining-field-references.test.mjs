import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { fieldReferences, resourceReferenceTargets } from './load-field-references.mjs';
const plain = value => JSON.parse(JSON.stringify(value));
const refs = { note: [{ kind: 'document', id: 'actual-plan', start: 2, end: 7 }] };
function workflowFixture(encrypted) {
  const state = { token: 'session', requests: [], events: [], available: true, rotate: false };
  const run = { id: 'run', channel_id: 'channel', step_index: 1, votes: [] };
  const secure = { handles: async () => encrypted, roomFor: async () => ({ c: { getUserId: () => '@fc_actor:test' }, roomId: '!room:test' }), sendEvent: async (_, type, content) => { state.events.push({ type, content: plain(content) }); return { event_id: '$vote' }; }, getDecryptedEvent: async () => ({ getRoomId: () => '!room:test', getType: () => 'eu.freecollab.workflow.vote', isRedacted: () => false, getSender: () => '@fc_actor:test', getContent: () => state.events.at(-1).content }) };
  const api = { getAccessToken: () => state.token, request: async (_, options) => { const body = JSON.parse(options.body); state.requests.push(body); return { ...run, votes: [{ user_id: 'actor', step_index: 1, choice: body.choice, note: body.note || null, ...(body.field_references ? { field_references: body.field_references } : {}), matrix_event_id: body.matrix_event_id || null }] }; } };
  const dependencies = { './api': { api }, './field-references': fieldReferences, './secure-client': { getSecureMessaging: async () => secure }, './resource-reference-targets': resourceReferenceTargets(api, async (_, refs) => { if (state.rotate) state.token = 'different'; return refs.map(ref => ({ ...ref, available: state.available })); }) };
  const exports = {}, source = readFileSync(new URL('../src/lib/workflows-api.ts', import.meta.url), 'utf8');
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText, { exports, require: name => dependencies[name] });
  return { ...exports, state, run };
}
for (const encrypted of [false, true]) test(`workflow approval note preserves actual IDs and exact text ${encrypted ? 'inside Matrix' : 'in native encrypted storage'}`, async () => {
  const f = workflowFixture(encrypted), saved = await f.workflowsApi.vote('workspace', 'channel', f.run, 'approve', '  @Plan  ', refs);
  assert.equal(saved.votes[0].note, '  @Plan  '); assert.deepEqual(plain(saved.votes[0].field_references), refs);
  if (encrypted) { assert.deepEqual(f.state.requests[0], { choice: 'approve', matrix_event_id: '$vote' }); assert.deepEqual(f.state.events[0].content.field_references, refs); }
  else assert.deepEqual(f.state.requests[0], { choice: 'approve', note: '  @Plan  ', field_references: refs });
});
test('workflow unavailable targets and credential rotation stop publication before encrypted/native writes', async () => {
  for (const rotate of [false, true]) {
    const f = workflowFixture(true); f.state.available = false; f.state.rotate = rotate;
    await assert.rejects(f.workflowsApi.vote('workspace', 'channel', f.run, 'reject', '  @Plan  ', refs));
    assert.equal(f.state.events.length, 0); assert.equal(f.state.requests.length, 0);
  }
});
test('resource target validation requires every actual selected ID, retains exact historical fields, and checks current authority', async () => {
  let token = 'session', queried = 0;
  const api = { getAccessToken: () => token }, helper = resourceReferenceTargets(api, async () => { queried++; return []; });
  const metadata = { title: [{ kind: 'person', id: 'actual-person', start: 0, end: 4 }] };
  await helper.validateResourceReferenceTargets('channel', { title: '@Ada' }, metadata, token, false, { title: '@Ada' }, metadata); assert.equal(queried, 0);
  await assert.rejects(helper.validateResourceReferenceTargets('channel', { title: '@Ada' }, metadata, token, false), /no longer available/);
  token = 'different'; await assert.rejects(helper.validateResourceReferenceTargets('channel', { title: '@Ada' }, metadata, 'session', true), /account changed/);
});

test('scoped collaboration inputs require the exact active provider channel and preserve existing controlled metadata', () => {
  let context = null;
  const exports = {}, source = readFileSync(new URL('../src/components/ScopedCollaborationInput.tsx', import.meta.url), 'utf8');
  const jsx = (type, props) => ({ type, props });
  const dependencies = { react: { forwardRef: callback => props => callback(props, null) }, 'react/jsx-runtime': { jsx }, './CollaborationInput': { CollaborationInput: 'actual-shared-input' }, '../lib/collaboration-context': { useCollaborationContext: () => context } };
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX } }).outputText, { exports, require: name => dependencies[name] });
  const props = { channelId: 'draft-channel', contextId: 'private-draft', value: '@Plan', references: [{ kind: 'document', id: 'retained-id', start: 0, end: 5 }], onValueChange() {} };
  for (const value of [null, { channelId: 'another-channel' }]) { context = value; const rendered = exports.ScopedCollaborationInput(props); assert.equal(rendered.props.allowCommands, false); assert.equal(rendered.props.allowReferences, false); assert.equal(rendered.props.references, props.references); }
  context = { channelId: 'draft-channel' }; const own = exports.ScopedCollaborationInput(props); assert.equal(own.props.allowCommands, true); assert.equal(own.props.allowReferences, true);
  const limited = exports.ScopedCollaborationInput({ ...props, allowReferences: false }); assert.equal(limited.props.allowReferences, false); assert.equal(limited.props.allowCommands, true);
});
