import { useCallback, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { MessageSquare, ShieldCheck, X } from 'lucide-react';
import { useI18n } from '../i18n';
import { useModalFocus } from '../lib/useModalFocus';
import { PilotFeedbackPanel, type PilotFeedbackGuard } from './PilotFeedbackPanel';
import '../styles/account.css';

export interface WorkspaceFeedbackDialogProps {
  workspaceId: string;
  onClose: () => void;
  onOpenLaunch?: () => void;
  onOpenConnections?: () => void;
}

export function WorkspaceFeedbackDialog({ workspaceId, onClose, onOpenLaunch, onOpenConnections }: WorkspaceFeedbackDialogProps) {
  const { t } = useI18n();
  const dialog = useRef<HTMLElement>(null), guardRef = useRef<PilotFeedbackGuard>(null);
  const [guard, setGuard] = useState<PilotFeedbackGuard>(null);
  const descriptionId = useId();
  const guardChanged = useCallback((next: PilotFeedbackGuard) => { guardRef.current = next; setGuard(next); }, []);
  function mayClose() {
    if (guardRef.current === 'busy') return false;
    return guardRef.current !== 'dirty' || window.confirm(t('Discard your unsaved workspace feedback?'));
  }
  function close() { if (mayClose()) onClose(); }
  function navigate(open?: () => void) { if (open && mayClose()) { onClose(); open(); } }
  useModalFocus(dialog, close);
  return createPortal(<div className="fc-account-overlay" onMouseDown={event => { if (event.target === event.currentTarget) close(); }}>
    <section ref={dialog} className="fc-account-panel fc-workspace-feedback-dialog" role="dialog" aria-modal="true" aria-label={t('Workspace feedback')} aria-describedby={descriptionId} tabIndex={-1}>
      <header className="fc-account-header"><div><span className="fc-kicker"><MessageSquare size={14} aria-hidden="true" /> {t('Optional, within your workspace')}</span><h2>{t('Workspace feedback')}</h2><p id={descriptionId}>{t('Share an outcome, keep a private note, or leave whenever you like.')}</p></div><button type="button" className="fc-icon-button" data-autofocus aria-label={t('Close workspace feedback')} disabled={guard === 'busy'} onClick={close}><X size={20} /></button></header>
      <div className="fc-account-body"><PilotFeedbackPanel workspaceId={workspaceId} onGuardChange={guardChanged} onOpenLaunch={onOpenLaunch ? () => navigate(onOpenLaunch) : undefined} onOpenConnections={onOpenConnections ? () => navigate(onOpenConnections) : undefined} /></div>
      <footer className="fc-account-footer"><ShieldCheck size={15} aria-hidden="true" /><span>{t('Only the feedback you explicitly save is submitted.')}</span><button type="button" className="fc-button" disabled={guard === 'busy'} onClick={close}>{t('Done')}</button></footer>
    </section>
  </div>, document.body);
}
