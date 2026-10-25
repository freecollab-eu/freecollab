export interface LocalCaptionConfiguration { max_part_bytes?:number; max_part_seconds?:number; sample_upload_limit_bytes?:number|null; sample_session_limit_bytes?:number|null; captions: boolean; engine?: string; model?: string; model_base?: string; languages?: string[]; reason?: string }
export interface LiveCaption { start: number; end: number; text: string }
/** Deterministic resampling, before any ASR worker receives the device audio. */
export function resampleAudio(input: Float32Array, sourceRate: number, targetRate = 16000) {
  if (!Number.isFinite(sourceRate) || sourceRate < 8000 || sourceRate > 192000 || input.length > sourceRate * 30
      || !Number.isFinite(targetRate) || targetRate < 8000 || targetRate > 192000)
    throw new Error('Invalid caption audio sample rate or window.');
  const length = Math.floor(input.length * targetRate / sourceRate), output = new Float32Array(length), ratio = sourceRate / targetRate;
  for (let i = 0; i < length; i++) {
    const position = i * ratio, left = Math.floor(position), fraction = position - left;
    output[i] = (input[left] || 0) * (1 - fraction) + (input[Math.min(input.length - 1, left + 1)] || 0) * fraction;
  }
  return output;
}
interface CaptionWindow { id: number; startFrame: number; endFrame: number }
interface CaptionDrain { frame: number; resolve: () => void; reject: (error: Error) => void; timer: ReturnType<typeof setTimeout> }
const cancelled = () => new DOMException('Local captions were cancelled.', 'AbortError');

export class LocalMeetingCaptions {
  private worker: Worker | null = null;
  private processor: ScriptProcessorNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private context: AudioContext | null = null;
  private silence: GainNode | null = null;
  private buffer: number[] = [];
  private request = 0; private loadRequest = 0; private inFlight: CaptionWindow | null = null;
  private stopped = false; private paused = false; private workerReady = false;
  private sampleRate = 0; private acceptedFrames = 0; private dispatchedFrames = 0; private completedFrames = 0;
  private drains = new Set<CaptionDrain>();
  private preparing: Promise<void> | null = null;
  private ready: (() => void) | null = null; private notReady: ((error: Error) => void) | null = null;
  constructor(private configuration: LocalCaptionConfiguration, private language: string,
    private emit: (caption: LiveCaption) => void, private fail: (message: string) => void,
    private progress: (message: string) => void = () => {}) {}

  /** Accepted consented audio, including pending inference, excluding pauses. */
  get activeSeconds() { return this.sampleRate ? this.acceptedFrames / this.sampleRate : 0; }

  prepare() {
    if (this.stopped) return Promise.reject(cancelled());
    if (this.preparing) return this.preparing;
    if (!this.configuration.captions || !this.configuration.model || this.configuration.model_base === undefined)
      throw new Error(this.configuration.reason || 'The local caption model is not configured.');
    this.worker = new Worker(new URL('./meeting-captions.worker.ts', import.meta.url), { type: 'module' });
    this.preparing = new Promise<void>((resolve, reject) => { this.ready = resolve; this.notReady = reject; });
    this.worker.onmessage = event => {
      if (this.stopped || !event.data || !Number.isSafeInteger(event.data.id)) return;
      const message = event.data;
      if (message.id === this.loadRequest && !this.workerReady) {
        if (message.progress) {
          const value = message.progress;
          this.progress(typeof value.progress === 'number' ? 'Loading local captions · ' + Math.round(value.progress) + '%' : 'Preparing local captions…');
          return;
        }
        if (message.ready) {
          this.workerReady = true; this.ready?.(); this.ready = null; this.notReady = null; this.pump(); return;
        }
      }
      if (message.error && (message.id === this.loadRequest || message.id === this.inFlight?.id)) {
        this.failed(String(message.error)); return;
      }
      const window = this.inFlight;
      if (!window || message.id !== window.id) return;
      if (typeof message.text !== 'string' || message.text.length > 10000) {
        this.failed('The local caption model returned invalid text.'); return;
      }
      this.inFlight = null;
      const text = message.text.trim();
      try {
        // A manual pause does not revoke audio already accepted with consent.
        // Revocation calls stop(), which invalidates this entire callback.
        if (text) this.emit({ start: window.startFrame / this.sampleRate, end: window.endFrame / this.sampleRate, text: text.slice(0, 2000) });
      } catch {
        this.failed('The local caption result could not be retained.'); return;
      }
      if (this.stopped) return;
      this.completedFrames = window.endFrame;
      this.resolveDrains(); this.pump();
    };
    this.worker.onerror = () => this.failed('The local caption worker could not continue.');
    try { this.worker.postMessage({ id: this.loadRequest = ++this.request, operation: 'load', configuration: this.configuration }); }
    catch { this.failed('The local caption worker could not be started.'); }
    return this.preparing;
  }

  async start(stream: MediaStream) {
    await this.prepare();
    if (this.stopped) throw cancelled();
    if (this.context) throw new Error('Local captions are already capturing audio.');
    this.context = new AudioContext();
    try {
      await this.context.resume();
      if (this.stopped) throw cancelled();
      this.sampleRate = this.context.sampleRate;
      this.source = this.context.createMediaStreamSource(stream);
      this.processor = this.context.createScriptProcessor(4096, 1, 1);
      this.silence = this.context.createGain(); this.silence.gain.value = 0;
      this.source.connect(this.processor); this.processor.connect(this.silence); this.silence.connect(this.context.destination);
      this.processor.onaudioprocess = event => {
        if (this.stopped || this.paused) return;
        const input = event.inputBuffer.getChannelData(0);
        if (this.buffer.length + input.length > this.sampleRate * 10) {
          this.failed('Local captions cannot keep up with this device. Audio is not sent to a fallback provider.'); return;
        }
        for (const value of input) this.buffer.push(value);
        this.acceptedFrames += input.length;
        this.pump();
      };
    } catch (error) {
      this.terminate(error instanceof Error ? error : cancelled()); throw error;
    }
  }

  private pump() {
    if (this.stopped || !this.workerReady || !this.worker || !this.sampleRate || this.inFlight) return;
    let frontier = Infinity;
    for (const drain of this.drains) if (drain.frame > this.dispatchedFrames) frontier = Math.min(frontier, drain.frame);
    const normal = Math.floor(this.sampleRate * 5);
    const frames = frontier !== Infinity ? Math.min(this.buffer.length, normal, frontier - this.dispatchedFrames)
      : this.buffer.length >= normal ? normal : 0;
    if (!frames) return;
    const audio = Float32Array.from(this.buffer.splice(0, frames));
    const samples = resampleAudio(audio, this.sampleRate);
    const startFrame = this.dispatchedFrames; this.dispatchedFrames += frames;
    if (!samples.length) {
      // Less than one output sample cannot be represented at the model rate.
      this.completedFrames = this.dispatchedFrames; this.resolveDrains(); this.pump(); return;
    }
    // Whisper's bounded input minimum is padded only for inference. The public
    // caption interval always ends at the last actual accepted source frame.
    const recognition = samples.length >= 8000 ? samples : new Float32Array(8000);
    if (recognition !== samples) recognition.set(samples);
    const window = { id: ++this.request, startFrame, endFrame: this.dispatchedFrames }; this.inFlight = window;
    try {
      this.worker.postMessage({ id: window.id, operation: 'transcribe', configuration: this.configuration, language: this.language, samples: recognition }, [recognition.buffer]);
    } catch {
      this.failed('The local caption audio window could not be processed.');
    }
  }

  /**
   * Finish accepted audio through a snapshot of the active-audio frontier.
   * Input can continue during segment rotation. A requested future frontier is
   * clamped to available audio; it never waits for clocks or a later segment.
   */
  drain(timeoutMs = 6000, throughSeconds?: number): Promise<void> {
    if (this.stopped) return Promise.reject(cancelled());
    if (!Number.isFinite(timeoutMs) || timeoutMs < 1 || timeoutMs > 15000
        || throughSeconds !== undefined && (!Number.isFinite(throughSeconds) || throughSeconds < 0))
      return Promise.reject(new Error('Invalid local caption completion deadline.'));
    const frame = throughSeconds === undefined ? this.acceptedFrames
      : Math.min(this.acceptedFrames, Math.floor(throughSeconds * this.sampleRate));
    if (frame <= this.completedFrames) return Promise.resolve();
    if (this.drains.size >= 16) return Promise.reject(new Error('Too many local caption completion requests.'));
    return new Promise<void>((resolve, reject) => {
      const drain: CaptionDrain = { frame, resolve, reject, timer: setTimeout(() => {
        if (this.drains.delete(drain)) reject(new DOMException('Local captions did not finish within the recording save deadline.', 'TimeoutError'));
      }, timeoutMs) };
      this.drains.add(drain); this.pump();
    });
  }

  private resolveDrains() {
    for (const drain of this.drains) if (drain.frame <= this.completedFrames) {
      this.drains.delete(drain); clearTimeout(drain.timer); drain.resolve();
    }
  }

  /** A manual pause keeps accepted audio; it does not erase a pending result. */
  pause(value: boolean) { this.paused = value; }

  private failed(message: string) {
    if (this.stopped) return;
    this.terminate(new Error(message)); this.fail(message);
  }

  private terminate(reason: Error) {
    if (this.stopped) return;
    this.stopped = true; this.buffer = []; this.inFlight = null;
    this.notReady?.(reason); this.notReady = null; this.ready = null;
    for (const drain of this.drains) { clearTimeout(drain.timer); drain.reject(reason); }
    this.drains.clear();
    if (this.worker) { this.worker.onmessage = null; this.worker.onerror = null; this.worker.terminate(); this.worker = null; }
    if (this.processor) this.processor.onaudioprocess = null;
    this.source?.disconnect(); this.processor?.disconnect(); this.silence?.disconnect();
    this.source = null; this.processor = null; this.silence = null;
    const context = this.context; this.context = null;
    void context?.close().catch(() => {});
  }

  /** Consent withdrawal, roster/auth changes and discard terminate immediately. */
  stop() { this.terminate(cancelled()); }
}
