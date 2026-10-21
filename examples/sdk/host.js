// This source is consumed by your application's normal bundler after installing
// the reviewed @freecollab/components tarball. No CDN or global script is needed.
import { createClient } from '@freecollab/components';
import { defineCustomElements } from '@freecollab/components/elements';
import { createSessionTokenFactory } from '@freecollab/components/auth';
defineCustomElements();
const client = createClient({
  apiBase: 'https://collaboration.your-company.eu/api',
  workspaceId: 'replace-with-workspace-id', channelId: 'replace-with-channel-id',
  // This is an EXAMPLE host route implemented by your own authenticated backend.
  // The helper POSTs the explicit workspace/channel, rejects redirects and
  // returns null on 401; your backend authorizes scope and protects against CSRF.
  tokenFactory: createSessionTokenFactory({endpoint: '/your-application/current-collaboration-session'}),
});
for (const element of document.querySelectorAll('freecollab-chat,freecollab-files,freecollab-feed,freecollab-notifications')) { element.options = { locale: 'en' }; element.client = client; }
client.on('navigate', event => { document.querySelector('#events').textContent = 'Your router opens ' + event.entityType + ' ' + event.entityId; });
client.on('notification', () => { document.querySelector('#events').textContent = 'A channel notification arrived.'; });
document.querySelector('#reauthenticate').onclick = () => void client.reauthenticate().catch(() => {});
document.querySelector('#disconnect').onclick = () => client.destroy();
window.addEventListener('pagehide', () => client.destroy(), { once: true });
// Also call client.reauthenticate() immediately from your host's login/logout
// callback. Call client.setScope({workspaceId,channelId}) for explicit navigation.
