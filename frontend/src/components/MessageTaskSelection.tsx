import { useEffect, useLayoutEffect, useRef, useState, type PointerEvent } from 'react';
import { createPortal } from 'react-dom';
import { CheckSquare } from 'lucide-react';
import { t, useI18n } from '../i18n';
import { api, type Message } from '../lib/api';
import { messageTaskToolbarPosition, readMessageTaskSelection, sameMessageTaskSelection, taskSelectionFingerprint, type MessageTaskExcerpt } from '../lib/message-task-selection';
import '../styles/message-task-selection.css';

interface Props {
  scopeKey: string;
  channelId: string;
  messages: readonly Message[];
  onCreateTask: (message: Message, text: string) => void;
}
interface SelectionOwner { scopeKey: string; channelId: string; token: string | null; userId: string | null }
type OwnedExcerpt = MessageTaskExcerpt & { owner: SelectionOwner };

/** Selecting text opens an editable task draft. It never creates a task itself. */
export function MessageTaskSelection({ scopeKey, channelId, messages, onCreateTask }: Props) {
  useI18n();
  const [excerpt, setExcerpt] = useState<OwnedExcerpt | null>(null);
  const [size, setSize] = useState({ width: 196, height: 40 });
  const button = useRef<HTMLButtonElement>(null);
  const live = useRef({ scopeKey, channelId, messages, onCreateTask });
  live.current = { scopeKey, channelId, messages, onCreateTask };
  const lease = useRef({ scopeKey, channelId, token: api.getAccessToken(), userId: api.getCurrentUserId(), active: true });
  const dismissed = useRef<MessageTaskExcerpt | null>(null);
  const visibleExcerpt = useRef(excerpt); visibleExcerpt.current = excerpt;
  const current = () => lease.current.active && !!lease.current.token && !!lease.current.userId
    && lease.current.scopeKey === live.current.scopeKey && lease.current.channelId === live.current.channelId
    && lease.current.token === api.getAccessToken() && lease.current.userId === api.getCurrentUserId();
  const owned = (value: OwnedExcerpt) => current() && value.owner.scopeKey === live.current.scopeKey && value.owner.channelId === live.current.channelId
    && value.owner.token === api.getAccessToken() && value.owner.userId === api.getCurrentUserId();
  useEffect(() => {
    lease.current = { scopeKey, channelId, token: api.getAccessToken(), userId: api.getCurrentUserId(), active: true };
    setExcerpt(null); dismissed.current = null;
    const capture = () => {
      if (!current()) { setExcerpt(null); return; }
      const next = readMessageTaskSelection(window.getSelection(), live.current.channelId, live.current.messages);
      if (next && dismissed.current && sameMessageTaskSelection(next, dismissed.current)) { setExcerpt(null); return; }
      dismissed.current = null;
      if (next && (next.rect.bottom < 0 || next.rect.top > window.innerHeight || next.rect.right < 0 || next.rect.left > window.innerWidth)) { setExcerpt(null); return; }
      setExcerpt(next ? { ...next, owner: { ...lease.current } } : null);
    };
    const auth = () => { lease.current.active = false; setExcerpt(null); };
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { dismissed.current = readMessageTaskSelection(window.getSelection(), live.current.channelId, live.current.messages); setExcerpt(null); }
      else capture();
    };
    const dismissKey = (event: KeyboardEvent) => {
      const shown = visibleExcerpt.current;
      if (event.key !== 'Escape' || event.defaultPrevented || event.isComposing || event.keyCode === 229 || !shown || !owned(shown)
        || !shown.root.isConnected || shown.root.closest('[hidden], [inert], [aria-hidden="true"]')) return;
      const actual = readMessageTaskSelection(window.getSelection(), live.current.channelId, live.current.messages);
      if (!actual || !sameMessageTaskSelection(shown, actual)) return;
      event.preventDefault(); event.stopPropagation(); dismissed.current = actual; setExcerpt(null);
    };
    document.addEventListener('selectionchange', capture);
    document.addEventListener('mouseup', capture);
    document.addEventListener('keyup', key);
    document.addEventListener('keydown', dismissKey, true);
    window.addEventListener('resize', capture);
    window.addEventListener('scroll', capture, true);
    window.addEventListener('freecollab:auth-changed', auth);
    return () => {
      lease.current.active = false;
      document.removeEventListener('selectionchange', capture);
      document.removeEventListener('mouseup', capture);
      document.removeEventListener('keyup', key);
      document.removeEventListener('keydown', dismissKey, true);
      window.removeEventListener('resize', capture);
      window.removeEventListener('scroll', capture, true);
      window.removeEventListener('freecollab:auth-changed', auth);
    };
  }, [scopeKey, channelId]);
  useEffect(() => {
    if (!excerpt) return;
    const message = messages.find(message => message.id === excerpt.messageId && message.channel_id === channelId);
    if (!message || taskSelectionFingerprint(message) !== excerpt.fingerprint || !excerpt.root.isConnected || !owned(excerpt)) setExcerpt(null);
  }, [messages, excerpt, scopeKey, channelId]);
  useLayoutEffect(() => {
    if (!excerpt || !button.current) return;
    const measured = button.current.getBoundingClientRect();
    if (measured.width > 0 && measured.height > 0 && (measured.width !== size.width || measured.height !== size.height)) setSize({ width: measured.width, height: measured.height });
  }, [excerpt, size]);
  const preserve = (event: PointerEvent<HTMLButtonElement>) => event.preventDefault();
  function open() {
    if (!excerpt || !owned(excerpt)) { setExcerpt(null); return; }
    const actual = readMessageTaskSelection(window.getSelection(), live.current.channelId, live.current.messages);
    const message = live.current.messages.find(message => message.id === excerpt.messageId && message.channel_id === live.current.channelId);
    if (!actual || !sameMessageTaskSelection(excerpt, actual) || !message || !owned(excerpt)) { setExcerpt(null); return; }
    dismissed.current = actual; setExcerpt(null);
    live.current.onCreateTask(message, actual.text);
  }
  if (!excerpt || !owned(excerpt) || !excerpt.root.isConnected || excerpt.root.closest('[hidden], [inert], [aria-hidden="true"]')) return null;
  const position = messageTaskToolbarPosition(excerpt.rect, { width: window.innerWidth, height: window.innerHeight }, size);
  const portal = excerpt.root.closest<HTMLElement>('[role="dialog"], [aria-modal="true"]') || document.body;
  return createPortal(<button ref={button} type="button" className="fc-message-task-selection" style={position}
    onPointerDown={preserve} onMouseDown={event => event.preventDefault()} onClick={open}>
    <CheckSquare size={15} aria-hidden="true" />{t('Make a next step')}
  </button>, portal);
}
