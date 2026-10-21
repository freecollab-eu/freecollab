/** Actual ONNX Whisper inference in this browser worker; no audio leaves it. */
import { pipeline, env } from '@huggingface/transformers';
env.allowLocalModels = false;
const mirrorPath = import.meta.env.VITE_WHISPER_MODEL_BASE_PATH;
if (mirrorPath) {
  const mirror = new URL(mirrorPath, self.location.origin);
  if (!mirrorPath.startsWith('/') || mirrorPath.startsWith('//') || mirror.origin !== self.location.origin || mirror.search || mirror.hash || mirrorPath.includes('\\')) throw new Error('Use a same-origin absolute path for the Whisper model mirror.');
  env.remoteHost = mirror.href.replace(/\/?$/, '/');
}
if (env.backends.onnx.wasm) {
  env.backends.onnx.wasm.proxy = false;
  env.backends.onnx.wasm.numThreads = 1;
  // Both executable runtime and WASM come from this release's own pinned
  // dependency assets; no third-party executable origin gains client access.
  env.backends.onnx.wasm.wasmPaths = {
    mjs: new URL('../../node_modules/onnxruntime-web/dist/ort-wasm-simd-threaded.jsep.mjs', import.meta.url).href,
    wasm: new URL('../../node_modules/onnxruntime-web/dist/ort-wasm-simd-threaded.jsep.wasm', import.meta.url).href,
  };
}
interface RecognitionResult { text: string; chunks?: Array<{ text: string; timestamp: [number | null, number | null] }> }
type Recognizer = (audio: Float32Array, options: Record<string, unknown>) => Promise<RecognitionResult | RecognitionResult[]>;
const createRecognizer = pipeline as unknown as (task: 'automatic-speech-recognition', model: string, options: Record<string, unknown>) => Promise<Recognizer>;
let recognizer: Recognizer | undefined;
self.onmessage = async (event: MessageEvent<{ audio: Float32Array }>) => {
  try {
    recognizer ||= await createRecognizer('automatic-speech-recognition', 'onnx-community/whisper-tiny', { revision: 'ff4177021cc41f7db950912b73ea4fdf7d01d8e7', dtype: 'q8', device: 'wasm', progress_callback: (progress: unknown) => self.postMessage({ type: 'progress', progress }) });
    const result = await recognizer(event.data.audio, { return_timestamps: true, chunk_length_s: 30, stride_length_s: 5 });
    const value = Array.isArray(result) ? result[0] : result;
    const duration = event.data.audio.length / 16000;
    const captions = (value.chunks || []).map(chunk => ({ start: Math.max(0, Math.min(duration, chunk.timestamp[0] || 0)), end: Math.max(0, Math.min(duration, chunk.timestamp[1] ?? duration)), text: chunk.text.trim().slice(0, 2000) })).filter(caption => caption.text && caption.end > caption.start).slice(0, 300);
    self.postMessage({ type: 'result', transcript: value.text.slice(0, 30000), captions: value.text.length > 30000 ? [] : captions });
  } catch (error) { self.postMessage({ type: 'error', message: error instanceof Error ? error.message : 'Local speech recognition failed.' }); }
};
