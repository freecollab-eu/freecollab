import type { CollaborationClient } from './client.js';
import type { ModuleOptions } from './types.js';
import { button, element, mountView } from './view.js';
export function mountFiles(target: HTMLElement | ShadowRoot, client: CollaborationClient, options: ModuleOptions = {}) {
  let preview: HTMLElement | null = null, objectUrl: string | null = null;
  const cleanup = () => {if (objectUrl) URL.revokeObjectURL(objectUrl); objectUrl = null; preview?.replaceChildren();};
  const unsubscribe = client.on('state', state => {if (state.status !== 'ready') cleanup();});
  const view = mountView(target, client, 'files', options, signal => client.listFiles(signal), (file, context) => {
    const row = element('li'); row.append(element('p', file.filename)); const metadata = element('span', Math.ceil(file.size / 1024) + ' KB' + (file.version_number ? ' · v' + file.version_number : '')); metadata.className = 'fc-meta'; row.append(metadata);
    row.append(button(context.labels.download, () => void context.run(async signal => {
      const result = await client.downloadFile(file.id, signal); if (signal.aborted || !context.ready()) return;
      const url = URL.createObjectURL(result.blob), link = element('a'); link.href = url; link.download = result.file.filename; link.rel = 'noopener'; link.click(); URL.revokeObjectURL(url);
    })), button(context.labels.preview, () => void context.run(async signal => {
      const result = await client.downloadFile(file.id, signal); if (signal.aborted || !context.ready() || !preview) return; cleanup();
      preview.append(element('h3', file.filename), button(context.labels.close, cleanup));
      if (/^text\//.test(file.content_type) || ['application/json', 'application/xml'].includes(file.content_type)) {const text = await result.blob.slice(0, 1024 * 1024).text(); if (signal.aborted || !context.ready()) return; const content = element('pre', text); content.style.whiteSpace = 'pre-wrap'; content.style.overflowWrap = 'anywhere'; preview.append(content);}
      else if (['image/png', 'image/jpeg', 'image/gif', 'image/webp'].includes(file.content_type)) {objectUrl = URL.createObjectURL(result.blob); const image = element('img'); image.src = objectUrl; image.alt = file.filename; image.style.maxWidth = '100%'; preview.append(image);}
      else preview.append(element('p', context.labels.fileLimit));
    })), button(context.labels.versions, () => void context.run(async signal => {const versions = await client.fileVersions(file.id, signal); if (signal.aborted || !context.ready() || !preview) return; cleanup(); preview.append(element('h3', context.labels.versions), ...versions.map(version => element('p', version.filename + ' · v' + (version.version_number || 1))), button(context.labels.close, cleanup));}))); return row;
  }, context => {
    const form = element('form'), label = element('label', context.labels.file), input = element('input'), upload = element('button', context.labels.upload);
    input.type = 'file'; input.required = true; input.setAttribute('aria-label', context.labels.file); label.append(input); upload.className = 'fc-primary'; upload.type = 'submit'; form.append(label, element('p', context.labels.fileLimit), upload);
    form.addEventListener('submit', event => { event.preventDefault(); const file = input.files?.[0]; if (file) void context.run(async signal => { await client.uploadFile(file, signal); input.value = ''; }); }); preview = element('section'); context.formArea.append(form, preview);
  });
  return {refresh: view.refresh, destroy() {cleanup(); unsubscribe(); preview = null; view.destroy();}};
}
