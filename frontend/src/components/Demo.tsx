import { useI18n } from '../i18n';
import type { DemoDestination } from '../lib/demo-showroom';
import { productDemoUrl } from '../lib/product-demo-url';
import '../styles/product-demo-frame.css';

interface Props { destination?: DemoDestination; onBack: () => void; onOpenApp?: () => void }

/** The public route embeds the real application. All features belong to Chat/WorkHub. */
export function Demo({ destination = {}, onBack }: Props) {
  const { t, locale } = useI18n();
  let source: string;
  try { source = productDemoUrl(import.meta.env.VITE_PUBLIC_DEMO_URL, window.location.origin, destination, locale); }
  catch { return <main className="fc-product-demo-frame"><div className="fc-product-demo-unavailable"><h1>{t('The demo installation is not available.')}</h1><button type="button" className="fc-button" onClick={onBack}>{t('Back to website')}</button></div></main>; }
  return <main className="fc-product-demo-frame" data-product-demo-frame>
    <header className="fc-product-demo-frame-header">
      <div><button type="button" className="fc-button" onClick={onBack}>{t('Back to website')}</button><p>{t('The product, with example data')}</p></div>
      <a className="fc-button" href={source} target="_blank" rel="noopener noreferrer">{t('Open demo in a new tab')}</a>
    </header>
    <iframe title={t('FreeCollab product demo')} src={source} referrerPolicy="no-referrer" allow="camera; microphone; display-capture; fullscreen; clipboard-write" />
  </main>;
}
