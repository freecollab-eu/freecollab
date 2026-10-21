import { t, useI18n } from '../i18n';
import { useEffect, useRef, useState } from 'react';
import { Copy, Loader2, Mic, MicOff, Monitor, PhoneOff, Plus, RefreshCw, ShieldCheck, UserCheck, Video, VideoOff, X } from 'lucide-react';
import { api } from '../lib/api';
import { MeetingConnection, leaveMeeting, meetingsApi, type Meeting, type MeetingAccess, type MeetingView } from '../lib/meetings-api';
import '../styles/meetings.css';

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
  const leave = () => { connection.current?.stop(); onLeave(); };
  const admitted = view?.state === 'admitted';
  return <section className="fc-meeting-session" aria-label={t("Live meeting")}>
    <div className="fc-meeting-status"><ShieldCheck size={17} /><p><strong>{t("Browser-to-browser encrypted media")}</strong><span>{t("WebRTC uses DTLS-SRTP. Signalling and participant identities are handled by FreeCollab; this is separate from Matrix device verification. Up to six devices.")}</span></p></div>
    {(error || view?.error) && <p role="alert" className="fc-alert">{error || view?.error}</p>}
    {view?.state === 'waiting' && <div className="fc-meeting-waiting"><Loader2 size={22} className="animate-spin" /><h4>{t("Waiting for the host")}</h4><p>{t("Your camera and microphone remain off until you are admitted and enable them.")}</p></div>}
    {view?.state === 'connecting' && <p role="status">{t("Connecting to the meeting…")}</p>}
    {admitted && !view.local && <div className="fc-meeting-setup"><label><input type="checkbox" checked={camera} onChange={event => setCamera(event.target.checked)} />{t("Start with camera")}</label><button className="fc-button fc-button-primary" disabled={!!busy} onClick={() => void run('media', () => connection.current!.enableMedia(camera))}><Mic size={16} />{t("Enable camera and microphone")}</button><p>{t("Choose your microphone and camera in the browser permission prompt.")}</p></div>}
    {admitted && view.local && <><div className="fc-meeting-grid"><VideoStream stream={view.local} muted label={view.sharing ? t('Your screen') : t('You')} />{view.participants.filter(row => row.id !== access.participant.id && row.state === 'admitted').map(row => <VideoStream key={row.id} stream={view.streams.get(row.id) || null} label={row.name + (row.kind === 'guest' ? t(' · Guest') : '')} />)}</div><p className="fc-account-muted" role="status">{t(view.connected === 1 ? '{count} connected participant' : '{count} connected participants', { count: view.connected })} {t(" · Camera and microphone are sent only to admitted participants.")}</p>
      <div className="fc-meeting-controls"><button className="fc-button" aria-pressed={!view.microphone} onClick={() => connection.current?.toggleMicrophone()}>{view.microphone ? <Mic size={16} /> : <MicOff size={16} />}{view.microphone ? t("Mute microphone") : t("Unmute microphone")}</button><button className="fc-button" disabled={!initialMedia?.getVideoTracks().length && !view.local.getVideoTracks().length} aria-pressed={!view.camera} onClick={() => connection.current?.toggleCamera()}>{view.camera ? <Video size={16} /> : <VideoOff size={16} />}{view.camera ? t("Turn camera off") : t("Turn camera on")}</button><button className="fc-button" disabled={!!busy} aria-pressed={view.sharing} onClick={() => void run('screen', () => connection.current!.shareScreen())}><Monitor size={16} />{view.sharing ? t("Stop sharing screen") : t("Share screen")}</button></div></>}
    {view?.host && admitted && <section className="fc-meeting-host"><h4>{t("Host controls")}</h4><p>{t("Guest invitations expire after 15 minutes, work once, and require your admission.")}</p><button className="fc-button" disabled={!!busy} onClick={() => void run('invite', async () => { const result = await meetingsApi.invite(meetingId); setInvite(result.url); })}><Plus size={15} />{t("Create guest invitation")}</button>{invite && <div className="fc-meeting-invite"><label htmlFor="meeting-invite-url">{t("One-use guest invitation")}</label><input id="meeting-invite-url" className="fc-input" readOnly value={invite} /><button className="fc-button" onClick={() => void run('copy', () => navigator.clipboard.writeText(invite))}><Copy size={15} />{t("Copy invitation")}</button><button className="fc-button" onClick={() => setInvite('')}>{t("Hide invitation")}</button></div>}<ul className="fc-meeting-people">{view.participants.filter(row => row.id !== access.participant.id).map(row => <li key={row.id}><span>{row.name}<small>{row.kind === 'guest' ? t("Guest · ") : ''}{row.state}</small></span>{row.state === 'waiting' && <button className="fc-button" disabled={!!busy} onClick={() => void run('admit', () => meetingsApi.manage(meetingId, row.id, 'admit'))}><UserCheck size={14} />{t("Admit")}</button>}<button className="fc-icon-button" disabled={!!busy} aria-label={'Remove ' + row.name + ' from meeting'} onClick={() => void run('remove', () => meetingsApi.manage(meetingId, row.id, 'remove'))}><X size={15} /></button></li>)}</ul></section>}
    <p className="fc-account-muted">{t("Closing workspace tools leaves this meeting. The host leaving ends the meeting for everyone. A participant may record their own screen; FreeCollab does not record media.")}</p><button className="fc-button fc-meeting-leave" onClick={leave}><PhoneOff size={16} />{view?.host ? t("End meeting for everyone") : t("Leave meeting")}</button>
  </section>;
}

export function MeetingsPanel({ workspaceId, channelId }: { workspaceId: string; channelId: string; isAdmin?: boolean }) {
  useI18n();
  const [meetings, setMeetings] = useState<Meeting[]>([]), [active, setActive] = useState<{ id: string; access: MeetingAccess; media: MediaStream } | null>(null), [camera, setCamera] = useState(true), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const generation = useRef(0), owner = useRef(api.getAccessToken()), mediaOwner = useRef<MediaStream | null>(null);
  async function load() {
    const current = generation.current;
    try { const result = await meetingsApi.list(channelId); if (current === generation.current && owner.current === api.getAccessToken()) setMeetings(result); }
    catch (failure) { if (current === generation.current) setError(describe(failure)); }
  }
  useEffect(() => { ++generation.current; owner.current = api.getAccessToken(); setActive(null); setMeetings([]); setError(''); void load(); const stop = () => { mediaOwner.current?.getTracks().forEach(track => track.stop()); mediaOwner.current = null; }; const changed = () => { ++generation.current; stop(); setActive(null); setMeetings([]); }; window.addEventListener('freecollab:auth-changed', changed); return () => { ++generation.current; stop(); window.removeEventListener('freecollab:auth-changed', changed); }; }, [channelId, workspaceId]);
  async function join(existing?: Meeting) {
    if (busy) return;
    const current = generation.current, token = api.getAccessToken(); setBusy(true); setError(''); let media: MediaStream | null = null;
    try {
      if (!navigator.mediaDevices?.getUserMedia || !window.RTCPeerConnection) throw new Error(t("Meetings require a secure HTTPS browser with microphone access."));
      media = await navigator.mediaDevices.getUserMedia({ audio: true, video: camera });
      mediaOwner.current = media;
      if (current !== generation.current || token !== api.getAccessToken()) { media.getTracks().forEach(t => t.stop()); return; }
      const result = existing ? await meetingsApi.join(existing.id) : await meetingsApi.create(channelId);
      if (current !== generation.current || token !== api.getAccessToken()) { media.getTracks().forEach(t => t.stop()); void leaveMeeting(result.meeting!.id, result.meeting_token).catch(() => undefined); return; }
      setActive({ id: result.meeting!.id, access: result, media }); media = null;
    } catch (failure) { media?.getTracks().forEach(t => t.stop()); if (mediaOwner.current === media) mediaOwner.current = null; if (current === generation.current) setError(describe(failure)); }
    finally { if (current === generation.current) setBusy(false); }
  }
  if (active) return <MeetingSession key={active.access.meeting_token} meetingId={active.id} access={active.access} initialMedia={active.media} onLeave={() => { setActive(null); void load(); }} />;
  return <section className="fc-meetings-panel" aria-label={t("Channel meetings")}><div className="fc-integration-content-heading"><div><h3>{t("A conversation, face to face.")}</h3><p>{t("Voice, video and screen sharing for this channel. Media stays between participants' browsers.")}</p></div><button className="fc-icon-button" aria-label={t("Refresh meetings")} disabled={busy} onClick={() => void load()}><RefreshCw size={16} /></button></div>{error && <p className="fc-alert" role="alert">{error}</p>}<div className="fc-meeting-setup"><label><input type="checkbox" checked={camera} onChange={event => setCamera(event.target.checked)} />{t("Start with camera")}</label><button className="fc-button fc-button-primary" disabled={busy || meetings.length > 0} onClick={() => void join()}>{busy ? <Loader2 size={16} /> : <Video size={16} />}{t("Start channel meeting")}</button></div><ul className="fc-meeting-list">{meetings.map(meeting => <li key={meeting.id}><div><strong>{t("Channel meeting")}</strong><span>{t("Up to ")}{meeting.max_participants} {t(" devices · Browser-to-browser WebRTC encryption")}</span></div><button className="fc-button" disabled={busy} onClick={() => void join(meeting)}>{t("Join meeting")}</button></li>)}</ul>{!meetings.length && <p className="fc-account-muted">{t("No active meeting. Start one when the team is ready.")}</p>}<p className="fc-account-muted">{t("Different networks may need your operator's TURN relay. There is no automatic third-party relay or recording.")}</p></section>;
}
