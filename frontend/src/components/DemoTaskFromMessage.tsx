import { useId, useRef } from 'react';
import { ArrowUpRight, Check, MessageSquare, Undo2, X } from 'lucide-react';
import { demoOwners, type DemoMessage, type DemoOwner, type DemoTask } from '../lib/demo-showroom';
import '../styles/demo-message-actions.css';

export interface DemoTaskFromMessageProps {
  message: DemoMessage;
  channelName: string;
  text: string;
  owner: DemoOwner | '';
  onTextChange: (value: string) => void;
  onOwnerChange: (value: DemoOwner | '') => void;
  onSubmit: () => void;
  onCancel: () => void;
  onRevealMessage: (id: string, source: HTMLElement) => void;
  existingTask?: DemoTask;
  limitReached?: boolean;
  canUndo?: boolean;
  onUndo?: () => void;
  t: (source: string, values?: Record<string, string | number>) => string;
}

/** A controlled review of the sent message, without inventing task text or an owner. */
export function DemoTaskFromMessage({ message, channelName, text, owner, onTextChange, onOwnerChange, onSubmit, onCancel, onRevealMessage, existingTask, limitReached = false, canUndo = false, onUndo, t }: DemoTaskFromMessageProps) {
  const uid = useId(), heading = useRef<HTMLHeadingElement>(null), currentMessage = useRef(message.id);
  currentMessage.current = message.id;
  const source = message.sample ? t(message.text) : message.text;
  const characters = Array.from(source), excerpt = characters.slice(0, 160).join('');
  const afterChange = () => { const sourceId = message.id; requestAnimationFrame(() => { if (currentMessage.current === sourceId) heading.current?.focus({ preventScroll: true }); }); };
  return <section className="fc-demo-task-from-message" aria-labelledby={uid + '-title'}>
    <header><div><span className="fc-demo-task-source-kicker"><MessageSquare size={13} aria-hidden="true" />{t('From the conversation')} · #{channelName}</span><h3 ref={heading} id={uid + '-title'} tabIndex={-1}>{t(existingTask ? 'Task from this message' : 'Make this a next step')}</h3></div><button type="button" className="fc-demo-task-close" aria-label={t('Close message task')} onClick={onCancel}><X size={16} aria-hidden="true" /></button></header>
    <div className="fc-demo-task-source"><div><strong>{message.sample ? message.author : t('You')}</strong><time>{message.time}</time></div><blockquote>{excerpt}{characters.length > 160 && '…'}</blockquote>{characters.length > 160 && <details><summary>{t('Read the full message')}</summary><blockquote>{source}</blockquote></details>}<button type="button" onClick={event => onRevealMessage(message.id, event.currentTarget)}>{t('View original message')}<ArrowUpRight size={13} aria-hidden="true" /></button></div>
    {existingTask ? <div className="fc-demo-task-created"><span><Check size={14} aria-hidden="true" />{t('Already a task')}</span><p>{existingTask.sample ? t(existingTask.text) : existingTask.text}</p><dl><div><dt>{t('Task owner')}</dt><dd>{existingTask.owner ?? t('Unassigned')}</dd></div></dl><p>{t('Follow the task in the list below.')}</p><div>{canUndo && onUndo && <button type="button" onClick={() => { onUndo(); afterChange(); }}><Undo2 size={14} aria-hidden="true" />{t('Undo task creation')}</button>}<button type="button" onClick={onCancel}>{t('Back to tasks')}</button></div></div> : <form onSubmit={event => { event.preventDefault(); if (limitReached || !text.trim() || text.length > 180) return; onSubmit(); afterChange(); }}>
      <label htmlFor={uid + '-text'}>{t('Task title')}<input id={uid + '-text'} value={text} maxLength={180} required onChange={event => onTextChange(event.target.value)} aria-describedby={uid + '-review'} /></label>
      <p id={uid + '-review'}>{t('Review the wording and owner, then create the task.')}</p>
      <fieldset><legend>{t('Task owner')}</legend><div className="fc-demo-task-owner-choices"><button type="button" aria-pressed={owner === ''} onClick={() => onOwnerChange('')}>{t('Unassigned')}</button>{demoOwners.map(name => <button key={name} type="button" aria-pressed={owner === name} onClick={() => onOwnerChange(name)}>{name}</button>)}</div></fieldset>
      {limitReached && <p role="status">{t('This sample project is full. Reset the demo to start again.')}</p>}
      <footer><button type="button" onClick={onCancel}>{t('Cancel')}</button><button type="submit" className="fc-showroom-primary" disabled={limitReached || !text.trim() || text.length > 180}>{t('Create task')}<Check size={14} aria-hidden="true" /></button></footer>
      <small>{t('This task stays in this demo. The original message keeps its place.')}</small>
    </form>}
  </section>;
}
