import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react';
import { t } from '../i18n';
import { api } from '../lib/api';
import { useCollaborationContext, type CollaborationInputElement } from '../lib/collaboration-context';
import type { EncryptedReference, EncryptedWorkSnapshot } from '../lib/encrypted-work-api';
import { CollaborationInput } from './CollaborationInput';
import { rebaseEncryptedReferences } from '../lib/encrypted-work-references';

export interface EncryptedReferenceCandidate { kind: EncryptedReference['kind']; id: string; label: string; resource_kind?: string }
export function encryptedReferenceCandidates(view: EncryptedWorkSnapshot): EncryptedReferenceCandidate[] {
  return [...view.context.members.map(member => ({ kind: 'participant' as const, id: member.matrix_user_id, label: member.name })),
    ...[...view.records, ...view.legacyRecords].filter(row => !row.invalid.length && !row.conflicts && !row.selected.archived && row.kind !== 'organization')
      .map(row => ({ kind: 'resource' as const, id: row.id, label: row.selected.fields.title, resource_kind: row.kind }))];
}
export const reconcileEncryptedReferences = rebaseEncryptedReferences;
interface Props {
  channelId: string;
  id?: string; hideLabel?: boolean; rows?: number; className?: string;
  contextId: string; value: string; references?: EncryptedReference[]; candidates: EncryptedReferenceCandidate[];
  onChange: (value: string, references: EncryptedReference[]) => void;
  label: string; disabled?: boolean; multiline?: boolean; maxLength?: number; required?: boolean;
}
/** Verified Matrix identities stay separate from installation-local mention IDs. */
export function EncryptedWorkInput({ channelId, id: explicitId, hideLabel, rows, className, contextId, value, references = [], candidates, onChange, label, disabled, multiline = true, maxLength = 12000, required }: Props) {
  const context = useCollaborationContext(), input = useRef<CollaborationInputElement | null>(null), root = useRef<HTMLDivElement>(null);
  const generatedId = useId(), id = explicitId || generatedId, [caret, setCaret] = useState(value.length), [selected, setSelected] = useState(0), [dismissed, setDismissed] = useState('');
  const composing = useRef(false), owner = api.getAccessToken(), actor = api.getCurrentUserId();
  const identity = JSON.stringify([api.getCurrentUserId(), owner, channelId, contextId, context?.channelId, context?.workspaceId]);
  const live = useRef({ identity, value }); live.current = { identity, value };
  const prefix = value.slice(0, caret), match = !disabled && !composing.current ? /(?:^|[\s([{"'])@([^@\n]{0,80})$/u.exec(prefix) : null;
  const query = match?.[1] || '', signature = identity + ':' + value + ':' + caret;
  const options = match && dismissed !== signature ? candidates.filter(row => (row.label + ' ' + row.id).toLocaleLowerCase().includes(query.toLocaleLowerCase())).slice(0, 12) : [];
  const open = !!match && dismissed !== signature && !!options.length;
  const active = Math.min(selected, Math.max(0, options.length - 1));
  const measure = () => { if (input.current) setCaret(input.current.selectionStart ?? input.current.value.length); };
  const choose = (candidate: EncryptedReferenceCandidate) => {
    const field = input.current;
    if (!field || disabled || owner !== api.getAccessToken() || actor !== api.getCurrentUserId() || live.current.identity !== identity || field.value !== value || !field.isConnected || !match) return;
    const start = caret - query.length - 1, insertion = '@' + candidate.label;
    const next = value.slice(0, start) + insertion + ' ' + value.slice(caret);
    if (next.length > maxLength) return;
    const adjusted = reconcileEncryptedReferences(value, next, references).filter(row => row.end <= start || row.start >= start + insertion.length + 1);
    adjusted.push({ ...candidate, start, end: start + insertion.length }); adjusted.sort((a, b) => a.start - b.start);
    onChange(next, adjusted); setDismissed(identity + ':' + next + ':' + (start + insertion.length + 1)); setSelected(0);
    requestAnimationFrame(() => {
      if (field.isConnected && live.current.identity === identity && field.value === next && document.activeElement === field && !field.closest('[hidden],[inert]')) {
        field.focus({ preventScroll: true }); field.setSelectionRange(start + insertion.length + 1, start + insertion.length + 1); measure();
      }
    });
  };
  const keys = (event: KeyboardEvent<CollaborationInputElement>) => {
    if (!open || event.nativeEvent.isComposing || composing.current || event.keyCode === 229) return;
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); setDismissed(signature); }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); event.stopPropagation(); setSelected(index => (index + (event.key === 'ArrowDown' ? 1 : options.length - 1)) % options.length); }
    if (event.key === 'Enter' || event.key === 'Tab') { event.preventDefault(); event.stopPropagation(); choose(options[active]); }
  };
  useEffect(() => {
    const field = input.current, form = field?.form;
    if (!form || !open) return;
    const block = (event: Event) => { event.preventDefault(); event.stopImmediatePropagation(); };
    form.addEventListener('submit', block, true);
    return () => form.removeEventListener('submit', block, true);
  }, [open, identity]);
  useEffect(() => {
    if (!open) return;
    const leave = (event: Event) => {
      const target = event.target;
      if (!(target instanceof Element) || root.current?.contains(target)) return;
      const control = target.closest('button,input') as HTMLButtonElement | HTMLInputElement | null;
      if (control?.type === 'submit' && control.form === input.current?.form) return;
      setDismissed(signature);
    };
    document.addEventListener('pointerdown', leave, true); document.addEventListener('focusin', leave, true);
    return () => { document.removeEventListener('pointerdown', leave, true); document.removeEventListener('focusin', leave, true); };
  }, [open, signature]);
  useEffect(() => { setDismissed(''); setSelected(0); }, [identity]);
  return <div ref={root} className="fc-encrypted-work-input" data-encrypted-reference-picker={open || undefined}>
    {!hideLabel && <label htmlFor={id}>{label}</label>}
    <CollaborationInput ref={input} id={id} as={multiline ? 'textarea' : 'input'} contextId={contextId}
      value={value} allowCommands={!!context?.secure && context.channelId === channelId && context.isCurrent?.() !== false} allowReferences={false}
      aria-label={hideLabel ? label : undefined} maxLength={maxLength} required={required} disabled={disabled} className={className || 'fc-input'} rows={multiline ? rows || 4 : undefined}
      aria-expanded={open} aria-controls={open ? id + '-references' : undefined} aria-activedescendant={open ? id + '-option-' + active : undefined}
      onValueChange={next => { if (owner !== api.getAccessToken() || actor !== api.getCurrentUserId()) return; onChange(next, reconcileEncryptedReferences(value, next, references)); setCaret(input.current?.selectionStart ?? next.length); setSelected(0); setDismissed(''); }}
      onSelect={measure} onClick={measure} onKeyUp={measure} onKeyDown={keys}
      onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; measure(); }} />
    {open && <div className="fc-encrypted-reference-list" role="listbox" aria-label={t('Mention or reference')} id={id + '-references'}>{options.map((candidate, index) =>
      <button type="button" role="option" aria-selected={active === index} id={id + '-option-' + index} key={candidate.kind + ':' + (candidate.resource_kind || '') + ':' + candidate.id}
        onPointerDown={event => event.preventDefault()} onClick={() => choose(candidate)}><strong>{candidate.label}</strong><small>{candidate.kind === 'participant' ? candidate.id : t(candidate.resource_kind || 'Resource')}</small></button>)}</div>}
  </div>;
}
