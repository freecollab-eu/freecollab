import type { CollaborationClient } from './client.js';
import type { ModuleOptions } from './types.js';
import { button, element, mountView, timestamp } from './view.js';
export function mountNotifications(target: HTMLElement | ShadowRoot, client: CollaborationClient, options: ModuleOptions = {}) {
  return mountView(target, client, 'notifications', options, signal => client.listNotifications(signal), (notification, context) => {
    const row = element('li'); row.append(element('h3', notification.title)); if (notification.body) row.append(element('p', notification.body));
    const state = element('span', notification.read ? context.labels.read : context.labels.unread); state.className = 'fc-meta'; row.append(state, timestamp(notification.created_at));
    const actions = element('div'); actions.className = 'fc-actions'; actions.append(button(context.labels.open, () => client.navigate(notification)));
    if (!notification.read) actions.append(button(context.labels.markRead, () => void context.run(async signal => { await client.markNotificationRead(notification.id, signal); }))); row.append(actions); return row;
  });
}
