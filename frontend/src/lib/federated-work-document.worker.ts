/// <reference lib="webworker" />
import { reconstructWorkDocument } from './federated-work-document';
const worker = self as unknown as DedicatedWorkerGlobalScope;
worker.onmessage = event => {
  try {
    const { initial, updates } = event.data;
    if (typeof initial !== 'string' || !Array.isArray(updates) || updates.length > 500) throw new Error('Invalid document reconstruction request.');
    worker.postMessage({ state: reconstructWorkDocument(initial, updates) });
  } catch (failure) { worker.postMessage({ error: failure instanceof Error ? failure.message : 'The encrypted document state is invalid.' }); }
};
worker.postMessage({ ready: true });
