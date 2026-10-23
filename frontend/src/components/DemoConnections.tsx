import { useEffect, useId, useRef, useState, type Dispatch } from 'react';
import { ArrowRight, ArrowUpRight, Check, FileText, Link2, MessageSquare, RotateCcw, Undo2, X } from 'lucide-react';
import { demoChannelDocuments, demoChannelStacks, demoDocumentLinkLimit, demoStackReferenceLimit, type DemoAction, type DemoChannel, type DemoDocument, type DemoDocumentLink, type DemoState, type DemoStackUndo } from '../lib/demo-showroom';
import type { DemoConnectionSuggestion } from '../lib/demo-connections';
import '../styles/demo-connections.css';

type Translate = (source: string, values?: Record<string, string | number>) => string;
type Notice = { source: string; values?: Record<string, string | number> };

export interface DemoConnectionsProps {
  state: DemoState;
  channel: DemoChannel;
  suggestions: readonly DemoConnectionSuggestion[];
  dispatch: Dispatch<DemoAction>;
  t: Translate;
  onOpenDocument: (id: string, source: HTMLElement) => void;
  onRevealMessage: (id: string, source: HTMLElement) => void;
  compact?: boolean;
  documentId?: string;
  enabled: boolean;
  onEnabledChange: (value: boolean) => void;
}

/** Content-derived suggestions for the public sample. Every write is an explicit local action. */
export function DemoConnections({ state, channel, suggestions, dispatch, t, onOpenDocument, onRevealMessage, compact = false, documentId, enabled, onEnabledChange }: DemoConnectionsProps) {
  const uid = useId(), heading = useRef<HTMLHeadingElement>(null);
  const frames = useRef(new Set<number>()), currentChannel = useRef(channel);
  const currentUndo = state.stackUndo[channel], undoRef = useRef(currentUndo);
  const [recentUndo, setRecentUndo] = useState<DemoStackUndo | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [expanded, setExpanded] = useState(!compact);
  const [allLinks, setAllLinks] = useState(false);
  currentChannel.current = channel; undoRef.current = currentUndo;
  const documents = demoChannelDocuments(state, channel);
  const stacks = demoChannelStacks(state, channel);
  const links = state.documentLinks[channel];
  const dismissed = state.dismissedConnections[channel];
  const documentName = (document: DemoDocument) => document.sample ? t(document.title) : document.title;
  const documentFor = (id: string) => documents.find(document => document.id === id);
  const stackName = (id: string) => { const stack = stacks.find(row => row.id === id); return stack ? stack.sample ? t(stack.title) : stack.title : ''; };

  useEffect(() => {
    setRecentUndo(null); setNotice(null); setExpanded(!compact); setAllLinks(false);
  }, [channel, compact]);
  useEffect(() => () => { for (const frame of frames.current) cancelAnimationFrame(frame); }, []);

  function afterChange(previousUndo?: DemoStackUndo, captureUndo = false) {
    const sourceChannel = channel;
    const frame = requestAnimationFrame(() => {
      frames.current.delete(frame);
      if (currentChannel.current !== sourceChannel) return;
      if (captureUndo && undoRef.current && undoRef.current !== previousUndo) setRecentUndo(undoRef.current);
      if (heading.current?.getClientRects().length && !heading.current.closest('[inert]')) heading.current.focus({ preventScroll: true });
    });
    frames.current.add(frame);
  }

  function isCurrent(suggestion: DemoConnectionSuggestion) {
    if (suggestion.channel !== channel || dismissed.includes(suggestion.id)) return false;
    const document = documentFor(suggestion.documentId);
    if (!document) return false;
    if (documentId && suggestion.documentId !== documentId && (suggestion.kind !== 'document_link' || suggestion.relatedDocumentId !== documentId)) return false;
    if (suggestion.kind === 'document_link') {
      return suggestion.relatedDocumentId !== suggestion.documentId && !!documentFor(suggestion.relatedDocumentId) && links.length < demoDocumentLinkLimit && !links.some(link => (link.documentId === suggestion.documentId && link.relatedDocumentId === suggestion.relatedDocumentId) || (link.documentId === suggestion.relatedDocumentId && link.relatedDocumentId === suggestion.documentId));
    }
    const stack = stacks.find(row => row.id === suggestion.stackId);
    if (!stack) return false;
    if (suggestion.kind === 'document_stack') return document.stackId === suggestion.fromStackId && document.stackId !== stack.id;
    return document.stackId === suggestion.stackId && state.messages[channel].some(message => message.id === suggestion.messageId) && stack.references.length < demoStackReferenceLimit && !stack.references.includes(suggestion.messageId);
  }

  const available = suggestions.filter(isCurrent);
  const visible = available.slice(0, compact ? expanded ? 2 : 1 : 3);
  const activeDocument = documentId ? documentFor(documentId) : undefined;
  const savedLinks = links.flatMap<{ link: DemoDocumentLink; document: DemoDocument; fromDocument?: DemoDocument }>(link => {
    const source = documentFor(link.documentId), related = documentFor(link.relatedDocumentId);
    if (!source || !related) return [];
    if (!activeDocument) return [{ link, document: related, fromDocument: source }];
    const document = source.id === activeDocument.id ? related : related.id === activeDocument.id ? source : undefined;
    return document ? [{ link, document, fromDocument: undefined }] : [];
  });
  const visibleLinks = allLinks ? savedLinks : savedLinks.slice(0, 3);

  function approve(suggestion: DemoConnectionSuggestion) {
    if (!isCurrent(suggestion)) return;
    const previousUndo = currentUndo;
    if (suggestion.kind === 'message_stack') {
      dispatch({ type: 'stack_reference', channel, stackId: suggestion.stackId, messageId: suggestion.messageId });
      setNotice({ source: 'Reference kept with {stack}.', values: { stack: stackName(suggestion.stackId) } });
    } else if (suggestion.kind === 'document_stack') {
      dispatch({ type: 'document_move', channel, id: suggestion.documentId, stackId: suggestion.stackId });
      setNotice({ source: 'Document moved to {stack}.', values: { stack: stackName(suggestion.stackId) } });
    } else {
      dispatch({ type: 'document_link', channel, documentId: suggestion.documentId, relatedDocumentId: suggestion.relatedDocumentId });
      setNotice({ source: 'Documents linked. The originals stay in place.' });
    }
    afterChange(previousUndo, true);
  }

  function reason(suggestion: DemoConnectionSuggestion) {
    const words = suggestion.reason.words.slice(0, 3).map(word => word.slice(0, 100));
    if (suggestion.reason.kind === 'document_title') return t('Mentions “{title}”.', { title: words[0] ?? documentName(documentFor(suggestion.documentId)!) });
    if (suggestion.reason.kind === 'stack_title') return t('Matches the stack title “{title}”.', { title: words[0] ?? (suggestion.kind === 'document_link' ? '' : stackName(suggestion.stackId)) });
    return t('Both mention {words}.', { words: words.join(', ') });
  }

  return <section className={'fc-demo-connections' + (compact ? ' fc-demo-connections-compact' : '')} aria-labelledby={uid + '-title'}>
    <header className="fc-demo-connections-heading"><div><span className="fc-demo-connections-kicker">{t('A little less sorting')}</span><h4 ref={heading} tabIndex={-1} id={uid + '-title'}>{t('Suggested connections')}{enabled && available.length > 0 && <span>{available.length}</span>}</h4></div><button type="button" className="fc-demo-connections-switch" role="switch" aria-checked={enabled} aria-label={t('Suggest connections')} onClick={() => { onEnabledChange(!enabled); setNotice(null); }}><span aria-hidden="true" className="fc-demo-connections-switch-track"><span /></span><span>{t(enabled ? 'On' : 'Off')}</span></button></header>
    <p className="fc-demo-connections-note">{enabled ? t('Suggestions use the words and titles in this channel. You choose what to connect.') : t('Suggestions are paused in this channel.')}</p>
    {enabled && visible.length > 0 ? <ul className="fc-demo-connections-list">{visible.map(suggestion => {
      const document = documentFor(suggestion.documentId)!;
      const message = suggestion.kind === 'message_stack' ? state.messages[channel].find(row => row.id === suggestion.messageId)! : undefined;
      const related = suggestion.kind === 'document_link' ? documentFor(suggestion.relatedDocumentId)! : undefined;
      const source = message ? message.sample ? t(message.text) : message.text : documentName(document);
      const target = suggestion.kind === 'document_link' ? documentName(related!) : stackName(suggestion.stackId);
      const sourceLabel = message ? t('A message from {name}', { name: message.author === 'You' ? t('You') : message.author }) : documentName(document);
      const actionLabel = suggestion.kind === 'message_stack' ? t('Save reference in {stack}', { stack: target }) : suggestion.kind === 'document_stack' ? t('Move {document} to {stack}', { document: sourceLabel, stack: target }) : t('Link {document} with {related}', { document: sourceLabel, related: target });
      return <li className="fc-demo-connection" key={suggestion.id} data-connection-kind={suggestion.kind}>
        <div className="fc-demo-connection-source"><span className="fc-demo-connection-icon" aria-hidden="true">{message ? <MessageSquare size={16} /> : <FileText size={16} />}</span><div><span>{message ? sourceLabel : t('Document')}</span><p>{source}</p></div><button type="button" className="fc-demo-connection-dismiss" aria-label={t('Dismiss suggestion linking {source} to {target}', { source: sourceLabel, target })} onClick={() => { dispatch({ type: 'connection_dismiss', channel, id: suggestion.id }); setNotice({ source: 'Suggestion dismissed.' }); afterChange(); }}><X size={15} aria-hidden="true" /></button></div>
        <div className="fc-demo-connection-target"><ArrowRight size={13} aria-hidden="true" /><span>{t(related ? 'Related document' : 'Suggested stack')}</span><strong>{target}</strong></div>
        <p className="fc-demo-connection-reason">{reason(suggestion)}</p>
        <div className="fc-demo-connection-actions"><button type="button" className="fc-demo-connection-inspect" onClick={event => { if (message) onRevealMessage(message.id, event.currentTarget); else onOpenDocument(document.id, event.currentTarget); }}>{t(message ? 'View message' : 'Read document')}<ArrowUpRight size={13} aria-hidden="true" /></button><button type="button" className="fc-demo-connection-confirm" aria-label={actionLabel} onClick={() => approve(suggestion)}>{suggestion.kind === 'document_link' ? <Link2 size={14} aria-hidden="true" /> : <Check size={14} aria-hidden="true" />}{t(suggestion.kind === 'message_stack' ? 'Save reference' : suggestion.kind === 'document_stack' ? 'Move document' : 'Link documents')}</button></div>
      </li>;
    })}</ul> : enabled && <p className="fc-demo-connections-empty">{t(documentId ? 'Nothing new to suggest for this document.' : 'No clear connections yet. Keep the conversation going.')}</p>}
    {enabled && compact && available.length > 1 && <button type="button" className="fc-demo-connections-more" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{t(expanded ? 'Show less' : 'Show another suggestion')}</button>}
    {savedLinks.length > 0 && <div className="fc-demo-connections-saved"><h5><Link2 size={14} aria-hidden="true" />{t('Linked documents')}</h5><ul>{visibleLinks.map(({ link, document, fromDocument }) => <li key={link.id}><div className="fc-demo-connections-linked-pair">{fromDocument && <><button type="button" className="fc-demo-connections-linked-document" onClick={event => onOpenDocument(fromDocument.id, event.currentTarget)}><FileText size={15} aria-hidden="true" /><span>{documentName(fromDocument)}</span></button><Link2 className="fc-demo-connections-pair-mark" size={13} aria-hidden="true" /></>}<button type="button" className="fc-demo-connections-linked-document" onClick={event => onOpenDocument(document.id, event.currentTarget)}><FileText size={15} aria-hidden="true" /><span>{documentName(document)}</span><ArrowUpRight size={13} aria-hidden="true" /></button></div><button type="button" className="fc-demo-connections-unlink" aria-label={fromDocument ? t('Unlink {document} from {related}', { document: documentName(fromDocument), related: documentName(document) }) : t('Unlink {document}', { document: documentName(document) })} onClick={() => { const previousUndo = currentUndo; dispatch({ type: 'document_unlink', channel, id: link.id }); setNotice({ source: 'Document connection removed.' }); afterChange(previousUndo, true); }}><X size={15} aria-hidden="true" /></button></li>)}</ul>{savedLinks.length > 3 && <button type="button" className="fc-demo-connections-more" aria-expanded={allLinks} onClick={() => setAllLinks(!allLinks)}>{allLinks ? t('Show fewer linked documents') : t('Show all linked documents ({count})', { count: savedLinks.length })}</button>}<p>{t('Linked here. Each document keeps its place in its own stack.')}</p></div>}
    <footer className="fc-demo-connections-footer">{recentUndo && recentUndo === currentUndo && <button type="button" className="fc-demo-connections-undo" onClick={() => { dispatch({ type: 'stack_undo', channel }); setRecentUndo(null); setNotice({ source: 'Last connection change undone.' }); afterChange(); }}><Undo2 size={14} aria-hidden="true" />{t('Undo connection')}</button>}{dismissed.length > 0 && <button type="button" className="fc-demo-connections-restore" onClick={() => { dispatch({ type: 'connection_restore_dismissed', channel }); setNotice({ source: 'Dismissed suggestions restored.' }); afterChange(); }}><RotateCcw size={13} aria-hidden="true" />{t('Restore dismissed ({count})', { count: dismissed.length })}</button>}</footer>
    <p className="fc-demo-connections-status" role="status" aria-live="polite">{notice ? t(notice.source, notice.values) : ''}</p>
  </section>;
}
