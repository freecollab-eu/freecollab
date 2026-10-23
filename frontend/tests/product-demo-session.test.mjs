import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const root = new URL('../src/', import.meta.url);
const source = name => readFileSync(new URL(name, root), 'utf8');
const compile = text => ts.transpileModule(text, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const module = {};
vm.runInNewContext(compile(source('lib/product-demo-session.ts')), { exports: module, URL });
const sample = (id = 'one') => ({
  visit_id: 'visit-' + id, workspace_id: 'workspace-' + id, channel_id: 'channel-' + id,
  expires_at: new Date(Date.now() + 3600000).toISOString(),
  session: { access_token: 'header.payload.signature-' + id, token_type: 'bearer', user_id: 'user-' + id, public_key: '', encrypted_private_key: null },
});

test('the demo accepts a bounded real-session contract and rejects incomplete, expired or unzoned responses', () => {
  const now = Date.now(), next = sample();
  assert.equal(module.verifiedProductDemoSession(next, now), next);
  for (const changed of [null, [], {}, { ...next, visit_id: '' }, { ...next, channel_id: '../../private' }, { ...next, expires_at: '2026-10-23T12:00:00' }, { ...next, expires_at: new Date(now - 1).toISOString() }, { ...next, expires_at: new Date(now + 86400001).toISOString() }, { ...next, session: { ...next.session, access_token: 'not-a-signed-session' } }, { ...next, session: { ...next.session, encrypted_private_key: 'real-account-material' } }]) {
    assert.throws(() => module.verifiedProductDemoSession(changed, now));
  }
});

test('the exit target is a trusted build setting, with no credential URLs or query return destinations', () => {
  const fallback = module.productDemoWebsiteURL();
  for (const destination of ['javascript:alert(1)', '//evil.test', 'https://username:secret@site.test/', 'https://site.test/?return=https://evil.test/', 'https://site.test/#account', 'http://192.168.0.1/', 'not a URL']) assert.equal(module.productDemoWebsiteURL(destination), fallback);
  assert.equal(module.productDemoWebsiteURL('https://own-website.test/'), 'https://own-website.test/');
  assert.equal(module.productDemoWebsiteURL('http://127.0.0.1:3000/'), 'http://127.0.0.1:3000/');
});

test('the real API constructor and complete demo sign-in never read, overwrite or remove a stored account', async () => {
  const original = source('lib/api.ts');
  const text = original.replace("const API_URL = import.meta.env.VITE_API_URL || '/api';", "const API_URL = 'https://demo.test/api';");
  assert.notEqual(original, text);
  const exports = {}, storageCalls = [], requests = [];
  const storage = new Map([['freecollab_token', 'private-existing-token'], ['freecollab_user_id', 'private-existing-user']]);
  vm.runInNewContext(compile(text), {
    exports, Event, FormData, Blob, File,
    document: { getElementById: () => ({ hasAttribute: name => name === 'data-demo-application' }) },
    localStorage: {
      getItem: key => { storageCalls.push(['get', key]); return storage.get(key) ?? null; },
      setItem: (key, value) => { storageCalls.push(['set', key]); storage.set(key, value); },
      removeItem: key => { storageCalls.push(['remove', key]); storage.delete(key); },
    },
    window: { dispatchEvent: () => true },
    fetch: async (url, options) => { requests.push({ url, options }); return new Response(JSON.stringify({ id: 'user-one' }), { headers: { 'Content-Type': 'application/json' } }); },
  });
  const api = exports.api;
  assert.equal(api.isMemorySession(), true);
  assert.equal(api.getAccessToken(), null);
  api.useMemorySession(); api.applySession(sample().session); api.syncStoredSession();
  await api.getMe(); api.logout();
  assert.equal(new Headers(requests[0].options.headers).get('Authorization'), 'Bearer header.payload.signature-one');
  assert.deepEqual(storageCalls, []);
  assert.equal(storage.get('freecollab_token'), 'private-existing-token');
  assert.equal(storage.get('freecollab_user_id'), 'private-existing-user');
});

function functions(file, names) {
  const text = source(file), tree = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  return tree.statements.filter(node => ts.isFunctionDeclaration(node) && names.includes(node.name?.text)).map(node => node.getText(tree)).join('\n');
}

test('actual workspace, appearance and locale cache functions keep demo preferences off browser storage', () => {
  const storageCalls = [], exports = {}, defaults = { mode: 'light' };
  const text = functions('account/AccountProvider.tsx', ['savedWorkspace']) + '\n'
    + functions('appearance/AppearanceProvider.tsx', ['readCache', 'writeCache']) + '\n'
    + functions('i18n/index.tsx', ['isExampleDocument', 'cached', 'cache'])
    + '\nexports.workspace=savedWorkspace; exports.appearance=readCache; exports.writeAppearance=writeCache; exports.locale=cached; exports.writeLocale=cache;';
  vm.runInNewContext(compile(text), {
    exports, api: { isMemorySession: () => true }, DEFAULT_APPEARANCE: defaults, currentLocale: () => 'sv',
    document: { getElementById: () => ({ hasAttribute: name => name === 'data-demo-application' }) },
    localStorage: {
      getItem: key => { storageCalls.push(['get', key]); throw new Error('No browser cache is permitted'); },
      setItem: key => { storageCalls.push(['set', key]); throw new Error('No browser cache is permitted'); },
      removeItem: key => { storageCalls.push(['remove', key]); throw new Error('No browser cache is permitted'); },
    },
  });
  assert.equal(exports.workspace('demo-user'), null);
  assert.deepEqual(JSON.parse(JSON.stringify(exports.appearance('demo-user'))), defaults);
  exports.writeAppearance('demo-user', { mode: 'dark' });
  assert.equal(exports.locale('demo-user'), 'sv'); exports.writeLocale('demo-user', 'en');
  assert.deepEqual(storageCalls, []);
});

function callback(name) {
  const text = source('components/ProductDemoSession.tsx');
  const tree = ts.createSourceFile('ProductDemoSession.tsx', text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let result;
  function visit(node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(tree) === name && ts.isCallExpression(node.initializer) && node.initializer.expression.getText(tree) === 'useCallback') result = node.initializer.arguments[0].getText(tree);
    ts.forEachChild(node, visit);
  }
  visit(tree); assert.ok(result, 'Execute the actual ' + name + ' callback');
  return result;
}

function startFixture(previous = null) {
  const current = { current: previous }, busy = { current: false }, sequence = { current: 0 }, mounted = { current: true }, controller = { current: null };
  const phases = [], sessions = [], retired = [], sent = [], applied = [], transport = [];
  let resolve, reject;
  const pending = new Promise((yes, no) => { resolve = yes; reject = no; });
  const exports = {};
  vm.runInNewContext(compile('exports.start=' + callback('start')), {
    exports, current, busy, sequence, mounted, controller, AbortController, Date,
    api: {
      request: (path, options) => { sent.push({ path, options }); return path === '/demo/session' ? pending : Promise.resolve({ locale: 'sv' }); },
      applySession: session => { applied.push(session); transport.push('adopt'); }, logout: () => transport.push('logout'),
    },
    verifiedProductDemoSession: module.verifiedProductDemoSession,
    currentLocale: () => 'sv', disconnectSocket: () => transport.push('disconnect'),
    window: { setTimeout, clearTimeout }, clearTimeout,
    setPhase: value => phases.push(value), setError: () => {}, setSession: value => sessions.push(value), retire: value => retired.push(value),
  });
  return { ...exports, resolve, reject, current, busy, sequence, mounted, phases, sessions, retired, sent, applied, transport };
}

test('a failed reset restores the current sample account and does not revoke it', async () => {
  const previous = sample('old'), fixture = startFixture(previous), running = fixture.start();
  fixture.reject(new Error('Capacity reached')); await running;
  assert.deepEqual(fixture.phases, ['loading', 'ready']);
  assert.equal(fixture.current.current, previous);
  assert.equal(fixture.applied[0], previous.session);
  assert.deepEqual(fixture.retired, []);
  assert.equal(fixture.busy.current, false);
});

test('reset cannot double-submit and adopts the new account only after disconnecting the old socket', async () => {
  const previous = sample('old'), next = sample('new'), fixture = startFixture(previous), running = fixture.start();
  await fixture.start(); assert.equal(fixture.sent.length, 1);
  fixture.resolve(next); await running;
  assert.equal(fixture.current.current, next);
  assert.deepEqual(fixture.transport, ['disconnect', 'adopt']);
  assert.equal(fixture.retired[0], previous);
  assert.equal(fixture.retired[0].session.access_token, previous.session.access_token);
  assert.equal(fixture.sent[1].path, '/preferences/language');
  assert.equal(fixture.sent[1].options.body, '{"locale":"sv"}');
});

test('a stale bootstrap completion cannot adopt an account after exit or unmount', async () => {
  const next = sample('late'), fixture = startFixture(), running = fixture.start();
  fixture.sequence.current++; fixture.mounted.current = false;
  fixture.resolve(next); await running;
  assert.deepEqual(fixture.applied, []);
  assert.equal(fixture.current.current, null);
  assert.equal(fixture.retired[0], next);
});

test('retiring the old sample account uses only its captured token and never sends browser credentials or follows redirects', async () => {
  const exports = {}, requests = [], previous = sample('old');
  vm.runInNewContext(compile('exports.retire=' + callback('retire')), {
    exports, AbortController,
    api: { getBaseUrl: () => 'https://demo.test/api', getAccessToken: () => { throw new Error('The current account must never be consulted'); } },
    window: { setTimeout, clearTimeout },
    fetch: async (url, options) => { requests.push({ url, options }); return new Response(null, { status: 204 }); },
  });
  exports.retire(previous); await new Promise(resolve => setImmediate(resolve));
  assert.equal(requests[0].url, 'https://demo.test/api/demo/session');
  assert.equal(requests[0].options.headers.Authorization, 'Bearer ' + previous.session.access_token);
  assert.equal(requests[0].options.credentials, 'omit');
  assert.equal(requests[0].options.redirect, 'error');
  assert.equal(requests[0].options.cache, 'no-store');
  assert.equal(requests[0].options.signal.aborted, false);
});
