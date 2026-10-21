/** User initiated MediaRecorder capture. No devices are requested at import time. */
export type ClipSource = 'audio' | 'camera' | 'screen';
export class ClipRecorder {
  private recorder: MediaRecorder | null = null;
  private streams: MediaStream[] = [];
  private chunks: Blob[] = [];
  private deadline: ReturnType<typeof setTimeout> | undefined;
  private started = 0;
  private bytes = 0;
  private discarded = false;
  private done: ((result: { blob: Blob; duration: number } | null) => void) | null = null;
  private failure: ((error: Error) => void) | null = null;
  preview: MediaStream | null = null;
  async start(source: ClipSource, complete: (result: { blob: Blob; duration: number } | null) => void, failure: (error: Error) => void) {
    if (this.recorder) throw new Error('A recording is already active.');
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') throw new Error('Recording requires a secure browser with MediaRecorder support.');
    this.done = complete; this.failure = failure;
    try {
      const microphone = await navigator.mediaDevices.getUserMedia({ audio: true, video: source === 'camera' }); this.streams.push(microphone);
      if (this.discarded) { this.release(); return; }
      let stream = microphone;
      if (source === 'screen') {
        if (!navigator.mediaDevices.getDisplayMedia) throw new Error('Screen recording is unavailable in this browser.');
        const display = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false }); this.streams.push(display);
        if (this.discarded) { this.release(); return; }
        stream = new MediaStream([...display.getVideoTracks(), ...microphone.getAudioTracks()]);
        display.getVideoTracks()[0].onended = () => this.stop();
      }
      this.preview = stream;
      const types = source === 'audio' ? ['audio/webm;codecs=opus', 'audio/mp4', 'audio/ogg;codecs=opus'] : ['video/webm;codecs=vp8,opus', 'video/mp4', 'video/webm'];
      const mimeType = types.find(type => MediaRecorder.isTypeSupported(type));
      if (!mimeType) throw new Error('This browser cannot encode a supported recording format.');
      const recorder = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: 800000, audioBitsPerSecond: 64000 }); this.recorder = recorder;
      recorder.ondataavailable = event => { if (event.data.size) { this.bytes += event.data.size; this.chunks.push(event.data); if (this.bytes > 48 * 1024 * 1024) this.stop(); } };
      recorder.onerror = () => { this.discarded = true; this.failure?.(new Error('The browser could not finish this recording.')); this.stop(); };
      recorder.onstop = () => { const blob = new Blob(this.chunks, { type: mimeType }), duration = Math.min(300, (performance.now() - this.started) / 1000); this.release(); this.done?.(this.discarded || !blob.size ? null : { blob, duration }); this.chunks = []; };
      this.started = performance.now(); recorder.start(500); this.deadline = setTimeout(() => this.stop(), 300000);
    } catch (error) { this.release(); throw error; }
  }
  stop(discard = false) { this.discarded ||= discard; if (this.recorder?.state === 'recording' || this.recorder?.state === 'paused') this.recorder.stop(); else this.release(); }
  private release() { clearTimeout(this.deadline); for (const stream of this.streams) for (const track of stream.getTracks()) { track.onended = null; track.stop(); } this.streams = []; this.preview = null; this.recorder = null; }
}
