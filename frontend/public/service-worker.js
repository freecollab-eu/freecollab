/* Only public application assets enter CacheStorage. No authenticated API data. */
const SHELL = 'freecollab-public-shell-v1';
const ASSETS = 'freecollab-public-assets-v1';
const PUBLIC = ['/', '/manifest.webmanifest', '/favicon.svg', '/icons/app-192.png', '/icons/app-512.png'];
const staticAsset = url => url.origin === self.location.origin && /^\/assets\/[A-Za-z0-9_./-]+$/.test(url.pathname);
async function rememberIndex(response) {
  if (!response.ok || !(response.headers.get('content-type') || '').includes('text/html')) return;
  const text = await response.clone().text();
  const paths = [...text.matchAll(/(?:src|href)=["'](\/assets\/[A-Za-z0-9_.-]+\.(?:js|css))["']/g)].map(match => match[1]);
  const cache = await caches.open(SHELL);
  await cache.put('/', response.clone());
  for (const path of paths) {
    try { const result = await fetch(path, { credentials: 'omit' }); if (result.ok) await cache.put(path, result); } catch { /* Preserve an already usable shell. */ }
  }
  for (const request of await cache.keys()) {
    const path = new URL(request.url).pathname;
    if (!PUBLIC.includes(path) && !paths.includes(path)) await cache.delete(request);
  }
}
self.addEventListener('install', event => event.waitUntil((async () => {
  const cache = await caches.open(SHELL);
  for (const path of PUBLIC) {
    const response = await fetch(path, { credentials: 'omit', cache: 'reload' });
    if (response.ok) await cache.put(path, response);
  }
  const response = await fetch('/', { credentials: 'omit', cache: 'reload' }); await rememberIndex(response);
  await self.skipWaiting();
})()));
self.addEventListener('activate', event => event.waitUntil((async () => {
  for (const name of await caches.keys()) if (name.startsWith('freecollab-public-') && ![SHELL, ASSETS].includes(name)) await caches.delete(name);
  await self.clients.claim();
})()));
self.addEventListener('fetch', event => {
  const request = event.request, url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin || request.headers.has('Authorization')) return;
  if (request.mode === 'navigate' && ['/', '/index.html'].includes(url.pathname) && !url.search) {
    event.respondWith((async () => {
      try { const response = await fetch(request); event.waitUntil(rememberIndex(response.clone()).catch(() => {})); return response; }
      catch { const cached = await caches.match('/'); return cached || new Response('Open FreeCollab online once to install its public app shell.', { status: 503, headers: { 'Content-Type': 'text/plain' } }); }
    })()); return;
  }
  if (!PUBLIC.includes(url.pathname) && !(staticAsset(url) && ['script', 'style', 'font', 'worker', 'image'].includes(request.destination))) return;
  if (url.search) return;
  event.respondWith((async () => {
    const existing = await caches.match(request); if (existing) return existing;
    const response = await fetch(request);
    if (response.ok && response.type === 'basic') {
      const cache = await caches.open(ASSETS); await cache.put(request, response.clone());
      const keys = await cache.keys(); for (const old of keys.slice(0, Math.max(0, keys.length - 100))) await cache.delete(old);
    }
    return response;
  })());
});
self.addEventListener('push', event => event.waitUntil((async () => {
  let data; try { data = event.data?.json(); } catch { return; }
  if (data?.type === 'subscription_confirmation' && /^[a-f0-9]{32}$/.test(data.subscription_id || '') && /^[A-Za-z0-9_-]{32,128}$/.test(data.challenge || '')) {
    for (const client of await self.clients.matchAll({ type: 'window', includeUncontrolled: true }))
      client.postMessage({ type: 'push_confirmation', subscription_id: data.subscription_id, challenge: data.challenge });
    await self.registration.showNotification('Confirm FreeCollab notifications', { body: 'Return to your open FreeCollab app to finish enabling notifications.', icon: '/icons/app-192.png', tag: 'freecollab-confirmation', data: { url: '/' } });
  } else if (data?.type === 'activity') {
    await self.registration.showNotification('New FreeCollab activity', { body: 'Open FreeCollab to view your updates.', icon: '/icons/app-192.png', badge: '/icons/app-192.png', tag: 'freecollab-activity', data: { url: '/#mobile=activity' } });
  }
})()));
self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil((async () => {
    const clients = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    const existing = clients.find(client => new URL(client.url).origin === self.location.origin);
    if (existing) { await existing.focus(); existing.postMessage({ type: 'open_activity' }); }
    else await self.clients.openWindow('/#mobile=activity');
  })());
});
