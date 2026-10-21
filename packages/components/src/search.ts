import type {CollaborationClient} from './client.js';
import type {CollaborationMessage, ModuleOptions} from './types.js';
import {button, element, mountView, timestamp} from './view.js';
import {field, submit} from './forms.js';
export function mountSearch(target: HTMLElement | ShadowRoot, client: CollaborationClient, options: ModuleOptions = {}) {
  let query = '';
  return mountView<CollaborationMessage>(target, client, 'search', options, signal => query ? client.searchMessages(query, signal) : client.listMessages(signal).then(() => []), (message, context) => {const row = element('li'); row.append(element('p', message.content), timestamp(message.created_at), button(context.labels.openItem, () => client.openResource('message', message.id))); return row;}, context => {
    query = ''; const form = element('form'), input = field(form, context.labels.searchRecent, false, 200); input.required = true;
    submit(form, context.labels.search, () => {query = input.value.trim(); void context.run(async () => {});}); context.formArea.append(element('p', context.labels.searchLimit), form);
  });
}
