import { t, useI18n } from '../i18n';
import { useEffect, useRef, useState } from 'react';
import { AlertCircle, ArrowLeft, ArrowRight, Loader2, Users } from 'lucide-react';
import { api } from '../lib/api';
import { workspaceApi, type Workspace } from '../lib/workspace-api';
import { Brand } from './Brand';
import '../styles/account.css';

interface WorkspaceInvitationProps { token: string; onJoined: (workspace: Workspace) => void; onBack: () => void; }

export function WorkspaceInvitation({ token, onJoined, onBack }: WorkspaceInvitationProps) {
  useI18n();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const identity = useRef(api.getCurrentUserId());
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  async function join() {
    if (busy) return;
    if (!api.getCurrentUserId()) { setError(t("Sign in before accepting this workspace invitation.")); return; }
    const userId = api.getCurrentUserId();
    setBusy(true); setError('');
    try { const workspace = await workspaceApi.acceptInvite(token); if (mounted.current && userId === api.getCurrentUserId() && identity.current === userId) onJoined(workspace); }
    catch (failure) { if (mounted.current && userId === api.getCurrentUserId()) setError(failure instanceof Error ? failure.message : t("This invitation could not be used. Ask your team for a new link.")); }
    finally { if (mounted.current && userId === api.getCurrentUserId()) setBusy(false); }
  }
  return <main className="fc-authentication-flow"><header><Brand /><button className="fc-account-link" type="button" onClick={onBack}><ArrowLeft size={16} />{t("Back to FreeCollab")}</button></header><section className="fc-authentication-card" aria-labelledby="workspace-invitation-title"><span className="fc-account-feature-icon"><Users size={27} /></span><p className="fc-kicker">{t("A shared place")}</p><h1 id="workspace-invitation-title">{t("You’re invited.")}</h1><p>{t("Join your team’s workspace with your FreeCollab account. Your access follows the role in this invitation.")}</p><p className="fc-account-muted">{t("Workspace membership and access to encrypted channel keys are managed separately.")}</p>{error && <div className="fc-alert fc-account-error" role="alert"><AlertCircle size={17} /><p>{error}</p></div>}<button type="button" className="fc-button fc-button-primary" disabled={busy || !token} onClick={() => void join()}>{busy ? <Loader2 size={16} className="fc-account-spinner" /> : <ArrowRight size={17} />}{busy ? t("Joining…") : t("Join workspace")}</button></section></main>;
}
