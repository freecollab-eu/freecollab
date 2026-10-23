import { useId, useRef } from 'react';
import { ArrowRight, ArrowUpRight, Check, FileText, MessageSquare, ShieldCheck } from 'lucide-react';
import {
  createDemoAiRun,
  demoAiProviders,
  demoAiRunIsCurrent,
  type DemoAiIntent,
  type DemoAiRun,
  type DemoAiSession,
  type DemoAiSource,
  type DemoAiTranslate,
} from '../lib/demo-ai';
import type { DemoChannel } from '../lib/demo-showroom';
import '../styles/demo-ai-flow.css';

export interface DemoAiFlowProps {
  channel: DemoChannel;
  channelName: string;
  sources: DemoAiSource[];
  session: DemoAiSession;
  onSessionChange: (session: DemoAiSession) => void;
  onReviewTask: (run: DemoAiRun, source: HTMLElement) => void;
  onOpenSource: (source: DemoAiSource, trigger: HTMLElement) => void;
  t: DemoAiTranslate;
}

const intents: Array<{ id: DemoAiIntent; label: string; description: string }> = [
  { id: 'summary', label: 'Summary', description: 'Bring the selected sources together.' },
  { id: 'next_steps', label: 'Next steps', description: 'Review what could happen next.' },
  { id: 'reply', label: 'Reply', description: 'Start with a draft you can review.' },
  { id: 'task', label: 'Task', description: 'Take a message into task review.' },
];

/** A controlled, extractive preview. No provider request or automatic workspace write. */
export function DemoAiFlow({ channel, channelName, sources, session, onSessionChange, onReviewTask, onOpenSource, t }: DemoAiFlowProps) {
  const uid = useId(), resultHeading = useRef<HTMLHeadingElement>(null);
  const currentView = useRef({ channel, run: session.run });
  currentView.current = { channel, run: session.run };
  const currentSources = sources.filter(source => source.channel === channel);
  const selectedKeys = [...new Set(session.selectedKeys)].filter(key => currentSources.some(source => source.key === key));
  const selectedCount = selectedKeys.length;
  const provider = demoAiProviders.find(option => option.id === session.provider);
  const recipient = provider ? t(provider.name) + ' (' + (provider.id === 'custom' ? t('Your own installation (placeholder)') : provider.host) + ')' : session.provider;
  const intent = intents.find(option => option.id === session.intent);
  const validModel = session.model.length <= 100 && /^[A-Za-z0-9][A-Za-z0-9_.:/-]*$/.test(session.model);
  const current = session.run !== null && demoAiRunIsCurrent(session.run, session, sources, channel);
  const canPrepare = Boolean(provider && intent && validModel && selectedCount > 0 && selectedCount <= 8);
  const change = (next: Partial<DemoAiSession>) => onSessionChange({ ...session, ...next, run: null });
  const toggleSource = (key: string) => {
    if (selectedKeys.includes(key)) change({ selectedKeys: selectedKeys.filter(value => value !== key) });
    else if (selectedCount < 8) change({ selectedKeys: [...selectedKeys, key] });
  };
  const prepare = () => {
    if (!canPrepare) return;
    const next = { ...session, selectedKeys, run: null };
    const run = createDemoAiRun(next, sources, channel, t);
    if (!run) return;
    onSessionChange({ ...next, run });
    requestAnimationFrame(() => { if (currentView.current.channel === channel && currentView.current.run === run) resultHeading.current?.focus({ preventScroll: true }); });
  };
  const stage = current ? 2 : selectedCount ? 1 : 0;
  const run = session.run;
  return <section className="fc-demo-ai" data-ai-preview="local" aria-labelledby={uid + '-heading'}>
    <header className="fc-demo-ai-heading">
      <span className="fc-demo-ai-local"><span aria-hidden="true" />{t('Interactive AI preview · runs locally')}</span>
      <h3 id={uid + '-heading'}>{t('Try your AI workflow')}</h3>
      <p>{t('Choose your sources, review an example, then decide what to use. No API key is needed and no AI service is called.')}</p>
    </header>

    <ol className="fc-demo-ai-stages" aria-label={t('AI preview flow')}>
      {['Sources', 'Review', 'Local example', 'Your approval'].map((label, index) => <li key={label} data-ai-step={index + 1} aria-current={stage === index ? 'step' : undefined}><span aria-hidden="true">{index < stage ? <Check size={12} /> : String(index + 1).padStart(2, '0')}</span>{t(label)}</li>)}
    </ol>

    <div className="fc-demo-ai-settings">
      <label htmlFor={uid + '-provider'}>{t('AI provider')}<select id={uid + '-provider'} value={session.provider} onChange={event => {
        const next = demoAiProviders.find(option => option.id === event.target.value);
        if (next) change({ provider: next.id, model: next.model });
      }}>{demoAiProviders.map(option => <option key={option.id} value={option.id}>{t(option.name)}</option>)}</select></label>
      <label htmlFor={uid + '-model'}>{t('AI model')}<input id={uid + '-model'} value={session.model} maxLength={100} autoComplete="off" spellCheck={false} onChange={event => change({ model: event.target.value })} aria-describedby={uid + '-model-note'} aria-invalid={!validModel} /></label>
    </div>
    <p className="fc-demo-ai-model-note" id={uid + '-model-note'}>{t(validModel ? 'The model ID is editable. This preview never contacts it.' : 'Use a model ID with letters, numbers, dots, slashes, colons, underscores or hyphens.')}</p>

    <fieldset className="fc-demo-ai-intents"><legend>{t('What would help?')}</legend><div>{intents.map(option => <button type="button" key={option.id} aria-pressed={session.intent === option.id} onClick={() => change({ intent: option.id })}>{t(option.label)}{session.intent === option.id && <Check size={12} aria-hidden="true" />}</button>)}</div><p>{intent && t(intent.description)}</p></fieldset>

    <fieldset className="fc-demo-ai-sources" aria-describedby={uid + '-source-note'}><legend>{t('Choose sources')}<span>{t('{count} of 8 selected', { count: selectedCount })}</span></legend>
      <p id={uid + '-source-note'}>{t('Only messages and documents from #{channel} are available. Open a source to check the exact text.', { channel: channelName })}</p>
      {selectedCount > 0 && <ul className="fc-demo-ai-selected" data-ai-selected-sources>{selectedKeys.map(key => {
        const source = currentSources.find(value => value.key === key)!;
        const characters = Array.from(source.text), excerpt = characters.slice(0, 92).join('');
        return <li key={key}><span className="fc-demo-ai-source-meta">{source.kind === 'message' ? <MessageSquare size={12} aria-hidden="true" /> : <FileText size={12} aria-hidden="true" />}{source.label}</span><p>{excerpt}{characters.length > 92 && '…'}</p></li>;
      })}</ul>}
      {!currentSources.length && <p className="fc-demo-ai-empty">{t('Send a message or add a document in this channel to try the preview.')}</p>}
      {currentSources.length > 0 && <details className="fc-demo-ai-source-picker" data-ai-source-picker><summary>{t('Change sources')}</summary><div className="fc-demo-ai-source-list">{currentSources.map((source, index) => {
        const selected = selectedKeys.includes(source.key);
        const descriptionId = uid + '-excerpt-' + index;
        const kindId = uid + '-kind-' + index, labelId = uid + '-label-' + index;
        const excerpt = Array.from(source.text).slice(0, 110).join('');
        return <div className="fc-demo-ai-source" key={source.key} data-ai-source-key={source.key} data-selected={selected}>
          <label><input type="checkbox" checked={selected} disabled={!selected && selectedCount >= 8} onChange={() => toggleSource(source.key)} aria-labelledby={kindId + ' ' + labelId} aria-describedby={descriptionId} /><span><span id={kindId} className="fc-demo-ai-source-meta">{source.kind === 'message' ? <MessageSquare size={12} aria-hidden="true" /> : <FileText size={12} aria-hidden="true" />}{t(source.kind === 'message' ? 'Message' : 'Document')}</span><strong id={labelId}>{source.label}</strong><span id={descriptionId} className="fc-demo-ai-source-excerpt">{excerpt}{Array.from(source.text).length > 110 && '…'}</span></span></label>
          <details><summary>{t('Inspect source')}</summary><blockquote>{source.text}</blockquote><button type="button" onClick={event => onOpenSource(source, event.currentTarget)}>{t('View original')}<ArrowUpRight size={13} aria-hidden="true" /></button></details>
        </div>;
      })}</div></details>}
      {selectedCount === 8 && <p role="status">{t('Eight sources selected. Remove one to include another.')}</p>}
    </fieldset>

    <aside className="fc-demo-ai-warning" aria-labelledby={uid + '-warning'}>
      <ShieldCheck size={17} aria-hidden="true" />
      <div><h4 id={uid + '-warning'}>{t('Review before sharing')}</h4>
        <p>{t('This local preview sends nothing. A connected installation would share {count} sources with {provider} using {model}.', { count: selectedCount, provider: recipient, model: session.model.trim() || t('your chosen model') })}</p>
        <p>{t('Your provider may charge for API use and retain submitted content. Check its policy before connecting.')}</p>
        <p>{t('If selected content comes from an end-to-end encrypted conversation, readable copies shared with external AI leave that encryption. Check permissions and review AI results for mistakes.')}</p>
      </div>
    </aside>
    <div className="fc-demo-ai-prepare"><button type="button" className="fc-showroom-primary" disabled={!canPrepare} onClick={prepare}>{t('Prepare local example')}<ArrowRight size={14} aria-hidden="true" /></button><small>{t('Selected text is reused locally. No model inference takes place.')}</small></div>

    {run && <section className="fc-demo-ai-result" data-ai-result={current ? 'current' : 'stale'} aria-labelledby={uid + '-result'}>
      <span className="fc-demo-ai-result-kicker">{t('Local example · not an AI response')}</span>
      <h4 id={uid + '-result'} ref={resultHeading} tabIndex={-1}>{run.title}</h4>
      {!current && <p className="fc-demo-ai-stale" role="status">{t('The sources or settings have changed. Prepare a new example before continuing.')}</p>}
      <p className="fc-demo-ai-result-description">{run.description}</p>
      {run.items.length > 0 && <ol className="fc-demo-ai-result-items">{run.items.map((item, index) => <li key={index}>{run.replyDraft && index === 0 ? <blockquote className="fc-demo-ai-reply">{item.text}</blockquote> : <p>{item.text}</p>}<div className="fc-demo-ai-citations">{item.sourceKeys.map(key => {
        const snapshot = run.sources.find(source => source.key === key);
        const source = currentSources.find(value => value.key === key && value.fingerprint === snapshot?.fingerprint);
        const number = run.sources.findIndex(value => value.key === key) + 1;
        return snapshot && <button type="button" key={key} disabled={!current || !source} aria-label={t('View source {number}: {label}', { number, label: snapshot.label })} onClick={event => { if (current && source) onOpenSource(source, event.currentTarget); }}><span aria-hidden="true">{number}</span>{snapshot.label}<ArrowUpRight size={12} aria-hidden="true" /></button>;
      })}</div></li>)}</ol>}
      {run.items.length === 0 && <p className="fc-demo-ai-output">{run.output}</p>}
      <footer>{run.intent === 'task' && (run.task ? <button type="button" className="fc-showroom-primary" disabled={!current} onClick={event => { if (current && run.task) onReviewTask(run, event.currentTarget); }}>{t('Continue to task review')}<ArrowRight size={14} aria-hidden="true" /></button> : <p>{t('Choose at least one message to take a task into review.')}</p>)}<small>{t(run.intent === 'task' ? 'Nothing is posted or created automatically. Task wording and owner are reviewed in the next step.' : 'This example stays here. Review and rewrite it before using it.')}</small></footer>
    </section>}
  </section>;
}
