import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Landing } from './components/Landing';
import { Demo } from './components/Demo';
import { Docs } from './components/Docs';
import { LocaleProvider } from './i18n';
import { api } from './lib/api';
import { demoDestinationHash, readDemoDestination, type DemoDestination } from './lib/demo-showroom';
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
const currentView = (): View => readDemoDestination(window.location.hash) ? 'demo' : window.location.hash === '#docs' ? 'docs' : 'landing';

function Website() {
  const [view, setView] = useState<View>(currentView);
  const [demoDestination, setDemoDestination] = useState<DemoDestination>(() => readDemoDestination(window.location.hash) ?? {});
  useEffect(() => {
    const update = () => { setDemoDestination(readDemoDestination(window.location.hash) ?? {}); setView(currentView()); window.scrollTo(0, 0); };
    window.addEventListener('hashchange', update);
    return () => window.removeEventListener('hashchange', update);
  }, []);
  const show = (next: View, destination: DemoDestination = {}) => {
    window.location.hash = next === 'landing' ? '' : next === 'demo' ? demoDestinationHash(destination).slice(1) : next;
    setDemoDestination(destination);
    setView(next); window.scrollTo(0, 0);
  };
  if (view === 'demo') return <Demo destination={demoDestination} onBack={() => show('landing')} />;
  if (view === 'docs') return <Docs onBack={() => show('landing')} />;
  return <Landing onTryDemo={() => show('demo')} onExploreDemo={destination => show('demo', destination)} onDocs={() => show('docs')} appLabel="Explore demo" />;
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><LocaleProvider><Website /></LocaleProvider></React.StrictMode>);
