import { t, useI18n, currentLocale } from '../i18n';
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { createPortal } from 'react-dom';
import { ArrowDown, ArrowRight, ArrowUp, CornerDownLeft, FileText, Hash, MessageSquare, Palette, Plus, Search, SearchX, Users, X } from 'lucide-react';
import {
  nextCommandIndex, searchCommands,
  type CommandAction, type CommandChannel, type CommandMessage, type CommandResult,
} from '../lib/commands';
import { useModalFocus } from '../lib/useModalFocus';
import '../styles/command.css';

export type { CommandAction } from '../lib/commands';

export interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
  channels: CommandChannel[];
  activeChannelId: string | null;
  messages: CommandMessage[];
  onChannel: (id: string) => void;
  onMessage: (id: string) => void;
  onAction: (action: CommandAction) => void;
  hasActiveChannel: boolean;
}

const ACTION_ICONS = { create: Plus, files: FileText, members: Users, appearance: Palette };

function MessageTime({ value }: { value: string }) {
  useI18n();
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return null;
  return <time dateTime={value}>{new Intl.DateTimeFormat(currentLocale(), { month: 'short', day: 'numeric' }).format(date)}</time>;
}

export function CommandPalette(props: CommandPaletteProps) {
  useI18n();
  return props.open ? <OpenCommandPalette {...props} /> : null;
}

function OpenCommandPalette({ onClose, channels, activeChannelId, messages, onChannel, onMessage, onAction, hasActiveChannel }: CommandPaletteProps) {
  useI18n();
  const [query, setQuery] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);
  const panelRef = useRef<HTMLElement>(null);
  const prefix = useId();
  const titleId = prefix + '-title';
  const listId = prefix + '-results';
  const scopeId = prefix + '-scope';
  const results = useMemo(() => searchCommands({ query, channels, activeChannelId, messages, hasActiveChannel }), [query, channels, activeChannelId, messages, hasActiveChannel]);
  const currentIndex = results.length ? Math.min(activeIndex, results.length - 1) : -1;
  const activeResult = results[currentIndex];
  useModalFocus(panelRef, onClose);

  useEffect(() => {
    if (activeResult) document.getElementById(prefix + '-' + activeResult.id)?.scrollIntoView({ block: 'nearest' });
  }, [activeResult, prefix]);

  function select(result: CommandResult) {
    onClose();
    if (result.kind === 'channel') onChannel(result.channelId);
    else if (result.kind === 'message') onMessage(result.messageId);
    else onAction(result.action);
  }

  function handleSearchKey(event: KeyboardEvent<HTMLInputElement>) {
    if (event.nativeEvent.isComposing) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      setActiveIndex(nextCommandIndex(currentIndex, event.key === 'ArrowDown' ? 1 : -1, results.length));
    } else if (event.key === 'Enter' && activeResult) {
      event.preventDefault(); select(activeResult);
    }
  }

  const groups = [
    { kind: 'channel', label: t("Channels") },
    { kind: 'action', label: t("Actions") },
    { kind: 'message', label: t("In this channel · loaded messages") },
  ] as const;

  return createPortal(
    <div className="fc-command-overlay" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="fc-command-panel" ref={panelRef} role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1}>
        <header className="fc-command-header">
          <div><span className="fc-kicker">{t("A shorter way there")}</span><h2 id={titleId}>{t("Quick switcher")}</h2></div>
          <button className="fc-icon-button fc-command-close" type="button" aria-label={t("Close quick switcher")} onClick={onClose}><X size={18} aria-hidden="true" /></button>
        </header>
        <div className="fc-command-search">
          <Search size={20} aria-hidden="true" />
          <input data-autofocus type="text" role="combobox" value={query} maxLength={256} autoComplete="off" spellCheck={false}
            placeholder={t("Search channels, messages or actions…")} aria-label={t("Search channels, messages or actions")}
            aria-autocomplete="list" aria-expanded="true" aria-controls={listId} aria-describedby={scopeId}
            aria-activedescendant={activeResult ? prefix + '-' + activeResult.id : undefined}
            onChange={event => { setQuery(event.target.value); setActiveIndex(0); }} onKeyDown={handleSearchKey} />
        </div>
        <p className="fc-command-scope" id={scopeId}>{hasActiveChannel ? t("Your channels, useful actions and messages loaded in this channel.") : t("Your channels and useful actions, all in one place.")}</p>
        <div className="fc-command-results" role="listbox" id={listId} aria-label={t("Quick switcher results")}>
          {groups.map(group => {
            const grouped = results.filter(result => result.kind === group.kind);
            if (!grouped.length) return null;
            return <div className="fc-command-group" role="group" aria-label={group.label} key={group.kind}>
              <p className="fc-command-group-label" aria-hidden="true">{group.label}<span>{grouped.length}</span></p>
              {grouped.map(result => {
                const selected = result.id === activeResult?.id;
                const Icon = result.kind === 'channel' ? Hash : result.kind === 'message' ? MessageSquare : ACTION_ICONS[result.action];
                return <div key={result.id} id={prefix + '-' + result.id} role="option" aria-selected={selected}
                  className={`fc-command-result${selected ? ' is-selected' : ''}`} tabIndex={-1}
                  onMouseDown={event => event.preventDefault()} onMouseEnter={() => setActiveIndex(results.findIndex(item => item.id === result.id))}
                  onClick={() => select(result)}>
                  <span className="fc-command-result-icon"><Icon size={18} aria-hidden="true" /></span>
                  <span className="fc-command-result-copy"><strong>{result.label}</strong><span>{result.description}{result.kind === 'message' && <MessageTime value={result.createdAt} />}</span></span>
                  {result.kind === 'channel' && result.current && <span className="fc-command-current" aria-label={t("Current channel")} />}
                  <ArrowRight size={15} className="fc-command-result-arrow" aria-hidden="true" />
                </div>;
              })}
            </div>;
          })}
          {!results.length && <div className="fc-command-empty" role="status"><SearchX size={26} aria-hidden="true" /><h3>{t("No matches found.")}</h3><p>{hasActiveChannel ? t("Try a channel name, an action or words from a message loaded in this channel.") : t("Try a channel name, or search for an action such as Appearance.")}</p></div>}
        </div>
        <footer className="fc-command-footer">
          <span className="fc-command-key-help"><kbd><ArrowUp size={11} /><ArrowDown size={11} /></kbd>{t("Navigate ")}<kbd><CornerDownLeft size={12} /></kbd>{t("Open")}</span>
          <span role="status" aria-live="polite" aria-atomic="true">{results.length} {results.length === 1 ? t("result") : t("results")}</span>
        </footer>
      </section>
    </div>, document.body,
  );
}
