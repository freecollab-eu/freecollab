import { LabeledWritingField } from './LabeledWritingField';
import { useScopedWritingGuard } from '../lib/useScopedWritingGuard';
import { ScopedCollaborationInput as CollaborationInput } from './ScopedCollaborationInput';
import { FieldReferenceText } from './FieldReferenceText';
import { reconcileFieldReferences } from '../lib/field-references';
import type { MessageReference } from '../lib/message-references';
import { useEffect, useId, useRef, useState } from 'react';
import { Mic, Video, Monitor, Square, Play, Trash2, Loader2 } from 'lucide-react';
import { t, useI18n } from '../i18n';
import { api } from '../lib/api';
import { getSecureMessaging } from '../lib/secure-client';
import { clipsApi, clipVtt, type ChannelClip, type ClipCaption } from '../lib/clips-api';
import { ClipRecorder, type ClipSource } from '../lib/clip-recorder';
import { transcribeLocally } from '../lib/local-transcription';
import { useMediaTargetFocus } from '../lib/useMediaTargetFocus';
import { ReviewedResourceCopilot } from './ReviewedResourceCopilot';
import { MeetingRecordingRecovery } from './MeetingRecordingRecovery';
import '../styles/clips.css';

const describe = (error: unknown) => error instanceof Error ? error.message : 'The clip could not be completed.';
function ClipPlayer({ blob, captions, audio = false }: { blob: Blob; captions?: ClipCaption[]; audio?: boolean }) {
  const [url, setUrl] = useState(''), [track, setTrack] = useState('');
  useEffect(() => { const value = URL.createObjectURL(blob); setUrl(value); return () => URL.revokeObjectURL(value); }, [blob]);
  useEffect(() => { if (!captions?.length) { setTrack(''); return; } const value = URL.createObjectURL(new Blob([clipVtt(captions)], { type: 'text/vtt' })); setTrack(value); return () => URL.revokeObjectURL(value); }, [captions]);
  return audio ? <audio src={url} controls preload="metadata" /> : <video src={url} controls playsInline preload="metadata">{track && <track kind="captions" src={track} label={t('Transcript captions')} default />}</video>;
}
function SavedClip({ row, remove, workspaceId }: { row: ChannelClip; remove: () => void; workspaceId?: string }) {
  const [blob, setBlob] = useState<Blob | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const generation = useRef(0);
  useEffect(() => { ++generation.current; return () => { ++generation.current; }; }, [row.id]);
  return <article className="fc-clip-card" tabIndex={-1} data-media-id={row.id} data-media-kind="clip"><div className="fc-clip-heading"><div><strong>{row.fields ? <FieldReferenceText value={row.fields.title} references={row.fields.field_references?.title} channelId={row.channel_id} /> : t('Encrypted clip')}</strong><small>{new Date(row.created_at).toLocaleString()} · {Math.round(row.fields?.duration || 0)}s</small></div>{(row.can_remove || row.created_by === api.getCurrentUserId()) && <button className="fc-icon-button" aria-label={t('Remove clip')} onClick={remove}><Trash2 size={15} /></button>}</div>{row.resolution_error && <p role="alert">{row.resolution_error}</p>}{error && <p role="alert">{error}</p>}{blob ? <ClipPlayer blob={blob} captions={row.fields?.captions} audio={row.file?.content_type.startsWith('audio/')} /> : <button className="fc-button" disabled={busy || !row.file} onClick={() => { const current = generation.current; setBusy(true); void clipsApi.download(row).then(value => { if (current === generation.current) setBlob(value); }).catch(failure => { if (current === generation.current) setError(describe(failure)); }).finally(() => { if (current === generation.current) setBusy(false); }); }}>{busy ? <Loader2 size={16} /> : <Play size={16} />}{row.file ? t('Play recording') : t('Recording unavailable')}</button>}{row.fields?.transcript && <details><summary>{t('Transcript')}</summary><p className="fc-clip-transcript"><FieldReferenceText value={row.fields.transcript} references={row.fields.field_references?.transcript} channelId={row.channel_id} /></p></details>}{row.fields?.transcript && <ReviewedResourceCopilot workspaceId={workspaceId} channelId={row.channel_id} label={row.fields.title} snapshot={row.id + ":" + row.created_at} text={row.fields.transcript} sourceRefs={[{ kind: "clip", id: row.id }]} />}</article>;
}

export function ClipsPanel({ channelId, instanceId, workspaceId, focusClipId }: { channelId: string; instanceId?: string; workspaceId?: string; focusClipId?: string }) {
  useI18n();
  const writingInstanceId = useId();
  const mediaRegion = useRef<HTMLElement>(null);
  const activeWritingPalettes = useRef(new Set<string>());
  const submissionScope = `${instanceId || writingInstanceId}:${channelId}:clip:publish`;
  const setPaletteActive = (field: string, active: boolean) => { if (active) activeWritingPalettes.current.add(field); else activeWritingPalettes.current.delete(field); };
  const [fieldReferences, setFieldReferences] = useState<Record<string, MessageReference[]>>({});
  function changeWritingField(name: "title" | "transcript", value: string, refs: MessageReference[]) {
    if (Object.entries(fieldReferences).reduce((count, [key, items]) => count + (key === name ? 0 : items.length), refs.length) > 50) { setError(t("Keep this item to at most 50 references.")); return; }
    const next = { title, transcript, [name]: value };
    setFieldReferences(reconcileFieldReferences({ title, transcript }, next, fieldReferences, { [name]: refs }) || {});
    if (name === "title") setTitle(value); else { setTranscript(value); setCaptions([]); }
  }
  const [rows, setRows] = useState<ChannelClip[]>([]), [recording, setRecording] = useState(false), [source, setSource] = useState<ClipSource>('audio');
  useMediaTargetFocus(mediaRegion, channelId, 'clip', focusClipId, async (id, current) => {
    try {
      const row = await clipsApi.get(channelId, id); if (!current()) return false;
      setRows(previous => [row, ...previous.filter(item => item.id !== row.id)]); return true;
    } catch (failure) { if (current()) setError(describe(failure)); return false; }
  });
  const [draft, setDraft] = useState<{ blob: Blob; duration: number;content_mode:'server'|'matrix' } | null>(null), [title, setTitle] = useState(''), [transcript, setTranscript] = useState(''), [captions, setCaptions] = useState<ClipCaption[]>([]);
  const [consent, setConsent] = useState(false), [localConsent, setLocalConsent] = useState(false), [busy, setBusy] = useState(''), [progress, setProgress] = useState(''), [error, setError] = useState('');
  const recorder = useRef<ClipRecorder | null>(null), transcription = useRef<AbortController | null>(null), generation = useRef(0), preview = useRef<HTMLVideoElement>(null);
  useScopedWritingGuard(channelId, !!draft || recording, !!busy);
  async function load() { const current = generation.current; try { const values = await clipsApi.list(channelId); if (current === generation.current) setRows(values); } catch (failure) { if (current === generation.current) setError(describe(failure)); } }
  useEffect(() => { ++generation.current; setRows([]); setDraft(null); setFieldReferences({}); setTitle(''); setTranscript(''); setCaptions([]); setConsent(false); setRecording(false); setBusy(''); void load(); const clear = () => { ++generation.current; recorder.current?.stop(true); transcription.current?.abort(); setRows([]); setDraft(null); setFieldReferences({}); setTitle(''); setRecording(false); setBusy(''); }; window.addEventListener('freecollab:auth-changed', clear); const updated = () => void load(); window.addEventListener('freecollab:clips-changed', updated); return () => { clear(); window.removeEventListener('freecollab:auth-changed', clear); window.removeEventListener('freecollab:clips-changed', updated); }; }, [channelId]);
  async function start() {
    if (busy || recording || !consent) return;
    const current = generation.current, capture = new ClipRecorder(); recorder.current = capture; setError(''); setBusy('capture');
    try {
      const secure=await getSecureMessaging(),content_mode=await secure.handles(channelId)?'matrix':'server';
      if(current!==generation.current)return;
      await capture.start(source, result => { if (current === generation.current) { setDraft(result?{...result,content_mode}:null); setRecording(false); } }, failure => { if (current === generation.current) setError(describe(failure)); });
      if (current !== generation.current) { capture.stop(true); return; }
      setRecording(true); if (preview.current) { preview.current.srcObject = capture.preview; void preview.current.play().catch(() => undefined); }
    } catch (failure) { if (current === generation.current) setError(describe(failure)); }
    finally { if (current === generation.current) setBusy(''); }
  }
  async function transcribe() {
    if (!draft || !localConsent || busy) return;
    const current = generation.current, controller = new AbortController(); transcription.current = controller; setBusy('transcribe'); setError('');
    try { const result = await transcribeLocally(draft.blob, controller.signal, value => { if (current === generation.current) setProgress(value); }); if (current === generation.current) { setTranscript(result.transcript); setFieldReferences(previous => { const next = { ...previous }; delete next.transcript; return next; }); setCaptions(result.captions); } }
    catch (failure) { if (current === generation.current && !controller.signal.aborted) setError(describe(failure)); }
    finally { if (current === generation.current) { setBusy(''); setProgress(''); } }
  }
  async function save() {
    if (!draft || !title.trim() || busy || activeWritingPalettes.current.size) return;
    const current = generation.current; setBusy('save'); setError('');
    try { await clipsApi.create(channelId, draft.blob, { title: fieldReferences.title?.length ? title : title.trim(), duration: draft.duration, transcript: fieldReferences.transcript?.length ? transcript : transcript.trim(), captions, ...(Object.keys(fieldReferences).length ? { field_references: fieldReferences } : {}) }, percent => { if (current === generation.current) setProgress(percent + '%'); },{expectedContentMode:draft.content_mode}); if (current === generation.current) { setDraft(null); setFieldReferences({}); setTitle(''); setTranscript(''); setCaptions([]); await load(); } }
    catch (failure) { if (current === generation.current) setError(describe(failure)); }
    finally { if (current === generation.current) { setBusy(''); setProgress(''); } }
  }
  return <section ref={mediaRegion} className="fc-clips" aria-label={t('Channel clips')}><div className="fc-integration-content-heading"><div><h3>{t('Show it. Share it. Keep moving.')}</h3><p>{t('Record a voice note, camera update or screen walkthrough. Up to five minutes and 50 MiB.')}</p></div></div>{error && <p role="alert" className="fc-alert">{error}</p>}
    <MeetingRecordingRecovery channelId={channelId} onPublished={() => void load()} />
    {!draft && <><div className="fc-clip-sources">{(['audio', 'camera', 'screen'] as const).map(value => <button key={value} className="fc-button" disabled={recording || !!busy} aria-pressed={source === value} onClick={() => setSource(value)}>{value === 'audio' ? <Mic size={16} /> : value === 'camera' ? <Video size={16} /> : <Monitor size={16} />}{value === 'audio' ? t('Voice note') : value === 'camera' ? t('Camera clip') : t('Screen walkthrough')}</button>)}</div><label className="fc-clip-consent"><input type="checkbox" checked={consent} disabled={recording} onChange={event => setConsent(event.target.checked)} />{t('I have permission from everyone whose voice, image or screen content I record.')}</label><video className={'fc-clip-preview' + (recording && source !== 'audio' ? '' : ' fc-clip-hidden')} ref={preview} autoPlay muted playsInline />{recording ? <button className="fc-button fc-meeting-leave" onClick={() => recorder.current?.stop()}><Square size={16} />{t('Stop recording')}</button> : <button className="fc-button fc-button-primary" disabled={!consent || !!busy} onClick={() => void start()}><Mic size={16} />{t('Start recording')}</button>}</>}
    {draft && <div className="fc-clip-draft"><ClipPlayer blob={draft.blob} captions={captions} audio={source === 'audio'} /><LabeledWritingField label={t('Clip title')}><CollaborationInput as="input" channelId={channelId} submissionScope={submissionScope} onPaletteActiveChange={active => setPaletteActive("title", active)} contextId={`${instanceId || writingInstanceId}:${channelId}:clip:title`} references={fieldReferences.title} className="fc-input" value={title} maxLength={200} onValueChange={(value, refs) => changeWritingField("title", value, refs)} /></LabeledWritingField><label className="fc-clip-consent"><input type="checkbox" checked={localConsent} disabled={!!busy} onChange={event => setLocalConsent(event.target.checked)} />{t('Download the pinned Whisper model to this browser for local transcription. My audio stays on this device.')}</label><p className="fc-account-muted">{import.meta.env.VITE_WHISPER_MODEL_BASE_PATH ? t('This installation serves the pinned model from its own origin.') : t('Model files are downloaded from Hugging Face; no audio is sent there.')}</p><div className="fc-clip-actions"><button className="fc-button" disabled={!localConsent || !!busy} onClick={() => void transcribe()}>{t('Transcribe on this device')}</button>{busy === 'transcribe' && <button className="fc-button" onClick={() => transcription.current?.abort()}>{t('Cancel transcription')}</button>}</div><LabeledWritingField label={t('Transcript')}><CollaborationInput as="textarea" toolbar channelId={channelId} submissionScope={submissionScope} onPaletteActiveChange={active => setPaletteActive("transcript", active)} contextId={`${instanceId || writingInstanceId}:${channelId}:clip:transcript`} references={fieldReferences.transcript} aria-label={t('Transcript')} className="fc-input" rows={4} value={transcript} maxLength={30000} onValueChange={(value, refs) => changeWritingField("transcript", value, refs)} /></LabeledWritingField><p className="fc-account-muted">{t('Review the transcript before publishing. Automatic recognition can make mistakes. Editing the transcript removes timed captions.')}</p><div className="fc-clip-actions"><button data-collaboration-submit={submissionScope} className="fc-button fc-button-primary" disabled={!!busy || !title.trim()} onClick={() => void save()}>{t('Publish clip')}</button><button className="fc-button" disabled={!!busy} onClick={() => { setDraft(null); setFieldReferences({}); setTitle(''); setTranscript(''); setCaptions([]); }}>{t('Discard recording')}</button></div></div>}
    {busy && <p role="status"><Loader2 size={16} className="animate-spin" /> {progress || t('Preparing recording…')}</p>}<p className="fc-account-muted">{t('Secure channels encrypt the recording and transcript on this device with Matrix. Native channel recordings use the existing file storage and access controls.')}</p><div className="fc-clips-list">{rows.map(row => <SavedClip key={row.id} row={row} workspaceId={workspaceId} remove={() => { void clipsApi.remove(row).then(load).catch(failure => setError(describe(failure))); }} />)}</div>{!rows.length && <p className="fc-account-muted">{t('No clips yet. Give the next update a voice.')}</p>}<p className="fc-account-muted">{t('Removing a clip hides its entry. Uploaded media remains in channel files and follows the storage retention policy.')}</p>
  </section>;
}
