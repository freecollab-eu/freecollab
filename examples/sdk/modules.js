import {createClient} from '@freecollab/components';
import {defineCustomElements} from '@freecollab/components/elements';
import {createSessionTokenFactory} from '@freecollab/components/auth';

defineCustomElements();
const client = createClient({
  apiBase: 'https://collaboration.your-company.eu/api',
  workspaceId: 'replace-with-workspace-id',
  channelId: 'replace-with-channel-id',
  // EXAMPLE host backend endpoint: your own authenticated backend implements
  // this route and returns a current scoped FreeCollab session or 401.
  tokenFactory: createSessionTokenFactory({endpoint: '/your-application/current-collaboration-session'}),
});

for (const element of document.querySelectorAll('freecollab-tasks,freecollab-documents,freecollab-calendar,freecollab-meetings,freecollab-workflows,freecollab-decisions,freecollab-search')) {
  element.options = {locale: 'sv'};
  element.client = client;
}
client.on('navigate', ({entityType, entityId}) => {
  // Your router can open the corresponding resource in your product or the
  // trusted full workspace. The server still checks every resource grant.
  document.querySelector('#events').textContent = entityType + ': ' + entityId;
});
document.querySelector('#reauthenticate').onclick = () => void client.reauthenticate().catch(() => {});
document.querySelector('#disconnect').onclick = () => client.destroy();
window.addEventListener('pagehide', () => client.destroy(), {once: true});
