import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Landing } from './components/Landing';
import { Demo } from './components/Demo';
import { Docs } from './components/Docs';
import { LocaleProvider } from './i18n';
import { api } from './lib/api';
import '@fontsource-variable/inter';
import '@fontsource-variable/manrope';
import '@fontsource-variable/source-serif-4';
import '@fontsource-variable/source-serif-4/wght-italic.css';
import './index.css';

// The public website has no account, native, mobile/offline or service-worker
// startup. Existing account credentials on this origin remain untouched and are
// never used for guest language preferences or public demo interactions.
api.useMemorySession();

type View = 'landing' | 'demo' | 'docs';
const currentView = (): View => window.location.hash === '#demo' ? 'demo' : window.location.hash === '#docs' ? 'docs' : 'landing';

function Website() {
  const [view, setView] = useState<View>(currentView);
  useEffect(() => {
    const update = () => { setView(currentView()); window.scrollTo(0, 0); };
    window.addEventListener('hashchange', update);
    return () => window.removeEventListener('hashchange', update);
  }, []);
  const show = (next: View) => {
    window.location.hash = next === 'landing' ? '' : next;
    setView(next); window.scrollTo(0, 0);
  };
  if (view === 'demo') return <Demo onBack={() => show('landing')} />;
  if (view === 'docs') return <Docs onBack={() => show('landing')} />;
  return <Landing onTryDemo={() => show('demo')} onExploreDemo={() => show('demo')} onDocs={() => show('docs')} appLabel="Explore demo" />;
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><LocaleProvider><Website /></LocaleProvider></React.StrictMode>);
