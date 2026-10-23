import { useEffect, useId, useLayoutEffect, useReducer, useRef, useState, type CSSProperties, type FormEvent, type KeyboardEvent } from 'react';
import { ArrowLeft, ArrowUp, ArrowDown, ArrowUpRight, Check, CheckCheck, ChevronDown, FileText, LayoutGrid, MessageSquare, Palette, Pin, Plus, Plug, RotateCcw, Send, SlidersHorizontal, Download, Focus, Menu, X } from 'lucide-react';
import { useI18n, type Locale } from '../i18n';
import swedish from '../i18n/demo-showroom.sv';
import refinementSwedish from '../i18n/studio-refinement.sv';
import journeySwedish from '../i18n/demo-journey.sv';
import flowSwedish from '../i18n/demo-natural-flow.sv';
import courtyard from '../assets/open-studio/courtyard.png';
import portraits from '../assets/open-studio/portraits.png';
import { Brand } from './Brand';
import { DemoChannelSwitcher, type DemoManagedChannel } from './DemoChannelSwitcher';
import { DemoDocumentDesk, DEMO_MESSAGE_DRAG_TYPE } from './DemoDocumentDesk';
import { useModalFocus } from '../lib/useModalFocus';
import { buildAppearanceTokens, DEFAULT_APPEARANCE, PRESETS, type AppearancePreferences } from '../lib/appearance';
import { createDemoState, demoCallbackExample, demoConnectionProviders, demoConnectionSteps, demoDecisionExample, demoDecisionTaskId, demoOwners, demoMarkdown, demoReducer, demoSections, demoChannelDocuments, type DemoDocumentId, type DemoMessage, type DemoChannel, type DemoSection, type DemoMaterial, type DemoDestination, type DemoOwner } from '../lib/demo-showroom';
import '../styles/demo-showroom.css';

export interface DemoProps { onBack: () => void; onOpenApp?: () => void; destination?: DemoDestination }
const sectionNames: Record<DemoSection, string> = { chat: 'Conversation', projects: 'Projects', documents: 'Documents', connections: 'Connections', appearance: 'Appearance' };
const sectionIcons = { chat: MessageSquare, projects: LayoutGrid, documents: FileText, connections: Plug, appearance: Palette };
const createChannels = (): DemoManagedChannel[] => [
  { id: 'studio', name: 'courtyard', description: 'Ideas, materials and a quieter courtyard.', members: [...demoOwners], sampleName: true, sampleDescription: true },
  { id: 'product', name: 'planning', description: 'A shared plan and the next small step.', members: ['Maja', 'Noor', 'Nora'], sampleName: true, sampleDescription: true },
  { id: 'design', name: 'design', description: 'Sketches, warm materials and room to think.', members: ['Elias', 'Maja'], sampleName: true, sampleDescription: true },
];

/** Public, local-only product showroom. No account, API or provider operations. */
export function Demo({ onBack, onOpenApp, destination }: DemoProps) {
  const { locale: appLocale } = useI18n();
  const [localLocale, setLocalLocale] = useState<Locale | null>(null);
  const locale = localLocale ?? appLocale;
  function t(source: string, variables: Record<string, string | number> = {}) {
    const text = locale === 'sv' ? (swedish[source] ?? refinementSwedish[source] ?? journeySwedish[source] ?? flowSwedish[source] ?? source) : source;
    return text.replace(/\{([A-Za-z][A-Za-z0-9_]*)\}/g, (whole, key: string) => Object.prototype.hasOwnProperty.call(variables, key) ? String(variables[key]) : whole);
  }
  const [toolsOpen, setToolsOpen] = useState(false), [layoutOpen, setLayoutOpen] = useState(false);
  const tools = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const outside = (event: PointerEvent) => { if (!tools.current?.contains(event.target as Node)) setToolsOpen(false); };
    const escape = (event: globalThis.KeyboardEvent) => { if (event.key === 'Escape') { if (tools.current?.contains(document.activeElement)) tools.current.querySelector<HTMLButtonElement>('button[aria-expanded]')?.focus(); setToolsOpen(false); setLayoutOpen(false); } };
    document.addEventListener('pointerdown', outside); document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape); };
  }, []);
  const [state, dispatch] = useReducer(demoReducer, undefined, createDemoState);
  const [section, setSection] = useState<DemoSection>(destination?.section ?? 'chat'), [channel, setChannel] = useState<DemoChannel>('studio');
  const [managedChannels, setManagedChannels] = useState(createChannels);
  const [pendingReference, setPendingReference] = useState<DemoMessage | null>(null);
  const [contextView, setContextView] = useState<'documents' | 'projects' | null>(null);
  const contextPanel = useRef<HTMLElement>(null);
  const contextOrigin = useRef<{ element: HTMLElement; x: number; y: number } | null>(null);
  const reading = useRef<Partial<Record<DemoChannel, { top: number; following: boolean }>>>({});
  const conversationPosition = useRef<Partial<Record<DemoChannel, { x: number; y: number }>>>({});
  const ownMessage = useRef<DemoChannel | null>(null);
  const navigationRevision = useRef(0);
  const [drafts, setDrafts] = useState<Record<DemoChannel, string>>({ studio: '', product: '', design: '' });
  const message = drafts[channel];
  const [task, setTask] = useState(''), [filter, setFilter] = useState<'all' | 'todo' | 'done'>('all');
  const [documentId, setDocumentId] = useState<DemoDocumentId>('brief');
  const [provider, setProvider] = useState<'microsoft' | 'google'>(destination?.provider ?? 'microsoft');
  const [decisionText, setDecisionText] = useState<string | null>(null), [nextStep, setNextStep] = useState<string | null>(null);
  const [decisionOwner, setDecisionOwner] = useState<DemoOwner>('Maja'), [editingDecision, setEditingDecision] = useState(false);
  const [checklists, setChecklists] = useState<Record<'microsoft' | 'google', number[]>>({ microsoft: [], google: [] });
  const [appearance, setAppearance] = useState<AppearancePreferences>({ ...DEFAULT_APPEARANCE, mode: 'light' });
  const [notice, setNotice] = useState(''), [resetting, setResetting] = useState(false);
  const resetDialog = useRef<HTMLDivElement>(null);
  useModalFocus(resetDialog, () => setResetting(false), resetting);
  const tabRefs = useRef<Partial<Record<DemoSection, HTMLButtonElement | null>>>({}), transcript = useRef<HTMLDivElement>(null), panel = useRef<HTMLDivElement>(null);
  const urls = useRef(new Set<string>()), id = useId();
  const style = { ...buildAppearanceTokens(appearance, 'light'), '--fc-portraits': `url(${portraits})` } as CSSProperties;
  const channelDocuments = demoChannelDocuments(state, channel);
  const selectedDocument = channelDocuments.find(row => row.id === documentId) ?? channelDocuments[0];
  const activeChannel = managedChannels.find(row => row.id === channel)!;
  const channelName = activeChannel.sampleName ? t(activeChannel.name) : activeChannel.name;
  const documentTitle = (row: typeof selectedDocument) => row.sample ? t(row.title) : row.title;
  const done = state.tasks.filter(row => row.done).length;
  const decisionValue = decisionText ?? state.decision?.text ?? t(demoDecisionExample.text);
  const nextStepValue = nextStep ?? state.decision?.nextStep ?? t(demoDecisionExample.nextStep);
  useEffect(() => { rememberReading(); rememberConversationPosition(); setSection(destination?.section ?? 'chat'); setProvider(destination?.provider ?? 'microsoft'); setContextView(null); contextOrigin.current = null; navigationRevision.current++; }, [destination?.section, destination?.provider]);
  useEffect(() => () => { navigationRevision.current++; }, []);
  useLayoutEffect(() => {
    const node = transcript.current;
    if (!node?.getClientRects().length) return;
    const saved = reading.current[channel];
    node.scrollTop = !saved || saved.following || ownMessage.current === channel ? node.scrollHeight : saved.top;
    ownMessage.current = null;
    rememberReading();
  }, [channel, state.messages[channel], section, contextView]);
  useEffect(() => () => { for (const url of urls.current) URL.revokeObjectURL(url); }, []);
  function afterNavigation(action: () => void) {
    const revision = ++navigationRevision.current;
    requestAnimationFrame(() => { if (revision === navigationRevision.current) action(); });
  }
  function rememberReading() {
    const node = transcript.current;
    if (node?.getClientRects().length) reading.current[channel] = { top: node.scrollTop, following: node.scrollHeight - node.scrollTop - node.clientHeight < 64 };
  }
  function rememberConversationPosition() {
    if (section !== 'chat') return;
    const position = contextView && contextOrigin.current ? contextOrigin.current : { x: window.scrollX, y: window.scrollY };
    conversationPosition.current[channel] = { x: position.x, y: position.y };
  }
  function selectDemoChannel(next: DemoChannel) {
    rememberReading();
    rememberConversationPosition();
    setChannel(next); setDocumentId(demoChannelDocuments(state, next)[0].id); setPendingReference(null); setSection('chat'); setContextView(null); contextOrigin.current = null; setNotice('');
    afterNavigation(() => { const position = conversationPosition.current[next]; window.scrollTo(position?.x ?? 0, position?.y ?? 0); });
  }
  function choose(next: DemoSection, focus = false) {
    rememberReading();
    rememberConversationPosition();
    setSection(next); setContextView(null); setPendingReference(null); contextOrigin.current = null; setNotice('');
    afterNavigation(() => {
      if (next === 'chat') { const position = conversationPosition.current[channel]; if (position) window.scrollTo(position.x, position.y); }
      if (focus) panel.current?.focus({ preventScroll: true });
    });
  }
  function openContext(next: 'documents' | 'projects', source: HTMLElement) {
    // Scroll events may still be queued when navigation starts in the same frame.
    rememberReading();
    if (!contextOrigin.current) {
      const position = section === 'chat' ? { x: window.scrollX, y: window.scrollY } : conversationPosition.current[channel] ?? { x: 0, y: 0 };
      contextOrigin.current = { element: source, ...position };
    }
    setSection('chat'); setContextView(next); setNotice('');
    afterNavigation(() => {
      contextPanel.current?.focus({ preventScroll: true });
      if (window.matchMedia('(max-width: 760px)').matches) contextPanel.current?.scrollIntoView({ block: 'start' });
    });
  }
  function returnToConversation() {
    rememberReading();
    rememberConversationPosition();
    const origin = contextOrigin.current;
    setSection('chat'); setContextView(null); setPendingReference(null); contextOrigin.current = null; setNotice('');
    afterNavigation(() => {
      if (origin?.element.isConnected && origin.element.getClientRects().length && !origin.element.closest('[inert]') && !origin.element.matches(':disabled')) { window.scrollTo(origin.x, origin.y); origin.element.focus({ preventScroll: true }); }
      else { const position = conversationPosition.current[channel]; if (position) window.scrollTo(position.x, position.y); panel.current?.focus({ preventScroll: true }); }
    });
  }
  function openDocument(next: DemoDocumentId, source: HTMLElement) {
    setDocumentId(next); openContext('documents', source);
    afterNavigation(() => {
      const editor = document.getElementById(id + '-document');
      editor?.focus({ preventScroll: true });
      if (window.matchMedia('(max-width: 760px)').matches) editor?.scrollIntoView({ block: 'center' });
    });
  }
  function revealMessage(messageId: string) {
    if (!state.messages[channel].some(row => row.id === messageId)) return;
    setSection('chat'); setContextView(null); setPendingReference(null); contextOrigin.current = null;
    afterNavigation(() => {
      const node = transcript.current?.querySelector<HTMLElement>('[data-message-id="' + messageId + '"]');
      node?.scrollIntoView({ block: 'center', behavior: 'auto' }); node?.focus({ preventScroll: true }); rememberReading();
    });
  }
  function renderDesk(compact = false) {
    return <DemoDocumentDesk state={state} channel={channel} dispatch={dispatch} t={t} compact={compact} onOpenDocument={openDocument} onRevealMessage={revealMessage} pendingReference={pendingReference} onReferenceSaved={() => setPendingReference(null)} onCancelReference={() => setPendingReference(null)} />;
  }
  function setMessage(value: string) { setDrafts(previous => ({ ...previous, [channel]: value })); }
  function keyboardTab(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    let next = index;
    if (event.key === 'ArrowRight') next = (index + 1) % demoSections.length;
    else if (event.key === 'ArrowLeft') next = (index + demoSections.length - 1) % demoSections.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = demoSections.length - 1;
    else return;
    event.preventDefault(); choose(demoSections[next]); tabRefs.current[demoSections[next]]?.focus();
  }
  function send(event: FormEvent) {
    event.preventDefault(); if (!message.trim()) return;
    if (state.messages[channel].length >= 100) { setNotice('This sample conversation is full. Reset the demo to start again.'); return; }
    ownMessage.current = channel; dispatch({ type: 'message', channel, text: message, time: new Date().toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' }) }); setMessage(''); setNotice('Your message was added to this demo only.');
  }
  function addTask(event: FormEvent) {
    event.preventDefault(); if (!task.trim()) return;
    if (state.tasks.length >= 50) { setNotice('This sample project is full. Reset the demo to start again.'); return; }
    dispatch({ type: 'task_add', text: task }); setTask(''); setNotice('Your task was added to this demo only.');
  }
  function recordDecision(event: FormEvent) {
    event.preventDefault();
    if (!decisionValue.trim() || !nextStepValue.trim()) return;
    if (state.tasks.length >= 50 && !state.tasks.some(row => row.id === demoDecisionTaskId)) { setNotice('This sample project is full. Reset the demo to start again.'); return; }
    dispatch({ type: 'decision_record', text: decisionValue, nextStep: nextStepValue, owner: decisionOwner });
    setDecisionText(null); setNextStep(null); setEditingDecision(false); setFilter('all');
    setNotice('Your sample decision and its next-step task are recorded in this tab only.');
    afterNavigation(() => (section === 'chat' && contextView ? contextPanel.current : panel.current)?.querySelector<HTMLElement>('.fc-studio-decision-record')?.focus({ preventScroll: true }));
  }
  function download() {
    const url = URL.createObjectURL(new Blob([demoMarkdown(selectedDocument, t)], { type: 'text/markdown;charset=utf-8' })); urls.current.add(url);
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'freecollab-demo-' + documentId + '.md'; document.body.append(anchor); anchor.click(); anchor.remove();
    // Download URLs are bounded by explicit user actions and released on reset/unmount.
    while (urls.current.size > 5) { const oldest = urls.current.values().next().value; if (oldest) { URL.revokeObjectURL(oldest); urls.current.delete(oldest); } }
    setNotice('Your demo document was downloaded as Markdown.');
  }
  function reset() {
    for (const url of urls.current) URL.revokeObjectURL(url); urls.current.clear();
    navigationRevision.current++; reading.current = {}; conversationPosition.current = {}; ownMessage.current = null; contextOrigin.current = null; setContextView(null);
    dispatch({ type: 'reset' }); setSection('chat'); setManagedChannels(createChannels()); setPendingReference(null); setToolsOpen(false); setLayoutOpen(false); setLocalLocale(null); setDrafts({ studio: '', product: '', design: '' }); setTask(''); setFilter('all'); setChannel('studio'); setDocumentId('brief'); setProvider('microsoft'); setChecklists({ microsoft: [], google: [] }); setDecisionText(null); setNextStep(null); setDecisionOwner('Maja'); setEditingDecision(false); setAppearance({ ...DEFAULT_APPEARANCE, mode: 'light' }); setResetting(false); setNotice('The demo has been reset. You have a fresh workspace to explore.');
  }
  const orderedMaterials = [...state.layout.order].sort((a, b) => Number(state.layout.pinned.includes(b)) - Number(state.layout.pinned.includes(a)));
  function avatar(author: string, large = false) {
    const position = ({ Maja: 0, Noor: 1, Elias: 2, Nora: 3 } as Record<string, number>)[author] ?? 0;
    return <span aria-hidden="true" className={'fc-studio-portrait fc-studio-portrait-' + position + (large ? ' fc-studio-portrait-large' : '')} />;
  }
  function materialName(material: DemoMaterial) { return material === 'courtyard' ? 'Courtyard image' : 'Shared brief'; }
  function renderProjects() { return (<><div className="fc-showroom-section-heading"><div><span className="fc-showroom-kicker">{t('Courtyard project')}</span><h2>{t('The courtyard project')}</h2><p>{t('Review the materials, add a task and prepare for the team walkthrough.')}</p></div><span className="fc-showroom-project-icon"><LayoutGrid size={22} /></span></div>
              <section className="fc-studio-decision" aria-labelledby={id + '-decision-title'}>
                <header><div><span className="fc-showroom-kicker">{t('From the shared brief')}</span><h3 id={id + '-decision-title'}>{t('Make the next step clear.')}</h3></div><button type="button" className="fc-showroom-subtle" onClick={event => { setDocumentId(channelDocuments[0].id); openContext('documents', event.currentTarget); }}>{t('View shared brief')}<ArrowUpRight size={14} /></button></header>
                {state.decision && !editingDecision ? <div className="fc-studio-decision-record" tabIndex={-1}>
                  <span className="fc-studio-decision-label"><Check size={14} />{t('Recorded in this demo')}</span>
                  <p>{state.decision.text}</p>
                  <dl><div><dt>{t('Next step')}</dt><dd>{state.decision.nextStep}</dd></div><div><dt>{t('Owner')}</dt><dd>{state.decision.owner}</dd></div></dl>
                  <button type="button" className="fc-showroom-subtle" onClick={() => setEditingDecision(true)}>{t('Revise sample decision')}<ArrowUpRight size={14} /></button>
                </div> : <form onSubmit={recordDecision} aria-describedby={id + '-decision-boundary'}>
                  <label>{t('Sample decision')}<textarea aria-label={t('Sample decision')} value={decisionValue} maxLength={1000} onChange={event => setDecisionText(event.target.value)} /></label>
                  <div className="fc-studio-decision-fields"><label>{t('Next step')}<input aria-label={t('Next step')} value={nextStepValue} maxLength={180} onChange={event => setNextStep(event.target.value)} /></label><label>{t('Owner')}<select aria-label={t('Owner')} value={decisionOwner} onChange={event => setDecisionOwner(event.target.value as DemoOwner)}>{demoOwners.map(owner => <option key={owner} value={owner}>{owner}</option>)}</select></label></div>
                  <footer><p id={id + '-decision-boundary'}>{t('This records an example decision and adds a task in this tab only.')}</p><button type="submit" className="fc-showroom-primary" disabled={!decisionValue.trim() || !nextStepValue.trim()}>{t('Record sample decision')}<Check size={15} /></button></footer>
                </form>}
              </section>
              <div className="fc-showroom-project-progress"><span>{t('{done} of {count} sample tasks complete', { done, count: state.tasks.length })}</span><progress value={done} max={state.tasks.length} aria-label={t('Sample project progress')} /></div><div className="fc-showroom-filter" role="group" aria-label={t('Filter demo tasks')}>{(['all', 'todo', 'done'] as const).map(value => <button key={value} type="button" aria-pressed={filter === value} onClick={() => setFilter(value)}>{t(value === 'all' ? 'All tasks' : value === 'todo' ? 'To do' : 'Done')}</button>)}</div><ul className="fc-showroom-tasks">{state.tasks.filter(row => filter === 'all' || (filter === 'done' ? row.done : !row.done)).map(row => <li key={row.id} className={row.done ? 'fc-showroom-task-done' : ''}><label><input type="checkbox" checked={row.done} onChange={() => dispatch({ type: 'task_toggle', id: row.id })} /><span>{row.sample ? t(row.text) : row.text}</span></label><span>{row.owner && <>{row.owner} · </>}{t(row.group)}</span></li>)}</ul>{!state.tasks.some(row => filter === 'all' || (filter === 'done' ? row.done : !row.done)) && <p className="fc-showroom-empty">{t('No tasks in this view. Add a next step below.')}</p>}<form className="fc-showroom-task-add" onSubmit={addTask}><Plus size={17} /><label className="fc-showroom-visually-hidden" htmlFor={id + '-task'}>{t('New demo task')}</label><input id={id + '-task'} value={task} maxLength={180} onChange={event => setTask(event.target.value)} placeholder={t('Add a small next step…')} /><button className="fc-showroom-subtle" disabled={!task.trim()}>{t('Add task')}</button></form><div className="fc-showroom-project-note"><CheckCheck size={18} /><p>{t('A live sample of the interaction. Your changes belong to this demo, not a real team or project.')}</p></div></>); }
  function renderDocuments() { return (<><div className="fc-showroom-section-heading"><div><span className="fc-showroom-kicker">{t('Shared documents')} · #{channelName}</span><h2>{t('Room for everything that matters.')}</h2><p>{t('Keep related documents together. Open one, make a change, then return to the conversation.')}</p></div><FileText size={25} /></div>{renderDesk()}<section className="fc-showroom-document" aria-label={t('Document editor')}><div><h3>{documentTitle(selectedDocument)}</h3><span>{selectedDocument.owner === 'You' ? t('You') : selectedDocument.owner} · {t(selectedDocument.updatedLabel)}</span></div><label className="fc-showroom-visually-hidden" htmlFor={id + '-document'}>{t('Edit demo document')}</label><textarea id={id + '-document'} value={selectedDocument.edited ?? (selectedDocument.sample ? t(selectedDocument.source) : selectedDocument.source)} maxLength={20000} spellCheck={false} onChange={event => dispatch({ type: 'document', id: selectedDocument.id, text: event.target.value })} /><footer><span>{t('Markdown · local changes only')}</span><button className="fc-showroom-subtle" onClick={download}><Download size={15} />{t('Download Markdown')}</button></footer></section>{selectedDocument.id === 'brief' && <div className="fc-studio-document-next"><p>{t('Keep the discussion, this brief and the follow-up together.')}</p><button type="button" className="fc-showroom-subtle" onClick={event => { setFilter('all'); openContext('projects', event.currentTarget); }}>{t('Agree a next step')}<ArrowUpRight size={15} /></button></div>}</>); }
  const activeSurface = section === 'chat' ? contextView ?? 'chat' : section;
  return <div className={'fc-showroom' + (state.layout.focus ? ' fc-studio-focused' : '')} style={style} lang={locale}>
    <header className="fc-showroom-top">
      <button className="fc-showroom-back" onClick={onBack} aria-label={t('Back to FreeCollab')}><ArrowLeft size={15} /><Brand size="small" /></button>
      <label className="fc-studio-workspace-selector"><span className="fc-showroom-visually-hidden">{t('Demo workspace')}</span><select aria-label={t('Demo workspace')} value="nord" onChange={() => {}}><option value="nord">Nord Studio</option></select></label>
      <span className="fc-studio-top-divider" aria-hidden="true" />
      <DemoChannelSwitcher value={channel} onChange={selectDemoChannel} channels={managedChannels} t={t} onUpdate={(value, patch) => { setManagedChannels(previous => previous.map(row => row.id === value ? { ...row, ...patch, sampleName: false, sampleDescription: false } : row)); setNotice('Channel details updated in this tab.'); }} />
      <div className="fc-showroom-top-actions">
        <div className="fc-studio-tools" ref={tools}>
          <button className="fc-showroom-subtle" aria-label={t('Tools')} aria-expanded={toolsOpen} aria-controls={id + '-tools'} onClick={() => setToolsOpen(!toolsOpen)}><Menu size={17} /><span>{t('Tools')}</span><ChevronDown size={12} /></button>
          <div id={id + '-tools'} className="fc-studio-tools-panel" hidden={!toolsOpen}>
            <nav className="fc-showroom-tabs" role="tablist" aria-label={t('Explore the demo')}>{demoSections.map((value, index) => { const Icon = sectionIcons[value]; return <button ref={element => { tabRefs.current[value] = element; }} type="button" role="tab" key={value} id={id + '-' + value + '-tab'} aria-controls={id + '-' + value + '-panel'} aria-selected={section === value} tabIndex={section === value ? 0 : -1} onClick={() => { choose(value, true); setToolsOpen(false); }} onKeyDown={event => keyboardTab(event, index)}><Icon size={16} /><span>{t(sectionNames[value])}</span></button>; })}</nav>
            <div className="fc-studio-tool-options"><div role="group" aria-label={t('Demo language')}><button aria-pressed={locale === 'en'} onClick={() => setLocalLocale('en')} lang="en">EN</button><button aria-pressed={locale === 'sv'} onClick={() => setLocalLocale('sv')} lang="sv">SV</button></div><button className="fc-showroom-subtle" aria-label={t('Reset demo')} onClick={() => { setResetting(true); setToolsOpen(false); }}><RotateCcw size={14} />{t('Reset demo')}</button></div>
            {onOpenApp && <button className="fc-studio-open-app" onClick={onOpenApp}>{t('Open the app')}<ArrowUpRight size={15} /></button>}
          </div>
        </div>
        {avatar('Maja')}
      </div>
    </header>
    <main className="fc-showroom-page">
      <div className="fc-studio-context"><span>{t('Sample workspace · fictional people · changes stay in this tab')}</span><div><button type="button" className="fc-studio-my-view" onClick={() => choose('appearance', true)}><Palette size={16} /><span>{t('My view')}</span></button><button className="fc-showroom-subtle" aria-pressed={state.layout.focus} onClick={() => { dispatch({ type: 'focus' }); choose('chat'); }}><Focus size={15} />{t(state.layout.focus ? 'Leave focus' : 'Focus')}</button><button className="fc-showroom-subtle" aria-expanded={layoutOpen} aria-controls={id + '-layout'} onClick={() => setLayoutOpen(!layoutOpen)}><SlidersHorizontal size={15} /><span>{t('Arrange')}</span></button></div></div>
      {['chat', 'documents', 'projects'].includes(section) && <nav className="fc-studio-journey" aria-label={t('Follow the courtyard project')}>
        <span className="fc-studio-channel-summary">#{channelName}<small>{activeChannel.sampleDescription ? t(activeChannel.description) : activeChannel.description}</small></span>
        <div>
          <button type="button" aria-pressed={activeSurface === 'chat'} onClick={returnToConversation}><span>{t('Conversation')}</span></button>
          <button type="button" aria-pressed={activeSurface === 'documents'} onClick={event => openContext('documents', event.currentTarget)}><span>{t('Documents')}</span><small>{channelDocuments.length}</small></button>
          <button type="button" aria-pressed={activeSurface === 'projects'} onClick={event => { setFilter('all'); openContext('projects', event.currentTarget); }}><span>{t('Tasks')}</span><small>{state.tasks.filter(row => !row.done).length}</small></button>
        </div>
      </nav>}
      <section className="fc-showroom-workspace" aria-label={t('Interactive sample workspace')}>
        {layoutOpen && <div className="fc-studio-layout" id={id + '-layout'} aria-label={t('Personal layout controls')}><header><div><strong>{t('Arrange your materials')}</strong><p>{t('Pinned materials appear first. This layout is yours for this visit.')}</p></div><button className="fc-showroom-subtle" aria-label={t('Close layout controls')} onClick={() => setLayoutOpen(false)}><X size={16} /></button></header><label>{t('Materials beside the conversation')}<select aria-label={t('Material position')} value={state.layout.side} onChange={event => dispatch({ type: 'layout_side', side: event.target.value as 'left' | 'right' })}><option value="right">{t('Right')}</option><option value="left">{t('Left')}</option></select></label><div className="fc-studio-layout-items">{orderedMaterials.map((material, index) => <div key={material}><span>{t(materialName(material))}</span><div><button aria-label={t('Move {material} earlier', { material: t(materialName(material)) })} disabled={index === 0 || state.layout.pinned.includes(material) !== state.layout.pinned.includes(orderedMaterials[index - 1])} onClick={() => dispatch({ type: 'layout_move', id: material, direction: -1 })}><ArrowUp size={15} /></button><button aria-label={t('Move {material} later', { material: t(materialName(material)) })} disabled={index === orderedMaterials.length - 1 || state.layout.pinned.includes(material) !== state.layout.pinned.includes(orderedMaterials[index + 1])} onClick={() => dispatch({ type: 'layout_move', id: material, direction: 1 })}><ArrowDown size={15} /></button><button aria-label={t('Pin {material}', { material: t(materialName(material)) })} aria-pressed={state.layout.pinned.includes(material)} onClick={() => dispatch({ type: 'layout_pin', id: material })}><Pin size={15} /></button></div></div>)}</div><button className="fc-showroom-subtle" onClick={() => dispatch({ type: 'layout_reset' })}><RotateCcw size={14} />{t('Reset layout')}</button><small>{t('On small screens, materials follow the conversation.')}</small></div>}
        <div ref={panel} className={'fc-showroom-content fc-showroom-content-' + section} role="tabpanel" id={id + '-' + section + '-panel'} aria-label={t(sectionNames[section])} tabIndex={0}>
          <div className="fc-studio-conversation-workspace" hidden={section !== 'chat'}><div className="fc-studio-scene" data-material-position={state.layout.side} data-context-open={!!contextView}>
            <div className="fc-studio-conversation"><div className="fc-showroom-transcript" role="log" aria-label={t('Sample conversation')} aria-live="polite" ref={transcript} onScroll={rememberReading}>{state.messages[channel].map((row, index) => <article className={'fc-showroom-message' + (index === 0 && row.sample ? ' fc-studio-opening' : '') + (!row.sample ? ' fc-showroom-message-own' : '')} key={row.id} data-message-id={row.id} tabIndex={-1} draggable onDragStart={event => { event.dataTransfer.effectAllowed = 'copy'; event.dataTransfer.setData(DEMO_MESSAGE_DRAG_TYPE, JSON.stringify({ channel, id: row.id })); }}>{row.sample ? avatar(row.author, index === 0) : <span className="fc-studio-you" aria-hidden="true">{t('You').slice(0, 1)}</span>}<div><header><strong>{row.sample ? row.author : t('You')}</strong>{index === 0 && <span>· #{channelName}</span>}<time>{row.time}</time>{!row.sample && <span>{t('Demo only')}</span>}</header>{index === 0 && row.sample ? <h1>{t(row.text)}</h1> : <p>{row.sample ? t(row.text) : row.text}</p>}<button type="button" className="fc-studio-message-keep" aria-label={t('Keep message from {name}', { name: row.sample ? row.author : t('You') })} onClick={event => { setPendingReference(row); openContext('documents', event.currentTarget); }}><Plus size={12} aria-hidden="true" />{t('Keep in a stack')}</button></div></article>)}</div>
            <form className="fc-showroom-composer" onSubmit={send}><MessageSquare size={19} aria-hidden="true" /><label className="fc-showroom-visually-hidden" htmlFor={id + '-message'}>{t('Your demo message')}</label><input id={id + '-message'} value={message} maxLength={2000} onChange={event => setMessage(event.target.value)} placeholder={t('What are you thinking?')} /><button type="submit" aria-label={t('Send demo message')} disabled={!message.trim()}><Send size={20} /></button></form><p className="fc-showroom-footnote">{message.trim() && <span className="fc-studio-draft">{t('Unsent draft · kept in this tab')}</span>}<span>{t('Your messages stay in this demo. Sample teammates do not reply.')}</span></p></div>
            <div className="fc-studio-materials" aria-label={t('Shared sample materials')}><div className="fc-studio-material-stack" hidden={!!contextView}>{channel === 'studio' ? orderedMaterials.map(material => material === 'courtyard' ? <figure className={'fc-studio-courtyard' + (state.layout.pinned.includes(material) ? ' fc-studio-pinned' : '')} data-material="courtyard" key={material}><img src={courtyard} alt={t('A sunlit brick courtyard with trees, an open doorway and a shared table')} /><figcaption>{state.layout.pinned.includes(material) && <Pin size={12} aria-label={t('Pinned')} />}<span>{t('The courtyard, version 04 · shared by Elias')}</span></figcaption><div className="fc-studio-material-note">{avatar('Nora')}<div><strong>Nora <time>11:03</time></strong><p>{t('I’ll bring the material samples tomorrow.')}</p></div></div></figure> : <section className={'fc-studio-brief' + (state.layout.pinned.includes(material) ? ' fc-studio-pinned' : '')} data-material="brief" key={material}><header><span><FileText size={15} />{t('Shared brief')}{state.layout.pinned.includes(material) && <Pin size={12} aria-label={t('Pinned')} />}</span><button className="fc-showroom-subtle" onClick={event => { setDocumentId('brief'); openContext('documents', event.currentTarget); }} aria-label={t('Open project brief')} aria-controls={id + '-context'} aria-expanded={contextView === 'documents'}><ArrowUpRight size={16} /></button></header><label htmlFor={id + '-shared-brief'}>{t('A quieter courtyard')}</label><textarea id={id + '-shared-brief'} aria-label={t('Edit courtyard brief')} value={state.documents[0].edited ?? t(state.documents[0].source)} maxLength={20000} onChange={event => dispatch({ type: 'document', id: 'brief', text: event.target.value })} /><footer>{t(state.documents[0].edited === null ? 'Sample document' : 'Edited in this demo')}<span>{t('Markdown')}</span></footer><div className="fc-studio-brief-actions"><button type="button" className="fc-showroom-subtle" onClick={event => openContext('projects', event.currentTarget)}>{t('Agree a next step')}<ArrowUpRight size={14} /></button></div></section>) : null}
              <div className="fc-studio-stack-surface">{renderDesk(true)}</div>
              {state.decision && <section className="fc-studio-follow-up" aria-label={t('Agreed next step')}><span className="fc-showroom-kicker">{t('Agreed next step')}</span><p>{state.decision.text}</p><label><input type="checkbox" checked={state.tasks.find(row => row.id === demoDecisionTaskId)?.done ?? false} onChange={() => dispatch({ type: 'task_toggle', id: demoDecisionTaskId })} /><span>{state.decision.nextStep}</span></label><footer><span>{state.decision.owner}</span><button type="button" className="fc-showroom-subtle" onClick={event => openContext('projects', event.currentTarget)}>{t('View decision')}<ArrowUpRight size={14} /></button></footer></section>}
              </div><section ref={contextPanel} id={id + '-context'} className="fc-studio-context-panel" hidden={!contextView || section !== 'chat'} aria-label={t(contextView === 'documents' ? 'The shared brief' : 'Decision & next step')} tabIndex={-1} onKeyDown={event => { if (event.key === 'Escape' && !event.nativeEvent.isComposing && !resetting) { event.preventDefault(); event.stopPropagation(); returnToConversation(); } }}>
                <header className="fc-studio-context-return"><button type="button" className="fc-showroom-subtle" onClick={returnToConversation}><ArrowLeft size={15} />{t('Back to conversation')}</button><span>#{channelName}</span></header>
                {section === 'chat' && contextView === 'documents' && renderDocuments()}{section === 'chat' && contextView === 'projects' && renderProjects()}
              </section></div>
          </div></div>
            {section === 'projects' && renderProjects()}
            {section === 'documents' && renderDocuments()}
            {section === 'connections' && <><div className="fc-showroom-section-heading"><div><span className="fc-showroom-kicker">{t('Self-hosted connections')}</span><h2>{t('Prepare your organisation’s app.')}</h2><p>{t('Explore how your own Google Workspace or Microsoft 365 app is prepared for a self-hosted installation.')}</p></div><Plug size={25} /></div><div className="fc-showroom-provider-picker" role="group" aria-label={t('Example connection provider')}>{(['microsoft', 'google'] as const).map(value => <button type="button" key={value} aria-pressed={provider === value} onClick={() => setProvider(value)}><span className={'fc-showroom-provider-logo fc-showroom-provider-logo-' + value} aria-hidden="true">{value === 'microsoft' ? <span><i /><i /><i /><i /></span> : 'G'}</span><strong>{demoConnectionProviders[value].name}</strong><small>{t('Preparation guide')}</small></button>)}</div><div className="fc-showroom-preparation"><div className="fc-showroom-preparation-heading"><h3>{t('Start with your own app')}</h3><span>{t('Demo checklist')}</span></div><p>{t('In your real installation, an administrator registers an app, reviews access, then explicitly authorizes and tests the connection. This demo makes no provider calls.')}</p><ol>{demoConnectionSteps.map((step, index) => <li key={step}><label><input type="checkbox" checked={checklists[provider].includes(index)} onChange={event => setChecklists(previous => ({ ...previous, [provider]: event.target.checked ? [...previous[provider], index] : previous[provider].filter(value => value !== index) }))} /><span>{t(step)}</span></label></li>)}</ol><dl><div><dt>{t('Customer app console')}</dt><dd>{demoConnectionProviders[provider].console}</dd></div><div><dt>{t('Example permissions to review')}</dt><dd>{demoConnectionProviders[provider].scopes}</dd></div></dl><label className="fc-showroom-callback">{t('Example callback — replace with your installation’s exact address')}<input readOnly value={demoCallbackExample} /></label><p className="fc-showroom-footnote">{t('Checking a box changes this example checklist only. No account is connected, authorized or tested here.')}</p></div></>}
            {section === 'appearance' && <><div className="fc-showroom-section-heading"><div><span className="fc-showroom-kicker">{t('My view')} · {t('Personal settings')}</span><h2>{t('Choose your colours and type.')}</h2><p>{t('Your colours, typeface and layout are personal. Your teammates keep their own view.')}</p></div><Palette size={25} /></div><div className="fc-showroom-palettes" role="group" aria-label={t('Demo colour palettes')}>{PRESETS.map(preset => <button type="button" key={preset.id} aria-pressed={appearance.accent_color === preset.accent_color && appearance.background_color === preset.background_color} onClick={() => setAppearance(previous => ({ ...previous, accent_color: preset.accent_color, background_color: preset.background_color }))}><span style={{ background: preset.background_color }}><i style={{ background: preset.accent_color }} />{appearance.accent_color === preset.accent_color && appearance.background_color === preset.background_color && <Check size={15} />}</span><strong>{t(preset.name)}</strong><small>{t(preset.description)}</small></button>)}</div><div className="fc-showroom-appearance-fields"><label>{t('Accent colour')}<span className="fc-showroom-colour-input"><input type="color" aria-label={t('Accent colour')} value={appearance.accent_color} onChange={event => setAppearance(previous => ({ ...previous, accent_color: event.target.value }))} /><span>{appearance.accent_color}</span></span></label><label>{t('Canvas colour')}<span className="fc-showroom-colour-input"><input type="color" aria-label={t('Canvas colour')} value={appearance.background_color} onChange={event => setAppearance(previous => ({ ...previous, background_color: event.target.value }))} /><span>{appearance.background_color}</span></span></label><label>{t('Demo typeface')}<select aria-label={t('Demo typeface')} value={appearance.font_family} onChange={event => setAppearance(previous => ({ ...previous, font_family: event.target.value as AppearancePreferences['font_family'] }))}><option value="inter">Inter</option><option value="manrope">Manrope</option><option value="serif">Source Serif</option><option value="system">{t('System')}</option></select></label><label>{t('Demo corners')}<select aria-label={t('Demo corners')} value={appearance.corner_style} onChange={event => setAppearance(previous => ({ ...previous, corner_style: event.target.value as AppearancePreferences['corner_style'] }))}><option value="soft">{t('Soft')}</option><option value="rounded">{t('Rounded')}</option><option value="square">{t('Square')}</option></select></label></div><div className="fc-showroom-type-sample"><span className="fc-showroom-kicker">{t('Conversation preview')}</span><h3>{t('Shall we open up to the courtyard?')}</h3><p>{t('Open doors, a long table and a few material samples to review together.')}</p><span className="fc-showroom-style-badge"><Check size={13} />{t('Applied to this demo only')}</span></div></>}
        </div>
      </section>
      <div className="fc-showroom-status" role="status" aria-live="polite">{notice ? t(notice) : t('Browser-only demo · no account or connected services · reset or reload to start fresh')}</div>
    </main>
    {resetting && <div ref={resetDialog} tabIndex={-1} className="fc-showroom-reset" role="alertdialog" aria-modal="true" aria-labelledby={id + '-reset-title'}><div><h2 id={id + '-reset-title'}>{t('Start fresh?')}</h2><p>{t('This clears your demo messages, tasks, decisions, documents, stacks, channel changes and personal settings. Nothing in a real workspace is changed.')}</p><div><button autoFocus onClick={() => setResetting(false)}>{t('Keep exploring')}</button><button className="fc-showroom-primary" onClick={reset}>{t('Reset this demo')}</button></div></div></div>}
  </div>;
}
