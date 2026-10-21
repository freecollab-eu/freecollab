import type { CollaborationClient } from './client.js';
import type { ModuleOptions } from './types.js';
import { element, mountView, timestamp } from './view.js';
export function mountFeed(target: HTMLElement | ShadowRoot, client: CollaborationClient, options: ModuleOptions = {}) {
  return mountView(target, client, 'feed', options, signal => client.listPosts(signal), post => {
    const row = element('li'); row.append(element('h3', post.fields.title), element('p', post.fields.body), timestamp(post.created_at)); return row;
  }, context => {
    const form = element('form'), titleLabel = element('label', context.labels.post), title = element('input'), bodyLabel = element('label', context.labels.message), input = element('textarea'), publish = element('button', context.labels.publish);
    title.required = true; title.maxLength = 160; title.setAttribute('aria-label', context.labels.post); titleLabel.append(title);
    input.required = true; input.maxLength = 20000; input.setAttribute('aria-label', context.labels.message); bodyLabel.append(input); publish.className = 'fc-primary'; publish.type = 'submit'; form.append(titleLabel, bodyLabel, publish);
    form.addEventListener('submit', event => { event.preventDefault(); const content = input.value, name = title.value; void context.run(async signal => { await client.createPost(content, name, signal); input.value = ''; title.value = ''; }); }); context.formArea.append(form);
  });
}
