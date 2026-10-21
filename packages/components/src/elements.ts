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
import type { ClientEvents, ModuleOptions, MountedComponent } from './types.js';

export interface CollaborationElement extends HTMLElement {
  /** Set a client as a JS property. Never place a bearer token in an HTML attribute. */
  client: CollaborationClient | null;
  options: ModuleOptions;
  refresh(): Promise<void>;
  destroy(): void;
}
const modules = { chat: mountChat, files: mountFiles, feed: mountFeed, notifications: mountNotifications, tasks: mountTasks, documents: mountDocuments, meetings: mountMeetings, calendar: mountCalendar, workflows: mountWorkflows, decisions: mountDecisions, search: mountSearch };
/** Explicit, idempotent registration; importing the SDK never mounts UI or signs a user in. */
export function defineCustomElements(prefix = 'freecollab') {
  if (!/^[a-z][a-z0-9-]*$/.test(prefix)) throw new Error('Use a lowercase custom-element prefix.');
  for (const [name, mount] of Object.entries(modules)) {
    const tag = prefix + '-' + name; if (customElements.get(tag)) continue;
    customElements.define(tag, class extends HTMLElement implements CollaborationElement {
      private binding: CollaborationClient | null = null;
      private settings: ModuleOptions = {};
      private mounted: MountedComponent | null = null;
      private subscriptions: Array<() => void> = [];
      get client() { return this.binding; }
      set client(value: CollaborationClient | null) { if (value !== this.binding) { this.destroy(); this.binding = value; this.connect(); } }
      get options() { return this.settings; }
      set options(value: ModuleOptions) { this.destroy(); this.settings = { ...value }; this.connect(); }
      connectedCallback() { this.connect(); }
      disconnectedCallback() { this.destroy(); }
      private connect() {
        if (!this.isConnected || this.mounted || !this.binding) return;
        const forward = <K extends keyof ClientEvents>(event: K) => this.binding!.on(event, detail => this.dispatchEvent(new CustomEvent('fc-' + event, { detail, bubbles: true, composed: true })));
        this.subscriptions = [forward('state'), forward('error'), forward('notification'), forward('navigate')];
        this.mounted = mount(this, this.binding, this.settings);
      }
      refresh() { return this.mounted?.refresh() || Promise.resolve(); }
      destroy() { this.mounted?.destroy(); this.mounted = null; for (const dispose of this.subscriptions.splice(0)) dispose(); }
    });
  }
}
declare global {
  interface HTMLElementTagNameMap {
    'freecollab-chat': CollaborationElement; 'freecollab-files': CollaborationElement;
    'freecollab-feed': CollaborationElement; 'freecollab-notifications': CollaborationElement;
    'freecollab-tasks': CollaborationElement; 'freecollab-documents': CollaborationElement;
    'freecollab-meetings': CollaborationElement; 'freecollab-calendar': CollaborationElement;
    'freecollab-workflows': CollaborationElement; 'freecollab-decisions': CollaborationElement; 'freecollab-search': CollaborationElement;
  }
}
