import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

function fixture() {
  const requests = [], api = { request: async (path, options) => { requests.push({ path, options }); return payload; } };
  const compiled = ts.transpileModule(readFileSync(new URL('../src/lib/connection-setup-api.ts', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  const exports = {};
  vm.runInNewContext(compiled, { exports, require: name => name === './api' ? { api } : null, URL, Set });
  return { ...exports, requests, api };
}
const recipe = {
  id: 'files.google', name: 'Google Drive', category: 'files', description: 'Customer-owned Drive', target: 'sync', provider: 'google', kind: 'files',
  enabled: true, can_configure: true, requires_channel: true, secure_supported: false, callback_url: 'https://collab.example.org/api/sync/oauth/callback',
  scopes: ['https://www.googleapis.com/auth/drive'], docs_url: 'https://developers.google.com/drive/api/guides/api-specific-auth',
  prerequisites: ['A customer OAuth application'], steps: ['Save, authorize and explicitly test'],
  fields: [{ key: 'client_id', label: 'Client ID', required: true, secret: false }, { key: 'client_secret', label: 'Client secret', required: true, secret: true }],
  status: 'authorization_required', blockers: ['Review consent'], connections: [{ id: 'connection', name: 'Private customer record', status: 'authorization_required', enabled: false, revision: 3, verified_at: null }],
};
const payload = { workspace_id: 'workspace', channel_id: 'channel', public_origin: 'https://collab.example.org', origin_status: 'valid', recipes: [recipe] };
const clone = value => JSON.parse(JSON.stringify(value));

test('native setup read encodes exact requested scope, preserves abort and never requests a mutation', async () => {
  const f = fixture(), signal = new AbortController().signal;
  await f.connectionSetupApi.get('workspace', 'channel', signal);
  assert.equal(f.requests[0].path, '/workspaces/workspace/connection-setup?channel_id=channel');
  assert.equal(f.requests[0].options.signal, signal); assert.equal(f.requests[0].options.cache, 'no-store'); assert.equal(f.requests[0].options.method, undefined);
  f.api.request = async (path, options) => { f.requests.push({ path, options }); return { ...payload, workspace_id: 'workspace /?', channel_id: 'channel &?' }; };
  await f.connectionSetupApi.get('workspace /?', 'channel &?');
  assert.equal(f.requests[1].path, '/workspaces/workspace%20%2F%3F/connection-setup?channel_id=channel%20%26%3F');
});

test('workspace and channel substitutions fail closed including implicit no-channel aggregation', () => {
  const f = fixture();
  for (const input of [{ ...payload, workspace_id: 'outsider' }, { ...payload, channel_id: 'other' }, { ...payload, channel_id: null }, { ...payload, channel_id: undefined }]) {
    assert.throws(() => f.parseConnectionSetup(input, 'workspace', 'channel'), /mismatched/);
  }
  assert.throws(() => f.parseConnectionSetup(payload, 'workspace', null), /mismatched/);
  assert.equal(f.parseConnectionSetup({ ...payload, channel_id: null, recipes: [{ ...recipe, connections: [], blockers: ['Choose a channel'], status: 'not_configured' }] }, 'workspace').channel_id, null);
});

test('setup export is an explicit versioned whitelist without session, stored credential or connection data', () => {
  const f = fixture(), source = clone(recipe);
  Object.assign(source, { access_token: 'SESSION-MUST-NOT-EXPORT', config_json: 'PRIVATE-CONFIG', encrypted_credentials: 'PRIVATE-CREDENTIAL', workspace_id: 'PRIVATE-WORKSPACE', channel_id: 'PRIVATE-CHANNEL' });
  source.fields[1].value = 'TYPED-SECRET-MUST-NOT-EXPORT'; source.fields[1].password = 'NATIVE-PASSWORD';
  const result = f.connectionRecipeDownload(source), data = JSON.parse(result.json);
  assert.equal(result.filename, 'freecollab-setup-files.google.json'); assert.equal(data.version, 1); assert.equal(data.format, 'freecollab.connection-setup');
  assert.deepEqual(data.fields[1], { key: 'client_secret', label: 'Client secret', required: true, secret: true });
  assert.equal(data.callback_url, recipe.callback_url); assert.deepEqual(data.scopes, recipe.scopes); assert.deepEqual(data.steps, recipe.steps);
  for (const forbidden of ['SESSION-MUST-NOT-EXPORT', 'PRIVATE-CONFIG', 'PRIVATE-CREDENTIAL', 'PRIVATE-WORKSPACE', 'PRIVATE-CHANNEL', 'Private customer record', 'TYPED-SECRET-MUST-NOT-EXPORT', 'NATIVE-PASSWORD', 'connections', 'blockers', 'verified_at']) assert.ok(!result.json.includes(forbidden), forbidden);
  assert.deepEqual(Object.keys(data).sort(), ['format', 'version', 'recipe', 'provider', 'feature', 'kind', 'callback_url', 'scopes', 'fields', 'prerequisites', 'steps', 'documentation_url'].sort());
});

test('native response parsing strips additional credential properties including nested field values', () => {
  const f = fixture(), input = clone(payload);
  input.account_session = 'session'; input.recipes[0].credentials = 'provider-secret'; input.recipes[0].fields[0].value = 'typed-client-id'; input.recipes[0].connections[0].credentials = 'saved-token';
  const parsed = JSON.stringify(f.parseConnectionSetup(input, 'workspace', 'channel'));
  for (const forbidden of ['account_session', 'provider-secret', 'typed-client-id', 'saved-token']) assert.ok(!parsed.includes(forbidden));
});

test('unsafe callback and documentation URLs cannot be rendered or downloaded', () => {
  const f = fixture();
  for (const address of ['javascript:alert(1)', 'https://user:password@example.org/callback', 'https://example.org/callback?access_token=private', 'https://example.org/callback#private', 'http://external.example.org/callback', '//example.org/callback']) {
    assert.throws(() => f.setupAddress(address, 'callback'), /unsafe/);
    assert.throws(() => f.connectionRecipeDownload({ ...recipe, callback_url: address }), /unsafe/);
    assert.throws(() => f.parseConnectionSetup({ ...payload, recipes: [{ ...recipe, callback_url: address }] }, 'workspace', 'channel'), /unsafe/);
  }
  assert.throws(() => f.setupAddress('http://localhost/docs', 'documentation'), /unsafe/);
  assert.throws(() => f.setupAddress('https://docs.example.org/?token=secret', 'documentation'), /unsafe/);
  assert.equal(f.setupAddress('http://127.0.0.1:8000/api/sync/oauth/callback', 'callback'), 'http://127.0.0.1:8000/api/sync/oauth/callback');
  assert.equal(f.setupAddress('https://docs.example.org/access#permissions', 'documentation'), 'https://docs.example.org/access#permissions');
});

test('unknown status, missing security flags, duplicated recipes and malformed field metadata fail closed', () => {
  const f = fixture();
  for (const changed of [{ status: 'pretend_connected' }, { enabled: undefined }, { can_configure: undefined }, { secure_supported: undefined }, { requires_channel: 'yes' }, { fields: [{ key: 'secret', label: 'Secret', required: true }] }, { fields: [...recipe.fields, recipe.fields[0]] }]) {
    assert.throws(() => f.parseConnectionSetup({ ...payload, recipes: [{ ...recipe, ...changed }] }, 'workspace', 'channel'), /invalid/);
  }
  assert.throws(() => f.parseConnectionSetup({ ...payload, recipes: [recipe, recipe] }, 'workspace', 'channel'), /invalid/);
});

test('blocked and local-development recipes remain downloadable without inventing provider readiness', () => {
  const f = fixture(), data = { ...payload, public_origin: null, origin_status: 'invalid', recipes: [{ ...recipe, enabled: false, callback_url: null, status: 'unavailable', blockers: ['An operator must configure the origin'], connections: [] }] };
  const parsed = f.parseConnectionSetup(data, 'workspace', 'channel');
  assert.equal(parsed.recipes[0].status, 'unavailable');
  const downloaded = JSON.parse(f.connectionRecipeDownload(parsed.recipes[0]).json);
  assert.equal(downloaded.callback_url, null); assert.ok(!('status' in downloaded)); assert.ok(!('enabled' in downloaded));
});

test('structural configure permission remains separate from actionable authorization or test notes', () => {
  const f = fixture();
  const repair = f.parseConnectionSetup(payload, 'workspace', 'channel').recipes[0];
  assert.equal(repair.status, 'authorization_required'); assert.equal(repair.blockers.length, 1); assert.equal(repair.can_configure, true);
  const denied = f.parseConnectionSetup({ ...payload, recipes: [{ ...recipe, status: 'ready', blockers: [], can_configure: false }] }, 'workspace', 'channel').recipes[0];
  assert.equal(denied.can_configure, false);
});
