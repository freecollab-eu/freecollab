import type { DemoDestination } from './demo-showroom';

/** A demo points at the real sample installation, never a second feature UI. */
export function productDemoUrl(base: string | undefined, origin: string, destination: DemoDestination = {}, locale?: string): string {
  if (!base?.trim()) throw new Error('A separate sample installation must be configured.');
  const url = new URL(base.trim(), origin);
  const loopback = ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname);
  if (url.username || url.password || url.protocol !== 'https:' && !(url.protocol === 'http:' && loopback)) throw new Error('The product demo needs a trusted HTTPS installation.');
  url.hash = '';
  url.search = '';
  url.searchParams.set('section', destination.section && ['chat', 'projects', 'documents', 'connections', 'appearance'].includes(destination.section) ? destination.section : 'chat');
  if (destination.section === 'connections' && ['microsoft', 'google'].includes(destination.provider || '')) url.searchParams.set('provider', destination.provider!);
  if (locale === 'en' || locale === 'sv') url.searchParams.set('locale', locale);
  return url.href;
}
