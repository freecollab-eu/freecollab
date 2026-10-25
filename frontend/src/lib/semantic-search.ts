import { api, type Channel } from './api';
import { loadSearchSources, verifySearchSource, type SearchSource } from './enterprise-search';
import { owner, assertOwner, listPrivate, savePrivate } from '../mobile/storage';

export interface SemanticHit extends SearchSource { score: number }
export interface SemanticResult { items: SemanticHit[]; complete: boolean; engine: string; index_revision?: unknown }
import { latentModel, rankSemantic, type LatentModel } from './latent-semantic';
export { latentModel, rankSemantic } from './latent-semantic';
interface LocalIndex { engine: 'local-lsa-v1'; fingerprint: string; items: SearchSource[]; model: LatentModel }
function projectOnDevice(items: SearchSource[], query: string, model: LatentModel | undefined, signal?: AbortSignal): Promise<{model: LatentModel; hits: SemanticHit[]}> {
  if (signal?.aborted) return Promise.reject(new DOMException('Cancelled', 'AbortError'));
  if (typeof Worker === 'undefined') { const projection = model || latentModel(items); return Promise.resolve({ model: projection, hits: rankSemantic(items, projection, query) }); }
  return new Promise((resolve, reject) => {
    const worker = new Worker(new URL('./semantic-search.worker.ts', import.meta.url), { type: 'module' });
    const stop = () => { worker.terminate(); signal?.removeEventListener('abort', cancel); };
    const cancel = () => { stop(); reject(new DOMException('Cancelled', 'AbortError')); };
    signal?.addEventListener('abort', cancel, {once: true});
    worker.onmessage = event => { stop(); event.data.error ? reject(new Error('The local semantic index could not be projected.')) : resolve(event.data); };
    worker.onerror = () => { stop(); reject(new Error('The local semantic worker is unavailable.')); };
    worker.postMessage({ items, query, model });
  });
}

async function secureSearch(channel: Channel, query: string, signal?: AbortSignal): Promise<SemanticResult> {
  const expected = owner(), captured = await loadSearchSources(channel, signal); assertOwner(expected);
  const fingerprint = captured.items.map(item => [item.kind, item.id, item.version].join(':')).join('|');
  const cached = (await listPrivate<LocalIndex>('semantic-index', expected)).find(row => row.id === expected.user + ':' + channel.id)?.value; assertOwner(expected);
  const items = captured.items.slice(0, 1000), retained = cached?.engine === 'local-lsa-v1' && cached.fingerprint === fingerprint ? cached.model : undefined;
  const {model, hits} = await projectOnDevice(items, query, retained, signal); assertOwner(expected);
  await savePrivate('semantic-index', expected.user + ':' + channel.id, { engine: 'local-lsa-v1', fingerprint, items, model } satisfies LocalIndex, expected); assertOwner(expected);
  if (signal?.aborted) throw new DOMException('Cancelled', 'AbortError');
  const latestChannel = await api.getChannel(channel.id); assertOwner(expected);
  if (latestChannel.workspace_id !== channel.workspace_id || !latestChannel.secure) throw new Error('The encrypted search scope changed.');
  const fresh = await loadSearchSources(latestChannel, signal); assertOwner(expected);
  const currentHits = hits.filter(hit => fresh.items.some(item => item.id === hit.id && item.kind === hit.kind && item.version === hit.version));
  return { items: currentHits, complete: captured.complete && fresh.complete && currentHits.length === hits.length && captured.items.length <= 1000, engine: 'device-local-lsa-v1' };
}

/** Native queries go only to this installation. Encrypted queries stay local. */
export async function semanticSearch(workspaceId: string, query: string, options: { scope: 'native' | 'encrypted'; channelId?: string; limit?: number; signal?: AbortSignal }): Promise<SemanticResult> {
  const expected = owner(), allChannels = await api.getChannels(workspaceId); assertOwner(expected);
  if (options.channelId && !allChannels.some(channel => channel.id === options.channelId)) throw new Error('The search channel is no longer available in this workspace.');
  const channels = options.channelId ? allChannels.filter(channel => channel.id === options.channelId) : allChannels;
  if (!query.trim() || query.length > 1000) throw new Error('Use a query up to 1000 characters.');
  if (options.scope === 'native') {
    const ids = channels.filter(channel => !channel.secure).map(channel => channel.id);
    if (!ids.length) return { items: [], complete: true, engine: 'local-lsa-v1' };
    const result = await api.request<SemanticResult>('/workspaces/' + encodeURIComponent(workspaceId) + '/semantic-search', { method: 'POST', signal: options.signal, body: JSON.stringify({ query, channel_ids: ids.slice(0, 50), limit: options.limit || 10 }) }); assertOwner(expected);
    return { ...result, complete: result.complete && ids.length <= 50 };
  }
  const hits: SemanticHit[] = []; let complete = true;
  for (const channel of channels.filter(channel => channel.secure)) { const result = await secureSearch(channel, query, options.signal); assertOwner(expected); hits.push(...result.items); complete &&= result.complete; }
  return { items: hits.sort((a, b) => b.score - a.score).slice(0, options.limit || 10), complete, engine: 'device-local-lsa-v1' };
}

export async function openSemanticSource(source: SearchSource, workspaceId: string, signal?: AbortSignal, lease?: {sourceElement: HTMLElement; isCurrent: () => boolean}) {
  const expected = owner(), channel = await api.getChannel(source.channel_id); assertOwner(expected);
  if (channel.workspace_id !== workspaceId) throw new Error('The source moved outside this workspace.');
  const verified = await verifySearchSource(source, channel, signal); assertOwner(expected);
  if (lease && !lease.isCurrent()) return;
  if (verified.encrypted_resource) window.dispatchEvent(new CustomEvent('freecollab:open-encrypted-resource', { detail: { channel_id: channel.id, kind: verified.encrypted_kind || verified.kind, id: verified.id, ...lease } }));
  else if (verified.kind === 'message') window.dispatchEvent(new CustomEvent('freecollab:open-personal-source', { detail: { workspace_id: workspaceId, channel_id: channel.id, kind: 'message', item_id: verified.id, ...lease } }));
  else window.dispatchEvent(new CustomEvent('freecollab:open-search-source', { detail: { channel_id: channel.id, kind: verified.kind, id: verified.id, parent_id: verified.parent_id, ...lease } }));
}
