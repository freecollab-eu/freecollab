import { useEffect, useId, useRef, useState } from 'react';
import { Loader2, Sparkles, X } from 'lucide-react';
import { api } from '../lib/api';
import { copilotApi, copilotChannelIsSecure, loadCopilotReferencedChoices, prepareCopilotSource, type ContextChoice, type CopilotSource, type CopilotState } from '../lib/copilot-api';
import { copilotProviderHost } from '../lib/copilot-intents';
import { aiProfileRecipient, aiRunIsActive, aiRuntimeApi, sameAIProfile, validRuntimeState, type AIModelProfile, type AIRun, type AIRuntimeState } from '../lib/ai-runtime-api';
import { useI18n } from '../i18n';
import '../styles/contextual-copilot.css';

export interface ContextualCopilotSource { kind: 'message' | 'shared_document' | 'file' | 'clip'; id: string }
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
  /** Current reader/writer and collaboration-provider lease, checked after awaits. */
  sourceCurrent?: () => boolean;
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
    if (!['message', 'shared_document', 'file', 'clip'].includes(ref.kind) || !ref.id || ref.id.length > 1024) throw new Error('This AI source is unavailable.');
    const matches = choices.filter(choice => choice.source.kind === ref.kind && choice.source.id === ref.id);
    const choice = matches[0];
    if (matches.length !== 1 || ref.kind === 'message' && (!choice.message || choice.message.channel_id !== channel || choice.message.deleted || choice.message.locked)
      || ref.kind === 'shared_document' && (!choice.document || choice.document.channel_id !== channel || choice.document.archived || choice.document.resolution_error)
      || ref.kind === 'file' && (!choice.file || choice.file.channel_id !== channel || choice.file.size > 12000 || !['text/plain', 'application/json'].includes(choice.file.content_type))
      || ref.kind === 'clip' && (!choice.clip || choice.clip.channel_id !== channel || choice.clip.resolution_error || !choice.clip.fields?.transcript)) throw new Error('This AI source is unavailable.');
    return choice;
  });
}
export function contextualSourceSnapshot(choices: ContextChoice[]): string {
  return JSON.stringify(choices.map(choice => ({ source: choice.source, preview: choice.preview, message: choice.message, document: choice.document, file: choice.file, clip: choice.clip })));
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
    const text = source.kind === 'message' ? choice.message?.content : source.text;
    if (typeof text !== 'string') return Infinity;
    count += Array.from('[S' + (index + 1) + '] ' + source.kind + '\n' + text).length + (index ? 2 : 0);
  }
  return count;
}

/** In-place, explicitly disclosed AI help; the parent applies reviewed text to its editor. */
export function ContextualCopilot({ workspaceId, channelId, contextKey, contextLabel, actions, sourceRefs = [], onApply, onConfigure, applyLabel = 'Use suggestion', applyHint = 'Uses reviewed text only after your approval.', maxApplyLength = 10000, applyActionIds, triggerLabel, hideTrigger = false, activation, onClose, sourceCurrent }: ContextualCopilotProps) {
  const { t } = useI18n(), uid = useId();
  const activationKey = activation ? JSON.stringify([activation.actionId, activation.key]) : '';
  const activationRequestKey = activation ? JSON.stringify(activation.key) : '';
  const requestedAction = selectContextualAction(actions, activation?.actionId);
  const identity = JSON.stringify([workspaceId, channelId, contextKey, sourceRefs, actions, applyActionIds, maxApplyLength, activationKey]);
  const live = useRef({ identity, token: api.getAccessToken() }); live.current = { identity, token: api.getAccessToken() };
  const sourceLease = useRef(sourceCurrent); sourceLease.current = sourceCurrent;
  const closeCallback = useRef(onClose); closeCallback.current = onClose;
  const triggerRef = useRef<HTMLButtonElement>(null), cardRef = useRef<HTMLElement>(null), focusedActivation = useRef('');
  const consumedActivation = useRef(activationRequestKey);
  const mounted = useRef(false), generation = useRef(0), locked = useRef(false);
  const requestIds = useRef(new Map<string, string>()), foregroundAttempt = useRef<number | null>(null);
  const [retryRequired, setRetryRequired] = useState(false), [retryAccepted, setRetryAccepted] = useState(false);
  const [open, setOpen] = useState(!!activation && !!requestedAction), [status, setStatus] = useState<CopilotState | null>(null), [secure, setSecure] = useState(false);
  const [choices, setChoices] = useState<ContextChoice[]>([]), [actionId, setActionId] = useState(requestedAction);
  const [reviewedSources, setReviewedSources] = useState<CopilotSource[]>([]);
  const [disclose, setDisclose] = useState(false), [discloseSecure, setDiscloseSecure] = useState(false);
  const [busy, setBusy] = useState(''), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [runtime, setRuntime] = useState<AIRuntimeState | null>(null), [profileId, setProfileId] = useState(''), [durable, setDurable] = useState(false);
  const [activeJob, setActiveJob] = useState<{ run: AIRun; profile: AIModelProfile; snapshot: string; action: string; context: string; identity: string; config: CopilotState; citations: string[] } | null>(null);
  const [result, setResult] = useState<{ text: string; context: string; sources: string; config: CopilotState; profile?: AIModelProfile; action: string; identity: string; citations: string[] } | null>(null);
  const valid = (version: number, captured = identity, token = live.current.token) => sourceLease.current?.() !== false && mounted.current && generation.current === version && live.current.identity === captured && token === api.getAccessToken();
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
    const changed = () => { generation.current++; requestIds.current.clear(); locked.current = false; setOpen(false); setStatus(null); setRuntime(null); setProfileId(''); setDurable(false); setActiveJob(null); setChoices([]); setReviewedSources([]); setResult(null); setDisclose(false); setDiscloseSecure(false); setError(''); setNotice(''); setBusy(''); setRetryRequired(false); setRetryAccepted(false); foregroundAttempt.current = null; };
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
    requestIds.current.clear(); foregroundAttempt.current = null; setRetryRequired(false); setRetryAccepted(false);
    setStatus(null); setRuntime(null); setProfileId(''); setDurable(false); setActiveJob(null); setChoices([]); setReviewedSources([]); setResult(null); setDisclose(false); setDiscloseSecure(false); setError(''); setNotice(''); setActionId(requestedAction); locked.current = false;
    if (!open || activation && !requestedAction || sourceLease.current?.() === false) { setBusy(''); return; }
    setBusy('Checking AI access…'); locked.current = true;
    void Promise.all([copilotApi.status(workspaceId), copilotChannelIsSecure(channelId), readSources(), aiRuntimeApi.status(workspaceId).catch(() => null)]).then(async ([next, encrypted, selected, models]) => {
      if (!valid(version, identity, token)) return;
      const prepared = await prepareSelected(selected, encrypted);
      if (valid(version, identity, token)) { setStatus(next); setRuntime(validRuntimeState(models) ? models : null); setSecure(encrypted); setChoices(selected); setReviewedSources(prepared); }
    }).catch(failure => { if (valid(version, identity, token)) setError(failure instanceof Error ? failure.message : 'AI context could not be loaded.'); }).finally(() => { if (valid(version, identity, token)) { locked.current = false; setBusy(''); } });
  }, [identity, open]);
  async function perform(label: string, task: (version: number, token: string | null) => Promise<void>) {
    if (locked.current) return;
    const version = generation.current, token = api.getAccessToken();
    if (!valid(version, identity, token)) return;
    locked.current = true; setBusy(label); setError(''); setNotice('');
    try { await task(version, token); }
    catch (failure) { if (valid(version, identity, token)) { setError(failure instanceof Error ? failure.message : 'AI help could not be completed.'); if (foregroundAttempt.current === version) { setRetryRequired(true); setRetryAccepted(false); setDisclose(false); setDiscloseSecure(false); } } }
    finally { if (foregroundAttempt.current === version) foregroundAttempt.current = null; if (valid(version, identity, token)) { locked.current = false; setBusy(''); } }
  }
  const selectedAction = actions.find(action => action.id === actionId);
  const promptFits = !!selectedAction?.prompt.trim() && selectedAction.prompt.length <= 3900;
  const contextFits = contextualContextCharacters(choices, reviewedSources) <= 24000;
  const profile = profileId ? runtime?.profiles.find(value => value.id === profileId) : durable && !secure ? runtime?.profiles.find(value => value.id === 'default') : undefined;
  const host = profile ? aiProfileRecipient(profile) : copilotProviderHost(status?.provider?.endpoint) || t('Configured provider');
  const model = profile?.model || status?.provider?.model || '';
  const available = profile ? runtime?.globally_enabled && profile.enabled && profile.configured && profile.consented : !profileId && !durable && status?.globally_enabled && status.configured && status.consented;
  const currentResult = result?.identity === identity && result.context === contextKey && result.action === actionId
    && (result.profile ? !!profile && sameAIProfile(result.profile, profile) : !profile) ? result : null;
  const entryLabel = triggerLabel || actions[0]?.label || 'Review AI actions';
  const actionLabel = selectedAction?.label || 'Review AI actions';
  const close = () => { setOpen(false); setActiveJob(null); triggerRef.current?.focus({ preventScroll: true }); closeCallback.current?.(); };
  async function recheck(reviewed: CopilotState, selectedSnapshot: string, reviewedProfile?: AIModelProfile) {
    const [current, selected, encrypted, models] = await Promise.all([copilotApi.status(workspaceId), readSources(), copilotChannelIsSecure(channelId), reviewedProfile ? aiRuntimeApi.status(workspaceId) : Promise.resolve(null)]);
    if (reviewedProfile) {
      const actual = models?.profiles.find(value => value.id === reviewedProfile.id);
      if (!models?.globally_enabled || !actual?.enabled || !actual.configured || !actual.consented || !sameAIProfile(reviewedProfile, actual)) throw new Error('The selected model changed. Review its recipient and consent again.');
    } else if (!current.globally_enabled || !current.configured || !current.consented || !sameContextualProvider(reviewed, current)) throw new Error('AI configuration or consent changed. Close and reopen this suggestion to review it.');
    const prepared = await prepareSelected(selected, encrypted);
    if (encrypted !== secure || contextualReviewedSnapshot(selected, prepared) !== selectedSnapshot) throw new Error('The selected context changed. Close and reopen this suggestion to review it.');
  }
  useEffect(() => {
    if (!activeJob || !aiRunIsActive(activeJob.run)) return;
    const version = generation.current, token = api.getAccessToken(), job = activeJob;
    let stopped = false, timer = 0;
    const poll = async () => {
      let finished: AIRun | null = null;
      try {
        const next = await aiRuntimeApi.run(workspaceId, job.run.id);
        if (stopped || !valid(version, job.identity, token)) return;
        if (aiRunIsActive(next)) { setActiveJob(previous => previous?.run.id === next.id ? { ...previous, run: next } : previous); return; }
        finished = next;
        if (!aiRunIsActive(next) && next.result?.text) {
          await recheck(job.config, job.snapshot, job.profile);
          if (stopped || !valid(version, job.identity, token)) return;
          if (next.result.proposals?.length) { setNotice('This AI job has a proposal to review in Models and AI work. Nothing was applied.'); setActiveJob(previous => previous?.run.id === next.id ? { ...previous, run: next } : previous); return; }
          setResult({ text: next.result.text, context: job.context, sources: job.snapshot, config: job.config, profile: job.profile,
            action: job.action, identity: job.identity, citations: job.citations });
        }
        if (next.error) setError(next.error);
        setActiveJob(previous => previous?.run.id === next.id ? { ...previous, run: next } : previous);
      } catch (failure) {
        if (!stopped && valid(version, job.identity, token)) {
          setError(failure instanceof Error ? failure.message : 'AI work could not be refreshed.');
          if (finished) setActiveJob(previous => previous?.run.id === finished!.id ? { ...previous, run: finished! } : previous);
          else timer = window.setTimeout(poll, 5000);
        }
      }
    };
    timer = window.setTimeout(poll, 1500);
    return () => { stopped = true; window.clearTimeout(timer); };
  }, [activeJob, workspaceId, identity]);
  async function send(version: number, token: string | null) {
    if (retryRequired || !status || !available || !selectedAction || !disclose || secure && !discloseSecure) return;
    if (!contextFits) throw new Error('Select fewer sources. AI context is limited to 24,000 characters; nothing was sent.');
    const prompt = selectedAction.prompt, agent = status.agents.find(value => value.tools.length === 0) || status.agents[0];
    if (!profile && !agent || !prompt.trim() || prompt.length > 3900) throw new Error('Shorten the selected text before asking AI.');
    const snapshot = contextualReviewedSnapshot(choices, reviewedSources);
    await recheck(status, snapshot, profile);
    const sources = reviewedSources;
    if (!valid(version, identity, token)) return;
    if (profile) {
      const requestKey = JSON.stringify([contextKey, selectedAction.id, profile.id, profile.revision, prompt, sources, durable]);
      const requestId = requestIds.current.get(requestKey) || crypto.randomUUID(); requestIds.current.set(requestKey, requestId);
      const payload = { request_id: requestId, channel_id: channelId, profile_id: profile.id, expected_profile_revision: profile.revision,
        operation: 'draft' as const, prompt, sources, max_steps: 1, max_tokens: Math.min(profile.max_output_tokens, 2048), disclose_to_provider: true as const };
      if (durable && !secure) {
        const run = await aiRuntimeApi.create(workspaceId, payload);
        requestIds.current.delete(requestKey);
        if (valid(version, identity, token)) { setActiveJob({ run, profile, snapshot, action: selectedAction.id, context: contextKey, identity, config: status, citations: choices.map(choice => choice.label) }); setDisclose(false); setDiscloseSecure(false); }
        return;
      }
      foregroundAttempt.current = version;
      const answer = await aiRuntimeApi.complete(workspaceId, { ...payload, disclose_decrypted_content: discloseSecure });
      requestIds.current.delete(requestKey);
      if (!valid(version, identity, token)) return;
      await recheck(status, snapshot, profile);
      if (!valid(version, identity, token)) return;
      if (!answer.text.trim() || answer.proposals?.length) throw new Error('AI returned an action proposal instead of text. Nothing was applied.');
      setResult({ text: answer.text, context: contextKey, sources: snapshot, config: status, profile, action: selectedAction.id, identity, citations: choices.map(choice => choice.label) });
      setDisclose(false); setDiscloseSecure(false); return;
    }
    if (!agent) throw new Error('Choose an available AI agent.');
    let conversation: string | null = null;
    try {
      const chat = await copilotApi.create(workspaceId, channelId); conversation = chat.id;
      if (!valid(version, identity, token)) return;
      foregroundAttempt.current = version;
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
      {runtime && runtime.profiles.some(value => value.id !== 'default') && <label className="fc-contextual-model"><span>{t('Model profile')}</span><select aria-label={t('Model profile')} value={profileId} disabled={!!busy} onChange={event => { setProfileId(event.target.value); setResult(null); setActiveJob(null); setDisclose(false); setDiscloseSecure(false); }}><option value="">{t('Workspace default')}</option>{runtime.profiles.filter(value => value.id !== 'default').map(value => <option key={value.id} value={value.id}>{value.name} · {value.model}</option>)}</select></label>}
      {!profileId && status && (!status.globally_enabled || !status.configured) && <p>{t('A workspace owner must enable and connect an AI provider in Connection setup.')}{onConfigure && <button type="button" onClick={onConfigure}>{t('Open connection setup')}</button>}</p>}
      {(profile || status?.provider) && <>
        <p className="fc-contextual-copilot-destination"><strong>{host}</strong> · {model}<small className="fc-contextual-recipient-url">{profile?.endpoint || status?.provider?.endpoint}</small></p>
        <label><input type="checkbox" checked={profile ? profile.consented : !!status?.consented} disabled={!!busy || (profile ? !runtime?.globally_enabled || !profile.configured || !profile.enabled : !status?.globally_enabled || !status.configured)} onChange={event => { const enabled = event.target.checked; void perform('Updating AI consent…', async (version, token) => {
          if (profile) { await aiRuntimeApi.consent(workspaceId, profile.id, profile.revision, enabled); const next = await aiRuntimeApi.status(workspaceId); if (valid(version, identity, token)) setRuntime(next); }
          else if (status) { await copilotApi.consent(workspaceId, status.config_revision, enabled); const next = await copilotApi.status(workspaceId); if (valid(version, identity, token)) setStatus(next); }
          if (valid(version, identity, token)) { setResult(null); setDisclose(false); setDiscloseSecure(false); }
        }); }} />{t(profile ? 'I opt in to this exact model profile and recipient.' : 'I opt in to this provider configuration for my account.')}</label>
      </>}
      {runtime && !secure && (profile || runtime.profiles.some(value => value.id === 'default' && value.configured)) && <label><input type="checkbox" checked={durable} disabled={!!busy} onChange={event => { setDurable(event.target.checked); setResult(null); setActiveJob(null); setDisclose(false); }} />{t('Keep this as an AI job that can continue after I leave.')}</label>}
      {status && available && <fieldset disabled={!!busy}>
        <div className="fc-contextual-copilot-actions" role="group" aria-label={t('AI actions')}>{actions.map(action => <button key={action.id} type="button" aria-pressed={action.id === actionId} onClick={() => { setActionId(action.id); setResult(null); setDisclose(false); setDiscloseSecure(false); }}>{t(action.label)}</button>)}</div>
        <details><summary>{t('Review this request')}</summary><p>{selectedAction?.prompt}</p></details>
        {!!choices.length && <details><summary>{t('Review {count} selected sources', { count: choices.length })}</summary>{choices.map((choice, index) => <div key={choice.key}><strong>{choice.label}</strong><p>{reviewedSources[index]?.text ?? choice.preview}</p></div>)}</details>}
        {choices.some(choice => choice.document) && <p>{t('Selected documents include up to 12,000 characters of verified text.')}</p>}
        {!promptFits && <p>{t('Shorten the selected text before asking AI.')}</p>}
        {!contextFits && <p>{t('Select fewer sources. AI context is limited to 24,000 characters; nothing was sent.')}</p>}
        <aside id={uid + '-disclosure'}><p>{t('Your request and {count} selected sources will be sent to {provider} using {model}.', { count: choices.length, provider: host, model })}</p><p>{t('API use may incur charges. A chat subscription is separate. The provider’s retention policy applies. AI answers can be wrong.')}</p>{durable && !secure && <p>{t('The private job can continue after this card closes. Cancel it explicitly to stop future steps.')}</p>}</aside>
        <label><input type="checkbox" checked={disclose} onChange={event => setDisclose(event.target.checked)} />{t('Send this request and the selected context to this provider.')}</label>
        {secure && <label className="fc-contextual-copilot-secure"><input type="checkbox" checked={discloseSecure} onChange={event => setDiscloseSecure(event.target.checked)} />{t('I explicitly disclose selected decrypted content through the server relay to the AI provider. These copies leave end-to-end encryption. Secure exchanges are not stored as plaintext history.')}</label>}
        <button type="button" className="fc-contextual-copilot-send" aria-describedby={uid + '-disclosure'} disabled={retryRequired || !promptFits || !contextFits || !disclose || secure && !discloseSecure} onClick={() => void perform('Asking your AI provider…', send)}>{t('Send to provider')}</button>
      </fieldset>}
      {retryRequired && <aside><p>{t('The previous foreground request may have been processed and charged. A new attempt can repeat those charges.')}</p><label><input type="checkbox" checked={retryAccepted} onChange={event => setRetryAccepted(event.target.checked)} />{t('I accept a new bounded attempt and possible duplicate charges.')}</label><button type="button" disabled={!!busy || !retryAccepted} onClick={() => { requestIds.current.clear(); setRetryRequired(false); setRetryAccepted(false); setDisclose(false); setDiscloseSecure(false); setError(''); }}>{t('Prepare a new AI attempt')}</button></aside>}
      {activeJob && <div className="fc-contextual-ai-job" aria-live="polite"><span>{t('AI work')}: {t(activeJob.run.status)}</span>{aiRunIsActive(activeJob.run) && <button type="button" disabled={!!busy} onClick={() => void perform('Cancelling AI work…', async (version, token) => { const next = await aiRuntimeApi.cancel(workspaceId, activeJob.run); if (valid(version, identity, token)) setActiveJob(previous => previous?.run.id === next.id ? { ...previous, run: next } : previous); })}>{t('Cancel future steps')}</button>}</div>}
      {currentResult && <div className="fc-contextual-copilot-result"><strong>{t('Review the suggestion')}</strong><p>{currentResult.text}</p>{!!currentResult.citations.length && <div className="fc-contextual-copilot-citations"><span>{t('Based on the reviewed sources')}</span><ol>{currentResult.citations.map((label, index) => <li key={index}><span>[S{index + 1}]</span> {label}</li>)}</ol></div>}{onApply && (!applyActionIds || applyActionIds.includes(currentResult.action)) && <button type="button" disabled={!!busy || currentResult.text.length > maxApplyLength} onClick={() => void perform('Checking the current context…', async (version, token) => { if (currentResult.context !== contextKey || currentResult.action !== actionId) return; await recheck(currentResult.config, currentResult.sources, currentResult.profile); if (valid(version, identity, token) && onApply(currentResult.text, currentResult.context) === false) throw new Error('The target changed. Review a new suggestion.'); })}>{t(applyLabel)}</button>}{onApply && (!applyActionIds || applyActionIds.includes(currentResult.action)) && currentResult.text.length > maxApplyLength && <p>{t('The suggestion is too long for this field. Nothing was changed.')}</p>}<small>{t(applyHint)}</small></div>}
      {busy && <p role="status"><Loader2 size={14} aria-hidden="true" />{t(busy)}</p>}
    </section>}
  </div>;
}
