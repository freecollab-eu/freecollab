import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowDown, ArrowRight, ArrowUpRight, Check, CheckSquare, ChevronDown, FileText, Hash, MessageSquare, Plug, RotateCcw, Send, Server, ShieldCheck, Sparkles, Users, Video } from 'lucide-react';
import { Brand } from './Brand';
import { LanguagePicker, t, useI18n } from '../i18n';
import { buildAppearanceTokens, DEFAULT_APPEARANCE, PRESETS } from '../lib/appearance';
import '../styles/public.css';

interface LandingProps {
  onTryDemo: () => void;
  onExploreDemo: () => void;
  onDocs: () => void;
  appLabel?: string;
}

type PreviewChannel = 'general' | 'design' | 'notes';
type PreviewFont = 'inter' | 'manrope' | 'serif';
type PreviewMessage = { name: string; initials: string; text: string; time: string; own?: boolean; file?: boolean };

const sampleMessages: Record<PreviewChannel, PreviewMessage[]> = {
  general: [
    { name: 'Maja Lind', initials: 'ML', text: 'Friday’s review is on. Let’s keep the ideas and decisions here.', time: '09:41' },
    { name: 'Leo Berg', initials: 'LB', text: 'The first draft is ready. A few notes for us to work through together.', time: '09:43', file: true },
  ],
  design: [
    { name: 'Astrid Holm', initials: 'AH', text: 'For the next draft: clearer type and a little more breathing room.', time: '10:12' },
    { name: 'Maja Lind', initials: 'ML', text: 'Agreed. Let’s bring one direction to Friday’s review.', time: '10:15' },
  ],
  notes: [],
};

const previewFonts: { id: PreviewFont; name: string }[] = [
  { id: 'inter', name: 'Inter' },
  { id: 'manrope', name: 'Manrope' },
  { id: 'serif', name: 'Source Serif' },
];

export function Landing({ onTryDemo, onExploreDemo, onDocs, appLabel }: LandingProps) {
  useI18n();
  const [channel, setChannel] = useState<PreviewChannel>('general');
  const [draft, setDraft] = useState('');
  const [sentMessages, setSentMessages] = useState<Record<PreviewChannel, PreviewMessage[]>>({ general: [], design: [], notes: [] });
  const [liked, setLiked] = useState(false);
  const [expandedFile, setExpandedFile] = useState<string | null>(null);
  const [palette, setPalette] = useState<string | null>(null);
  const [font, setFont] = useState<PreviewFont | ''>('');
  const [previewNotice, setPreviewNotice] = useState('Messages stay in this browser tab.');
  const messagesRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLInputElement>(null);
  const messages = [...sampleMessages[channel], ...sentMessages[channel]];

  // These preferences belong only to the sample; the visitor's appearance is untouched.
  const previewStyle = useMemo(() => {
    const tokens: Record<string, string> = {};
    const selectedPalette = PRESETS.find(item => item.id === palette);
    if (selectedPalette) {
      const mode = document.documentElement.dataset.mode === 'dark' ? 'dark' : 'light';
      const colors = buildAppearanceTokens({ ...DEFAULT_APPEARANCE, ...selectedPalette, mode });
      Object.entries(colors).forEach(([key, value]) => {
        if (!['--fc-font', '--fc-heading-font', '--fc-body-size', '--fc-message-size', '--fc-radius', '--fc-space'].includes(key)) tokens[key] = value;
      });
    }
    if (font) {
      const type = buildAppearanceTokens({ ...DEFAULT_APPEARANCE, font_family: font });
      tokens['--fc-font'] = type['--fc-font'];
      tokens['--fc-heading-font'] = type['--fc-heading-font'];
    }
    return tokens as React.CSSProperties;
  }, [palette, font]);

  useEffect(() => {
    if (sentMessages[channel].length && messagesRef.current) {
      messagesRef.current.scrollTop = messagesRef.current.scrollHeight;
    }
  }, [sentMessages, channel]);

  useEffect(() => {
    if (expandedFile) messagesRef.current?.querySelector('.fc-preview-file-excerpt')?.scrollIntoView({ block: 'nearest' });
  }, [expandedFile]);

  const sendSampleMessage = (event: React.FormEvent) => {
    event.preventDefault();
    const text = draft.trim();
    if (!text) return;
    setSentMessages(previous => ({
      ...previous,
      [channel]: [...previous[channel].slice(-9), { name: 'You', initials: 'Y', text, time: 'Now', own: true }],
    }));
    setDraft('');
    setPreviewNotice('Sample message added. Only visible in this tab.');
    composerRef.current?.focus();
  };

  const resetPreview = () => {
    setSentMessages({ general: [], design: [], notes: [] });
    setDraft('');
    setLiked(false);
    setExpandedFile(null);
    setPalette(null);
    setFont('');
    setChannel('general');
    setPreviewNotice('Sample workspace reset. Make yourself at home.');
  };

  return (
    <div className="fc-public">
      <header className="fc-public-header">
        <a className="fc-public-brand-link" href="#" aria-label={t("FreeCollab home")}><Brand size="large" /></a>
        <nav className="fc-public-nav" aria-label={t("Main navigation")}>
          <LanguagePicker compact />
          <button type="button" className="fc-public-text-button fc-public-demo-nav" onClick={onExploreDemo}>{t('Try the demo')}</button>
          <button type="button" className="fc-public-text-button" onClick={onDocs}>{t("Documentation")}</button>
          <a className="fc-public-source-link" href="https://github.com/freecollab-eu/freecollab" target="_blank" rel="noopener noreferrer">{t("Open source ")}<ArrowUpRight size={14} aria-hidden="true" /></a>
          <button type="button" className="fc-button fc-button-primary fc-public-nav-cta" onClick={onTryDemo}>{appLabel || t("Open App ")}<ArrowUpRight size={16} aria-hidden="true" /></button>
        </nav>
      </header>

      <main>
        <section className="fc-public-hero fc-public-container" aria-labelledby="public-heading">
          <div className="fc-public-hero-copy">
            <p className="fc-kicker fc-public-eyebrow"><span aria-hidden="true" /> {t('OPEN SOURCE. OPEN POSSIBILITIES.')}</p>
            <h1 id="public-heading">{t('Good work.')}<br /><em>{t('Your terms.')}</em></h1>
            <p className="fc-public-hero-description">{t('Conversations, documents, projects and the people behind them. One considered workspace. Open source, and hosted where you choose.')}</p>
            <div className="fc-public-hero-actions">
              <button type="button" className="fc-button fc-button-primary" onClick={onExploreDemo}>{t('Explore the demo')}<ArrowUpRight size={18} aria-hidden="true" /></button>
              <button type="button" className="fc-public-text-button" onClick={onDocs}>{t("Set up your own ")}<ArrowRight size={16} aria-hidden="true" /></button>
            </div>
            <div className="fc-public-principles"><span>{t("SELF-HOSTED")}</span><span>{t("OPEN SOURCE")}</span><span>{t("PERSONALLY YOURS")}</span></div>
            <p className="fc-public-hero-note">{t('No signup for the demo. MIT licensed. No per-seat software fee.')}</p>
          </div>

          <div className="fc-public-preview-wrap">
            <div className="fc-public-preview-overline"><span>{t("01 / A SHARED PLACE")}</span><span>{t("TRY IT BELOW ")}<ArrowDown size={10} aria-hidden="true" /></span></div>
            <div className="fc-preview" style={previewStyle} aria-label={t("Interactive sample workspace")}>
              <div className="fc-preview-titlebar">
                <div><Brand size="small" showName={false} /><span>{t("Studio North")}<span className="fc-preview-workspace-kind">{t("SAMPLE WORKSPACE")}</span></span></div>
                <button type="button" className="fc-preview-icon-button" onClick={resetPreview} aria-label={t("Reset sample conversation")} title={t("Reset sample conversation")}><RotateCcw size={15} /></button>
              </div>
              <div className="fc-preview-layout">
                <aside className="fc-preview-sidebar" aria-label={t("Sample channels")}>
                  <span className="fc-preview-section-label">{t("WORKSPACE")}</span>
                  <div className="fc-preview-channel-label">{t("Channels")}<span>03</span></div>
                  <div className="fc-preview-channels">
                    {(['general', 'design', 'notes'] as const).map(item => (
                      <button type="button" key={item} aria-pressed={channel === item} onClick={() => { setChannel(item); setDraft(''); setExpandedFile(null); }} className={channel === item ? 'is-active' : ''}><Hash size={14} aria-hidden="true" />{item}{channel === item && <span className="fc-preview-channel-dot" aria-hidden="true" />}</button>
                    ))}
                  </div>
                  <div className="fc-preview-members"><span className="fc-preview-section-label">{t("SAMPLE MEMBERS")}</span><div><span>{t("ML")}</span><span>{t("LB")}</span><span>{t("AH")}</span></div><p>{t("Three perspectives.")}<br />{t("One project.")}</p></div>
                  <div className="fc-preview-sidebar-footer"><Brand size="small" showName={false} /><span>{t("A little more together.")}</span></div>
                </aside>
                <div className="fc-preview-conversation">
                  <div className="fc-preview-channel-heading"><div><Hash size={17} aria-hidden="true" /><strong>{channel}</strong></div><span><Users size={14} aria-hidden="true" />3</span></div>
                  <div className="fc-preview-messages" ref={messagesRef} aria-live="polite" aria-relevant="additions">
                    {messages.length > 0 ? <div className="fc-preview-date"><span />{t("MONDAY, AT THE STUDIO")}<span /></div> : <div className="fc-preview-empty"><FileText size={30} strokeWidth={1.2} aria-hidden="true" /><h3>{t("No sample notes yet.")}</h3><p>{t("A useful link. A small idea.")}<br />{t("Leave something here for later.")}</p><button type="button" onClick={() => { setDraft('An idea for our next review: '); composerRef.current?.focus(); }}>{t("Write the first note ")}<ArrowRight size={12} aria-hidden="true" /></button></div>}
                    {messages.map((message, index) => {
                      const fileKey = `${channel}-${index}`;
                      return (
                        <article className={`fc-preview-message${message.own ? ' is-own' : ''}`} key={fileKey}>
                          <span className={`fc-preview-avatar fc-preview-avatar-${index % 3}`} aria-hidden="true">{message.initials}</span>
                          <div className="fc-preview-message-content">
                            <div className="fc-preview-message-meta"><strong>{message.own ? t("You") : message.name}</strong><time>{message.time === "Now" ? t("Now") : message.time}</time></div>
                            <p>{message.own ? message.text : t(message.text)}</p>
                            {message.file && <><button type="button" className="fc-preview-file" aria-label={t("Read sample project notes")} aria-expanded={expandedFile === fileKey} onClick={() => setExpandedFile(expandedFile === fileKey ? null : fileKey)}><FileText size={23} strokeWidth={1.4} aria-hidden="true" /><span><strong>{t("Friday review.md")}</strong><span>{t("Sample document · Read notes")}</span></span><ChevronDown size={12} aria-hidden="true" /></button>{expandedFile === fileKey && <div className="fc-preview-file-excerpt"><strong>{t("For Friday’s review")}</strong><p>{t("01. Choose our first direction.")}<br />{t("02. Bring one question to discuss.")}<br />{t("03. Keep the decisions in #general.")}</p></div>}</>}
                            {channel === 'general' && index === 0 && <button type="button" className={`fc-preview-reaction${liked ? ' is-active' : ''}`} aria-label={t("Like Maja's sample message")} aria-pressed={liked} onClick={() => setLiked(value => !value)}><Check size={11} strokeWidth={1.5} aria-hidden="true" />{liked ? '2' : '1'}</button>}
                          </div>
                        </article>
                      );
                    })}
                  </div>
                  <form className="fc-preview-composer" onSubmit={sendSampleMessage}>
                    <input ref={composerRef} aria-label={t("Write a sample message")} placeholder={t("Message #{value0}", { value0: String(channel) })} value={draft} onChange={event => setDraft(event.target.value)} maxLength={500} />
                    <button type="submit" aria-label={t("Send sample message")} disabled={!draft.trim()}><Send size={16} aria-hidden="true" /></button>
                  </form>
                </div>
              </div>
            </div>
            <div className="fc-public-preview-personalize">
              <div><span>{t("YOUR OWN PERSPECTIVE")}</span><p>{t("Try a color. Find your type.")}</p></div>
              <div className="fc-public-preview-palette" role="group" aria-label={t("Sample color palette")}>{PRESETS.map(item => <button type="button" key={item.id} aria-label={t("Preview {value0} palette", { value0: String(item.name) })} aria-pressed={palette === item.id} title={item.name} style={{ '--fc-swatch': item.accent_color } as React.CSSProperties} onClick={() => { setPalette(item.id); setPreviewNotice('Palette updated in the sample only.'); }}><span /></button>)}</div>
              <label className="fc-public-preview-font"><span className="fc-public-sr-only">{t("Sample typeface")}</span><select aria-label={t("Sample typeface")} value={font} onChange={event => { setFont(event.target.value as PreviewFont | ''); setPreviewNotice('Typeface updated in the sample only.'); }}><option value="">{t("Your typeface")}</option>{previewFonts.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select><ChevronDown size={12} aria-hidden="true" /></label>
            </div>
            <p className="fc-public-preview-caption"><span className="fc-public-preview-sample-label">{t("INTERACTIVE SAMPLE")}</span><span role="status">{t(previewNotice)}</span></p>
          </div>
        </section>

        <div className="fc-public-intro-line fc-public-container"><p>{t('A place for the work. Space for the people.')}</p><span>{t('CHAT / DOCUMENTS / PROJECTS / MEETINGS')}</span></div>

        <section className="fc-public-essentials fc-public-container" aria-labelledby="essentials-heading">
          <div className="fc-public-section-heading"><p className="fc-kicker">{t("02 / THE EVERYDAY, CONSIDERED")}</p><h2 id="essentials-heading">{t("Everything has")}<br /><em>{t("a place.")}</em></h2><p>{t('From the first idea to the next decision. Keep the conversation, shared documents and project work close together.')}</p></div>
          <div className="fc-public-feature-grid">
            <article><MessageSquare size={26} strokeWidth={1.3} aria-hidden="true" /><span className="fc-public-feature-index">01</span><h3>{t("Conversations, with context.")}</h3><p>{t("Private channels for your people and projects. Threads keep replies together; reactions make a small acknowledgment easy.")}</p><span className="fc-public-feature-detail">{t("CHANNELS / THREADS / REACTIONS")}</span></article>
            <article><FileText size={26} strokeWidth={1.3} aria-hidden="true" /><span className="fc-public-feature-index">02</span><h3>{t("Files, close at hand.")}</h3><p>{t("Share a draft, a document, or the next thing to review alongside the conversation it belongs to.")}</p><span className="fc-public-feature-detail">{t("SHARED FILES / SHARED CONTEXT")}</span></article>
            <article><Server size={26} strokeWidth={1.3} aria-hidden="true" /><span className="fc-public-feature-index">03</span><h3>{t("A server you choose.")}</h3><p>{t("Run FreeCollab on your own infrastructure. Invite collaborators and manage who belongs to each channel.")}</p><span className="fc-public-feature-detail">{t("SELF-HOSTED / OPEN SOURCE")}</span></article>
            <article><CheckSquare size={26} strokeWidth={1.3} aria-hidden="true" /><span className="fc-public-feature-index">04</span><h3>{t('Ideas that move forward.')}</h3><p>{t('Turn a conversation into a task. Share ownership, set a date and keep decisions with the work.')}</p><span className="fc-public-feature-detail">{t('PROJECTS / TASKS / APPROVALS')}</span></article>
            <article><Video size={26} strokeWidth={1.3} aria-hidden="true" /><span className="fc-public-feature-index">05</span><h3>{t('Make room for a conversation.')}</h3><p>{t('Meet in the browser or prepare connections to Meet, Teams and Zoom. Keep the next conversation in context.')}</p><span className="fc-public-feature-detail">{t('BROWSER MEETINGS / CALENDARS')}</span></article>
            <article><Sparkles size={26} strokeWidth={1.3} aria-hidden="true" /><span className="fc-public-feature-index">06</span><h3>{t('Your space. Your perspective.')}</h3><p>{t('Choose your colors, type and density. Switch between Swedish and English. Bring your own AI provider when you want assistance.')}</p><span className="fc-public-feature-detail">{t('PERSONAL THEMES / LANGUAGES / OPTIONAL AI')}</span></article>
          </div>
        </section>

        <section className="fc-public-connections fc-public-container" aria-labelledby="connections-heading"><div className="fc-public-section-heading"><p className="fc-kicker">{t('03 / YOUR TOOLS, TOGETHER')}</p><h2 id="connections-heading">{t('The tools you know.')}<br /><em>{t('On your terms.')}</em></h2><p>{t('Keep your organization’s identity and provider accounts. FreeCollab prepares the right settings and walks you through the connection.')}</p></div><div className="fc-public-provider-grid">{[{name:'Microsoft 365',letter:'M',copy:'Prepare Entra sign-in, OneDrive and SharePoint files, Outlook calendars and Teams meetings.'},{name:'Google Workspace',letter:'G',copy:'Prepare Google sign-in, Drive files, Calendar and Meet with your organization’s own app.'}].map(provider => <article key={provider.name}><span className="fc-public-provider-mark" aria-hidden="true">{provider.letter}</span><h3>{provider.name}</h3><p>{t(provider.copy)}</p><button type="button" className="fc-public-text-button" onClick={onExploreDemo}>{t('Explore connection setup')}<ArrowUpRight size={15} /></button></article>)}<p className="fc-public-provider-note"><Plug size={16} />{t('15 setup guides. Your app registration, permissions and consent. Provider accounts and licences are supplied by your organization.')}</p></div></section>

        <section className="fc-public-ownership fc-public-container" aria-labelledby="ownership-heading"><div><ShieldCheck size={28} strokeWidth={1.3} /><p className="fc-kicker">{t('CONTROL, CONSIDERED')}</p><h2 id="ownership-heading">{t('A shared workspace.')}<br /><em>{t('A clear boundary.')}</em></h2></div><div><p>{t('Choose end-to-end encrypted channels for private conversations, with device verification and recovery. Keep control of your installation and who belongs to it.')}</p><p>{t('Cloud synchronization and AI sharing are explicit choices. Customer SSO belongs to each workspace. Personal settings belong to each person.')}</p><button type="button" className="fc-public-text-button" onClick={onDocs}>{t('Understand the security model')}<ArrowUpRight size={16} /></button></div></section>

        <section className="fc-public-how fc-public-container" aria-labelledby="how-heading">
          <div className="fc-public-section-heading"><p className="fc-kicker">{t('04 / YOUR NEXT SHARED PLACE')}</p><h2 id="how-heading">{t("Your server.")}<br /><em>{t("Your people.")}</em></h2><p>{t('Run it on Railway or your own infrastructure. Start with the guided workspace setup, then connect the services your team chooses.')}</p><button type="button" className="fc-public-text-button" onClick={onDocs}>{t("Read the setup guide ")}<ArrowUpRight size={17} aria-hidden="true" /></button></div>
          <ol className="fc-public-steps">
            <li><span>01</span><div><h3>{t("Make a home for your team.")}</h3><p>{t("Follow the setup guide to run FreeCollab on a server you trust.")}</p></div><Server size={19} strokeWidth={1.3} aria-hidden="true" /></li>
            <li><span>02</span><div><h3>{t("Bring your people in.")}</h3><p>{t("Create a channel and share an invitation link with your collaborators.")}</p></div><Users size={19} strokeWidth={1.3} aria-hidden="true" /></li>
            <li><span>03</span><div><h3>{t("Make the space your own.")}</h3><p>{t("Choose your colors and type. Start a conversation. Share the next draft.")}</p></div><Sparkles size={19} strokeWidth={1.3} aria-hidden="true" /></li>
          </ol>
        </section>

        <section className="fc-public-faq fc-public-container" aria-labelledby="questions-heading"><div className="fc-public-section-heading"><p className="fc-kicker">{t('A FEW USEFUL DETAILS')}</p><h2 id="questions-heading">{t('Open by design.')}</h2></div><div>{[{q:'Is FreeCollab free?',a:'The source is available under the MIT licence, with no per-seat software fee. You operate your installation and cover hosting, storage and any external provider costs.'},{q:'Can I try it without an account?',a:'Yes. The public demo uses sample data in your browser tab. Explore conversations, tasks, documents and connection preparation, then reset it whenever you like.'},{q:'Do we use FreeCollab’s Microsoft or Google account?',a:'Your organization registers and controls its own provider apps. The connection guide supplies callbacks, required permissions and setup steps for both Microsoft 365 and Google Workspace.'},{q:'Can we change the look and language?',a:'Yes. Each person can choose colors, typography, density and Swedish or English. Workspace administrators can set the shared name and logo.'}].map(item => <details key={item.q}><summary>{t(item.q)}<ChevronDown size={17} /></summary><p>{t(item.a)}</p></details>)}</div></section>

        <section className="fc-public-cta" aria-labelledby="cta-heading"><div className="fc-public-container"><p className="fc-kicker">{t("THE NEXT CONVERSATION STARTS HERE.")}</p><div><h2 id="cta-heading">{t("Make room")}<br /><em>{t("for your team.")}</em></h2><div className="fc-public-cta-aside"><p>{t('Explore it. Make it yours.')}<br />{t('Build your next shared place.')}</p><button type="button" className="fc-button fc-public-inverse-button" onClick={onExploreDemo}>{t('Explore the demo')}<ArrowUpRight size={19} aria-hidden="true" /></button><a className="fc-public-text-button" href="https://github.com/freecollab-eu/freecollab" target="_blank" rel="noopener noreferrer">{t('Get the source')}<ArrowUpRight size={16} /></a></div></div><div className="fc-public-cta-mark" aria-hidden="true"><Brand showName={false} size="large" /></div></div></section>
      </main>

      <footer className="fc-public-footer fc-public-container"><Brand /><p>{t("Independent software. Shared possibilities.")}</p><div><button type="button" className="fc-public-text-button" onClick={onDocs}>{t("Documentation")}</button><a href="https://github.com/freecollab-eu/freecollab" target="_blank" rel="noopener noreferrer">{t("GitHub ")}<ArrowUpRight size={13} aria-hidden="true" /></a><a href="https://github.com/freecollab-eu/freecollab/blob/main/LICENSE" target="_blank" rel="noopener noreferrer">{t('MIT licence')}</a><span>{t('Open source · MIT')}</span></div></footer>
    </div>
  );
}
