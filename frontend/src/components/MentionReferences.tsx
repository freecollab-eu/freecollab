import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from 'react';
import { ArrowDown, ArrowUp, AtSign, CheckSquare, CornerDownLeft, FileText, Hash, Loader2, Paperclip, UserRound, Users, X } from 'lucide-react';
import { t, useI18n } from '../i18n';
import { referenceOptionId, type ReferenceCandidate, type ReferenceKind } from '../lib/message-references';
import '../styles/slash-commands.css';
import '../styles/message-references.css';

export interface MentionReferencesProps {
  id: string; candidates: ReferenceCandidate[]; activeIndex: number;
  onActiveIndex: (index: number) => void; onSelect: (candidate: ReferenceCandidate) => void;
  onClose: () => void; query: string; loading?: boolean; error?: string; onManageGroups?: () => void;
}
export const REFERENCE_LABELS: Record<ReferenceKind, string> = {
  person: 'People', group: 'Groups', document: 'Documents', task: 'Tasks', file: 'Files', channel: 'Channels',
};
const icons = { person: UserRound, group: Users, document: FileText, task: CheckSquare, file: Paperclip, channel: Hash };

/** Suggestions stay attached to the active composer and never take its focus. */
export function MentionReferences({ id, candidates, activeIndex, onActiveIndex, onSelect, onClose, query, loading = false, error = '', onManageGroups }: MentionReferencesProps) {
  useI18n();
  const panel = useRef<HTMLElement>(null), list = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState<number>();
  const selected = candidates.length ? Math.min(Math.max(activeIndex, 0), candidates.length - 1) : -1;
  const current = candidates[selected];
  useLayoutEffect(() => {
    const anchor = panel.current?.parentElement;
    if (!anchor) return;
    const measure = () => setHeight(Math.max(0, Math.floor(anchor.getBoundingClientRect().top - (window.visualViewport?.offsetTop || 0) - 12)));
    measure();
    const observer = new ResizeObserver(measure); observer.observe(anchor);
    window.addEventListener('resize', measure); window.visualViewport?.addEventListener('resize', measure); window.visualViewport?.addEventListener('scroll', measure);
    return () => { observer.disconnect(); window.removeEventListener('resize', measure); window.visualViewport?.removeEventListener('resize', measure); window.visualViewport?.removeEventListener('scroll', measure); };
  }, []);
  useEffect(() => {
    const viewport = list.current, option = current && document.getElementById(referenceOptionId(id, current));
    if (!viewport || !option || !viewport.contains(option)) return;
    const frame = viewport.getBoundingClientRect(), bounds = option.getBoundingClientRect();
    if (bounds.top < frame.top) viewport.scrollTop -= frame.top - bounds.top;
    else if (bounds.bottom > frame.bottom) viewport.scrollTop += bounds.bottom - frame.bottom;
  }, [current?.kind, current?.id, id, height]);
  const grouped: { kind: ReferenceKind; rows: { candidate: ReferenceCandidate; index: number }[] }[] = [];
  candidates.forEach((candidate, index) => {
    const previous = grouped[grouped.length - 1];
    if (previous?.kind === candidate.kind) previous.rows.push({ candidate, index });
    else grouped.push({ kind: candidate.kind, rows: [{ candidate, index }] });
  });
  return <section ref={panel} className={`fc-slash-commands fc-mention-references${height !== undefined && height < 220 ? ' is-compact' : ''}`}
    aria-labelledby={id + '-title'} style={height === undefined ? undefined : { '--fc-slash-available-height': height + 'px' } as CSSProperties}
    onKeyDown={event => { if (event.key === 'Escape' && !event.nativeEvent.isComposing) { event.preventDefault(); event.stopPropagation(); onClose(); } }}>
    <header className="fc-slash-commands-header"><div><AtSign size={20} aria-hidden="true" /><h2 id={id + '-title'}>{t('Mention or reference')}</h2></div><button type="button" aria-label={t('Close reference list')} onClick={onClose}><X size={17} aria-hidden="true" /></button></header>
    <div ref={list} id={id} className="fc-slash-commands-list" role="listbox" aria-labelledby={id + '-title'} aria-busy={loading}>
      {grouped.map((group, groupIndex) => <div key={group.kind + '-' + groupIndex} role="group" aria-label={t(REFERENCE_LABELS[group.kind])}>
        <p className="fc-slash-commands-group" aria-hidden="true">{t(REFERENCE_LABELS[group.kind])}</p>
        {/* Keyboard scrolling must not select rows under a stationary pointer. */}
        {group.rows.map(({ candidate, index }) => {
          const Icon = icons[candidate.kind];
          const duplicate = candidate.kind === 'person' && candidates.some(item => item.kind === 'person' && item.id !== candidate.id && item.label === candidate.label);
          return <div role="option" key={candidate.kind + ':' + candidate.id} id={referenceOptionId(id, candidate)} tabIndex={-1}
            aria-selected={index === selected} className={`fc-slash-command fc-reference-candidate${index === selected ? ' is-active' : ''}`}
            data-reference-kind={candidate.kind} data-reference-id={candidate.id}
            onMouseDown={event => event.preventDefault()} onMouseMove={() => { if (index !== selected) onActiveIndex(index); }} onClick={() => onSelect(candidate)}>
            <Icon size={17} aria-hidden="true" /><span className="fc-slash-command-copy"><strong>{candidate.label}</strong><span>{candidate.description || t(REFERENCE_LABELS[candidate.kind])}{candidate.kind === 'group' && candidate.member_count !== undefined && <> · {t('{count} current members', { count: candidate.member_count })}</>}{duplicate && <> · {t('Account {id}', { id: candidate.id.slice(-8) })}</>}</span></span>
          </div>;
        })}
      </div>)}
    </div>
    {loading && <p className="fc-reference-list-status" role="status"><Loader2 size={14} className="fc-account-spinner" aria-hidden="true" />{t('Finding people and shared work…')}</p>}
    {!!error && <p className="fc-reference-list-status fc-reference-list-error" role="alert">{t(error)}</p>}
    {!candidates.length && !loading && !error && <div className="fc-slash-commands-empty" role="status"><strong>{t('No matching references')}</strong><p>{query.trim() ? t('Try a name, group, document, task or file in this conversation.') : t('People and shared work will appear here when they are available.')}</p></div>}
    <footer className="fc-slash-commands-footer"><span className="fc-slash-commands-key-help"><ArrowUp size={11} aria-hidden="true" /><ArrowDown size={11} aria-hidden="true" />{t('Navigate')}<CornerDownLeft size={12} aria-hidden="true" />{t('Choose')}<kbd>Esc</kbd>{t('Close')}</span>{onManageGroups && <button type="button" className="fc-reference-manage-groups" onClick={onManageGroups}><Users size={14} aria-hidden="true" />{t('Manage groups')}</button>}<span role="status" aria-live="polite">{t('{count} references', { count: candidates.length })}</span></footer>
  </section>;
}
