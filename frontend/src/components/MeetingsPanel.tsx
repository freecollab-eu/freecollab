import { t, useI18n } from '../i18n';
import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { Copy, Loader2, Mic, MicOff, Monitor, PhoneOff, Plus, RefreshCw, ShieldCheck, UserCheck, Video, VideoOff, X } from 'lucide-react';
import { api } from '../lib/api';
import { MeetingConnection, leaveMeeting, meetingsApi, type Meeting, type MeetingAccess, type MeetingView } from '../lib/meetings-api';
import '../styles/meetings.css';
import { ClipsPanel } from './ClipsPanel';
import { NativeCallJoinButton } from './NativeCalling';
import { currentMeeting, subscribeMeeting, startMeshMeeting, startSFUMeeting, startMatrixMeeting } from '../lib/active-meeting';
import { sfuApi, type SFURoom, type SFUStatus } from '../lib/sfu-api';
import { useMediaTargetFocus } from '../lib/useMediaTargetFocus';
import { MeetingCaptureControls } from './MeetingCaptureControls';
import { finalizeMeetingCapture } from '../lib/active-meeting';
import { MatrixMeetingRoom, listMatrixMeetings, getMatrixMeeting } from '../lib/federated-meeting';
import { MatrixCaptureAuthority } from '../lib/matrix-meeting-capture';
import { loadEncryptedRoomContext } from '../lib/encrypted-work-api';

const describe = (error: unknown) => error instanceof Error ? error.message : 'The meeting action could not be completed.';
function VideoStream({ stream, muted, label }: { stream: MediaStream | null; muted?: boolean; label: string }) {
  useI18n();
  const video = useRef<HTMLVideoElement>(null);
  useEffect(() => { if (video.current) { video.current.srcObject = stream; void video.current.play().catch(() => undefined); } }, [stream]);
  return <figure className="fc-meeting-video"><video ref={video} autoPlay playsInline muted={muted} aria-label={label} /><figcaption>{label}</figcaption></figure>;
}

export function MeetingSession({ meetingId, access, initialMedia, onLeave }: { meetingId: string; access: MeetingAccess; initialMedia?: MediaStream | null; onLeave: () => void }) {
  useI18n();
  const [view, setView] = useState<MeetingView | null>(null), [busy, setBusy] = useState(''), [error, setError] = useState(''), [invite, setInvite] = useState(''), [camera, setCamera] = useState(true);
  const connection = useRef<MeetingConnection | null>(null), generation = useRef(0);
  useEffect(() => {
    ++generation.current;
    let client = connection.current;
    if (!client || client.access.meeting_token !== access.meeting_token) {
      client?.stop();
      const created = new MeetingConnection(meetingId, access, initialMedia || null, value => { if (connection.current === created) setView(value); });
      client = created; connection.current = created; created.start();
    }
    const unload = () => client.stop();
    const auth = () => { if (access.participant.kind === 'member') { client.stop(); onLeave(); } };
    window.addEventListener('pagehide', unload); window.addEventListener('freecollab:auth-changed', auth);
    return () => {
      const cleanupGeneration = ++generation.current;
      window.removeEventListener('pagehide', unload); window.removeEventListener('freecollab:auth-changed', auth);
      queueMicrotask(() => { if (generation.current === cleanupGeneration) { client.stop(); if (connection.current === client) connection.current = null; } });
    };
  }, [meetingId, access.meeting_token]);
  async function run(key: string, operation: () => Promise<unknown>) {
    if (busy) return; const current = generation.current; setBusy(key); setError('');
    try { await operation(); } catch (failure) { if (current === generation.current) setError(describe(failure)); }
    finally { if (current === generation.current) setBusy(''); }
  }
  const leave = async () => { await finalizeMeetingCapture(meetingId); connection.current?.stop(); onLeave(); };
  const admitted = view?.state === 'admitted';
  return <section className="fc-meeting-session" aria-label={t("Live meeting")}>
    <div className="fc-meeting-status"><ShieldCheck size={17} /><p><strong>{t("Browser-to-browser encrypted media")}</strong><span>{t("WebRTC uses DTLS-SRTP. Signalling and participant identities are handled by FreeCollab; this is separate from Matrix device verification. Up to six devices.")}</span></p></div>
    {(error || view?.error) && <p role="alert" className="fc-alert">{error || view?.error}</p>}
    {view?.state === 'waiting' && <div className="fc-meeting-waiting"><Loader2 size={22} className="animate-spin" /><h4>{t("Waiting for the host")}</h4><p>{t("Your camera and microphone remain off until you are admitted and enable them.")}</p></div>}
    {view?.state === 'connecting' && <p role="status">{t("Connecting to the meeting…")}</p>}
    {admitted && !view.local && <div className="fc-meeting-setup"><label><input type="checkbox" checked={camera} onChange={event => setCamera(event.target.checked)} />{t("Start with camera")}</label><button className="fc-button fc-button-primary" disabled={!!busy} onClick={() => void run('media', () => connection.current!.enableMedia(camera))}><Mic size={16} />{t("Enable camera and microphone")}</button><p>{t("Choose your microphone and camera in the browser permission prompt.")}</p></div>}
    {admitted && view.local && <><div className="fc-meeting-grid"><VideoStream stream={view.local} muted label={view.sharing ? t('Your screen') : t('You')} />{view.participants.filter(row => row.id !== access.participant.id && row.state === 'admitted').map(row => <VideoStream key={row.id} stream={view.streams.get(row.id) || null} label={row.name + (row.kind === 'guest' ? t(' · Guest') : '')} />)}</div><p className="fc-account-muted" role="status">{t(view.connected === 1 ? '{count} connected participant' : '{count} connected participants', { count: view.connected })} {t(" · Camera and microphone are sent only to admitted participants.")}</p>
      <div className="fc-meeting-controls"><button className="fc-button" aria-pressed={!view.microphone} onClick={() => connection.current?.toggleMicrophone()}>{view.microphone ? <Mic size={16} /> : <MicOff size={16} />}{view.microphone ? t("Mute microphone") : t("Unmute microphone")}</button><button className="fc-button" disabled={!initialMedia?.getVideoTracks().length && !view.local.getVideoTracks().length} aria-pressed={!view.camera} onClick={() => connection.current?.toggleCamera()}>{view.camera ? <Video size={16} /> : <VideoOff size={16} />}{view.camera ? t("Turn camera off") : t("Turn camera on")}</button><button className="fc-button" disabled={!!busy} aria-pressed={view.sharing} onClick={() => void run('screen', () => connection.current!.shareScreen())}><Monitor size={16} />{view.sharing ? t("Stop sharing screen") : t("Share screen")}</button></div></>}
    {view && <MeetingCaptureControls meetingId={meetingId} channelId={access.meeting?.channel_id || ""} transport="mesh" ownId={access.participant.id} view={view} guestToken={access.participant.kind === "guest" ? access.meeting_token : undefined} />}
    {view?.host && admitted && <section className="fc-meeting-host"><h4>{t("Host controls")}</h4><p>{t("Guest invitations expire after 15 minutes, work once, and require your admission.")}</p><button className="fc-button" disabled={!!busy} onClick={() => void run('invite', async () => { const result = await meetingsApi.invite(meetingId); setInvite(result.url); })}><Plus size={15} />{t("Create guest invitation")}</button>{invite && <div className="fc-meeting-invite"><label htmlFor="meeting-invite-url">{t("One-use guest invitation")}</label><input id="meeting-invite-url" className="fc-input" readOnly value={invite} /><button className="fc-button" onClick={() => void run('copy', () => navigator.clipboard.writeText(invite))}><Copy size={15} />{t("Copy invitation")}</button><button className="fc-button" onClick={() => setInvite('')}>{t("Hide invitation")}</button></div>}<ul className="fc-meeting-people">{view.participants.filter(row => row.id !== access.participant.id).map(row => <li key={row.id}><span>{row.name}<small>{row.kind === 'guest' ? t("Guest · ") : ''}{row.state}</small></span>{row.state === 'waiting' && <button className="fc-button" disabled={!!busy} onClick={() => void run('admit', () => meetingsApi.manage(meetingId, row.id, 'admit'))}><UserCheck size={14} />{t("Admit")}</button>}<button className="fc-icon-button" disabled={!!busy} aria-label={'Remove ' + row.name + ' from meeting'} onClick={() => void run('remove', () => meetingsApi.manage(meetingId, row.id, 'remove'))}><X size={15} /></button></li>)}</ul></section>}
    <p className="fc-account-muted">{t("Closing workspace tools leaves this meeting. The host leaving ends the meeting for everyone. Recording requires explicit consent from all admitted participants and runs on the recording host’s trusted device.")}</p><button className="fc-button fc-meeting-leave" onClick={leave}><PhoneOff size={16} />{view?.host ? t("End meeting for everyone") : t("Leave meeting")}</button>
  </section>;
}

export function MeetingsPanel({ workspaceId, channelId, focusMeetingId, focusClipId }: { workspaceId: string; channelId: string; isAdmin?: boolean; focusMeetingId?: string; focusClipId?: string }) {
  useI18n();
  const active = useSyncExternalStore(subscribeMeeting, currentMeeting);
  const lastCall=useRef(active);if(active)lastCall.current=active;
  const mediaRegion = useRef<HTMLElement>(null);
  const [meetings, setMeetings] = useState<Meeting[]>([]), [rooms, setRooms] = useState<SFURoom[]>([]), [sfu, setSfu] = useState<SFUStatus | null>(null);
  const [camera, setCamera] = useState(true), [autoRecord, setAutoRecord] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [mode,setMode]=useState<'native'|'matrix'|null>(null);
  const generation = useRef(0), owner = useRef(api.getAccessToken());
  useMediaTargetFocus(mediaRegion, channelId, 'meeting', focusMeetingId, async (id, current) => {
    try {
      const currentChannel=await api.getChannel(channelId);
      const row = currentChannel.secure ? await getMatrixMeeting(channelId,id) : await meetingsApi.get(channelId, id); if (!current()) return false;
      if ('transport' in row && row.transport === 'livekit') setRooms(previous => [row as unknown as SFURoom, ...previous.filter(item => item.id !== row.id)]);
      else setMeetings(previous => [row, ...previous.filter(item => item.id !== row.id)]);
      return true;
    } catch (failure) { if (current()) setError(describe(failure)); return false; }
  });
  async function load(isCurrent:()=>boolean=()=>true) {
    const current = generation.current;
    const scope=await api.getChannel(channelId);
    if(current!==generation.current||owner.current!==api.getAccessToken()||!isCurrent())return;
    if(scope.secure){const context=await loadEncryptedRoomContext(channelId);if(!context.room_protocols.includes('freecollab-matrix-meeting/1'))throw new Error('This encrypted channel needs verified meeting signaling on both installations.');
      const rows=await listMatrixMeetings(channelId);if(current===generation.current&&owner.current===api.getAccessToken()&&isCurrent()){setMode('matrix');setMeetings(rows);setRooms([]);}return rows.map(row=>row.id);}
    setMode('native');
    const results = await Promise.allSettled([meetingsApi.list(channelId), sfuApi.list(channelId)]);
    if (current !== generation.current || owner.current !== api.getAccessToken()||!isCurrent()) return;
    if (results[0].status === 'fulfilled') setMeetings(results[0].value); else setError(describe(results[0].reason));
    if (results[1].status === 'fulfilled') { setRooms(results[1].value.rooms); setSfu(results[1].value.status); }
    else setSfu({ provider: 'disabled', configured: false, ready: false, reason: describe(results[1].reason), max_participants: 0 });
    return results[0].status==='fulfilled'?results[0].value.map(row=>row.id):undefined;
  }
  useEffect(() => { ++generation.current; owner.current = api.getAccessToken(); setMeetings([]); setRooms([]); setError(''); setMode(null); void load().catch(failure=>setError(describe(failure))); return () => { ++generation.current; }; }, [channelId, workspaceId]);
  useEffect(()=>{
    if(active)return;const ended=lastCall.current;lastCall.current=null;if(!ended||ended.channelId!==channelId)return;
    let live=true,timer:ReturnType<typeof setTimeout>;const attempt=generation.current,token=api.getAccessToken();
    const cancel=()=>{live=false;clearTimeout(timer);};window.addEventListener('freecollab:auth-changed',cancel);
    const refresh=async(remaining:number)=>{try{const ids=await load(()=>live&&attempt===generation.current&&token===api.getAccessToken());if(!live||attempt!==generation.current||token!==api.getAccessToken())return;
      // Native leave is asynchronous; wait for the server's ended row to leave
      // discovery rather than keeping a stale disabled Start button forever.
      if(remaining&&ended.view?.host&&ended.transport!=='livekit'&&ids?.includes(ended.id))timer=setTimeout(()=>void refresh(remaining-1),500);
    }catch(failure){if(live&&attempt===generation.current)setError(describe(failure));}};
    void refresh(10);return()=>{cancel();window.removeEventListener('freecollab:auth-changed',cancel);};
  },[active?.id,channelId]);
  async function join(existing?: Meeting | SFURoom, larger = false) {
    if (busy || active) return;
    const current = generation.current, token = api.getAccessToken(); setBusy(true); setError(''); let media: MediaStream | null = null;
    try {
      if (!navigator.mediaDevices?.getUserMedia || !window.RTCPeerConnection) throw new Error(t('Meetings require a secure HTTPS browser with microphone access.'));
      media = await navigator.mediaDevices.getUserMedia({ audio: true, video: camera });
      if (current !== generation.current || token !== api.getAccessToken()) { media.getTracks().forEach(track => track.stop()); return; }
      if(mode==='matrix'){const room=await MatrixMeetingRoom.open(channelId,existing?.id);if(current!==generation.current||token!==api.getAccessToken()){media.getTracks().forEach(track=>track.stop());void room.request('leave',{}).catch(()=>{});return;}startMatrixMeeting(room,media,{enabled:autoRecord,video:false,captions:false,save_transcript:false,language:'auto'},new MatrixCaptureAuthority(room));
      } else if (larger) {
        const room = existing as SFURoom | undefined || await sfuApi.create(channelId), access = await sfuApi.join(room.id);
        if (current !== generation.current || token !== api.getAccessToken()) { media.getTracks().forEach(track => track.stop()); void sfuApi.leave(room.id, access.participant_id).catch(() => undefined); return; }
        startSFUMeeting(access, media, { enabled: autoRecord, video: false, captions: false, save_transcript: false, language: 'auto' });
      } else {
        const access = existing ? await meetingsApi.join(existing.id) : await meetingsApi.create(channelId);
        if (current !== generation.current || token !== api.getAccessToken()) { media.getTracks().forEach(track => track.stop()); void leaveMeeting(access.meeting!.id, access.meeting_token, token || undefined).catch(() => undefined); return; }
        startMeshMeeting(channelId, access, media, { enabled: autoRecord, video: false, captions: false, save_transcript: false, language: 'auto' });
      }
      media = null; await load();
    } catch (failure) { media?.getTracks().forEach(track => track.stop()); if (current === generation.current) setError(describe(failure)); }
    finally { if (current === generation.current) setBusy(false); }
  }
  return <section ref={mediaRegion} className="fc-meetings-panel" aria-label={t('Channel meetings')}><div className="fc-integration-content-heading"><div><h3>{t('A conversation, face to face.')}</h3><p>{mode === 'matrix' ? t('Verified Matrix devices exchange encrypted signaling across installations. Camera and audio travel directly between admitted browsers.') : t('Start a browser meeting, or use your own LiveKit infrastructure for a larger room.')}</p></div><button className="fc-icon-button" aria-label={t('Refresh meetings')} disabled={busy} onClick={() => void load()}><RefreshCw size={16} /></button></div>{error && <p className="fc-alert" role="alert">{error}</p>}{active && <p role="status" className="fc-account-muted">{t('Your call stays connected in the meeting dock while you keep working.')}</p>}<div className="fc-meeting-setup"><label><input type="checkbox" checked={camera} onChange={event => setCamera(event.target.checked)} />{t('Start with camera')}</label><label><input type="checkbox" checked={autoRecord} onChange={event => setAutoRecord(event.target.checked)} />{t('Ask participants to record automatically after consent')}</label><button className="fc-button fc-button-primary" disabled={busy || !!active || !mode || meetings.length > 0} onClick={() => void join()}>{busy ? <Loader2 size={16} /> : <Video size={16} />}{t('Start channel meeting')}</button></div><ul className="fc-meeting-list">{meetings.map(meeting => <li key={meeting.id} tabIndex={-1} data-media-id={meeting.id} data-media-kind="meeting"><div><strong>{t('Channel meeting')}</strong><span>{t('Up to ')}{meeting.max_participants} {mode==='matrix'?t(' devices · Verified Matrix signaling and WebRTC media'):t(' devices · Browser-to-browser WebRTC encryption')}</span></div><button className="fc-button" disabled={busy || !!active} onClick={() => void join(meeting)}>{t('Join meeting')}</button></li>)}</ul><p className="fc-account-muted">{t("Different networks may need your operator's TURN relay. No third-party relay is selected automatically. Recording requires participant consent.")}</p>{mode !== "matrix" && <section className="fc-meeting-sfu"><h4>{t('Larger meetings on your infrastructure')}</h4><p>{sfu?.ready ? t('LiveKit is configured for up to {count} devices. Rooms stay open when the host leaves.', { count: sfu.max_participants }) : sfu?.reason || t('Checking meeting infrastructure…')}</p><button className="fc-button" disabled={busy || !!active || !sfu?.ready || rooms.length > 0} onClick={() => void join(undefined, true)}>{t('Start larger meeting')}</button><ul className="fc-meeting-list">{rooms.map(room => <li key={room.id} tabIndex={-1} data-media-id={room.id} data-media-kind="meeting"><span>LiveKit · {room.max_participants} {t('devices')}</span><button className="fc-button" disabled={busy || !!active || !sfu?.ready} onClick={() => void join(room, true)}>{t('Join meeting')}</button><NativeCallJoinButton roomId={room.id} channelId={channelId} /></li>)}</ul><p className="fc-account-muted">{t('Media uses DTLS-SRTP to your SFU. The SFU can access media. This is separate from Matrix end-to-end channel encryption.')}</p></section>}<ClipsPanel channelId={channelId} workspaceId={workspaceId} focusClipId={focusClipId} /></section>;
}
