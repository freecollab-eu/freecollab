/// <reference lib="webworker" />
import { env, pipeline } from '@huggingface/transformers';
const worker = self as unknown as DedicatedWorkerGlobalScope;
type Recognizer = (audio: Float32Array, options: Record<string, unknown>) => Promise<unknown>;
let recognizer: Recognizer | null = null, identity = '', busy = false;
worker.onmessage = async event => {
  const message = event.data;
  if (busy || !message || !Number.isSafeInteger(message.id) || message.id < 1) return;
  busy = true;
  try {
    const config = message.configuration;
    if (!config || typeof config.model !== 'string' || !/^[A-Za-z0-9_-]+(?:\/[A-Za-z0-9_-]+)?$/.test(config.model)
        || typeof config.model_base !== 'string' || config.model_base && (!/^\/[A-Za-z0-9_/-]*\/$/.test(config.model_base) || config.model_base.startsWith('//') || config.model_base.includes('..'))
        || !config.model_base && config.model !== 'onnx-community/whisper-tiny')
      throw new Error('Configure a local caption model on this installation.');
    if (!['load', 'transcribe'].includes(message.operation) || message.operation === 'transcribe' && (!(message.samples instanceof Float32Array) || message.samples.length < 8000 || message.samples.length > 16000 * 30
        || typeof message.language !== 'string' || !/^(auto|[a-z]{2})$/.test(message.language))
      )
      throw new Error('Invalid local caption audio window.');
    // Only pinned public weights, or the operator's same-origin mirror, are
    // fetched. Audio/text and inference never leave this browser worker.
    env.allowRemoteModels = true; env.allowLocalModels = false;
    env.remoteHost = config.model_base ? new URL(config.model_base, worker.location.origin).href : 'https://huggingface.co/';
    if (env.backends.onnx.wasm) {
      env.backends.onnx.wasm.proxy = false; env.backends.onnx.wasm.numThreads = 1;
      env.backends.onnx.wasm.wasmPaths = {
        mjs: new URL('../../node_modules/onnxruntime-web/dist/ort-wasm-simd-threaded.jsep.mjs', import.meta.url).href,
        wasm: new URL('../../node_modules/onnxruntime-web/dist/ort-wasm-simd-threaded.jsep.wasm', import.meta.url).href,
      };
    }
    const selected = config.model_base + config.model;
    if (!recognizer || identity !== selected) {
      recognizer = await pipeline('automatic-speech-recognition', config.model, { device: 'wasm', dtype: 'q8',
        revision: 'ff4177021cc41f7db950912b73ea4fdf7d01d8e7',
        progress_callback: (progress: unknown) => worker.postMessage({ id: message.id, progress }) }) as unknown as Recognizer;
      identity = selected;
    }
    if (message.operation === 'load') { worker.postMessage({ id: message.id, ready: true }); return; }
    const value = await recognizer(message.samples, { return_timestamps: true, task: 'transcribe',
      ...(message.language === 'auto' ? {} : { language: message.language }) });
    if (!value || typeof value !== 'object' || typeof (value as { text?: unknown }).text !== 'string')
      throw new Error('The local caption model returned invalid text.');
    const result = value as { text: string; chunks?: Array<{ text: string; timestamp: [number, number | null] }> };
    if (result.text.length > 10000 || result.chunks && (!Array.isArray(result.chunks) || result.chunks.length > 200))
      throw new Error('The local caption result exceeds its bound.');
    worker.postMessage({ id: message.id, text: result.text, chunks: result.chunks || [] });
  } catch (failure) {
    worker.postMessage({ id: message.id, error: failure instanceof Error ? failure.message : 'Local captions could not be generated.' });
  } finally { busy = false; }
};
