'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const { createHash } = require('node:crypto');
const { deploymentOrigin, sameOrigin, externalUrl, partitionFor, trustedSender } = require('../src/policy.cjs');
const { browserSignIn } = require('../src/sign-in.cjs');
const { PermissionGrants } = require('../src/permissions.cjs');
const request = url => new Promise((resolve, reject) => { http.get(url, response => { response.resume(); response.on('end', () => resolve(response.statusCode)); }).on('error', reject); });
test('deployment input rejects insecure, credentialed and ambiguous destinations', () => {
  for (const url of ['http://localhost:3000', 'file:///tmp/app', 'https://user:secret@collab.test', 'https://collab.test/path', 'https://collab.test/?token=x', 'https://collab.test/#app', ' https://collab.test']) assert.throws(() => deploymentOrigin(url));
  assert.equal(deploymentOrigin('https://collab.test:443/'), 'https://collab.test');
  assert.equal(deploymentOrigin('https://collab.test:8443'), 'https://collab.test:8443');
  assert.notEqual(partitionFor('https://one.test'), partitionFor('https://two.test'));
});
test('only the exact live main frame can invoke the native bridge', () => {
  const mainFrame = { url: 'https://collab.test/' }, contents = { mainFrame };
  assert.equal(trustedSender({ sender: contents, senderFrame: mainFrame }, contents, 'https://collab.test'), true);
  assert.equal(trustedSender({ sender: contents, senderFrame: { url: mainFrame.url } }, contents, 'https://collab.test'), false);
  assert.equal(trustedSender({ sender: contents, senderFrame: mainFrame }, contents, 'https://attacker.test'), false);
  assert.equal(sameOrigin('https://collab.test.attacker.test/', 'https://collab.test'), false);
  for (const url of ['javascript:alert(1)', 'file:///etc/passwd', 'data:text/html,x', 'https://user:pass@example.test/']) assert.equal(externalUrl(url), null);
});
test('permission checks require prior explicit approval for each requested device', () => {
  const grants = new PermissionGrants();
  assert.equal(grants.check('notifications'), false);
  assert.equal(grants.check('media', { mediaType: 'audio' }), false);
  grants.approve('media', { mediaTypes: ['audio'] });
  assert.equal(grants.check('media', { mediaType: 'audio' }), true);
  assert.equal(grants.check('media', { mediaType: 'video' }), false);
  assert.equal(grants.check('media', { mediaType: 'unknown' }), false);
  assert.equal(grants.check('media', { mediaTypes: ['audio', 'video'] }), false);
  grants.approve('notifications'); assert.equal(grants.check('notifications'), true);
  grants.approve('display-capture', { mediaTypes: ['video'] });
  assert.equal(grants.check('display-capture', { mediaTypes: ['video'] }), false);
  assert.equal(new PermissionGrants().check('notifications'), false);
});
test('actual loopback callback rejects forged state and returns one-use PKCE material', async () => {
  let captured;
  const result = await browserSignIn('https://collab.test', async url => {
    assert.ok(!url.includes('code_verifier')); assert.ok(!url.includes('access_token'));
    captured = JSON.parse(Buffer.from(url.split('#native-login=')[1], 'base64url').toString());
    const callback = new URL(captured.redirectUri);
    callback.searchParams.set('code', 'c'.repeat(43)); callback.searchParams.set('state', 'x'.repeat(43));
    assert.equal(await request(callback), 400);
    callback.searchParams.set('state', 'å'.repeat(43)); assert.equal(await request(callback), 400);
    callback.searchParams.set('state', captured.state); assert.equal(await request(callback), 200);
  }, { timeoutMs: 2000 });
  assert.equal(result.code, 'c'.repeat(43)); assert.equal(result.redirect_uri, captured.redirectUri);
  assert.equal(createHash('sha256').update(result.code_verifier).digest('base64url'), captured.codeChallenge);
  await assert.rejects(request(new URL(captured.redirectUri)));
});
test('timed-out and cancelled native browser flows close their listeners', async () => {
  let callback;
  await assert.rejects(browserSignIn('https://collab.test', async url => { callback = JSON.parse(Buffer.from(url.split('#native-login=')[1], 'base64url')).redirectUri; }, { timeoutMs: 25 }), /timed out/);
  await assert.rejects(request(callback));
  const controller = new AbortController();
  await assert.rejects(browserSignIn('https://collab.test', async () => controller.abort(), { signal: controller.signal }), /cancelled/);
});
