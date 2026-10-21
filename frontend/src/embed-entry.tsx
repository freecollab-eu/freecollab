import React from 'react';
import ReactDOM from 'react-dom/client';
import { EmbedApp } from './components/EmbedApp';
import '@fontsource-variable/inter';
import '@fontsource-variable/manrope';
import '@fontsource-variable/source-serif-4';
import '@fontsource-variable/source-serif-4/wght-italic.css';
import './index.css';
import './styles/embed.css';

// No service worker, persistent account session or top-level route is adopted.
ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><EmbedApp /></React.StrictMode>);
