import type { CollaborationClient } from './client.js';
import type { ModuleOptions } from './types.js';
import { button, element, mountView, timestamp } from './view.js';
import {field, submit} from './forms.js';
export function mountChat(target: HTMLElement | ShadowRoot, client: CollaborationClient, options: ModuleOptions = {}) {
  let editor: HTMLElement | null = null;
  return mountView(target, client, 'chat', options, signal => client.listMessages(signal), (message, context) => {
    const row = element('li'); row.append(element('p', message.content), timestamp(message.created_at));
    const actions = element('div'); actions.className = 'fc-actions';
    actions.append(button(context.labels.reply + (message.thread_count ? ' (' + message.thread_count + ')' : ''), () => {
      if (!editor) return; editor.replaceChildren(); const form = element('form'), replies = element('div'), input = field(form, context.labels.reply, true, 20000); input.required = true;
      submit(form, context.labels.reply, () => void context.run(async signal => {await client.replyMessage(message.id, input.value, signal); input.value = ''; const rows = await client.listThread(message.id, signal); if (!signal.aborted && context.ready()) replies.replaceChildren(...rows.map(reply => element('p', reply.content)));}));
      form.append(button(context.labels.cancel, () => editor?.replaceChildren())); editor.append(element('h3', context.labels.replies), element('p', message.content), replies, form);
      void context.run(async signal => {const rows = await client.listThread(message.id, signal); if (!signal.aborted && context.ready()) replies.replaceChildren(...rows.map(reply => element('p', reply.content)));});
    }), button(context.labels.react, () => void context.run(async signal => {const reactions = await client.listReactions(message.id, signal); await client.reactMessage(message.id, '👍', !!reactions.find(row => row.emoji === '👍' && row.me), signal);})), button(context.labels.bookmark, () => void context.run(async signal => {await client.bookmarkMessage(message.id, signal);})));
    if (message.author_id === client.state.userId) actions.append(button(context.labels.edit, () => {
      if (!editor) return; editor.replaceChildren(); const form = element('form'), input = field(form, context.labels.message, true, 20000); input.value = message.content; input.required = true;
      submit(form, context.labels.save, () => void context.run(async signal => {await client.editMessage(message.id, input.value, signal); editor?.replaceChildren();})); form.append(button(context.labels.cancel, () => editor?.replaceChildren())); editor.append(form);
    }), button(context.labels.remove, () => {
      if (!editor) return; editor.replaceChildren(element('p', message.content), button(context.labels.remove, () => void context.run(async signal => {await client.deleteMessage(message.id, signal); editor?.replaceChildren();})), button(context.labels.cancel, () => editor?.replaceChildren()));
    }));
    row.append(actions); return row;
  }, context => {
    const form = element('form'), label = element('label', context.labels.message), input = element('textarea'), send = element('button', context.labels.send);
    input.required = true; input.maxLength = 20000; input.setAttribute('aria-label', context.labels.message); label.append(input); send.className = 'fc-primary'; send.type = 'submit'; form.append(label, send);
    form.addEventListener('submit', event => { event.preventDefault(); const content = input.value; void context.run(async signal => { await client.sendMessage(content, signal); input.value = ''; }); }); editor = element('section'); context.formArea.append(form, editor);
  });
}
