import { useEffect, useId, useRef, useState } from 'react';
import { Loader2, Sparkles, X } from 'lucide-react';
import { api } from '../lib/api';
import { copilotApi, copilotChannelIsSecure, loadCopilotReferencedChoices, prepareCopilotSource, type ContextChoice, type CopilotSource, type CopilotState } from '../lib/copilot-api';
import { copilotProviderHost } from '../lib/copilot-intents';
import { useI18n } from '../i18n';
import '../styles/contextual-copilot.css';

export interface ContextualCopilotSource { kind: 'message' | 'shared_document'; id: string }
export interface ContextualCopilotAction { id: string; label: string; prompt: string }
export interface ContextualCopilotActivation { actionId: string; key: string | number }
export interface ContextualCopilotProps {
  workspaceId: string; channelId: string; contextKey: string; contextLabel: string;
  actions: ContextualCopilotAction[]; sourceRefs?: ContextualCopilotSource[];
  onApply?: (text: string, originalContextKey: string) => boolean | void;
  onConfigure?: () => void; applyLabel?: string; applyHint?: string; maxApplyLength?: number; applyActionIds?: string[];
  /** A concrete action label replaces the generic help entry. */
  triggerLabel?: string;
  /** External context menus can open the panel beside its content instead. */
  hideTrigger?: boolean;
  /** A new key opens this action for review only; it never sends or applies. */
  activation?: ContextualCopilotActivation;
  onClose?: () => void;
}

/** An explicit action must resolve once; never silently substitute another. */
export function selectContextualAction(actions: ContextualCopilotAction[], requested = actions[0]?.id): string {
  const matches = actions.filter(action => action.id === requested);
  return requested && matches.length === 1 ? requested : '';
}

/** Resolve exactly the reviewed references. Never substitute a missing source. */
export function selectContextualSources(choices: ContextChoice[], refs: ContextualCopilotSource[], channel: string): ContextChoice[] {
  if (refs.length > 8 || new Set(refs.map(ref => ref.kind + ':' + ref.id)).size !== refs.length) throw new Error('Choose up to eight unique sources.');
  return refs.map(ref => {
    if (!['message', 'shared_document'].includes(ref.kind) || !ref.id || ref.id.length > 1024) throw new Error('This AI source is unavailable.');
    const matches = choices.filter(choice => choice.source.kind === ref.kind && choice.source.id === ref.id);
    const choice = matches[0];
    if (matches.length !== 1 || ref.kind === 'message' && (!choice.message || choice.message.channel_id !== channel || choice.message.deleted || choice.message.locked)
      || ref.kind === 'shared_document' && (!choice.document || choice.document.channel_id !== channel || choice.document.archived || choice.document.resolution_error)) throw new Error('This AI source is unavailable.');
    return choice;
  });
}
export function contextualSourceSnapshot(choices: ContextChoice[]): string {
  return JSON.stringify(choices.map(choice => ({ source: choice.source, preview: choice.preview, message: choice.message, document: choice.document })));
}
export function contextualReviewedSnapshot(choices: ContextChoice[], sources: CopilotSource[]): string {
  return JSON.stringify([contextualSourceSnapshot(choices), sources]);
}
export function sameContextualProvider(left: CopilotState, right: CopilotState): boolean {
  return left.config_revision === right.config_revision && left.provider?.endpoint === right.provider?.endpoint && left.provider?.model === right.provider?.model;
}
/** Count the server's selected-context envelope, in Unicode characters. */
export function contextualContextCharacters(choices: ContextChoice[], sources: CopilotSource[]): number {
  if (choices.length !== sources.length) return Infinity;
  let count = 0;
  for (let index = 0; index < sources.length; index++) {
    const source = sources[index], choice = choices[index];
    if (source.id !== choice.source.id || source.kind !== choice.source.kind) return Infinity;
    const text = source.kind === 'shared_document' ? source.text : source.kind === 'message' ? choice.message?.content : undefined;
    if (typeof text !== 'string') return Infinity;
    count += Array.from('[S' + (index + 1) + '] ' + source.kind + '\n' + text).length + (index ? 2 : 0);
  }
  return count;
}

/** In-place, explicitly disclosed AI help; the parent applies reviewed text to its editor. */
export function ContextualCopilot({ workspaceId, channelId, contextKey, contextLabel, actions, sourceRefs = [], onApply, onConfigure, applyLabel = 'Use suggestion', applyHint = 'Uses reviewed text only after your approval.', maxApplyLength = 10000, applyActionIds, triggerLabel, hideTrigger = false, activation, onClose }: ContextualCopilotProps) {
  const { t } = useI18n(), uid = useId();
  const activationKey = activation ? JSON.stringify([activation.actionId, activation.key]) : '';
  const activationRequestKey = activation ? JSON.stringify(activation.key) : '';
  const requestedAction = selectContextualAction(actions, activation?.actionId);
  const identity = JSON.stringify([workspaceId, channelId, contextKey, sourceRefs, actions, applyActionIds, maxApplyLength, activationKey]);
  const live = useRef({ identity, token: api.getAccessToken() }); live.current = { identity, token: api.getAccessToken() };
  const closeCallback = useRef(onClose); closeCallback.current = onClose;
  const triggerRef = useRef<HTMLButtonElement>(null), cardRef = useRef<HTMLElement>(null), focusedActivation = useRef('');
  const consumedActivation = useRef(activationRequestKey);
  const mounted = useRef(false), generation = useRef(0), locked = useRef(false);
  const [open, setOpen] = useState(!!activation && !!requestedAction), [status, setStatus] = useState<CopilotState | null>(null), [secure, setSecure] = useState(false);
  const [choices, setChoices] = useState<ContextChoice[]>([]), [actionId, setActionId] = useState(requestedAction);
  const [reviewedSources, setReviewedSources] = useState<CopilotSource[]>([]);
  const [disclose, setDisclose] = useState(false), [discloseSecure, setDiscloseSecure] = useState(false);
  const [busy, setBusy] = useState(''), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [result, setResult] = useState<{ text: string; context: string; sources: string; config: CopilotState; action: string; identity: string; citations: string[] } | null>(null);
  const valid = (version: number, captured = identity, token = live.current.token) => mounted.current && generation.current === version && live.current.identity === captured && token === api.getAccessToken();
  async function readSources() {
    if (!sourceRefs.length) return [];
    return selectContextualSources((await loadCopilotReferencedChoices(channelId, sourceRefs)).choices, sourceRefs, channelId);
  }
  async function prepareSelected(selected: ContextChoice[], encrypted: boolean) {
    const prepared = await Promise.all(selected.map(choice => prepareCopilotSource(choice, channelId, encrypted)));
    if (prepared.some((source, index) => selected[index].document && source.revision !== selected[index].document!.version)) throw new Error('The selected context changed. Close and reopen this suggestion to review it.');
    return prepared;
  }
  useEffect(() => {
    mounted.current = true;
    const changed = () => { generation.current++; locked.current = false; setOpen(false); setStatus(null); setChoices([]); setReviewedSources([]); setResult(null); setDisclose(false); setDiscloseSecure(false); setError(''); setNotice(''); setBusy(''); };
    window.addEventListener('freecollab:auth-changed', changed);
    return () => { mounted.current = false; generation.current++; window.removeEventListener('freecollab:auth-changed', changed); };
  }, []);
  useEffect(() => {
    if (!activation) return;
    if (!requestedAction) {
      consumedActivation.current = activationRequestKey;
      setActionId(''); setOpen(false);
      return;
    }
    if (consumedActivation.current === activationRequestKey) return;
    consumedActivation.current = activationRequestKey;
    setActionId(requestedAction);
    setOpen(true);
  }, [activationKey, requestedAction]);
  useEffect(() => {
    if (!open || !activation || !requestedAction || focusedActivation.current === activationRequestKey || !cardRef.current) return;
    focusedActivation.current = activationRequestKey;
    cardRef.current.focus({ preventScroll: true });
  }, [open, activationKey, requestedAction]);
  useEffect(() => {
    const version = ++generation.current, token = api.getAccessToken();
    setStatus(null); setChoices([]); setReviewedSources([]); setResult(null); setDisclose(false); setDiscloseSecure(false); setError(''); setNotice(''); setActionId(requestedAction); locked.current = false;
    if (!open || activation && !requestedAction) { setBusy(''); return; }
    setBusy('Checking AI access…'); locked.current = true;
    void Promise.all([copilotApi.status(workspaceId), copilotChannelIsSecure(channelId), readSources()]).then(async ([next, encrypted, selected]) => {
      if (!valid(version, identity, token)) return;
      const prepared = await prepareSelected(selected, encrypted);
      if (valid(version, identity, token)) { setStatus(next); setSecure(encrypted); setChoices(selected); setReviewedSources(prepared); }
    }).catch(failure => { if (valid(version, identity, token)) setError(failure instanceof Error ? failure.message : 'AI context could not be loaded.'); }).finally(() => { if (valid(version, identity, token)) { locked.current = false; setBusy(''); } });
  }, [identity, open]);
  async function perform(label: string, task: (version: number, token: string | null) => Promise<void>) {
    if (locked.current) return;
    const version = generation.current, token = api.getAccessToken();
    if (!valid(version, identity, token)) return;
    locked.current = true; setBusy(label); setError(''); setNotice('');
    try { await task(version, token); }
    catch (failure) { if (valid(version, identity, token)) setError(failure instanceof Error ? failure.message : 'AI help could not be completed.'); }
    finally { if (valid(version, identity, token)) { locked.current = false; setBusy(''); } }
  }
  const selectedAction = actions.find(action => action.id === actionId);
  const promptFits = !!selectedAction?.prompt.trim() && selectedAction.prompt.length <= 3900;
  const contextFits = contextualContextCharacters(choices, reviewedSources) <= 24000;
  const host = copilotProviderHost(status?.provider?.endpoint) || t('Configured provider');
  const available = status?.globally_enabled && status.configured && status.consented;
  const currentResult = result?.identity === identity && result.context === contextKey && result.action === actionId ? result : null;
  const entryLabel = triggerLabel || actions[0]?.label || 'Review AI actions';
  const actionLabel = selectedAction?.label || 'Review AI actions';
  const close = () => { setOpen(false); triggerRef.current?.focus({ preventScroll: true }); closeCallback.current?.(); };
  async function recheck(reviewed: CopilotState, selectedSnapshot: string) {
    const [current, selected, encrypted] = await Promise.all([copilotApi.status(workspaceId), readSources(), copilotChannelIsSecure(channelId)]);
    if (!current.globally_enabled || !current.configured || !current.consented || !sameContextualProvider(reviewed, current)) throw new Error('AI configuration or consent changed. Close and reopen this suggestion to review it.');
    const prepared = await prepareSelected(selected, encrypted);
    if (encrypted !== secure || contextualReviewedSnapshot(selected, prepared) !== selectedSnapshot) throw new Error('The selected context changed. Close and reopen this suggestion to review it.');
  }
  async function send(version: number, token: string | null) {
    if (!status || !available || !selectedAction || !disclose || secure && !discloseSecure) return;
    if (!contextFits) throw new Error('Select fewer sources. AI context is limited to 24,000 characters; nothing was sent.');
    const prompt = selectedAction.prompt, agent = status.agents.find(value => value.tools.length === 0) || status.agents[0];
    if (!agent || !prompt.trim() || prompt.length > 3900) throw new Error('Shorten the selected text before asking AI.');
    const snapshot = contextualReviewedSnapshot(choices, reviewedSources);
    await recheck(status, snapshot);
    const sources = reviewedSources;
    if (!valid(version, identity, token)) return;
    let conversation: string | null = null;
    try {
      const chat = await copilotApi.create(workspaceId, channelId); conversation = chat.id;
      if (!valid(version, identity, token)) return;
      const answer = await copilotApi.send(workspaceId, chat.id, { request_id: crypto.randomUUID(), expected_revision: chat.revision, expected_config_revision: status.config_revision, prompt: '@' + agent.name + ' ' + prompt, sources, disclose_to_provider: true, disclose_decrypted_content: discloseSecure });
      if (!valid(version, identity, token)) return;
      await recheck(status, snapshot);
      if (!valid(version, identity, token)) return;
      if (!answer.text.trim() || answer.proposals.length) throw new Error('AI returned an action proposal instead of text. Nothing was applied.');
      setResult({ text: answer.text, context: contextKey, sources: snapshot, config: status, action: selectedAction.id, identity, citations: choices.map(choice => choice.label) });
    } finally {
      if (conversation && token === api.getAccessToken()) {
        try { await copilotApi.reset(workspaceId, conversation); }
        catch { if (valid(version, identity, token)) setNotice('The private AI request history could not be cleared. You can reset it in AI settings.'); }
      }
      if (valid(version, identity, token)) { setDisclose(false); setDiscloseSecure(false); }
    }
  }
  return <div className="fc-contextual-copilot">
    {!hideTrigger && <button ref={triggerRef} type="button" className="fc-contextual-copilot-trigger" disabled={!selectContextualAction(actions) || !!activation && !requestedAction} aria-expanded={open} aria-controls={open ? uid + '-card' : undefined} aria-label={t('{action} for {context}', { action: t(entryLabel), context: contextLabel })} onClick={() => { if (!locked.current) { if (open) close(); else setOpen(true); } }}><Sparkles size={14} aria-hidden="true" />{t(entryLabel)}</button>}
    {open && <section ref={cardRef} id={uid + '-card'} className="fc-contextual-copilot-card" tabIndex={-1} aria-label={t('AI help for {context}', { context: contextLabel })} aria-busy={!!busy} onKeyDown={event => { if (event.key === 'Escape' && !event.defaultPrevented && !event.nativeEvent.isComposing && event.keyCode !== 229) { event.preventDefault(); event.stopPropagation(); if (!locked.current) close(); } }}>
      <header><div><span className="fc-contextual-copilot-context">{contextLabel}</span><h3>{t(actionLabel)}</h3></div><button type="button" disabled={!!busy} aria-label={t('Close AI help')} onClick={close}><X size={15} /></button></header>
      {error && <p role="alert">{t(error)}</p>}{notice && <p role="status">{t(notice)}</p>}
      {status && (!status.globally_enabled || !status.configured) && <p>{t('A workspace owner must enable and connect an AI provider in Connection setup.')}{onConfigure && <button type="button" onClick={onConfigure}>{t('Open connection setup')}</button>}</p>}
      {status?.provider && <>
        <p className="fc-contextual-copilot-destination"><strong>{host}</strong> · {status.provider.model}</p>
        <label><input type="checkbox" checked={status.consented} disabled={!!busy || !status.globally_enabled || !status.configured} onChange={event => { const enabled = event.target.checked; void perform('Updating AI consent…', async (version, token) => { await copilotApi.consent(workspaceId, status.config_revision, enabled); const next = await copilotApi.status(workspaceId); if (valid(version, identity, token)) { setStatus(next); setResult(null); setDisclose(false); setDiscloseSecure(false); } }); }} />{t('I opt in to this provider configuration for my account.')}</label>
      </>}
      {available && <fieldset disabled={!!busy}>
        <div className="fc-contextual-copilot-actions" role="group" aria-label={t('AI actions')}>{actions.map(action => <button key={action.id} type="button" aria-pressed={action.id === actionId} onClick={() => { setActionId(action.id); setResult(null); setDisclose(false); setDiscloseSecure(false); }}>{t(action.label)}</button>)}</div>
        <details><summary>{t('Review this request')}</summary><p>{selectedAction?.prompt}</p></details>
        {!!choices.length && <details><summary>{t('Review {count} selected sources', { count: choices.length })}</summary>{choices.map((choice, index) => <div key={choice.key}><strong>{choice.label}</strong><p>{reviewedSources[index]?.text ?? choice.preview}</p></div>)}</details>}
        {choices.some(choice => choice.document) && <p>{t('Selected documents include up to 12,000 characters of verified text.')}</p>}
        {!promptFits && <p>{t('Shorten the selected text before asking AI.')}</p>}
        {!contextFits && <p>{t('Select fewer sources. AI context is limited to 24,000 characters; nothing was sent.')}</p>}
        <aside id={uid + '-disclosure'}><p>{t('Your request and {count} selected sources will be sent to {provider} using {model}.', { count: choices.length, provider: host, model: status.provider?.model || '' })}</p><p>{t('API use may incur charges. A chat subscription is separate. The provider’s retention policy applies. AI answers can be wrong.')}</p></aside>
        <label><input type="checkbox" checked={disclose} onChange={event => setDisclose(event.target.checked)} />{t('Send this request and the selected context to this provider.')}</label>
        {secure && <label className="fc-contextual-copilot-secure"><input type="checkbox" checked={discloseSecure} onChange={event => setDiscloseSecure(event.target.checked)} />{t('I explicitly disclose selected decrypted content through the server relay to the AI provider. These copies leave end-to-end encryption. Secure exchanges are not stored as plaintext history.')}</label>}
        <button type="button" className="fc-contextual-copilot-send" aria-describedby={uid + '-disclosure'} disabled={!promptFits || !contextFits || !disclose || secure && !discloseSecure} onClick={() => void perform('Asking your AI provider…', send)}>{t('Send to provider')}</button>
      </fieldset>}
      {currentResult && <div className="fc-contextual-copilot-result"><strong>{t('Review the suggestion')}</strong><p>{currentResult.text}</p>{!!currentResult.citations.length && <div className="fc-contextual-copilot-citations"><span>{t('Based on the reviewed sources')}</span><ol>{currentResult.citations.map((label, index) => <li key={index}><span>[S{index + 1}]</span> {label}</li>)}</ol></div>}{onApply && (!applyActionIds || applyActionIds.includes(currentResult.action)) && <button type="button" disabled={!!busy || currentResult.text.length > maxApplyLength} onClick={() => void perform('Checking the current context…', async (version, token) => { if (currentResult.context !== contextKey || currentResult.action !== actionId) return; await recheck(currentResult.config, currentResult.sources); if (valid(version, identity, token) && onApply(currentResult.text, currentResult.context) === false) throw new Error('The target changed. Review a new suggestion.'); })}>{t(applyLabel)}</button>}{onApply && (!applyActionIds || applyActionIds.includes(currentResult.action)) && currentResult.text.length > maxApplyLength && <p>{t('The suggestion is too long for this field. Nothing was changed.')}</p>}<small>{t(applyHint)}</small></div>}
      {busy && <p role="status"><Loader2 size={14} aria-hidden="true" />{t(busy)}</p>}
    </section>}
  </div>;
}
