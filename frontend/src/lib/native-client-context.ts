export interface NativeClientContext {
  protocol: 'freecollab-native-v1'; clientKind: 'desktop' | 'mobile';
  redirectUri: string; state: string; codeChallenge: string; expiresAt: number;
}
export const nativeContextKey = 'freecollab.native-client.context.v1';
export function validateNativeContext(value: unknown, now = Date.now()): NativeClientContext {
  if (!value || typeof value !== 'object') throw new Error('Invalid native client connection.');
  const v = value as NativeClientContext;
  const callback = new URL(v.redirectUri);
  const desktop = v.clientKind === 'desktop' && callback.protocol === 'http:' && callback.hostname === '127.0.0.1'
    && /^\d{4,5}$/.test(callback.port) && Number(callback.port) >= 1024 && Number(callback.port) <= 65535
    && callback.pathname === '/freecollab-native/callback' && !callback.username && !callback.password && !callback.search && !callback.hash
    && v.redirectUri === `http://127.0.0.1:${callback.port}/freecollab-native/callback`;
  const mobile = v.clientKind === 'mobile' && v.redirectUri === 'freecollab://native/complete';
  if (v.protocol !== 'freecollab-native-v1' || (!desktop && !mobile)
    || !/^[A-Za-z0-9_-]{43}$/.test(v.state) || !/^[A-Za-z0-9_-]{43}$/.test(v.codeChallenge)
    || !Number.isFinite(v.expiresAt) || v.expiresAt <= now || v.expiresAt > now + 601_000)
    throw new Error('Invalid native client connection.');
  return { protocol: v.protocol, clientKind: v.clientKind, redirectUri: v.redirectUri, state: v.state, codeChallenge: v.codeChallenge, expiresAt: v.expiresAt };
}
