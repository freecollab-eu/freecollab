import { createClient, type TokenContext, type MountedComponent } from '@freecollab/components';
import { mountChat } from '@freecollab/components/chat';
import { mountFiles } from '@freecollab/components/files';
import { mountFeed } from '@freecollab/components/feed';
import { mountNotifications } from '@freecollab/components/notifications';
import {mountTasks} from '@freecollab/components/tasks';
import {mountDocuments} from '@freecollab/components/documents';
import {mountMeetings} from '@freecollab/components/meetings';
import {mountCalendar} from '@freecollab/components/calendar';
import {mountWorkflows} from '@freecollab/components/workflows';
import {mountDecisions} from '@freecollab/components/decisions';
import {mountSearch} from '@freecollab/components/search';
import { defineCustomElements, type CollaborationElement } from '@freecollab/components/elements';

const client = createClient({
  apiBase: 'https://collab.example/api', workspaceId: 'team', channelId: 'shared',
  tokenFactory: async ({ signal, scope, reason }: TokenContext) => {
    const labels: string[] = [scope.workspaceId, scope.channelId, reason];
    return signal.aborted || !labels.length ? null : 'customer-user-token';
  },
});
const views: MountedComponent[] = [mountChat(document.body, client, {locale:'sv'}), mountFiles(document.body, client), mountFeed(document.body, client), mountNotifications(document.body, client)];
defineCustomElements();
const element: CollaborationElement = document.createElement('freecollab-chat'); element.client = client;
views.push(mountTasks(document.body,client),mountDocuments(document.body,client),mountMeetings(document.body,client),mountCalendar(document.body,client),mountWorkflows(document.body,client),mountDecisions(document.body,client),mountSearch(document.body,client));
for (const view of views) view.destroy();
// @ts-expect-error An explicit channel scope is required.
createClient({apiBase:'https://collab.example/api',workspaceId:'team',tokenFactory:()=>null});
client.destroy();
