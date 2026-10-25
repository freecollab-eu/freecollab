import { latentModel, rankSemantic, type LatentModel } from './latent-semantic';
import type { SearchSource } from './enterprise-search';
self.onmessage = (event: MessageEvent<{items: SearchSource[]; query: string; model?: LatentModel}>) => {
  try { const {items, query} = event.data; const model = event.data.model || latentModel(items); self.postMessage({ model, hits: rankSemantic(items, model, query) }); }
  catch { self.postMessage({ error: true }); }
};
