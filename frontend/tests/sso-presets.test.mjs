import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

function moduleFor(file, dependencies = {}) {
  const exports = {};
  const source = readFileSync(new URL('../src/' + file, import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  vm.runInNewContext(compiled, { exports, URL, require: name => {
    if (!(name in dependencies)) throw new Error('Unexpected dependency: ' + name);
    return dependencies[name];
  } });
  return exports;
}
const helpers = moduleFor('lib/sso-presets.ts');
const fields = update => ({ ...helpers.emptySSOPresetFields(), ...update });
const build = (preset, update = {}) => helpers.buildSSOPresetIssuer(preset, fields(update));

test('provider issuers use tenant-specific paths and exact trailing-slash conventions', () => {
  assert.equal(build('entra', { tenant: '1A2B3C4D-1111-2222-3333-1234567890AB' }), 'https://login.microsoftonline.com/1a2b3c4d-1111-2222-3333-1234567890ab/v2.0');
  assert.equal(build('google'), 'https://accounts.google.com');
  assert.equal(build('okta', { domain: 'Example.OKTA.com' }), 'https://example.okta.com');
  assert.equal(build('okta', { domain: 'login.example.org', authorizationServer: 'aus_Chosen-01' }), 'https://login.example.org/oauth2/aus_Chosen-01');
  assert.equal(build('auth0', { domain: 'example.eu.auth0.com' }), 'https://example.eu.auth0.com/');
  assert.equal(build('auth0', { domain: 'login.example.org' }), 'https://login.example.org/');
  assert.equal(build('keycloak', { baseUrl: 'https://identity.example.org/auth/', realm: 'Our-organization_1' }), 'https://identity.example.org/auth/realms/Our-organization_1');
  assert.equal(build('keycloak', { baseUrl: 'https://identity.example.org', realm: 'organization' }), 'https://identity.example.org/realms/organization');
});

test('Entra rejects shared discovery tenants, domain aliases and path/query injection', () => {
  for (const tenant of ['common', 'organizations', 'consumers', 'company.onmicrosoft.com', '',
    '00000000-0000-0000-0000-000000000000/v2.0', '../common', '00000000-0000-0000-0000-000000000000?x=1',
    '00000000-0000-0000-0000-000000000000\n']) {
    assert.throws(() => build('entra', { tenant }), undefined, tenant);
  }
});

test('provider domain fields reject credentials, paths, schemes, ports and local/IP aliases', () => {
  for (const domain of ['https://example.okta.com', 'example.okta.com/path', 'example.org?tenant=1',
    'name:secret@example.org', 'example.org@evil.example', 'example.org#ignored', 'example.org:443',
    'example.org\\@evil.example', 'example%2eorg', 'example.org.', 'bad..example.org', '-bad.example.org',
    'a'.repeat(64) + '.example.org', '127.0.0.1', '127.1', '0x7f000001', '[::1]', 'localhost',
    'provider.local', 'provider.internal', 'provider.home.arpa', 'login.localhost', '\nexample.org', 'example.org\t', 'exämple.org']) {
    for (const preset of ['okta', 'auth0']) assert.throws(() => build(preset, { domain }), undefined, preset + ': ' + domain);
  }
  for (const authorizationServer of ['../default', 'default/other', 'default?x=1', 'default#fragment', '%2f', 'default\n', 'a'.repeat(129)]) {
    assert.throws(() => build('okta', { domain: 'example.okta.com', authorizationServer }), undefined, authorizationServer);
  }
});

test('Keycloak validates server/context and realm separately before appending the realm path', () => {
  for (const baseUrl of ['http://identity.example.org', 'https://user:secret@identity.example.org',
    'https://identity.example.org?client=one', 'https://identity.example.org#fragment', 'https://identity.example.org:8443',
    'https://identity.example.org/auth/../admin', 'https://identity.example.org/%2e%2e',
    'https://identity.example.org/%61uth', 'https://identity.example.org/auth//context',
    'https://identity.example.org/realms/other', 'https://identity.example.org/admin/master',
    'https://identity.example.org\\@evil.example', 'https://127.0.0.1', 'https://service.internal']) {
    assert.throws(() => build('keycloak', { baseUrl, realm: 'organization' }), undefined, baseUrl);
  }
  for (const realm of ['.', '..', '../other', 'one/two', 'one\\two', '%2e%2e', 'one?x=1', 'one#fragment', 'with space', 'valid\n', 'a'.repeat(129)]) {
    assert.throws(() => build('keycloak', { baseUrl: 'https://identity.example.org/auth', realm }), undefined, realm);
  }
});

test('manual issuer validation preserves backend-compatible literal IPs and encoded issuer paths exactly', () => {
  for (const issuer of ['https://Identity.Example.org/CustomRealm/', 'https://identity.example.org:443/auth/realms/Example',
    'https://example.eu.auth0.com/', 'https://accounts.google.com', 'https://8.8.8.8/issuer',
    'https://[2606:4700:4700::1111]/issuer', 'https://identity.example.org/realm%20with%20spaces',
    'https://identity.example.org/path%2fother', 'https://identity.example.org/path%5cother',
    'https://identity.example.org/%2E%2E/other', 'https://identity.example.org:0443/issuer']) assert.equal(helpers.validateSSOIssuer(issuer), issuer);
  for (const issuer of ['http://identity.example.org', 'https://name:secret@identity.example.org',
    'https://identity.example.org?', 'https://identity.example.org#', 'https://identity.example.org?scope=openid',
    'https://identity.example.org\n', 'https://identity.example.org:8443',
    'https://identity.example.org:', 'https://%69dentity.example.org', 'https://service.internal',
    'https://identity.example.org\\@evil.example', 'javascript:alert(1)']) assert.throws(() => helpers.validateSSOIssuer(issuer), undefined, issuer);
});

test('untouched new and exact saved forms are clean, and a reset cannot retain an old secret', () => {
  const empty = { name: '', issuer: '', clientId: '', scopes: helpers.defaultSSOScopes, secretHandling: 'replace', secret: '', discardSavedSecret: false };
  assert.equal(helpers.ssoDraftIsDirty(null, empty), false);
  const saved = { name: 'Our IdP', issuer_url: 'https://Identity.Example.org/Custom/', client_id: 'own-client', scopes: helpers.defaultSSOScopes, has_secret: true };
  const draft = { ...empty, name: saved.name, issuer: saved.issuer_url, clientId: saved.client_id, secretHandling: 'retain' };
  assert.equal(helpers.ssoDraftIsDirty(saved, draft), false);
  assert.equal(helpers.ssoDraftIsDirty({ ...saved, has_secret: false }, { ...draft, secretHandling: 'remove' }), false);
  assert.equal(helpers.ssoDraftIsDirty(saved, { ...draft, discardSavedSecret: true }), true);
  assert.equal(helpers.ssoSecretInput('retain', '', true).client_secret, null);
  assert.equal(helpers.ssoSecretInput('replace', '', true).client_secret, null);
  assert.equal(helpers.ssoSecretInput('replace', 'new-app-secret', true).client_secret, 'new-app-secret');
  assert.equal(Object.hasOwn(helpers.ssoSecretInput('retain', '', false), 'client_secret'), false);
});

// Exercise the actual component's handlers and effects without browser globals or
// adding a DOM dependency. The full connection-center proof covers browser layout.
async function formFixture(saved = null) {
  const hooks = [], effects = [], state = { token: 'native-session', calls: [], guards: [], dirty: [], tree: null };
  let cursor = 0, rerender = false;
  const react = {
    useState(initial) {
      const index = cursor++;
      if (!(index in hooks)) hooks[index] = { value: typeof initial === 'function' ? initial() : initial };
      return [hooks[index].value, next => {
        const value = typeof next === 'function' ? next(hooks[index].value) : next;
        if (!Object.is(value, hooks[index].value)) { hooks[index].value = value; rerender = true; }
      }];
    },
    useRef(initial) { const index = cursor++; if (!(index in hooks)) hooks[index] = { current: initial }; return hooks[index]; },
    useEffect(effect, dependencies) {
      const index = cursor++, previous = hooks[index];
      if (!previous || dependencies.some((value, key) => !Object.is(value, previous.dependencies[key]))) {
        hooks[index] = { dependencies, cleanup: previous?.cleanup };
        effects.push(() => { hooks[index].cleanup?.(); hooks[index].cleanup = effect(); });
      }
    },
  };
  const props = { workspaceId: 'workspace', security: { mfa_enabled: true }, onGuardChange: value => state.guards.push(value), onDirtyChange: value => state.dirty.push(value) };
  const settings = () => ({ callback_url: 'https://collab.example.org/api/auth/oidc/callback', login_url: null, provider: saved });
  const workspaceSSOApi = {
    get: async () => settings(),
    save: async (workspace, body) => {
      state.calls.push({ action: 'save', workspace, body });
      saved = { name: body.name, issuer_url: body.issuer_url, client_id: body.client_id, scopes: body.scopes, has_secret: !!body.client_secret, enabled: false, tested: false };
    },
    test: async () => { state.calls.push({ action: 'test' }); },
    enable: async () => { state.calls.push({ action: 'enable' }); },
    disable: async () => { state.calls.push({ action: 'disable' }); },
  };
  const jsx = (type, properties) => ({ type, props: properties });
  const { WorkspaceSSOSettings } = moduleFor('components/WorkspaceSSOSettings.tsx', {
    'react': react, 'react/jsx-runtime': { jsx, jsxs: jsx, Fragment: 'fragment' }, 'lucide-react': {},
    '../i18n': { t: text => text, useI18n: () => ({ t: text => text, locale: 'en' }) },
    '../lib/api': { api: { getAccessToken: () => state.token } }, '../lib/integrations-api': { workspaceSSOApi }, '../lib/sso-presets': helpers,
  });
  function render() {
    for (let attempts = 0; attempts < 50; attempts++) {
      cursor = 0; rerender = false; state.tree = WorkspaceSSOSettings(props);
      effects.splice(0).forEach(effect => effect());
      if (!rerender) return;
    }
    throw new Error('Component did not settle');
  }
  async function flush() { for (let attempt = 0; attempt < 8; attempt++) { await Promise.resolve(); if (rerender) render(); } }
  function nodes(node) {
    if (!node || typeof node !== 'object') return [];
    if (Array.isArray(node)) return node.flatMap(item => nodes(item));
    return [node, ...nodes(node.props?.children)];
  }
  function byId(id) { const found = nodes(state.tree).find(node => node.props?.id === id); assert.ok(found, id); return found; }
  function form(label) { const found = nodes(state.tree).find(node => node.type === 'form' && node.props['aria-label'] === label); assert.ok(found, label); return found; }
  function change(id, value) { byId(id).props.onChange({ target: { value } }); render(); }
  render(); await flush();
  return { state, props, byId, form, change, render, flush, unmount: () => hooks.forEach(hook => hook.cleanup?.()) };
}

test('component loads an exact saved issuer cleanly and only prepares a different preset locally', async () => {
  const original = 'https://Identity.Example.org/ExactIssuer/';
  const f = await formFixture({ name: 'Original provider', issuer_url: original, client_id: 'old-app', scopes: helpers.defaultSSOScopes, has_secret: true });
  assert.equal(f.byId('workspace-sso-issuer').props.value, original);
  assert.equal(f.byId('workspace-sso-preset').props.value, 'generic');
  assert.ok(f.state.dirty.every(value => value === false));
  f.change('workspace-sso-secret-handling', 'replace'); f.change('workspace-sso-secret', 'pending-original-secret');
  f.change('workspace-sso-preset', 'google');
  assert.equal(f.byId('workspace-sso-client-id').props.value, '');
  assert.equal(f.byId('workspace-sso-secret').props.value, '');
  assert.equal(f.byId('workspace-sso-secret-handling').props.value, 'replace');
  assert.equal(f.byId('workspace-sso-issuer').props.value, 'https://accounts.google.com');
  assert.equal(f.state.dirty.at(-1), true);
  assert.equal(f.state.calls.length, 0);
  f.change('workspace-sso-client-id', 'prepared-app'); f.change('workspace-sso-secret', 'prepared-secret');
  f.change('workspace-sso-preset', 'google');
  assert.equal(f.byId('workspace-sso-client-id').props.value, 'prepared-app');
  assert.equal(f.byId('workspace-sso-secret').props.value, 'prepared-secret');
  // Direct manual editing remains available without losing the exact identifier.
  f.change('workspace-sso-issuer', 'https://Another.Example.org/Custom/');
  assert.equal(f.byId('workspace-sso-preset').props.value, 'generic');
  assert.equal(f.byId('workspace-sso-issuer').props.value, 'https://Another.Example.org/Custom/');
  f.unmount(); assert.equal(f.state.dirty.at(-1), false);
});

test('new untouched form is clean; saving a new app uses explicit password/MFA and never tests or enables', async () => {
  const f = await formFixture();
  assert.ok(f.state.dirty.every(value => value === false));
  f.change('workspace-sso-preset', 'keycloak');
  f.change('workspace-sso-base', 'https://identity.example.org/auth'); f.change('workspace-sso-realm', 'organization');
  f.change('workspace-sso-client-id', 'customer-app'); f.change('workspace-sso-secret', 'customer-app-secret');
  assert.equal(f.byId('workspace-sso-issuer').props.value, 'https://identity.example.org/auth/realms/organization');
  assert.equal(f.state.calls.length, 0);
  f.form('Workspace SSO configuration').props.onSubmit({ preventDefault() {} }); f.render();
  assert.equal(f.state.calls.length, 0);
  f.change('workspace-sso-password', 'native-password'); f.change('workspace-sso-code', '123456');
  await f.form('Confirm workspace SSO change').props.onSubmit({ preventDefault() {} }); await f.flush();
  assert.equal(f.state.calls.length, 1);
  assert.equal(f.state.calls[0].action, 'save');
  assert.equal(f.state.calls[0].body.client_secret, 'customer-app-secret');
  assert.equal(f.state.calls[0].body.password, 'native-password');
  assert.equal(f.state.calls[0].body.code, '123456');
  assert.equal(f.state.dirty.at(-1), false);
  assert.deepEqual(f.state.guards, ['busy', null]);
});

test('switching presets then choosing a public client explicitly removes the previous saved credential', async () => {
  const f = await formFixture({ name: 'Original provider', issuer_url: 'https://accounts.google.com', client_id: 'original-app', scopes: helpers.defaultSSOScopes, has_secret: true });
  f.change('workspace-sso-preset', 'google');
  f.change('workspace-sso-client-id', 'original-app');
  f.change('workspace-sso-secret-handling', 'remove');
  f.form('Workspace SSO configuration').props.onSubmit({ preventDefault() {} }); f.render();
  f.change('workspace-sso-password', 'native-password'); f.change('workspace-sso-code', '123456');
  await f.form('Confirm workspace SSO change').props.onSubmit({ preventDefault() {} }); await f.flush();
  assert.equal(f.state.calls[0].body.client_secret, null);
  assert.equal(f.state.calls[0].body.issuer_url, 'https://accounts.google.com');
});

test('account changes before confirmation cannot submit an old-account form', async () => {
  const f = await formFixture();
  f.change('workspace-sso-preset', 'google'); f.change('workspace-sso-client-id', 'customer-app'); f.change('workspace-sso-secret', 'own-secret');
  f.form('Workspace SSO configuration').props.onSubmit({ preventDefault() {} }); f.render();
  f.change('workspace-sso-password', 'native-password'); f.change('workspace-sso-code', '123456');
  f.state.token = 'different-session';
  await f.form('Confirm workspace SSO change').props.onSubmit({ preventDefault() {} }); await f.flush();
  assert.equal(f.state.calls.length, 0);
  assert.equal(f.state.guards.length, 0);
});

test('SSO Swedish catalog covers its UI, provider guidance and validation messages with stable placeholders', () => {
  const catalog = moduleFor('i18n/sso-presets.sv.ts').default;
  for (const preset of helpers.ssoPresets) {
    assert.ok(Object.hasOwn(catalog, preset.name), preset.name);
    assert.ok(Object.hasOwn(catalog, preset.guide), preset.guide);
  }
  const existing = {};
  const directory = new URL('../src/i18n/', import.meta.url);
  // Include existing catalogs when checking the modified component's old labels.
  for (const name of readdirSync(directory).filter(name => name === 'sv.ts' || name.endsWith('.sv.ts'))) Object.assign(existing, moduleFor('i18n/' + name).default);
  const text = readFileSync(new URL('../src/components/WorkspaceSSOSettings.tsx', import.meta.url), 'utf8');
  const tree = ts.createSourceFile('WorkspaceSSOSettings.tsx', text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  function check(node) {
    const argument = node.arguments?.[0];
    if (ts.isCallExpression(node) && node.expression.getText(tree) === 't' && argument && ts.isStringLiteral(argument)) {
      assert.ok(Object.hasOwn(existing, argument.text), 'Missing Swedish SSO key: ' + argument.text);
    }
    ts.forEachChild(node, check);
  }
  check(tree);
  const helperTree = ts.createSourceFile('sso-presets.ts', readFileSync(new URL('../src/lib/sso-presets.ts', import.meta.url), 'utf8'), ts.ScriptTarget.Latest, true);
  function checkError(node) {
    if (ts.isNewExpression(node) && node.expression.getText(helperTree) === 'Error' && ts.isStringLiteral(node.arguments[0])) assert.ok(Object.hasOwn(catalog, node.arguments[0].text));
    ts.forEachChild(node, checkError);
  }
  checkError(helperTree);
  const placeholders = value => [...new Set(value.match(/\{[A-Za-z][A-Za-z0-9_]*\}/g) || [])].sort();
  for (const [key, value] of Object.entries(catalog)) assert.deepEqual(placeholders(key), placeholders(value), key);
});
