import { ArrowRight, ArrowUpRight, CheckSquare, ChevronDown, MessageSquare, Plug, Server, ShieldCheck, Sparkles, Users, Video } from 'lucide-react';
import { Brand } from './Brand';
import { LanguagePicker, t, useI18n } from '../i18n';
import type { DemoDestination } from '../lib/demo-showroom';
import productScreenshot from '../assets/product/courtyard-product.png';
import '../styles/public.css';
import '../styles/product-preview.css';

interface LandingProps {
  onTryDemo: () => void;
  onExploreDemo: (destination?: DemoDestination) => void;
  onDocs: () => void;
  appLabel?: string;
}

export function Landing({ onTryDemo, onExploreDemo, onDocs, appLabel }: LandingProps) {
  useI18n();
  return (
    <div className="fc-public">
      <header className="fc-public-header">
        <a className="fc-public-brand-link" href="#" aria-label={t("FreeCollab home")}><Brand size="large" /></a>
        <nav className="fc-public-nav" aria-label={t("Main navigation")}>
          <LanguagePicker compact />
          <button type="button" className="fc-public-text-button fc-public-demo-nav" onClick={() => onExploreDemo()}>{t('Try the demo')}</button>
          <button type="button" className="fc-public-text-button" onClick={onDocs}>{t("Documentation")}</button>
          <a className="fc-public-source-link" href="https://github.com/freecollab-eu/freecollab" target="_blank" rel="noopener noreferrer">{t("Open source ")}<ArrowUpRight size={14} aria-hidden="true" /></a>
          <button type="button" className="fc-button fc-button-primary fc-public-nav-cta" onClick={onTryDemo}>{appLabel ? t(appLabel) : t("Open App ")}<ArrowUpRight size={16} aria-hidden="true" /></button>
        </nav>
      </header>

      <main>
        <section className="fc-public-hero fc-public-container" aria-labelledby="public-heading">
          <div className="fc-public-hero-copy">
            <div className="fc-public-hero-heading">
            <p className="fc-kicker fc-public-eyebrow"><span aria-hidden="true" /> {t('OPEN SOURCE. OPEN POSSIBILITIES.')}</p>
            <h1 id="public-heading">{t('Good work.')}<br /><em>{t('Your terms.')}</em></h1>
            <div className="fc-public-principles"><span>{t("SELF-HOSTED")}</span><span>{t("OPEN SOURCE")}</span><span>{t("PERSONALLY YOURS")}</span></div>
            </div>
            <div className="fc-public-hero-details">
            <p className="fc-public-hero-description">{t('Conversations, documents, projects and the people behind them. One considered workspace. Open source, and hosted where you choose.')}</p>
            <div className="fc-public-hero-actions">
              <button type="button" className="fc-button fc-button-primary" onClick={() => onExploreDemo()}>{t('Explore the demo')}<ArrowUpRight size={18} aria-hidden="true" /></button>
              <button type="button" className="fc-public-text-button" onClick={onDocs}>{t("Set up your own ")}<ArrowRight size={16} aria-hidden="true" /></button>
            </div>
            <p className="fc-public-hero-note">{t('No signup for the demo. MIT licensed. No per-seat software fee.')}</p>
            </div>
          </div>

          <div className="fc-public-preview-wrap">
            <div className="fc-public-preview-overline"><span>{t('01 / A SHARED PLACE')}</span><span>{t('The actual product')}</span></div>
            <figure className="fc-public-product-preview">
              <button type="button" className="fc-public-product-open" aria-label={t('Explore the demo')} onClick={() => onExploreDemo()}>
                <img className="fc-public-product-image" src={productScreenshot} alt={t('FreeCollab with a Courtyard conversation, shared document and next step.')} width={1440} height={1047} decoding="async" />
                <span className="fc-public-product-open-label">{t('Explore the demo')}<ArrowUpRight size={17} aria-hidden="true" /></span>
              </button>
              <figcaption>{t('The same product, with example data.')}</figcaption>
            </figure>
          </div>
        </section>

        <div className="fc-public-intro-line fc-public-container"><p>{t('A place for the work. Space for the people.')}</p><span>{t('CHAT / DOCUMENTS / PROJECTS / MEETINGS')}</span></div>

        <section className="fc-public-essentials fc-public-container" aria-labelledby="essentials-heading">
          <div className="fc-public-section-heading"><p className="fc-kicker">{t("02 / THE EVERYDAY, CONSIDERED")}</p><h2 id="essentials-heading">{t("Everything has")}<br /><em>{t("a place.")}</em></h2><p>{t('A team project, a customer channel or collaboration in your app. Keep the conversation, shared brief and next step together.')}</p></div>
          <div className="fc-public-feature-grid">
            <article><MessageSquare size={26} strokeWidth={1.3} aria-hidden="true" /><span className="fc-public-feature-index">01</span><h3>{t('Your team, in the same place.')}</h3><p>{t('Keep everyday conversations beside the brief, the decisions and the people doing the work. A project can move forward without losing its context.')}</p><span className="fc-public-feature-detail">{t('TEAM CHANNELS / DOCUMENTS / NEXT STEPS')}</span></article>
            <article><Users size={26} strokeWidth={1.3} aria-hidden="true" /><span className="fc-public-feature-index">02</span><h3>{t('A shared place with your customers.')}</h3><p>{t('Invite a customer into the channels you choose. Review a draft, collect questions and keep the next delivery close to the conversation.')}</p><span className="fc-public-feature-detail">{t('CUSTOMER CHANNELS / SHARED REVIEWS')}</span></article>
            <article><Plug size={26} strokeWidth={1.3} aria-hidden="true" /><span className="fc-public-feature-index">03</span><h3>{t('Collaboration inside your product.')}</h3><p>{t('Use the SDK and embedded views to bring conversations into your application. Your installation, your identity integration and explicit access to each shared space.')}</p><span className="fc-public-feature-detail">{t('SDK / EMBEDDED VIEWS / YOUR INFRASTRUCTURE')}</span></article>
            <article><CheckSquare size={26} strokeWidth={1.3} aria-hidden="true" /><span className="fc-public-feature-index">04</span><h3>{t('Ideas that move forward.')}</h3><p>{t('Turn a conversation into a task. Share ownership, set a date and keep decisions with the work.')}</p><span className="fc-public-feature-detail">{t('PROJECTS / TASKS / APPROVALS')}</span></article>
            <article><Video size={26} strokeWidth={1.3} aria-hidden="true" /><span className="fc-public-feature-index">05</span><h3>{t('Make room for a conversation.')}</h3><p>{t('Meet in the browser or prepare connections to Meet, Teams and Zoom. Keep the next conversation in context.')}</p><span className="fc-public-feature-detail">{t('BROWSER MEETINGS / CALENDARS')}</span></article>
            <article><Sparkles size={26} strokeWidth={1.3} aria-hidden="true" /><span className="fc-public-feature-index">06</span><h3>{t('Your space. Your perspective.')}</h3><p>{t('Choose your colors, type and density. Switch between Swedish and English. Bring your own AI provider when you want assistance.')}</p><span className="fc-public-feature-detail">{t('PERSONAL THEMES / LANGUAGES / OPTIONAL AI')}</span></article>
          </div>
        </section>

        <section className="fc-public-connections fc-public-container" aria-labelledby="connections-heading"><div className="fc-public-section-heading"><p className="fc-kicker">{t('03 / YOUR TOOLS, TOGETHER')}</p><h2 id="connections-heading">{t('The tools you know.')}<br /><em>{t('On your terms.')}</em></h2><p>{t('Keep your organization’s identity and provider accounts. FreeCollab prepares the right settings and walks you through the connection.')}</p></div><div className="fc-public-provider-grid">{[{id:'microsoft' as const,name:'Microsoft 365',letter:'M',copy:'Prepare Entra sign-in, OneDrive and SharePoint files, Outlook calendars and Teams meetings.'},{id:'google' as const,name:'Google Workspace',letter:'G',copy:'Prepare Google sign-in, Drive files, Calendar and Meet with your organization’s own app.'}].map(provider => <article key={provider.name}><span className="fc-public-provider-mark" aria-hidden="true">{provider.letter}</span><h3>{provider.name}</h3><p>{t(provider.copy)}</p><button type="button" className="fc-public-text-button" onClick={() => onExploreDemo({ section: 'connections', provider: provider.id })}>{t('Explore connection setup')}<ArrowUpRight size={15} /></button></article>)}<p className="fc-public-provider-note"><Plug size={16} />{t('15 setup guides. Your app registration, permissions and consent. Provider accounts and licences are supplied by your organization.')}</p></div></section>

        <section className="fc-public-ownership fc-public-container" aria-labelledby="ownership-heading"><div><ShieldCheck size={28} strokeWidth={1.3} /><p className="fc-kicker">{t('CONTROL, CONSIDERED')}</p><h2 id="ownership-heading">{t('A shared workspace.')}<br /><em>{t('A clear boundary.')}</em></h2></div><div><p>{t('Choose end-to-end encrypted channels for private conversations, with device verification and recovery. Keep control of your installation and who belongs to it.')}</p><p>{t('Cloud synchronization and AI sharing are explicit choices. Customer SSO belongs to each workspace. Personal settings belong to each person.')}</p><button type="button" className="fc-public-text-button" onClick={onDocs}>{t('Understand the security model')}<ArrowUpRight size={16} /></button></div></section>

        <section className="fc-public-how fc-public-container" aria-labelledby="how-heading">
          <div className="fc-public-section-heading"><p className="fc-kicker">{t('04 / YOUR NEXT SHARED PLACE')}</p><h2 id="how-heading">{t("Your server.")}<br /><em>{t("Your people.")}</em></h2><p>{t('Run it on Railway or your own infrastructure. Start with the guided workspace setup, then connect the services your team chooses.')}</p><button type="button" className="fc-public-text-button" onClick={onDocs}>{t("Read the setup guide ")}<ArrowUpRight size={17} aria-hidden="true" /></button></div>
          <ol className="fc-public-steps">
            <li><span>01</span><div><h3>{t("Make a home for your team.")}</h3><p>{t("Follow the setup guide to run FreeCollab on a server you trust.")}</p></div><Server size={19} strokeWidth={1.3} aria-hidden="true" /></li>
            <li><span>02</span><div><h3>{t("Bring your people in.")}</h3><p>{t("Create a channel and share an invitation link with your collaborators.")}</p></div><Users size={19} strokeWidth={1.3} aria-hidden="true" /></li>
            <li><span>03</span><div><h3>{t("Make the space your own.")}</h3><p>{t("Choose your colors and type. Start a conversation. Share the next draft.")}</p></div><Sparkles size={19} strokeWidth={1.3} aria-hidden="true" /></li>
          </ol>
        </section>

        <section className="fc-public-faq fc-public-container" aria-labelledby="questions-heading"><div className="fc-public-section-heading"><p className="fc-kicker">{t('A FEW USEFUL DETAILS')}</p><h2 id="questions-heading">{t('Open by design.')}</h2></div><div>{[{q:'Is FreeCollab free?',a:'The source is available under the MIT licence, with no per-seat software fee. You operate your installation and cover hosting, storage and any external provider costs.'},{q:'Can I try it without an account?',a:'Yes. The demo opens the real product with a temporary example workspace. Explore the same conversations, documents, tasks and settings, then reset your examples whenever you like.'},{q:'Do we use FreeCollab’s Microsoft or Google account?',a:'Your organization registers and controls its own provider apps. The connection guide supplies callbacks, required permissions and setup steps for both Microsoft 365 and Google Workspace.'},{q:'Can we change the look and language?',a:'Yes. Each person can choose colors, typography, density and Swedish or English. Workspace administrators can set the shared name and logo.'}].map(item => <details key={item.q}><summary>{t(item.q)}<ChevronDown size={17} /></summary><p>{t(item.a)}</p></details>)}</div></section>

        <section className="fc-public-cta" aria-labelledby="cta-heading"><div className="fc-public-container"><p className="fc-kicker">{t("THE NEXT CONVERSATION STARTS HERE.")}</p><div><h2 id="cta-heading">{t("Make room")}<br /><em>{t("for your team.")}</em></h2><div className="fc-public-cta-aside"><p>{t('Explore it. Make it yours.')}<br />{t('Build your next shared place.')}</p><button type="button" className="fc-button fc-public-inverse-button" onClick={() => onExploreDemo()}>{t('Explore the demo')}<ArrowUpRight size={19} aria-hidden="true" /></button><a className="fc-public-text-button" href="https://github.com/freecollab-eu/freecollab" target="_blank" rel="noopener noreferrer">{t('Get the source')}<ArrowUpRight size={16} /></a></div></div><div className="fc-public-cta-mark" aria-hidden="true"><Brand showName={false} size="large" /></div></div></section>
      </main>

      <footer className="fc-public-footer fc-public-container"><Brand /><p>{t("Independent software. Shared possibilities.")}</p><div><button type="button" className="fc-public-text-button" onClick={onDocs}>{t("Documentation")}</button><a href="https://github.com/freecollab-eu/freecollab" target="_blank" rel="noopener noreferrer">{t("GitHub ")}<ArrowUpRight size={13} aria-hidden="true" /></a><a href="https://github.com/freecollab-eu/freecollab/blob/main/LICENSE" target="_blank" rel="noopener noreferrer">{t('MIT licence')}</a><span>{t('Open source · MIT')}</span></div></footer>
    </div>
  );
}
