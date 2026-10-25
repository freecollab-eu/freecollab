import { EncryptedWorkClient, type EncryptedWorkRecord, type EncryptedCollectionItem } from './encrypted-work-api';
import type { CollectionDraft, CollectionItem, ContentCollection } from './content-collections-api';

export type Item = CollectionItem;
export type Collection = ContentCollection;
const canonicalItem = (item: CollectionItem): EncryptedCollectionItem => ({ kind: item.kind, id: item.id, ...(item.parent_id ? { parent_id: item.parent_id } : {}) });
const key = (item: CollectionItem) => item.kind + '\0' + item.id + '\0' + (item.parent_id || '');
async function generation(row: EncryptedWorkRecord) {
  return [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(row.root_event_id)))].map(value => value.toString(16).padStart(2, '0')).join('').slice(0, 32);
}
async function value(channel: string, row: EncryptedWorkRecord): Promise<ContentCollection> {
  return { id: row.id, channel_id: channel, title: row.selected.fields.title, version: row.history.length,
    generation: await generation(row), created_by: row.created_by, created_at: new Date(row.history[0].timestamp).toISOString(),
    items: row.selected.fields.items || [] };
}
async function use<T>(channel: string, operation: (client: EncryptedWorkClient) => Promise<T>) {
  const client = new EncryptedWorkClient(channel);
  try { return await operation(client); } finally { client.stop(); }
}
async function current(client: EncryptedWorkClient, row: ContentCollection) {
  const view = await client.load(), found = view.records.find(item => item.id === row.id && item.kind === 'collection');
  if (!found || found.selected.archived || found.invalid.length || found.conflicts || found.history.length !== row.version || await generation(found) !== row.generation)
    throw Error('This encrypted collection changed. Refresh and review its current version.');
  return found;
}

/** Same collection contract as native work, with no resource/title sidecar. */
export const encryptedCollectionsApi = {
  list: (channel: string) => use(channel, async client => {
    const view = await client.load();
    const rows = view.records.filter(row => row.kind === 'collection' && !row.selected.archived);
    if (rows.some(row => row.invalid.length || row.conflicts)) throw Error('Review competing encrypted collection versions in encrypted work before continuing.');
    return Promise.all(rows.map(row => value(channel, row)));
  }),
  create: (channel: string, draft: CollectionDraft) => use(channel, async client => {
    const row = await client.save('collection', { title: draft.title, body: '', items: draft.items.map(canonicalItem) }, undefined, { id: draft.id });
    return value(channel, row);
  }),
  rename: (channel: string, row: ContentCollection, title: string) => use(channel, async client => {
    const original = await current(client, row);
    return value(channel, await client.save('collection', { ...original.selected.fields, title }, original));
  }),
  removeCollection: (channel: string, row: ContentCollection) => use(channel, async client => {
    const original = await current(client, row);
    if (original.selected.fields.items?.length) throw Error('Remove the collection items before deleting this encrypted collection.');
    await client.save('collection', original.selected.fields, original, { archived: true });
  }),
  addItem: (channel: string, row: ContentCollection, item: CollectionItem) => use(channel, async client => {
    const original = await current(client, row), items = original.selected.fields.items || [];
    if (items.some(existing => key(existing) === key(item))) return value(channel, original);
    return value(channel, await client.save('collection', { ...original.selected.fields, items: [...items, canonicalItem(item)] }, original));
  }),
  removeItem: (channel: string, row: ContentCollection, item: CollectionItem) => use(channel, async client => {
    const original = await current(client, row), items = (original.selected.fields.items || []).filter(existing => key(existing) !== key(item));
    return value(channel, await client.save('collection', { ...original.selected.fields, items }, original));
  }),
};
