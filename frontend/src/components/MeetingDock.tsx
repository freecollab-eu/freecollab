import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { Mic, MicOff, Video, VideoOff, Monitor, PhoneOff, ChevronDown, ChevronUp } from 'lucide-react';
import { t, useI18n } from '../i18n';
import { currentMeeting, subscribeMeeting, leaveActiveMeeting, endActiveMeeting } from '../lib/active-meeting';
import { MeetingConnection, meetingsApi } from '../lib/meetings-api';
import { sfuApi } from '../lib/sfu-api';
import '../styles/meeting-dock.css';

function Stream({ stream, local, label }: { stream: MediaStream | null; local?: boolean; label: string }) {
  const video = useRef<HTMLVideoElement>(null);
  useEffect(() => { if (video.current) { video.current.srcObject = stream; void video.current.play().catch(() => undefined); } }, [stream]);
  return <figure className="fc-meeting-video"><video ref={video} autoPlay playsInline muted={local} aria-label={label} /><figcaption>{label}</figcaption></figure>;
}
/** Mounted above workspace tools, so navigating/closing a panel retains the call. */
export function MeetingDock() {
  useI18n();
  const call = useSyncExternalStore(subscribeMeeting, currentMeeting), [expanded, setExpanded] = useState(true), [error, setError] = useState(''), [invite, setInvite] = useState('');
  useEffect(() => { setInvite(''); setError(''); }, [call?.id]);
  if (!call) return null;
  const view = call.view;
  const fail = (failure: unknown) => setError(failure instanceof Error ? failure.message : 'The meeting action failed.');
  const ownId = call.connection instanceof MeetingConnection ? call.connection.access.participant.id : call.connection.access.participant_id;
  return <aside className={'fc-meeting-dock' + (expanded ? '' : ' fc-meeting-dock-compact')} aria-label={t('Active channel meeting')}><header><div><strong>{call.transport === 'mesh' ? t('Browser-to-browser encrypted media') : t('You are in a meeting')}</strong><small>{call.transport === 'livekit' ? t('Customer LiveKit · DTLS-SRTP to the SFU') : t('Browser mesh · DTLS-SRTP · Six devices')}</small></div><button className="fc-icon-button" aria-label={expanded ? t('Minimize meeting') : t('Expand meeting')} onClick={() => setExpanded(!expanded)}>{expanded ? <ChevronDown size={18} /> : <ChevronUp size={18} />}</button></header>{(error || view?.error) && <p className="fc-alert" role="alert">{error || view?.error}</p>}
    <div className={expanded ? 'fc-meeting-grid' : 'fc-meeting-dock-audio'}><Stream stream={view?.local || null} local label={t('You')} />{view && [...view.streams].map(([id, stream]) => <Stream key={id} stream={stream} label={view.participants.find(participant => participant.id === id)?.name || t('Participant')} />)}</div>
    <div className="fc-meeting-controls"><button className="fc-button" disabled={view?.state !== 'admitted'} aria-label={view?.microphone ? t('Mute microphone') : t('Unmute microphone')} aria-pressed={!view?.microphone} onClick={() => call.connection.toggleMicrophone()}>{view?.microphone ? <Mic size={16} /> : <MicOff size={16} />}</button><button className="fc-button" disabled={view?.state !== 'admitted' || !view?.local?.getVideoTracks().length} aria-label={view?.camera ? t('Turn camera off') : t('Turn camera on')} aria-pressed={!view?.camera} onClick={() => call.connection.toggleCamera()}>{view?.camera ? <Video size={16} /> : <VideoOff size={16} />}</button><button className="fc-button" disabled={view?.state !== 'admitted'} aria-label={view?.sharing ? t('Stop sharing screen') : t('Share screen')} aria-pressed={view?.sharing} onClick={() => { void call.connection.shareScreen().catch(fail); }}><Monitor size={16} /></button><button className="fc-button fc-meeting-leave" onClick={leaveActiveMeeting}><PhoneOff size={16} />{call.transport === 'mesh' && view?.host ? t('End meeting for everyone') : t('Leave meeting')}</button>{call.transport === 'livekit' && view?.host && <button className="fc-button" onClick={() => { void endActiveMeeting().catch(fail); }}>{t('End meeting for everyone')}</button>}</div>
    {view?.host && expanded && <section className="fc-meeting-host"><h4>{t('Host controls')}</h4>{call.transport === 'mesh' && <><button className="fc-button" onClick={() => { void meetingsApi.invite(call.id).then(result => setInvite(result.url)).catch(fail); }}>{t('Create guest invitation')}</button>{invite && <div className="fc-meeting-invite"><label htmlFor="dock-meeting-invite">{t('One-use guest invitation')}</label><input id="dock-meeting-invite" className="fc-input" value={invite} readOnly /><button className="fc-button" onClick={() => setInvite('')}>{t('Hide invitation')}</button></div>}</>}<ul className="fc-meeting-people">{view.participants.filter(row => row.id !== ownId).map(row => <li key={row.id}><span>{row.name}<small>{row.state}</small></span>{row.state === 'waiting' && <button className="fc-button" onClick={() => { void meetingsApi.manage(call.id, row.id, 'admit').catch(fail); }}>{t('Admit')}</button>}<button className="fc-icon-button" aria-label={'Remove ' + row.name + ' from meeting'} onClick={() => { void (call.transport === 'mesh' ? meetingsApi.manage(call.id, row.id, 'remove') : sfuApi.remove(call.id, row.id)).catch(fail); }}>×</button></li>)}</ul></section>}
    <p className="fc-account-muted">{call.transport === 'livekit' ? t('This room stays open when the host leaves. The SFU can access media; Matrix end-to-end encryption does not apply.') : t('Keep working while the call stays connected. The host leaving ends a browser mesh meeting.')}</p>
  </aside>;
}
