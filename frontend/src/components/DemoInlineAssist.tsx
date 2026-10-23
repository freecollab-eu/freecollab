import { useEffect, useId, useRef, useState } from 'react';
import { ArrowRight, ArrowUpRight, Check, Sparkles, X } from 'lucide-react';
import { demoAiProviders, type DemoAiTranslate } from '../lib/demo-ai';
import {
  createDemoAssistPreview,
  demoAssistCanApply,
  demoAssistPreviewIsCurrent,
  type DemoAssistContext,
  type DemoAssistIntent,
  type DemoAssistPreview,
  type DemoAssistRecipe,
  type DemoAssistSource,
} from '../lib/demo-assist';
import '../styles/demo-inline-assist.css';

export interface DemoInlineAssistProps {
  context: DemoAssistContext;
  onApply: (preview: DemoAssistPreview, recipe: DemoAssistRecipe, source: HTMLElement) => boolean | void;
  onOpenSource: (source: DemoAssistSource, trigger: HTMLElement) => void;
  t: DemoAiTranslate;
}

/** Help stays beside its target. All examples are local until explicitly applied. */
export function DemoInlineAssist({ context, onApply, onOpenSource, t }: DemoInlineAssistProps) {
  const uid = useId(), trigger = useRef<HTMLButtonElement>(null), heading = useRef<HTMLHeadingElement>(null), result = useRef<HTMLHeadingElement>(null);
  const [open, setOpen] = useState(false), [intent, setIntent] = useState<DemoAssistIntent | undefined>(context.actions[0]?.id);
  const [preview, setPreview] = useState<DemoAssistPreview | null>(null);
  const [applied, setApplied] = useState(false);
  const location = context.channel + ':' + JSON.stringify(context.scope);
  const view = useRef({ location, fingerprint: context.fingerprint, preview });
  view.current = { location, fingerprint: context.fingerprint, preview };
  const selectedIntent = context.actions.find(action => action.id === intent)?.id ?? context.actions[0]?.id;
  const provider = demoAiProviders.find(option => option.id === context.provider);
  const recipient = provider ? t(provider.name) + ' (' + (provider.id === 'custom' ? t('Your own installation (placeholder)') : provider.host) + ')' : context.provider;
  const current = !!preview && preview.intent === selectedIntent && demoAssistPreviewIsCurrent(preview, context);
  const validModel = context.model.length <= 100 && /^[A-Za-z0-9][A-Za-z0-9_.:/-]*$/.test(context.model);
  const hasContent = context.sources.some(source => source.text.trim());
  const triggerLabel = context.actions[0]?.label ?? t('AI');

  useEffect(() => { setOpen(false); setPreview(null); setApplied(false); setIntent(context.actions[0]?.id); }, [location]); // Each location keeps its own nearby interaction.

  const close = () => {
    setOpen(false); setPreview(null); setApplied(false);
    requestAnimationFrame(() => { if (view.current.location === location && trigger.current?.isConnected) trigger.current.focus({ preventScroll: true }); });
  };
  const show = () => {
    setOpen(true);
    requestAnimationFrame(() => { if (view.current.location === location) heading.current?.focus({ preventScroll: true }); });
  };
  const prepare = () => {
    if (!selectedIntent || !provider || !validModel) return;
    const next = createDemoAssistPreview(context, selectedIntent, t);
    if (!next) return;
    setPreview(next); setApplied(false);
    requestAnimationFrame(() => { if (view.current.location === location && view.current.fingerprint === context.fingerprint && view.current.preview === next) result.current?.focus({ preventScroll: true }); });
  };

  return <div className="fc-inline-assist" data-demo-assist data-assist-scope={context.scope.kind} data-open={open}>
    <button ref={trigger} type="button" className="fc-inline-assist-trigger" data-assist-trigger aria-label={t('{action} for {target}', { action: triggerLabel, target: context.label })} aria-expanded={open} aria-controls={uid + '-card'} onClick={open ? close : show}><Sparkles size={13} aria-hidden="true" /><span>{triggerLabel}</span></button>
    {open && <section id={uid + '-card'} className="fc-inline-assist-card" aria-labelledby={uid + '-title'} onKeyDown={event => { if (event.key === 'Escape' && !event.nativeEvent.isComposing) { event.preventDefault(); event.stopPropagation(); close(); } }}>
      <header><div><span className="fc-inline-assist-caption">{t('Local demo example')}</span><h4 ref={heading} tabIndex={-1} id={uid + '-title'}>{t('AI for {target}', { target: context.label })}</h4></div><button type="button" className="fc-inline-assist-close" aria-label={t('Close AI for {target}', { target: context.label })} onClick={close}><X size={15} aria-hidden="true" /></button></header>
      <div className="fc-inline-assist-provider"><span>{provider ? t(provider.name) : context.provider}</span><code>{context.model}</code></div>
      <p className="fc-inline-assist-local">{t('Uses selected text; no AI request.')}</p>
      <div className="fc-inline-assist-actions" role="group" aria-label={t('Choose help for {target}', { target: context.label })}>{context.actions.map(action => <button type="button" key={action.id} aria-pressed={selectedIntent === action.id} onClick={() => { setIntent(action.id); setPreview(null); setApplied(false); }}>{action.label}{selectedIntent === action.id && <Check size={12} aria-hidden="true" />}</button>)}</div>
      <div className="fc-inline-assist-sources" aria-label={t('Sources for {target}', { target: context.label })}>{context.sources.map((source, index) => <button type="button" key={source.key} aria-label={t('View source {number}: {label}', { number: index + 1, label: source.label })} onClick={event => onOpenSource(source, event.currentTarget)}><span aria-hidden="true">{index + 1}</span>{source.label}<ArrowUpRight size={11} aria-hidden="true" /></button>)}</div>
      {context.omittedCount > 0 && <p className="fc-inline-assist-empty">{t('{count} sources omitted by this demo’s limits.', { count: context.omittedCount })}</p>}
      <details className="fc-inline-assist-sharing"><summary>{t('What is shared?')}</summary><p>{t('This demo stays in this tab. No service receives your sources.')}</p><p>{t('In a connected workspace, {count} sources would be sent to {provider} using {model}.', { count: context.sources.length, provider: recipient, model: context.model })}</p><p>{t('API use may cost money and your provider may retain submitted content.')}</p><p>{t('If encrypted content is shared as readable text with an external AI, those copies leave end-to-end encryption. Check permissions and review results.')}</p></details>
      {!hasContent && <p className="fc-inline-assist-empty" role="status">{t(context.scope.kind === 'composer' ? 'Write a draft first.' : 'Add content here to prepare an example.')}</p>}
      {!validModel && <p className="fc-inline-assist-empty" role="status">{t('Choose a valid AI model in Connections.')}</p>}
      <button type="button" className="fc-inline-assist-prepare" disabled={!selectedIntent || !provider || !validModel || !hasContent} onClick={prepare}>{t('Prepare example')}<ArrowRight size={13} aria-hidden="true" /></button>
      {preview && <section className="fc-inline-assist-result" data-assist-preview={applied ? 'applied' : current ? 'current' : 'stale'} aria-labelledby={uid + '-result'}><h5 id={uid + '-result'} ref={result} tabIndex={-1}>{preview.title}</h5><p>{preview.description}</p>{applied ? <p className="fc-inline-assist-applied" role="status"><Check size={12} aria-hidden="true" />{t('Applied')}</p> : !current && <p className="fc-inline-assist-stale" role="status">{t('The content or settings changed. Prepare a fresh example before using it.')}</p>}
        {preview.items.length > 0 && <ul>{preview.items.map((item, index) => <li key={index}><p>{item.text}</p><div className="fc-inline-assist-citations">{item.sourceKeys.map(key => {
          const snapshot = preview.sources.find(source => source.key === key), source = context.sources.find(value => value.key === key && value.fingerprint === snapshot?.fingerprint);
          const number = preview.sources.findIndex(value => value.key === key) + 1;
          return snapshot && <button key={key} type="button" disabled={!current || !source} aria-label={t('View source {number}: {label}', { number, label: snapshot.label })} onClick={event => { if (current && source) onOpenSource(source, event.currentTarget); }}>{t('Source {number}', { number })}<ArrowUpRight size={11} aria-hidden="true" /></button>;
        })}</div></li>)}</ul>}
        {preview.recipes.map(recipe => {
          const addition = recipe.before && recipe.after.startsWith(recipe.before) ? recipe.after.slice(recipe.before.length).trimStart() : recipe.after;
          const characters = Array.from(addition), excerpt = characters.slice(0, 360).join('');
          return <div className="fc-inline-assist-recipe" key={recipe.id}><span>{t('Proposed change')}</span><blockquote>{excerpt}{characters.length > 360 && '…'}</blockquote><details><summary>{t('Compare current and proposed text')}</summary><dl><div><dt>{t('Current text')}</dt><dd>{recipe.before || t('No text yet')}</dd></div><div><dt>{t('Proposed text')}</dt><dd>{recipe.after}</dd></div></dl></details><button type="button" className="fc-showroom-primary" aria-label={t('{action} for {target}', { action: recipe.label, target: context.label })} disabled={applied || !current || !demoAssistCanApply(preview, recipe, context)} onClick={event => { if (!applied && current && demoAssistCanApply(preview, recipe, context) && onApply(preview, recipe, event.currentTarget) === true) setApplied(true); }}>{recipe.label}<ArrowRight size={13} aria-hidden="true" /></button></div>;
        })}
        <small>{t('Nothing changes until you choose an action. Existing text is checked again before applying.')}</small>
      </section>}
      <footer><button type="button" onClick={close}>{t('Cancel')}</button></footer>
    </section>}
  </div>;
}
