'use strict';
const http = require('node:http');
const { randomBytes, createHash, timingSafeEqual } = require('node:crypto');
const { deploymentOrigin } = require('./policy.cjs');
const b64 = bytes => bytes.toString('base64url');
function equals(first, second) {
  if (typeof first !== 'string' || typeof second !== 'string') return false;
  const a = Buffer.from(first), b = Buffer.from(second);
  return a.length === b.length && timingSafeEqual(a, b);
}
async function browserSignIn(origin, openBrowser, { timeoutMs = 600000, signal } = {}) {
  origin = deploymentOrigin(origin);
  const verifier = b64(randomBytes(32)), state = b64(randomBytes(32));
  const challenge = b64(createHash('sha256').update(verifier).digest());
  let resolveCallback, rejectCallback;
  const callback = new Promise((resolve, reject) => { resolveCallback = resolve; rejectCallback = reject; });
  const server = http.createServer((request, response) => {
    response.setHeader('Cache-Control', 'no-store'); response.setHeader('Referrer-Policy', 'no-referrer');
    response.setHeader('Content-Security-Policy', "default-src 'none'; frame-ancestors 'none'");
    const url = new URL(request.url || '/', 'http://127.0.0.1');
    if (request.method !== 'GET' || url.pathname !== '/freecollab-native/callback'
      || !equals(url.searchParams.get('state'), state) || !/^[A-Za-z0-9_-]{43}$/.test(url.searchParams.get('code') || '')) {
      response.writeHead(400, { 'Content-Type': 'text/plain; charset=utf-8' }); response.end('This connection is invalid. Return to FreeCollab and start again.'); return;
    }
    response.writeHead(200, { 'Content-Type': 'text/plain; charset=utf-8' });
    response.end('Your app is connected. You can close this browser tab and return to FreeCollab.');
    resolveCallback(url.searchParams.get('code'));
  });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  const redirect = `http://127.0.0.1:${server.address().port}/freecollab-native/callback`;
  const context = { protocol: 'freecollab-native-v1', clientKind: 'desktop', redirectUri: redirect,
    state, codeChallenge: challenge, expiresAt: Date.now() + Math.min(timeoutMs, 600000) };
  const timeout = setTimeout(() => rejectCallback(new Error('Native sign-in timed out. Start again.')), timeoutMs);
  const abort = () => rejectCallback(new Error('Native sign-in was cancelled.'));
  signal?.addEventListener('abort', abort, { once: true });
  try {
    if (signal?.aborted) throw new Error('Native sign-in was cancelled.');
    await openBrowser(origin + '/#native-login=' + b64(Buffer.from(JSON.stringify(context))));
    const code = await callback;
    return { code, code_verifier: verifier, redirect_uri: redirect };
  } finally {
    clearTimeout(timeout); signal?.removeEventListener('abort', abort);
    server.closeAllConnections(); server.close();
  }
}
module.exports = { browserSignIn };
