import { useEffect, useId, useRef, useState, type Dispatch, type DragEvent, type FormEvent, type ReactNode } from 'react';
import { ArrowUpRight, Check, ChevronDown, FileText, GripVertical, Layers, List, MessageSquare, Plus, Undo2, X } from 'lucide-react';
import { demoChannelDocuments, demoChannelStacks, demoDocumentLimit, demoStackDocuments, demoStackLimit, demoStackReferenceLimit, demoStackReferences, type DemoAction, type DemoChannel, type DemoDocument, type DemoDocumentStack, type DemoMessage, type DemoState } from '../lib/demo-showroom';
import '../styles/demo-document-desk.css';

/** Only these local public-demo payloads are accepted. No external files or URLs. */
export const DEMO_DOCUMENT_DRAG_TYPE = 'application/x-freecollab-demo-document';
export const DEMO_MESSAGE_DRAG_TYPE = 'application/x-freecollab-demo-message';

export interface DemoDocumentDeskProps {
  state: DemoState;
  channel: DemoChannel;
  dispatch: Dispatch<DemoAction>;
  t: (source: string, values?: Record<string, string | number>) => string;
  onOpenDocument: (id: string, source: HTMLElement) => void;
  onRevealMessage: (id: string, source: HTMLElement) => void;
  pendingReference?: DemoMessage | null;
  onReferenceSaved?: () => void;
  onCancelReference?: () => void;
  compact?: boolean;
  renderAssist?: (scope: { kind: 'stack' | 'document'; id: string }) => ReactNode;
}

type NewItem = { kind: 'document' | 'stack'; stackId: string };

/** Paper stacks for the public sample. All changes live in the parent's memory. */
export function DemoDocumentDesk({ state, channel, dispatch, t, onOpenDocument, onRevealMessage, pendingReference, onReferenceSaved, onCancelReference, compact = false, renderAssist }: DemoDocumentDeskProps) {
  const uid = useId();
  const [view, setView] = useState<'stacks' | 'list'>('stacks');
  const [expanded, setExpanded] = useState<string | null>(null);
  const [newItem, setNewItem] = useState<NewItem | null>(null);
  const [newTitle, setNewTitle] = useState('');
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameTitle, setRenameTitle] = useState('');
  const [formError, setFormError] = useState('');
  const [status, setStatus] = useState('');
  const [dropTarget, setDropTarget] = useState<string | null>(null);
  const [referenceTarget, setReferenceTarget] = useState('');
  const newButton = useRef<HTMLButtonElement>(null);
  const undoButton = useRef<HTMLButtonElement>(null);
  const stackButtons = useRef<Record<string, HTMLButtonElement | null>>({});
  const renameButtons = useRef<Record<string, HTMLButtonElement | null>>({});
  const moveSelects = useRef<Record<string, HTMLSelectElement | null>>({});
  const stacks = demoChannelStacks(state, channel);
  const documents = demoChannelDocuments(state, channel);
  const validReference = pendingReference && state.messages[channel].find(message => message.id === pendingReference.id);
  const expandedStack = stacks.find(stack => stack.id === expanded);
  const stackTitle = (stack: DemoDocumentStack) => stack.sample ? t(stack.title) : stack.title;
  const documentTitle = (document: DemoDocument) => document.sample ? t(document.title) : document.title;
  const messageText = (message: DemoMessage) => message.sample ? t(message.text) : message.text;
  const ownerName = (owner: string) => owner === 'You' ? t('You') : owner;
  const documentCount = (count: number) => count === 1 ? t('1 document') : t('{count} documents', { count });
  const referenceCount = (count: number) => count === 1 ? t('1 message reference') : t('{count} message references', { count });

  useEffect(() => {
    setExpanded(null); setNewItem(null); setRenaming(null); setNewTitle(''); setFormError(''); setStatus(''); setDropTarget(null);
    setReferenceTarget('');
  }, [channel]);

  const chosenReferenceStack = stacks.find(stack => stack.id === referenceTarget) ?? stacks[0];
  const referenceAlreadySaved = !!validReference && !!chosenReferenceStack?.references.includes(validReference.id);
  const referenceAtLimit = !!chosenReferenceStack && chosenReferenceStack.references.length >= demoStackReferenceLimit;

  function beginCreate(kind: NewItem['kind'], stackId = expandedStack?.id ?? stacks[0]?.id ?? '') {
    setNewItem({ kind, stackId }); setNewTitle(''); setFormError(''); setRenaming(null);
  }

  function finishCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!newItem) return;
    const title = newTitle.trim();
    if (!title) { setFormError(t('Give it a name first.')); return; }
    const titleLimit = newItem.kind === 'document' ? 200 : 80;
    if (title.length > titleLimit) { setFormError(t('Use {count} characters or fewer.', { count: titleLimit })); return; }
    if (newItem.kind === 'stack') {
      if (stacks.some(stack => stack.title.toLowerCase() === title.toLowerCase() || stackTitle(stack).toLowerCase() === title.toLowerCase())) { setFormError(t('There is already a stack with that name.')); return; }
      if (stacks.length >= demoStackLimit) { setFormError(t('This demo has room for {count} stacks per channel.', { count: demoStackLimit })); return; }
      const id = 'stack-' + state.nextId;
      dispatch({ type: 'stack_add', channel, title });
      setExpanded(id); setView('stacks'); setStatus(t('Stack created. Add a document or bring a message here.'));
      setNewItem(null);
      requestAnimationFrame(() => stackButtons.current[id]?.focus({ preventScroll: true }));
      return;
    }
    if (!stacks.some(stack => stack.id === newItem.stackId) || documents.length >= demoDocumentLimit) return;
    const id = 'document-' + state.nextId;
    const source = newButton.current;
    dispatch({ type: 'document_add', channel, stackId: newItem.stackId, title });
    setExpanded(newItem.stackId); setNewItem(null); setStatus(t('Document created in this demo.'));
    if (source) onOpenDocument(id, source);
  }

  function moveDocument(document: DemoDocument, stackId: string, keepKeyboardFocus = false) {
    const stack = stacks.find(row => row.id === stackId);
    if (!stack || document.stackId === stackId) return;
    dispatch({ type: 'document_move', channel, id: document.id, stackId });
    setExpanded(stackId);
    setStatus(t('Moved {document} to {stack}.', { document: documentTitle(document), stack: stackTitle(stack) }));
    if (keepKeyboardFocus) requestAnimationFrame(() => moveSelects.current[document.id]?.focus({ preventScroll: true }));
  }

  function saveReference(stackId: string, messageId: string, restoreKeyboardFocus = false) {
    const stack = stacks.find(row => row.id === stackId);
    if (!stack || !state.messages[channel].some(message => message.id === messageId)) return false;
    if (stack.references.includes(messageId)) { setStatus(t('This message is already in that stack.')); return false; }
    if (stack.references.length >= demoStackReferenceLimit) { setStatus(t('This stack has reached the demo reference limit.')); return false; }
    dispatch({ type: 'stack_reference', channel, stackId, messageId });
    setExpanded(stackId); setStatus(t('Message saved as a reference. The original stays in the conversation.'));
    if (pendingReference?.id === messageId) onReferenceSaved?.();
    if (restoreKeyboardFocus) requestAnimationFrame(() => (view === 'stacks' ? stackButtons.current[stackId] : renameButtons.current[stackId])?.focus({ preventScroll: true }));
    return true;
  }

  function acceptDrag(event: DragEvent<HTMLElement>, stackId: string) {
    if (!Array.from(event.dataTransfer.types).some(type => type === DEMO_DOCUMENT_DRAG_TYPE || type === DEMO_MESSAGE_DRAG_TYPE)) return;
    event.preventDefault(); event.dataTransfer.dropEffect = Array.from(event.dataTransfer.types).includes(DEMO_MESSAGE_DRAG_TYPE) ? 'copy' : 'move'; setDropTarget(stackId);
  }

  function receiveDrop(event: DragEvent<HTMLElement>, stackId: string) {
    const documentData = event.dataTransfer.getData(DEMO_DOCUMENT_DRAG_TYPE);
    const messageData = event.dataTransfer.getData(DEMO_MESSAGE_DRAG_TYPE);
    if (!documentData && !messageData) return;
    event.preventDefault(); event.stopPropagation(); setDropTarget(null);
    try {
      const data: unknown = JSON.parse((documentData || messageData).slice(0, 1000));
      if (!data || typeof data !== 'object' || !('channel' in data) || !('id' in data) || data.channel !== channel || typeof data.id !== 'string') throw new Error('Invalid demo origin');
      if (documentData) {
        const document = documents.find(row => row.id === data.id);
        if (!document) throw new Error('Invalid document');
        moveDocument(document, stackId);
      } else if (!state.messages[channel].some(message => message.id === data.id)) throw new Error('Invalid message');
      else saveReference(stackId, data.id);
    } catch {
      setStatus(t('Choose a document or message from this channel.'));
    }
  }

  function finishRename(event: FormEvent<HTMLFormElement>, stack: DemoDocumentStack) {
    event.preventDefault();
    const title = renameTitle.trim();
    if (!title) { setFormError(t('Give it a name first.')); return; }
    if (stacks.some(row => row.id !== stack.id && (row.title.toLowerCase() === title.toLowerCase() || stackTitle(row).toLowerCase() === title.toLowerCase()))) { setFormError(t('There is already a stack with that name.')); return; }
    if (title !== stack.title) dispatch({ type: 'stack_rename', channel, id: stack.id, title });
    setRenaming(null); setFormError(''); setStatus(t('Stack renamed in this demo.'));
    requestAnimationFrame(() => renameButtons.current[stack.id]?.focus({ preventScroll: true }));
  }

  function renderDocument(document: DemoDocument) {
    return <li key={document.id} className="fc-demo-desk-document" draggable onDragStart={event => {
      event.dataTransfer.setData(DEMO_DOCUMENT_DRAG_TYPE, JSON.stringify({ channel, id: document.id })); event.dataTransfer.effectAllowed = 'move';
    }} onDragEnd={() => setDropTarget(null)}>
      <span className="fc-demo-desk-grip" aria-hidden="true"><GripVertical size={14} /></span>
      <div className="fc-demo-desk-document-name">
        <button type="button" onClick={event => onOpenDocument(document.id, event.currentTarget)}><FileText size={15} aria-hidden="true" /><span>{documentTitle(document)}</span><ArrowUpRight size={14} aria-hidden="true" /></button>
        <span>{ownerName(document.owner)}<span aria-hidden="true"> · </span>{t(document.updatedLabel)}{document.edited !== null && <span className="fc-demo-desk-local">{t('Edited here')}</span>}</span>
      </div>
      <label className="fc-demo-desk-move"><span>{t('Move to')}</span><select ref={element => { moveSelects.current[document.id] = element; }} aria-label={t('Move {document} to', { document: documentTitle(document) })} value={document.stackId} onChange={event => moveDocument(document, event.target.value, true)}>{stacks.map(stack => <option key={stack.id} value={stack.id}>{stackTitle(stack)}</option>)}</select></label>
      {renderAssist && <div className="fc-demo-desk-assist fc-demo-desk-document-assist" data-assist-document={document.id}>{renderAssist({ kind: 'document', id: document.id })}</div>}
    </li>;
  }

  function renderStackContents(stack: DemoDocumentStack) {
    const stackDocuments = demoStackDocuments(state, channel, stack.id);
    const references = demoStackReferences(state, channel, stack.id);
    return <section className="fc-demo-desk-open" id={uid + '-stack-' + stack.id} key={stack.id} aria-label={stackTitle(stack)} onDragOver={event => acceptDrag(event, stack.id)} onDrop={event => receiveDrop(event, stack.id)}>
      <header>
        {renaming === stack.id ? <form className="fc-demo-desk-rename" onSubmit={event => finishRename(event, stack)}>
          <label><span>{t('Stack name')}</span><input autoFocus value={renameTitle} maxLength={80} onChange={event => { setRenameTitle(event.target.value); setFormError(''); }} aria-describedby={formError ? uid + '-rename-error' : undefined} /></label>
          <button type="submit" aria-label={t('Save stack name')}><Check size={16} aria-hidden="true" /></button>
          <button type="button" aria-label={t('Cancel renaming')} onClick={() => { setRenaming(null); setFormError(''); requestAnimationFrame(() => renameButtons.current[stack.id]?.focus({ preventScroll: true })); }}><X size={16} aria-hidden="true" /></button>
          {formError && <p id={uid + '-rename-error'} role="alert">{formError}</p>}
        </form> : <><div><span className="fc-demo-desk-eyebrow">{t('Inside the stack')}</span><h4>{stackTitle(stack)}</h4></div><div className="fc-demo-desk-stack-actions"><button ref={element => { renameButtons.current[stack.id] = element; }} type="button" onClick={() => { setRenaming(stack.id); setRenameTitle(stackTitle(stack)); setFormError(''); setNewItem(null); }}>{t('Rename')}</button>{view === 'stacks' && <button type="button" onClick={() => { setExpanded(null); stackButtons.current[stack.id]?.focus({ preventScroll: true }); }} aria-label={t('Close {stack}', { stack: stackTitle(stack) })}><X size={16} aria-hidden="true" /></button>}</div></>}
      </header>
      {renderAssist && <div className="fc-demo-desk-assist fc-demo-desk-stack-assist" data-assist-stack={stack.id}>{renderAssist({ kind: 'stack', id: stack.id })}</div>}
      {stackDocuments.length > 0 ? <ul className="fc-demo-desk-document-list">{stackDocuments.map(renderDocument)}</ul> : <p className="fc-demo-desk-empty">{t('Room for the next idea. Add a document or save a message from the conversation.')}</p>}
      {references.length > 0 && <div className="fc-demo-desk-references"><h5><MessageSquare size={14} aria-hidden="true" />{t('From the conversation')}</h5><ul>{references.map(message => <li key={message.id}><blockquote>{messageText(message)}</blockquote><div><span>{ownerName(message.author)}<span aria-hidden="true"> · </span>{message.time}</span><div className="fc-demo-desk-reference-actions"><button type="button" onClick={event => onRevealMessage(message.id, event.currentTarget)}>{t('Show original')}<ArrowUpRight size={13} aria-hidden="true" /></button><button type="button" aria-label={t('Remove reference from {stack}', { stack: stackTitle(stack) })} onClick={() => { dispatch({ type: 'stack_reference_remove', channel, stackId: stack.id, messageId: message.id }); setStatus(t('Reference removed. The original message is unchanged.')); requestAnimationFrame(() => undoButton.current?.focus({ preventScroll: true })); }}>{t('Remove')}</button></div></div></li>)}</ul></div>}
      <footer><button type="button" disabled={documents.length >= demoDocumentLimit} onClick={() => beginCreate('document', stack.id)}><Plus size={14} aria-hidden="true" />{t('Add document')}</button><span>{t('Drag a document here, or use Move to.')}</span></footer>
    </section>;
  }

  return <section className={'fc-demo-desk' + (compact ? ' fc-demo-desk-compact' : '')} aria-labelledby={uid + '-heading'}>
    <header className="fc-demo-desk-heading"><div><span className="fc-demo-desk-eyebrow">{t('Keep things together')}</span><h3 id={uid + '-heading'}>{t('Documents')}<span>{documents.length.toString().padStart(2, '0')}</span></h3></div><button ref={newButton} type="button" className="fc-demo-desk-new" onClick={() => { if (newItem) { setNewItem(null); setFormError(''); } else beginCreate(documents.length >= demoDocumentLimit ? 'stack' : 'document'); }} aria-expanded={!!newItem} aria-controls={newItem ? uid + '-create' : undefined} disabled={documents.length >= demoDocumentLimit && stacks.length >= demoStackLimit}><Plus size={16} aria-hidden="true" />{t('New')}</button></header>
    <div className="fc-demo-desk-toolbar"><div className="fc-demo-desk-view" role="group" aria-label={t('Document view')}><button type="button" aria-pressed={view === 'stacks'} onClick={() => setView('stacks')}><Layers size={14} aria-hidden="true" />{t('Stacks')}</button><button type="button" aria-pressed={view === 'list'} onClick={() => setView('list')}><List size={15} aria-hidden="true" />{t('List')}</button></div>{state.stackUndo[channel] && <button ref={undoButton} type="button" className="fc-demo-desk-undo" onClick={() => {
      const undo = state.stackUndo[channel];
      dispatch({ type: 'stack_undo', channel });
      if (undo?.kind === 'document_move') setExpanded(undo.fromStackId);
      setStatus(t('Last change undone.'));
      const stackId = undo?.kind === 'document_move' ? undo.fromStackId : undo?.stackId;
      requestAnimationFrame(() => { if (stackId) (view === 'stacks' ? stackButtons.current[stackId] : renameButtons.current[stackId])?.focus({ preventScroll: true }); });
    }}><Undo2 size={14} aria-hidden="true" />{t('Undo')}</button>}</div>
    {validReference && <form className="fc-demo-desk-reference-picker" onSubmit={event => { event.preventDefault(); if (chosenReferenceStack) saveReference(chosenReferenceStack.id, validReference.id, true); }}>
      <div><MessageSquare size={16} aria-hidden="true" /><strong>{t('Keep this message with the work')}</strong>{onCancelReference && <button type="button" aria-label={t('Cancel saving this message')} onClick={() => { onCancelReference(); requestAnimationFrame(() => newButton.current?.focus({ preventScroll: true })); }}><X size={16} aria-hidden="true" /></button>}</div><blockquote>{messageText(validReference)}</blockquote><p>{t('The original stays in the conversation.')}</p>
      <label><span>{t('Choose a stack')}</span><select value={chosenReferenceStack?.id ?? ''} onChange={event => setReferenceTarget(event.target.value)}>{stacks.map(stack => <option key={stack.id} value={stack.id}>{stackTitle(stack)}</option>)}</select></label><button type="submit" disabled={!chosenReferenceStack || referenceAlreadySaved || referenceAtLimit}>{referenceAlreadySaved ? t('Already saved here') : t('Save reference')}</button>{referenceAtLimit && !referenceAlreadySaved && <p>{t('This stack has reached the demo reference limit.')}</p>}
    </form>}
    {newItem && <form id={uid + '-create'} className="fc-demo-desk-create" onSubmit={finishCreate}>
      <div className="fc-demo-desk-create-heading"><strong>{t('Make room for an idea')}</strong><button type="button" aria-label={t('Cancel creating')} onClick={() => { setNewItem(null); setFormError(''); newButton.current?.focus(); }}><X size={16} aria-hidden="true" /></button></div>
      <div className="fc-demo-desk-create-kind" role="group" aria-label={t('What would you like to create?')}><button type="button" disabled={documents.length >= demoDocumentLimit} aria-pressed={newItem.kind === 'document'} onClick={() => { setNewItem({ ...newItem, kind: 'document' }); setFormError(''); }}>{t('Document')}</button><button type="button" disabled={stacks.length >= demoStackLimit} aria-pressed={newItem.kind === 'stack'} onClick={() => { setNewItem({ ...newItem, kind: 'stack' }); setFormError(''); }}>{t('Stack')}</button></div>
      <label><span>{newItem.kind === 'document' ? t('Document title') : t('Stack name')}</span><input autoFocus value={newTitle} maxLength={newItem.kind === 'document' ? 200 : 80} onChange={event => { setNewTitle(event.target.value); setFormError(''); }} aria-describedby={formError ? uid + '-create-error' : undefined} /></label>
      {newItem.kind === 'document' && <label><span>{t('In this stack')}</span><select value={newItem.stackId} onChange={event => setNewItem({ ...newItem, stackId: event.target.value })}>{stacks.map(stack => <option key={stack.id} value={stack.id}>{stackTitle(stack)}</option>)}</select></label>}
      {formError && <p id={uid + '-create-error'} role="alert">{formError}</p>}<button type="submit" className="fc-showroom-primary" disabled={!newTitle.trim() || (newItem.kind === 'document' && documents.length >= demoDocumentLimit) || (newItem.kind === 'stack' && stacks.length >= demoStackLimit)}><Plus size={14} aria-hidden="true" />{newItem.kind === 'document' ? t('Create document') : t('Create stack')}</button><small>{t('Only in this public demo. Reset or reload clears your changes.')}</small>
    </form>}
    {view === 'stacks' ? <><div className="fc-demo-desk-board">{stacks.map((stack, index) => {
      const stackDocuments = demoStackDocuments(state, channel, stack.id);
      const references = demoStackReferences(state, channel, stack.id);
      const latest = stackDocuments.reduce<DemoDocument | undefined>((previous, document) => !previous || document.updatedLabel === 'Just now' || (previous.updatedLabel !== 'Just now' && document.updatedLabel > previous.updatedLabel) ? document : previous, undefined);
      return <div key={stack.id} className={'fc-demo-stack-place' + (dropTarget === stack.id ? ' fc-demo-stack-drop' : '')} data-papers={Math.min(3, Math.max(1, stackDocuments.length + references.length))} data-position={index % 3} onDragOver={event => acceptDrag(event, stack.id)} onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDropTarget(null); }} onDrop={event => receiveDrop(event, stack.id)}>
        <button ref={element => { stackButtons.current[stack.id] = element; }} className="fc-demo-stack-cover" type="button" aria-expanded={expanded === stack.id} aria-controls={expanded === stack.id ? uid + '-stack-' + stack.id : undefined} onClick={() => { setExpanded(expanded === stack.id ? null : stack.id); setRenaming(null); setFormError(''); }}>
          <span className="fc-demo-stack-index">{(index + 1).toString().padStart(2, '0')}<span>{documentCount(stackDocuments.length)}{references.length > 0 && <><span aria-hidden="true"> · </span>{referenceCount(references.length)}</>}</span></span>
          <strong>{stackTitle(stack)}</strong><span className="fc-demo-stack-rule" aria-hidden="true" />
          <span className="fc-demo-stack-latest">{latest ? documentTitle(latest) : references[0] ? t('A note from {author}', { author: ownerName(references[0].author) }) : t('A little room to begin')}</span>
          <span className="fc-demo-stack-bottom"><span>{latest ? <>{ownerName(latest.owner)}<span aria-hidden="true"> · </span>{t(latest.updatedLabel)}</> : references.length ? t('Linked to the conversation') : t('Add something here')}</span><ChevronDown size={15} aria-hidden="true" /></span>
        </button>
      </div>;
    })}</div>{expandedStack && renderStackContents(expandedStack)}</> : <div className="fc-demo-desk-list-view">{stacks.map(renderStackContents)}</div>}
    <p className="fc-demo-desk-status" role="status" aria-live="polite">{status || t('Move papers between stacks. Keep the conversation close.')}</p>
  </section>;
}
