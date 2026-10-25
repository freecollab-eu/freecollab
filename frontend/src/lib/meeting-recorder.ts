import type { MeetingView } from './meetings-api';
export interface MeetingRecordingPart { blob: Blob; duration: number }
export const meetingRosterKey = (view: MeetingView, ownId: string) =>
  [...new Set([ownId, ...view.participants.filter(row => row.state === 'admitted').map(row => row.id)])].sort().join('\0');
/** Records already-admitted streams. It never asks for or stops call devices. */
export class MeetingRecorder {
  private context: AudioContext | null = null;
  private destination: MediaStreamAudioDestinationNode | null = null;
  private inputs = new Map<string, MediaStreamAudioSourceNode>();
  private recorder: MediaRecorder | null = null;
  private output: MediaStream | null = null;
  private videos: Array<{ video: HTMLVideoElement; label: string }> = [];
  private canvas: HTMLCanvasElement | null = null;
  private drawTimer: ReturnType<typeof setInterval> | undefined;
  private deadline: ReturnType<typeof setTimeout> | undefined;
  private chunks: Blob[] = []; private bytes = 0; private activeMillis = 0; private resumed = 0;
  private paused = false; private discard = false; private roster = ''; private mime = '';
  private disposed = false;
  private rotating = false; private maxPartBytes=48*1024*1024;private maxPartMillis=299000;
  private finish: (() => void) | null = null;
  private readonly finished = new Promise<void>(resolve => { this.finish = resolve; });
  get audioStream() { return this.destination?.stream || null; }
  constructor(private ownId: string, private complete: (part: MeetingRecordingPart | null, final: boolean) => void | Promise<void>,
    private fail: (message: string) => void) {}
  async start(view: MeetingView, video: boolean, limits?:{max_part_bytes?:number;max_part_seconds?:number}) {
    if(limits?.max_part_bytes)this.maxPartBytes=Math.max(256*1024,Math.min(48*1024*1024,limits.max_part_bytes));
    if(limits?.max_part_seconds)this.maxPartMillis=Math.max(5000,Math.min(299000,limits.max_part_seconds*1000));
    if (this.recorder || !view.local || view.state !== 'admitted' || typeof MediaRecorder === 'undefined')
      throw new Error('Join with media before recording in a supported secure browser.');
    this.roster = meetingRosterKey(view, this.ownId);
    this.context = new AudioContext(); this.destination = this.context.createMediaStreamDestination();
    await this.context.resume(); if (this.disposed) { this.release(); return; } this.update(view);
    const tracks = [...this.destination.stream.getAudioTracks()];
    if (video) {
      this.canvas = document.createElement('canvas'); this.canvas.width = 1280; this.canvas.height = 720;
      const canvas = this.canvas, paint = () => {
        const context = canvas.getContext('2d'); if (!context) return;
        context.fillStyle = '#181917'; context.fillRect(0, 0, canvas.width, canvas.height);
        const columns = this.videos.length > 1 ? 2 : 1, rows = Math.ceil(this.videos.length / columns);
        this.videos.forEach((source, index) => {
          const x = index % columns * canvas.width / columns, y = Math.floor(index / columns) * canvas.height / Math.max(1, rows);
          const width = canvas.width / columns, height = canvas.height / Math.max(1, rows);
          if (source.video.readyState >= 2) context.drawImage(source.video, x, y, width, height);
          context.fillStyle = '#f7f5ef'; context.font = '18px sans-serif'; context.fillText(source.label.slice(0, 80), x + 12, y + height - 16, width - 24);
        });
      };
      paint(); this.drawTimer = setInterval(paint, 80); tracks.push(...canvas.captureStream(12).getVideoTracks());
    }
    this.output = new MediaStream(tracks);
    const types = video ? ['video/webm;codecs=vp8,opus', 'video/mp4', 'video/webm'] : ['audio/webm;codecs=opus', 'audio/mp4', 'audio/ogg;codecs=opus'];
    this.mime = types.find(type => MediaRecorder.isTypeSupported(type)) || '';
    if (!this.mime) { this.release(); throw new Error('This browser cannot encode a supported meeting recording.'); }
    this.beginSegment();
  }
  private beginSegment() {
    if (!this.output || this.disposed || this.paused) return;
    this.activeMillis = 0; this.bytes = 0; this.chunks = [];
    const recorder = new MediaRecorder(this.output, { mimeType: this.mime, audioBitsPerSecond: 64000, videoBitsPerSecond: 700000 });
    this.recorder = recorder;
    recorder.ondataavailable = event => { if (event.data.size) { this.bytes += event.data.size; this.chunks.push(event.data); if (this.bytes >= this.maxPartBytes) this.rotate(); } };
    recorder.onerror = () => { this.discard = true; this.fail('The meeting recording could not be encoded.'); this.stop(true); };
    recorder.onstop = () => {
      const duration = Math.max(.01, Math.min(300, (this.activeMillis + (this.paused ? 0 : performance.now() - this.resumed)) / 1000));
      const blob = new Blob(this.chunks, { type: this.mime }), discarded = this.discard, final = this.disposed;
      this.recorder = null; this.chunks = []; this.rotating = false;
      if (final || discarded) this.release();
      const completed = Promise.resolve(this.complete(discarded || !blob.size ? null : { blob, duration }, final));
      void completed.catch(() => this.fail('The meeting segment could not be retained.')).finally(() => { if (final) this.finish?.(); });
      if (!final && !discarded) this.beginSegment();
    };
    this.resumed = performance.now(); recorder.start(500); this.deadline = setTimeout(() => this.rotate(), this.maxPartMillis);
  }
  private rotate() {
    if (!this.recorder || this.rotating || this.disposed || this.recorder.state !== 'recording') return;
    this.rotating = true; clearTimeout(this.deadline); this.recorder.stop();
  }
  update(view: MeetingView) {
    if (!this.context || !this.destination) return;
    if (this.roster && this.roster !== meetingRosterKey(view, this.ownId)) {
      this.stop(true); this.fail('Meeting participants changed. Recording stopped until everyone reviews the new request.'); return;
    }
    const admitted=new Set(view.participants.filter(row=>row.state==='admitted').map(row=>row.id));
    const streams = [view.local, ...[...view.streams].filter(([id])=>admitted.has(id)).map(([,stream])=>stream)].filter((stream): stream is MediaStream => !!stream);
    const tracks = new Map(streams.flatMap(stream => stream.getAudioTracks()).filter(track => track.readyState === 'live').map(track => [track.id, track]));
    for (const [id, input] of this.inputs) if (!tracks.has(id)) { input.disconnect(); this.inputs.delete(id); }
    for (const [id, track] of tracks) if (!this.inputs.has(id)) {
      const input = this.context.createMediaStreamSource(new MediaStream([track])); input.connect(this.destination); this.inputs.set(id, input);
    }
    for (const source of this.videos) source.video.srcObject = null;
    this.videos = streams.filter(stream => stream.getVideoTracks().length).map((stream, index) => {
      const element = document.createElement('video'); element.muted = true; element.playsInline = true; element.srcObject = stream;
      void element.play().catch(() => {});
      return { video: element, label: index === 0 && stream === view.local ? 'You' : view.participants.find(row => view.streams.get(row.id) === stream)?.name || 'Participant' };
    });
  }
  pause(value: boolean) {
    if (!this.recorder || value === this.paused) return;
    if (value && this.recorder.state === 'recording') {
      this.activeMillis += performance.now() - this.resumed; this.recorder.pause(); this.paused = true; clearTimeout(this.deadline);
    } else if (!value && this.recorder.state === 'paused') {
      this.resumed = performance.now(); this.recorder.resume(); this.paused = false;
      this.deadline = setTimeout(() => this.rotate(), Math.max(10, this.maxPartMillis - this.activeMillis));
    }
  }
  stop(discard = false) {
    this.disposed = true;
    this.discard ||= discard;
    if (this.recorder && ['recording', 'paused'].includes(this.recorder.state)) this.recorder.stop();
    else if (!this.recorder) { this.release(); this.finish?.(); }
    return this.finished;
  }
  private release() {
    clearTimeout(this.deadline); clearInterval(this.drawTimer);
    for (const source of this.inputs.values()) source.disconnect(); this.inputs.clear();
    for (const source of this.videos) source.video.srcObject = null; this.videos = [];
    this.output?.getTracks().forEach(track => track.stop()); this.output = null;
    void this.context?.close().catch(() => {}); this.context = null; this.destination = null; this.recorder = null;
  }
}
