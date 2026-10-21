import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/lib/connection-health.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const health = {};
vm.runInNewContext(compiled, { exports: health, Set });
const recipe = (id, status, saved = true) => ({ id, name: id, status, connections: saved ? [{ id: id + '-saved', verified_at: null }] : [] });
const ids = rows => Array.from(rows, row => row.id);

test('health summary counts saved services without promoting unknown verification dates to access', () => {
  const rows = [recipe('ready', 'ready'), recipe('authorize', 'authorization_required'), recipe('paused', 'paused'), recipe('draft', 'draft'), recipe('new', 'not_configured', false)];
  rows[1].connections[0].verified_at = '2999-01-01T00:00:00Z';
  assert.deepEqual(JSON.parse(JSON.stringify(health.connectionHealthSummary(rows))), { configured: 4, ready: 1, followUp: 2, paused: 1 });
  assert.equal(health.connectionStatusLabels.ready, 'Configuration ready');
  assert.notEqual(health.connectionNextAction('ready'), 'Connected');
});

test('saved and follow-up filters preserve paused/unavailable state and never mutate the server catalog', () => {
  const rows = Object.freeze([recipe('Z ready', 'ready'), recipe('B authorization', 'authorization_required'), recipe('A unavailable', 'unavailable'), recipe('N new', 'not_configured', false), recipe('A draft', 'draft'), recipe('P paused', 'paused')]);
  assert.deepEqual(ids(health.connectionHealthRows(rows, 'configured')), ['A draft', 'B authorization', 'A unavailable', 'P paused', 'Z ready']);
  assert.deepEqual(ids(health.connectionHealthRows(rows, 'follow_up')), ['A draft', 'B authorization']);
  assert.deepEqual(ids(health.connectionHealthRows(rows, 'all')), ['A draft', 'B authorization', 'A unavailable', 'N new', 'P paused', 'Z ready']);
  assert.deepEqual(ids(rows), ['Z ready', 'B authorization', 'A unavailable', 'N new', 'A draft', 'P paused']);
});

test('every supported health state sends the user to an explicit appropriate review step', () => {
  const expected = {
    not_configured: 'Prepare this connection', draft: 'Finish the saved draft', authorization_required: 'Authorize the customer account',
    verification_required: 'Review and run an explicit test', ready: 'Review saved configuration', paused: 'Review the paused configuration',
    attention: 'Review current access and repair the connection', unavailable: 'Review installation requirements',
  };
  for (const [status, action] of Object.entries(expected)) assert.equal(health.connectionNextAction(status), action);
});
