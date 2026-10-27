import { forwardRef, useEffect, useId, useLayoutEffect, useRef, useState, type ChangeEventHandler, type CompositionEventHandler, type CSSProperties, type FormEventHandler, type InputHTMLAttributes, type KeyboardEventHandler, type MouseEventHandler, type ReactEventHandler, type TextareaHTMLAttributes } from 'react';
import { t, useI18n } from '../i18n';
import { api } from '../lib/api';
import { useCollaborationContext, type CollaborationInputElement, type CollaborationOrigin } from '../lib/collaboration-context';
import { parseComposerCommand, prepareCommandPeople } from '../lib/composer-commands';
import { nextCommandIndex } from '../lib/commands';
import { getSlashCommands, type SlashCommand, type SlashContext } from '../lib/slash-commands';
import { insertReference, normalizeMessageReferences, reconcileReferences, referenceOptionId, type MessageReference, type ReferenceCandidate, type ReferenceEdit } from '../lib/message-references';
import { useComposerReferences } from '../lib/useComposerReferences';
import { inferSuggestionContext, recordCommandChoice, type SuggestionContext } from '../lib/suggestion-ranking';
import { SlashCommands } from './SlashCommands';
import { MentionReferences } from './MentionReferences';
import { WritingCopilot, writingSuggestion } from './WritingCopilot';
import '../styles/collaboration-input.css';

export type { CollaborationInputElement } from '../lib/collaboration-context';
type EventAttributes = 'onChange' | 'onBeforeInput' | 'onKeyDown' | 'onKeyUp' | 'onClick' | 'onSelect' | 'onCompositionStart' | 'onCompositionEnd';
type NativeAttributes = Omit<InputHTMLAttributes<HTMLInputElement> & TextareaHTMLAttributes<HTMLTextAreaElement>, EventAttributes | 'value' | 'defaultValue' | 'children'>;
export interface CollaborationInputProps extends NativeAttributes {
  as?: 'input' | 'textarea';
  contextId: string;
  suggestions?: SuggestionContext;
  value: string;
  references?: MessageReference[];
  onValueChange: (text: string, references: MessageReference[]) => void;
  allowCommands?: boolean;
  allowReferences?: boolean;
  /** Explicit AI review is available beside ordinary writing, never technical credentials. */
  allowAI?: boolean;
  /** Consume only the accepted current command line, never unrelated writing. */
  consumeCommands?: boolean;
  toolbar?: boolean;
  containerClassName?: string;
  /** Guards explicit Save buttons whose owner does not submit a native form. */
  onPaletteActiveChange?: (active: boolean) => void;
  /** Matching explicit Save controls use data-collaboration-submit={scope}. */
  submissionScope?: string;
  onChange?: ChangeEventHandler<CollaborationInputElement>;
  onBeforeInput?: FormEventHandler<CollaborationInputElement>;
  onKeyDown?: KeyboardEventHandler<CollaborationInputElement>;
  onKeyUp?: KeyboardEventHandler<CollaborationInputElement>;
  onClick?: MouseEventHandler<CollaborationInputElement>;
  onSelect?: ReactEventHandler<CollaborationInputElement>;
  onCompositionStart?: CompositionEventHandler<CollaborationInputElement>;
  onCompositionEnd?: CompositionEventHandler<CollaborationInputElement>;
}

export interface CollaborationCommandRange { start: number; end: number; text: string }

/** Only an intentional command line at the actual caret is interpreted. */
export function getCollaborationCommandRange(value: string, caret: number): CollaborationCommandRange | null {
  if (!Number.isSafeInteger(caret) || caret < 0 || caret > value.length) return null;
  const lineStart = value.lastIndexOf('\n', caret - 1) + 1;
  const nextLine = value.indexOf('\n', caret);
  const lineEnd = nextLine < 0 ? value.length : nextLine;
  const line = value.slice(lineStart, lineEnd);
  const indentation = /^[\t ]*/.exec(line)![0].length;
  const start = lineStart + indentation;
  if (caret <= start || value[start] !== '/' || value[start + 1] === '/') return null;
  // A Markdown code sample remains literal writing.
  let fence: string | null = null;
  for (const previous of value.slice(0, lineStart).split('\n')) {
    const match = /^[\t ]*(`{3,}|~{3,})(.*)$/.exec(previous);
    if (!match) continue;
    if (!fence) fence = match[1];
    else if (match[1][0] === fence[0] && match[1].length >= fence.length && !match[2].trim()) fence = null;
  }
  if (fence) return null;
  return { start, end: lineEnd, text: value.slice(start, lineEnd) };
}

const emptyContext: SlashContext = { hasChannel: false, hasWorkspace: false, secure: false, embedded: true, nativeSession: false, hasAdmin: false };
const visible = (input: CollaborationInputElement | null): input is CollaborationInputElement => !!input?.isConnected && !input.closest('[hidden], [inert], [aria-hidden="true"]') && input.getClientRects().length > 0 && getComputedStyle(input).visibility !== 'hidden';
interface Palette { identity: string; mode: 'typed' | 'browse'; query: string; value: string; range?: CollaborationCommandRange }
interface Placement { left: number; top: number; width: number; height: number; below: boolean }

/** Explicit opt-in writing field. Source text and reference persistence remain owned by its editor. */
export const CollaborationInput = forwardRef<CollaborationInputElement, CollaborationInputProps>(function CollaborationInput({
  as = 'textarea', contextId, suggestions, value, references = [], onValueChange, allowCommands = true, allowReferences = true, allowAI = true,
  consumeCommands = true, toolbar = false, containerClassName = '', onPaletteActiveChange, submissionScope, onChange, onBeforeInput, onKeyDown, onKeyUp, onClick, onSelect,
  onCompositionStart, onCompositionEnd, ...attributes
}, forwardedRef) {
  useI18n();
  const collaboration = useCollaborationContext();
  const root = useRef<HTMLDivElement>(null), input = useRef<CollaborationInputElement | null>(null), fixedOrigin = useRef<HTMLSpanElement>(null);
  const composing = useRef(false), edit = useRef<ReferenceEdit>(), dismissed = useRef(''), sequence = useRef(0);
  const actor = api.getCurrentUserId(), token = api.getAccessToken();
  const identity = JSON.stringify([actor, token, collaboration?.workspaceId, collaboration?.channelId, !!collaboration?.secure, collaboration?.surfaceId, contextId]);
  const referenceIdentity = JSON.stringify(normalizeMessageReferences(value, references));
  const revision = useRef({ identity, value, referenceIdentity, version: 0 });
  if (revision.current.identity !== identity || revision.current.value !== value || revision.current.referenceIdentity !== referenceIdentity)
    revision.current = { identity, value, referenceIdentity, version: revision.current.version + 1 };
  const live = useRef({ identity, value, references, onValueChange, collaboration });
  live.current = { identity, value, references, onValueChange, collaboration };
  const id = useId();
  const [palette, setPalette] = useState<Palette | null>(null), [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState(''), [pending, setPending] = useState(false), [placement, setPlacement] = useState<Placement | null>(null);
  const safeType = as === 'textarea' || !attributes.type || ['text', 'search'].includes(attributes.type);
  const writable = safeType && !attributes.disabled && !attributes.readOnly;
  const available = writable && !!collaboration?.workspaceId && !!collaboration.channelId && !!actor && !!token;
  const commandsEnabled = available && allowCommands;
  const referencesEnabled = available && allowReferences;
  const suggestionScope = { actorId: actor, token, workspaceId: collaboration?.workspaceId, channelId: collaboration?.channelId };
  const inferredContext = inferSuggestionContext(attributes.id, contextId, collaboration?.surfaceId);
  const writingContext = suggestions || {
    ...(collaboration?.commandContext.suggestions || inferredContext),
    ...(inferredContext.surface !== 'conversation' ? { surface: inferredContext.surface } : {}),
    ...(inferredContext.field ? { field: inferredContext.field } : {}),
  };
  const commandContext = { ...(collaboration?.commandContext || emptyContext), suggestions: writingContext, suggestionScope };
  const activePalette = commandsEnabled && palette?.identity === identity && palette.value === value ? palette : null;
  const range = activePalette?.range;
  const commandText = range?.text || '';
  const lineReferences = range ? normalizeMessageReferences(value, references).filter(reference => reference.start >= range.start && reference.end <= range.end)
    .map(reference => ({ ...reference, start: reference.start - range.start, end: reference.end - range.start })) : [];
  const people = prepareCommandPeople(commandText, lineReferences);
  const raw = activePalette?.mode === 'typed' ? parseComposerCommand(commandText, commandContext) : null;
  const parsed = raw?.command && ['task', 'decision', 'groups'].includes(raw.command.id) ? parseComposerCommand(people.text, commandContext) : raw;
  const commands = activePalette ? raw?.command && raw.arguments?.trim() ? [raw.command] : getSlashCommands(commandContext, parsed?.query ?? activePalette.query) : [];
  const activeIndex = commands.length ? Math.max(0, commands.findIndex(command => command.id === selected)) : -1;

  function isSubmissionControl(target: Node) {
    if (!(target instanceof Element)) return false;
    const marked = target.closest('[data-collaboration-submit]');
    if (submissionScope && marked?.getAttribute('data-collaboration-submit') === submissionScope) return true;
    const control = target.closest('button, input') as HTMLButtonElement | HTMLInputElement | null;
    return control?.type === 'submit' && !!control.form && control.form === input.current?.form;
  }
  const picker = useComposerReferences({
    channelId: collaboration?.channelId, workspaceId: collaboration?.workspaceId, secure: collaboration?.secure,
    contextId: JSON.stringify([collaboration?.surfaceId, contextId]), suggestions: writingContext, text: value, references, input, disabled: !referencesEnabled,
    maxLength: attributes.maxLength ?? 10000, keepOpenFor: isSubmissionControl,
    onInsert(text, nextReferences) {
      if (live.current.identity !== identity || !visible(input.current)) return;
      live.current.onValueChange(text, nextReferences);
      updateCommand(text, input.current.selectionStart ?? text.length, true);
    },
  });
  const open = !!activePalette && !picker.open || picker.open;
  const formBlocked = useRef(false); formBlocked.current = !!activePalette || picker.open;
  const paletteCallback = useRef(onPaletteActiveChange); paletteCallback.current = onPaletteActiveChange;
  useLayoutEffect(() => { paletteCallback.current?.(open); return () => paletteCallback.current?.(false); }, [open, identity]);

  function bindInput(node: CollaborationInputElement | null) {
    input.current = node;
    if (typeof forwardedRef === 'function') forwardedRef(node);
    else if (forwardedRef) forwardedRef.current = node;
  }
  function closePalette(restoreFocus = false, cancelPending = false) {
    if (cancelPending) { ++sequence.current; setPending(false); }
    const current = palette;
    dismissed.current = JSON.stringify([identity, value, current?.range?.start, current?.range?.end]);
    formBlocked.current = false;
    setPalette(null); setError('');
    if (restoreFocus && visible(input.current)) input.current.focus({ preventScroll: true });
  }
  function updateCommand(text: string, caret: number, editing = false) {
    if (editing) dismissed.current = '';
    const next = getCollaborationCommandRange(text, caret);
    const key = JSON.stringify([identity, text, next?.start, next?.end]);
    if (!commandsEnabled || composing.current || !next || dismissed.current === key) { setPalette(null); return; }
    setError(''); setSelected(null);
    setPalette(previous => previous?.identity === identity && previous.mode === 'typed' && previous.value === text && previous.range?.start === next.start && previous.range?.end === next.end
      ? previous : { identity, mode: 'typed', value: text, query: next.text, range: next });
  }
  function origin(): CollaborationOrigin | null {
    const field = input.current, provider = live.current.collaboration;
    if (!available || !token || !field || !visible(field) || !provider?.workspaceId || !provider.channelId || field.value !== value) return null;
    let version = revision.current.version, expectedValue = value;
    const selectionStart = field.selectionStart ?? value.length, selectionEnd = field.selectionEnd ?? selectionStart;
    const current = () => identity === live.current.identity && version === revision.current.version && input.current === field && field.isConnected
      && provider.isCurrent?.() !== false && field.value === expectedValue && live.current.value === expectedValue
      && actor === api.getCurrentUserId() && token === api.getAccessToken();
    const result: CollaborationOrigin & { consume?: () => void } = { contextId, surfaceId: provider.surfaceId, input: field, value, references: normalizeMessageReferences(value, references), selectionStart, selectionEnd,
      commandRange: range, personIds: people.personIds, actorId: actor, token, workspaceId: provider.workspaceId, channelId: provider.channelId, secure: !!provider.secure,
      isCurrent: current,
      restore() { if (!current() || !visible(field)) return false; field.focus({ preventScroll: true }); field.setSelectionRange(result.selectionStart, result.selectionEnd); return true; },
      consume() {
        if (!range || !current() || field.disabled || field.readOnly) return;
        const after = value.slice(0, range.start) + value.slice(range.end);
        const nextReferences = reconcileReferences(value, after, result.references, { start: range.start, end: range.end });
        const beforeVersion = version;
        live.current.onValueChange(after, nextReferences);
        expectedValue = after; version = beforeVersion + 1;
        result.value = after; result.references = nextReferences;
        result.selectionStart = selectionStart <= range.start ? selectionStart : selectionStart < range.end ? range.start : selectionStart - (range.end - range.start);
        result.selectionEnd = selectionEnd <= range.start ? selectionEnd : selectionEnd < range.end ? range.start : selectionEnd - (range.end - range.start);
        result.commandRange = undefined;
      },
    };
    return result;
  }
  async function chooseCommand(command: SlashCommand) {
    const source = origin(), provider = live.current.collaboration, entry = activePalette;
    if (!entry || !source || !provider || pending || !getSlashCommands(provider.commandContext).some(item => item.id === command.id)) return;
    const result = entry.mode === 'typed' ? parseComposerCommand(['task', 'decision', 'groups'].includes(command.id) ? people.text : commandText, provider.commandContext, command.id)
      : { status: 'ready' as const, intent: { kind: 'open' as const, action: command.id } };
    if (result.status !== 'ready' || !result.intent) { setError('error' in result && result.error || t('Choose a command and complete its arguments.')); return; }
    if (['task', 'decision'].includes(command.id) && source.personIds.length > 1) { setError(t('Choose one person to own this work. Other people can be mentioned in its context.')); return; }
    if (['task', 'decision'].includes(command.id) && source.personIds.length && result.intent.kind === 'open') { setError(t('Give this work a title before choosing its owner.')); return; }
    if (result.intent.kind === 'groups' && result.intent.memberText.trim()) { setError(t('Choose group members from the @ suggestions, or add them in the group form.')); return; }
    const request = ++sequence.current;
    setError(''); setPending(true);
    try {
      const dispatch = provider.onCommand(command, result.intent, source);
      const accepted = dispatch && typeof (dispatch as Promise<unknown>).then === 'function' ? await dispatch : dispatch;
      if (request !== sequence.current || !source.isCurrent()) return;
      if (accepted !== false) {
        recordCommandChoice(suggestionScope, command.id);
        if (consumeCommands && entry.mode === 'typed') (source as CollaborationOrigin & { consume?: () => void }).consume?.();
        closePalette(); picker.close();
      }
      else if (visible(input.current)) input.current.focus({ preventScroll: true });
    } catch {
      if (request === sequence.current && source.isCurrent()) setError(t('This feature could not be opened. Your text is unchanged.'));
    } finally { if (request === sequence.current) setPending(false); }
  }
  function chooseReference(candidate: ReferenceCandidate) {
    if (!picker.open || !picker.query || !visible(input.current)) return;
    try {
      const preview = insertReference(value, references, picker.query, candidate);
      if (preview.text.length > (attributes.maxLength ?? 10000)) { setError(t('This reference exceeds the field’s text limit.')); return; }
      picker.choose(candidate); setError('');
    } catch { setError(t('This reference choice is no longer current.')); }
  }
  function browse() {
    if (!commandsEnabled || pending || !visible(input.current)) return;
    picker.close(); dismissed.current = ''; setError(''); setSelected(null);
    setPalette({ identity, value, mode: 'browse', query: '' });
  }
  function mention() {
    const field = input.current;
    if (!referencesEnabled || !visible(field)) return;
    const start = field.selectionStart ?? value.length, end = field.selectionEnd ?? start;
    const marker = start && !/\s/.test(value[start - 1]) ? ' @' : '@';
    const text = value.slice(0, start) + marker + value.slice(end);
    if (text.length > (attributes.maxLength ?? 10000)) return;
    const nextReferences = reconcileReferences(value, text, references, { start, end });
    onValueChange(text, nextReferences); setPalette(null);
    picker.update(text, start + marker.length, nextReferences, true);
    requestAnimationFrame(() => {
      if (live.current.identity !== identity || input.current !== field || field.value !== text || !visible(field)) return;
      field.focus({ preventScroll: true }); field.setSelectionRange(start + marker.length, start + marker.length);
    });
  }

  useEffect(() => { ++sequence.current; setPalette(null); setPending(false); setError(''); dismissed.current = ''; }, [identity]);
  useEffect(() => {
    const clear = () => { ++sequence.current; setPalette(null); setPending(false); setError(''); };
    window.addEventListener('freecollab:auth-changed', clear); window.addEventListener('freecollab:workspace-changed', clear);
    return () => { ++sequence.current; window.removeEventListener('freecollab:auth-changed', clear); window.removeEventListener('freecollab:workspace-changed', clear); };
  }, []);
  useEffect(() => {
    if (!open) return;
    const outside = (event: Event) => {
      // A submit button must not turn an active command into a published
      // comment merely because pointerdown/focus happens before submit.
      if (event.target instanceof Node && isSubmissionControl(event.target)) return;
      if (!root.current?.contains(event.target as Node)) closePalette(false, event.type === 'pointerdown');
    };
    const preventSubmit = (event: Event) => { if (event.target === input.current?.form && formBlocked.current && visible(input.current)) { event.preventDefault(); event.stopImmediatePropagation(); } };
    document.addEventListener('pointerdown', outside, true); document.addEventListener('focusin', outside);
    document.addEventListener('submit', preventSubmit, true);
    return () => { document.removeEventListener('pointerdown', outside, true); document.removeEventListener('focusin', outside); document.removeEventListener('submit', preventSubmit, true); };
  }, [open, identity]);

  useLayoutEffect(() => {
    if (!open) { setPlacement(null); return; }
    const field = input.current;
    if (!field) return;
    const measure = () => {
      if (!visible(field)) { setPalette(null); picker.close(); return; }
      const box = field.getBoundingClientRect(), viewport = window.visualViewport;
      const top = viewport?.offsetTop || 0, left = viewport?.offsetLeft || 0;
      const width = viewport?.width || window.innerWidth, height = viewport?.height || window.innerHeight;
      const caretTop = Math.max(top + 8, Math.min(box.bottom, top + height - 8));
      const above = Math.max(0, caretTop - top - 16), below = Math.max(0, top + height - caretTop - 16);
      const menuWidth = Math.max(0, Math.min(420, Math.max(260, box.width), width - 16));
      // A transformed modal is the containing block for its fixed descendants.
      // Measure its zero point independently. Reading the popup's old frame
      // together with its new inline position can otherwise double offsets
      // during nested modal layout/resize updates.
      const frame = fixedOrigin.current?.getBoundingClientRect();
      const scaleX = frame?.width || 1, scaleY = frame?.height || 1;
      const offsetX = frame?.left || 0, offsetY = frame?.top || 0;
      const placement: Placement = { left: (Math.max(left + 8, Math.min(box.left, left + width - menuWidth - 8)) - offsetX) / scaleX,
        top: (caretTop - offsetY) / scaleY, width: menuWidth / scaleX, height: (above >= 180 || above >= below ? above : below) / scaleY, below: above < 180 && below > above };
      setPlacement(previous => previous && Object.keys(placement).every(key => previous[key as keyof Placement] === placement[key as keyof Placement]) ? previous : placement);
    };
    measure();
    const resize = new ResizeObserver(measure); resize.observe(field);
    window.addEventListener('resize', measure); document.addEventListener('scroll', measure, true);
    window.visualViewport?.addEventListener('resize', measure); window.visualViewport?.addEventListener('scroll', measure);
    return () => { resize.disconnect(); window.removeEventListener('resize', measure); document.removeEventListener('scroll', measure, true); window.visualViewport?.removeEventListener('resize', measure); window.visualViewport?.removeEventListener('scroll', measure); };
  }, [open, identity, value]);

  const handlers = {
    onChange: ((event) => {
      const text = event.currentTarget.value;
      const nextReferences = reconcileReferences(value, text, references, edit.current); edit.current = undefined;
      onValueChange(text, nextReferences);
      if (!composing.current) { picker.update(text, event.currentTarget.selectionStart ?? text.length, nextReferences, true); updateCommand(text, event.currentTarget.selectionStart ?? text.length, true); }
      onChange?.(event);
    }) as ChangeEventHandler<CollaborationInputElement>,
    onBeforeInput: ((event) => { edit.current = { start: event.currentTarget.selectionStart ?? 0, end: event.currentTarget.selectionEnd ?? 0 }; onBeforeInput?.(event); }) as FormEventHandler<CollaborationInputElement>,
    onKeyDown: ((event) => {
      if (event.nativeEvent.isComposing || event.keyCode === 229 || composing.current) { onKeyDown?.(event); return; }
      if (picker.open && event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); event.stopPropagation(); const candidate = picker.candidates[picker.activeIndex]; if (candidate) chooseReference(candidate); return; }
      if (picker.handleKeyDown(event)) return;
      if (activePalette) {
        if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closePalette(true, true); return; }
        if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) { event.preventDefault(); const next = event.key === 'Home' ? 0 : event.key === 'End' ? commands.length - 1 : nextCommandIndex(activeIndex, event.key === 'ArrowDown' ? 1 : -1, commands.length); setSelected(commands[next]?.id || null); return; }
        if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); event.stopPropagation(); const command = commands[activeIndex]; if (command) void chooseCommand(command); return; }
        if (event.key === 'Tab') closePalette(false, true);
      }
      onKeyDown?.(event);
    }) as KeyboardEventHandler<CollaborationInputElement>,
    onSelect: ((event) => { if (!composing.current && document.activeElement === event.currentTarget) { picker.update(value, event.currentTarget.selectionStart ?? value.length, references); updateCommand(value, event.currentTarget.selectionStart ?? value.length); } onSelect?.(event); }) as ReactEventHandler<CollaborationInputElement>,
    onClick: ((event) => { if (!composing.current) { picker.update(value, event.currentTarget.selectionStart ?? value.length, references); updateCommand(value, event.currentTarget.selectionStart ?? value.length); } onClick?.(event); }) as MouseEventHandler<CollaborationInputElement>,
    onKeyUp: ((event) => { if (['ArrowLeft', 'ArrowRight'].includes(event.key) && !composing.current) { picker.update(value, event.currentTarget.selectionStart ?? value.length, references); updateCommand(value, event.currentTarget.selectionStart ?? value.length); } onKeyUp?.(event); }) as KeyboardEventHandler<CollaborationInputElement>,
    onCompositionStart: ((event) => { composing.current = true; setPalette(null); picker.close(); onCompositionStart?.(event); }) as CompositionEventHandler<CollaborationInputElement>,
    onCompositionEnd: ((event) => { composing.current = false; picker.update(event.currentTarget.value, event.currentTarget.selectionStart ?? value.length, references, true); updateCommand(event.currentTarget.value, event.currentTarget.selectionStart ?? value.length, true); onCompositionEnd?.(event); }) as CompositionEventHandler<CollaborationInputElement>,
  };
  const activeOption = picker.open ? picker.candidates[picker.activeIndex] ? referenceOptionId(picker.id, picker.candidates[picker.activeIndex]) : undefined
    : activePalette?.mode === 'typed' && commands[activeIndex] ? id + '-' + commands[activeIndex].id : undefined;
  const common = { ...attributes, value, ...handlers, ref: bindInput,
    role: picker.open || activePalette?.mode === 'typed' ? 'combobox' : attributes.role,
    'aria-autocomplete': picker.open || activePalette?.mode === 'typed' ? 'list' as const : attributes['aria-autocomplete'],
    'aria-expanded': open ? true : undefined, 'aria-controls': picker.open ? picker.id : activePalette ? id : undefined,
    'aria-activedescendant': activeOption, 'data-collaboration-field': true };
  const popupStyle = placement ? { left: placement.left, top: placement.top, width: placement.width, '--fc-collaboration-popup-height': placement.height + 'px' } as CSSProperties
    : { left: 0, top: 0, width: 1, visibility: 'hidden' } as CSSProperties;
  return <div ref={root} className={`fc-collaboration-input ${containerClassName}`}>
    {as === 'input' ? <input {...common as InputHTMLAttributes<HTMLInputElement>} ref={bindInput} /> : <textarea {...common as TextareaHTMLAttributes<HTMLTextAreaElement>} ref={bindInput} />}
    {open && <span ref={fixedOrigin} className="fc-collaboration-fixed-origin" aria-hidden="true" />}
    {open && <div className={`fc-collaboration-popup${placement?.below ? ' is-below' : ''}${placement && placement.height < 220 ? ' is-compact' : ''}`} style={popupStyle}>
      {picker.open ? <MentionReferences id={picker.id} candidates={picker.candidates} activeIndex={picker.activeIndex} onActiveIndex={picker.setActiveIndex}
        onSelect={chooseReference} onClose={() => { picker.close(); if (visible(input.current)) input.current.focus({ preventScroll: true }); }} query={picker.query?.query || ''} loading={picker.loading} error={picker.error} />
        : activePalette && <SlashCommands contextual id={id} commands={commands} activeIndex={activeIndex} onActiveIndex={index => setSelected(commands[index]?.id || null)}
          onSelect={command => void chooseCommand(command)} onClose={() => closePalette(true, true)} query={parsed?.query ?? activePalette.query}
          usage={parsed?.usage} argumentPreview={parsed?.arguments} error={error || parsed?.error}
          onQueryChange={activePalette.mode === 'browse' ? query => { setPalette(previous => previous ? { ...previous, query } : null); setSelected(null); setError(''); } : undefined}
          onKeyDown={handlers.onKeyDown} />}
      {pending && <span role="status" className="fc-collaboration-status">{t('Opening feature…')}</span>}
    </div>}
    {toolbar && available && <div className="fc-collaboration-tools" role="group" aria-label={t('Writing tools')}>
      {allowCommands && <button type="button" aria-label={t('Browse collaboration features')} aria-haspopup="listbox" aria-expanded={!!activePalette} onMouseDown={event => event.preventDefault()} onClick={browse}><span aria-hidden="true">/</span><span>{t('All features')}</span></button>}
      {allowReferences && <button type="button" aria-label={t('Insert a reference')} aria-haspopup="listbox" aria-expanded={picker.open} onMouseDown={event => event.preventDefault()} onClick={mention}>@</button>}
    </div>}
    {available && allowAI && <WritingCopilot value={value} identity={identity} disabled={!!activePalette || picker.open || pending || composing.current} maxLength={attributes.maxLength ?? 10000} capture={origin}
      apply={(source, selection, text) => {
        const field = input.current;
        if (!source.isCurrent() || !visible(field) || field.disabled || field.readOnly || field.value !== selection.value) return false;
        const next = writingSuggestion(selection, text, source.references, attributes.maxLength ?? 10000);
        if (!next) return false;
        const expectedIdentity = live.current.identity;
        let moved = false;
        const newerIntent = () => { moved = true; };
        document.addEventListener('pointerdown', newerIntent, true); document.addEventListener('keydown', newerIntent, true);
        live.current.onValueChange(next.value, next.references);
        requestAnimationFrame(() => {
          document.removeEventListener('pointerdown', newerIntent, true); document.removeEventListener('keydown', newerIntent, true);
          if (!moved && live.current.identity === expectedIdentity && input.current === field && visible(field) && field.value === next.value
            && api.getAccessToken() === source.token && api.getCurrentUserId() === source.actorId) { field.focus({ preventScroll: true }); field.setSelectionRange(next.caret, next.caret); }
        });
        return true;
      }} />}
    {error && picker.open && <p role="alert" className="fc-collaboration-error">{error}</p>}
  </div>;
});
