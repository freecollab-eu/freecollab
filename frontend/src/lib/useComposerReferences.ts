import { useEffect, useId, useRef, useState, type KeyboardEvent, type RefObject } from 'react';
import { api } from './api';
import { nextCommandIndex } from './commands';
import { messageReferencesApi } from './message-references-api';
import { getReferenceQuery, insertReference, referenceKey, type MessageReference, type ReferenceCandidate, type ReferenceQuery } from './message-references';

interface Options {
  channelId?: string;
  workspaceId?: string;
  secure?: boolean;
  text: string;
  references: MessageReference[];
  input: RefObject<HTMLInputElement | HTMLTextAreaElement>;
  onInsert: (text: string, references: MessageReference[]) => void;
  disabled?: boolean;
}

/** Suggestions belong to one input, caret and authenticated conversation. */
export function useComposerReferences(options: Options) {
  const id = useId();
  const token = api.getAccessToken();
  const scope = JSON.stringify([api.getCurrentUserId(), token, options.workspaceId, options.channelId, !!options.secure]);
  const [selection, setSelection] = useState<{ scope: string; text: string; query: ReferenceQuery } | null>(null);
  const [response, setResponse] = useState<{ key: string; candidates: ReferenceCandidate[]; error: string } | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const dismissed = useRef('');
  const live = useRef(options); live.current = options;
  const currentScope = useRef(scope); currentScope.current = scope;
  const open = !options.disabled && !!token && !!options.channelId && !!options.workspaceId &&
    !!selection && selection.scope === scope && selection.text === options.text;
  const query = open ? selection.query : null;
  const key = query && selection ? JSON.stringify([scope, selection.text, query.start, query.end, query.query]) : '';
  const candidates = key && response?.key === key ? response.candidates : [];
  const error = key && response?.key === key ? response.error : '';
  const activeIndex = candidates.length ? Math.max(0, candidates.findIndex(candidate => referenceKey(candidate) === selected)) : -1;

  const close = () => {
    dismissed.current = JSON.stringify([scope, live.current.text, live.current.input.current?.selectionStart]);
    setSelection(null);
  };

  function update(text: string, caret: number, references = live.current.references, editing = false) {
    const value = getReferenceQuery(text, caret);
    if (editing) dismissed.current = '';
    if (options.disabled || !token || !options.channelId || !options.workspaceId || !value ||
      references.some(reference => reference.start === value.start) ||
      dismissed.current === JSON.stringify([scope, text, caret])) { setSelection(null); return; }
    setSelection(previous => previous?.scope === scope && previous.text === text && previous.query.start === value.start && previous.query.end === value.end
      ? previous : { scope, text, query: value });
    if (!selection || selection.query.query !== value.query || selection.query.start !== value.start) setSelected(null);
  }

  useEffect(() => { setSelection(null); setResponse(null); setSelected(null); dismissed.current = ''; }, [scope]);
  useEffect(() => {
    const clear = () => { setSelection(null); setResponse(null); };
    window.addEventListener('freecollab:auth-changed', clear);
    return () => window.removeEventListener('freecollab:auth-changed', clear);
  }, []);
  useEffect(() => {
    if (!open) return;
    const outside = (event: Event) => {
      const target = event.target as Node;
      if (target !== live.current.input.current && !document.getElementById(id)?.closest('section')?.contains(target)) setSelection(null);
    };
    document.addEventListener('pointerdown', outside, true);
    document.addEventListener('focusin', outside);
    return () => { document.removeEventListener('pointerdown', outside, true); document.removeEventListener('focusin', outside); };
  }, [open, id]);
  useEffect(() => {
    if (!key || !query || !options.channelId) return;
    let alive = true;
    const abort = new AbortController();
    const timer = window.setTimeout(() => {
      void messageReferencesApi.suggestions(options.channelId!, query.query, abort.signal, !!options.secure).then(items => {
        if (alive && currentScope.current === scope && token === api.getAccessToken()) setResponse({ key, candidates: items, error: '' });
      }).catch(failure => {
        if (alive && !abort.signal.aborted && currentScope.current === scope && token === api.getAccessToken())
          setResponse({ key, candidates: [], error: failure instanceof Error ? failure.message : 'References could not be loaded.' });
      });
    }, 120);
    return () => { alive = false; clearTimeout(timer); abort.abort(); };
  }, [key]);

  function choose(candidate: ReferenceCandidate) {
    if (!open || !query || !key || response?.key !== key || token !== api.getAccessToken() || currentScope.current !== scope ||
      !candidates.some(row => referenceKey(row) === referenceKey(candidate))) return;
    const before = live.current;
    if (before.text !== selection.text) return;
    const result = insertReference(before.text, before.references, query, candidate);
    if (result.text.length > 10000) return;
    before.onInsert(result.text, result.references);
    setSelection(null);
    requestAnimationFrame(() => {
      const input = live.current.input.current;
      if (currentScope.current === scope && token === api.getAccessToken() && input?.value === result.text) {
        input.focus({ preventScroll: true }); input.setSelectionRange(result.caret, result.caret);
      }
    });
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>) {
    if (!open || event.nativeEvent.isComposing || event.keyCode === 229) return false;
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); return true; }
    if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? candidates.length - 1 : nextCommandIndex(activeIndex, event.key === 'ArrowDown' ? 1 : -1, candidates.length);
      setSelected(candidates[next] ? referenceKey(candidates[next]) : null); return true;
    }
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault(); event.stopPropagation();
      if (candidates[activeIndex]) choose(candidates[activeIndex]);
      return true;
    }
    if (event.key === 'Tab') close();
    return false;
  }

  return { id, open, query, candidates, error, activeIndex, loading: !!key && response?.key !== key, close, update, choose, handleKeyDown,
    setActiveIndex: (index: number) => setSelected(candidates[index] ? referenceKey(candidates[index]) : null) };
}
