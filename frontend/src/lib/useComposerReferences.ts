import { useEffect, useId, useRef, useState, type KeyboardEvent, type RefObject } from 'react';
import { api } from './api';
import { nextCommandIndex } from './commands';
import { messageReferencesApi } from './message-references-api';
import { getReferenceQuery, insertReference, referenceKey, type MessageReference, type ReferenceCandidate, type ReferenceQuery } from './message-references';
import { inferSuggestionContext, rankReferenceSuggestions, recordReferenceChoice, referenceSuggestionContext, type SuggestionContext } from './suggestion-ranking';

interface Options {
  channelId?: string;
  workspaceId?: string;
  secure?: boolean;
  /** Distinguishes draft contexts such as threads within the same channel. */
  contextId?: string;
  suggestions?: SuggestionContext;
  text: string;
  references: MessageReference[];
  input: RefObject<HTMLInputElement | HTMLTextAreaElement>;
  onInsert: (text: string, references: MessageReference[]) => void;
  disabled?: boolean;
  maxLength?: number;
  /** Explicit owner Save controls may need to retain the picker until activation. */
  keepOpenFor?: (target: Node) => boolean;
}

/** Suggestions belong to one input, caret and authenticated conversation. */
export function useComposerReferences(options: Options) {
  const id = useId();
  const token = api.getAccessToken();
  const scope = JSON.stringify([api.getCurrentUserId(), token, options.workspaceId, options.channelId, !!options.secure, options.contextId]);
  const [selection, setSelection] = useState<{ scope: string; text: string; query: ReferenceQuery } | null>(null);
  const [response, setResponse] = useState<{ key: string; candidates: ReferenceCandidate[]; error: string } | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const dismissed = useRef('');
  const pendingFocus = useRef<(() => void) | null>(null);
  const live = useRef(options); live.current = options;
  const currentScope = useRef(scope); currentScope.current = scope;
  const open = !options.disabled && !!token && !!options.channelId && !!options.workspaceId &&
    !!selection && selection.scope === scope && selection.text === options.text;
  const query = open ? selection.query : null;
  const key = query && selection ? JSON.stringify([scope, selection.text, query.start, query.end, query.query]) : '';
  const suggestionScope = { actorId: api.getCurrentUserId(), token, workspaceId: options.workspaceId, channelId: options.channelId };
  const suggestions = referenceSuggestionContext(options.suggestions || inferSuggestionContext(options.contextId), options.text, query?.start ?? options.text.length);
  const candidates = key && response?.key === key ? rankReferenceSuggestions(response.candidates, query?.query || '', suggestions, suggestionScope) : [];
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

  useEffect(() => { pendingFocus.current?.(); setSelection(null); setResponse(null); setSelected(null); dismissed.current = ''; }, [scope]);
  useEffect(() => {
    const clear = () => { pendingFocus.current?.(); setSelection(null); setResponse(null); };
    window.addEventListener('freecollab:auth-changed', clear);
    return () => { pendingFocus.current?.(); window.removeEventListener('freecollab:auth-changed', clear); };
  }, []);
  useEffect(() => {
    if (!open) return;
    const outside = (event: Event) => {
      const target = event.target as Node;
      if (live.current.keepOpenFor?.(target)) return;
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
    if (result.text.length > (before.maxLength ?? 10000)) return;
    const input = before.input.current, actor = api.getCurrentUserId();
    if (!input?.isConnected) return;
    pendingFocus.current?.();
    let retired = false, frame: number | null = null;
    const intents = ['pointerdown', 'touchstart', 'keydown', 'beforeinput', 'input', 'compositionstart', 'focusin', 'select'];
    const retire = () => {
      if (retired) return;
      retired = true;
      for (const name of intents) document.removeEventListener(name, retire, true);
      if (frame !== null) cancelAnimationFrame(frame);
      if (pendingFocus.current === retire) pendingFocus.current = null;
    };
    pendingFocus.current = retire;
    for (const name of intents) document.addEventListener(name, retire, true);
    try { before.onInsert(result.text, result.references); }
    catch (failure) { retire(); throw failure; }
    if (token === api.getAccessToken() && actor === api.getCurrentUserId() && currentScope.current === scope) recordReferenceChoice(suggestionScope, candidate);
    setSelection(null);
    if (retired) return;
    frame = requestAnimationFrame(() => {
      const current = !retired && !live.current.disabled && currentScope.current === scope && token === api.getAccessToken() && actor === api.getCurrentUserId()
        && live.current.input.current === input && input.isConnected && document.activeElement === input && input.value === result.text;
      retire();
      if (current) {
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
