import { t, useI18n } from '../i18n';
import { useEffect, useRef, useState } from 'react';
import { ArrowRight, AlertCircle, DoorOpen, Loader2, Users } from 'lucide-react';
import { Brand } from './Brand';
import { api, Channel } from '../lib/api';
import '../styles/public.css';

export function AcceptInvite({ token, onJoined, onCancel }: { token: string; onJoined: (channel: Channel) => void; onCancel: () => void }) {
  useI18n();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const errorRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (error) errorRef.current?.focus();
  }, [error]);

  const join = async () => {
    if (busy) return;
    setBusy(true);
    setError('');
    try { onJoined(await api.acceptChannelInvite(token)); }
    catch (err) { setError(err instanceof Error ? err.message : t("Please try again in a moment.")); }
    finally { setBusy(false); }
  };

  return (
    <main className="fc-invite">
      <Brand size="large" />
      <section className="fc-invite-card" aria-labelledby="invite-heading" aria-busy={busy}>
        <div className="fc-invite-card-overline"><span>{t("AN INVITATION TO COLLABORATE")}</span><span>01</span></div>
        <div className="fc-invite-icon" aria-hidden="true"><DoorOpen size={31} strokeWidth={1.3} /></div>
        <h1 id="invite-heading">{t("You have a")}<br /><em>{t("channel invitation.")}</em></h1>
        <p className="fc-invite-description">{t("Join with your signed-in account to see this channel’s conversations and shared files.")}</p>
        <div className="fc-invite-detail"><Users size={19} strokeWidth={1.4} aria-hidden="true" /><p>{t("Your place in the conversation.")}<span>{t("You become a member only when you choose to join. Accept invitations from people you trust.")}</span></p></div>
        {error && <div ref={errorRef} tabIndex={-1} role="alert" className="fc-alert fc-alert-error fc-public-error"><AlertCircle size={18} aria-hidden="true" /><div><strong>{t("We couldn’t accept this invitation.")}</strong><p>{error}</p></div></div>}
        <div className="fc-invite-actions"><button type="button" aria-label={t("Join channel")} className="fc-button fc-button-primary" disabled={busy} onClick={() => void join()}>{busy ? <><span role="status">{t("Joining channel…")}</span><Loader2 size={17} className="fc-public-spinner" aria-hidden="true" /></> : <>{t("Join channel")}<ArrowRight size={17} aria-hidden="true" /></>}</button><button type="button" className="fc-button" disabled={busy} onClick={onCancel}>{t("Cancel")}</button></div>
        {error && <p className="fc-invite-help">{t("If the link has expired or was already used, ask a channel admin for a fresh invitation.")}</p>}
      </section>
      <p className="fc-invite-footer">{t("A shared channel. A new perspective.")}</p>
    </main>
  );
}
