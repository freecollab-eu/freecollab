import type { Token } from './api';

/** Only the dedicated demonstration backend creates this ordinary account session. */
export interface ProductDemoSession {
  session: Token;
  workspace_id: string;
  channel_id: string;
  visit_id: string;
  expires_at: string;
}

function object(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}

export function verifiedProductDemoSession(value: unknown, now = Date.now()): ProductDemoSession {
  if (!object(value) || !object(value.session)) throw new Error('The example workspace returned an invalid session.');
  const session = value.session;
  const identifier = (input: unknown): input is string => typeof input === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(input);
  if (!identifier(value.workspace_id) || !identifier(value.channel_id) || !identifier(value.visit_id) || !identifier(session.user_id)
    || typeof session.access_token !== 'string' || session.access_token.length > 8192 || !/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(session.access_token)
    || session.token_type !== 'bearer' || typeof session.public_key !== 'string' || session.public_key.length > 16384
    || session.encrypted_private_key !== null
    || typeof value.expires_at !== 'string' || !/^\d{4}-\d\d-\d\dT.+(?:Z|[+-]\d\d:\d\d)$/.test(value.expires_at)) {
    throw new Error('The example workspace returned an invalid session.');
  }
  const expiry = Date.parse(value.expires_at);
  if (!Number.isFinite(expiry) || expiry <= now || expiry > now + 86400000) throw new Error('The example workspace returned an invalid expiry.');
  return value as unknown as ProductDemoSession;
}

/** A build-time website link, never an untrusted return URL from query parameters. */
export function productDemoWebsiteURL(configured?: string): string {
  const fallback = 'https://freecollab-website-production.up.railway.app/';
  if (!configured) return fallback;
  try {
    const url = new URL(configured);
    const loopback = url.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
    if ((url.protocol !== 'https:' && !loopback) || url.username || url.password || url.search || url.hash) return fallback;
    return url.href;
  } catch { return fallback; }
}
