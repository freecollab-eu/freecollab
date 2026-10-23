import { readFileSync, writeFileSync } from 'node:fs';

let origin = "'none'";
if (process.env.VITE_PUBLIC_DEMO_URL?.trim()) {
  const url = new URL(process.env.VITE_PUBLIC_DEMO_URL.trim());
  const local = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
  if (url.username || url.password || url.protocol !== 'https:' && !(url.protocol === 'http:' && local)) throw new Error('The demo frame requires HTTPS or loopback.');
  origin = url.origin;
}
const template = readFileSync(new URL('./nginx.conf.template', import.meta.url), 'utf8');
if (!template.includes('__PRODUCT_DEMO_FRAME_ORIGIN__')) throw new Error('Missing frame policy placeholder.');
writeFileSync(process.argv[2] || '/tmp/freecollab-website-nginx.conf.template', template.replace('__PRODUCT_DEMO_FRAME_ORIGIN__', origin));
