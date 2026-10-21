import type {CollaborationClient} from './client.js';
import type {ModuleOptions} from './types.js';
import {button, element, mountView} from './view.js';
import {field, submit} from './forms.js';
import {initialDocument, NativeDocumentSession} from './shared-document.js';
export function mountDocuments(target: HTMLElement | ShadowRoot, client: CollaborationClient, options: ModuleOptions = {}) {
  let session: NativeDocumentSession | null = null, editor: HTMLElement | null = null;
  const view = mountView(target, client, 'documents', options, signal => client.listDocuments(signal), (record, context) => {
    const row = element('li'); row.append(element('h3', record.title || context.labels.documents), button(context.labels.edit, () => {
      session?.destroy(); if (!editor) return; editor.replaceChildren();
      const form = element('form'), group = element('fieldset'), title = field(group, context.labels.title), body = field(group, context.labels.body, true, 50000), status = element('p'); group.style.border = '0'; group.style.padding = '0'; group.style.minWidth = '0'; group.disabled = title.disabled = body.disabled = true; form.append(group);
      let save: HTMLButtonElement;
      const active = new NativeDocumentSession(client, record, state => {if (!context.ready() || active !== session) return; if (title.value !== state.title) title.value = state.title; if (body.value !== state.body) body.value = state.body; group.disabled = title.disabled = body.disabled = save.disabled = !state.ready || state.archived; status.textContent = state.pending ? (options.locale === 'sv' ? 'Ändringar väntar på synkronisering.' : 'Changes pending synchronization.') : '';}, context.error);
      session = active; title.addEventListener('input', () => {try {active.replace('title', title.value);} catch (error) {context.error(error);}}); body.addEventListener('input', () => {try {active.replace('body', body.value);} catch (error) {context.error(error);}});
      save = submit(form, context.labels.save, () => void context.run(async () => {await active.sync();})); save.disabled = true; group.append(save); form.append(status, button(context.labels.close, () => {active.destroy(); if (session === active) session = null; editor?.replaceChildren();})); editor.append(form); active.start();
    })); return row;
  }, context => {
    session?.destroy(); session = null; const form = element('form'), title = field(form, context.labels.title); title.required = true;
    submit(form, context.labels.create, () => void context.run(async signal => {const value = title.value.trim(); await client.createDocument(value, initialDocument(value), signal); title.value = '';})); editor = element('section'); context.formArea.append(form, editor);
  });
  return {refresh: view.refresh, destroy() {session?.destroy(); session = null; editor = null; view.destroy();}};
}
