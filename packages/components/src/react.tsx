import { createElement, useEffect, useRef, type CSSProperties } from 'react';
import type { CollaborationClient } from './client.js';
import { mountChat } from './chat.js';
import { mountFiles } from './files.js';
import { mountFeed } from './feed.js';
import { mountNotifications } from './notifications.js';
import {mountTasks} from './tasks.js';
import {mountDocuments} from './documents.js';
import {mountMeetings} from './meetings.js';
import {mountCalendar} from './calendar.js';
import {mountWorkflows} from './workflows.js';
import {mountDecisions} from './decisions.js';
import {mountSearch} from './search.js';
import type { ModuleOptions, MountedComponent } from './types.js';
export interface CollaborationProps extends ModuleOptions { client: CollaborationClient; className?: string; style?: CSSProperties }
function wrapper(mount: (target: HTMLElement, client: CollaborationClient, options: ModuleOptions) => MountedComponent) {
  return function CollaborationComponent({ client, title, locale, labels, errorMessages, pollIntervalMs, className, style }: CollaborationProps) {
    const host = useRef<HTMLDivElement>(null);
    useEffect(() => { if (!host.current) return; const view = mount(host.current, client, { title, locale, labels, errorMessages, pollIntervalMs }); return () => view.destroy(); }, [client, title, locale, labels, errorMessages, pollIntervalMs]);
    return createElement('div', { ref: host, className, style });
  };
}
/** Wrappers own their mounted view; the host owns and destroys the shared client. */
export const Chat = wrapper(mountChat);
export const Files = wrapper(mountFiles);
export const Feed = wrapper(mountFeed);
export const Notifications = wrapper(mountNotifications);
export const Tasks = wrapper(mountTasks);
export const Documents = wrapper(mountDocuments);
export const Meetings = wrapper(mountMeetings);
export const Calendar = wrapper(mountCalendar);
export const Workflows = wrapper(mountWorkflows);
export const Decisions = wrapper(mountDecisions);
export const Search = wrapper(mountSearch);
