import { Maximize2, Minimize2, Minus, X } from 'lucide-react';
import { t, useI18n } from '../i18n';
import '../styles/work-surface.css';

export interface ConversationWorkLayoutActions {
  expanded: boolean;
  onToggleExpanded: () => void;
  onMinimize: () => void;
  onBeforeViewChange?: () => void;
  workAreaId?: string;
}

interface Props {
  layout: ConversationWorkLayoutActions;
  onClose: () => void;
  closeLabel?: string;
}

/** Changing the view leaves the active work and its draft mounted. */
export function ConversationWorkControls({ layout, onClose, closeLabel = 'Close this work' }: Props) {
  useI18n();
  const expandLabel = layout.expanded ? t('Restore beside conversation') : t('Expand work area');
  const ExpandIcon = layout.expanded ? Minimize2 : Maximize2;
  return <div className="fc-conversation-work-controls" role="group" aria-label={t('Work area controls')}>
    <button type="button" className="fc-work-view-control" data-work-minimize aria-label={t('Minimize work')} title={t('Minimize work')}
      onPointerDown={layout.onBeforeViewChange} onClick={layout.onMinimize}>
      <Minus size={17} aria-hidden="true" /><span className="fc-work-control-label">{t('Minimize')}</span>
    </button>
    <button type="button" className="fc-work-view-control" data-work-expand aria-label={expandLabel} title={expandLabel}
      aria-pressed={layout.expanded} aria-controls={layout.workAreaId}
      onPointerDown={layout.onBeforeViewChange} onClick={layout.onToggleExpanded}>
      <ExpandIcon size={17} aria-hidden="true" /><span className="fc-work-control-label">{layout.expanded ? t('Split view') : t('Full screen')}</span>
    </button>
    <button type="button" className="fc-work-view-control" data-work-close aria-label={t(closeLabel)} title={t(closeLabel)} onClick={onClose}>
      <X size={18} aria-hidden="true" />
    </button>
  </div>;
}
