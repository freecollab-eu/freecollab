import { createRoot } from 'react-dom/client';
import { ProductDemoSession } from './components/ProductDemoSession';
import { api } from './lib/api';
import { applyLocale, resolveLanguage } from './i18n';
import '@fontsource-variable/inter';
import '@fontsource-variable/manrope';
import '@fontsource-variable/source-serif-4';
import '@fontsource-variable/source-serif-4/wght-italic.css';
import './index.css';

// The HTML marker prevents even the API constructor from reading stored tokens.
// There is no mobile, native sign-in or service-worker initialization here.
api.useMemorySession();
const requested = new URLSearchParams(window.location.search).get('locale');
applyLocale(resolveLanguage(requested === 'en' || requested === 'sv' ? requested : 'auto'));

createRoot(document.getElementById('root')!).render(<ProductDemoSession />);
