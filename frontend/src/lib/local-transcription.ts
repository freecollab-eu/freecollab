import type { ClipCaption } from './clips-api';
/** The caller must obtain explicit consent before downloading the model. */
export async function transcribeLocally(blob: Blob, signal: AbortSignal, progress: (message: string) => void): Promise<{ transcript: string; captions: ClipCaption[] }> {
  if (signal.aborted) throw new DOMException('Cancelled.', 'AbortError');
  const context = new AudioContext({ sampleRate: 16000 });
  let samples: Float32Array;
  try {
    const decoded = await context.decodeAudioData(await blob.arrayBuffer());
    if (decoded.duration > 301) throw new Error('Local transcription supports recordings up to five minutes.');
    const mono = new OfflineAudioContext(1, Math.ceil(decoded.duration * 16000), 16000), source = mono.createBufferSource();
    source.buffer = decoded; source.connect(mono.destination); source.start(); samples = (await mono.startRendering()).getChannelData(0);
  } finally { await context.close(); }
  if (signal.aborted) throw new DOMException('Cancelled.', 'AbortError');
  return new Promise((resolve, reject) => {
    const worker = new Worker(new URL('./transcription.worker.ts', import.meta.url), { type: 'module' });
    const finish = () => { worker.terminate(); signal.removeEventListener('abort', cancel); };
    const cancel = () => { finish(); reject(new DOMException('Cancelled.', 'AbortError')); };
    signal.addEventListener('abort', cancel, { once: true });
    worker.onerror = () => { finish(); reject(new Error('Local speech recognition could not start. Check model downloads and browser memory.')); };
    worker.onmessage = event => {
      if (event.data.type === 'progress') { const p = event.data.progress; progress(p.status === 'progress' ? `${p.file}: ${Math.round(p.progress || 0)}%` : p.status); }
      if (event.data.type === 'error') { finish(); reject(new Error(event.data.message)); }
      if (event.data.type === 'result') { finish(); resolve({ transcript: event.data.transcript, captions: event.data.captions }); }
    };
    worker.postMessage({ audio: samples }, [samples.buffer]);
  });
}
