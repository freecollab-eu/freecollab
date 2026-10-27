import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const exports = {};
vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/lib/document-organization-assist.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, { exports });
const { suggestDocumentStack } = exports;
const document = (id, title, extra = {}) => ({ id, title, channel_id: 'room', archived: false, version: 1, updated_at: 'now', ...extra });
const stack = (id, title, extra = {}) => ({ id, title, channel_id: 'room', version: 1, generation: id + '-generation', ...extra });
function organization() {
  return { supported: true, documents: [document('source', 'Research brief'), document('peer', 'Research evidence')],
    stacks: [stack('research', 'Source material'), stack('budget', 'Budget')],
    placements: [{ document_id: 'peer', channel_id: 'room', stack_id: 'research', version: 1 }],
    relations: [], message_references: [], stack_comments: [], comments_incomplete: false };
}

test('one local proposal uses actual visible stack membership and current title evidence', () => {
  const data = organization(), proposal = suggestDocumentStack(data.documents[0], data);
  assert.equal(proposal.stackId, 'research'); assert.equal(proposal.stackTitle, 'Source material');
  assert.deepEqual(Array.from(proposal.terms), ['research']);
  assert.equal(suggestDocumentStack(data.documents[0], { ...data, placements: [] }), null);
  assert.equal(suggestDocumentStack(data.documents[0], { ...data, placements: [...data.placements, { document_id: 'source', channel_id: 'room', stack_id: 'research', version: 2 }] }), null);
});

test('suggestions exclude foreign, archived, unresolved, conflicted and ambiguous evidence', () => {
  for (const extra of [{ channel_id: 'other' }, { archived: true }, { resolution_error: true }]) {
    const data = organization(); data.documents[1] = { ...data.documents[1], ...extra };
    assert.equal(suggestDocumentStack(data.documents[0], data), null);
  }
  const data = organization();
  assert.equal(suggestDocumentStack(data.documents[0], { ...data, organization_conflicts: [{ event_id: 'unreviewed' }] }), null);
  assert.equal(suggestDocumentStack({ ...data.documents[0], archived: true }, data), null);
  data.stacks.push(stack('equal', 'Other source material')); data.documents.push(document('equal-peer', 'Research draft'));
  data.placements.push({ document_id: 'equal-peer', channel_id: 'room', stack_id: 'equal', version: 1 });
  assert.equal(suggestDocumentStack(data.documents[0], data), null);
});

test('visible unresolved comments may ground a proposal, and every reviewed revision participates in freshness', () => {
  const data = organization(); data.documents[0].title = 'Working brief';
  const comment = { id: 'comment', channel_id: 'room', document_id: 'source', text: 'Check the research evidence', version: 1, resolved: false };
  data.stack_comments.push(comment);
  const proposal = suggestDocumentStack(data.documents[0], data); assert.equal(proposal.stackId, 'research');
  comment.version++; assert.notEqual(suggestDocumentStack(data.documents[0], data).identity, proposal.identity);
  assert.equal(suggestDocumentStack(data.documents[0], data).dismissKey, proposal.dismissKey, 'a dismissed destination stays quiet after polling');
  comment.resolved = true; assert.equal(suggestDocumentStack(data.documents[0], data), null);
  comment.resolved = false; comment.channel_id = 'other'; assert.equal(suggestDocumentStack(data.documents[0], data), null);
});

function acceptingFixture(data) {
  const source = readFileSync(new URL('../src/components/SharedDocumentOrganization.tsx', import.meta.url), 'utf8');
  const tree = ts.createSourceFile('component.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let callback;
  function visit(node) { if (ts.isFunctionDeclaration(node) && node.name?.text === 'acceptStackSuggestion') callback = node.getText(tree); ts.forEachChild(node, visit); }
  visit(tree); assert.ok(callback);
  const state = { current: true, moves: [], undo: [], notices: [], loaded: 0, fresh: structuredClone(data) };
  const context = { exports: {}, channelId: 'room', archived: false, suggestDocumentStack, t: value => value,
    current: () => state.current, run: action => { state.pending = action(1); },
    organizationApi: { list: async () => state.fresh, move: async (...args) => { state.moves.push(args); return { version: 2 }; } },
    setData: value => { state.fresh = value; }, setNotice: value => state.notices.push(value), setArrangingId() {},
    setUndo: value => state.undo.push(value), load: async () => { state.loaded++; },
  };
  vm.runInNewContext(ts.transpileModule(callback + '\nexports.accept = acceptStackSuggestion;', { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, context);
  return { state, accept: context.exports.accept };
}

test('accept rechecks the exact current evidence before issuing a metadata-only CAS move', async () => {
  const data = organization(), proposal = suggestDocumentStack(data.documents[0], data), f = acceptingFixture(data);
  f.accept(proposal); await f.state.pending;
  assert.deepEqual(f.state.moves, [['room', 'source', 'research', 0, 'research-generation']]);
  assert.equal(f.state.undo[0].kind, 'move'); assert.equal(f.state.undo[0].receipt.expectedVersion, 2); assert.equal(f.state.loaded, 1);
  for (const change of [fresh => { fresh.documents[0].version++; }, fresh => { fresh.stacks[0].generation = 'replacement'; }, fresh => { fresh.documents.pop(); }]) {
    const stale = acceptingFixture(data); change(stale.state.fresh); stale.accept(proposal); await stale.state.pending;
    assert.equal(stale.state.moves.length, 0); assert.equal(stale.state.undo.length, 0); assert.equal(stale.state.notices.length, 1);
  }
  const changedAccount = acceptingFixture(data); changedAccount.state.current = false; changedAccount.accept(proposal); await changedAccount.state.pending;
  assert.equal(changedAccount.state.moves.length, 0); assert.equal(changedAccount.state.undo.length, 0);
});
