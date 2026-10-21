import type {CollaborationClient} from './client.js';
import type {ModuleOptions} from './types.js';
import {button, element, mountView, timestamp} from './view.js';
export function mountMeetings(target: HTMLElement | ShadowRoot, client: CollaborationClient, options: ModuleOptions = {}) {
  return mountView(target, client, 'meetings', options, signal => client.listMeetings(signal), (meeting, context) => {
    const row = element('li'); row.append(element('h3', context.labels.meetings), timestamp(meeting.created_at), element('p', meeting.ended ? context.labels.done : context.labels.doing));
    if (!meeting.ended) {row.append(button(context.labels.openItem, () => client.openResource('meeting', meeting.id))); if (meeting.host_user_id === client.state.userId) row.append(button(context.labels.endMeeting, () => void context.run(async signal => {await client.endMeeting(meeting.id, signal);})));} return row;
  }, context => {context.formArea.append(button(context.labels.startMeeting, () => void context.run(async signal => {const meeting = await client.createMeeting(signal); if (!signal.aborted && context.ready()) client.openResource('meeting', meeting.id);})));});
}
