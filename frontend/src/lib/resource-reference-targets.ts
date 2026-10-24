import { api } from './api';
import { changedFieldReferences, type FieldReferences } from './field-references';

/** New bindings resolve under the caller's current account before publication. */
export async function validateResourceReferenceTargets(channelId: string, texts: Record<string, string>, references: FieldReferences | undefined, owner: string | null, encrypted: boolean, previousTexts?: Record<string, string>, previous?: FieldReferences) {
  const current = () => { if (!owner || owner !== api.getAccessToken()) throw new Error('The signed-in account changed.'); };
  current();
  const changed = changedFieldReferences(texts, references, previousTexts, previous);
  if (!changed.length) return;
  const { messageReferencesApi } = await import('./message-references-api'); current();
  const resolved = await messageReferencesApi.resolve(channelId, changed, undefined, encrypted); current();
  if (changed.some(ref => !resolved.some(target => target.kind === ref.kind && target.id === ref.id && target.available))) throw new Error('A selected reference is no longer available in this conversation.');
}
