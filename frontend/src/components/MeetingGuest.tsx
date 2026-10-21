import { t, useI18n } from '../i18n';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Video } from 'lucide-react';
import { leaveMeeting, meetingsApi, type MeetingAccess } from '../lib/meetings-api';
import { MeetingSession } from './MeetingsPanel';
import '../styles/meetings.css';

export function MeetingGuest({ meetingId, invitationToken, onLeave }: { meetingId: string; invitationToken?: string | null; onLeave?: () => void }) {
  useI18n();
  const key = 'freecollab:meeting:' + meetingId;
  const [saved, setSaved] = useState<MeetingAccess | null>(() => {
    if (invitationToken) return null;
    try {
      const value = sessionStorage.getItem(key), result = value ? JSON.parse(value) as MeetingAccess : null;
      return result?.meeting_id === meetingId && result.participant?.kind === 'guest' && typeof result.meeting_token === 'string'
        && result.meeting_token.length >= 32 && result.meeting_token.length <= 256 ? result : null;
    } catch { return null; }
  });
  const [access, setAccess] = useState<MeetingAccess | null>(null);
  const [name, setName] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState(''), [left, setLeft] = useState(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  async function join(event: FormEvent) {
    event.preventDefault(); if (!invitationToken || busy) return; setBusy(true); setError('');
    try { const result = await meetingsApi.guest(meetingId, invitationToken, name.trim()); if (!mounted.current) { void leaveMeeting(meetingId, result.meeting_token).catch(() => undefined); return; } sessionStorage.setItem(key, JSON.stringify(result)); setAccess(result); }
    catch (failure) { if (mounted.current) setError(failure instanceof Error ? failure.message : t("The guest invitation could not be used.")); }
    finally { if (mounted.current) setBusy(false); }
  }
  async function resume() {
    if (!saved || busy) return; setBusy(true); setError('');
    try { const result = await meetingsApi.resumeGuest(meetingId, saved.meeting_token); if (!mounted.current) { void leaveMeeting(meetingId, result.meeting_token).catch(() => undefined); return; } sessionStorage.setItem(key, JSON.stringify(result)); setSaved(null); setAccess(result); }
    catch (failure) { if (mounted.current) setError(failure instanceof Error ? failure.message : t("Ask the host for a new invitation.")); }
    finally { if (mounted.current) setBusy(false); }
  }
  function leave() { sessionStorage.removeItem(key); setSaved(null); setAccess(null); setLeft(true); onLeave?.(); }
  return <main className="fc-meeting-guest"><div className="fc-meeting-guest-card"><span className="fc-kicker">{t("FreeCollab · Guest meeting")}</span><h1>{t("A place to meet.")}</h1><p>{t("Your invitation grants access to this call. Guest participation does not grant access to the workspace, chat or files.")}</p>{access ? <MeetingSession meetingId={meetingId} access={access} onLeave={leave} /> : left ? <p role="status">{t("You have left the meeting. You can close this tab.")}</p> : saved ? <div className="fc-meeting-guest-form"><p>{t("Continue as ")}{saved.participant.name}{t(". The host must admit you again after reconnecting.")}</p><button className="fc-button fc-button-primary" disabled={busy} onClick={() => void resume()}>{t("Request to rejoin meeting")}</button>{error && <p className="fc-alert" role="alert">{error}</p>}</div> : invitationToken ? <form className="fc-meeting-guest-form" onSubmit={join}><label htmlFor="meeting-guest-name">{t("Your name")}</label><input autoFocus id="meeting-guest-name" className="fc-input" maxLength={80} required value={name} disabled={busy} onChange={event => setName(event.target.value)} /><button className="fc-button fc-button-primary" disabled={busy || !name.trim()}><Video size={16} />{t("Ask to join meeting")}</button>{error && <p className="fc-alert" role="alert">{error}</p>}<p className="fc-account-muted">{t("The host must admit you. Your camera and microphone stay off until you choose to enable them.")}</p></form> : <p role="alert">{t("This guest invitation is unavailable. Ask the host for a new invitation.")}</p>}</div></main>;
}
