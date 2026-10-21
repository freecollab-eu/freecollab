import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createEmbedSignIn, createSessionTokenFactory } from '../dist/auth.js';

function browser() {
  const previous = globalThis.window, listeners = new Set();
  const popup = { closed: false, location: { href: '' }, close() { this.closed = true; } };
  const window = { location: new URL('https://product.example/projects'), open: () => popup,
    addEventListener(name, fn) { assert.equal(name, 'message'); listeners.add(fn); }, removeEventListener(name, fn) { assert.equal(name, 'message'); listeners.delete(fn); } };
  globalThis.window = window;
  return { popup, window, listeners, restore() { globalThis.window = previous; }, send(data, origin = 'https://collab.example', source = popup) { for (const fn of listeners) fn({ data, origin, source }); } };
}
const context = () => ({ applicationId: 'app', workspaceId: 'team', channelId: 'project', parentOrigin: 'https://product.example', codeChallenge: 'A'.repeat(43), signal: new AbortController().signal });
function requestIn(popup) { return JSON.parse(Buffer.from(popup.location.href.split('#embed-login=')[1], 'base64url').toString()); }

test('OIDC popup grant requires exact trusted origin, popup source and unpredictable request id', async () => {
  const host = browser();
  try {
    const signIn = createEmbedSignIn({ appUrl: 'https://collab.example', applicationId: 'app', locale: 'sv' });
    signIn.open(); const pending = signIn.grantFactory(context()); let settled = false; void pending.finally(() => { settled = true; });
    const request = requestIn(host.popup);
    assert.equal(request.codeChallenge, 'A'.repeat(43)); assert.equal(request.parentOrigin, 'https://product.example'); assert.equal(request.locale, 'sv');
    assert.equal('codeVerifier' in request, false); assert.equal('access_token' in request, false);
    const grant = { protocol: request.protocol, type: 'grant', requestId: request.requestId, code: 'g'.repeat(43) };
    host.send(grant, 'https://attacker.example'); host.send(grant, undefined, {}); host.send({ ...grant, requestId: 'wrong' });
    await Promise.resolve(); assert.equal(settled, false);
    host.send(grant); assert.deepEqual(await pending, { code: 'g'.repeat(43) });
    assert.equal(host.listeners.size, 0); assert.equal(host.popup.closed, true); signIn.destroy();
  } finally { host.restore(); }
});

test('sign-in needs explicit user interaction and matches application and current origin', async () => {
  const host = browser();
  try {
    const helper = createEmbedSignIn({ appUrl: 'https://collab.example', applicationId: 'app' });
    await assert.rejects(helper.grantFactory(context()), error => error.code === 'sign-in-required');
    helper.open(); await assert.rejects(helper.grantFactory({ ...context(), applicationId: 'other' }), error => error.code === 'scope-mismatch');
    assert.equal(host.popup.closed, true); helper.destroy();
    assert.throws(() => helper.open(), error => error.code === 'destroyed');
    for (const appUrl of ['http://remote.example', 'https://collab.example/path', 'https://collab.example/?token=x', 'https://user:password@collab.example']) {
      assert.throws(() => createEmbedSignIn({ appUrl, applicationId: 'app' }));
    }
  } finally { host.restore(); }
});

test('abort and destruction close popup and remove message listeners before a late grant', async () => {
  for (const action of ['abort', 'destroy']) {
    const host = browser();
    try {
      const helper = createEmbedSignIn({ appUrl: 'https://collab.example', applicationId: 'app' }); helper.open();
      const controller = new AbortController(), pending = helper.grantFactory({ ...context(), signal: controller.signal });
      const rejection = assert.rejects(pending, error => error.name === 'AbortError');
      if (action === 'abort') controller.abort(); else helper.destroy();
      await rejection; assert.equal(host.listeners.size, 0); assert.equal(host.popup.closed, true); helper.destroy();
    } finally { host.restore(); }
  }
});

test('host session adapter only calls same-origin backend and carries scope without browser API secret', async () => {
  const host = browser();
  try {
    let observed;
    const factory = createSessionTokenFactory({ endpoint: '/session/freecollab', fetch: async (url, options) => {
      observed = { url, options }; return Response.json({ access_token: 'real-scoped-token' });
    } });
    assert.equal(await factory({ ...context(), scope: { workspaceId: 'team', channelId: 'project' }, reason: 'request' }), 'real-scoped-token');
    assert.equal(observed.url, 'https://product.example/session/freecollab');
    assert.deepEqual(JSON.parse(observed.options.body), { workspace_id: 'team', channel_id: 'project' });
    assert.equal(observed.options.credentials, 'same-origin'); assert.equal(observed.options.cache, 'no-store'); assert.equal(observed.options.redirect, 'error');
    assert.equal('Authorization' in observed.options.headers, false);
    for (const endpoint of ['https://other.example/session', '/session?token=x', '/session#token=x']) assert.throws(() => createSessionTokenFactory({ endpoint }), error => error.code === 'invalid-session-endpoint');
  } finally { host.restore(); }
});

test('host logout is null and invalid/error responses cannot become credentials', async () => {
  const host = browser();
  try {
    const ctx = { signal: new AbortController().signal, scope: { workspaceId: 'team', channelId: 'project' }, reason: 'request' };
    assert.equal(await createSessionTokenFactory({ endpoint: '/session', fetch: async () => new Response('', { status: 401 }) })(ctx), null);
    assert.equal(await createSessionTokenFactory({ endpoint: '/session', fetch: async () => Response.json({ access_token: null }) })(ctx), null);
    for (const value of [{ token: 'unknown' }, { access_token: '' }, { access_token: { secret: 'no' } }]) {
      await assert.rejects(createSessionTokenFactory({ endpoint: '/session', fetch: async () => Response.json(value) })(ctx), error => error.code === 'invalid-session');
    }
    await assert.rejects(createSessionTokenFactory({ endpoint: '/session', fetch: async () => new Response('private provider error', { status: 500 }) })(ctx), error => error.code === 'session-unavailable' && !error.message.includes('private provider'));
    const controller = new AbortController();
    await assert.rejects(createSessionTokenFactory({ endpoint: '/session', fetch: async () => { controller.abort(); return Response.json({ access_token: 'old-session' }); } })({ ...ctx, signal: controller.signal }), error => error.name === 'AbortError');
  } finally { host.restore(); }
});
