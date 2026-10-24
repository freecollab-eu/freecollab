import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type KeyboardEventHandler } from 'react';
import { ArrowDown, ArrowUp, CornerDownLeft, Search, X } from 'lucide-react';
import { t, useI18n } from '../i18n';
import type { SlashCommand } from '../lib/slash-commands';
import '../styles/slash-commands.css';

export interface SlashCommandsProps {
  id: string;
  commands: SlashCommand[];
  activeIndex: number;
  onActiveIndex: (index: number) => void;
  onSelect: (command: SlashCommand) => void;
  onClose: () => void;
  query: string;
  /** Help for the typed command; omitted when browsing all features. */
  usage?: string;
  argumentPreview?: string;
  error?: string;
  /** The explicit launcher searches here without replacing an existing draft. */
  onQueryChange?: (query: string) => void;
  onKeyDown?: KeyboardEventHandler<HTMLInputElement>;
}

const GROUP_LABELS = {
  conversation: 'Features in this conversation',
  personal: 'Your own space',
  workspace: 'Your workspace',
} as const;

/** A local suggestion list. Focus stays in the composer (or its explicit search). */
export function SlashCommands({ id, commands, activeIndex, onActiveIndex, onSelect, onClose, query, usage, argumentPreview, error, onQueryChange, onKeyDown }: SlashCommandsProps) {
  useI18n();
  const panelRef = useRef<HTMLElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const [availableHeight, setAvailableHeight] = useState<number>();
  const currentIndex = commands.length ? Math.min(Math.max(activeIndex, 0), commands.length - 1) : -1;
  const activeCommand = commands[currentIndex];
  const titleId = id + '-title';
  const helpId = id + '-help';
  const commandHelpId = id + '-command-help';
  const hasCommandHelp = Boolean(usage || argumentPreview || error);

  useLayoutEffect(() => {
    const panel = panelRef.current;
    const anchor = panel?.parentElement;
    if (!panel || !anchor) return;
    const measure = () => {
      const viewport = window.visualViewport;
      const available = Math.floor(anchor.getBoundingClientRect().top - (viewport?.offsetTop || 0) - 12);
      setAvailableHeight(Math.max(0, available));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(anchor);
    window.addEventListener('resize', measure);
    window.visualViewport?.addEventListener('resize', measure);
    window.visualViewport?.addEventListener('scroll', measure);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', measure);
      window.visualViewport?.removeEventListener('resize', measure);
      window.visualViewport?.removeEventListener('scroll', measure);
    };
  }, []);

  useEffect(() => {
    const list = listRef.current;
    const option = activeCommand && document.getElementById(id + '-' + activeCommand.id);
    if (!list || !option || !list.contains(option)) return;
    // Scroll this list only: scrollIntoView can also move the conversation.
    const viewport = list.getBoundingClientRect();
    const bounds = option.getBoundingClientRect();
    if (bounds.top < viewport.top) list.scrollTop -= viewport.top - bounds.top;
    else if (bounds.bottom > viewport.bottom) list.scrollTop += bounds.bottom - viewport.bottom;
  }, [activeCommand?.id, id, availableHeight]);

  const groups: { key: string; commands: { command: SlashCommand; index: number }[] }[] = [];
  commands.forEach((command, index) => {
    const key = query.trim() ? 'results' : command.group;
    const previous = groups[groups.length - 1];
    if (previous?.key === key) previous.commands.push({ command, index });
    else groups.push({ key, commands: [{ command, index }] });
  });

  return <section ref={panelRef} className={`fc-slash-commands${availableHeight !== undefined && availableHeight < 220 ? ' is-compact' : ''}`}
    aria-labelledby={titleId}
    aria-describedby={hasCommandHelp ? commandHelpId : undefined}
    style={availableHeight === undefined ? undefined : { '--fc-slash-available-height': availableHeight + 'px' } as CSSProperties}
    onKeyDown={event => {
      if (event.key === 'Escape' && !event.nativeEvent.isComposing) {
        event.preventDefault(); event.stopPropagation(); onClose();
      }
    }}>
    <header className="fc-slash-commands-header">
      <div><span className="fc-slash-commands-mark" aria-hidden="true">/</span><h2 id={titleId}>{t('Everything you can do here')}</h2></div>
      <button type="button" aria-label={t('Close feature list')} onClick={onClose}><X size={17} aria-hidden="true" /></button>
    </header>
    {hasCommandHelp && <div id={commandHelpId} className="fc-slash-commands-argument-help">
      {usage && <p className="fc-slash-commands-usage"><span>{t('Command format')}</span><code>{usage}</code></p>}
      {argumentPreview && <p className="fc-slash-commands-preview"><span>{t('Draft preview')}</span><span>{argumentPreview}</span></p>}
      {error && <p className="fc-slash-commands-error" role="status" aria-live="polite" aria-atomic="true">{error}</p>}
    </div>}
    {onQueryChange && <div className="fc-slash-commands-search">
      <Search size={16} aria-hidden="true" />
      <input autoFocus type="text" role="combobox" autoComplete="off" spellCheck={false} maxLength={128}
        aria-label={t('Search features')} placeholder={t('Search features')} value={query}
        aria-autocomplete="list" aria-expanded="true" aria-controls={id} aria-describedby={hasCommandHelp ? `${commandHelpId} ${helpId}` : helpId}
        aria-activedescendant={activeCommand ? id + '-' + activeCommand.id : undefined}
        onChange={event => onQueryChange(event.target.value)} onKeyDown={onKeyDown} />
    </div>}
    <div ref={listRef} id={id} className="fc-slash-commands-list" role="listbox" aria-labelledby={titleId}>
      {groups.map((group, groupIndex) => <div key={group.key + '-' + groupIndex} role="group" aria-label={group.key === 'results' ? t('Everything you can do here') : t(GROUP_LABELS[group.key as keyof typeof GROUP_LABELS])}>
        {group.key !== 'results' && <p className="fc-slash-commands-group" aria-hidden="true">{t(GROUP_LABELS[group.key as keyof typeof GROUP_LABELS])}</p>}
        {/* Keyboard scrolling can move rows under a stationary pointer.
            Only actual mouse movement should change the hover selection. */}
        {group.commands.map(({ command, index }) => <div key={command.id} id={id + '-' + command.id} role="option"
          aria-selected={index === currentIndex} tabIndex={-1} className={`fc-slash-command${index === currentIndex ? ' is-active' : ''}`}
          onMouseDown={event => event.preventDefault()} onMouseMove={() => { if (index !== currentIndex) onActiveIndex(index); }} onClick={() => onSelect(command)}>
          <span className="fc-slash-command-copy"><strong>{command.label}</strong><span>{command.description}</span></span>
          <code>/{command.command}</code>
        </div>)}
      </div>)}
    </div>
    {!commands.length && <div className="fc-slash-commands-empty" role="status"><strong>{t('No matching features')}</strong><p>{t('Try another word, or remove the search to see everything.')}</p></div>}
    <footer className="fc-slash-commands-footer">
      <span id={helpId} className="fc-slash-commands-key-help"><ArrowUp size={11} aria-hidden="true" /><ArrowDown size={11} aria-hidden="true" />{t('Navigate')}<CornerDownLeft size={12} aria-hidden="true" />{t('Open')}<kbd>Esc</kbd>{t('Close')}</span>
      <span className="fc-slash-commands-count" role="status" aria-live="polite" aria-atomic="true">{t('{count} available features', { count: commands.length })}</span>
    </footer>
  </section>;
}
