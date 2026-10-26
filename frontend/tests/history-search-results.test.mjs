import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

// Execute the actual panel projection; the full-App browser retains its existing
// 122-message pagination, exact result count, live edit/delete and mention checks.
const exports = {};
vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/components/HistorySearchPanel.tsx', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX },
}).outputText, { exports, require: () => ({}), Map, Set, JSON });
const { historySupplementalSources, historySourceKey, historySourceLabel } = exports;
const source = (kind, id = 'same-id', fields = {}) => ({ kind, id, channel_id: 'channel', title: 'Current title', text: 'Earlier searchable text', created_by: 'actor', created_at: '2026-10-26', version: 'one', ...fields });

test('full-history message and decision projections remain authoritative after edits and deletion', () => {
  const cached = [source('message'), source('decision'), source('document', 'page')];
  // A deleted canonical row is absent, so deduplicating only existing IDs would
  // resurrect this old cached body. These families never supply fallback rows.
  assert.deepEqual(Array.from(historySupplementalSources(cached), row => row.kind), ['document']);
  assert.deepEqual(Array.from(historySupplementalSources(cached, 'messages')), []);
  assert.deepEqual(Array.from(historySupplementalSources(cached, 'decisions')), []);
  assert.equal(cached.length, 3); assert.equal(cached[0].text, 'Earlier searchable text');
});

test('genuine device-verified peer decisions retain the Decisions facet while legacy and partial flags cannot masquerade as peers', () => {
  const peer = source('decision', 'connection:creator:peer', { secure: true, encrypted_resource: true, encrypted_kind: 'decision' });
  const candidates = [source('decision'), source('decision', 'only-secure', { secure: true }),
    source('decision', 'wrong-kind', { secure: true, encrypted_resource: true, encrypted_kind: 'feed' }),
    source('decision', 'missing-device-mode', { encrypted_resource: true, encrypted_kind: 'decision' }), peer];
  assert.deepEqual(Array.from(historySupplementalSources(candidates, 'decisions'), row => row.id), [peer.id]);
  assert.deepEqual(Array.from(historySupplementalSources(candidates, 'feed')), []);
});

test('supplemental keys separate canonical families, channels, content types and IDs containing separators', () => {
  const rows = [source('decision', 'id', { secure: true, encrypted_resource: true, encrypted_kind: 'decision' }),
    source('document', 'id'), source('document', 'id', { channel_id: 'other' }), source('task', 'a:b'), source('task', 'b', { channel_id: 'channel:a' })];
  assert.equal(new Set(rows.map(historySourceKey)).size, rows.length);
  assert.ok(rows.every(row => historySourceKey(row) !== row.kind + ':' + row.id));
  assert.equal(historySupplementalSources([...rows, rows[1]]).length, rows.length);
});

test('expanded typed sources keep their real facet and labels instead of becoming Activity feed', () => {
  const rows = ['document', 'project', 'task', 'feed', 'file-content', 'calendar', 'clip', 'workflow', 'linked-document'].map(kind => source(kind));
  for (const row of rows) assert.deepEqual(Array.from(historySupplementalSources(rows, row.kind), item => item.kind), [row.kind]);
  assert.equal(historySupplementalSources(rows, 'files').length, 0, 'Filenames remain separate from extracted file contents');
  for (const kind of ['decision', 'calendar', 'clip', 'workflow', 'linked-document']) assert.notEqual(historySourceLabel(kind), 'Activity feed');
  assert.equal(historySourceLabel('decision'), 'Decision'); assert.equal(historySourceLabel('calendar'), 'Calendar event');
});
