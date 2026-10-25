import { useEffect, useMemo, useRef, useState } from 'react';
import { Circle, Pause, Play, Square, FileText, Loader2, ShieldCheck } from 'lucide-react';
import { useI18n } from '../i18n';
import { api } from '../lib/api';
import type { MeetingView } from '../lib/meetings-api';
import { nativeCaptureAuthority, type CaptureAuthority, type CaptureStatus, type MeetingCapturePreferences } from '../lib/meeting-capture-api';
import { MeetingRecorder } from '../lib/meeting-recorder';
import { LocalMeetingCaptions, type LiveCaption } from '../lib/meeting-captions';
import { aiRuntimeApi, type AIModelProfile } from '../lib/ai-runtime-api';
import { owner, assertOwner } from '../mobile/storage';
import { retainMeetingRecording, publishMeetingRecording, pendingMeetingRecordings } from '../lib/meeting-recording-vault';
import { registerMeetingFinalizer,type CaptureFinalization } from '../lib/active-meeting';
import '../styles/meeting-capture.css';

interface Props {
  meetingId: string; channelId: string; transport: 'mesh' | 'livekit' | 'matrix'; ownId: string; view: MeetingView;
  preferences?: MeetingCapturePreferences; guestToken?: string; authority?: CaptureAuthority;
  onStatusChange?: (state:'requesting'|'recording'|'paused'|'stopped'|null)=>void;
}
async function within<T>(operation:Promise<T>,milliseconds=6000):Promise<T|undefined>{let timer:ReturnType<typeof setTimeout>;try{return await Promise.race([operation,new Promise<undefined>(resolve=>{timer=setTimeout(()=>resolve(undefined),milliseconds);})]);}finally{clearTimeout(timer!);}}
const defaults: MeetingCapturePreferences = { enabled: false, video: false, captions: false, save_transcript: false, language: 'auto' };
export function MeetingCaptureControls({ meetingId, channelId, transport, ownId, view, preferences, guestToken, authority: supplied, onStatusChange }: Props) {
  const { t } = useI18n();
  const authority = useMemo(() => supplied || nativeCaptureAuthority(transport === 'livekit' ? 'livekit' : 'mesh', meetingId, channelId, guestToken),
    [supplied, transport, meetingId, channelId, guestToken]);
  const [status, setStatus] = useState<CaptureStatus | null>(null), [prefs, setPrefs] = useState(preferences || defaults);
  const [busy, setBusy] = useState(''), [error, setError] = useState(''), [progress, setProgress] = useState('');
  const [publicationState,setPublicationState]=useState<'saving'|'saved'|'private'|null>(null);
  const [unretainedPart,setUnretainedPart]=useState<{blob:Blob;id:string}|null>(null),unretained=useRef<{blob:Blob;id:string}|null>(null);
  const [profiles, setProfiles] = useState<AIModelProfile[]>([]), [profileId, setProfileId] = useState(''), [aiAcknowledged, setAIAcknowledged] = useState(false);
  const [workspace, setWorkspace] = useState(''), [secure, setSecure] = useState(false), [recording, setRecording] = useState(false);
  const [localCaptions, setLocalCaptions] = useState<LiveCaption[]>([]);
  const current = useRef({ status, view, prefs, authority, workspace }); current.current = { status, view, prefs, authority, workspace };
  const captureRecorder = useRef<MeetingRecorder | null>(null), captions = useRef<LocalMeetingCaptions | null>(null);
  const lifecycle = useRef(0), starting = useRef(''), requested = useRef(false), blocked = useRef(false);
  const allCaptions = useRef<LiveCaption[]>([]), partStart = useRef(0), partIndex = useRef(0);
  const queue = useRef(Promise.resolve()), finalReceipt = useRef<Promise<CaptureStatus|undefined> | null>(null), finalPartId = useRef('');
  const finalReservation=useRef<Promise<CaptureStatus>|null>(null);
  const recordingLease = useRef(0), stopped = useRef(new Set<string>()), manuallyPaused = useRef(false);
  const incompleteParts=useRef(new Set<string>()),stopPending=useRef<Promise<CaptureFinalization>|null>(null);
  const publications = useRef<AbortController | null>(null);
  const owns = !!status?.capture && status.capture.recorder_participant_id === status.participant_id;
  const selected = profiles.find(profile => profile.id === profileId);
  const profileSnapshot = selected ? JSON.stringify([selected.id, selected.revision, selected.endpoint, selected.model, selected.protocol]) : '';
  useEffect(()=>{onStatusChange?.(status?.capture?.state||null);},[status?.capture?.state,onStatusChange]);
  function acceptStatus(next: CaptureStatus) {
    const previous = current.current.status?.capture;
    if (next.capture && stopped.current.has(next.capture.id) && next.capture.state !== 'stopped') return;
    if (next.capture && previous?.id === next.capture.id && next.capture.revision < previous.revision) return;
    current.current.status = next; setStatus(next);
  }
  useEffect(() => { setAIAcknowledged(false); }, [profileSnapshot]);
  useEffect(() => {
    const attempt = ++lifecycle.current, token = api.getAccessToken(), actor = api.getCurrentUserId(); let live = true;
    const valid = () => live && attempt === lifecycle.current && token === api.getAccessToken() && actor === api.getCurrentUserId();
    if (!guestToken && token) void api.getChannel(channelId).then(async channel => {
      if (!valid() || !channel.workspace_id) return; setWorkspace(channel.workspace_id); setSecure(!!channel.secure);
      if (view.host && !channel.secure) {
        const result = await aiRuntimeApi.status(channel.workspace_id); if (valid()) setProfiles(result.profiles);
      }
    }).catch(() => {});
    const cancel = () => { ++lifecycle.current; ++recordingLease.current; publications.current?.abort(); captureRecorder.current?.stop(true); captions.current?.stop();unretained.current=null;setUnretainedPart(null); };
    window.addEventListener('freecollab:auth-changed', cancel);
    return () => { live = false; cancel(); window.removeEventListener('freecollab:auth-changed', cancel); };
  }, [meetingId, channelId, guestToken, view.host]);
  useEffect(() => {
    let live = true, running = false, timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      if (!live || running || view.state !== 'admitted') return;
      running = true;
      try {
        const next = await authority.status();
        if (!live) return;
        const previous = current.current.status?.capture;
        if (!next.capture && previous && (previous.state === 'stopped'||stopped.current.has(previous.id))) return;
        if (next.capture && previous?.id === next.capture.id && next.capture.revision < previous.revision || next.capture && stopped.current.has(next.capture.id)) return;
        if (captureRecorder.current && (!next.capture || !next.capture.all_consented || next.capture.policy_invalidated || previous?.id !== next.capture.id || previous.generation !== next.capture.generation)) {
          blocked.current = true; ++recordingLease.current; publications.current?.abort(); captureRecorder.current.stop(true); captureRecorder.current = null; captions.current?.stop(); allCaptions.current=[];setLocalCaptions([]); setRecording(false);
        }
        if (captureRecorder.current && next.capture) {
          captureRecorder.current.pause(manuallyPaused.current || next.capture.state !== 'recording'); captions.current?.pause(manuallyPaused.current || next.capture.state !== 'recording');
        }
        acceptStatus(next);
      } catch (failure) {
        if (live) { blocked.current = true; publications.current?.abort(); captureRecorder.current?.stop(true); captions.current?.stop(); setRecording(false); setError(failure instanceof Error ? failure.message : 'Meeting recording controls are unavailable.'); }
      } finally { running = false; if (live) timer = setTimeout(() => void poll(), 2000); }
    };
    void poll(); return () => { live = false; clearTimeout(timer); };
  }, [authority, view.state]);
  useEffect(() => { captureRecorder.current?.update(view); }, [view]);
  async function action(name: string, operation: () => Promise<CaptureStatus>) {
    if (busy) return; const attempt = lifecycle.current; setBusy(name); setError('');
    try { const result = await operation(); if (attempt === lifecycle.current) {if(!result.capture?.all_consented&&captureRecorder.current){blocked.current=true;++recordingLease.current;publications.current?.abort();captureRecorder.current.stop(true);captureRecorder.current=null;captions.current?.stop();allCaptions.current=[];setLocalCaptions([]);setRecording(false);}acceptStatus(result);} }
    catch (failure) { if (attempt === lifecycle.current) setError(failure instanceof Error ? failure.message : 'The recording action failed.'); }
    finally { if (attempt === lifecycle.current) setBusy(''); }
  }
  async function request() {
    const chosen = { ...prefs, enabled: true, caption_audience: (secure||transport==='matrix'?'channel':'admitted') as 'channel'|'admitted', ...(aiAcknowledged && selected ? {
      captions: true, save_transcript: true, ai_profile_id: selected.id, ai_profile_revision: selected.revision, ai_disclose: true,
    } : {}) };
    setPrefs(chosen); requested.current = true; blocked.current = false;
    await action('request', () => authority.request(chosen));
  }
  useEffect(() => {
    if (preferences?.enabled && view.host && view.state === 'admitted' && status && !status.capture && !requested.current) void request();
  }, [preferences?.enabled, view.host, view.state, status?.capture?.id, !!status]);
  useEffect(() => {
    const capture = status?.capture;
    if (!capture || !owns || !capture.all_consented || blocked.current || busy || !prefs.enabled) return;
    if(capture.content_mode!=='server'&&capture.content_mode!=='matrix'){setError('Review the recording encryption policy before capture starts.');return;}
    const key = capture.id + ':' + capture.generation;
    if (['requesting', 'paused'].includes(capture.state) && prefs.enabled && starting.current !== key) {
      starting.current = key; void action('start', () => authority.control(capture, capture.state === 'requesting' ? 'start' : 'resume'));
    }
    if (capture.state !== 'recording' || captureRecorder.current) return;
    starting.current = key;
    const attempt = lifecycle.current, lease = ++recordingLease.current, expected = owner();
    publications.current?.abort(); const publication = new AbortController(); publications.current = publication;
    const valid = () => lease === recordingLease.current && attempt === lifecycle.current && expected.user === api.getCurrentUserId() && expected.token === api.getAccessToken();
    const fail = (message: string) => {
      if (!valid()) return; blocked.current = true; setError(message); setRecording(false);
      captions.current?.stop(); captureRecorder.current?.stop(true); captureRecorder.current = null;
      void authority.control(capture, 'pause').catch(() => {});
    };
    const persist = async (part: { blob: Blob; duration: number } | null, final: boolean) => {
      if (!part || !valid()) return;
      const id = final && finalPartId.current || crypto.randomUUID(), offset = partStart.current,pendingReservation=final?finalReservation.current:null; partStart.current += part.duration;
      const index = ++partIndex.current;
      let retained=false;
      try {
        if(!final&&captions.current)try{await captions.current.drain(6000,offset+part.duration);}catch{incompleteParts.current.add(id);if(valid())setError(t('The final caption window could not finish. This recording is saved with an incomplete-transcript notice and no automatic AI notes.'));}
        let result:CaptureStatus|undefined;
        try{result=final&&finalReceipt.current?await finalReceipt.current:await within(authority.control(capture,'segment',id));}
        catch(failure){if((failure as {status?:number}).status&&((failure as {status:number}).status<500))throw failure;if(valid())setError(t('The recording remains private until its publication receipt can be recovered.'));}
        assertOwner(expected); if (!valid()) return;
        if(result&&!result.publication_receipt&&!result.publication_proof)throw new Error('Current recording consent is required before this segment can be published.');
        const selectedCaptions = capture.save_transcript ? allCaptions.current.filter(row => row.start < offset + part.duration && row.end > offset)
          .map(row => ({ start: Math.max(0, row.start - offset), end: Math.min(part.duration, row.end - offset), text: row.text })) : [];
        const transcript = (incompleteParts.current.has(id)?t('Transcript incomplete: the final audio window needs manual review.')+'\n':'')+selectedCaptions.map(row => row.text).join(' ');
        if (transcript.length > 30000 || selectedCaptions.length > 300) throw new Error('This transcript exceeds the clip bounds; recording paused for review.');
        const value = await retainMeetingRecording({ id, channel_id: channelId, capture_id: capture.id, generation: capture.generation,
          created_at: new Date().toISOString(), content_mode:capture.content_mode as 'server'|'matrix',transport:transport==='matrix'?'matrix':'native', fields: { title: t('Meeting recording') + ' · ' + new Date().toLocaleDateString() + ' · ' + index,
            duration: part.duration, transcript, captions: selectedCaptions }, publication_receipt: result?.publication_receipt, publication_proof:result?.publication_proof,
          ...(!incompleteParts.current.has(id)&&capture.ai?.enabled && capture.ai.profile_id && capture.ai.profile_revision && workspace
            ? { assistant: { workspace_id: workspace, profile_id: capture.ai.profile_id, profile_revision: capture.ai.profile_revision } } : {}) }, part.blob, expected);
        retained=true;
        assertOwner(expected);
        const beforePublish = () => {
          assertOwner(expected); const active = current.current.status?.capture;
          if (!valid() || publication.signal.aborted || active?.id !== capture.id || active.generation !== capture.generation || !active.all_consented)
            throw new Error('Recording consent changed before publication.');
        };
        if(valid())setPublicationState('saving');
        queue.current = queue.current.then(() => { beforePublish(); return publishMeetingRecording(value, expected, publication.signal, beforePublish,pendingReservation||undefined); }).then(() => {if(valid())setPublicationState('saved');}).catch(failure => {
          if (valid()) {setPublicationState('private');fail(failure instanceof Error ? failure.message : 'A recording segment needs upload review.');}
        });
      } catch (failure) { if (valid()) {if(!retained){unretained.current={blob:part.blob,id};setUnretainedPart(unretained.current);}fail(failure instanceof Error ? failure.message : 'The recording segment could not be retained.');} }
    };
    const recorder = new MeetingRecorder(ownId, (part, final) => { if (final) { setRecording(false); captions.current?.stop(); } return persist(part, final); }, fail);
    captureRecorder.current = recorder; partStart.current = 0; partIndex.current = 0; allCaptions.current = [];
    const recognizer = capture.captions ? new LocalMeetingCaptions(status.configuration, capture.language, caption => {
      if (!valid()) return;
      allCaptions.current.push(caption); setLocalCaptions(previous => [...previous.slice(-3), caption]);
      if(!manuallyPaused.current&&current.current.status?.capture?.state==='recording'&&current.current.status.capture.all_consented)void authority.caption(capture, caption,publication.signal).catch(failure => { if (valid()) setError(failure instanceof Error ? failure.message : 'A caption could not be shared.'); });
    }, fail, message => { if (valid()) setProgress(message); }) : null;
    captions.current = recognizer;
    void (async () => {
      try {
        if (recognizer) await recognizer.prepare();
        if (!valid() || current.current.status?.capture?.generation !== capture.generation || current.current.status.capture.state !== 'recording') return;
        await recorder.start(current.current.view, capture.video, status.configuration);
        if (!valid()) { recorder.stop(true); return; }
        if (recognizer && recorder.audioStream) await recognizer.start(recorder.audioStream);
        if (valid()) { setRecording(true); setProgress(''); }
      } catch (failure) { if (valid()) fail(failure instanceof Error ? failure.message : 'Recording could not start.'); }
    })();
  }, [status?.capture?.id, status?.capture?.generation, status?.capture?.state, status?.capture?.all_consented, owns, prefs.enabled, busy]);
  async function stopCapture() {
    const capture = current.current.status?.capture;
    if (!capture || !captureRecorder.current) {
      if (capture && owns) await action('stop', () => authority.control(capture, 'stop'));
      return;
    }
    const endingRecorder=captureRecorder.current;
    stopped.current.add(capture.id); blocked.current = true; manuallyPaused.current = true;
    captureRecorder.current.pause(true); captions.current?.pause(true);
    setPrefs(value => ({ ...value, enabled: false }));
    finalPartId.current = crypto.randomUUID();
    const attempt=lifecycle.current,id=finalPartId.current;
    // Keep the original reservation alive: a six-second UI bound must not lose
    // an eventual exact receipt for a part already retained on this device.
    finalReservation.current=authority.control(capture,'stop',id);
    finalReceipt.current=within(finalReservation.current).then(value=>{if(value&&attempt===lifecycle.current)acceptStatus(value);return value;}).catch(failure=>{
      if((failure as {status?:number}).status&&((failure as {status:number}).status<500)){++recordingLease.current;publications.current?.abort();captureRecorder.current?.stop(true);captions.current?.stop();throw failure;}
      if(attempt===lifecycle.current)setError(t('The recording remains private until its publication receipt can be recovered.'));return undefined;
    });
    const drain=captions.current?captions.current.drain(6000).catch(()=>{incompleteParts.current.add(id);if(attempt===lifecycle.current)setError(t('The final caption window could not finish. This recording is saved with an incomplete-transcript notice and no automatic AI notes.'));}):Promise.resolve();
    const [reserved]=await Promise.all([finalReceipt.current,drain]);if(attempt!==lifecycle.current||captureRecorder.current!==endingRecorder){await endingRecorder.stop(true);return;}if(reserved)acceptStatus(reserved);
    await endingRecorder.stop(); if(captureRecorder.current===endingRecorder)captureRecorder.current = null; captions.current?.stop(); setRecording(false); setPrefs(value => ({ ...value, enabled: false }));
  }
  async function stop():Promise<CaptureFinalization>{if(stopPending.current)return stopPending.current;const operation=(async()=>{
    if(guestToken)return {privatePending:false};
    const expected=owner(),attempt=lifecycle.current;await stopCapture();await within(queue.current,8000);assertOwner(expected);
    if(unretained.current)return {privatePending:false,retentionFailed:true};
    const pending=(await pendingMeetingRecordings(channelId)).some(row=>row.stage==='captured');assertOwner(expected);
    if(attempt===lifecycle.current&&pending)setPublicationState('private');return {privatePending:pending};
  })();stopPending.current=operation;try{return await operation;}finally{if(stopPending.current===operation)stopPending.current=null;}}
  const stopRef = useRef(stop); stopRef.current = stop;
  useEffect(() => registerMeetingFinalizer(meetingId, () => stopRef.current()), [meetingId]);
  const capture = status?.capture, mine = !!capture?.consented.includes(status?.participant_id || '');
  return <section className="fc-meeting-capture" aria-label={t('Recording and live captions')}>
    <header><strong>{recording ? <Circle size={14} className="fc-recording-dot" /> : <ShieldCheck size={15} />}{t('Recording and live captions')}</strong>
      {capture && <span role="status">{t(capture.state === 'recording' ? 'Recording is active' : capture.state === 'paused' ? 'Recording is paused' : capture.state === 'stopped' ? 'Recording has stopped' : 'Waiting for everyone’s consent')}</span>}</header>
    {status?.configuration.sample_upload_limit_bytes && <p className="fc-account-muted" role="note">{t("Sample recording parts are at most 15 seconds with a 1.5 MiB target. The sample allows 2 MiB per upload and 8 MiB for the session; over-limit parts stay private for download or discard.")}</p>}
    {error && <p role="alert" className="fc-alert">{t(error)}</p>}{progress && <p role="status"><Loader2 size={14} />{t(progress)}</p>}
    {publicationState&&<p role="status">{t(publicationState==='saving'?'Saving the recording to channel clips…':publicationState==='saved'?'Recording saved to channel clips.':'Recording publication is pending. Review Private recording drafts on this device.')}</p>}
    {unretainedPart&&<div role="alert"><p>{t('This recording could not be stored. Keep this window open and download or discard its available bytes before leaving.')}</p><button className="fc-button" onClick={()=>{const url=URL.createObjectURL(unretainedPart.blob),link=document.createElement('a');link.href=url;link.download='unsaved-meeting-'+unretainedPart.id+(unretainedPart.blob.type.includes('mp4')?'.mp4':'.webm');link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}}>{t('Download unsaved recording')}</button><button className="fc-button" onClick={()=>{unretained.current=null;setUnretainedPart(null);}}>{t('Discard unsaved recording')}</button></div>}
    {!capture && view.host && <details><summary>{t('Set up automatic recording')}</summary>
      <p>{t('Everyone admitted must explicitly agree before capture starts. New participants or revoked consent stop the current segment. Five-minute parts are published to channel clips using the channel’s encryption.')}</p>
      <label><input type="checkbox" checked={prefs.video} onChange={event => setPrefs({ ...prefs, video: event.target.checked })} />{t('Include camera and shared-screen video')}</label>
      <label><input type="checkbox" checked={prefs.captions} onChange={event => setPrefs({ ...prefs, captions: event.target.checked, save_transcript: event.target.checked && prefs.save_transcript })} />{t('Generate continuous captions on this device')}</label>
      <p className="fc-account-muted">{t('The pinned Whisper model downloads to this browser, or uses the operator’s local mirror. Audio is processed on this device; recognition can be wrong.')} {secure||transport==='matrix'?t('Live captions are disclosed to all encrypted channel members, including people outside this call.'):t('Live captions are shared only with admitted meeting participants.')}</p>
      <label><input type="checkbox" checked={prefs.save_transcript} disabled={!prefs.captions} onChange={event => setPrefs({ ...prefs, save_transcript: event.target.checked })} />{t('Save the transcript with each recording part')}</label>
      <label>{t('Spoken language')}<select className="fc-input" value={prefs.language} onChange={event => setPrefs({ ...prefs, language: event.target.value })}>
        {(status?.configuration.languages || ['auto','en','sv']).map(language => <option key={language} value={language}>{language === 'auto' ? t('Detect language') : language.toUpperCase()}</option>)}</select></label>
      {!secure && !guestToken && <fieldset><legend>{t('Automatic private meeting notes')}</legend>
        <select className="fc-input" aria-label={t('Meeting AI model')} value={profileId} onChange={event => setProfileId(event.target.value)}><option value="">{t('No automatic AI')}</option>
          {profiles.filter(profile => profile.enabled && profile.configured && profile.consented).map(profile => <option key={profile.id} value={profile.id}>{profile.name} · {profile.model}</option>)}</select>
        {selected && <><p>{t('Recipient')}: {new URL(selected.endpoint).hostname} · {selected.model} · {selected.protocol}</p>
          <p>{t('One bounded summary request per recorded part, up to 24 parts for seven days. Provider API charges and workspace budgets apply. Results stay private until explicitly shared.')}</p>
          <label><input type="checkbox" checked={aiAcknowledged} onChange={event => setAIAcknowledged(event.target.checked)} />{t('I approve this recipient receiving the saved transcripts automatically after all participants consent. Enable and retain captions for these notes.')}</label></>}</fieldset>}
      {secure && <p>{t('AI for encrypted recordings requires a separate foreground review on this device. Decrypted text is never placed in a server background queue.')}</p>}
      <button type="button" className="fc-button" disabled={!!busy || view.state !== 'admitted'} onClick={() => void request()}><Circle size={14} />{t('Ask everyone and record automatically')}</button>
    </details>}
    {capture && <>{capture.policy_invalidated&&<p role="alert" className="fc-alert">{t('Channel encryption changed. Stop this recording request and review a new policy.')}</p>}<p>{capture.content_mode==='matrix'?t('Recording files are encrypted on the recording device for this encrypted channel.'):capture.content_mode==='server'?t('Recording files use this installation’s storage and access controls.'):t('This recording has no reviewed encryption policy.')}</p>{capture.captions&&<p role="note">{capture.caption_audience==='channel'?t('Live captions are disclosed to all encrypted channel members, including people outside this call.'):capture.caption_audience==='admitted'?t('Live captions are shared only with admitted meeting participants.'):t('The live-caption recipient audience must be reviewed again.')}</p>}<p>{t('This meeting requests')}: {capture.video ? t('audio and video recording') : t('audio recording')}{capture.captions ? ' · ' + t('live captions') : ''}{capture.save_transcript ? ' · ' + t('saved transcript') : ''}.</p>
      {capture.ai?.enabled && <p role="note">{t('Automatic AI recipient')}: {capture.ai.recipient ? new URL(capture.ai.recipient).hostname : ''} · {capture.ai.model}.
        {' '}{t('Up to 24 saved parts may be sent for private summary within seven days; API charges and retention terms apply.')}
        <button type="button" className="fc-button" onClick={() => { const endpoint = guestToken ? '/meeting-access/' + encodeURIComponent(meetingId) + '/capture/' + encodeURIComponent(capture.id) + '/ai/revoke' : '/channels/' + encodeURIComponent(channelId) + '/meeting-captures/' + encodeURIComponent(capture.id) + '/ai/revoke';
          const promise = guestToken ? fetch(api.getBaseUrl() + endpoint, { method: 'POST', headers: { 'X-Meeting-Token': guestToken }, credentials: 'omit' }).then(result => { if (!result.ok) throw new Error('AI disclosure could not be revoked.'); })
            : api.request(endpoint, { method: 'POST' }); void promise.then(() => authority.status()).then(setStatus).catch(failure => setError(String(failure))); }}>{t('Stop automatic AI disclosure')}</button></p>}
      <div className="fc-meeting-capture-actions">
        {capture.state === "stopped" && view.host && <button type="button" className="fc-button" onClick={() => { requested.current = false; current.current.status = { ...status!, capture: null }; setStatus(current.current.status); }}>{t("Set up another recording")}</button>}
        <button type="button" className="fc-button" disabled={!!busy || capture.state === "stopped"} onClick={() => void action('consent', () => authority.consent(capture, !mine))}>{mine ? t('Withdraw recording consent') : t('I agree to the disclosed recording and processing')}</button>
        {owns && capture.state === 'recording' && <button className="fc-button" onClick={() => { manuallyPaused.current = true; captureRecorder.current?.pause(true); captions.current?.pause(true); void action('pause', () => authority.control(capture, 'pause')); }}><Pause size={14} />{t('Pause recording')}</button>}
        {owns && capture.state === 'paused' && <button className="fc-button" disabled={!capture.all_consented || !!busy} onClick={() => { blocked.current = false; manuallyPaused.current = false; starting.current = ''; setPrefs(value => ({ ...value, enabled: true })); void action('resume', () => authority.control(capture, 'resume')); }}><Play size={14} />{t('Resume recording')}</button>}
        {owns && capture.state !== "stopped" && <button className="fc-button" disabled={!!busy} onClick={() => { void stop().catch(failure => setError(String(failure))); }}><Square size={14} />{t('Stop and save recording')}</button>}
      </div><p className="fc-account-muted">{t('{count} participants still need to consent.', { count: capture.awaiting.length })}</p></>}
    {(localCaptions.length > 0 || !!capture?.live_captions?.length) && <div className="fc-live-captions" role="log" aria-live="polite" aria-label={t('Live captions')}><FileText size={14} />
      {(owns ? localCaptions : capture?.live_captions || []).map((caption, index) => <p key={caption.start + ':' + index}>{caption.text}</p>)}</div>}
  </section>;
}
